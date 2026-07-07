from typing import Literal

from pydantic import BaseModel, Field


class PortfolioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["real", "watchlist"]
    base_currency: str = Field(pattern=r"^[A-Z]{3}$")


class PortfolioUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    base_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")


class PortfolioOut(BaseModel):
    id: int
    name: str
    kind: str
    base_currency: str
    position_count: int
