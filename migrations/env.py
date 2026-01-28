import asyncio
from logging.config import fileConfig
import sys
from pathlib import Path
import os
from dotenv import load_dotenv # 确保引入

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from alembic import context

# 1. 路径设置
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 2. 强制加载 .env
load_dotenv()

# 读取配置
config = context.config

# 3. 获取 URL
db_url = os.environ.get("DATABASE_URL")

# --- 🔍 调试打印 (关键) ---
print(f"-------------- DEBUG INFO --------------")
print(f"原始 URL: {db_url}")

if not db_url:
    raise ValueError("❌ Error: DATABASE_URL is missing in .env!")

# 4. 暴力修复逻辑 (不管开头是 postgres 还是 postgresql，统统加驱动)
if "asyncpg" not in db_url:
    print("⚠️ 检测到 URL 缺少驱动，正在尝试自动修复...")
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

print(f"最终 URL: {db_url}")
print(f"----------------------------------------")

# 设置给 Alembic
config.set_main_option("sqlalchemy.url", db_url)

# 日志配置
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# -------------------- [ 模型导入 ] --------------------
# 1. 从【定义它的地方】直接导入 Base，而不是从 app.models
from app.db.base import Base

# 2. 显式导入所有模型类
# 这一步非常关键！只有导入了它们，Base.metadata 才能感知到表的存在
from app.models.event_card import EventCard
from app.models.event_snapshot import EventSnapshot
from app.models.tag import Tag
from app.models.card_tag import CardTag
from app.models.ai_prediction import AIPrediction

# 3. 设置 target_metadata
target_metadata = Base.metadata
# -------------------- [ 模型导入结束 ] --------------------

def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()

def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()

async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        # 必须加这一行，否则 Supabase/PgBouncer 会报 DuplicatePreparedStatementError
        connect_args={"statement_cache_size": 0},
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()

def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()