from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from app.schemas.portfolio import PortfolioCreate


class ImportRowIn(BaseModel):
    symbol: str
    quantity: Decimal | None = None
    avg_cost: Decimal | None = None


class ImportCommitIn(BaseModel):
    portfolio_id: int | None = None
    new_portfolio: PortfolioCreate | None = None
    merge: Literal["update", "skip", "replace"] = "update"
    rows: list[ImportRowIn]


class ImportCommitOut(BaseModel):
    created: int
    updated: int
    skipped: int
    portfolio_id: int
