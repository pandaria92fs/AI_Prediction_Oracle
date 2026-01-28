# Alembic 数据库迁移设置指南

## 1. 初始化 Alembic

在项目根目录执行以下命令：

```bash
alembic init alembic
```

这会在项目根目录创建 `alembic/` 文件夹和 `alembic.ini` 配置文件。

## 2. 配置 alembic.ini

编辑 `alembic.ini` 文件，修改以下配置：

```ini
# 数据库 URL（将从环境变量读取，这里可以留空或设置默认值）
# sqlalchemy.url = driver://user:pass@localhost/dbname

# 或者直接从环境变量读取（推荐）
# 在 env.py 中配置
```

## 3. 配置 alembic/env.py

编辑 `alembic/env.py` 文件，替换为以下内容以支持异步 SQLAlchemy：

```python
from logging.config import fileConfig
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# 导入应用配置和模型
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.db.base import Base

# this is the Alembic Config object
config = context.config

# 设置数据库 URL
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL.replace("+asyncpg", ""))

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 设置目标元数据
target_metadata = Base.metadata

# 其他配置对象
def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    # 对于异步引擎，我们需要使用 sync 模式进行迁移
    # Alembic 目前不完全支持异步，所以我们需要同步连接
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async def run_async_migrations():
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
            await connection.commit()

    def do_run_migrations(connection: Connection) -> None:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()

    import asyncio
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

## 4. 创建初始迁移

执行以下命令创建初始迁移：

```bash
alembic revision --autogenerate -m "Initial migration"
```

## 5. 应用迁移

执行以下命令应用迁移到数据库：

```bash
alembic upgrade head
```

## 6. 常用命令

- **创建新迁移**: `alembic revision --autogenerate -m "描述信息"`
- **查看迁移历史**: `alembic history`
- **升级到最新版本**: `alembic upgrade head`
- **降级一个版本**: `alembic downgrade -1`
- **降级到特定版本**: `alembic downgrade <revision>`
- **查看当前版本**: `alembic current`

## 注意事项

1. **环境变量**: 确保 `.env` 文件中设置了正确的 `DATABASE_URL`
2. **异步支持**: Alembic 的异步支持有限，上述配置使用同步连接进行迁移
3. **模型导入**: 确保 `app/db/base.py` 中导入了所有模型，这样 Alembic 才能发现它们
4. **JSONB 字段**: PostgreSQL 的 JSONB 字段会自动被 Alembic 识别
