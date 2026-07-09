# Phase 4 — ORSO environment — Design Spec

**Date:** 2026-07-09 · **Status:** Approved pending user spec review
**Parent spec:** `2026-07-07-investment-guru-design.md` §5 (fixes the shape)
**Depends on:** Phases 1–2b, all live at `925bd06` (Guru LLM layer, chat, guru_reports, FxService).

## 1. Summary

A distinct ORSO pension environment (HSBC/Hang Seng scheme, HKD): an editable
fund menu, a manually-maintained allocation snapshot with an automatic switch
log, retirement goals with deterministic three-scenario projection maths, fund
pricing via an HSBC fund-centre provider with manual entry as a first-class
permanent fallback, and a Guru ORSO advice mode (switching strategy among the
user's own funds only). Never mixed with trading portfolios; stays out of the
daily digest/take.

**Decisions locked during brainstorm (2026-07-09):**
- Seeded fund menu (public scheme data) + manual allocation entry.
- Snapshot + automatic switch log (no contribution ledger).
- Deterministic projection: 2% / 5% / 8% fixed annual scenarios, monthly compounding.
- Guru surface = on-demand report + ORSO-scoped chat; NOT in the daily cycle.
- Pricing approach A: attempt the HSBC fund-centre fetcher (fixture-tested parser,
  discovery task in the plan); manual entry always available; if no stable source
  exists the fetcher ships disabled with zero design change.

## 2. Data model (migration 0006)

All tables carry `user_id`; money/units are `Numeric`.

| Table | Purpose | Key columns |
|---|---|---|
| `orso_funds` | Scheme fund menu | `code` (String(16), unique per user), `name` (String(120)), `asset_class` (String(32)), `risk_rating` (int, 1–5), `archived` (bool, default false), timestamps |
| `orso_allocations` | Current snapshot | `fund_id` (FK, unique), `units` Numeric(18,4), `contribution_pct` Numeric(5,2) |
| `orso_switch_log` | Auto-written change history | `changed_at`, `old_state` JSONB, `new_state` JSONB (each: `{allocations: [{code, units, contribution_pct}]}`), `note` (String(300), nullable) |
| `orso_fund_prices` | Daily NAVs, HKD | `fund_id` FK, `price` Numeric(12,4), `as_of` Date, `source` (String(8): `hsbc`\|`manual`), `fetched_at`; unique `(fund_id, as_of)` — manual upsert overwrites |

`investor_profiles` gains nullable columns: `birth_year` (int), `retirement_target_age`
(int), `retirement_target_pot` Numeric(14,2) (HKD), `orso_monthly_contribution`
Numeric(10,2) (HKD). `chat_threads` gains nullable `scope` String(8)
(`"orso"` → ORSO context in chat; null → existing portfolio behaviour).

**Seed:** `app/seed.py` gains an idempotent ORSO fund-menu seed (HSBC/Hang Seng
ORSO scheme fund list: code, name, asset class, risk rating — public scheme
data; no personal units/splits are ever seeded or committed).

## 3. Pricing — `app/services/orso/`

Mirrors `market_data`: `OrsoPriceProvider` ABC (`async get_prices(codes) ->
dict[code, PriceDTO(price: Decimal, as_of: date)]`); `HsbcFundCentreProvider`
first implementation with a **recorded-fixture-tested parser**; non-finite
values dropped at the parser boundary (Phase 2a NaN-Decimal lesson).
`OrsoPriceService.refresh(db, funds)`: daily TTL, per-fund failure isolation,
returns the set of fund ids actually refreshed (fundamentals-service contract);
failures leave prior prices in place. Manual entry is a first-class path
(`upsert_manual_price`), not an error branch.

**Discovery task (in the plan):** probe the real HSBC HK fund-centre source and
record a fixture. If no stable machine-readable source exists, the provider
ships disabled behind a config flag (`orso_price_fetch_enabled: bool = False`
until proven) — the parser + fixture tests remain for later; manual entry
carries the phase. Either outcome requires no schema/API/UI change.

**Valuation:** pot value per fund = units × latest price (HKD); totals shown in
HKD with a base-currency conversion line via the existing `FxService`. Prices
with `as_of` older than 7 days are flagged `stale` per fund; funds with no
price are flagged `unpriced` (integrity flags in the overview payload, same
spirit as `costed_positions`).

## 4. Projection — `app/services/orso/projection.py`

Pure, unit-tested function. Inputs: current pot (Decimal, HKD), monthly
contribution, years to target (`retirement_target_age − (today.year −
birth_year)`), target pot. For each fixed annual rate in `(0.02, 0.05, 0.08)`:
future value with monthly compounding of pot + contributions →
`Scenario(rate, projected_pot, on_track: bool, gap: Decimal)` (gap = projected −
target; positive = surplus). Missing goal fields → projection omitted with a
`goals_incomplete` flag (no error). No Monte Carlo, no inflation modelling.

## 5. Guru ORSO advice mode

Reuses Phase 2b machinery wholesale:

- `GuruService.generate_orso(db, user) -> GuruReport` — advice model
  (`guru_advice_model`), per-kind lock `"orso"` (409 when in flight),
  `max_tokens=4096`, usage row `mode="orso"`, persists to `guru_reports` with
  `kind="orso"`, `portfolio_id=None`. Commit only on success.
- Context (`build_orso_context`): active fund menu, allocation with latest
  prices + per-fund and total value (HKD + base ccy), contribution split (+
  sum-≠-100 flag), goals, projection scenarios, last 10 switch-log entries,
  staleness/unpriced flags, as-of timestamps. Facts only — the LLM fetches
  nothing.
- Schema `OrsoAdvicePayload`: `fund_verdicts: [{code, action:
  Literal["keep","increase","reduce","exit"], conviction:
  Literal["low","med","high"], rationale}]`, `switch_plan: [{from_code,
  to_code, note}]`, `projection_comment: str`, `watch: [str]`,
  `disclaimer: str`.
- **Validity check (master-spec constraint):** every `code`/`from_code`/`to_code`
  must exist in the user's fund menu — one corrective retry, then `LLMError`
  (mirrors the review coverage check; usage accumulates across the retry).
- Chat: threads created with `scope="orso"` get ORSO context injected instead
  of portfolio context; "discuss" links from advice items create such threads
  (seed_context = the verdict/switch item).
- **No scheduler changes** — ORSO is absent from the daily digest and take.

## 6. API — `app/api/orso.py` (`/api/orso/*`, auth + ownership)

```
GET    /funds                 · POST /funds        · PATCH /funds/{id}   (edit/archive)
GET    /allocation            · PUT  /allocation   (full replace; auto-writes switch log
                                                    when state actually changed; optional note)
POST   /prices/refresh        (fetcher; 200 with {refreshed: [codes], unavailable: bool})
PUT    /prices/manual         ({fund_id, price, as_of} upsert)
GET    /goals                 · PUT  /goals
GET    /overview              (allocation + latest prices + values + split + projection
                               + flags {stale: [codes], unpriced: [codes], split_sum_off: bool,
                               goals_incomplete: bool})
POST   /advice                → 201 guru report (kind="orso")   [map_guru_errors]
GET    /advice/latest         · GET /advice?limit=20            (404 when none / list)
```

Validation: units ≥ 0, `0 ≤ contribution_pct ≤ 100`, price > 0 → 422 on
violation; split sum ≠ 100 is a flag, never an error. Fund codes normalised
upper. Archiving a fund with non-zero units → 409 `fund_has_units`.

## 7. Frontend

**Figma first** (standing rule): one ORSO screen mocked in file
`0gU58wfjttdZS0NXQeEtuD` on the existing visual language; user approves before
frontend build. Then: ORSO nav item enabled → `OrsoPage`:

- **Overview card** — total pot (HKD large, base-ccy line), per-fund table
  (code, name, asset class, risk, units, latest price + as-of, value, split %),
  stale/unpriced badges, inline manual price edit, "Refresh prices" button
  (hidden/disabled when the fetcher is disabled), allocation edit mode (units +
  split with an optional note → PUT).
- **Goals & projection card** — goals form (birth year, target age, target pot,
  monthly contribution) + three scenario bars with on/off-track verdict and gap;
  `goals_incomplete` empty state.
- **Switching advice card** — "Get switching advice" button (advice model),
  latest report: verdict chips (reuse `VerdictChip` with keep→muted mapping),
  switch plan list, projection comment, watch items, disclaimer, versioned
  history, per-item "discuss in chat" (creates `scope="orso"` thread).
- **Switch log** — dated list of changes with notes.

Guru-unconfigured state: advice card shows the existing "not configured"
banner; everything else on the page works.

## 8. Error handling

| Failure | Behaviour |
|---|---|
| Fetcher down / no source | Prior prices kept; overview flags stale/unpriced; banner; never 500 |
| Fetcher disabled by config | `POST /prices/refresh` → 200 `{unavailable: true}`; UI hides refresh |
| LLM unconfigured / busy / error | Existing 503 / 409 / 502 mapping; nothing persisted on failure |
| Invalid fund code in LLM output | One corrective retry → 502 |
| Goals incomplete | Projection omitted with flag; advice still runs (context notes the gap) |

## 9. Testing

Recorded-fixture HSBC parser suite (drift = failing fixture in CI); projection
unit tests against hand-computed FV numbers (incl. zero-contribution and
past-target-age edge cases); allocation PUT writes exactly one switch-log row
per real change (and none on no-op); advice validity-retry + fund-code
enforcement with `FakeLLMProvider`; API auth/ownership/422/409 coverage;
`scope="orso"` chat context test; frontend vitest + RTL + axe on `OrsoPage`.
Live smoke: manual price entry → overview values; real advice call; fetcher
smoke only if discovery proved a source.

## 10. Out of scope

Contribution ledger / money-weighted returns; Monte Carlo or inflation
modelling; ORSO in the daily digest/take; multi-scheme support; automated
holdings import; provider implementations beyond HSBC fund centre.

## 11. Build order (for the implementation plan)

1. Migration 0006 + models + seed → 2. pricing provider + discovery + refresh/manual
services → 3. projection → 4. overview/allocation/funds/goals/prices API →
5. `build_orso_context` + `OrsoAdvicePayload` + `generate_orso` + advice API →
6. chat `scope="orso"` → 7. Figma pass (user gate) → 8. frontend OrsoPage →
9. docs + live smoke → final whole-branch review (Opus).
