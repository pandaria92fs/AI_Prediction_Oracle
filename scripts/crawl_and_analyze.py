"""
完整流程脚本：爬取数据 → AI 分析 → 存储预测

Usage:
    python -m scripts.crawl_and_analyze [--limit N] [--skip-ai]

流程：
    1. 爬取 Polymarket 数据
    2. 存储 EventCard 和 EventSnapshot
    3. 调用 Gemini AI 分析每个事件
    4. 存储 AIPrediction 到数据库
"""

import asyncio
import sys
import os
import json
from pathlib import Path
from datetime import datetime

# 添加路径
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select, delete
from sqlalchemy.dialects.postgresql import insert

from app.db.session import async_session_factory
from app.models import EventCard, EventSnapshot, Tag, CardTag, AIPrediction
from app.services.crawler import PolymarketCrawler
from app.services.gemini_analyzer import ai_analyzer


async def crawl_and_save(crawler: PolymarketCrawler, limit: int = 10) -> list:
    """
    爬取数据并保存到数据库
    
    Returns:
        保存成功的 event 数据列表
    """
    print(f"\n{'='*60}")
    print(f"📡 Step 1: 爬取 Polymarket 数据 (limit: {limit})")
    print(f"{'='*60}")
    
    # 爬取数据
    events_data = await crawler.fetch_page(limit=limit, offset=0)
    
    if not events_data:
        print("❌ 没有获取到数据")
        return []
    
    print(f"✅ 获取到 {len(events_data)} 条事件数据")
    
    # 保存到数据库
    await crawler.save_batch(events_data)
    print(f"✅ 数据已保存到数据库")
    
    return events_data


async def analyze_and_save(events_data: list):
    """
    对事件进行 AI 分析并保存预测结果
    """
    print(f"\n{'='*60}")
    print(f"🤖 Step 2: AI 分析 ({len(events_data)} 个事件)")
    print(f"{'='*60}")
    
    if not os.getenv("GEMINI_API_KEY"):
        print("⚠️ GEMINI_API_KEY 未设置，跳过 AI 分析")
        return
    
    async with async_session_factory() as session:
        success_count = 0
        skip_count = 0
        error_count = 0
        
        for i, event in enumerate(events_data, 1):
            event_id = str(event.get("id", ""))
            title = event.get("title", "Unknown")[:50]
            
            print(f"\n[{i}/{len(events_data)}] 分析: {title}...")
            
            # 检查是否有 markets
            markets = event.get("markets", [])
            if not markets:
                print(f"   ⚠️ 跳过 (无 markets)")
                skip_count += 1
                continue
            
            # 构建事件数据用于 AI 分析
            event_data = {
                "title": event.get("title", ""),
                "description": event.get("description", ""),
                "markets": markets
            }
            
            # 调用 AI 分析
            try:
                ai_result = await ai_analyzer.analyze_event(event_data)
            except Exception as e:
                print(f"   ❌ AI 分析失败: {e}")
                error_count += 1
                continue
            
            if not ai_result:
                print(f"   ❌ AI 返回空结果")
                error_count += 1
                continue
            
            # 解析结果
            summary = ai_result.get("executive_summary", "No summary available")
            markets_data = ai_result.get("markets", {})
            
            print(f"   📝 Summary: {summary[:60]}...")
            print(f"   📈 分析了 {len(markets_data)} 个 markets")
            
            # 找到最高 confidence 的 market 作为主要预测
            primary_prediction = "0"
            primary_conf = 0.0
            
            for mid, mdata in markets_data.items():
                conf = mdata.get("confidence_score", 0)
                if conf > primary_conf:
                    primary_conf = conf
                    odds = mdata.get("ai_calibrated_odds", 0) * 100
                    primary_prediction = f"{odds:.1f}"
            
            # 转换为存储格式
            raw_analysis = ai_analyzer.transform_to_raw_analysis(ai_result)
            
            # 补充原始数据
            for market in markets:
                market_id = str(market.get("id", ""))
                if market_id in raw_analysis:
                    raw_analysis[market_id]["question"] = market.get("question", "")
                    outcome_prices = market.get("outcomePrices", [])
                    if outcome_prices:
                        try:
                            if isinstance(outcome_prices, str):
                                outcome_prices = json.loads(outcome_prices)
                            raw_analysis[market_id]["original_odds"] = float(outcome_prices[0])
                        except (json.JSONDecodeError, ValueError, IndexError):
                            pass
            
            # 查找 card_id
            card_stmt = select(EventCard.id).where(EventCard.polymarket_id == event_id)
            card_result = await session.execute(card_stmt)
            card_row = card_result.first()
            
            if not card_row:
                print(f"   ⚠️ 未找到对应的 EventCard")
                skip_count += 1
                continue
            
            card_id = card_row[0]
            
            # 删除旧的预测
            await session.execute(
                delete(AIPrediction).where(AIPrediction.card_id == card_id)
            )
            
            # 存入 AIPrediction 表
            new_prediction = AIPrediction(
                card_id=card_id,
                summary=summary,
                outcome_prediction=primary_prediction,
                confidence_score=min(primary_conf * 10, 99.99),
                raw_analysis=json.dumps(raw_analysis, ensure_ascii=False)
            )
            
            session.add(new_prediction)
            success_count += 1
            print(f"   ✅ 已保存 AI 预测")
        
        await session.commit()
        
        print(f"\n{'='*60}")
        print(f"📊 AI 分析完成统计:")
        print(f"   ✅ 成功: {success_count}")
        print(f"   ⚠️ 跳过: {skip_count}")
        print(f"   ❌ 失败: {error_count}")
        print(f"{'='*60}")


async def main():
    # 解析参数
    limit = 10
    skip_ai = False
    
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--limit" and i + 1 < len(args):
            try:
                limit = int(args[i + 1])
            except ValueError:
                pass
            i += 2
        elif args[i] == "--skip-ai":
            skip_ai = True
            i += 1
        else:
            try:
                limit = int(args[i])
            except ValueError:
                pass
            i += 1
    
    print(f"\n🚀 启动完整流程 (limit: {limit}, skip_ai: {skip_ai})")
    print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    crawler = PolymarketCrawler()
    
    try:
        # Step 1: 爬取并保存
        events_data = await crawl_and_save(crawler, limit)
        
        if not events_data:
            return
        
        # Step 2: AI 分析并保存
        if not skip_ai:
            await analyze_and_save(events_data)
        else:
            print("\n⏭️ 跳过 AI 分析 (--skip-ai)")
        
        print(f"\n🎉 完整流程执行完成!")
        
    finally:
        await crawler.close()


if __name__ == "__main__":
    asyncio.run(main())
