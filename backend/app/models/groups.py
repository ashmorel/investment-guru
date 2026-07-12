from datetime import date
from decimal import Decimal

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.crypto import EncryptedDecimal
from app.core.db import Base
from app.models.base import TimestampMixin


class HoldingGroup(TimestampMixin, Base):
    __tablename__ = "holding_groups"
    __table_args__ = (UniqueConstraint("user_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    color: Mapped[str] = mapped_column(String(16), default="", server_default="")
    sort_order: Mapped[int] = mapped_column(default=0, server_default="0")


class GroupAssignment(Base):
    __tablename__ = "group_assignments"
    __table_args__ = (UniqueConstraint("user_id", "instrument_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    group_id: Mapped[int] = mapped_column(
        ForeignKey("holding_groups.id", ondelete="CASCADE"))


class GroupSnapshot(Base):
    __tablename__ = "group_snapshots"
    __table_args__ = (
        UniqueConstraint("user_id", "group_id", "as_of",
                         postgresql_nulls_not_distinct=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("holding_groups.id", ondelete="CASCADE"), nullable=True)
    as_of: Mapped[date] = mapped_column()
    value_base: Mapped[Decimal] = mapped_column(EncryptedDecimal())
