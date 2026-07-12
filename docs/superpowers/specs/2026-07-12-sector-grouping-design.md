# User-Defined Sector/Theme Grouping — Design Spec

_Date: 2026-07-12 · Enhancement Project 4 of Investment Guru._

## Goal

Let the user split their holdings into their own named groups/themes ("Tech",
"Space", …), seeded from the auto-sector but freely editable, and see **current
exposure** (value + % per group, across all real portfolios, with a per-portfolio
filter) plus a **forward-building trend** of each group over time.

## Context & constraints (what exists today)

- **`Instrument`** (`app/models/instrument.py`): has an auto `sector: str | None`
  (from Yahoo) + `industry`. "Space stocks" is not a Yahoo sector — hence
  user-defined groups.
- **Concentration signal** (`app/services/signals/rules.py`) already computes
  per-name and per-**auto-sector** exposure (value/%) per portfolio, using
  `sector_by_symbol` with an "Unclassified" fallback and
  `PositionValue.market_value_base` / `PortfolioSummary.total_value`.
- **Valuation** (`app/services/valuation.py`): `value_portfolio(...)` →
  `PortfolioSummary` with `positions: [PositionValue{ symbol, market_value_base,
  day_change_base? }]` and `total_value`, in the portfolio's base currency;
  degrades per-position (null value) on quote failure, never 500.
- **Scheduler** (`app/services/guru/scheduler.py`): APScheduler, single replica,
  a daily `run_daily_job` (digest/take) + startup `catch_up`. New cheap
  (non-LLM) jobs can be added alongside.
- **Encryption** (Project 1): monetary amounts are encrypted at rest via
  `EncryptedDecimal` (Fernet, `DATA_ENCRYPTION_KEY`). Frontend: React 18 + Vite +
  Tailwind + TanStack Query — **no charting library** (minimalist). Alembic head
  **0011**.

**Golden rules that apply:** money = `Decimal`, encrypted at rest for amounts;
every user-data table has `user_id` and every route 404s on another user's data;
valuation/quote failures degrade per-position, never 500; DB change = one
hand-written chained migration; Figma-first for the new page.

## Decisions (resolved during brainstorming)

1. **Views = exposure breakdown + forward-building trend.**
2. **One group per stock**, seeded from the Yahoo auto-sector, then freely
   renamed/merged/reassigned. Clean 100% breakdown; an implicit **Ungrouped**
   bucket for unassigned holdings.
3. **Aggregated across all real portfolios** (base currency) with an optional
   per-portfolio filter on the live breakdown. The group→stock mapping is
   **global/user-level** (one group per instrument regardless of portfolio).
4. **Snapshot storage = across-all-holdings per-group totals only** (trend is
   total exposure over time; the per-portfolio filter applies to the live
   breakdown, not the historical trend).
5. **Trend chart = inline SVG** (no new frontend dependency).

---

## Section 1 — Data model (migration 0012)

- **`HoldingGroup`** (per-user): `id`, `user_id` (FK, indexed), `name`
  (`String(64)`), `color` (`String(16)` — a UI token/hex), `sort_order` (int).
  Unique `(user_id, name)`.
- **`GroupAssignment`** (one group per stock): `id`, `user_id` (FK, indexed),
  `instrument_id` (FK), `group_id` (FK `holding_groups.id`). **Unique
  `(user_id, instrument_id)`** — one group per instrument per user. Assigning
  upserts; deleting a `HoldingGroup` cascades its assignments (holdings become
  Ungrouped).
- **`GroupSnapshot`** (forward-only history): `id`, `user_id` (FK, indexed),
  `group_id: int | None` (FK; NULL = the Ungrouped bucket), `as_of` (date),
  `value_base` (**`EncryptedDecimal`** — monetary amount). Unique
  `(user_id, group_id, as_of)` (upsert). Percentages are **derived at read**
  from the day's set (encrypted amounts can't be summed in SQL, so trend reads
  decrypt + aggregate in Python — data volume is tiny).

Migration **0012** (chained on 0011) creates the three tables. Additive.
`ON DELETE CASCADE` from `holding_groups` to `group_assignments` and
`group_snapshots` (a deleted group's assignments + trend disappear with it).

## Section 2 — Groups CRUD + seed-from-sectors

New router `app/api/groups.py` (prefix `/api/groups`), all auth + user-scoped.

- **`GET /api/groups`** → `[{ id, name, color, sort_order, holding_count }]`.
- **`POST /api/groups`** (`name`, `color?`) → 409 on duplicate name.
- **`PATCH /api/groups/{id}`** (`name?`, `color?`, `sort_order?`) → 404 if not owned.
- **`DELETE /api/groups/{id}`** → 404 if not owned; assignments + snapshots
  cascade (its holdings become Ungrouped).
- **`PUT /api/groups/assign`** (`symbol`, `group_id: int | null`) → upsert the
  unique `(user_id, instrument_id)` assignment (null clears it → Ungrouped);
  **422** if the user doesn't hold `symbol`; **404** if `group_id` isn't owned.
- **`POST /api/groups/seed-from-sectors`** → for each distinct auto `sector`
  among the user's currently-held instruments (null → "Unclassified"), create a
  `HoldingGroup` with that name if absent; assign each **currently-unassigned**
  held instrument to its sector's group. **Idempotent + non-destructive** (never
  overrides an existing user assignment). Returns `{ created: [names],
  assigned: n }`.

"Held instruments" = instruments referenced by the user's real (kind="real")
portfolio positions (watchlists excluded).

## Section 3 — Live exposure API

- **`GET /api/groups/exposure?portfolio_id=`** → `{ groups: [{ group_id | null,
  name, color, value_base, pct, day_change_base }], total_base, unpriced: [str],
  as_of }`. Computed **live**: value the user's real portfolios (or the single
  `portfolio_id` if owned, else 404) via the existing valuation service, map each
  position's `market_value_base` to its instrument's group (unassigned → the
  Ungrouped entry, `group_id: null`), sum per group + day change, derive `pct`
  from `total_base`. A position with no price contributes 0 and its symbol goes
  in `unpriced`; **never 500**. Values in the user's base currency (GBP default,
  as elsewhere).

## Section 4 — Daily snapshot job + trend API

- **Snapshot job** `app/services/groups/snapshot.py::run_group_snapshot_job`,
  added to the scheduler (a daily cron alongside `run_daily_job`; single replica).
  For each user with real holdings: compute the same across-all-portfolios
  per-group totals as the exposure endpoint, and **upsert** one
  `GroupSnapshot(value_base)` per group (incl. the null Ungrouped bucket) for
  today's `as_of`. Cheap (no LLM), per-user failure-isolated, idempotent. A
  **startup catch-up** writes today's snapshot if missing; additionally the
  `GET /exposure` endpoint opportunistically upserts today's snapshot, so there's
  always at least one data point and **history accrues forward from launch**
  (no backfill — there is no historical valuation to reconstruct).
- **`GET /api/groups/trend?range=30d|90d|1y`** → `{ series: [{ group_id | null,
  name, color, points: [{ as_of, value_base, pct }] }], as_of }` from the
  snapshots within the range (pct derived per date across that date's set).
  Across-all-holdings only.

## Section 5 — Frontend (Sectors page)

New **"Sectors"** nav item + `frontend/src/pages/SectorsPage.tsx`:
- **Manage:** groups list (create / rename / recolor / delete / reorder), a
  **"Seed from sectors"** button, and a holdings list where each holding has a
  group `<select>` to reassign it (calls `PUT /assign`). Ungrouped shown
  distinctly.
- **Exposure breakdown:** horizontal bars (value + %) per group in the group's
  color, with today's change, a portfolio filter, `total_base`, and the
  `unpriced` degrade note.
- **Trend:** a lightweight **inline-SVG** multi-line chart of each group's value
  (or weight, toggle) over the selected range; an empty "history is building —
  check back tomorrow" state until snapshots accrue.
- New `lib/api.ts` clients (`getGroups`, `createGroup`, `updateGroup`,
  `deleteGroup`, `assignGroup`, `seedGroups`, `getGroupExposure`,
  `getGroupTrend`) + `lib/types.ts`. `vitest-axe` on the page.

## Section 6 — Error handling, testing, rollout

**Error handling**
- Exposure + snapshot degrade per-position on quote failure (unpriced list),
  never 500. `value_base` encrypted at rest.
- All group routes user-scoped: 404 on a group you don't own, 422 assigning a
  symbol you don't hold. Deleting a group is safe (cascade to Ungrouped).
- Trend/exposure with no groups/holdings → empty, not an error.

**Testing**
- Seeding (creates missing sector groups, assigns only unassigned, idempotent +
  non-destructive re-run). Assignment uniqueness/move (re-assign updates, null
  clears). Exposure aggregation (per-group + Ungrouped + `total_base` + pct),
  portfolio filter, unpriced degrade, cross-user 404. Snapshot upsert idempotency
  + per-group aggregation + null bucket. Trend pct derivation per date.
- Frontend: `vitest-axe` on `SectorsPage`; the SVG chart renders points/empty
  state; fetch mocked via `vi.spyOn`.

**Figma gate (standing rule):** the Sectors page (management + breakdown bars +
trend chart) gets a Figma pass for user approval **before** the frontend build.

**Migration/deploy:** 0012 additive (three new tables), reversible.

**Build order (rough):**
1. Migration 0012 + models (`HoldingGroup`, `GroupAssignment`, `GroupSnapshot`).
2. Groups CRUD + `seed-from-sectors`.
3. Live exposure API (aggregate valuation by group + Ungrouped + filter).
4. Snapshot job + scheduler wiring + startup catch-up + `GET /trend`.
5. Figma gate (USER GATE).
6. Frontend Sectors page (manage + breakdown + inline-SVG trend) (push seam).
7. Docs + live smoke + final Opus review.

## Out of scope

- Many-groups-per-stock (tags) — one group per stock only.
- Per-portfolio historical trend (trend is across-all-holdings; only the live
  breakdown filters by portfolio).
- Group-level concentration signals / Guru group commentary (chosen against;
  the concentration signal keeps using the auto-sector).
- Historical backfill of the trend (forward-only from launch).
- A chart library (inline SVG).
