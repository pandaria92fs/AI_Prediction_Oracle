"""EventCard <-> Tag 关联表"""

from sqlalchemy import BigInteger, Column, ForeignKey, Table
from sqlalchemy.orm import Mapped

from app.db.base import Base

card_tags = Table(
    "card_tags",
    Base.metadata,
    Column(
        "card_id",
        BigInteger,
        ForeignKey("event_cards.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "tag_id",
        BigInteger,
        ForeignKey("tags.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class CardTag(Base):
    """card_tags 的 ORM 映射（便于查询/插入）"""

    __table__ = card_tags

    # typing only (列来自 __table__)
    card_id: Mapped[int]
    tag_id: Mapped[int]

