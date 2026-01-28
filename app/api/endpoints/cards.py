"""Card API 端点"""
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.decorators import profile_endpoint
from app.db.session import get_db
from app.models.ai_prediction import AIPrediction
from app.models.card_tag import card_tags
from app.models.event_card import EventCard
from app.models.event_snapshot import EventSnapshot
from app.models.tag import Tag
from app.schemas.card import (
    CardData,
    CardDetailsResponse,
    CardListPayload,
    CardListResponse,
)

router = APIRouter()


def _extract_markets_from_raw_data(raw_data: dict) -> list:
    """从 raw_data 中提取 markets 列表"""
    markets = raw_data.get("markets", [])
    result = []
    for market in markets:
        # 提取所有关键字段，包括新增的字段
        market_data = {
            "id": market.get("id", ""),
            "question": market.get("question", ""),
            "outcomes": market.get("outcomes", []),
            "currentPrices": market.get("currentPrices", {}),
            "volume": market.get("volume"),
            "liquidity": market.get("liquidity"),  # Market 级别的 liquidity
            "active": market.get("active", True),
            # 新增字段（前端 Mock 要求）
            "groupItemTitle": market.get("groupItemTitle"),  # 修复：添加 groupItemTitle
            "icon": market.get("icon"),  # Market 级别的 icon（如果有）
            "outcomePrices": market.get("outcomePrices"),  # 修复：添加 outcomePrices（用于计算 probability）
        }
        result.append(market_data)
    return result


def _extract_tags_from_raw_data(raw_data: dict) -> list:
    """从 raw_data 中提取 tags 列表"""
    tags = raw_data.get("tags", [])
    result = []
    for tag in tags:
        result.append({
            "id": str(tag.get("id", "")),
            "label": tag.get("label", ""),
            "slug": tag.get("slug", ""),
        })
    return result


def _build_card_data(card: EventCard, snapshot: Optional[EventSnapshot] = None, predictions: Optional[list] = None) -> dict:
    """构建卡片数据对象"""
    raw_data = snapshot.raw_data if snapshot else {}
    
    # 格式化日期字段
    def format_date(date_value):
        """格式化日期为 ISO 字符串"""
        if date_value is None:
            return None
        if isinstance(date_value, str):
            return date_value
        # 如果是 datetime 对象，转换为 ISO 格式
        return date_value.isoformat() if hasattr(date_value, 'isoformat') else str(date_value)
    
    # 获取 AI 预测数据（从最新的 prediction 中提取）
    ai_logic_summary = None
    adjusted_probability = None
    if predictions and len(predictions) > 0:
        # predictions 应该按 created_at 降序排序，取第一个
        latest = predictions[0]
        ai_logic_summary = latest.summary
        # outcome_prediction 存的是纯数字，如 "56.5"
        if latest.outcome_prediction:
            try:
                adjusted_probability = float(latest.outcome_prediction)
            except ValueError:
                adjusted_probability = None
    
    # 基础字段从 EventCard 获取，但优先使用 raw_data 中的最新值
    # 修复：icon 字段映射 - 使用 validation_alias，所以这里用 image_url
    card_dict = {
        "id": card.polymarket_id,  # 使用 id 作为公开字段名
        "slug": card.slug,
        "title": card.title,
        "description": card.description or raw_data.get("description"),
        "image_url": card.image_url or raw_data.get("image") or raw_data.get("icon"),  # 修复：优先使用 image，其次 icon
        "volume": float(card.volume) if card.volume else (float(raw_data.get("volume", 0)) if raw_data.get("volume") else None),
        "liquidity": float(raw_data.get("liquidity", 0)) if raw_data.get("liquidity") else None,
        "active": card.is_active,
        "closed": raw_data.get("closed", False),
        "startDate": format_date(raw_data.get("startDate")),
        "endDate": format_date(raw_data.get("endDate")) or format_date(card.end_date),
        "createdAt": card.created_at.isoformat() if card.created_at else None,  # 修复：添加 createdAt
        "updatedAt": card.updated_at.isoformat() if card.updated_at else None,  # 修复：添加 updatedAt
        "tags": _extract_tags_from_raw_data(raw_data),
        "markets": _extract_markets_from_raw_data(raw_data),
        "aILogicSummary": ai_logic_summary,  # AI 分析摘要
        "adjustedProbability": adjusted_probability,  # AI 调整后的概率
    }
    return card_dict


@router.get("/list", response_model=CardListResponse)
@profile_endpoint
async def get_card_list(
    page: int = Query(1, ge=1, description="页码"),
    pageSize: int = Query(20, ge=1, le=100, description="每页数量"),
    tagId: Optional[str] = Query(None, description="标签 ID 过滤"),
    sortBy: str = Query("volume", pattern="^(volume|liquidity)$", description="排序字段"),
    order: str = Query("desc", pattern="^(asc|desc)$", description="排序方向"),
    db: AsyncSession = Depends(get_db),
):
    """
    获取卡片列表
    
    - **page**: 页码（从 1 开始）
    - **pageSize**: 每页数量（1-100）
    - **tagId**: 可选的标签 ID 过滤
    - **sortBy**: 排序字段（volume 或 liquidity）
    - **order**: 排序方向（asc 或 desc）
    """
    print(f"\n⏱️ === 开始详细性能诊断 (Page: {page}, PageSize: {pageSize}) ===")
    overall_start = time.perf_counter()

    try:
        # -------- 1. 构建基础查询（用于列表数据），并预加载关系以避免 N+1 --------
        t_query_build_start = time.perf_counter()
        base_query = (
            select(EventCard)
            .options(
                selectinload(EventCard.tags),
                selectinload(EventCard.predictions),  # 预加载 AI 预测，用于获取 aiLogicSummary
                # 目前 markets 来源于 EventSnapshot.raw_data，这里没有 ORM 关系可预加载
                # 如未来为 Market 建表并建立关系，可在此添加 selectinload(EventCard.markets)
            )
            .where(EventCard.is_active == True)
        )

        # 标签过滤（使用 Polymarket 原始 tag id）
        if tagId:
            base_query = base_query.join(
                card_tags, EventCard.id == card_tags.c.card_id
            ).join(
                Tag, card_tags.c.tag_id == Tag.id
            ).where(Tag.polymarket_id == str(tagId))
        t_query_build_end = time.perf_counter()
        print(f"📋 [Step 0] 查询构建耗时: {(t_query_build_end - t_query_build_start) * 1000:.2f}ms")

        # -------- 2. 优化 Count 查询：直接计数，避免子查询和排序 --------
        t_count_start = time.perf_counter()
        # 构建干净的 count 查询，完全不依赖 base_query，避免任何子查询开销
        if tagId:
            # 标签过滤：使用 COUNT(DISTINCT) 避免 JOIN 导致的重复计数
            count_query = (
                select(func.count(func.distinct(EventCard.id)))
                .select_from(EventCard)
                .join(card_tags, EventCard.id == card_tags.c.card_id)
                .join(Tag, card_tags.c.tag_id == Tag.id)
                .where(EventCard.is_active == True)
                .where(Tag.polymarket_id == str(tagId))
            )
        else:
            # 无过滤：直接计数，最简单最快
            count_query = (
                select(func.count(EventCard.id))
                .where(EventCard.is_active == True)
            )
        
        # 直接执行 count 查询
        total_result = await db.execute(count_query)
        total_count = total_result.scalar_one() or 0
        t_count_end = time.perf_counter()
        print(f"📊 [Step 1] Count 查询耗时: {(t_count_end - t_count_start) * 1000:.2f}ms (Total: {total_count})")

        # -------- 3. 排序（仍在 SQL 层面） --------
        query = base_query
        # 注意：liquidity 存储在 raw_data 中，无法直接在 SQL 层面排序
        # 如果按 liquidity 排序，需要在 Python 层面处理
        if sortBy == "volume":
            sort_column = EventCard.volume
            if order == "desc":
                query = query.order_by(desc(sort_column))
            else:
                query = query.order_by(sort_column)
        else:  # liquidity - 需要在获取数据后排序
            # 先按 volume 排序作为默认，然后在 Python 层面重新排序
            query = query.order_by(desc(EventCard.volume))

        # -------- 4. 分页 --------
        offset = (page - 1) * pageSize
        query = query.offset(offset).limit(pageSize)

        # -------- 5. 诊断 Main Query (DB + 网络) 耗时 --------
        t_query_start = time.perf_counter()
        result = await db.execute(query)
        cards = result.scalars().all()
        t_query_end = time.perf_counter()
        print(f"🐢 [Step 2] 列表 SQL 执行 + 网络传输: {(t_query_end - t_query_start) * 1000:.2f}ms (Cards: {len(cards)})")

        # -------- 6. 优化 Snapshot 查询：使用窗口函数获取最新快照 --------
        t_snap_start = time.perf_counter()
        card_data_list = []
        if cards:
            polymarket_ids = [card.polymarket_id for card in cards]

            # 使用窗口函数子查询：为每个 polymarket_id 找到最新的 created_at
            # 然后 JOIN 回原表获取完整记录（比循环过滤快得多）
            from sqlalchemy import text
            # 使用 PostgreSQL 的 DISTINCT ON（性能最优，但需要原生 SQL）
            snapshots_query = text("""
                SELECT DISTINCT ON (polymarket_id) 
                    id, polymarket_id, raw_data, created_at
                FROM event_snapshots
                WHERE polymarket_id = ANY(:ids)
                ORDER BY polymarket_id, created_at DESC
            """)
            
            snapshots_result = await db.execute(
                snapshots_query, {"ids": polymarket_ids}
            )
            snapshots_rows = snapshots_result.mappings().all()

            # 将结果映射回字典格式（直接使用 raw_data，无需创建 EventSnapshot 对象）
            latest_snapshot_by_id: dict[str, dict] = {}
            for row in snapshots_rows:
                latest_snapshot_by_id[row["polymarket_id"]] = {
                    "raw_data": row["raw_data"],
                    "created_at": row["created_at"],
                }

            # 构建卡片数据
            t_build_start = time.perf_counter()
            for card in cards:
                snapshot_data = latest_snapshot_by_id.get(card.polymarket_id)
                # 创建一个临时 EventSnapshot 对象用于 _build_card_data
                snapshot = None
                if snapshot_data:
                    snapshot = EventSnapshot(
                        polymarket_id=card.polymarket_id,
                        raw_data=snapshot_data["raw_data"],
                        created_at=snapshot_data["created_at"],
                    )
                # 传入 predictions（已通过 selectinload 预加载，按 created_at 降序排序）
                card_dict = _build_card_data(card, snapshot, card.predictions)
                card_data_list.append(card_dict)
            t_build_end = time.perf_counter()
            print(f"🔄 [Step 3] Snapshot 批量查询: {(t_build_start - t_snap_start) * 1000:.2f}ms")
            print(f"   📦 [Step 3.1] 数据构建耗时: {(t_build_end - t_build_start) * 1000:.2f}ms")
        t_snap_end = time.perf_counter()
        print(f"🔄 [Step 3 Total] Snapshot 处理总耗时: {(t_snap_end - t_snap_start) * 1000:.2f}ms")

        # -------- 7. 如果按 liquidity 排序，在 Python 层面排序 --------
        if sortBy == "liquidity":
            t_sort_start = time.perf_counter()
            card_data_list.sort(
                key=lambda x: x.get("liquidity") or 0,
                reverse=(order == "desc"),
            )
            t_sort_end = time.perf_counter()
            print(f"🔀 [Step 4] Python 层面排序耗时: {(t_sort_end - t_sort_start) * 1000:.2f}ms")

        # -------- 8. 诊断 Pydantic 序列化耗时 --------
        t_serialize_start = time.perf_counter()
        card_data_objects = [CardData(**item) for item in card_data_list]
        t_serialize_end = time.perf_counter()
        print(f"🧠 [Step 5] Pydantic 序列化 (CPU): {(t_serialize_end - t_serialize_start) * 1000:.2f}ms")

        # 构建符合前端期望结构的分页载体
        payload = CardListPayload(
            total=total_count,
            page=page,
            pageSize=pageSize,
            list=card_data_objects,
        )

        overall_end = time.perf_counter()
        print(f"🏁 [Total] 总接口逻辑耗时: {(overall_end - overall_start) * 1000:.2f}ms")
        print("=" * 60 + "\n")

        return CardListResponse(
            code=200,
            message="success",
            data=payload,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询失败: {str(e)}")


@router.get("/details", response_model=CardDetailsResponse)
@profile_endpoint
async def get_card_details(
    id: str = Query(..., description="Polymarket Event ID"),
    db: AsyncSession = Depends(get_db),
):
    """
    获取卡片详情
    
    - **id**: Polymarket Event ID（对应 EventCard.polymarket_id）
    """
    try:
        # 查询 EventCard，预加载 predictions
        card_query = (
            select(EventCard)
            .options(selectinload(EventCard.predictions))
            .where(EventCard.polymarket_id == id)
        )
        card_result = await db.execute(card_query)
        card = card_result.scalar_one_or_none()

        if not card:
            raise HTTPException(status_code=404, detail=f"Card with id '{id}' not found")

        # 获取最新的 EventSnapshot
        snapshot_query = (
            select(EventSnapshot)
            .where(EventSnapshot.polymarket_id == id)
            .order_by(desc(EventSnapshot.created_at))
            .limit(1)
        )
        snapshot_result = await db.execute(snapshot_query)
        snapshot = snapshot_result.scalar_one_or_none()

        card_dict = _build_card_data(card, snapshot, card.predictions)

        return CardDetailsResponse(
            code=200,
            message="success",
            data=CardData(**card_dict),
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询失败: {str(e)}")


