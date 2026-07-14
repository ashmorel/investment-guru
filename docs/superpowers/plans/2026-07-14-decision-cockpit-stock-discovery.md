# Decision Cockpit and Stock Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an on-demand, saved Decision Brief that leads with grounded holding verdicts, decision-relevant news, and up to five stocks or ETFs to consider holding.

**Architecture:** A versioned catalogue and deterministic scorer produce a bounded, fact-backed candidate shortlist. A new decision-context builder combines that shortlist with all real portfolios, signals, exposure, news, and profile data; `GuruService` validates a structured advice-model response and stores it as encrypted `GuruReport(kind="decision")`. A focused React Decision Cockpit renders the latest saved report and generates a new one only on explicit request.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async, Pydantic v2, pytest, React 18, TypeScript, TanStack Query, Tailwind CSS v4, Vitest, Testing Library, vitest-axe, Figma.

## Global Constraints

- Use `Decimal` or decimal strings for numeric investment data; never introduce float-based money or quantity logic.
- Every query involving user holdings, watchlists, profile, or reports must be scoped by `user_id` through the owning portfolio or row.
- The LLM may use only symbols, figures, and evidence references present in the assembled context.
- Unknown symbols or evidence references receive one constrained retry, then `LLMError`; never persist a partial or invalid report.
- Provider failures degrade per instrument and never become an unhandled 500.
- Generation is on-demand only; do not add scheduler or event-trigger integration.
- Do not add trade execution, price targets, position sizes, or automatic real-portfolio changes.
- Reuse `GuruReport(kind="decision")`; no Alembic migration is required.
- Provider calls are fixture-mocked in tests.
- All-async test modules use `pytestmark = pytest.mark.asyncio(loop_scope="session")` only when every test in the module is async.
- Use failing-test → minimal implementation → passing-test → commit for every code task.
- Complete and obtain approval for the Figma gate before editing frontend production components.

---

## File map

### New backend files

- `backend/app/data/recommendation_catalog.json` — bounded US/UK/HK stock and ETF metadata; no live facts.
- `backend/app/services/recommendations/__init__.py` — public recommendation service exports.
- `backend/app/services/recommendations/catalog.py` — catalogue parsing, validation, canonical symbol lookup.
- `backend/app/services/recommendations/candidates.py` — user-scoped source assembly and held-symbol exclusion.
- `backend/app/services/recommendations/scoring.py` — deterministic shortlist factors, availability, ordering.
- `backend/app/services/guru/decision_context.py` — cross-portfolio context and stable evidence references.
- `backend/tests/test_recommendation_catalog.py` — catalogue and assembly tests.
- `backend/tests/test_recommendation_scoring.py` — scoring and degraded-data tests.
- `backend/tests/test_decision_context.py` — aggregation, evidence, and isolation tests.
- `backend/tests/test_decision_brief.py` — service and API contract tests.

### Modified backend files

- `backend/app/services/guru/schemas.py` — Decision Brief Pydantic schemas.
- `backend/app/services/guru/service.py` — generation, correction retry, encrypted persistence, usage.
- `backend/app/api/guru.py` — POST and latest GET routes.
- `backend/app/services/guru/usage.py` only if its mode typing/allowlist requires `decision`.
- `backend/tests/conftest.py` only for reusable candidate-provider fixtures needed by three or more test modules.

### New frontend files

- `frontend/src/components/DecisionCockpit.tsx` — complete report UI and generation states.
- `frontend/src/components/DecisionCockpit.test.tsx` — rendering, interactions, errors, and accessibility.

### Modified frontend files

- `frontend/src/lib/types.ts` — Decision Brief API types and `GuruReport` kind.
- `frontend/src/lib/api.ts` — latest/generate client functions and error copy.
- `frontend/src/pages/DashboardPage.tsx` — action-first composition.
- `frontend/src/pages/DashboardPage.test.tsx` — integration and reading-order regression.

### Documentation files

- `docs/PROGRESS.md` — update only after implementation is live.
- `AGENTS.md` — refresh status only after a production push reaches production.

---

### Task 1: Figma implementation-ready Decision Cockpit

**Files:**
- Reference: `docs/superpowers/specs/2026-07-14-decision-cockpit-stock-discovery-design.md`
- Figma file: `0gU58wfjttdZS0NXQeEtuD`

**Interfaces:**
- Consumes: the approved Decision Cockpit and candidate-pipeline wireframes from brainstorming.
- Produces: approved desktop and mobile frames for default, evidence-expanded, empty, generating, data-incomplete, and error states.

- [ ] **Step 1: Load the required Figma skills before using Figma**

Read `figma:figma-use` and `figma:figma-generate-design` completely. Use the existing Investment Guru Figma file; do not create a new file.

- [ ] **Step 2: Build the desktop frame**

Create a desktop frame using the existing app tokens and this exact order: header/generate control → Actions (`Act`, collapsed `Hold`, `Data incomplete`) → News that matters → Portfolio context → Ideas to consider holding → existing full news surface.

- [ ] **Step 3: Build component states**

Add variants for `default`, `evidence-expanded`, `empty`, `generating`, `budget-exhausted`, `llm-unconfigured`, `provider-error`, and `data-incomplete`. Ensure action and importance states have text labels and do not depend on colour.

- [ ] **Step 4: Build the mobile frame**

Use one-column order: actions → material news → portfolio context → candidates. Keep the generate control and report age visible without horizontal scrolling.

- [ ] **Step 5: Obtain user approval**

Present desktop and mobile frames in Figma. Stop before frontend production edits until the user explicitly approves them.

- [ ] **Step 6: Record the approved frame URLs**

Add the approved desktop and mobile Figma node URLs to the implementation ledger `.superpowers/sdd/progress.md` (gitignored); do not commit the ledger.

---

### Task 2: Recommendation catalogue and candidate assembly

**Files:**
- Create: `backend/app/data/recommendation_catalog.json`
- Create: `backend/app/services/recommendations/__init__.py`
- Create: `backend/app/services/recommendations/catalog.py`
- Create: `backend/app/services/recommendations/candidates.py`
- Create: `backend/tests/test_recommendation_catalog.py`

**Interfaces:**
- Produces: `CatalogEntry`, `load_catalog() -> tuple[CatalogEntry, ...]`, and `assemble_candidates(db: AsyncSession, user: User, profile: InvestorProfile | None) -> list[CandidateSeed]`.
- `CandidateSeed` fields: `symbol`, `name`, `market`, `currency`, `instrument_type`, `sector`, `themes`, `sources`.

- [ ] **Step 1: Write failing catalogue-validation tests**

```python
def test_catalog_is_unique_and_supported():
    entries = load_catalog()
    assert entries
    assert len({e.symbol for e in entries}) == len(entries)
    assert {e.market for e in entries} <= {"US", "UK", "HK"}
    assert {e.instrument_type for e in entries} <= {"stock", "etf"}
    assert all(e.name and e.currency for e in entries)
```

Also test that `parse_catalog()` raises `ValueError` for duplicate symbols, unsupported markets, and missing required fields.

- [ ] **Step 2: Run the catalogue tests and verify failure**

Run: `cd backend && .venv/bin/pytest tests/test_recommendation_catalog.py -q`

Expected: FAIL because `app.services.recommendations.catalog` does not exist.

- [ ] **Step 3: Implement typed catalogue parsing**

```python
@dataclass(frozen=True)
class CatalogEntry:
    symbol: str
    name: str
    market: Literal["US", "UK", "HK"]
    currency: str
    instrument_type: Literal["stock", "etf"]
    sector: str | None
    themes: tuple[str, ...]

def load_catalog() -> tuple[CatalogEntry, ...]:
    raw = json.loads(_CATALOG_PATH.read_text())
    return parse_catalog(raw)
```

Seed a deliberately bounded, diversified catalogue of major liquid instruments across all three supported markets. Catalogue metadata must be non-sensitive and contain no prices, performance claims, or user data.

- [ ] **Step 4: Write failing candidate-assembly tests**

Create a real portfolio holding `AAPL`, a watchlist containing `MSFT`, and a profile interested in `technology`. Assert that `assemble_candidates()` excludes `AAPL`, includes `MSFT` with source `watchlist`, includes matching catalogue entries with source `profile_interest`, and never includes another user's watchlist instrument.

- [ ] **Step 5: Run the assembly tests and verify failure**

Run: `cd backend && .venv/bin/pytest tests/test_recommendation_catalog.py -q`

Expected: FAIL because `assemble_candidates` is not implemented.

- [ ] **Step 6: Implement minimal user-scoped assembly**

Query `Portfolio`/`Position`/`Instrument` for the requesting user, split held symbols (`kind == "real"`) from watchlist symbols, union catalogue/profile/watchlist sources, canonicalize to uppercase symbols, and return seeds sorted by symbol. Do not perform provider calls in this module.

- [ ] **Step 7: Run tests and commit**

Run: `cd backend && .venv/bin/ruff check app/services/recommendations tests/test_recommendation_catalog.py && .venv/bin/pytest tests/test_recommendation_catalog.py -q`

Expected: all pass.

```bash
git add backend/app/data/recommendation_catalog.json backend/app/services/recommendations backend/tests/test_recommendation_catalog.py
git commit -m "feat: add grounded recommendation catalogue"
```

---

### Task 3: Deterministic candidate scoring

**Files:**
- Create: `backend/app/services/recommendations/scoring.py`
- Create: `backend/tests/test_recommendation_scoring.py`

**Interfaces:**
- Consumes: `list[CandidateSeed]` from Task 2 and injected market-data readers.
- Produces: `score_candidates(..., limit: int = 12) -> list[ScoredCandidate]`.
- `ScoredCandidate` contains `seed`, `score: Decimal`, `factors: dict[str, str | None]`, `availability: dict[str, bool]`, and `evidence: list[CandidateEvidence]`.

- [ ] **Step 1: Write failing pure score tests**

```python
def test_rank_candidate_prefers_complete_positive_inputs():
    strong = CandidateInputs(momentum="positive", valuation="reasonable", news_count=3,
                             diversification_fit="high", stale=False)
    weak = CandidateInputs(momentum=None, valuation=None, news_count=0,
                           diversification_fit="low", stale=True)
    assert score_inputs(strong) > score_inputs(weak)
```

Add tests proving stale and missing inputs reduce the score and that all numeric factor outputs are decimal strings.

- [ ] **Step 2: Run pure tests and verify failure**

Run: `cd backend && .venv/bin/pytest tests/test_recommendation_scoring.py -q`

Expected: FAIL because the scoring module does not exist.

- [ ] **Step 3: Implement score types and pure scoring**

Use integer/`Decimal` weights only. Keep the formula explicit in named constants. Do not emit buy/sell language or price targets.

- [ ] **Step 4: Write failing orchestration/degradation tests**

Use fakes where one candidate's history call raises and another returns complete inputs. Assert `score_candidates()` continues, omits the candidate below the minimum-evidence threshold, returns at most `limit`, and sorts by `(-score, symbol)`.

- [ ] **Step 5: Implement scoring orchestration**

Fetch quote/history/fundamental/news/signal inputs through injected callables or existing service interfaces. Catch provider errors per candidate and per input, record `availability`, and omit candidates that lack the minimum evidence contract: a current quote plus at least one of history, fundamentals, or recent news.

- [ ] **Step 6: Run tests and commit**

Run: `cd backend && .venv/bin/ruff check app/services/recommendations/scoring.py tests/test_recommendation_scoring.py && .venv/bin/pytest tests/test_recommendation_scoring.py -q`

Expected: all pass.

```bash
git add backend/app/services/recommendations/scoring.py backend/tests/test_recommendation_scoring.py
git commit -m "feat: score grounded stock candidates"
```

---

### Task 4: Decision context and stable evidence references

**Files:**
- Create: `backend/app/services/guru/decision_context.py`
- Create: `backend/tests/test_decision_context.py`

**Interfaces:**
- Consumes: user, profile, real portfolios, `QuoteService`, `FxService`, and `score_candidates`.
- Produces: `build_decision_context(db, user, quote_service, fx) -> dict[str, Any]` with keys `profile`, `holdings`, `signals`, `material_news`, `portfolio_context`, `candidates`, `evidence`, `availability`, `data_as_of`.
- Every evidence item has a stable ID shaped as `signal:<id>`, `news:<id>`, or `candidate:<symbol>:<factor>`.

- [ ] **Step 1: Write failing aggregation tests**

Create two real portfolios holding the same symbol and one watchlist. Assert the context has one aggregated holding entry, retains both source portfolio IDs, excludes watchlist quantities from held exposure, and contains only the requesting user's rows.

- [ ] **Step 2: Write failing evidence/degradation tests**

Insert a signal and `NewsItem`; assert stable references and URLs appear. Make one valuation/provider input fail and assert the holding remains with `availability` false and the input name in the top-level availability block.

- [ ] **Step 3: Run tests and verify failure**

Run: `cd backend && .venv/bin/pytest tests/test_decision_context.py -q`

Expected: FAIL because `build_decision_context` does not exist.

- [ ] **Step 4: Implement the context builder**

Reuse `value_portfolio`, `_profile_dict` behavior, existing severity ordering, deduplicated news reads, and group exposure services. Convert every `Decimal` to a string before JSON serialization. Bound candidate and headline counts and apply the existing 60,000-character context ceiling without dropping held symbols.

- [ ] **Step 5: Run tests and commit**

Run: `cd backend && .venv/bin/ruff check app/services/guru/decision_context.py tests/test_decision_context.py && .venv/bin/pytest tests/test_decision_context.py -q`

Expected: all pass.

```bash
git add backend/app/services/guru/decision_context.py backend/tests/test_decision_context.py
git commit -m "feat: build decision brief context"
```

---

### Task 5: Decision schemas and Guru generation

**Files:**
- Modify: `backend/app/services/guru/schemas.py`
- Modify: `backend/app/services/guru/service.py`
- Create: `backend/tests/test_decision_brief.py`

**Interfaces:**
- Produces: `DecisionBriefPayload`, `HoldingDecision`, `DecisionNewsItem`, `CandidateIdea`.
- Produces: `GuruService.generate_decision_brief(db: AsyncSession, user: User) -> GuruReport`.

- [ ] **Step 1: Write failing schema tests**

Assert valid `data_incomplete` holdings require `conviction=None`; actionable holdings require a conviction; candidates accept only `action="consider"`; URLs and evidence refs are strings; unsupported actions fail validation.

- [ ] **Step 2: Add exact Pydantic contracts**

Implement the fields and literals from the approved spec. Add a model validator to `HoldingDecision` enforcing the `data_incomplete` conviction rule.

- [ ] **Step 3: Write failing generation and correction tests**

Queue a valid `DecisionBriefPayload` in `FakeLLMProvider` and assert encrypted `GuruReport(kind="decision", portfolio_id=None)`, advice-model use, and `LlmUsage(mode="decision")`. Queue an invalid-context symbol followed by a corrected payload and assert two calls; queue two invalid payloads and assert `LLMError` with no report persisted.

- [ ] **Step 4: Implement context-reference validation helpers**

```python
def _decision_invalid_refs(payload: DecisionBriefPayload, ctx: dict) -> tuple[set[str], set[str]]:
    allowed_symbols = {h["symbol"] for h in ctx["holdings"]} | {c["symbol"] for c in ctx["candidates"]}
    allowed_refs = {e["ref"] for e in ctx["evidence"]}
    used_symbols = {h.symbol for h in payload.holdings} | {c.symbol for c in payload.candidates}
    used_refs = {r for h in payload.holdings for r in h.evidence_refs} | {r for c in payload.candidates for r in c.evidence_refs}
    return used_symbols - allowed_symbols, used_refs - allowed_refs
```

Make the context expose one flat `evidence` index in addition to domain-specific collections so this validator has a single source of truth.

- [ ] **Step 5: Implement `generate_decision_brief`**

Use `_lock(f"decision:{user.id}")`, `check_budget`, `build_decision_context`, the advice model, `DecisionBriefPayload`, one constrained correction prompt, `GuruReport(kind="decision")`, and `record_usage(mode="decision")`. The system instruction must prohibit model-memory facts, amounts, prices, and trade instructions and require the standard disclaimer.

- [ ] **Step 6: Run tests and commit**

Run: `cd backend && .venv/bin/ruff check app/services/guru/schemas.py app/services/guru/service.py tests/test_decision_brief.py && .venv/bin/pytest tests/test_decision_brief.py -q`

Expected: all service/schema tests pass.

```bash
git add backend/app/services/guru/schemas.py backend/app/services/guru/service.py backend/tests/test_decision_brief.py
git commit -m "feat: generate grounded decision briefs"
```

---

### Task 6: Decision Brief API

**Files:**
- Modify: `backend/app/api/guru.py`
- Modify: `backend/tests/test_decision_brief.py`

**Interfaces:**
- Produces: `POST /api/guru/decision-brief -> ReportOut` with status 201.
- Produces: `GET /api/guru/decision-brief/latest -> ReportOut | null` with status 200.

- [ ] **Step 1: Write failing endpoint tests**

Test latest-empty returns JSON `null`; POST returns 201; latest returns the newest decision report for the current user; another user's report is invisible; 429/409/502/503 mappings match `map_guru_errors`.

- [ ] **Step 2: Run endpoint tests and verify failure**

Run: `cd backend && .venv/bin/pytest tests/test_decision_brief.py -q -k "endpoint or latest or error"`

Expected: FAIL with 404 route responses.

- [ ] **Step 3: Implement endpoints**

```python
@router.post("/decision-brief", response_model=ReportOut, status_code=201)
async def create_decision_brief(db: SessionDep, user: CurrentUser, guru: GuruDep):
    with map_guru_errors():
        return _report_out(await guru.generate_decision_brief(db, user))

@router.get("/decision-brief/latest", response_model=ReportOut | None)
async def read_latest_decision_brief(db: SessionDep, user: CurrentUser):
    report = (await db.execute(select(GuruReport).where(
        GuruReport.user_id == user.id, GuruReport.kind == "decision"
    ).order_by(GuruReport.created_at.desc(), GuruReport.id.desc()).limit(1))).scalar_one_or_none()
    return None if report is None else _report_out(report)
```

- [ ] **Step 4: Run API and focused regression tests**

Run: `cd backend && .venv/bin/pytest tests/test_decision_brief.py tests/test_guru.py tests/test_news_api.py tests/test_groups_api.py -q`

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/guru.py backend/tests/test_decision_brief.py
git commit -m "feat: expose decision brief API"
```

---

### Task 7: Frontend API contract and Decision Cockpit component

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Create: `frontend/src/components/DecisionCockpit.tsx`
- Create: `frontend/src/components/DecisionCockpit.test.tsx`

**Interfaces:**
- Produces: `DecisionBriefPayload`, `HoldingDecision`, `DecisionNewsItem`, `CandidateIdea` TypeScript interfaces.
- Produces: `getLatestDecisionBrief()` and `generateDecisionBrief()`.
- Produces: `<DecisionCockpit />`, which owns latest query, generation mutation, evidence disclosure, and error states.

- [ ] **Step 1: Add frontend contract types**

Mirror the backend payload exactly, add `"decision"` to `GuruReport.kind`, and type latest as `GuruReport<DecisionBriefPayload> | null`.

- [ ] **Step 2: Write failing component tests**

Use `vi.spyOn(globalThis, "fetch")`. Cover: null empty state; saved report rendering; `Act` before collapsed `Hold`; distinct `Data incomplete`; evidence expansion with source link; candidate cards; generate POST then latest cache update; 429/409/502/503 copy; and `axe(container)` with no violations.

- [ ] **Step 3: Run tests and verify failure**

Run: `cd frontend && npx vitest run src/components/DecisionCockpit.test.tsx`

Expected: FAIL because the component and API functions do not exist.

- [ ] **Step 4: Implement API functions and error mapping**

```typescript
export function getLatestDecisionBrief() {
  return apiFetch<GuruReport<DecisionBriefPayload> | null>("/api/guru/decision-brief/latest");
}

export function generateDecisionBrief() {
  return apiFetch<GuruReport<DecisionBriefPayload>>("/api/guru/decision-brief", { method: "POST" });
}
```

Add `decisionBriefErrorMessage()` mapping 429, 409, 502, and 503 to concise existing-app-style copy.

- [ ] **Step 5: Implement the approved component**

Use semantic sections and native `<details>`/`<summary>` for evidence. Group actions without mutating query data. Render external links with `target="_blank" rel="noreferrer"`. Provide a watchlist selector populated from `/api/portfolios`, filtered to `kind === "watchlist"`; adding calls `POST /api/portfolios/{id}/positions` with `{symbol, quantity: null}`.

- [ ] **Step 6: Run tests and commit**

Run: `cd frontend && npx vitest run src/components/DecisionCockpit.test.tsx && npm run lint`

Expected: all pass.

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts frontend/src/components/DecisionCockpit.tsx frontend/src/components/DecisionCockpit.test.tsx
git commit -m "feat: add decision cockpit component"
```

---

### Task 8: Dashboard action-first integration

**Files:**
- Modify: `frontend/src/pages/DashboardPage.tsx`
- Modify: `frontend/src/pages/DashboardPage.test.tsx`

**Interfaces:**
- Consumes: `<DecisionCockpit />` from Task 7.
- Produces: dashboard order `header -> DecisionCockpit -> portfolio cards -> AttentionPanel -> NewsPanel`, with detailed news visually secondary.

- [ ] **Step 1: Write failing dashboard integration tests**

Mock latest Decision Brief as `null` in existing tests. Add a populated test asserting `Actions across your holdings` precedes portfolio card text and `News`; ensure `Run analysis` still works and the full `NewsPanel` still renders.

- [ ] **Step 2: Run tests and verify failure**

Run: `cd frontend && npx vitest run src/pages/DashboardPage.test.tsx`

Expected: FAIL because the Decision Cockpit is not mounted.

- [ ] **Step 3: Integrate the component and responsive spacing**

Import and render `<DecisionCockpit />` immediately below the dashboard header/status messages. Keep portfolio cards compact and place existing `GuruTakePanel`, `AttentionPanel`, and `NewsPanel` after the decision surface; do not delete their routes or data calls.

- [ ] **Step 4: Run dashboard and component tests**

Run: `cd frontend && npx vitest run src/pages/DashboardPage.test.tsx src/components/DecisionCockpit.test.tsx src/components/NewsPanel.test.tsx src/components/AttentionPanel.test.tsx src/components/GuruTakePanel.test.tsx`

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/DashboardPage.tsx frontend/src/pages/DashboardPage.test.tsx
git commit -m "feat: make dashboard action first"
```

---

### Task 9: Full verification, review, and production documentation

**Files:**
- Modify after production only: `docs/PROGRESS.md`
- Modify after production only: `AGENTS.md`
- Modify if operational behavior needs explanation: `docs/deployment.md`

**Interfaces:**
- Consumes: completed backend and frontend feature.
- Produces: verified, review-ready implementation and, only when explicitly pushed/deployed, current production handoff docs.

- [ ] **Step 1: Run complete backend verification**

Run: `cd backend && .venv/bin/ruff check . && .venv/bin/pytest -q`

Expected: zero Ruff errors and all tests pass.

- [ ] **Step 2: Run complete frontend verification**

Run: `cd frontend && npm run check`

Expected: TypeScript, lint, Vitest, and Vite build all pass.

- [ ] **Step 3: Perform browser verification**

Start dependencies and the two development servers in separate terminals:

```bash
cd backend && docker compose up -d db && .venv/bin/uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev -- --host 127.0.0.1
```

Open the Vite URL and verify desktop and mobile: empty brief; successful generation; saved reload; Act/Hold/Data incomplete grouping; evidence disclosure; external news link; and candidate watchlist addition. The four mapped Guru error states are verified by `DecisionCockpit.test.tsx` and `test_decision_brief.py`, where they can be produced deterministically without changing live configuration.

- [ ] **Step 4: Run whole-change review**

Use `superpowers:requesting-code-review`. Resolve every correctness, security, user-isolation, grounding, accessibility, or regression finding, rerunning the relevant focused tests after each fix.

- [ ] **Step 5: Re-run full verification after review fixes**

Run both commands again:

```bash
cd backend && .venv/bin/ruff check . && .venv/bin/pytest -q
cd ../frontend && npm run check
```

Expected: both pass with no warnings promoted to errors.

- [ ] **Step 6: Commit verification or review fixes**

If tracked fixes or documentation changes exist, stage only feature-related files and commit:

```bash
git commit -m "fix: harden decision cockpit"
```

If there are no tracked changes, do not create an empty commit.

- [ ] **Step 7: Production handoff only when deployment is authorized and succeeds**

After a push reaches production, smoke-test authenticated generation and unauthenticated 401 behavior, then update `docs/PROGRESS.md` and `AGENTS.md` with the date, live behavior, test counts, migration head unchanged at `0013`, and any accepted limitations. Commit those documentation changes separately as `docs: mark decision cockpit live`.
