from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin


class OrsoFund(TimestampMixin, Base):
    __tablename__ = "orso_funds"
    __table_args__ = (UniqueConstraint("user_id", "code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    code: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(120))
    asset_class: Mapped[str] = mapped_column(String(32))
    risk_rating: Mapped[int] = mapped_column()
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class OrsoAllocation(Base):
    __tablename__ = "orso_allocations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    fund_id: Mapped[int] = mapped_column(ForeignKey("orso_funds.id"), unique=True)
    units: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    contribution_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2))


class OrsoSwitchLog(Base):
    __tablename__ = "orso_switch_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    changed_at: Mapped[datetime] = mapped_column()
    old_state: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    new_state: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)


class OrsoFundPrice(Base):
    __tablename__ = "orso_fund_prices"
    __table_args__ = (UniqueConstraint("fund_id", "as_of"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    fund_id: Mapped[int] = mapped_column(ForeignKey("orso_funds.id"), index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    as_of: Mapped[date] = mapped_column()
    source: Mapped[str] = mapped_column(String(8))  # hsbc | manual
    fetched_at: Mapped[datetime] = mapped_column()
