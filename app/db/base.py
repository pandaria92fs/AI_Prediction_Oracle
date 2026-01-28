"""数据库基类和模型导入模块"""
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    """SQLAlchemy 2.0 Declarative Base"""


__all__ = ["Base"]
