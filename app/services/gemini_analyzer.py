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
        构建 Prompt (V4 最终版：审计员模式 + 锚定效应 + 格式化增强)
        """
        current_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        
        # 1. 市场数据循环处理 (关键修复：支持 calculated_odds 并同时显示 %)
        markets_text = ""
        markets = event_data.get("markets", [])
        for m in markets:
            market_id = m.get("id", m.get("polymarket_id", ""))
            question = m.get("question", "")
            
            # 优先级逻辑：预处理赔率 > 原始 outcomePrices > 原始 probability
            probability = 0.0
            if "calculated_odds" in m:
                probability = m["calculated_odds"]
            else:
                outcome_prices = m.get("outcomePrices", [])
                if outcome_prices:
                    try:
                        if isinstance(outcome_prices, str):
                            outcome_prices = json.loads(outcome_prices)
                        probability = float(outcome_prices[0])
                    except:
                        probability = m.get("probability", 0.0)
            
            # 格式化：同时显示 0.65 和 65.0%
            markets_text += f"""
            - Market ID: {market_id}
            - Question: {question}
            - Current Probability: {probability:.2f} ({probability*100:.1f}%)
            """

        # 2. V4 核心 Prompt：审计员 + 锚定效应 + 严格约束
        prompt = f"""
        Role: You are a Senior Risk Manager at a Hedge Fund. 
        Current Time: {current_time}

        Task: AUDIT the current prediction market odds. 
        **CRITICAL RULE**: The market is "Efficient" by default. The Current Probability is your STARTING ANCHOR. 
        Do NOT invent a probability from scratch. You only adjust the market price up or down based on "Alpha" (new information the market hasn't priced in).

        Input Event:
        Title: {event_data.get("title", "")}
        Description: {event_data.get("description", "")}
        Markets:
        {markets_text}

        Analysis Framework (The "Delta" Method):
        1. **Start with Market Odds**.
        2. **Search for Contradictions**: Is there breaking news, injury reports, or legal filings that the market ignores?
        3. **Apply Adjustment**:
           - No new info? -> Keep AI Odds close to Market Odds (e.g., Market 65% -> AI 63-67%).
           - Minor friction? -> Small adjustment (e.g., -5%).
           - "Smoking Gun" (Fatal flaw)? -> Large adjustment (e.g., -20%).
           
        **Sanity Check**: 
        - If Market Odds > 60% and you predict < 10%, YOU ARE LIKELY WRONG unless the team has been disqualified or the event cancelled. 
        - Do not be overly conservative just because the event is far in the future.

        Analysis Requirements (The "Auditor" Standard):
        1. **Executive Summary**: One ruthless sentence (max 20 words) citing the biggest macro-factor (e.g., "Fed Rate Cut", "QB Injury", "SEC Deadline").

        2. **For EACH Market**, provide a forensic breakdown:
           
           - **Structural Anchor (The Baseline)**: 
             * State the base assumption supporting the current price. 
             * Example: "Market prices in dominant 12-win season performance."
           
           - **The Noise (Overreaction)**: 
             * What SPECIFIC headline/hype is inflating the price?
             * ⛔ BAD: "Sentiment is mixed."
             * ✅ GOOD: "Viral rumors about a settlement on Twitter are ignoring the judge's latest scheduling order."
           
           - **The Barrier (The Risk)**: 
             * Specific hurdle (Injury, Law, Math).
             * ✅ GOOD: "Cap space is -$15M, preventing key signings."
           
           - **The Blindspot (The Edge)**: 
             * What specific data is the crowd missing?
           
           - **Calibrated Probability**: 
             * YOUR FINAL ADJUSTED ODDS (0.0 - 1.0). 
             * **Must be relative to the original odds.**
           
           - **Confidence**: 0-10 (How confident are you in your *deviation* from the market?).

        OUTPUT FORMAT (Strict JSON):
        {{
            "executive_summary": "string",
            "markets": {{
                "MARKET_ID_HERE": {{
                    "ai_calibrated_odds": 0.65, 
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

    def analyze_with_gemini(self, event_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        同步版本：分析单个事件（带输入输出审计日志）
        """
        if not self.api_key:
            logger.error("❌ GEMINI_API_KEY not configured")
            return None

        model = self._get_model()
        prompt = self._construct_prompt(event_data)

        # --- [检索点 1: 输入审计] ---
        logger.debug(f"===== AI INPUT PROMPT (Event: {event_data.get('id')}) =====")
        logger.debug(prompt)

        try:
            response = model.generate_content(prompt)
            raw_response = response.text

            # --- [检索点 2: 输出审计] ---
            logger.debug(f"===== AI RAW RESPONSE =====")
            logger.debug(raw_response)

            # 尝试解析 JSON
            try:
                parsed_data = json.loads(raw_response)
                return parsed_data
            except json.JSONDecodeError:
                # 尝试修复并重新解析
                fixed_text = _fix_json_string(raw_response)
                try:
                    parsed_data = json.loads(fixed_text)
                    logger.warning("⚠️ JSON was malformed, auto-fixed successfully")
                    return parsed_data
                except json.JSONDecodeError as e:
                    logger.error(f"解析 AI 回复失败: {e}, 原始文本: {raw_response}")
                    return None
        except Exception as e:
            logger.error(f"Gemini API 调用失败: {e}")
            return None

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
