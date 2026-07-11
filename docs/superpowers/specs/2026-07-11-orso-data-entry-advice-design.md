# ORSO Data-Entry + Advice — Design Spec

_Date: 2026-07-11 · Extends Phase 4 (ORSO pension environment) of Investment Guru._

## Goal

Let the user get their real HSBC ORSO holdings into the app quickly and keep them
current — by **CSV upload**, **statement-screenshot extraction**, or **manual
entry** — with per-fund currency support, a searchable fund menu, and Guru
commentary that is explicitly oriented around closing the gap to their
retirement goal. All ingest paths converge on one **review-and-confirm** step
before anything is written.

## Context & constraints (what already exists)

Phase 4 built a **units-based** ORSO model:

- `OrsoFund` (per-user fund menu): `code, name, asset_class, risk_rating,
  archived`. **No currency** — everything is assumed HKD.
- `OrsoAllocation`: `units` + `contribution_pct` per fund (both `EncryptedDecimal`,
  one row per fund, `fund_id` unique).
- `OrsoSwitchLog`: full `old_state`/`new_state` snapshots (`EncryptedJSON`) written
  whenever the allocation changes.
- `OrsoFundPrice`: `fund_id, price Numeric(12,4), as_of, source (hsbc|manual)`.
- Goals live on `InvestorProfile`: `birth_year, retirement_target_age,
  retirement_target_pot, orso_monthly_contribution`.
- `PUT /api/orso/allocation` — full-replace that writes a switch-log entry on
  change (the audited write path).
- `build_overview()` — computes each fund's value in HKD, sums to `total_hkd`,
  converts the **total** HKD→GBP for display, runs the 2/5/8% projection, and
  emits integrity flags (`stale, unpriced, split_sum_off, goals_incomplete`).
- `POST /api/orso/advice` — Guru ORSO switching-advice mode (LLM, stored as a
  `GuruReport` kind `"orso"`, regenerable on demand); `GET /advice/latest`.
- Prices: `OrsoPriceService` + `HsbcFundCentreProvider`, hardwired to
  `schemeIdentifier=WMFS`. The endpoint returns the **whole WMFS scheme** in one
  call and filters to the user's codes.

**Critical domain fact:** the user is in the **HSBC Local Staff Defined
Contribution Scheme**, *not* WMFS. So the live price feed will most likely **not**
return the user's fund codes. Statement-derived prices are therefore the
**primary** pricing mechanism, and the live feed is best-effort only (funds it
doesn't cover simply stay on manual/derived prices — no error). Pointing the
feed at the Local Staff DC Scheme is a **separate investigation, out of scope**
for this spec.

**Golden rules that apply** (from AGENTS.md): public repo — no real holdings or
secrets committed (synthetic/redacted fixtures only); money/quantity =
`Decimal`, never float; DB change = one hand-written chained Alembic migration;
providers are fixture-mocked in tests and endpoints **degrade, never 500**; LLM
output governed by the per-user daily budget + rate limit; encrypted columns stay
encrypted. Figma-first for non-trivial UI.

## Decisions (resolved during brainstorming)

1. **Units-based model stays.** Statements show units + value + %. Ingest derives
   an **implied price** = `value ÷ units` (in the fund's native currency) and
   stores it as a `manual` price, so valuation works without a live feed.
2. **Currency: per-fund native + switchable display.** Each `OrsoFund` gets a
   native `currency`; the overview total is shown in a user-set
   `orso_display_currency`. Each fund converts from its own currency to the
   display currency.
3. **Fund catalogue = the `OrsoFund` menu itself** (no separate catalogue table).
   Funds can exist with no allocation, so "search to add" searches the menu
   (including zero-allocation and archived funds). The menu is **seeded by running
   the first ingest** against a real statement.
4. **Contribution = monthly total**, split across funds by `contribution_pct`;
   maps directly to `orso_monthly_contribution` (given a currency by
   `orso_contribution_currency`).
5. **Ingest architecture = unified draft pipeline** (Approach A): CSV + screenshot
   + manual all produce one `AllocationDraft`; the user reviews/edits it; a single
   transactional `apply` commits it through the existing full-replace + switch-log
   logic.
6. **Goal-gap advice = enrich the existing `generate_orso` mode**, not a new
   surface. Regenerate-on-demand already exists.

---

## Section 1 — Data model changes (migration 0009)

All additive; forward-only chained migration on head `0008`.

- **`orso_funds.currency`** — `String(3)` ISO code (e.g. `HKD`/`USD`/`GBP`), the
  fund's native pricing currency. `server_default='HKD'` so existing rows and the
  NOT NULL constraint are satisfied. Non-sensitive → **plaintext** (not encrypted).
- **`investor_profiles.orso_display_currency`** — `String(3)`, currency the ORSO
  overview totals render in. `server_default='GBP'` (preserves today's behaviour).
- **`investor_profiles.orso_contribution_currency`** — `String(3)`,
  `server_default='HKD'`; gives `orso_monthly_contribution` a currency for
  projection normalisation.
- `orso_monthly_contribution` / `contribution_pct` **reused unchanged**.
- `OrsoFundPrice` **unchanged**: a price row is interpreted as being in its fund's
  `currency`. Statement-derived prices land here with `source='manual'`.

No new tables. `OrsoFund` model gains a validated 3-letter uppercase `currency`
field; the `FundCreate`/`FundOut`/`FundUpdate` schemas gain `currency`.

## Section 2 — The ingest pipeline (the spine)

One normaliser, three front doors, one review gate, one transactional commit.

**`AllocationDraft`** (returned by ingest endpoints, never persisted server-side):
```
AllocationDraft {
  rows: [ DraftRow ],
  warnings: [ str ],          # e.g. "pct_sum=97.00 (not 100)"
  source: "csv" | "screenshot"
}
DraftRow {
  matched_fund_id: int | null,          # matched in the user's menu, else null
  proposed_fund: { code, name, currency, asset_class?, risk_rating? } | null,
  units: Decimal,
  value: Decimal | null,                # native-currency market value
  currency: str,                        # fund's native currency (matched or proposed)
  contribution_pct: Decimal,
  implied_price: Decimal | null,        # value / units when both present
  flags: [ str ]                        # per-row: "unmatched", "missing_units", ...
}
```

- **Parse step:** CSV parser and screenshot extractor each emit an
  `AllocationDraft`. Fund **matching**: by `code` (exact, upper-normalised), then
  fuzzy `name`; unmatched rows carry a `proposed_fund` for the user to confirm.
- **Endpoints (read-only — they parse and return a draft; they never write):**
  - `POST /api/orso/ingest/csv` (multipart file) → `AllocationDraft`
  - `POST /api/orso/ingest/screenshot` (multipart image) → `AllocationDraft`
- **Commit:** `POST /api/orso/allocation/apply` takes the **reviewed** draft and, in
  **one transaction**: (a) creates each confirmed `proposed_fund` as an `OrsoFund`
  (with currency); (b) writes each row's derived `manual` price
  (`value ÷ units`, `as_of` = today) via `OrsoPriceService.upsert_manual_price`;
  (c) replaces the allocation, **reusing the existing full-replace + switch-log
  logic** so every apply is audited exactly like a form save. All-or-nothing:
  any validation failure rolls the whole transaction back.
- The existing **`PUT /api/orso/allocation`** (pure form path) stays for
  hand-editing without an import.

`apply` request shape (already-reviewed, client-confirmed):
```
ApplyRequest {
  new_funds: [ { code, name, currency, asset_class, risk_rating } ],
  allocations: [ { fund_id | new_fund_code, units, contribution_pct,
                   price?: { market_value, as_of } } ],
  note: str | null
}
```
`new_fund_code` links an allocation row to a to-be-created fund within the same
transaction. When `price.market_value` is supplied, the server derives and stores
the unit price as `market_value ÷ units` (in the fund's currency). Validation mirrors `replace_allocation` (own funds only, no dup
fund, pct 0–100, units ≥ 0) plus: new-fund codes unique vs existing menu,
currencies are valid ISO codes.

## Section 3 — Screenshot vision extraction

- Runs through the **existing Guru LLM layer** — an Anthropic vision call (image
  block + structured-output schema returning the fund rows: code/name, units,
  value, currency, contribution_pct). Same provider/config seam as the rest of the
  Guru, so it inherits Project 2's multi-provider work later.
- **Governed like every LLM call:** per-user **daily budget** (→ 429
  `budget_exhausted`), **rate-limited**, **image size-capped** (reuse the existing
  2 MB upload cap), and it **never auto-commits** — output is always a draft the
  user confirms.
- Structured output is **validated** (Decimals, ISO currency codes). Anything
  unparseable becomes a **flagged draft row**, not a silent drop. Extraction
  failure (LLM down / unreadable image / no key) degrades to "couldn't read this —
  enter manually," **never a 500**.
- The image is used transiently for extraction and **not persisted**.

## Section 4 — CSV format

Header row required; column order-independent; unknown columns ignored.

| Column | Required | Notes |
|---|---|---|
| `fund_code` | yes | matched to menu; unmatched → proposed new fund |
| `fund_name` | if new | used when proposing a new fund |
| `units` | yes | Decimal ≥ 0 |
| `value` | optional | native-currency market value; with `units` → implied price |
| `currency` | if new / non-default | ISO code; defaults to the matched fund's currency |
| `contribution_pct` | yes | 0–100; draft warns if the column doesn't sum to 100 |

Parse errors become **flagged draft rows**, not a rejected file. Reuses the 2 MB
upload cap. Emits the same `AllocationDraft` as the screenshot path → same review
screen.

## Section 5 — Fund search + currency/projection refactor

- **Fund search:** `GET /api/orso/funds/search?q=` over the user's `OrsoFund` menu
  (matches `code` + `name`, includes zero-allocation and archived funds), used on
  the review/allocation screen to add a fund row. Adding a brand-new fund uses the
  existing `POST /api/orso/funds` (now with `currency`).
- **Overview refactor (`build_overview`):** replace the HKD-only sum. Each fund
  value = `units × price` **in the fund's own `currency`**, converted to the
  user's `orso_display_currency` via `FxService`, then summed. Each fund row
  carries both **native value** and **display value**. On an FX failure for a
  fund, that fund's display value is `null` and a new flag (e.g.
  `fx_unavailable: [codes]`) is raised — **never a 500** (mirrors today's
  degrade rule). `total_base` becomes `total_display` in the display currency.
- **Projection:** runs entirely in the display currency — current pot (converted)
  + monthly contribution (converted from `orso_contribution_currency`) → the
  existing 2/5/8% engine (`project()` unchanged internally).
- **Display-currency switcher:** `PUT /api/orso/display-currency` writes
  `investor_profiles.orso_display_currency`; the overview recomputes on next read.

## Section 6 — Goal-gap advice (extends the existing ORSO Guru mode)

Enrich the existing `generate_orso` **context**, not a new endpoint. Add to the
assembled context:

- the **projection gap** (shortfall/surplus vs `retirement_target_pot` under each
  2/5/8% scenario, in the display currency),
- the current **monthly contribution** and a computed **headroom** figure,
- per-fund **risk_rating / asset_class** and current vs target split.

The ORSO persona prompt gains a directive to recommend **concrete levers** —
raise contributions toward a figure, and/or shift a % from one fund/asset-class
toward another — to close the gap, while keeping existing guardrails: **own funds
only**, output **moderated**, **structured** (schema-validated), framed as
general guidance and **not licensed financial advice**. Regenerate-on-demand
already exists (`POST /api/orso/advice`) — no change there.

## Section 7 — Error handling, testing, rollout

**Error handling**
- Ingest endpoints are read-only; nothing is written until `apply`.
- LLM / parse / FX failures degrade to a flagged draft row or a null display
  value with a UI banner — **never 500**.
- `apply` is one transaction (all-or-nothing) and writes the switch log.
- Encryption unchanged: `units`/`contribution_pct`/switch-log state stay
  encrypted; `currency` codes are non-sensitive plaintext.

**Testing**
- Unit: CSV parse (happy + malformed rows), implied-price derivation, fund
  matching (code + fuzzy name), multi-currency `build_overview` (incl. FX-failure
  degrade), projection-in-display-currency, `apply` all-or-nothing rollback +
  switch-log write, display-currency switch.
- LLM: a `FakeLLMProvider` vision fixture built from the user's **redacted** sample
  statement; extraction validation (good rows, flagged rows, total failure →
  manual-entry fallback); budget-exhausted → 429.
- Isolation: ingest/apply/search/display-currency all 404 on another user's data.
- Frontend: vitest-axe on the new wizard; fetch mocked.

**Figma gate (standing rule):** the ingest wizard (upload → review/edit draft →
confirm), fund search, and currency switcher get a Figma pass for user approval
**before** the frontend build.

**Sample statement:** requested from the user twice — (1) during the
screenshot-ingest build task (redacted, to validate the schema + build the test
fixture); (2) post-ship, to seed the real catalogue in prod.

**Build order (rough):**
1. Migration 0009 + `OrsoFund.currency` + profile currency fields + schema updates.
2. Multi-currency `build_overview` + projection-in-display-currency refactor +
   `PUT /display-currency`.
3. CSV ingest (`POST /ingest/csv`) + `AllocationDraft` + fund matching +
   `POST /allocation/apply` (transactional, switch-logged).
4. Screenshot vision ingest (`POST /ingest/screenshot`) through the Guru LLM layer.
5. Fund search (`GET /funds/search`).
6. Goal-gap advice enrichment (`generate_orso` context + persona).
7. Figma gate (USER GATE).
8. Frontend ingest wizard + fund search + currency switcher.
9. Docs + live smoke + final whole-branch Opus review.

## Out of scope

- Pointing the live HSBC price feed at the Local Staff DC Scheme (separate
  investigation).
- Multi-scheme support / a shared cross-user fund catalogue.
- Automatic statement fetching (user uploads manually).
- Contribution periods other than monthly (annual/per-paycheck normalisation).
