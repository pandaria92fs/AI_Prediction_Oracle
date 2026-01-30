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
        构建 Prompt (V5：5% 准入门槛 + 审计员模式)
        """
        current_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        
        # 1. 严格的 5% 市场准入过滤
        MIN_PROBABILITY_THRESHOLD = 0.05  # 5% 门槛
        markets = event_data.get("markets", [])
        
        markets_text = ""
        filtered_count = 0
        for m in markets:
            probability = self._get_market_probability(m)
            
            # 严格遵守 5% 门槛，低于此值不进入 AI 分析池
            if probability < MIN_PROBABILITY_THRESHOLD:
                filtered_count += 1
                continue
            
            market_id = m.get("id", m.get("polymarket_id", ""))
            question = m.get("question", "")
            
            # 格式化：同时显示 0.65 和 65.0%
            markets_text += f"""
            - Market ID: {market_id}
            - Question: {question}
            - Current Probability: {probability:.2f} ({probability*100:.1f}%)
            """
        
        if filtered_count > 0:
            logger.info(f"📊 过滤掉 {filtered_count} 个低概率市场 (< 5%)")

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
        
        **IMPORTANT MATHEMATICAL CONSTRAINT**: The following markets are MUTUALLY EXCLUSIVE and part of the same event. The sum of your ai_calibrated_odds for all listed Market IDs MUST EQUAL 1.0 (100%). If you assign a high probability to one date, you must reduce others proportionally.
        
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

    def _should_normalize(self, event_title: str, market_count: int) -> bool:
        """
        判断是否需要对概率进行归一化（总和 = 100%）
        
        跳过归一化的场景：
        - 累积型事件（标题含 by, hit, reach, above, below, over, under 等）
        - 单一市场（market_count == 1）
        
        执行归一化的场景：
        - 竞争性多选一（标题含 nominee, winner, which, who will 等）
        """
        title_lower = (event_title or "").lower()
        
        # 1. 单一市场：跳过归一化
        if market_count <= 1:
            logger.info("📊 单一市场，跳过归一化")
            return False
        
        # 2. 累积型关键词：跳过归一化（保留 AI 原始偏差信号）
        cumulative_keywords = [
            " by ", "hit", "reach", "above", "below", "over", "under",
            "at least", "more than", "less than", "exceed", "surpass"
        ]
        for kw in cumulative_keywords:
            if kw in title_lower:
                logger.info(f"📊 累积型事件 (含 '{kw}')，跳过归一化")
                return False
        
        # 3. 竞争性关键词：执行归一化
        competitive_keywords = [
            "nominee", "winner", "which", "who will win", "who will be",
            "next president", "next prime minister", "champion"
        ]
        for kw in competitive_keywords:
            if kw in title_lower:
                logger.info(f"📊 竞争性事件 (含 '{kw}')，执行归一化")
                return True
        
        # 4. 默认：多市场执行归一化
        logger.info(f"📊 多市场 ({market_count} 个)，默认执行归一化")
        return True

    def transform_to_raw_analysis(
        self, 
        gemini_result: Dict[str, Any], 
        original_markets: list = None,
        event_title: str = None
    ) -> Dict[str, Any]:
        """
        将 Gemini 返回结果转换为 raw_analysis 存储格式（智能归一化）
        
        Args:
            gemini_result: Gemini API 返回的原始结果
            original_markets: 原始市场列表（包含未进入 AI 分析池的市场）
            event_title: 事件标题（用于判断是否需要归一化）
            
        Returns:
            适合存入 AIPrediction.raw_analysis 的格式
        """
        if not gemini_result:
            return {}
        
        ai_markets = gemini_result.get("markets", {})
        original_markets = original_markets or []
        
        # 1. 收集所有原始市场的概率
        all_market_probs = {}
        for m in original_markets:
            market_id = m.get("id", m.get("polymarket_id", ""))
            prob = self._get_market_probability(m)
            all_market_probs[market_id] = prob
        
        # 2. 计算 AI 返回的概率总和
        total_ai_prob = sum(m.get("ai_calibrated_odds", 0) for m in ai_markets.values())
        
        # 3. 判断是否需要归一化
        should_normalize = self._should_normalize(event_title, len(original_markets))
        
        # 4. 计算未分析市场的原始概率总和
        analyzed_ids = set(ai_markets.keys())
        unanalyzed_prob_sum = sum(
            prob for mid, prob in all_market_probs.items() 
            if mid not in analyzed_ids
        )
        
        # 5. 确定归一化基准
        if should_normalize:
            normalization_base = total_ai_prob + unanalyzed_prob_sum
            if normalization_base <= 0:
                normalization_base = 1.0
            if abs(normalization_base - 1.0) > 0.01:
                logger.warning(f"⚠️ AI 概率总和为 {normalization_base:.3f}，将强制归一化到 1.0")
        else:
            # 不归一化：直接使用 AI 原始值（乘以 100 转为百分比）
            normalization_base = 1.0
        
        raw_analysis = {}
        
        # 6. 处理 AI 分析过的市场
        for market_id, market_data in ai_markets.items():
            analysis = market_data.get("analysis", {})
            calibrated_prob = market_data.get("ai_calibrated_odds", 0)
            
            if should_normalize:
                final_pct = (calibrated_prob / normalization_base) * 100
            else:
                # 不归一化：直接转为百分比
                final_pct = calibrated_prob * 100
            
            raw_analysis[market_id] = {
                "ai_calibrated_odds_pct": round(final_pct, 2),
                "ai_confidence": market_data.get("confidence_score", 0),
                "structural_anchor": analysis.get("structural_anchor"),
                "noise": analysis.get("noise"),
                "barrier": analysis.get("barrier"),
                "blindspot": analysis.get("blindspot"),
                "_analyzed": True,
                "_normalized": should_normalize,
            }
        
        # 7. 处理未分析的市场（低于 5% 门槛）
        for market_id, original_prob in all_market_probs.items():
            if market_id not in analyzed_ids:
                if should_normalize:
                    final_pct = (original_prob / normalization_base) * 100
                else:
                    final_pct = original_prob * 100
                
                raw_analysis[market_id] = {
                    "ai_calibrated_odds_pct": round(final_pct, 2),
                    "ai_confidence": 0,
                    "structural_anchor": None,
                    "noise": None,
                    "barrier": None,
                    "blindspot": None,
                    "_analyzed": False,
                    "_normalized": should_normalize,
                }
        
        return raw_analysis


# 单例模式
ai_analyzer = GeminiAnalyzer()
