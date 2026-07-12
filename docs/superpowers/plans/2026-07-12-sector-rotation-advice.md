# Sector-Rotation Advice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Guru "rotation view" — a macro-aware, app-data-grounded read on how the user's holding groups are positioned plus directional rotation suggestions — saved and regenerate-on-demand on the Sectors page.

**Architecture:** Mirrors the existing ORSO advice mode exactly (`generate_orso`): a new `build_rotation_context` aggregates per-group weight/drift/momentum/news + profile from Projects 2a/3/4, a new `GuruService.generate_rotation` calls the advice model with a typed `RotationAdvicePayload` (guardrailed, group-name-validated with one re-prompt), persists an encrypted `GuruReport(kind="rotation")`, and `POST`/`GET /api/groups/rotation` expose generate + read-latest. Frontend adds a rotation panel to `SectorsPage.tsx`.

**Tech Stack:** FastAPI + SQLAlchemy 2 async + Postgres (Alembic head **stays 0012** — no migration); provider-agnostic Guru LLM layer; React 18 + Vite + TS + Tailwind + TanStack Query; inline patterns from the ORSO advice panel + Guru take panel.

## Global Constraints

- **No migration.** Reuses `GuruReport` (new `kind="rotation"` value) + the existing `usage` ledger. Alembic head stays `0012`.
- **User-scoped everywhere.** Every query filters by `user.id`; both endpoints auth-required; cross-user reads return null/404, never another user's data.
- **Degrade-never.** LLM budget → HTTP **429**; a generation already running → **409** `generation_in_progress`; provider/feed failure → **502/503** via `map_guru_errors`. The context builder drops any unavailable input (records it in `availability`) and never blocks generation.
- **Encrypted at rest.** The `GuruReport.payload` is already encrypted (`EncryptedJSON`) — no plaintext money leaves the DB.
- **Directional only.** `RotationAdvicePayload` carries **no** amount/quantity/price fields. The instruction forbids invented figures and specific trade instructions.
- **Conviction literal** is `Literal["low", "med", "high"]` (match the existing `FundVerdict`/`IdeaItem` — `"med"`, not `"medium"`).
- **LLM tests use `FakeLLMProvider`** (no real API keys). Valuation/signals/news are seeded/mocked.
- **Adult personal-portfolio app** — the kids-app WCAG/moderation rules do NOT apply, but the not-regulated-financial-advice `disclaimer` is always present in the payload.
- Commit messages end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`. Push only at the Task 6 seam (frontend), after the Figma gate.

## File Structure

- `backend/app/services/guru/schemas.py` — **modify**: add `GroupObservation`, `Rotation`, `RotationAdvicePayload`.
- `backend/app/services/guru/service.py` — **modify**: add `_ROTATION_INSTRUCTION`, `_rotation_invalid_groups(...)`, `GuruService.generate_rotation(...)`.
- `backend/app/services/groups/rotation_context.py` — **create**: `build_rotation_context(db, user, quote_service, fx)`.
- `backend/app/api/groups.py` — **modify**: `POST` + `GET /api/groups/rotation`.
- `backend/tests/test_rotation_context.py`, `backend/tests/test_generate_rotation.py`, `backend/tests/test_groups_rotation_api.py` — **create**.
- `frontend/src/lib/types.ts`, `frontend/src/lib/api.ts` — **modify**: `RotationAdvice` types + `getRotation`/`generateRotation`.
- `frontend/src/pages/SectorsPage.tsx` — **modify**: rotation panel. `frontend/src/pages/SectorsPage.test.tsx` — **modify**.

---

### Task 1: `RotationAdvicePayload` schema + `_ROTATION_INSTRUCTION`

**Files:**
- Modify: `backend/app/services/guru/schemas.py`
- Modify: `backend/app/services/guru/service.py` (add the instruction constant + invalid-group helper)
- Test: `backend/tests/test_rotation_schema.py` (create)

**Interfaces:**
- Produces: `RotationAdvicePayload(market_view: str, groups: list[GroupObservation], rotations: list[Rotation], caveats: list[str], disclaimer: str)`; `GroupObservation(name: str, weight_pct: str, observation: str, signal: Literal["favour","trim","hold"])`; `Rotation(from_group: str, to_group: str, rationale: str, conviction: Literal["low","med","high"])`. `service._ROTATION_INSTRUCTION: str`. `service._rotation_invalid_groups(payload, group_names: set[str]) -> set[str]`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_rotation_schema.py
from app.services.guru.schemas import RotationAdvicePayload
from app.services.guru.service import _ROTATION_INSTRUCTION, _rotation_invalid_groups


def test_rotation_payload_shape_and_no_money_fields():
    p = RotationAdvicePayload(
        market_view="Leaning to trim megacap tech toward lighter groups.",
        groups=[{"name": "Big Tech", "weight_pct": "54.00",
                 "observation": "Up strongly, now 54% of the book.", "signal": "trim"}],
        rotations=[{"from_group": "Big Tech", "to_group": "Financials",
                    "rationale": "Reduce concentration.", "conviction": "med"}],
        caveats=["Limited trend history."],
        disclaimer="Educational, not regulated financial advice.",
    )
    assert p.rotations[0].conviction == "med"
    assert p.groups[0].signal == "trim"
    # Guardrail: no amount/quantity/price fields anywhere in the schema
    text = " ".join(RotationAdvicePayload.model_json_schema()["$defs"].keys()) \
        + " " + " ".join(RotationAdvicePayload.model_fields)
    for banned in ("amount", "quantity", "shares", "price", "value_base", "gbp"):
        assert banned not in text.lower()


def test_rotation_instruction_carries_guardrails():
    t = _ROTATION_INSTRUCTION.lower()
    assert "only" in t and "context" in t          # reason only from provided context
    assert "not" in t and ("amount" in t or "trade" in t or "price" in t)  # no specific trades/figures
    assert "disclaimer" in t


def test_rotation_invalid_groups_detects_unknown():
    p = RotationAdvicePayload(
        market_view="x", groups=[],
        rotations=[{"from_group": "Big Tech", "to_group": "Crypto",
                    "rationale": "y", "conviction": "low"}],
        caveats=[], disclaimer="z")
    assert _rotation_invalid_groups(p, {"Big Tech", "Financials", "Ungrouped"}) == {"Crypto"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_rotation_schema.py -q`
Expected: FAIL (ImportError — `RotationAdvicePayload` / `_ROTATION_INSTRUCTION` not defined).

- [ ] **Step 3: Add the schemas**

Append to `backend/app/services/guru/schemas.py`:

```python
class GroupObservation(BaseModel):
    name: str
    weight_pct: str
    observation: str
    signal: Literal["favour", "trim", "hold"]


class Rotation(BaseModel):
    from_group: str
    to_group: str
    rationale: str
    conviction: Literal["low", "med", "high"]


class RotationAdvicePayload(BaseModel):
    market_view: str
    groups: list[GroupObservation]
    rotations: list[Rotation]
    caveats: list[str]
    disclaimer: str
```

- [ ] **Step 4: Add the instruction + helper**

In `backend/app/services/guru/service.py`, add the import to the existing schemas import line (`RotationAdvicePayload`), then near `_ORSO_INSTRUCTION`:

```python
_ROTATION_INSTRUCTION = (
    "Give a sector/theme ROTATION view across the user's holding groups. Reason ONLY "
    "from the grounding context provided (weights, drift, momentum, news themes, "
    "profile) — do NOT invent live prices, rates, or any figures not in the context; "
    "if the data doesn't support a call, say so in caveats instead of guessing. In "
    "market_view give a short, explicitly-hedged read on how the groups are positioned "
    "now. For every group give an observation and a signal (favour/trim/hold). In "
    "rotations, suggest directional shifts between groups the user actually has "
    "(from_group -> to_group) with a plain rationale and conviction — DIRECTIONAL ONLY: "
    "never state amounts, share counts, or specific prices, and never give a specific "
    "trade instruction. Record thin history / sparse news / high uncertainty in "
    "caveats. Always include the disclaimer that this is general educational "
    "information, not regulated financial advice."
)


def _rotation_invalid_groups(payload: RotationAdvicePayload, group_names: set[str]) -> set[str]:
    names = {r.from_group for r in payload.rotations} | {r.to_group for r in payload.rotations}
    return names - group_names
```

- [ ] **Step 5: Run tests + ruff**

Run: `cd backend && .venv/bin/pytest tests/test_rotation_schema.py -q && .venv/bin/ruff check app/services/guru/schemas.py app/services/guru/service.py`
Expected: PASS, ruff clean.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/guru/schemas.py backend/app/services/guru/service.py backend/tests/test_rotation_schema.py
git commit -m "feat(rotation): RotationAdvicePayload schema + guardrailed instruction

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `build_rotation_context`

**Files:**
- Create: `backend/app/services/groups/rotation_context.py`
- Test: `backend/tests/test_rotation_context.py`

**Interfaces:**
- Consumes: `compute_group_exposure(db, user, quote_service, fx) -> {"groups":[{group_id,name,color,value_base,pct,day_change_base}], "total_base", "unpriced", "as_of"}` (from `app.services.groups.exposure`); `GroupSnapshot(user_id, group_id, as_of, value_base)`; `Signal(portfolio_id, instrument_id, kind, severity, title, detail, data, computed_at)`; `NewsItem` (per instrument, has `title, source, published_at, fetched_at, instrument_id`); `GroupAssignment(user_id, instrument_id, group_id)`; `Position`/`Portfolio` (real held instruments); `GuruService._profile` → row with `risk_appetite`, `horizon`.
- Produces: `async build_rotation_context(db, user, quote_service, fx) -> dict` with keys `as_of, total_base, profile{risk_appetite,horizon}, groups[{name, weight_pct, value_base, holdings[str], drift{from_pct,to_pct,days}|None, momentum{summary,notable_movers[str]}|None, news[{title,source}]}], availability{trend_history,news,signals}`.

**Reference:** read `backend/app/services/orso/context.py::build_orso_context` and `backend/app/services/groups/exposure.py` for the exact style before writing.

- [ ] **Step 1: Write the failing test** (seed two real portfolios GBP+HKD, two instruments, one group + Ungrouped; stub `value_portfolio` the way `tests/test_group_exposure.py` does; assert per-group weight, that a group with no snapshot history has `drift is None` and `availability["trend_history"] is False`, and that a group with two snapshots on different `as_of` gets a `drift` with `from_pct`/`to_pct`; assert user-scoping — another user's group never appears).

```python
# backend/tests/test_rotation_context.py — sketch; mirror tests/test_group_exposure.py fixtures
import pytest
from app.services.groups.rotation_context import build_rotation_context

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_context_has_per_group_weight_and_degrades_without_history(
        db_session, seeded_user, fake_quote_service, fake_fx, monkeypatch):
    # ... seed a "Big Tech" group with one held instrument, no GroupSnapshot rows ...
    ctx = await build_rotation_context(db_session, seeded_user, fake_quote_service, fake_fx)
    names = {g["name"] for g in ctx["groups"]}
    assert "Big Tech" in names
    bt = next(g for g in ctx["groups"] if g["name"] == "Big Tech")
    assert bt["drift"] is None                 # no history yet
    assert ctx["availability"]["trend_history"] is False
    assert ctx["profile"]["risk_appetite"]     # present
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_rotation_context.py -q`
Expected: FAIL (module not found). If the local DB hangs/mass-errors, recreate it: `docker compose down db && docker compose up -d db`, wait, retry.

- [ ] **Step 3: Implement `build_rotation_context`**

```python
# backend/app/services/groups/rotation_context.py
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models import (GroupAssignment, GroupSnapshot, Instrument, NewsItem,
                        Portfolio, Position, Signal)
from app.services.groups.exposure import compute_group_exposure

_DRIFT_DAYS = 90
_MOMENTUM_KINDS = ("price_move_day", "price_move_week", "fifty_two_week", "unusual_volume")
_NEWS_PER_GROUP = 5


async def _group_instruments(db, user_id: int) -> dict[int | None, list[Instrument]]:
    """group_id (None = Ungrouped) -> the user's real held instruments in it."""
    rows = (await db.execute(
        select(Instrument, GroupAssignment.group_id)
        .join(Position, Position.instrument_id == Instrument.id)
        .join(Portfolio, Portfolio.id == Position.portfolio_id)
        .outerjoin(GroupAssignment, (GroupAssignment.instrument_id == Instrument.id)
                   & (GroupAssignment.user_id == user_id))
        .where(Portfolio.user_id == user_id, Portfolio.kind == "real").distinct()
    )).all()
    out: dict[int | None, list[Instrument]] = {}
    for inst, gid in rows:
        out.setdefault(gid, []).append(inst)
    return out


async def _drift(db, user_id: int, group_id: int | None):
    since = (datetime.now(UTC).date() - timedelta(days=_DRIFT_DAYS))
    rows = (await db.execute(
        select(GroupSnapshot.as_of, GroupSnapshot.value_base).where(
            GroupSnapshot.user_id == user_id, GroupSnapshot.group_id == group_id,
            GroupSnapshot.as_of >= since).order_by(GroupSnapshot.as_of)
    )).all()
    if len(rows) < 2:
        return None
    first, last = rows[0], rows[-1]
    return {"days": (last.as_of - first.as_of).days,
            "from_value": str(first.value_base), "to_value": str(last.value_base)}


async def _momentum(db, user_id: int, instruments):
    if not instruments:
        return None
    ids = [i.id for i in instruments]
    rows = (await db.execute(
        select(Signal).join(Portfolio, Portfolio.id == Signal.portfolio_id)
        .where(Portfolio.user_id == user_id, Signal.instrument_id.in_(ids),
               Signal.kind.in_(_MOMENTUM_KINDS))
    )).scalars().all()
    if not rows:
        return None
    by_sym = {i.id: i.symbol for i in instruments}
    movers = sorted({by_sym[s.instrument_id] for s in rows if s.severity in ("watch", "high")})
    notes = [f"{by_sym[s.instrument_id]}: {s.title}" for s in rows][:6]
    return {"summary": "; ".join(notes), "notable_movers": movers}


async def _news(db, instruments):
    if not instruments:
        return []
    ids = [i.id for i in instruments]
    rows = (await db.execute(
        select(NewsItem).where(NewsItem.instrument_id.in_(ids))
        .order_by(NewsItem.published_at.desc().nullslast()).limit(_NEWS_PER_GROUP * 2)
    )).scalars().all()
    seen, out = set(), []
    for n in rows:
        key = (n.title or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append({"title": n.title, "source": n.source})
        if len(out) >= _NEWS_PER_GROUP:
            break
    return out


async def build_rotation_context(db, user, quote_service, fx) -> dict:
    exposure = await compute_group_exposure(db, user, quote_service, fx)
    from app.api.guru import get_profile_row
    profile = await get_profile_row(db, user)
    members = await _group_instruments(db, user.id)

    groups, any_hist, any_news, any_sig = [], False, False, False
    for g in exposure["groups"]:
        insts = members.get(g["group_id"], [])
        drift = await _drift(db, user.id, g["group_id"])
        momentum = await _momentum(db, user.id, insts)
        news = await _news(db, insts)
        any_hist = any_hist or drift is not None
        any_news = any_news or bool(news)
        any_sig = any_sig or momentum is not None
        groups.append({"name": g["name"], "weight_pct": g["pct"], "value_base": g["value_base"],
                       "holdings": [i.symbol for i in insts], "drift": drift,
                       "momentum": momentum, "news": news})
    return {
        "as_of": exposure["as_of"], "total_base": exposure["total_base"],
        "profile": {"risk_appetite": getattr(profile, "risk_appetite", "balanced"),
                    "horizon": getattr(profile, "horizon", "medium")},
        "groups": groups,
        "availability": {"trend_history": any_hist, "news": any_news, "signals": any_sig},
    }
```

> Note: confirm `NewsItem` has `instrument_id`, `title`, `source`, `published_at` (read `backend/app/models/`); if a field name differs, follow the model. `get_profile_row` returns None for a user with no profile — the `getattr(..., default)` handles that.

- [ ] **Step 4: Run tests + ruff**

Run: `cd backend && .venv/bin/pytest tests/test_rotation_context.py -q && .venv/bin/ruff check app/services/groups/rotation_context.py`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/groups/rotation_context.py backend/tests/test_rotation_context.py
git commit -m "feat(rotation): per-group grounding context (weight/drift/momentum/news + availability)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: `GuruService.generate_rotation`

**Files:**
- Modify: `backend/app/services/guru/service.py`
- Test: `backend/tests/test_generate_rotation.py`

**Interfaces:**
- Consumes: `build_rotation_context(db, user, self.quotes, self.fx)`; `RotationAdvicePayload`; `_ROTATION_INSTRUCTION`; `_rotation_invalid_groups`; `self._require_provider()`, `self._lock("rotation")`, `check_budget`, `provider.generate_structured(system=PERSONA_V1, messages, schema, model=self.advice_model, max_tokens=4096)`, `usage_mod.record_usage(db, user_id, mode="rotation", model, usage, report_id, price=self.advice_price)`, `GuruReport(kind="rotation", portfolio_id=None, payload=..., model, created_at=_now())`, `GenerationInProgress`.
- Produces: `async GuruService.generate_rotation(self, db, user) -> GuruReport`.

**Reference:** copy the structure of `generate_orso` (service.py:134-180) verbatim, swapping the context builder, schema, instruction, and the invalid-code retry for the group-name variant.

- [ ] **Step 1: Write the failing test** (use `FakeLLMProvider` returning a `RotationAdvicePayload`; build a `GuruService` the way `tests/test_generate_orso.py`/`test_guru_*` do; seed a user with one group + a held instrument):

```python
# backend/tests/test_generate_rotation.py — sketch; mirror the ORSO advice service test
import pytest
from app.models import GuruReport
from app.services.guru.schemas import RotationAdvicePayload

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_generate_rotation_persists_encrypted_report(db_session, seeded_user, rotation_guru):
    report = await rotation_guru.generate_rotation(db_session, seeded_user)
    assert report.kind == "rotation"
    assert report.payload["market_view"]
    # raw ciphertext in DB, plaintext absent (mirror test_group_models encryption assertion)


async def test_generate_rotation_reprompts_on_unknown_group(db_session, seeded_user, monkeypatch):
    # FakeLLMProvider returns a rotation to a non-existent group first, valid on retry;
    # assert the second payload is persisted (see the ORSO fund-code retry test).
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_generate_rotation.py -q`
Expected: FAIL (`GuruService` has no `generate_rotation`).

- [ ] **Step 3: Implement `generate_rotation`**

Add to `GuruService` (after `generate_orso`), mirroring it:

```python
    async def generate_rotation(self, db: AsyncSession, user: User) -> GuruReport:
        from app.services.groups.rotation_context import build_rotation_context

        provider = self._require_provider()
        lock = self._lock("rotation")
        if lock.locked():
            raise GenerationInProgress("rotation")
        async with lock:
            await check_budget(db, user.id)
            ctx = await build_rotation_context(db, user, self.quotes, self.fx)
            group_names = {g["name"] for g in ctx["groups"]}
            messages = [{"role": "user", "content":
                         _ROTATION_INSTRUCTION + "\n\n" + json.dumps(ctx)}]
            payload, usage = await provider.generate_structured(
                system=PERSONA_V1, messages=messages, schema=RotationAdvicePayload,
                model=self.advice_model, max_tokens=4096)
            invalid = _rotation_invalid_groups(payload, group_names)
            if invalid:
                messages += [
                    {"role": "assistant", "content": payload.model_dump_json()},
                    {"role": "user", "content":
                     f"These group names are not valid: {sorted(invalid)}. Allowed groups "
                     f"are: {sorted(group_names)}. Return the complete rotation advice again, "
                     "using only these group names."},
                ]
                first_usage = usage
                payload, usage = await provider.generate_structured(
                    system=PERSONA_V1, messages=messages, schema=RotationAdvicePayload,
                    model=self.advice_model, max_tokens=4096)
                usage = Usage(input_tokens=first_usage.input_tokens + usage.input_tokens,
                              output_tokens=first_usage.output_tokens + usage.output_tokens)
                invalid = _rotation_invalid_groups(payload, group_names)
                if invalid:
                    raise LLMError(f"rotation advice referenced invalid groups: {sorted(invalid)}")
            report = GuruReport(user_id=user.id, kind="rotation", portfolio_id=None,
                                payload=payload.model_dump(), model=self.advice_model,
                                created_at=_now())
            db.add(report)
            await db.flush()
            await usage_mod.record_usage(db, user_id=user.id, mode="rotation",
                                         model=self.advice_model, usage=usage,
                                         report_id=report.id, price=self.advice_price)
            await db.commit()
            return report
```

- [ ] **Step 4: Run tests + ruff**

Run: `cd backend && .venv/bin/pytest tests/test_generate_rotation.py -q && .venv/bin/ruff check app/services/guru/service.py`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/guru/service.py backend/tests/test_generate_rotation.py
git commit -m "feat(rotation): GuruService.generate_rotation (advice model + group-name retry + encrypted persist)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: `POST` + `GET /api/groups/rotation`

**Files:**
- Modify: `backend/app/api/groups.py`
- Test: `backend/tests/test_groups_rotation_api.py`

**Interfaces:**
- Consumes: from `app.api.guru` — `GuruDep`, `ReportOut`, `_report_out`, `map_guru_errors`; `GuruReport`; existing `SessionDep`, `CurrentUser`. (No `get_services` needed — `generate_rotation` uses the service's own `self.quotes`/`self.fx`.)
- Produces: `POST /api/groups/rotation` → 201 `ReportOut`; `GET /api/groups/rotation` → `ReportOut | None` (200, `null` body when none yet).

**Reference:** the ORSO advice endpoints (`orso.py:667-683`) for the exact `map_guru_errors()` + `_report_out` usage.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_groups_rotation_api.py — sketch; use the client/admin_client fixtures
import pytest
pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_rotation_get_null_before_any(client):
    r = await client.get("/api/groups/rotation")
    assert r.status_code == 200 and r.json() is None


async def test_rotation_post_generates_then_get_returns_it(client, patch_guru_rotation):
    # patch_guru_rotation makes GuruService.generate_rotation persist a fake report
    p = await client.post("/api/groups/rotation")
    assert p.status_code == 201 and p.json()["kind"] == "rotation"
    g = await client.get("/api/groups/rotation")
    assert g.json()["payload"]["market_view"]


async def test_rotation_requires_auth(unauth_client):
    assert (await unauth_client.post("/api/groups/rotation")).status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_groups_rotation_api.py -q`
Expected: FAIL (routes 404).

- [ ] **Step 3: Implement the endpoints**

In `backend/app/api/groups.py`, add the imports (`from app.api.guru import GuruDep, ReportOut, _report_out, map_guru_errors`) and append:

```python
@router.post("/rotation", response_model=ReportOut, status_code=201)
async def create_rotation(db: SessionDep, user: CurrentUser, guru: GuruDep):
    with map_guru_errors():
        report = await guru.generate_rotation(db, user)
    return _report_out(report)


@router.get("/rotation", response_model=ReportOut | None)
async def read_rotation(db: SessionDep, user: CurrentUser):
    r = (await db.execute(
        select(GuruReport).where(GuruReport.user_id == user.id, GuruReport.kind == "rotation")
        .order_by(GuruReport.created_at.desc(), GuruReport.id.desc()).limit(1)
    )).scalar_one_or_none()
    return _report_out(r) if r is not None else None
```

Add `GuruReport` to the existing `from app.models import (...)` line in `groups.py`.

- [ ] **Step 4: Run tests + ruff + full suite**

Run: `cd backend && .venv/bin/pytest tests/test_groups_rotation_api.py tests/test_generate_rotation.py tests/test_rotation_context.py tests/test_rotation_schema.py -q && .venv/bin/ruff check app/api/groups.py && .venv/bin/pytest -q`
Expected: all PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/groups.py backend/tests/test_groups_rotation_api.py
git commit -m "feat(rotation): POST/GET /api/groups/rotation (generate + read-latest, user-scoped)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Figma gate (USER GATE)

**Not a code task — STOP for user approval.**

- [ ] **Step 1:** In Figma file `0gU58wfjttdZS0NXQeEtuD`, add a **"Guru's rotation view"** panel to the bottom of the `10 Sectors` frame (reuse the existing card style + group colours), plus a companion **empty-state** frame. Show: header + Generate/Regenerate button + timestamp; populated state = `market_view` headline, rotations list (`from → to` + rationale + conviction chip), per-group favour/trim/hold signal row, caveats (muted) + disclaimer; empty state = prompt + Generate button.
- [ ] **Step 2:** Screenshot both states and present to the user. **Do not proceed to Task 6 until the user approves.** Revise on request.

---

### Task 6: Frontend rotation panel on `SectorsPage` (push seam)

**Files:**
- Modify: `frontend/src/lib/types.ts`, `frontend/src/lib/api.ts`, `frontend/src/pages/SectorsPage.tsx`
- Test: `frontend/src/pages/SectorsPage.test.tsx`

**Interfaces:**
- Consumes: `POST /api/groups/rotation` → `{id, kind, portfolio_id, payload, model, created_at}`; `GET /api/groups/rotation` → that or `null`. Payload = `{market_view, groups[{name,weight_pct,observation,signal}], rotations[{from_group,to_group,rationale,conviction}], caveats[], disclaimer}`.

**Reference:** the ORSO advice panel in `frontend/src/pages/OrsoPage.tsx` (generate + latest + 429/409/error states) and the existing `SectorsPage` colour helper (`resolveColor(groupId, color)`), plus the `groups` query already on the page (map group `name → id` for colours).

- [ ] **Step 1: Types** — add to `lib/types.ts`:

```ts
export interface RotationGroup { name: string; weight_pct: string; observation: string; signal: "favour" | "trim" | "hold"; }
export interface RotationItem { from_group: string; to_group: string; rationale: string; conviction: "low" | "med" | "high"; }
export interface RotationPayload { market_view: string; groups: RotationGroup[]; rotations: RotationItem[]; caveats: string[]; disclaimer: string; }
export interface RotationReport { id: number; kind: string; payload: RotationPayload; model: string; created_at: string; }
```

- [ ] **Step 2: API client** — add to `lib/api.ts` (match the existing fetch helper + error surfacing so 429/409 statuses propagate like the ORSO calls):

```ts
export const getRotation = () => api<RotationReport | null>("/api/groups/rotation");
export const generateRotation = () => api<RotationReport>("/api/groups/rotation", { method: "POST" });
```

- [ ] **Step 3: Failing test** — extend `SectorsPage.test.tsx`: mock `GET /api/groups/rotation` → `null` (empty state shows the prompt + Generate button); clicking Generate calls `POST` and renders `market_view` + a rotation row; a `POST` → 429 shows the "daily AI budget" message. `vitest-axe` on the populated page. Run to confirm RED.

- [ ] **Step 4: Implement the panel** — add a `RotationPanel` section to `SectorsPage.tsx` using TanStack Query (`["rotation"]` for the GET, a mutation for POST that invalidates `["rotation"]`). Empty state when data is `null`; populated renders `market_view`, the rotations list (colour dots via `resolveColor` looked up by `name → id` from the `groups` query, Ungrouped grey), the per-group signal row, caveats (muted) + disclaimer; button spinner while pending; map 429/409/error to the existing inline messages. Follow the ORSO advice panel markup.

- [ ] **Step 5: Verify** — `cd frontend && npm run check` → green (tsc + oxlint + vitest + vitest-axe + build).

- [ ] **Step 6: Commit + push (push seam — reaches prod; NO migration)**

```bash
git add frontend/src backend
git commit -m "feat(rotation): Guru rotation view panel on the Sectors page (frontend)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push origin main
```

Confirm CI green (by head SHA); Railway redeploys the backend (no migration — head stays 0012), Vercel the frontend.

---

### Task 7: Docs + live smoke + final review

**Files:** `AGENTS.md`, `docs/PROGRESS.md` (+ memory refresh, outside the repo).

- [ ] **Step 1: Live smoke** — after CI green + deploy: `curl -s -o /dev/null -w "%{http_code}" https://backend-production-c90f.up.railway.app/api/groups/rotation` → **401** (mounted, auth-gated); confirm `/sectors` still 200. Optionally, an authenticated real generation (spends a little budget) to confirm the end-to-end path.
- [ ] **Step 2: Docs** — add an "Enhancement Project 5 — sector-rotation advice: COMPLETE" section to `docs/PROGRESS.md` (no migration; `POST`/`GET /api/groups/rotation`; grounded context; guardrails) + a bullet to `AGENTS.md`'s status block; update the enhancement-programme line to mark Project 5 ✅ (programme complete). Commit + push.
- [ ] **Step 3: Final whole-branch Opus review** — `scripts/review-package <merge-base> HEAD`, dispatch the final reviewer on Opus: focus on the guardrail instruction actually constraining the model, user-scoping on both endpoints, degrade-never (429/409/502/503 + context builder input-drop), no money fields in the payload, and no accidental migration/head change. Triage findings; fix Critical/Important before declaring done.
- [ ] **Step 4:** Update memory (`project_investment_guru.md` + `MEMORY.md`): Project 5 live, programme complete.

---

## Self-Review

**Spec coverage:** §1 grounding context → Task 2; §2 payload + guardrails → Task 1 (+ enforced in Task 3 retry); §3 API/persistence → Tasks 3–4; §4 frontend → Tasks 5–6; §5 testing/rollout → every task's tests + Task 7. No migration anywhere (head stays 0012) ✓. On-demand only (no scheduler entry) ✓.

**Placeholder scan:** context/service/API/schema steps carry complete code; the frontend panel (Task 6 Step 4) and the two backend test bodies (Tasks 2–3) are described as "mirror the named existing file" sketches rather than full listings — deliberate, because they must match large existing patterns (`OrsoPage` panel, the ORSO advice service test fixtures) that the implementer will read; the interfaces + assertions to hit are specified.

**Type consistency:** `RotationAdvicePayload` fields identical across Tasks 1/3/4/6; conviction `"med"` (not "medium") matches the existing schemas; `generate_rotation(db, user)` signature identical in Tasks 3 and 4; `_report_out`/`ReportOut`/`map_guru_errors`/`GuruDep` imported from `app.api.guru` as they exist today; `compute_group_exposure` return keys used in Task 2 match `exposure.py`.
