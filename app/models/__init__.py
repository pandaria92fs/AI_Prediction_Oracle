"""数据库模型模块（Application-First Schema）"""

from app.models.ai_prediction import AIPrediction
from app.models.card_tag import CardTag, card_tags
from app.models.event_card import EventCard
from app.models.event_snapshot import EventSnapshot
from app.models.tag import Tag

__all__ = [
    "EventSnapshot",
    "EventCard",
    "Tag",
    "CardTag",
    "card_tags",
    "AIPrediction",
]
