"""Presentation Layer: EventCard"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.card_tag import card_tags


class EventCard(Base):
    __tablename__ = "event_cards"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    polymarket_id: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        index=True,
        nullable=False,
    )

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)

    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)

    volume: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 2), nullable=True)
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    tags: Mapped[List["Tag"]] = relationship(
        "Tag",
        secondary=card_tags,
        back_populates="cards",
        lazy="selectin",
    )
    predictions: Mapped[List["AIPrediction"]] = relationship(
        "AIPrediction",
        back_populates="card",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="desc(AIPrediction.created_at)",
    )

