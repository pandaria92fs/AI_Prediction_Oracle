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
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)

    cards: Mapped[List["EventCard"]] = relationship(
        "EventCard",
        secondary=card_tags,
        back_populates="tags",
        lazy="selectin",
    )
