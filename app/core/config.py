"""应用配置管理模块"""
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置类，从环境变量加载配置"""

    # 数据库配置
    DATABASE_URL: str = "postgresql+asyncpg://user:password@localhost:5432/dbname"
    
    # API 配置
    API_V1_PREFIX: str = "/api/v1"
    PROJECT_NAME: str = "AI Prediction Oracle"
    VERSION: str = "0.1.0"
    
    # 日志配置
    LOG_LEVEL: str = "INFO"
    
    # CORS 配置
    CORS_ORIGINS: list[str] = ["*"]
    
    # 其他配置
    DEBUG: bool = False
    
    # 管理员密钥（用于触发爬虫等敏感操作）
    ADMIN_SECRET_KEY: str = "change_me_in_production"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()
