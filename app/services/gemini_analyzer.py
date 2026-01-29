"""
Gemini AI Analyzer Service
使用 Google Gemini 进行事件分析和概率校准
"""

import os
import re
import json
import asyncio
import logging
from datetime import datetime
from typing import Optional, Dict, Any

import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

logger = logging.getLogger(__name__)


def _fix_json_string(text: str) -> str:
    """
    尝试修复常见的 JSON 格式问题
    """
    # 1. 移除 markdown 代码块标记
    text = re.sub(r'^```json\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^```\s*$', '', text, flags=re.MULTILINE)
    text = text.strip()
    
    # 2. 移除尾部逗号 (trailing commas)
    text = re.sub(r',(\s*[}\]])', r'\1', text)
    
    # 3. 修复单引号为双引号 (简单情况)
    # 注意：这是粗暴处理，可能在某些边缘情况失效
    
    return text


class GeminiAnalyzer:
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            logger.warning("⚠️ GEMINI_API_KEY not set. AI analysis will fail.")
        else:
            genai.configure(api_key=self.api_key)

    def _get_model(self):
        """配置 Gemini 模型"""
        generation_config = {
            "temperature": 0.7,
            "response_mime_type": "application/json",  # 强制输出 JSON
        }

        return genai.GenerativeModel(
            model_name="gemini-2.0-flash",  # 稳定可用的模型
            generation_config=generation_config,
            safety_settings={
                HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
            }
        )

    def _construct_prompt(self, event_data: Dict[str, Any]) -> str:
        """
        构建 Prompt (基于 Red-Team Forecaster 逻辑)
        
        Args:
            event_data: 包含 title, markets 等字段的事件数据
        """
        current_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        
        # 构建市场列表文本
        markets_text = ""
        markets = event_data.get("markets", [])
        for m in markets:
            market_id = m.get("id", m.get("polymarket_id", ""))
            question = m.get("question", "")
            # 获取概率 - 可能来自 outcomePrices 或 probability
            probability = 0.0
            outcome_prices = m.get("outcomePrices", [])
            if outcome_prices:
                try:
                    if isinstance(outcome_prices, str):
                        outcome_prices = json.loads(outcome_prices)
                    probability = float(outcome_prices[0]) if outcome_prices else 0.0
                except (json.JSONDecodeError, ValueError, IndexError):
                    probability = m.get("probability", 0.0)
            else:
                probability = m.get("probability", 0.0)
            
            markets_text += f"""
            - Market ID: {market_id}
            - Question: {question}
            - Current Probability: {probability:.2f}
            """

        # 核心 Prompt (Red-Team Forecaster)
        prompt = f"""
        Role: You are a Red-Team Forecaster for a prediction market platform.
        Current Time: {current_time}

        Goal: Analyze the following Polymarket Event and its markets. Use Google Search to find "Hard Data" (official filings, laws, polls) that contradicts the crowd sentiment.

        Input Event:
        Title: {event_data.get("title", "")}
        Description: {event_data.get("description", "")}
        Markets:
        {markets_text}

        Analysis Requirements (The "Forensic" Approach):
        1. **Executive Summary**: One precise sentence (max 20 words) capturing the macro-anchor.
        2. **For EACH Market**, identify:
           - **Structural Anchor**: The primary hard-data constraint (e.g., specific law, math).
           - **The Noise**: What sentiment is driving the current price?
           - **The Barrier**: Specific regulatory or logical hurdles.
           - **The Blindspot**: Why the crowd is wrong.
           - **Calibrated Probability**: Your AI-adjusted probability (0.0 to 1.0).
           - **Confidence**: 0-10 score.

        OUTPUT FORMAT:
        You MUST return valid JSON matching this structure exactly:
        {{
            "executive_summary": "string",
            "markets": {{
                "MARKET_ID_HERE": {{
                    "ai_calibrated_odds": 0.55,
                    "confidence_score": 8.5,
                    "analysis": {{
                        "structural_anchor": "string",
                        "noise": "string",
                        "barrier": "string",
                        "blindspot": "string"
                    }}
                }}
            }}
        }}
        """
        return prompt

    async def analyze_event(self, event_data: Dict[str, Any], max_retries: int = 3, retry_delay: float = 2.0) -> Optional[Dict[str, Any]]:
        """
        主入口：分析单个事件（带重试机制）
        
        Args:
            event_data: 包含 title, description, markets 等字段的事件数据
            max_retries: 最大重试次数，默认 3 次
            retry_delay: 重试间隔秒数，默认 2 秒
            
        Returns:
            分析结果字典，格式：
            {
                "executive_summary": "...",
                "markets": {
                    "market_id": {
                        "ai_calibrated_odds": 0.55,
                        "confidence_score": 8.5,
                        "analysis": {
                            "structural_anchor": "...",
                            "noise": "...",
                            "barrier": "...",
                            "blindspot": "..."
                        }
                    }
                }
            }
        """
        if not self.api_key:
            logger.error("❌ GEMINI_API_KEY not configured")
            return None

        event_title = event_data.get("title", "Unknown")
        model = self._get_model()
        prompt = self._construct_prompt(event_data)
        
        last_error = None
        
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"🤖 Calling Gemini for: {event_title[:30]}... (attempt {attempt}/{max_retries})")
                
                # 异步调用 Gemini
                response = await model.generate_content_async(prompt)
                
                # 解析 JSON (带容错)
                raw_text = response.text
                try:
                    result_json = json.loads(raw_text)
                except json.JSONDecodeError:
                    # 尝试修复并重新解析
                    fixed_text = _fix_json_string(raw_text)
                    try:
                        result_json = json.loads(fixed_text)
                        logger.warning("⚠️ JSON was malformed, auto-fixed successfully")
                    except json.JSONDecodeError as e2:
                        # JSON 解析失败，记录并重试
                        logger.warning(f"⚠️ JSON parse failed (attempt {attempt}): {e2}")
                        last_error = e2
                        if attempt < max_retries:
                            await asyncio.sleep(retry_delay)
                        continue
                
                logger.info("✅ Gemini analysis complete.")
                return result_json

            except Exception as e:
                last_error = e
                logger.warning(f"⚠️ Gemini call failed (attempt {attempt}): {e}")
                if attempt < max_retries:
                    await asyncio.sleep(retry_delay)
                continue
        
        # 所有重试都失败
        logger.error(f"❌ Gemini Analysis Failed after {max_retries} attempts: {last_error}")
        return None

    def transform_to_raw_analysis(self, gemini_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        将 Gemini 返回结果转换为 raw_analysis 存储格式
        
        Args:
            gemini_result: Gemini API 返回的原始结果
            
        Returns:
            适合存入 AIPrediction.raw_analysis 的格式
        """
        if not gemini_result:
            return {}
        
        raw_analysis = {}
        markets = gemini_result.get("markets", {})
        
        for market_id, market_data in markets.items():
            analysis = market_data.get("analysis", {})
            raw_analysis[market_id] = {
                "question": None,  # 需要从原始数据补充
                "original_odds": None,  # 需要从原始数据补充
                # AI 校准概率 (0-1 转为百分比 0-100)
                "ai_calibrated_odds_pct": market_data.get("ai_calibrated_odds", 0) * 100,
                # AI 置信度 (0-10)
                "ai_confidence": market_data.get("confidence_score", 0),
                # AI 分析详情
                "structural_anchor": analysis.get("structural_anchor"),
                "noise": analysis.get("noise"),
                "barrier": analysis.get("barrier"),
                "blindspot": analysis.get("blindspot"),
            }
        
        return raw_analysis


# 单例模式
ai_analyzer = GeminiAnalyzer()
