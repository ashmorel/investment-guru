import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.api.deps import AdminUser, SessionDep
from app.models import LlmConfig
from app.services.guru.config import load_active_config
from app.services.guru.llm.base import Usage  # noqa: F401 (documents return shape)
from app.services.guru.llm.factory import build_provider
from app.services.guru.service import invalidate_guru_service

router = APIRouter(prefix="/api/admin", tags=["admin"])

_PROVIDERS = {"anthropic", "openai", "google"}


@router.get("/ping")
async def ping(user: AdminUser) -> dict[str, bool]:
    return {"ok": True}


class LlmConfigOut(BaseModel):
    provider: str
    advice_model: str
    scan_model: str
    advice_input_price: str | None
    advice_output_price: str | None
    scan_input_price: str | None
    scan_output_price: str | None
    key_set: bool
    updated_at: str | None
    updated_by: str | None


class LlmConfigIn(BaseModel):
    provider: str
    advice_model: str = Field(min_length=1, max_length=64)
    scan_model: str = Field(min_length=1, max_length=64)
    api_key: str | None = None
    advice_input_price: str | None = None
    advice_output_price: str | None = None
    scan_input_price: str | None = None
    scan_output_price: str | None = None

    @field_validator(
        "advice_input_price", "advice_output_price", "scan_input_price", "scan_output_price")
    @classmethod
    def _valid_price(cls, v: str | None) -> str | None:
        # None/"" mean "unpriced"; anything else must parse as a Decimal.
        # A bad value raises here -> FastAPI returns 422 (not an uncaught 500 later).
        if v in (None, ""):
            return v
        try:
            Decimal(v)
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise ValueError("invalid_price") from exc
        return v


async def _get_row(db) -> LlmConfig | None:
    result = await db.execute(select(LlmConfig).order_by(LlmConfig.id).limit(1))
    return result.scalar_one_or_none()


def _dec(v):
    return None if v in (None, "") else Decimal(v)


# Redact common API-key shapes from provider error text before it is returned.
# Google's genai puts the FULL key in a ?key=... query param; OpenAI/Anthropic
# keys start with sk-; Google keys start with AIza.
_KEY_PATTERNS = [
    re.compile(r"key=[^\s&\"']+"),          # ?key=<value> query params (Google)
    re.compile(r"sk-[A-Za-z0-9_\-]+"),      # OpenAI / Anthropic secret keys
    re.compile(r"AIza[A-Za-z0-9_\-]+"),     # Google API keys
]


def _scrub(text: str, api_key: str) -> str:
    """Strip the submitted key + common key patterns from provider error text,
    then truncate. The api_key must never reach the client."""
    if api_key:
        text = text.replace(api_key, "***")
    for pat in _KEY_PATTERNS:
        text = pat.sub("***", text)
    return text[:200]


@router.get("/llm-config", response_model=LlmConfigOut)
async def get_llm_config(db: SessionDep, user: AdminUser):
    row = await _get_row(db)
    if row is None:
        cfg = await load_active_config(db)  # env fallback view
        return LlmConfigOut(provider=cfg.provider, advice_model=cfg.advice_model,
                            scan_model=cfg.scan_model, advice_input_price=None,
                            advice_output_price=None, scan_input_price=None,
                            scan_output_price=None, key_set=bool(cfg.api_key),
                            updated_at=None, updated_by=None)
    def _s(v: object) -> str | None:
        return None if v is None else str(v)

    return LlmConfigOut(
        provider=row.provider, advice_model=row.advice_model, scan_model=row.scan_model,
        advice_input_price=_s(row.advice_input_price),
        advice_output_price=_s(row.advice_output_price),
        scan_input_price=_s(row.scan_input_price),
        scan_output_price=_s(row.scan_output_price),
        key_set=bool(row.api_key), updated_at=row.updated_at.isoformat(), updated_by=row.updated_by)


@router.put("/llm-config", response_model=LlmConfigOut)
async def put_llm_config(body: LlmConfigIn, db: SessionDep, user: AdminUser):
    if body.provider not in _PROVIDERS:
        raise HTTPException(status_code=422, detail="unknown_provider")
    row = await _get_row(db)
    if row is None:
        row = LlmConfig(api_key="")
        db.add(row)
    row.provider = body.provider
    row.advice_model = body.advice_model
    row.scan_model = body.scan_model
    if body.api_key:                       # blank/None -> keep the stored key
        row.api_key = body.api_key
    row.advice_input_price = _dec(body.advice_input_price)
    row.advice_output_price = _dec(body.advice_output_price)
    row.scan_input_price = _dec(body.scan_input_price)
    row.scan_output_price = _dec(body.scan_output_price)
    row.updated_at = datetime.now(UTC).replace(tzinfo=None)
    row.updated_by = user.email
    await db.commit()
    invalidate_guru_service()              # next request rebuilds with the new config
    return await get_llm_config(db, user)


class _Probe(BaseModel):
    ok: bool


async def _run_test_call(provider: str, api_key: str, model: str) -> None:
    """One minimal structured call to validate provider+key+model. Raises on failure."""
    prov = build_provider(provider, api_key)
    await prov.generate_structured(
        system="Reply with ok=true.", messages=[{"role": "user", "content": "ping"}],
        schema=_Probe, model=model, max_tokens=16)


@router.post("/llm-config/test")
async def test_llm_config(body: LlmConfigIn, db: SessionDep, user: AdminUser) -> dict:
    api_key = body.api_key
    if not api_key:
        row = await _get_row(db)
        api_key = (row.api_key if row else "") or ""
    if not api_key:
        return {"ok": False, "detail": "no api key configured"}
    try:
        await _run_test_call(body.provider, api_key, body.advice_model)
    except Exception as exc:  # provider/auth/network failure -> clean report, never 500
        return {"ok": False, "detail": _scrub(str(exc), api_key)}
    return {"ok": True, "detail": "connection ok"}
