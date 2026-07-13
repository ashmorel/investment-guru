# Progress

_Last updated: 2026-07-13 (sector-rotation advice complete, no migration — enhancement programme DONE)._

## Phase 1 — portfolio core: COMPLETE

### Backend (FastAPI + Postgres)
- **Auth-lite**: `POST /api/auth/login` (session cookie), `POST /api/auth/logout`, `GET /api/auth/me`; single seeded user (`python -m app.seed`).
- **Portfolios**: `GET/POST /api/portfolios`, `PATCH/DELETE /api/portfolios/{id}` — real + watchlist kinds, 3-letter base currency.
- **Positions**: `GET/POST /api/portfolios/{id}/positions`, `PATCH/DELETE /api/positions/{id}`.
- **Instruments**: `GET /api/instruments/lookup?symbol=` — live Yahoo Finance lookup (US/UK/HK markets inferred).
- **Valuation**: `GET /api/portfolios/{id}/valuation` — live quotes with cache, FX conversion to base currency, GBp pence→GBP normalisation, per-position and total P&L, day change, currency exposure; degrades to null prices (never crashes) when a quote is unavailable.
- **CSV import**: `POST /api/imports/preview` (multipart Yahoo export parse) and `POST /api/imports/commit` (into existing or new portfolio; merge strategies update/skip/replace; all-or-nothing).
- **Dashboard**: `GET /api/dashboard` — per-portfolio total value, day change, P&L % and an `as_of` stamp.

### Frontend (React + Vite + Tailwind tokens + TanStack Query)
- Login page; authenticated shell with sidebar nav (Guru/ORSO slots reserved for later phases).
- **Dashboard** (`/`) — portfolio cards (name, kind, total value, day change, P&L %), as-of stamp, empty state linking to create/import, and a "Guru's take" placeholder panel (Phase 2 slot).
- **Portfolios** (`/portfolios`) — list + create; **detail** (`/portfolios/:id`) — positions with live valuation.
- **Import wizard** (`/import`) — upload → preview → assign to new/existing portfolio → commit.

### Not yet (later phases, per spec §8)
Profile, the Guru (Phase 2b, LLM), price history charts, digest scheduler, ORSO (Phase 4). Signals shipped in Phase 2a (below).

### Post-review hardening (2026-07-07)
Four fix-commits from the final whole-branch review: valuation integrity flags (costed_positions/day_change_partial/currency_mismatch guard + zero-cost-basis fix); position uniqueness (migration 0003 + 409), symbol normalisation at all API boundaries, and request-owned transaction boundaries; test conftest default null provider + CI now exercises the full alembic upgrade/downgrade chain; import-wizard error rendering + docs/repo cleanup.

## Phase 2a — signals engine: COMPLETE

A deterministic, no-LLM analysis pass that scores a portfolio against market facts, stores a timestamped snapshot, and lights up the dashboard "Needs your attention" panel. Every rule is a pure function of a `SignalContext`; provider/feed failures are isolated (an endpoint never 500s on a down feed) and reported in `unavailable_inputs`.

### Endpoints
- **`POST /api/portfolios/{id}/analyze`** — force-refreshes quotes, gathers market inputs (failure-isolated), runs the rules, and transactionally **replaces** the portfolio's signal snapshot (one `computed_at`). Returns `{ signals, as_of, unavailable_inputs }`.
- **`GET /api/portfolios/{id}/signals`** — reads the stored snapshot (`{ signals, computed_at }`).
- **`GET /api/dashboard/attention`** — all of the user's signals across portfolios, severity-ranked (`high` → `watch` → `info`), each tagged with its `portfolio_id`/`portfolio_name`.

All three require auth and 404 on another user's portfolio.

### Signal kinds (8)
Per-instrument: `earnings_upcoming`, `price_move_day`, `price_move_week`, `fifty_two_week`, `unusual_volume`, `news_recent`. Portfolio-level: `concentration` (single-name + sector) and `fx_exposure` (non-base currency). Thresholds/severity live in `app/services/signals/config.py`.

### Market-data inputs (behind provider abstractions, fixture-tested parsers)
Daily price-bar history (backfill + derived helpers: period return, 52-week range, average volume — all non-finite/NaN bars dropped at ingest and at the engine loader), next-earnings dates + fundamentals cache, and an RSS news provider + cache. yfinance/RSS is never called in tests.

### Frontend
Dashboard **"Needs your attention"** panel (`<AttentionPanel />`, severity-ranked with an empty state) + a **"Run analysis"** action; per-position **signal badges** (`<SignalBadges />`) on the portfolio detail page, matched to positions by symbol. Reuses Phase 1 Tailwind tokens.

### How to run an analysis
```bash
# with the backend running and logged in (cookie jar $J):
curl -s -b $J -X POST http://localhost:8000/api/portfolios/{id}/analyze   # writes the snapshot
curl -s -b $J http://localhost:8000/api/dashboard/attention               # severity-ranked flags
```
Or click **Run analysis** on the dashboard / a portfolio page.

### Verified end-to-end (2026-07-08)
Live smoke against real yfinance + RSS: login → GBP portfolio → AAPL/HSBA.L/0700.HK → `analyze` returned `price_move_week`, `concentration` (name + sector), and `fx_exposure` signals with plausible figures; the attention endpoint returned them severity-ranked. The RSS news feed was down during the run and degraded correctly (HTTP 200, `unavailable_inputs: ["news"]`, other signals still emitted). Backend 91 tests + ruff clean; frontend 11 tests + `npm run check` clean.

## Phase 2b — the Guru: COMPLETE

The judgment layer on top of the Phase 2a signals engine. A provider-agnostic LLM layer (`app/services/guru/llm/`, Anthropic first) receives *profile + valuations + stored signals* assembled by a shared `ContextBuilder` and returns schema-validated structured output; chat is the only free-text path. Signals stay deterministic code — the LLM never fetches data itself. No API key → Guru endpoints return `503 llm_unconfigured`, the UI shows a "not configured" banner, and everything else keeps working.

### Models & config (`app/core/config.py`)
`guru_advice_model` (default `claude-opus-4-8`: reviews, Guru's take, chat) and `guru_scan_model` (default `claude-haiku-4-5`: daily digest); `anthropic_api_key`; `guru_digest_hour` + `guru_timezone` for the scheduler. Model swaps are config edits.

### Endpoints (all `/api/guru/*`, auth + ownership)
- **Profile** — `GET/PUT /profile` (risk appetite, horizon, sector interests, free text; upsert).
- **Reviews** — `POST /reviews {portfolio_id}` (per-position verdict hold/increase/reduce/exit + conviction + rationale, portfolio observations, watch-next; a post-parse check forces coverage of every position with one corrective retry), `GET /reviews[?portfolio_id=]`, `GET /reviews/{id}`. Versioned history = rows.
- **Digest** — `GET /digest/latest`, `POST /digest` (scan model: earnings this week, movers, news flags, summary).
- **Guru's take** — `GET /take/latest`, `POST /take` (advice model; sees the latest digest; commentary, risks, rebalance ideas).
- **Chat** — `GET/POST /chat/threads`, `GET /chat/threads/{id}`, `POST /chat/threads/{id}/messages` → **SSE stream** (`delta`/`done`/`error` frames); user message persists immediately, assistant message only on stream completion. "Discuss" links seed threads from take ideas.
- **Usage** — `GET /usage/summary` (per-mode calls/tokens/estimated cost + 30-day total; every LLM call writes an `llm_usage` row).

Errors map to `503 llm_unconfigured` / `409 generation_in_progress` (per-kind in-flight lock) / `502 llm_error` (nothing persisted on failure).

### Scheduler (APScheduler, FastAPI lifespan)
Daily job at `guru_digest_hour` in `guru_timezone`: digest → take. **Startup catch-up**: on boot, generates whatever is missing for "today" (digest+take, or just a missing take after a partial failure); never raises — no key or provider failure logs and moves on. Phase 5 (always-on cloud) inherits this unchanged.

### Frontend
- **Guru page** — Guru's-take card, daily-digest card, portfolio reviews (run-review per portfolio, history, per-position `VerdictChip`s + observations), chat panel (thread pills, optimistic bubbles, token-by-token streaming via `src/lib/sse.ts`, thread-switch-safe).
- **Dashboard** — `GuruTakePanel` fills the reserved slot (refresh, staleness label, discuss links, unconfigured banner).
- **Portfolio detail** — per-position take column derived from the latest review (no extra LLM call) with an ask-in-chat link.
- **Settings** — investor profile form (segmented risk control, horizon, sector chips, free text) + usage/cost readout.
Screens match the approved Figma mocks (file `0gU58wfjttdZS0NXQeEtuD`, frames 05–07).

### Verified end-to-end (2026-07-09)
Live smoke with a real Anthropic key: boot ran the startup catch-up and generated a real Haiku digest + Opus 4.8 take (grounded in actual valuations — flagged the 79% AAPL concentration and watchlist-only holdings correctly); portfolio review covered all 3 positions with verdicts; chat streamed 15 SSE delta frames and persisted both turns; a backend restart correctly did **not** regenerate (catch-up idempotent per day); `usage/summary` showed all four modes totalling ≈$0.11. Browser pass over the Vite dev server confirmed the Guru page, dashboard take panel and chat render with the live data. Backend 145 tests + ruff clean; frontend 50 tests + `npm run check` clean.

## Phase 4 — ORSO environment: COMPLETE

A distinct HK pension area for the HSBC ORSO (WMFS) scheme — never mixed with
trading portfolios, absent from the daily digest/take. Data model (migration
0006): `orso_funds` (menu, seeded with the real 14-fund WMFS HKD menu),
`orso_allocations` (snapshot), `orso_switch_log` (auto-written on every real
allocation change), `orso_fund_prices` (HKD NAVs, `hsbc`|`manual` source);
retirement-goal columns on the investor profile; `chat_threads.scope`.

### Pricing
`OrsoPriceProvider` abstraction; **discovery found a real unauthenticated HSBC
fund-price JSON endpoint** (WMFS scheme) — the parser is tested against a
genuine captured fixture. The two API-gateway values HSBC's own page uses go in
`ORSO_HSBC_CLIENT_ID`/`ORSO_HSBC_CLIENT_SECRET` (see `.env.example`; visible in
the fund-centre page's devtools network tab); without them the fetcher is
simply absent and `POST /prices/refresh` returns `{unavailable: true}`. Manual
price entry is first-class and permanent. Refresh: 12h TTL, per-day upsert,
never overwrites a manual row, never raises.

### Projection (`app/services/orso/projection.py`)
Pure Decimal future-value maths — pot + monthly contributions compounded
monthly to target age under fixed 2%/5%/8% scenarios; on-track verdict + gap vs
target pot. Maths is code; the Guru only comments on it.

### Endpoints (`/api/orso/*`, auth + ownership)
Funds list/create/edit/archive (409 `fund_has_units` archiving a holding fund;
422 `fund_archived` allocating into one) · allocation GET/PUT (full replace,
writes one switch-log row per real change) · `GET /switchlog` · goals GET/PUT ·
prices refresh/manual · `GET /overview` (per-fund values, HKD total + GBP line
via FxService, projection, flags `{stale, unpriced, split_sum_off,
goals_incomplete}`) · advice POST/latest/list (`kind="orso"` in `guru_reports`,
advice model, per-kind lock, fund-code validity check with one corrective
retry) · ORSO-scoped chat (`scope="orso"` threads get ORSO context).

### Frontend
ORSO page per the approved Figma (file `0gU58wfjttdZS0NXQeEtuD` frame 08):
allocation table with stale badges + inline manual price edit + refresh (auto-
disables to "manual prices only" when unavailable), goals + three projection
bars, switching-advice card (verdict chips incl. `keep`, switch plan,
discuss→ORSO chat), switch log.

### Verified end-to-end (2026-07-09)
Live smoke: seeded real menu; allocation + note → switch log; manual prices;
overview HK$1,847,478 ≈ £175,824 (live FX); projections on-track; refresh
degraded gracefully without gateway creds; real Opus switching advice
(verdicts on valid codes only — it even flagged that its proposed destination
fund was unpriced); ORSO chat streamed with scheme context; usage rows
`mode="orso"`. Backend 210 tests + ruff clean; frontend 62 tests + `npm run
check` clean.

## Phase 5 — Cloud deployment: COMPLETE

Live in production: **https://investment-guru-rose.vercel.app**. Railway runs
the backend (Docker image, `alembic upgrade head` on every deploy, single
replica so the in-process APScheduler is the always-on daily-digest scheduler)
plus Postgres; Vercel serves the frontend and rewrites `/api/*` to the Railway
domain — single origin, first-party cookies, zero frontend env vars. Deploys
gate on green GitHub Actions on both platforms (Railway check-suite setting;
Vercel Git integration, root directory `frontend/`).

### Hardening (production flag `ENV=production`)
Secure + SameSite=Lax + HttpOnly session cookies · startup fails hard on the
default/short SECRET_KEY · seed refuses default credentials · login throttling
(5 failures → 60s lockout → 429) · 2 MB CSV upload cap (413) · security
headers (nosniff / frame-deny / same-origin referrer, always on). All
unit-tested and re-verified live from the public internet.

### Verified end-to-end in production (2026-07-09)
Cookie flags + headers + 429 throttle + 413 cap confirmed from outside; digest
(Haiku) + take (Opus) 201 through the proxy; **SSE chat streamed token-by-token
through the Vercel rewrite** (5 deltas over ~2s — no buffering, contingency
unused); ORSO manual price + allocation + real switching advice; service
restart triggered the catch-up which generated the first user's daily
digest+take (always-on confirmed). Smoke ran under a throwaway user, fully
purged afterwards — prod DB holds only the real account + seeded fund menu.
Ops detail (env vars, rollback, backups, key rotation): `docs/deployment.md`.

## Enhancement Project 1 — Multi-user + encryption + admin: COMPLETE

Turned the single-user app into a real multi-user product. First of a five-project
enhancement programme (2 = multi-provider LLM + admin config panel, 3 = dashboard/news
UX, 4 = user sector grouping, 5 = sector-rotation advice).

### Accounts & isolation
Open self-service registration (`POST /api/auth/register`, EmailStr + password >=8,
409 `email_taken` race-safe via IntegrityError catch, IP rate-limited). Per-user
isolation was already enforced (`user_id` + `get_owned_*` 404s) and is now guarded by a
central `test_isolation.py` sweep that asserts user B gets 404 AND user A's data is
unchanged after every rejected cross-user mutation.

### Encryption at rest (server-held key)
`app/core/crypto.py` — Fernet (authenticated AES) behind three SQLAlchemy TypeDecorators
(`EncryptedDecimal`/`EncryptedJSON`/`EncryptedText`, versioned `v1:` tokens for future key
rotation). Encrypted columns: `positions.quantity`/`avg_cost`, `orso_allocations.units`/
`contribution_pct`, `orso_switch_log` state, `guru_reports.payload`, `chat_messages.content`,
`investor_profiles.free_text` (migration 0007, in-place). Structural FKs + shared market
data stay plaintext (joins/valuation/signals keep working). `DATA_ENCRYPTION_KEY` env
(distinct from `SECRET_KEY`); production fails hard if it's empty OR the committed dev key.
`@validates` quantizers re-impose the old `Numeric` scales with ROUND_HALF_UP. A stolen DB
reveals no amounts, analysis, or chat.

### Admin + budget + opt-in digest
Email-allowlist admin role (`ADMIN_EMAILS`, default the owner; `AdminUser` dep → 403
`admin_only`; `me.is_admin`; `/api/admin/ping`; `/admin` area shell — LLM config lands in
project 2). Per-user daily LLM budget (`app/services/guru/budget.py`, default $1.00/day,
sums `llm_usage` since local-midnight → 429 `budget_exhausted`; wired into all Guru
generate paths + chat). Daily digest is opt-in per user (`investor_profiles.digest_enabled`,
Settings toggle); the scheduler iterates opted-in in-budget users with per-user failure
isolation.

### Verified end-to-end in production (2026-07-09)
Registered a throwaway user through the live UI: `is_admin` false + `/api/admin/ping` 403
(backend-enforced, not just nav-hidden); read of another user's portfolio → 404 (isolation);
duplicate register → 409; weak password → 422; `.test` TLD correctly rejected by the
validator. Encryption proof: the throwaway's position `quantity` is `v1:` ciphertext in the
raw DB with the plaintext absent — and **undecryptable with the committed dev key** (only
the prod `DATA_ENCRYPTION_KEY` works), while the running server round-trips it to `42.500000`
via the API. Digest toggle persisted. Throwaway user + data purged; prod DB holds only the
real account. Backend 268 tests, frontend 83, all green. Migration 0007 ran cleanly in prod.

### Post-review fix-forward (2026-07-11)
Final whole-branch Opus security review came back with no Critical findings; three Important
items were fixed forward. (1) **`positions.notes` now encrypted** — it was the one user-authored
free-text field left plaintext; switched to `EncryptedText` + migration **0008** (in-place, same
already-Text path as 0007; upgrade/downgrade round-trip proven against a real alembic DB). (2)
**Dev-key-in-prod migration trap closed** — the crypto layer now refuses to fall back to the
committed dev key when `env=production` (migrations call `encrypt()` before the app's boot-time
guard runs, so a key-less deploy would otherwise have written dev-key ciphertext to real
columns). (3) **Key rotation path** — `DATA_ENCRYPTION_KEY` accepts a comma-separated `new,old`
list (encrypt with the first, decrypt with any); prod validation rejects an invalid/dev key in
any list position; runbook added to `docs/deployment.md`. Also (Minor) `run_daily_job` now uses a
fresh DB session per user (matches `catch_up`). Backend **274** tests green (6 new).

## Enhancement — ORSO data-entry + advice: COMPLETE (2026-07-12, migration 0009)

Turned ORSO holdings entry into a fast, safe, review-before-commit flow and made the currency
model + Guru advice goal-aware. Spec `docs/superpowers/specs/2026-07-11-orso-data-entry-advice-design.md`,
plan `docs/superpowers/plans/2026-07-11-orso-data-entry-advice.md`. Live in prod (migration
0008→0009 ran clean; new endpoints mounted, 401 unauth).

### Data model (0009, additive)
`orso_funds.currency` (native, default HKD, plaintext); `investor_profiles.orso_display_currency`
(default GBP) + `orso_contribution_currency` (default HKD). Encrypted columns unchanged.

### Currency
`build_overview` now values each fund in its native currency (`units×price`) and converts to the
user's display currency via `FxService` (same-currency short-circuits; per-fund FX failure → null +
`flags.fx_unavailable`, never 500). Legacy `total_hkd`/`value_hkd`/`total_base` kept populated
(additive, so the frontend never broke between backend pushes). Projection runs in the display
currency. `PUT /orso/display-currency` persists the choice.

### Ingest (one draft, three doors, one transactional commit)
CSV (`POST /orso/ingest/csv`), statement screenshot via the Guru vision path
(`POST /orso/ingest/screenshot` — budget-gated 429, LLM failure → 502 not 500, image not persisted),
and manual all produce a **read-only** `AllocationDraft` (`app/services/orso/ingest.py`: code-then-
normalized-name matching, implied price = value÷units, flagged rows for unmatched/unparseable). The
user reviews/edits, then `POST /orso/allocation/apply` (`app/services/orso/allocation.py`) commits it
in ONE transaction: create confirmed new funds + write derived `manual` prices + full-replace the
allocation with a switch-log entry. All-or-nothing (422 → nothing committed); archived-fund guard +
cross-user 422 parity with `PUT /allocation`.

### Fund search + goal-gap advice
`GET /orso/funds/search` over the user's own menu (code + normalized name, incl. archived). The Guru
ORSO advice context gained `goal_gap` (projection shortfall/surplus per scenario, display currency),
`monthly_contribution`/headroom, and per-fund risk; `OrsoAdvicePayload` gained
`contribution_suggestion` (a concrete lever). Regenerate-on-demand unchanged.

### Frontend
Ingest wizard at `/orso/import` (Upload → Review&edit draft → Saved), fund-search typeahead,
display-currency switcher. `npm run check` green (tsc 0, lint 0, vitest 94 incl. vitest-axe, build).

### Verified
Backend **~300** tests green; each of the 7 backend tasks reviewed with fix loops (notable:
CSV fuzzy-name→normalized-equality, apply archived-fund guard). A pre-existing UTC-vs-local staleness
test flake was fixed in passing. Live: migration 0009 clean, `/api/health` 200, all 5 new endpoints
mounted (401 unauth). **Domain fact:** user is on the HSBC Local Staff DC Scheme (not WMFS), so
statement-derived prices are primary. Remaining user step: run the first real ingest to seed the fund
catalogue in prod. (Prod fund menu since seeded via `python -m app.seed_orso_lsrbs`: 18 LSRBS funds
HKD/USD/EUR, 14 legacy WMFS archived.)

## Enhancement — Multi-provider LLM + admin config: COMPLETE (2026-07-12, migration 0010)

The Guru can run on **Anthropic / OpenAI / Google Gemini**, chosen at runtime from the admin panel —
no code change or redeploy. Spec `docs/superpowers/specs/2026-07-12-multiprovider-llm-admin-design.md`,
plan `.../plans/2026-07-12-multiprovider-llm-admin.md`. Live in prod (0009→0010 clean; endpoints 401
unauth). Final Opus whole-branch review: see the review report.

### Config + storage
Single-row `llm_config` (`app/models/guru.py`): provider, advice_model, scan_model, `api_key`
(**EncryptedText**, Fernet at rest — never returned to the client), optional per-role $/1M prices,
updated_at/by. `load_active_config(db)` (`app/services/guru/config.py`): a saved row is authoritative,
else env fallback (`ANTHROPIC_API_KEY` + `guru_*_model`), so prod ran unchanged until the panel is used.

### Adapters + factory
Three `LLMProvider` implementations in `app/services/guru/llm/`: `AnthropicProvider` (canonical shape,
unchanged), `OpenAIProvider` and `GoogleProvider` translate Anthropic-shaped messages (system routing,
base64 image blocks, **per-message role** preserved — assistant→Gemini "model" so multi-turn/retry
conversations don't collapse), structured-parse + usage mapping, all failures → uniform `LLMError`.
`factory.build_provider(provider, key)` picks the adapter. `get_guru_service(db)` is now **async,
config-driven, and rebuildable**: `invalidate_guru_service()` clears the cache on save so the next
request uses the new provider/model/key (single Railway replica). Role→model wiring: advice paths use
`advice_model`+`advice_price`, digest uses `scan_model`+`scan_price` (no swap). `estimate_cost(model,
usage, price=)` resolves config price → built-in OpenAI/Gemini table → None (logged, not budget-counted).

### Admin API + panel
`app/api/admin.py` under `AdminUser` (403 non-admin): `GET /api/admin/llm-config` (returns config +
`key_set` bool — **never the key**), `PUT` (upsert; **omitted/blank api_key preserves the stored key**;
bad provider/price → 422; calls `invalidate_guru_service()` on save), `POST /llm-config/test` (one
minimal live call; error `detail` **scrubbed** of the submitted key incl. Google `key=`/`sk-`/`AIza`
forms; never 500). Frontend panel in `AdminPage.tsx` (provider select, model ids, write-only key with
"configured" pill, optional budget grid, Test button); `npm run check` green (98 tests incl. vitest-axe).

### Verified
Backend **324** tests; each of the 6 backend tasks reviewed with fix loops (notable: Gemini multi-turn
role preservation; `/test` key-scrub + price→422). All 3 adapters verified against the live SDKs via
context7 (openai 2.45, google-genai 2.11) and mock-tested (no real keys/network). **`ANTHROPIC_API_KEY`
env is now only the pre-panel fallback** — the active provider/key live in the encrypted `llm_config` row.

## Enhancement — Dashboard / stock-news UX: COMPLETE (2026-07-12, migration 0011)

Turned the buried one-line `news_recent` signal into a readable news surface. Spec
`docs/superpowers/specs/2026-07-12-dashboard-news-ux-design.md`, plan `.../plans/2026-07-12-dashboard-news-ux.md`.
Live in prod (0010→0011 clean; endpoints 401 unauth). Final Opus whole-branch review: see the review report.

### Data model (0011)
`GuruReport.instrument_id` (nullable FK, indexed) + `kind="news"`; the summary payload (existing
`EncryptedJSON` column) is `{summary, sentiment: positive|negative|neutral|watch, key_points, disclaimer}`
(`NewsSummaryPayload`). Additive/reversible.

### Read API (`app/api/news.py` + `app/services/market_data/news_read.py`)
`GET /api/news` returns per-holding groups: each stock's headlines **de-duped** by normalized title
(`norm_title`/`dedupe` — keep earliest-published, newest-first), capped 8, groups **ranked** by
recent-headline count then recency (`rank_groups`), with a `summary_available` flag; TTL-refreshed first
via the existing `NewsService.refresh` (6h; feed failure degrades to cache per-instrument → `unavailable`,
never 500). `GET /api/news/{symbol}` (fuller list, cap 30, 404 if not held), `POST /api/news/refresh`.
All scoped to the user's own instruments (Position→Portfolio.user_id join).

### On-demand summary
`GuruService.generate_news_summary(db, user, instrument, headlines)` — "news" lock, `check_budget` first,
`generate_structured(schema=NewsSummaryPayload, model=self.scan_model)` (the **cheap scan model**, not
advice), persists `GuruReport(kind="news", instrument_id)`, `record_usage(mode="news", price=self.scan_price)`.
`POST /api/news/{symbol}/summary` (422 no-headlines, 404 not-held, 429 budget, degrade-never-500 via
`map_guru_errors`), `GET /api/news/{symbol}/summary` (latest stored, 404 none). Saved + regenerable.
Headlines and summaries are separate endpoints, so headlines render even when a summary fails.

### Frontend
`components/NewsPanel.tsx` on the dashboard (below AttentionPanel): ranked cards, headline rows
(source · relative time · `rel="noreferrer"` external link), Refresh (invalidates `["news"]`),
Summarize/Regenerate revealing the summary + color-coded sentiment pill + key points; per-position
collapsible list on `PortfolioDetailPage`. `npm run check` green (108 tests incl. vitest-axe).

### Verified
Backend **343** tests; each of the 3 backend tasks reviewed with fix loops. Live: 0011 clean, `/api/health`
200, `GET /api/news` 401 (mounted). Cross-user scoped (own instruments only); the old `news_recent`
attention signal was kept as a nudge.

## Enhancement Project 4 — user-defined sector/theme grouping: COMPLETE (2026-07-12, migration 0012)

Spec `.../specs/2026-07-12-sector-grouping-design.md`, plan `.../plans/2026-07-12-sector-grouping.md`
(7 tasks, subagent-driven TDD). Live in prod (0011→0012 clean; endpoints 401 unauth). Final Opus
whole-branch review: **READY TO MERGE**, migration go/no-go **GO**.

### Data model (0012, additive — three new tables)
- `HoldingGroup(user_id, name String(64), color String(16), sort_order)` — unique `(user_id, name)`.
- `GroupAssignment(user_id, instrument_id, group_id)` — **one group per instrument** (unique
  `(user_id, instrument_id)`), `group_id` FK `ON DELETE CASCADE` (deleting a group → its holdings fall to Ungrouped).
- `GroupSnapshot(user_id, group_id: int|None, as_of date, value_base EncryptedDecimal)` — unique
  `(user_id, group_id, as_of)` with **NULLS NOT DISTINCT** (the null Ungrouped bucket upserts cleanly).
  Amounts encrypted at rest; percentages derived at read (decrypt + aggregate in Python).

### API (`app/api/groups.py`, all auth + user-scoped)
- CRUD: `GET/POST /api/groups`, `PATCH/DELETE /api/groups/{id}` (409 dup name, 404 not-owned).
- `PUT /api/groups/assign` (`symbol`, `group_id: int|null`) — upsert, 422 if not held, null clears to Ungrouped.
- `POST /api/groups/seed-from-sectors` — idempotent + non-destructive; auto Yahoo `sector`→group (null→"Unclassified"), assigns only currently-unassigned held instruments.
- `GET /api/groups/holdings` — each real held instrument + its current `group_id`/`group_name` (powers the manage-list preselect; deduped across portfolios).
- `GET /api/groups/exposure?portfolio_id=` — live valuation aggregated per group **in a single GBP base**
  (`app/services/groups/exposure.py`: each portfolio converted via `fx.get_rate(…,"GBP")`; a quote/FX failure
  degrades that holding to `unpriced`, **never 500**); returns `{groups[{group_id,name,color,value_base,pct,day_change_base}], total_base, unpriced, as_of}`. Opportunistically upserts today's snapshot (best-effort — a concurrent unique-constraint race rolls back and still returns 200).
- `GET /api/groups/trend?range=30d|90d|1y` — forward-only history from the snapshots (pct derived per date).

### Snapshot job (`app/services/groups/snapshot.py`)
`run_group_snapshot_job` on the scheduler (`digest_hour:30`, single replica, per-user failure-isolated, idempotent delete-then-insert); startup `snapshot_catch_up` always runs the idempotent job (wrapped so a failure never breaks boot). History accrues forward from launch (no backfill).

### Frontend
`SectorsPage.tsx` at `/sectors` (nav after Portfolios): manage (groups CRUD + seed + per-holding `<select>` preselected to the current group), exposure bars (value/%/day-change GBP, portfolio filter, unpriced note), and inline-SVG `TrendChart.tsx` (**no chart lib** — Value/Weight + 30d/90d/1y toggles, "history is building" empty state). Group colour is `group_id`-keyed so a colourless seeded group renders the same swatch everywhere.

### Verified
Backend group suite **15** tests (full suite 356+); frontend **126** (incl. vitest-axe). Live: 0011→0012
migration clean in Railway logs, `/api/groups` · `/exposure` · `/holdings` all 401 (mounted), `/sectors` 200.

## Enhancement Project 5 — sector-rotation advice: COMPLETE (2026-07-13, NO migration) — PROGRAMME DONE

Spec `.../specs/2026-07-12-sector-rotation-advice-design.md`, plan `.../plans/2026-07-12-sector-rotation-advice.md`
(7 tasks, subagent-driven TDD). Live in prod (no DB change — reuses `GuruReport(kind="rotation")` + the usage
ledger, Alembic head stays **0012**; endpoints 401 unauth). Final Opus whole-branch review: **READY TO MERGE**.
This is the final project — the 5-project enhancement programme is complete.

### What it is
A Guru **"rotation view"**: a macro-aware read on how the user's holding groups are positioned + directional
rotation suggestions, grounded in the app's OWN data (news + signals + group weight/drift), on-demand on the
Sectors page. Educational commentary, never executes trades.

### Grounding (`app/services/groups/rotation_context.py::build_rotation_context`)
Per user-defined group (incl. null Ungrouped): current weight + **weight-share drift** (`from_pct`/`to_pct` =
group value ÷ that date's total across ALL the user's groups, from `GroupSnapshot` history — NOT absolute value,
so market-wide appreciation isn't mistaken for rotation), momentum (signals engine), recent news themes (Project
3 read), + profile risk/horizon; an `availability` block records which inputs were present. Every input degrades
(missing → None/[], never raises); all queries user-scoped (Signal→Portfolio.user_id verified).

### Advice (`GuruService.generate_rotation`, `app/services/guru/schemas.py`)
Mirrors `generate_orso`: advice-model structured call → `RotationAdvicePayload` (`market_view`,
`groups[{name,weight_pct,observation,signal:favour/trim/hold}]`, `rotations[{from_group,to_group,rationale,
conviction:low/med/high}]`, `caveats`, `disclaimer` — **money-free schema, no amount/price/quantity fields**),
unknown-group-name re-prompt once then `LLMError`, encrypted `GuruReport(kind="rotation")`, usage recorded,
budget (429) + single-flight lock (409). `_ROTATION_INSTRUCTION` is strict: reason ONLY from the provided
context, no invented figures, DIRECTIONAL ONLY (no trades/amounts/prices), always the disclaimer.

### API + frontend
`POST /api/groups/rotation` (generate+persist, `map_guru_errors` → 429/409/502/503) and
`GET /api/groups/rotation` (latest saved report or **null**) in `app/api/groups.py`, both user-scoped. Frontend
rotation panel on `SectorsPage.tsx` (below Trend): on-demand Generate/Regenerate, empty-state prompt (no LLM call
until clicked), market-view callout + per-group signal pills + suggested rotations + caveats + disclaimer;
colours mapped group name→id (Ungrouped grey); 429/409/error states reuse the ORSO advice panel.

### Verified
Backend rotation tests (schema/context/service/API, full suite **372**); frontend **129** (incl. vitest-axe).
Live: no migration (head 0012), `POST`/`GET /api/groups/rotation` 401 (mounted), `/sectors` 200.

## Post-programme maintenance (2026-07-13)

Deferred-minors cleanup sweep (commit `1bd04ad`, no migration, live): (1) group snapshot
`as_of` now uses the app timezone via `exposure.local_today()` (guru_timezone, consistent with the
daily digest) across the scheduled job, the opportunistic write, and the trend cutoff — not raw UTC;
(2) `TrendChart` series `key` is `group_id` not `name`, so a user group literally named "Ungrouped"
can't collide with the null bucket; (3) dropped a redundant `RotationItem` annotation in `SectorsPage`.
Backend 372 / frontend 129 green.

**ORSO ingest fix (2026-07-13, migration `0013`, live — commits `0224ab4`/`818564c`):** the real HSBC
statement puts full fund NAMES in the `fund_code` column (→ ~60-char codes), which failed
`ApplyNewFund.code` (max_length 16) with a 422 the frontend rendered as a generic error. Fix:
(1) `ingest._dec` now parses comma/`%`/currency-formatted numbers — but validates thousands-grouping
(`_THOUSANDS_RE`) so a malformed comma like `"9,97"`/`"1,2,3"` returns None (unparseable) rather than a
silently-wrong Decimal — a Critical caught in review; (2) `ingest._derive_code` builds a short (≤16),
deduped, editable fund code from the name; (3) migration `0013` widens `orso_funds.code` 16→32 (VARCHAR
widen, metadata-only; **downgrade fails once >16-char codes exist** — cleanup first), `ApplyNewFund`/
`FundCreate` schemas bumped to 32; (4) frontend: editable Code column on new-fund rows, `liveFlags`
recomputes parse-flags as you edit, and array-shaped 422 details now render a readable reason. Backend
**381** / frontend **131** green; head now **0013**.

**ORSO LSRBS matching + native price (2026-07-13, no migration, live — commits `19014e1`/`26fe52b`):**
the real LSRBS statement uses full fund names WITH share-class suffixes (e.g. "iShares Developed World
Index Fund (IE) Inst Acc USD"), but `seed_orso_lsrbs.py` stored base names, so exact-normalized matching
missed. Fix: (1) updated the 11 held funds' catalogue names to the exact statement wording (codes/currency
unchanged) + re-seeded prod (`railway run` against the Postgres public proxy with an `INITIAL_USER_EMAIL`
override → 11 updated); (2) added an editable native **Price** column to the ingest review
(`valueFromUnitsPrice` → value = units × price), so USD/EUR/HKD-priced funds import in their own currency
and display in HKD (value-only CSV path unchanged). Backend **382** / frontend **132** green. Operator:
set ORSO display currency = HKD on the ORSO page.

**Correction — LSRBS funds modelled in HKD (2026-07-13, commit `4b342dc`, re-seeded prod):** the LSRBS
member statement reports every balance AND unit price in HKD, so treating the funds as their USD/EUR
underlying class made the HKD overview FX-convert an already-HKD figure (~7.84× too large — e.g. IDWI
showed HKD 7,883,571 instead of 1,005,857). All LSRBS funds are now `currency="HKD"` in
`seed_orso_lsrbs.py` (re-seeded prod → 11 updated), so the manual HKD-per-unit price needs no conversion.
This supersedes the "native price" framing above for LSRBS: the member-facing figures the app ingests are
HKD. (Display currency must be HKD, which it is.)

**Open (uncommitted, optional — none blocking):** automated DB backups (recommended before real data
lands), custom domain, Vercel↔Railway shared-secret header. Remaining code-minors are low-value (per-group
N+1 in `build_rotation_context`; failed-rotation LLM cost not recorded — mirrors ORSO). Operator steps:
import real Yahoo CSV, first ORSO ingest, optional OpenAI/Gemini key in `/admin`.

## How to run locally
```bash
docker compose up -d db                      # Postgres on :5433
cd backend && source .venv/bin/activate
alembic upgrade head && python -m app.seed   # seeds you@example.com / change-me
uvicorn app.main:app --reload --port 8000    # ANTHROPIC_API_KEY in backend env file enables the Guru
# in another shell:
cd frontend && npm install && npm run dev    # Vite dev server, proxies /api
```
Checks: `cd backend && pytest -v && ruff check .` · `cd frontend && npm run check`.
