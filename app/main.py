"""FastAPI 应用主入口"""
from typing import Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import bindparam, desc, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.endpoints import cards
from app.core.config import settings
from app.db.session import get_db
from app.models.event_card import EventCard
from app.models.event_snapshot import EventSnapshot
from app.services.crawler import run_batch_crawl

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="AI Prediction Oracle Backend API",
)

# CORS 配置 - 允许前端跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 开发环境允许所有源
    allow_credentials=True,
    allow_methods=["*"],  # 允许所有 HTTP 方法
    allow_headers=["*"],  # 必须是 "*" 或包含 "ngrok-skip-browser-warning"
)

# 注册路由
# 路由路径: /card/list, /card/details
app.include_router(
    cards.router,
    prefix="/card",
    tags=["Cards"],
)


@app.on_event("startup")
async def startup_event():
    """应用启动事件"""
    print("🚀 Polymarket Backend Ready")
    print(f"📚 API Documentation: http://localhost:8000/docs")
    print(f"🔍 ReDoc: http://localhost:8000/redoc")


@app.get("/")
async def root():
    """根路径"""
    return {
        "message": "AI Prediction Oracle API",
        "version": settings.VERSION,
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    """健康检查"""
    return {"status": "healthy"}


@app.get("/api/v1/cards")
async def get_cards(
    page: int = Query(1, ge=1, description="页码"),
    pageSize: int = Query(20, ge=1, description="每页数量"),
    tag_id: Optional[str] = Query(None, description="第三方标签 ID 过滤（Polymarket 的 tag id）"),
    db: AsyncSession = Depends(get_db),
):
    """
    获取卡片列表（简化版）
    
    - **page**: 页码（从 1 开始）
    - **pageSize**: 每页数量
    - **tag_id**: 可选的第三方标签 ID 过滤（从 Polymarket API 的 raw_data.tags 中获取）
    """
    offset = (page - 1) * pageSize
    
    # 构建基础查询，预加载 predictions 关系
    query = (
        select(EventCard)
        .options(selectinload(EventCard.predictions))
        .where(EventCard.is_active == True)
    )
    
    # 如果传了 tag_id，从 EventSnapshot 的 raw_data JSONB 中过滤
    # 使用 PostgreSQL 的 JSONB 查询：检查 tags 数组中是否有 id 匹配的元素
    if tag_id:
        # JOIN EventSnapshot 表，并使用 JSONB 查询条件
        # 检查 raw_data->'tags' 数组中是否有任何一个 tag 的 id 等于传入的 tag_id
        # 注意：tags 是一个数组，每个元素是 {id, label, slug, ...} 对象
        query = query.join(
            EventSnapshot,
            EventCard.polymarket_id == EventSnapshot.polymarket_id
        ).where(
            text("""
                EXISTS (
                    SELECT 1 
                    FROM jsonb_array_elements(event_snapshots.raw_data->'tags') AS tag
                    WHERE tag->>'id' = :tag_id
                )
            """).bindparams(bindparam("tag_id", tag_id))
        ).distinct()
    
    # 加上分页和排序
    query = query.offset(offset).limit(pageSize).order_by(desc(EventCard.created_at))
    
    result = await db.execute(query)
    cards = result.scalars().all()
    
    # 转换为字典格式，包含 aiLogicSummary 字段
    cards_data = []
    for card in cards:
        card_dict = {
            "id": card.polymarket_id,
            "slug": card.slug,
            "title": card.title,
            "description": card.description,
            "image_url": card.image_url,
            "volume": float(card.volume) if card.volume else None,
            "end_date": card.end_date.isoformat() if card.end_date else None,
            "is_active": card.is_active,
            "created_at": card.created_at.isoformat() if card.created_at else None,
            "updated_at": card.updated_at.isoformat() if card.updated_at else None,
            "aiLogicSummary": None,  # 默认值
        }
        
        # 从 predictions 中取最新的 summary 作为 aiLogicSummary
        # predictions 已经按 created_at 降序排序，所以第一个就是最新的
        if card.predictions and len(card.predictions) > 0:
            latest_prediction = card.predictions[0]
            card_dict["aiLogicSummary"] = latest_prediction.summary
        
        cards_data.append(card_dict)
    
    return cards_data


@app.post("/api/admin/trigger-update")
async def trigger_update(background_tasks: BackgroundTasks, secret: str):
    """
    触发后台爬虫更新任务（Cron 友好，使用 BackgroundTasks 防止超时）
    
    - **secret**: 管理员密钥（从环境变量 ADMIN_SECRET_KEY 读取）
    
    Example:
        curl -X POST "https://your-app.railway.app/api/admin/trigger-update?secret=your_secret_key"
    """
    # 验证管理员密钥
    if secret != settings.ADMIN_SECRET_KEY:
        return {"error": "Invalid secret key", "status": "unauthorized"}

    # 将爬虫任务加入后台队列（立即返回，防止 HTTP 超时）
    background_tasks.add_task(run_batch_crawl)

    return {"message": "Crawler task queued successfully", "status": "ok"}
