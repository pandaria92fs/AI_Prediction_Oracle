"""Card API 的 Pydantic 模式定义"""
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class TagItem(BaseModel):
    """标签项"""

    id: str
    label: str
    slug: str

    class Config:
        populate_by_name = True
        from_attributes = True


class AIAnalysis(BaseModel):
    """AI 分析详情"""
    structural_anchor: Optional[str] = Field(None, serialization_alias="structuralAnchor")
    noise: Optional[str] = None
    barrier: Optional[str] = None
    blindspot: Optional[str] = None

    class Config:
        populate_by_name = True


class MarketItem(BaseModel):
    """市场项（从 raw_data 中提取）"""

    id: str
    question: str
    outcomes: List[str]
    current_prices: Dict[str, float] = Field(default_factory=dict, serialization_alias="currentPrices")
    volume: Optional[float] = None
    liquidity: Optional[float] = None
    active: bool = True

    # 新增字段（按前端 Mock 要求）
    probability: float = 0.0
    adjusted_probability: float = Field(0.0, serialization_alias="adjustedProbability")
    tag_ids: List[str] = Field(default_factory=list, serialization_alias="tagIds")
    group_item_title: Optional[str] = Field(None, serialization_alias="groupItemTitle")
    icon: Optional[str] = None
    archived: bool = False
    
    # AI 分析字段
    ai_confidence: Optional[float] = Field(None, serialization_alias="aiConfidence")
    ai_analysis: Optional[AIAnalysis] = Field(None, serialization_alias="aiAnalysis")

    @field_validator("outcomes", mode="before")
    @classmethod
    def parse_outcomes_json(cls, v):
        """解析 outcomes JSON 字符串为列表"""
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                return parsed if isinstance(parsed, list) else []
            except (json.JSONDecodeError, TypeError, ValueError):
                return []
        return v if isinstance(v, list) else []

    @field_validator("current_prices", mode="before")
    @classmethod
    def parse_current_prices(cls, v):
        """解析 currentPrices"""
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError, ValueError):
                return {}
        return v if isinstance(v, dict) else {}

    @model_validator(mode="before")
    @classmethod
    def compute_probabilities(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        """
        从 outcomePrices 计算 probability
        adjusted_probability 优先使用 AI 分析数据，否则使用 probability
        """
        # 处理 currentPrices 字段映射
        if "currentPrices" in values and "current_prices" not in values:
            values["current_prices"] = values.pop("currentPrices")
        
        # 处理 tagIds 字段映射
        if "tagIds" in values and "tag_ids" not in values:
            values["tag_ids"] = values.pop("tagIds")
        
        # 处理 groupItemTitle 字段映射
        if "groupItemTitle" in values and "group_item_title" not in values:
            values["group_item_title"] = values.pop("groupItemTitle")
        
        outcome_prices = values.get("outcomePrices")

        # 如果是字符串，先尝试解析为列表
        if isinstance(outcome_prices, str):
            try:
                outcome_prices = json.loads(outcome_prices)
            except (json.JSONDecodeError, TypeError, ValueError):
                outcome_prices = []

        prob = 0.0
        if isinstance(outcome_prices, (list, tuple)) and outcome_prices:
            try:
                prob = float(outcome_prices[0])
            except (TypeError, ValueError):
                prob = 0.0

        values["probability"] = prob
        
        # 优先使用 AI 调整后的概率，否则使用原始 probability
        ai_prob = values.get("ai_adjusted_probability")
        if ai_prob is not None:
            # AI 概率是百分比（如 56.5），需要转为小数（0.565）
            values["adjusted_probability"] = float(ai_prob) / 100.0
        else:
            values["adjusted_probability"] = prob
        
        # AI 置信度
        ai_confidence = values.get("ai_confidence")
        if ai_confidence is not None:
            values["ai_confidence"] = float(ai_confidence)
        
        # AI 分析详情
        ai_analysis_data = values.get("ai_analysis_data")
        if ai_analysis_data and isinstance(ai_analysis_data, dict):
            values["ai_analysis"] = {
                "structural_anchor": ai_analysis_data.get("structuralAnchor"),
                "noise": ai_analysis_data.get("noise"),
                "barrier": ai_analysis_data.get("barrier"),
                "blindspot": ai_analysis_data.get("blindspot"),
            }
        
        return values

    class Config:
        populate_by_name = True
        from_attributes = True


class CardData(BaseModel):
    """卡片数据"""

    id: str  # 公开的 ID，对应 polymarket_id
    slug: str
    title: str
    description: Optional[str] = None
    # 前端字段名为 icon，ORM 字段为 image_url
    icon: Optional[str] = Field(default=None, validation_alias="image_url")
    volume: Optional[float] = None
    liquidity: Optional[float] = None
    active: bool = True
    closed: bool = False
    startDate: Optional[str] = None
    endDate: Optional[str] = None
    createdAt: Optional[datetime] = None
    updatedAt: Optional[datetime] = None
    # AI 分析摘要（从 ai_predictions 表获取）
    aILogicSummary: Optional[str] = None
    # AI 调整后的概率（百分比，如 56.5 表示 56.5%）
    adjustedProbability: Optional[float] = None

    tags: List[TagItem] = []
    markets: List[MarketItem] = []

    @model_validator(mode="after")
    def process_markets(self) -> "CardData":
        """
        同步标签到 markets，并对 markets 进行过滤与排序：
        1. 将父级 tags 的 id 同步到每个 market 的 tagIds（如果其为空）
        2. 仅保留 active=True 且 archived=False 的 markets
        3. 按 probability 降序排序，volume 作为次级排序键
        """
        if not self.markets:
            return self

        # 1. Sync Tags: 将父级 tags 的 id 同步到 market.tag_ids
        if self.tags:
            tag_ids = [t.id for t in self.tags]
            for m in self.markets:
                if not m.tag_ids:
                    m.tag_ids = tag_ids

        # 2. 过滤：仅保留 active=True 且 archived=False 的 markets
        valid_markets: List[MarketItem] = [
            m
            for m in self.markets
            if m.active is True and getattr(m, "archived", False) is False
        ]

        # 3. 排序：按 probability 降序，volume 为次级排序键
        valid_markets.sort(
            key=lambda x: (x.probability, x.volume or 0.0),
            reverse=True,
        )

        self.markets = valid_markets
        return self

    class Config:
        populate_by_name = True
        from_attributes = True


class StandardResponse(BaseModel):
    """标准 API 响应格式"""

    code: int = 200
    message: str = "success"
    data: Any = None

    class Config:
        populate_by_name = True
        from_attributes = True


class CardListPayload(BaseModel):
    """卡片列表数据载体，符合前端期望结构"""

    total: int
    page: int
    pageSize: int
    list: List[CardData]

    class Config:
        populate_by_name = True
        from_attributes = True


class CardListResponse(StandardResponse):
    """卡片列表响应"""

    data: CardListPayload


class CardDetailsResponse(StandardResponse):
    """卡片详情响应"""

    data: CardData
