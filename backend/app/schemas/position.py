from decimal import Decimal

from pydantic import BaseModel, field_serializer


class PositionCreate(BaseModel):
    symbol: str
    quantity: Decimal | None = None
    avg_cost: Decimal | None = None
    notes: str | None = None


class PositionUpdate(BaseModel):
    quantity: Decimal | None = None
    avg_cost: Decimal | None = None
    notes: str | None = None


class PositionOut(BaseModel):
    id: int
    symbol: str
    name: str
    market: str
    currency: str
    quantity: Decimal | None
    avg_cost: Decimal | None
    notes: str | None

    @field_serializer("quantity", "avg_cost")
    def _ser_decimal(self, v: Decimal | None) -> str | None:
        return None if v is None else str(v)
