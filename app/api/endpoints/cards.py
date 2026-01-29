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


def _extract_markets_from_raw_data(raw_data: dict, ai_markets: dict = None) -> list:
    """
    [最终修正版] 从 raw_data 提取 markets
    1. 包含 outcomePrices 的 JSON 解析兜底
    2. 包含 AI 数据的归一化处理 (0-100 -> 0-1)
    """
    import json
    markets = raw_data.get("markets", [])
    ai_markets = ai_markets or {}
    result = []
    for market in markets:
        market_id = market.get("id", "")
        
        # --- 1. 顽固的概率获取逻辑 ---
        probability = 0.0
        if "probability" in market:
            probability = float(market["probability"] or 0)
        
        if probability == 0.0:
            outcome_prices = market.get("outcomePrices")
            if outcome_prices:
                try:
                    if isinstance(outcome_prices, str):
                        outcome_prices = json.loads(outcome_prices)
                    if isinstance(outcome_prices, list) and len(outcome_prices) > 0:
                        probability = float(outcome_prices[0])
                except:
                    pass
        
        # --- 2. 基础数据 ---
        market_data = {
            "id": market_id,
            "question": market.get("question", ""),
            "outcomes": market.get("outcomes", []),
            "currentPrices": market.get("currentPrices", {}),
            "volume": float(market.get("volume") or 0),
            "liquidity": float(market.get("liquidity") or 0),
            "active": market.get("active", True),
            "groupItemTitle": market.get("groupItemTitle"),
            "icon": market.get("icon"),
            "outcomePrices": market.get("outcomePrices"),
            "probability": probability,
        }
        
        # --- 3. AI 数据注入 ---
        ai_adj_prob = None
        
        if "adjustedProbability" in market:
            ai_adj_prob = market["adjustedProbability"]
        elif market_id in ai_markets:
            ai_data = ai_markets[market_id]
            if "ai_calibrated_odds_pct" in ai_data:
                ai_adj_prob = ai_data["ai_calibrated_odds_pct"]
            if "ai_confidence" in ai_data:
                market_data["ai_confidence"] = float(ai_data["ai_confidence"])
            market_data["ai_analysis_data"] = {
                "structuralAnchor": ai_data.get("anchor") or ai_data.get("structural_anchor"),
                "noise": ai_data.get("noise") or ai_data.get("the_noise"),
                "barrier": ai_data.get("barrier") or ai_data.get("the_barrier"),
                "blindspot": ai_data.get("blindspot") or ai_data.get("the_blindspot"),
            }
        
        # --- 4. 归一化 0-1 ---
        if ai_adj_prob is not None:
            val = float(ai_adj_prob)
            if val > 1.0:
                val = val / 100.0
            market_data["ai_adjusted_probability"] = val
        
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
    ai_markets = {}  # market_id -> AI 分析数据
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
        # 解析 raw_analysis 获取每个 market 的 AI 概率
        if latest.raw_analysis:
            try:
                import json
                ai_markets = json.loads(latest.raw_analysis)
            except (json.JSONDecodeError, TypeError):
                ai_markets = {}
    
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
        "markets": _extract_markets_from_raw_data(raw_data, ai_markets),
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
        
        # 优化：使用 LEFT JOIN + IS NULL 代替 NOT IN（性能更好）
        # 子查询：找出 sports 标签的 ID
        from sqlalchemy.orm import aliased
        sports_card_tags = aliased(card_tags, name="sports_ct")
        sports_tag_ids = select(Tag.id).where(Tag.name.ilike("%sport%")).scalar_subquery()
        
        base_query = (
            select(EventCard)
            .outerjoin(
                sports_card_tags,
                (EventCard.id == sports_card_tags.c.card_id) & 
                (sports_card_tags.c.tag_id.in_(select(Tag.id).where(Tag.name.ilike("%sport%"))))
            )
            .options(
                selectinload(EventCard.tags),
                selectinload(EventCard.predictions),
            )
            .where(EventCard.is_active == True)
            .where(EventCard.is_active.isnot(None))
            .where(EventCard.is_closed == False)
            .where(EventCard.is_closed.isnot(None))
            .where(EventCard.is_archived == False)
            .where(EventCard.is_archived.isnot(None))
            .where(sports_card_tags.c.card_id.is_(None))  # 排除有 sports 标签的
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

        # -------- 2. Count 查询：与 base_query 过滤条件一致 --------
        t_count_start = time.perf_counter()
        # 基础过滤条件（与 base_query 一致）
        base_filters = [
            EventCard.is_active == True,
            EventCard.is_active.isnot(None),
            EventCard.is_closed == False,
            EventCard.is_closed.isnot(None),
            EventCard.is_archived == False,
            EventCard.is_archived.isnot(None),
        ]
        
        if tagId:
            # 标签过滤：使用 COUNT(DISTINCT) 避免 JOIN 导致的重复计数
            count_query = (
                select(func.count(func.distinct(EventCard.id)))
                .select_from(EventCard)
                .outerjoin(
                    sports_card_tags,
                    (EventCard.id == sports_card_tags.c.card_id) & 
                    (sports_card_tags.c.tag_id.in_(select(Tag.id).where(Tag.name.ilike("%sport%"))))
                )
                .join(card_tags, EventCard.id == card_tags.c.card_id)
                .join(Tag, card_tags.c.tag_id == Tag.id)
                .where(*base_filters)
                .where(sports_card_tags.c.card_id.is_(None))
                .where(Tag.polymarket_id == str(tagId))
            )
        else:
            # 无标签过滤：排除 sports + 基础过滤
            count_query = (
                select(func.count(func.distinct(EventCard.id)))
                .select_from(EventCard)
                .outerjoin(
                    sports_card_tags,
                    (EventCard.id == sports_card_tags.c.card_id) & 
                    (sports_card_tags.c.tag_id.in_(select(Tag.id).where(Tag.name.ilike("%sport%"))))
                )
                .where(*base_filters)
                .where(sports_card_tags.c.card_id.is_(None))
            )
        
        # 直接执行 count 查询
        total_result = await db.execute(count_query)
        total_count = total_result.scalar_one() or 0
        t_count_end = time.perf_counter()
        print(f"📊 [Step 1] Count 查询耗时: {(t_count_end - t_count_start) * 1000:.2f}ms (Total: {total_count})")

        # -------- 3. 排序优化：先按 volume DESC 获取候选集（最热 100 条） --------
        # 混合排序需要在候选集上进行，而不是直接分页
        CANDIDATE_POOL_SIZE = 100  # 候选池大小，避免对全库重计算
        
        query = base_query.order_by(desc(EventCard.volume)).limit(CANDIDATE_POOL_SIZE)

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

        # -------- 7. 混合加权排序：volume + AI alpha (性能优化版) --------
        t_sort_start = time.perf_counter()

        def _normalize_prob(val) -> float:
            """归一化概率到 0.0-1.0 范围"""
            if val is None:
                return 0.0
            v = float(val)
            if v > 1.0:
                v = v / 100.0
            return max(0.0, min(1.0, v))

        # === 预计算阶段：一次性为所有卡片计算分数，避免 sorted() 内重复计算 ===
        for card in card_data_list:
            # 1. Volume Score（显式 float 转换）
            vol = float(card.get("volume") or 0)
            card["_volume_score"] = round(vol, 2)
            
            # 2. Alpha Score = volume × max_diff（预计算归一化差值）
            alpha_score = 0.0
            if vol > 0:
                diffs = []
                for m in card.get("markets", []):
                    prob = _normalize_prob(m.get("probability", 0.0))
                    adj_prob = m.get("ai_adjusted_probability") or m.get("adjustedProbability")
                    if adj_prob is None:
                        curr_ai = prob  # 无 AI 数据时，diff = 0
                    else:
                        curr_ai = _normalize_prob(adj_prob)
                    diff = abs(prob - curr_ai)
                    diffs.append(diff)
                # 取最大的两个差值
                diffs.sort(reverse=True)
                top_diffs = diffs[:2] if len(diffs) >= 2 else diffs
                # 显式 float 转换 + round 防止 Decimal 精度抖动
                alpha_score = round(float(vol) * float(sum(top_diffs)), 2)
            card["_alpha_score"] = alpha_score

        # === 验证阶段：确保预计算分数无 None ===
        for card in card_data_list:
            assert card.get("_volume_score") is not None, f"_volume_score is None for {card.get('id')}"
            assert card.get("_alpha_score") is not None, f"_alpha_score is None for {card.get('id')}"
            assert isinstance(card["_alpha_score"], (int, float)), f"_alpha_score is not float: {type(card['_alpha_score'])}"

        # === 排序阶段：直接使用预计算的分数（O(1) 访问） ===
        list_volume = sorted(card_data_list, key=lambda c: c["_volume_score"], reverse=True)
        list_alpha = sorted(card_data_list, key=lambda c: c["_alpha_score"], reverse=True)

        # 调试：打印前 5 名的排序情况（含分数）
        print(f"   📊 Volume Top5: {[(c.get('id')[:8], c['_volume_score']) for c in list_volume[:5]]}")
        print(f"   📊 Alpha Top5:  {[(c.get('id')[:8], c['_alpha_score']) for c in list_alpha[:5]]}")
        
        # 验证：检查 Volume 和 Alpha Top10 是否完全一致（用于测试去重逻辑）
        vol_top10_ids = [c.get('id') for c in list_volume[:10]]
        alpha_top10_ids = [c.get('id') for c in list_alpha[:10]]
        overlap_count = len(set(vol_top10_ids) & set(alpha_top10_ids))
        print(f"   🔍 Top10 重叠度: {overlap_count}/10 (相同事件数)")

        # 精确交替插值：Index 0 -> volume[0], Index 1 -> alpha[0], Index 2 -> volume[1], ...
        final_list = []
        used_ids = set()
        ptr_vol, ptr_alpha = 0, 0
        turn_volume = True  # 从 volume 开始
        target_size = len(card_data_list)

        while len(final_list) < target_size:
            added = False
            
            if turn_volume:
                # 从 list_volume 取下一个未使用的
                while ptr_vol < len(list_volume):
                    card = list_volume[ptr_vol]
                    ptr_vol += 1
                    if card.get("id") not in used_ids:
                        final_list.append(card)
                        used_ids.add(card.get("id"))
                        added = True
                        break
            else:
                # 从 list_alpha 取下一个未使用的（去重保护：自动顺延）
                while ptr_alpha < len(list_alpha):
                    card = list_alpha[ptr_alpha]
                    ptr_alpha += 1
                    if card.get("id") not in used_ids:
                        final_list.append(card)
                        used_ids.add(card.get("id"))
                        added = True
                        break
            
            # 交替切换
            turn_volume = not turn_volume
            
            # 安全检查：如果两个列表都耗尽且没有添加新元素，退出循环
            if not added and ptr_vol >= len(list_volume) and ptr_alpha >= len(list_alpha):
                break

        # -------- 8. 应用分页（在混合排序后） --------
        offset = (page - 1) * pageSize
        card_data_list = final_list[offset:offset + pageSize]
        
        t_sort_end = time.perf_counter()
        print(f"🔀 [Step 4] 混合加权排序完成: {(t_sort_end - t_sort_start) * 1000:.2f}ms (候选池: {len(final_list)}, 返回: {len(card_data_list)})")
        
        # 调试：打印当前页的交替情况（前 10 条）
        debug_slice = final_list[offset:offset + min(10, pageSize)]
        for i, c in enumerate(debug_slice):
            src = "VOL" if i % 2 == 0 else "ALP"
            print(f"   [{offset + i}] {src}: {c.get('id')[:12]}... vol={c.get('volume', 0):.0f}")

        # -------- 9. 诊断 Pydantic 序列化耗时 --------
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


