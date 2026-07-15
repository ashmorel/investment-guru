from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, model_validator


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


class FundVerdict(BaseModel):
    code: str
    action: Literal["keep", "increase", "reduce", "exit"]
    conviction: Literal["low", "med", "high"]
    rationale: str


class SwitchStep(BaseModel):
    from_code: str | None
    to_code: str | None
    note: str


class OrsoAdvicePayload(BaseModel):
    fund_verdicts: list[FundVerdict]
    switch_plan: list[SwitchStep]
    projection_comment: str
    watch: list[str]
    disclaimer: str
    contribution_suggestion: str


class ExtractedFundRow(BaseModel):
    fund_code: str
    fund_name: str | None = None
    units: str | None = None            # decimal-as-string; None if not shown
    value: str | None = None
    currency: str | None = None
    contribution_pct: str | None = None


class OrsoStatementExtraction(BaseModel):
    rows: list[ExtractedFundRow]


class NewsSummaryPayload(BaseModel):
    summary: str
    sentiment: Literal["positive", "negative", "neutral", "watch"]
    key_points: list[str]
    disclaimer: str


class GroupObservation(BaseModel):
    name: str
    weight_pct: str
    observation: str
    signal: Literal["favour", "trim", "hold"]


class Rotation(BaseModel):
    from_group: str
    to_group: str
    rationale: str
    conviction: Literal["low", "med", "high"]


class RotationAdvicePayload(BaseModel):
    market_view: str
    groups: list[GroupObservation]
    rotations: list[Rotation]
    caveats: list[str]
    disclaimer: str


class HoldingDecision(BaseModel):
    symbol: str
    action: Literal["hold", "increase", "reduce", "exit", "data_incomplete"]
    conviction: Literal["low", "med", "high"] | None
    rationale: str
    evidence_refs: list[str]
    change_conditions: list[str]

    @model_validator(mode="after")
    def conviction_matches_action(self) -> Self:
        if self.action == "data_incomplete" and self.conviction is not None:
            raise ValueError("data_incomplete holdings must not have a conviction")
        if self.action != "data_incomplete" and self.conviction is None:
            raise ValueError("actionable holdings require a conviction")
        return self


class DecisionNewsItem(BaseModel):
    evidence_ref: str
    symbol: str
    importance: Literal["material", "watch", "context"]
    headline: str
    source: str
    url: str
    impact: str


class CandidateIdea(BaseModel):
    symbol: str
    name: str
    instrument_type: Literal["stock", "etf"]
    market: Literal["US", "UK", "HK"]
    action: Literal["consider"]
    conviction: Literal["low", "med", "high"]
    why_surfaced: str
    portfolio_fit: str
    principal_risk: str
    watch_next: list[str]
    evidence_refs: list[str]


class DecisionBriefPayload(BaseModel):
    summary: str
    holdings: list[HoldingDecision]
    material_news: list[DecisionNewsItem]
    portfolio_observations: list[str]
    candidates: list[CandidateIdea]
    unavailable_inputs: list[str]
    data_as_of: datetime
    disclaimer: str
