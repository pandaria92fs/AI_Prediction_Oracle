import asyncio
import httpx
import time
import json
from datetime import datetime
from typing import List, Dict, Any, Optional

from sqlalchemy import select, delete
from sqlalchemy.dialects.postgresql import insert

from app.db.session import async_session_factory
from app.models import EventSnapshot, EventCard, Tag, CardTag, AIPrediction
from app.services.gemini_analyzer import ai_analyzer

# --- 配置区域 ---
POLYMARKET_API_URL = "https://gamma-api.polymarket.com/events"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

class PolymarketCrawler:
    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=30.0, 
            headers=HEADERS,
        )

    # ==========================================
    # 👇 新增：市场数据清洗逻辑
    # ==========================================
    def _get_market_odds(self, market: Dict[str, Any]) -> float:
        """从市场数据中提取当前赔率 (优先级: lastTradePrice > bestBid > outcomePrices)"""
        # 1. 尝试 lastTradePrice
        if 'lastTradePrice' in market and market['lastTradePrice'] is not None:
            try:
                return float(market['lastTradePrice'])
            except (ValueError, TypeError):
                pass
        
        # 2. 尝试 bestBid
        if 'bestBid' in market and market['bestBid']:
            try:
                return float(market['bestBid'])
            except (ValueError, TypeError):
                pass
        
        # 3. 尝试 outcomePrices
        if 'outcomePrices' in market:
            outcome_prices = market['outcomePrices']
            if isinstance(outcome_prices, str):
                try:
                    outcome_prices = json.loads(outcome_prices)
                except:
                    pass
            if isinstance(outcome_prices, list) and len(outcome_prices) > 0:
                try:
                    return float(outcome_prices[0])
                except:
                    pass
        return 0.0

    def _preprocess_event_for_ai(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        核心筛选逻辑：
        1. 过滤无效市场 (archived, closed)
        2. 按赔率从高到低排序
        3. 仅保留赔率 >= 0.05 的市场 (若不足2个则取前2，若超过5个则取前5)
        """
        markets = event.get("markets", [])
        if not markets:
            return None
        
        # 1. 基础过滤：必须是活跃且未关闭的
        eligible_markets = []
        for market in markets:
            if market.get('archived') is True: continue
            if market.get('active') is not True: continue
            if market.get('closed') is True: continue
            eligible_markets.append(market)

        if not eligible_markets:
            return None

        # 2. 计算赔率并附加元数据
        all_markets_with_odds = []
        for market in eligible_markets:
            odds = self._get_market_odds(market)
            # 保留原始 market 对象里的所有字段，并更新 calculated_odds
            market_copy = market.copy()
            market_copy['calculated_odds'] = odds
            # 同时也把这个赔率塞回 outcomePrices 格式，适配 gemini_analyzer 的读取逻辑
            market_copy['outcomePrices'] = [str(odds), str(1-odds)] 
            
            all_markets_with_odds.append(market_copy)

        # 3. 按赔率降序排序
        all_markets_with_odds.sort(key=lambda x: x['calculated_odds'], reverse=True)

        # 4. 智能截取逻辑
        # 规则 A: 先找所有赔率 >= 5% 的
        filtered_markets = [m for m in all_markets_with_odds if m['calculated_odds'] >= 0.05]

        # 规则 B: 数量控制
        if len(filtered_markets) < 2:
            # 如果符合条件的太少，至少取前 2 个 (矮子里拔将军)
            final_markets = all_markets_with_odds[:2]
        elif len(filtered_markets) > 5:
            # 如果符合条件的太多，只取前 5 个 (头部聚焦)
            final_markets = filtered_markets[:5]
        else:
            final_markets = filtered_markets
            
        return {
            "title": event.get("title"),
            "description": event.get("description"),
            "markets": final_markets
        }

    # ==========================================
    # 👆 新增结束
    # ==========================================

    async def fetch_page(self, limit: int = 50, offset: int = 0):
        params = {
            "active": "true",
            "closed": "false",
            "limit": limit,
            "offset": offset,
            "order": "volume",
            "ascending": "false",
        }
        try:
            print(f"🕷️ [Offset {offset}] 准备发起请求...")
            t_start = time.time()
            response = await self.client.get(POLYMARKET_API_URL, params=params)
            t_net = time.time()
            print(f"   📡 [网络] Polymarket API 耗时: {t_net - t_start:.2f}s")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"❌ [Offset {offset}] 抓取失败: {str(e)}")
            return []

    async def save_batch(self, events_data: List[Dict[str, Any]]):
        if not events_data: return

        t_start = time.time()
        event_card_ids: dict[str, int] = {}

        async with async_session_factory() as session:
            try:
                # --- 1. Tags 处理 ---
                all_tags: dict[str, str] = {}
                for event in events_data:
                    for t in event.get("tags", []):
                        if t.get("id") and t.get("slug"):
                            all_tags[str(t.get("id"))] = t.get("slug")
                
                sorted_poly_ids = sorted(all_tags.keys())
                tag_map: dict[str, int] = {}

                if sorted_poly_ids:
                    tag_insert_stmt = insert(Tag).values([
                        {"polymarket_id": pid, "name": all_tags[pid]} 
                        for pid in sorted_poly_ids
                    ])
                    await session.execute(
                        tag_insert_stmt.on_conflict_do_update(
                            index_elements=["polymarket_id"],
                            set_={"name": tag_insert_stmt.excluded.name},
                        )
                    )
                    tag_stmt = select(Tag.polymarket_id, Tag.id).where(Tag.polymarket_id.in_(sorted_poly_ids))
                    for pid, tid in (await session.execute(tag_stmt)).all():
                        tag_map[pid] = tid

                # --- 2. EventCard 处理 ---
                for event in events_data:
                    poly_id = str(event.get("id"))
                    image_url = event.get("image") or event.get("icon")
                    volume = float(event.get("volume") or 0)
                    end_date = None
                    if event.get("endDate"):
                        try:
                            end_date = datetime.fromisoformat(event.get("endDate").replace("Z", "+00:00"))
                        except: pass

                    session.add(EventSnapshot(polymarket_id=poly_id, raw_data=event))

                    stmt = (
                        insert(EventCard).values(
                            polymarket_id=poly_id,
                            title=event.get("title", "No Title"),
                            slug=event.get("slug", poly_id),
                            description=event.get("description"),
                            image_url=image_url,
                            volume=volume,
                            end_date=end_date,
                            is_active=event.get("active", True),
                            updated_at=datetime.utcnow(),
                        ).on_conflict_do_update(
                            index_elements=["polymarket_id"],
                            set_={
                                "title": event.get("title"),
                                "volume": volume,
                                "updated_at": datetime.utcnow(),
                                "image_url": image_url,
                                "is_active": event.get("active", True)
                            },
                        )
                    )
                    card_id = (await session.execute(stmt.returning(EventCard.id))).scalar_one()
                    event_card_ids[poly_id] = card_id

                # --- 3. 关联 Tags ---
                card_tag_links = []
                for event in events_data:
                    cid = event_card_ids.get(str(event.get("id")))
                    if not cid: continue
                    for t in event.get("tags", []):
                        tid = tag_map.get(str(t.get("id")))
                        if tid: card_tag_links.append({"card_id": cid, "tag_id": tid})
                
                if card_tag_links:
                    await session.execute(insert(CardTag).values(card_tag_links).on_conflict_do_nothing())

                await session.commit()
                print(f"   💾 [数据库] 写入 {len(events_data)} 条 | 耗时: {time.time() - t_start:.2f}s")

            except Exception as e:
                await session.rollback()
                print(f"❌ 入库失败: {str(e)}")
                return

        # --- 4. 触发 AI 分析 ---
        if event_card_ids:
            await self._process_ai_analysis(events_data, event_card_ids)

    async def _process_ai_analysis(self, events_data: List[Dict[str, Any]], event_card_ids: Dict[str, int]):
        """处理 AI 分析 (应用了预处理筛选)"""
        if not ai_analyzer.api_key: return

        async with async_session_factory() as session:
            try:
                for event in events_data:
                    poly_id = str(event.get("id"))
                    card_id = event_card_ids.get(poly_id)
                    if not card_id: continue

                    # -------------------------------------------------------
                    # 👇 关键修改：使用预处理函数筛选 Markets
                    # -------------------------------------------------------
                    filtered_event_data = self._preprocess_event_for_ai(event)
                    
                    # 如果筛选后没有有效市场 (比如都关闭了)，则跳过 AI 分析
                    if not filtered_event_data or not filtered_event_data['markets']:
                        continue

                    try:
                        await asyncio.sleep(0.5) # 限流
                        # 传入的是筛选后的数据，AI 只会分析这几个
                        ai_result = await ai_analyzer.analyze_event(filtered_event_data)
                    except Exception as e:
                        print(f"   ⚠️ AI 请求失败: {e}")
                        continue

                    if not ai_result: continue

                    # --- 后续入库逻辑 ---
                    summary = ai_result.get("executive_summary", "")
                    markets_data = ai_result.get("markets", {})
                    
                    primary_prediction = "0"
                    primary_conf = 0.0
                    for _, mdata in markets_data.items():
                        conf = mdata.get("confidence_score", 0)
                        if conf > primary_conf:
                            primary_conf = conf
                            odds = mdata.get("ai_calibrated_odds", 0) * 100
                            primary_prediction = f"{odds:.1f}"

                    raw_analysis = ai_analyzer.transform_to_raw_analysis(ai_result)
                    
                    # 回填原始数据
                    all_original_markets = event.get("markets", [])
                    for market in all_original_markets:
                        m_id = str(market.get("id", ""))
                        if m_id in raw_analysis:
                            raw_analysis[m_id]["question"] = market.get("question", "")
                            odds = self._get_market_odds(market)
                            raw_analysis[m_id]["original_odds"] = odds

                    await session.execute(delete(AIPrediction).where(AIPrediction.card_id == card_id))
                    session.add(AIPrediction(
                        card_id=card_id,
                        summary=summary,
                        outcome_prediction=primary_prediction,
                        confidence_score=min(primary_conf * 10, 99.9),
                        raw_analysis=json.dumps(raw_analysis, ensure_ascii=False)
                    ))
                    print(f"   🤖 AI 分析完成: {event.get('title', '')[:30]}... (基于 Top {len(filtered_event_data['markets'])} 市场)")

                await session.commit()
            except Exception as e:
                await session.rollback()
                print(f"❌ AI 分析批次失败: {e}")

    async def close(self):
        await self.client.aclose()

# -------------------------------------------------
# 🚀 极速并发执行入口
# -------------------------------------------------
async def process_batch_task(crawler, offset, semaphore):
    async with semaphore:
        data = await crawler.fetch_page(limit=50, offset=offset)
        print(f"📄 Offset {offset}: 抓到 {len(data)} 条数据")
        if not data: return 0
        await crawler.save_batch(data)
        return len(data)

async def run_batch_crawl():
    crawler = PolymarketCrawler()
    # 生产环境配置
    TOTAL_TARGET = 200   
    BATCH_SIZE = 50       
    CONCURRENCY = 5       
    
    print(f"🚀 启动极速爬虫 (带 AI 智能筛选) | 目标: {TOTAL_TARGET} | 并发: {CONCURRENCY}")
    
    semaphore = asyncio.Semaphore(CONCURRENCY)
    offsets = range(0, TOTAL_TARGET, BATCH_SIZE)
    tasks = [process_batch_task(crawler, offset, semaphore) for offset in offsets]
    
    try:
        t_start = time.time()
        results = await asyncio.gather(*tasks)
        total = sum(results)
        print("-" * 40)
        print(f"🎉 任务结束！共处理 {total} 条数据 | 耗时: {time.time() - t_start:.2f}s")
    finally:
        await crawler.close()

if __name__ == "__main__":
    asyncio.run(run_batch_crawl())
