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

    def _get_market_probability(self, market: Dict[str, Any]) -> float:
        """提取市场概率（统一逻辑）"""
        if "calculated_odds" in market:
            return float(market["calculated_odds"])
        
        outcome_prices = market.get("outcomePrices", [])
        if outcome_prices:
            try:
                if isinstance(outcome_prices, str):
                    outcome_prices = json.loads(outcome_prices)
                return float(outcome_prices[0])
            except:
                pass
        
        return float(market.get("probability", 0.0))

    def _construct_prompt(self, event_data: Dict[str, Any]) -> str:
        """
        构建 Prompt (V6：完整预处理 + 5% 门槛 + 兜底/上限)
        """
        current_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        
        # === 1. 市场预处理（融合 preprocess_event 逻辑） ===
        MIN_ODDS_THRESHOLD = 0.05  # 5% 门槛
        MIN_MARKETS = 2            # 最少保留数量
        MAX_MARKETS = 5            # 最多保留数量
        
        raw_markets = event_data.get("markets", [])
        
        # Step 1: 过滤不可交易的市场（archived/inactive/closed）
        eligible_markets = []
        for m in raw_markets:
            if m.get("archived") is True:
                continue
            if m.get("active") is not True:
                continue
            if m.get("closed") is True:
                continue
            eligible_markets.append(m)
        
        # Step 2: 计算赔率并排序（降序）
        markets_with_odds = []
        for m in eligible_markets:
            odds = self._get_market_probability(m)
            markets_with_odds.append({
                "market": m,
                "odds": odds,
                "market_id": m.get("id", m.get("polymarket_id", "")),
                "question": m.get("question", ""),
            })
        markets_with_odds.sort(key=lambda x: x["odds"], reverse=True)
        
        # Step 3: 主过滤 - 5% 门槛
        filtered_markets = [m for m in markets_with_odds if m["odds"] >= MIN_ODDS_THRESHOLD]
        
        # Step 4: 兜底 & 上限
        if len(filtered_markets) < MIN_MARKETS:
            # 不足 2 个，取前 2（即使 < 5%）
            selected_markets = markets_with_odds[:MIN_MARKETS]
            logger.info(f"📊 不足 {MIN_MARKETS} 个市场满足 5% 门槛，兜底取前 {MIN_MARKETS}")
        elif len(filtered_markets) > MAX_MARKETS:
            # 超过 5 个，只取前 5
            selected_markets = filtered_markets[:MAX_MARKETS]
            logger.info(f"📊 超过 {MAX_MARKETS} 个市场满足门槛，截取前 {MAX_MARKETS}")
        else:
            selected_markets = filtered_markets
            logger.info(f"📊 {len(selected_markets)} 个市场进入 AI 分析池")
        
        # Step 5: 构建 markets_text
        markets_text = ""
        for item in selected_markets:
            odds = item["odds"]
            markets_text += f"""
            - Market ID: {item["market_id"]}
            - Question: {item["question"]}
            - Current Probability: {odds:.2f} ({odds*100:.1f}%)
            """

        # 2. V4 核心 Prompt：审计员 + 锚定效应 + 严格约束
        prompt = f"""
        Role: You are a Red-Team Forecaster. Your goal is to analyze a Polymarket Event and its associated markets to provide a "Skeptical Calibration" of the odds.

        Input Format: You will receive an Event Title, Event Description, and a list of Markets (each with its own Question, Description, and Current Odds).

        ---
        Analytical Process (Red-Team Logic)
        For the overall Event and each specific Market, use Google Search to investigate:
        1. The Event Strategy (Global): Identify the overarching macro-tension (e.g., Regulatory environment, legal timelines, or broad political trends).
        2. Structural Reality (The Anchor): Find hard data (laws, SEC filings, official OPM procedures) that contradicts current market pricing.
        3. The Blindspot (Calibration): Why is the crowd wrong? Look for "Headline Confusion" where traders bet on news rather than the legal resolution criteria.

        IMPORTANT: Use Google Search to find current information, official documents, and hard data to support your analysis.
        IMPORTANT: Current datetime (minute-accurate): {current_time}

        Input Event:
        Title: {event_data.get("title", "")}
        Description: {event_data.get("description", "")}
                
        Markets:
        {markets_text}

        OUTPUT :
        Please provide the response in the following structure:
        1. Executive AI Event Summary
        [Write ONE precise sentence (MAX 18 words) capturing the macro-anchor governing the entire event.]
        ---
        2. Individual Market Calibrations
        For each market provided in the input, generate a separate analysis block:
        Market: [Market Question]
        - AI Calibrated Odds: [Your %] 
        - The Structural Anchor: [One sentence explaining the primary hard-data constraint for this specific market.] 

        OUTPUT FORMAT (Strict JSON):
        {{
            "executive_summary": "string",
            "markets": {{
                "MARKET_ID_1": {{
                    "ai_calibrated_odds": 0.65, 
                }}, "MARKET_ID_2": {{
                    "ai_calibrated_odds": 0.35,
                }}, "MARKET_ID_3": {{
                    "ai_calibrated_odds": 0.0,
                }},
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

    def transform_to_raw_analysis(
        self, 
        gemini_result: Dict[str, Any], 
        original_markets: list = None
    ) -> Dict[str, Any]:
        """
        将 Gemini 返回结果转换为 raw_analysis 存储格式（带归一化）
        
        Args:
            gemini_result: Gemini API 返回的原始结果
            original_markets: 原始市场列表（包含未进入 AI 分析池的市场）
            
        Returns:
            适合存入 AIPrediction.raw_analysis 的格式（确保所有 Market ID 都有返回）
        """
        if not gemini_result:
            return {}
        
        ai_markets = gemini_result.get("markets", {})
        original_markets = original_markets or []
        
        # 1. 收集所有原始市场的概率（用于未分析市场的极小值分配）
        all_market_probs = {}
        for m in original_markets:
            market_id = m.get("id", m.get("polymarket_id", ""))
            prob = self._get_market_probability(m)
            all_market_probs[market_id] = prob
        
        # 2. 计算 AI 返回的概率总和
        total_ai_prob = sum(m.get("ai_calibrated_odds", 0) for m in ai_markets.values())
        
        # 3. 计算未分析市场的原始概率总和（用于分配剩余概率）
        analyzed_ids = set(ai_markets.keys())
        unanalyzed_prob_sum = sum(
            prob for mid, prob in all_market_probs.items() 
            if mid not in analyzed_ids
        )
        
        # 日志
        if total_ai_prob > 0 and abs(total_ai_prob - 1.0) > 0.01:
            logger.warning(f"⚠️ AI 概率总和为 {total_ai_prob:.3f}，将强制归一化")
        if unanalyzed_prob_sum > 0:
            logger.info(f"📊 未分析市场原始概率总和: {unanalyzed_prob_sum:.3f}")
        
        # 4. 归一化基准 = AI 分析的 + 未分析市场的原始概率
        normalization_base = total_ai_prob + unanalyzed_prob_sum
        if normalization_base <= 0:
            normalization_base = 1.0  # 防止除零
        
        raw_analysis = {}
        
        # 5. 处理 AI 分析过的市场
        for market_id, market_data in ai_markets.items():
            analysis = market_data.get("analysis", {})
            calibrated_prob = market_data.get("ai_calibrated_odds", 0)
            normalized_pct = (calibrated_prob / normalization_base) * 100
            
            raw_analysis[market_id] = {
                "ai_calibrated_odds_pct": round(normalized_pct, 2),
                "ai_confidence": market_data.get("confidence_score", 0),
                "structural_anchor": analysis.get("structural_anchor"),
                "noise": analysis.get("noise"),
                "barrier": analysis.get("barrier"),
                "blindspot": analysis.get("blindspot"),
                "_analyzed": True,  # 标记：已被 AI 分析
            }
        
        # 6. 处理未分析的市场（低于 5% 门槛）
        for market_id, original_prob in all_market_probs.items():
            if market_id not in analyzed_ids:
                # 使用原始概率按比例分配（保持极小值）
                normalized_pct = (original_prob / normalization_base) * 100
                
                raw_analysis[market_id] = {
                    "ai_calibrated_odds_pct": round(normalized_pct, 2),
                    "ai_confidence": 0,  # 未分析，置信度为 0
                    "structural_anchor": None,
                    "noise": None,
                    "barrier": None,
                    "blindspot": None,
                    "_analyzed": False,  # 标记：未被 AI 分析（低于 5% 门槛）
                }
        
        return raw_analysis


# 单例模式
ai_analyzer = GeminiAnalyzer()
