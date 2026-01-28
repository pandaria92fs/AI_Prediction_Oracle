"""FastAPI 应用主入口"""
from typing import Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.endpoints import cards
from app.core.config import settings
from app.db.session import get_db
from app.models.card_tag import card_tags
from app.models.event_card import EventCard
from app.models.tag import Tag
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
    tag_id: Optional[str] = Query(None, description="标签 ID 过滤"),
    db: AsyncSession = Depends(get_db),
):
    """
    获取卡片列表（简化版）
    
    - **page**: 页码（从 1 开始）
    - **pageSize**: 每页数量
    - **tag_id**: 可选的标签 ID 过滤
    """
    offset = (page - 1) * pageSize
    
    # 构建基础查询，预加载 predictions 关系
    query = (
        select(EventCard)
        .options(selectinload(EventCard.predictions))
        .where(EventCard.is_active == True)
    )
    
    # 如果传了 tag_id，就加过滤条件（通过 JOIN 关联表）
    if tag_id:
        query = query.join(
            card_tags, EventCard.id == card_tags.c.card_id
        ).join(
            Tag, card_tags.c.tag_id == Tag.id
        ).where(Tag.id == int(tag_id))
    
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
    触发后台爬虫更新任务
    
    - **secret**: 密码保护（防止未授权访问）
    """
    # 简单的密码保护，防止路人乱点
    if secret != "my_super_secret_password":
        return {"error": "密码错误"}

    # 将爬虫任务加入后台队列
    background_tasks.add_task(run_batch_crawl)

    return {"message": "已触发后台更新任务，请稍后查看日志"}
