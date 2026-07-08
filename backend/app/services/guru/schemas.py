from typing import Literal

from pydantic import BaseModel


class PositionVerdict(BaseModel):
    symbol: str
    action: Literal["hold", "increase", "reduce", "exit"]
    conviction: Literal["low", "med", "high"]
    rationale: str


class ReviewPayload(BaseModel):
    positions: list[PositionVerdict]
    observations: list[str]
    watch_next: list[str]
    disclaimer: str


class EarningsItem(BaseModel):
    symbol: str
    date: str | None
    note: str


class MoverItem(BaseModel):
    symbol: str
    note: str


class NewsFlag(BaseModel):
    symbol: str | None
    headline: str
    comment: str


class DigestPayload(BaseModel):
    earnings_this_week: list[EarningsItem]
    movers: list[MoverItem]
    news_flags: list[NewsFlag]
    summary: str
    disclaimer: str


class RiskItem(BaseModel):
    kind: str
    note: str


class IdeaItem(BaseModel):
    symbol: str | None
    action: Literal["hold", "increase", "reduce", "exit"]
    conviction: Literal["low", "med", "high"]
    rationale: str


class TakePayload(BaseModel):
    commentary: str
    risks: list[RiskItem]
    ideas: list[IdeaItem]
    disclaimer: str
