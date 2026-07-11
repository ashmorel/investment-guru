from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Numeric, String, false
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.crypto import EncryptedJSON, EncryptedText
from app.core.db import Base
from app.models.base import TimestampMixin


class InvestorProfile(TimestampMixin, Base):
    __tablename__ = "investor_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    risk_appetite: Mapped[str] = mapped_column(String(16), default="balanced")
    horizon: Mapped[str] = mapped_column(String(16), default="medium")
    sector_interests: Mapped[list[str]] = mapped_column(JSONB, default=list)
    free_text: Mapped[str] = mapped_column(EncryptedText(), default="")
    birth_year: Mapped[int | None] = mapped_column(nullable=True)
    retirement_target_age: Mapped[int | None] = mapped_column(nullable=True)
    retirement_target_pot: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    orso_monthly_contribution: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    digest_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    orso_display_currency: Mapped[str] = mapped_column(
        String(3), default="GBP", server_default="GBP")
    orso_contribution_currency: Mapped[str] = mapped_column(
        String(3), default="HKD", server_default="HKD")


class GuruReport(Base):
    __tablename__ = "guru_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(8))  # review | digest | take
    portfolio_id: Mapped[int | None] = mapped_column(ForeignKey("portfolios.id"))
    payload: Mapped[dict[str, Any]] = mapped_column(EncryptedJSON())
    model: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column()


class ChatThread(TimestampMixin, Base):
    __tablename__ = "chat_threads"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    portfolio_id: Mapped[int | None] = mapped_column(ForeignKey("portfolios.id"))
    seed_context: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    scope: Mapped[str | None] = mapped_column(String(8), nullable=True)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("chat_threads.id"), index=True)
    role: Mapped[str] = mapped_column(String(9))  # user | assistant
    content: Mapped[str] = mapped_column(EncryptedText())
    created_at: Mapped[datetime] = mapped_column()


class LlmConfig(Base):
    __tablename__ = "llm_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(16), default="anthropic")
    advice_model: Mapped[str] = mapped_column(String(64))
    scan_model: Mapped[str] = mapped_column(String(64))
    api_key: Mapped[str] = mapped_column(EncryptedText(), default="")
    advice_input_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    advice_output_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    scan_input_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    scan_output_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    updated_at: Mapped[datetime] = mapped_column()
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)


class LlmUsage(Base):
    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    mode: Mapped[str] = mapped_column(String(16))  # review | digest | take | chat
    model: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column()
    output_tokens: Mapped[int] = mapped_column()
    est_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    report_id: Mapped[int | None] = mapped_column(ForeignKey("guru_reports.id"))
    thread_id: Mapped[int | None] = mapped_column(ForeignKey("chat_threads.id"), index=True)
    created_at: Mapped[datetime] = mapped_column()
