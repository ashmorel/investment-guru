"""ORSO ingest: parse a CSV (or vision extraction) into an AllocationDraft the
user reviews before committing via POST /allocation/apply. Read-only — building
a draft never writes."""
import csv
import io
import re
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OrsoFund

_REQUIRED_HEADERS = {"fund_code", "units", "contribution_pct"}
_PRICE_Q = Decimal("0.0001")

# Strip everything that isn't a letter, digit, or space, then collapse runs of
# whitespace to a single space. Used for a SAFE fuzzy name match: normalized
# forms must be *equal* (never substring/contains, which would false-match
# "US Bond" to "US Bond Plus").
_PUNCT_RE = re.compile(r"[^0-9a-z ]+")
_WS_RE = re.compile(r"\s+")


def _norm_name(name: str) -> str:
    return _WS_RE.sub(" ", _PUNCT_RE.sub(" ", name.lower())).strip()


class ProposedFund(BaseModel):
    code: str
    name: str
    currency: str
    asset_class: str = "unknown"
    risk_rating: int = 4


class DraftRow(BaseModel):
    parsed_code: str
    parsed_name: str | None
    matched_fund_id: int | None
    proposed_fund: ProposedFund | None
    units: str | None
    value: str | None
    currency: str
    contribution_pct: str | None
    implied_price: str | None
    flags: list[str]


class AllocationDraft(BaseModel):
    rows: list[DraftRow]
    warnings: list[str]
    source: str


class CsvHeaderError(Exception):
    """Required CSV headers missing."""


def parse_csv(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text))
    headers = {(h or "").strip().lower() for h in (reader.fieldnames or [])}
    if not _REQUIRED_HEADERS.issubset(headers):
        raise CsvHeaderError(sorted(_REQUIRED_HEADERS - headers))
    out: list[dict] = []
    for raw in reader:
        out.append({(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()})
    return out


def _dec(val: str | None) -> Decimal | None:
    if not val:
        return None
    try:
        return Decimal(val)
    except (InvalidOperation, TypeError):
        return None


async def build_draft(
    db: AsyncSession, user_id: int, parsed_rows: list[dict], source: str
) -> AllocationDraft:
    funds = (await db.execute(
        select(OrsoFund).where(OrsoFund.user_id == user_id)
    )).scalars().all()
    by_code = {f.code.upper(): f for f in funds}
    by_name = {_norm_name(f.name): f for f in funds}

    rows: list[DraftRow] = []
    pct_sum = Decimal("0")
    for r in parsed_rows:
        code = (r.get("fund_code") or "").upper()
        name = r.get("fund_name") or None
        units = _dec(r.get("units"))
        value = _dec(r.get("value"))
        pct = _dec(r.get("contribution_pct"))
        currency = (r.get("currency") or "").upper()
        flags: list[str] = []

        match = by_code.get(code) or (by_name.get(_norm_name(name)) if name else None)
        if r.get("units") and units is None:
            flags.append("unparseable_units")
        if r.get("value") and value is None:
            flags.append("unparseable_value")
        if r.get("contribution_pct") and pct is None:
            flags.append("unparseable_pct")
        if match is None:
            flags.append("unmatched")

        eff_currency = currency or (match.currency if match else "HKD")
        implied = None
        if units and value and units != 0:
            implied = (value / units).quantize(_PRICE_Q)

        proposed = None
        if match is None:
            proposed = ProposedFund(
                code=code, name=name or code, currency=eff_currency)

        if pct is not None:
            pct_sum += pct

        rows.append(DraftRow(
            parsed_code=code, parsed_name=name,
            matched_fund_id=(match.id if match else None),
            proposed_fund=proposed,
            units=(None if units is None else str(units)),
            value=(None if value is None else str(value)),
            currency=eff_currency,
            contribution_pct=(None if pct is None else str(pct)),
            implied_price=(None if implied is None else str(implied)),
            flags=flags,
        ))

    warnings: list[str] = []
    if rows and pct_sum != Decimal("100"):
        warnings.append(f"pct_sum={pct_sum} (not 100)")
    return AllocationDraft(rows=rows, warnings=warnings, source=source)
