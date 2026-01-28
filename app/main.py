"""FastAPI 应用主入口"""
from fastapi import BackgroundTasks, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.endpoints import cards
from app.core.config import settings
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
