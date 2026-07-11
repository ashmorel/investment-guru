from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.crypto import EncryptedDecimal, EncryptedText
from app.core.db import Base
from app.models.base import TimestampMixin
from app.models.instrument import Instrument

_QTY_Q = Decimal("0.000001")  # was Numeric(18, 6)
_COST_Q = Decimal("0.0001")  # was Numeric(18, 4)


class Portfolio(TimestampMixin, Base):
    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(16))  # real | watchlist
    base_currency: Mapped[str] = mapped_column(String(8), default="GBP", server_default="GBP")

    positions: Mapped[list["Position"]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan", lazy="selectin"
    )


class Position(TimestampMixin, Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), index=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    quantity: Mapped[Decimal | None] = mapped_column(EncryptedDecimal())
    avg_cost: Mapped[Decimal | None] = mapped_column(EncryptedDecimal())  # native ccy
    notes: Mapped[str | None] = mapped_column(EncryptedText())

    portfolio: Mapped[Portfolio] = relationship(back_populates="positions")
    instrument: Mapped[Instrument] = relationship(lazy="selectin")

    # EncryptedDecimal (unlike the Numeric columns it replaces) stores whatever
    # scale the caller passed in, so quantize explicitly to preserve the
    # previous DB-enforced precision (18,6)/(18,4).
    @validates("quantity")
    def _quantize_quantity(self, key: str, value: Decimal | None) -> Decimal | None:
        return None if value is None else Decimal(value).quantize(_QTY_Q, rounding=ROUND_HALF_UP)

    @validates("avg_cost")
    def _quantize_avg_cost(self, key: str, value: Decimal | None) -> Decimal | None:
        return None if value is None else Decimal(value).quantize(_COST_Q, rounding=ROUND_HALF_UP)
