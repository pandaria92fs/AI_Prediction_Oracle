"""Tag 模型"""

from __future__ import annotations

from typing import List

from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.card_tag import card_tags


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Polymarket 原始标签 ID（字符串形式，例如 "100196"、"2"）
    polymarket_id: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
        index=True,
    )
    # 存储 Polymarket 的 slug（如 "politics"）
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    cards: Mapped[List["EventCard"]] = relationship(
        "EventCard",
        secondary=card_tags,
        back_populates="tags",
        lazy="selectin",
    )
