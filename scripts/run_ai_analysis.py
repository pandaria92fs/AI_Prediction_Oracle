"""
AI Analysis Batch Script
使用 Gemini AI 分析事件并保存预测结果到数据库

Usage:
    python -m scripts.run_ai_analysis [--limit N]

Environment:
    GEMINI_API_KEY: Google Gemini API Key
"""

import asyncio
import sys
import json
import os
from pathlib import Path

# 添加路径以便导入 app 模块
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.session import async_session_factory
from app.models.event_card import EventCard
from app.models.event_snapshot import EventSnapshot
from app.models.ai_prediction import AIPrediction
from app.services.gemini_analyzer import ai_analyzer


async def process_batch(limit: int = 5):
    """
    批量处理事件，调用 AI 分析并保存结果
    
    Args:
        limit: 处理的事件数量
    """
    async with async_session_factory() as session:
        # 1. 获取需要分析的 Event
        # 优先获取没有 AI 预测的事件
        stmt = (
            select(EventCard)
            .where(EventCard.is_active == True)
            .order_by(EventCard.volume.desc())  # 按交易量排序
            .limit(limit)
        )
        result = await session.execute(stmt)
        events = result.scalars().all()
        
        print(f"🎯 Found {len(events)} events to analyze.")

        for event in events:
            print(f"\n📊 Processing: {event.title}...")
            
            # 获取最新的 snapshot 以获取 markets 数据
            snapshot_stmt = (
                select(EventSnapshot)
                .where(EventSnapshot.polymarket_id == event.polymarket_id)
                .order_by(EventSnapshot.created_at.desc())
                .limit(1)
            )
            snapshot_result = await session.execute(snapshot_stmt)
            snapshot = snapshot_result.scalar_one_or_none()
            
            if not snapshot or not snapshot.raw_data:
                print("   ⚠️ Skipping (no snapshot data)")
                continue
            
            # 构建事件数据用于 AI 分析
            event_data = {
                "title": event.title,
                "description": event.description,
                "markets": snapshot.raw_data.get("markets", [])
            }
            
            if not event_data["markets"]:
                print("   ⚠️ Skipping (no markets)")
                continue
            
            # 2. 调用 AI 分析
            ai_result = await ai_analyzer.analyze_event(event_data)
            
            if not ai_result:
                print("   ❌ Skipping (AI analysis failed)")
                continue

            # 3. 解析 AI 返回结果
            summary = ai_result.get("executive_summary", "No summary available")
            markets_data = ai_result.get("markets", {})
            
            print(f"   📝 Summary: {summary[:80]}...")
            print(f"   📈 Analyzed {len(markets_data)} markets")
            
            # 找到最高 confidence 的 market 作为主要预测
            primary_prediction = "0"
            primary_conf = 0.0
            
            for mid, mdata in markets_data.items():
                conf = mdata.get("confidence_score", 0)
                if conf > primary_conf:
                    primary_conf = conf
                    # 存储百分比形式
                    odds = mdata.get("ai_calibrated_odds", 0) * 100
                    primary_prediction = f"{odds:.1f}"

            # 4. 转换为存储格式
            raw_analysis = ai_analyzer.transform_to_raw_analysis(ai_result)
            
            # 补充原始数据
            for market in event_data["markets"]:
                market_id = str(market.get("id", ""))
                if market_id in raw_analysis:
                    raw_analysis[market_id]["question"] = market.get("question", "")
                    # 获取原始概率
                    outcome_prices = market.get("outcomePrices", [])
                    if outcome_prices:
                        try:
                            if isinstance(outcome_prices, str):
                                outcome_prices = json.loads(outcome_prices)
                            raw_analysis[market_id]["original_odds"] = float(outcome_prices[0])
                        except (json.JSONDecodeError, ValueError, IndexError):
                            pass

            # 5. 存入 AIPrediction 表
            new_prediction = AIPrediction(
                card_id=event.id,
                summary=summary,
                outcome_prediction=primary_prediction,
                confidence_score=min(primary_conf * 10, 99.99),  # 转为 0-100，限制最大值
                raw_analysis=json.dumps(raw_analysis, ensure_ascii=False)
            )
            
            session.add(new_prediction)
            print(f"   ✅ Saved analysis for event {event.id}")

        await session.commit()
        print(f"\n🎉 Batch processing complete!")


async def main():
    # 解析命令行参数
    limit = 5
    if len(sys.argv) > 1:
        if sys.argv[1] == "--limit" and len(sys.argv) > 2:
            try:
                limit = int(sys.argv[2])
            except ValueError:
                print("❌ Invalid limit value")
                exit(1)
        else:
            try:
                limit = int(sys.argv[1])
            except ValueError:
                pass
    
    print(f"🚀 Starting AI analysis (limit: {limit})")
    await process_batch(limit)


if __name__ == "__main__":
    # 检查 API Key
    if not os.getenv("GEMINI_API_KEY"):
        print("❌ Error: GEMINI_API_KEY environment variable is not set")
        print("   Please set it: export GEMINI_API_KEY='your-api-key'")
        exit(1)
    
    asyncio.run(main())
