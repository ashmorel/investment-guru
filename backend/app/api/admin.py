from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
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


async def _get_row(db) -> LlmConfig | None:
    result = await db.execute(select(LlmConfig).order_by(LlmConfig.id).limit(1))
    return result.scalar_one_or_none()


def _dec(v):
    from decimal import Decimal
    return None if v in (None, "") else Decimal(v)


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
        return {"ok": False, "detail": str(exc)[:200]}
    return {"ok": True, "detail": "connection ok"}
