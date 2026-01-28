import asyncio
import httpx
import time
import random
from datetime import datetime
from typing import List, Dict, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert

from app.db.session import async_session_factory
from app.models import EventSnapshot, EventCard, Tag, CardTag

# --- 配置区域 ---
POLYMARKET_API_URL = "https://gamma-api.polymarket.com/events"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
# ⚠️ 如果你的代理端口不是 7890，请在这里修改
PROXY_URL = "http://127.0.0.1:7890" 

class PolymarketCrawler:
    def __init__(self):
        # 配置代理和超时
        self.client = httpx.AsyncClient(
            timeout=30.0, 
            headers=HEADERS,
            # proxies={
            #     "http://": PROXY_URL,
            #     "https://": PROXY_URL,
            # }
        )

    async def fetch_page(self, limit: int = 50, offset: int = 0):
        """抓取单页数据 (带计时)"""
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
            
            # ⏱️ 计时点 1: API 请求
            t_start = time.time()
            response = await self.client.get(POLYMARKET_API_URL, params=params)
            t_net = time.time()
            
            # 打印 API 耗时
            duration = t_net - t_start
            print(f"   📡 [网络] Polymarket API 耗时: {duration:.2f}s")
            
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"❌ [Offset {offset}] 抓取失败: {str(e)}")
            return []

    async def save_batch(self, events_data: List[Dict[str, Any]]):
        """分批存入数据库 (修复死锁版：批量处理 Tags)"""
        if not events_data:
            return

        t_start = time.time()

        async with async_session_factory() as session:
            try:
                # =================================================
                # 第一步：提取所有 Tags 并批量处理 (解决死锁核心)
                # =================================================
                # 1. 收集本批次所有用到的标签 (polymarket_id, slug)
                #    - polymarket_id: 上游 Polymarket 的标签 ID（字符串）
                #    - slug: 作为本地 Tag.name 存储
                all_tags: dict[str, str] = {}  # polymarket_id -> slug
                for event in events_data:
                    for t in event.get("tags", []):
                        poly_tag_id = t.get("id")
                        slug = t.get("slug")
                        if poly_tag_id is None or not slug:
                            continue
                        poly_tag_id_str = str(poly_tag_id)
                        all_tags[poly_tag_id_str] = slug
                
                # 2. 按 polymarket_id 排序 (关键！防止死锁)
                sorted_poly_ids = sorted(all_tags.keys())

                tag_map: dict[str, int] = {}  # 存放 polymarket_id -> 本地 tag.id 的映射

                if sorted_poly_ids:
                    # 3. 批量插入 Tags (ON CONFLICT DO UPDATE)
                    #    - polymarket_id: 唯一约束，用于去重和关联
                    #    - name: 存 slug，便于调试/展示
                    #    - 如果 polymarket_id 已存在，更新 name（以防 slug 变化）
                    tag_insert_stmt = insert(Tag).values(
                        [
                            {
                                "polymarket_id": poly_id,
                                "name": all_tags[poly_id],
                            }
                            for poly_id in sorted_poly_ids
                        ]
                    )
                    await session.execute(
                        tag_insert_stmt.on_conflict_do_update(
                            index_elements=["polymarket_id"],
                            set_={"name": tag_insert_stmt.excluded.name},
                        )
                    )

                    # 4. 批量查出所有 Tags 的 ID（通过 polymarket_id）
                    tag_stmt = select(Tag.polymarket_id, Tag.id).where(
                        Tag.polymarket_id.in_(sorted_poly_ids)
                    )
                    tag_results = await session.execute(tag_stmt)
                    for poly_id, tag_id in tag_results.all():
                        tag_map[poly_id] = tag_id

                # =================================================
                # 第二步：处理 EventCard 和 EventSnapshot
                # =================================================
                # 收集 card_id 映射，用于后续批量插入关联
                event_card_ids: dict[str, int] = {}  # polymarket_id -> card_id
                
                for event in events_data:
                    poly_id = str(event.get("id"))
                    
                    # 字段清洗
                    image_url = event.get("image") or event.get("icon")
                    try:
                        volume = float(event.get("volume") or 0)
                    except:
                        volume = 0.0
                    
                    end_date = None
                    if event.get("endDate"):
                        try:
                            end_date = datetime.fromisoformat(event.get("endDate").replace("Z", "+00:00"))
                        except:
                            pass

                    # 添加快照
                    session.add(EventSnapshot(polymarket_id=poly_id, raw_data=event))

                    # Upsert EventCard
                    stmt = (
                        insert(EventCard)
                        .values(
                            polymarket_id=poly_id,
                            title=event.get("title", "No Title"),
                            slug=event.get("slug", poly_id),
                            description=event.get("description"),
                            image_url=image_url,
                            volume=volume,
                            end_date=end_date,
                            is_active=event.get("active", True),
                            updated_at=datetime.utcnow(),
                        )
                        .on_conflict_do_update(
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
                    
                    # 获取 Card ID
                    result = await session.execute(stmt.returning(EventCard.id))
                    card_id = result.scalar_one()
                    event_card_ids[poly_id] = card_id

                # =================================================
                # 第三步：批量插入关联关系 (使用 tag_map)
                # =================================================
                card_tag_links = []
                for event in events_data:
                    poly_id = str(event.get("id"))
                    card_id = event_card_ids.get(poly_id)
                    if not card_id:
                        continue
                    
                    for tag_data in event.get("tags", []):
                        poly_tag_id = tag_data.get("id")
                        if poly_tag_id is None:
                            continue
                        poly_tag_id_str = str(poly_tag_id)
                        if poly_tag_id_str in tag_map:
                            card_tag_links.append({
                                "card_id": card_id,
                                "tag_id": tag_map[poly_tag_id_str],
                            })
                
                # 批量插入关联 (忽略冲突)
                if card_tag_links:
                    await session.execute(
                        insert(CardTag).values(card_tag_links).on_conflict_do_nothing()
                    )

                # 提交事务
                await session.commit()
                t_commit = time.time()
                
                # 算一下这一批的平均耗时
                total_time = t_commit - t_start
                print(f"   💾 [数据库] 写入 {len(events_data)} 条 | 耗时: {total_time:.2f}s")

            except Exception as e:
                await session.rollback()
                # 打印更详细的错误堆栈，方便调试
                print(f"❌ 入库批次失败: {str(e)}")

    async def close(self):
        await self.client.aclose()


# -------------------------------------------------
# 🚀 极速并发执行入口
# -------------------------------------------------
async def process_batch_task(crawler, offset, semaphore):
    """单个批次任务"""
    async with semaphore:
        data = await crawler.fetch_page(limit=50, offset=offset)
        
        # 👇 加这行日志
        print(f"📄 Offset {offset}: 抓到 {len(data)} 条数据")
        
        if not data:
            return 0
        await crawler.save_batch(data)
        return len(data)

async def run_batch_crawl():
    crawler = PolymarketCrawler()
    
    # --- 参数配置 ---
    TOTAL_TARGET = 1000   # 目标抓取数量
    BATCH_SIZE = 50       # 每页数量
    CONCURRENCY = 5       # 🔥 并发数：同时发 5 个请求
    
    print(f"🚀 启动极速爬虫 | 目标: {TOTAL_TARGET} | 并发: {CONCURRENCY}")
    
    semaphore = asyncio.Semaphore(CONCURRENCY)
    offsets = range(0, TOTAL_TARGET, BATCH_SIZE)
    
    tasks = []
    for offset in offsets:
        task = process_batch_task(crawler, offset, semaphore)
        tasks.append(task)
    
    try:
        t_start = time.time()
        results = await asyncio.gather(*tasks)
        t_end = time.time()
        
        total = sum(results)
        print("-" * 40)
        print(f"🎉 任务结束！共处理 {total} 条数据")
        print(f"⏱️ 总耗时: {t_end - t_start:.2f}s")
        print(f"🚀 平均速度: {total / (t_end - t_start):.2f} 条/秒")
            
    finally:
        await crawler.close()

if __name__ == "__main__":
    asyncio.run(run_batch_crawl())