# Sector-Rotation Advice — Design Spec

_Date: 2026-07-12 · Enhancement Project 5 of 5 (final) · Investment Guru._

## Goal

Give the user a Guru **"rotation view"**: a macro/market-aware read on how their
user-defined holding groups ("Big Tech", "Space", "Financials", Ungrouped) are
positioned, and directional suggestions on where they might **rotate** between
groups — grounded in the data the app already collects, saved and
regenerate-on-demand, surfaced on the Sectors page. Educational commentary with
the standard not-regulated-financial-advice disclaimer; never auto-executes
trades.

## Decisions (resolved during brainstorming)

1. **Advice basis = macro rotation view.** Not just portfolio-internal drift —
   the Guru layers a market/business-cycle-aware opinion on which sectors/themes
   look favoured now, on top of the user's exposure. (The most advisor-like of
   the options; the guardrails in §2 exist because it is also the most
   speculative mode built so far.)
2. **Macro view grounded in the app's own data**, not the model's training
   memory: per-group news headlines (Project 3), price momentum/signals (Project
   2a), and group weight + trend drift (Project 4). Keeps the view current and
   defensible; no new external dependency.
3. **On-demand only**, mirroring the ORSO advice mode — a saved
   `GuruReport(kind="rotation")` shown on the Sectors page with a
   Generate/Regenerate button. Not in the daily digest (avoids burning budget on
   a speculative call the user didn't ask for).
4. **No new table, no migration** — reuses `GuruReport` (new `kind` value) + the
   existing usage ledger. Payload encrypted at rest like every other report.
5. **Directional only** — rotations name from-group → to-group with rationale +
   conviction; **no £ amounts, share counts, or price targets** (educational,
   not an order).

## Context & constraints (what exists today)

- **Groups (Project 4, migration 0012):** `HoldingGroup` + one-group-per-stock
  `GroupAssignment` + `GroupSnapshot` (forward-only value history).
  `compute_group_exposure(db, user, quote_service, fx)`
  (`app/services/groups/exposure.py`) already aggregates every real portfolio
  into a single **GBP base** per group (value + pct + day_change), degrading a
  holding to `unpriced` on quote/FX failure (never 500). `GET /api/groups/trend`
  reads the snapshot history.
- **Guru LLM layer (Projects 1/2):** provider-agnostic
  (`app/services/guru/llm/`, Anthropic/OpenAI/Gemini via `build_provider`);
  `GuruService` with `advice_model`/`scan_model`; `PERSONA_V1`; per-user daily
  budget (`check_budget` → **429**); single-flight `_lock(kind)` → **409
  `generation_in_progress`**; degrade-never error mapping (503/502);
  `provider.generate_structured(system, messages, schema, model, max_tokens)`
  returning a validated Pydantic payload + `Usage`; `record_usage(...)`; and the
  `generate_orso` method is the closest analog for this feature (build context →
  advice model with a typed payload → persist encrypted `GuruReport` → save +
  regenerate on demand).
- **Signals (Project 2a):** the signals engine stores per-instrument signals
  (`price_move_day`, `price_move_week`, `fifty_two_week`, `unusual_volume`,
  `news_recent`, …) and portfolio-level `concentration`/`fx_exposure`.
- **News (Project 3):** `app/services/market_data/news_read.py` provides
  recent, de-duped, per-instrument headlines (TTL-refreshed, feed failure
  degrades to cache, never 500).
- **Profile:** `InvestorProfile.risk_appetite` (e.g. conservative/balanced/
  aggressive) + `horizon`.
- **Frontend:** React 18 + Vite + Tailwind + TanStack Query; the ORSO advice
  panel + Guru take panel are the visual/interaction templates. Figma key
  `0gU58wfjttdZS0NXQeEtuD`.

**Golden rules that apply:** every user-data route is user-scoped and 404s on
another user's data; LLM output degrades, never 500s (budget → 429, in-progress
→ 409, provider/feed failure → 502/503); monetary amounts encrypted at rest;
this is the **adult personal-portfolio app** — the kids-app WCAG/moderation rules
do NOT apply, but the not-regulated-financial-advice disclaimer does.

---

## Section 1 — Grounding context (`build_rotation_context`)

New `app/services/groups/rotation_context.py::build_rotation_context(db, user,
quote_service, fx)` → a JSON-serialisable dict the advice model reasons over.
Per user-defined group (including the null **Ungrouped** bucket):

- **Weight & drift** — current GBP `value_base` + `weight_pct` (from
  `compute_group_exposure`); drift over the available `GroupSnapshot` history
  (e.g. first vs latest weight over N days). When history is sparse (it will be
  at launch — 0012 only just shipped), drift is omitted and `history_days` is
  recorded instead.
- **Momentum** — aggregated from the signals engine over the group's held
  instruments: a weighted/average recent move (`price_move_day/week`),
  `fifty_two_week` position, and any `unusual_volume` movers → a compact
  `momentum` summary + `notable_movers: [symbol, …]`.
- **News themes** — a compact, de-duped list of recent headlines
  (title/source/published_at) for the group's holdings via the Project 3 news
  read, capped per group (e.g. top ~5) so the "what's happening" is current.
- **Profile** — `risk_appetite` + `horizon`, so the view is framed to the user.

Also returns a top-level `availability` block recording which inputs were
present (e.g. `{"trend_history": false, "news": true, "signals": true}`) so the
advice can honestly caveat thin data.

**Degrade-never:** a down feed, a missing signal, or absent history drops that
field only — it never blocks generation. Reuses `compute_group_exposure`'s
existing FX/quote degradation. No new external dependency; no new tables — this
is a read-time aggregation of what Projects 2a/3/4 already store, user-scoped
throughout.

## Section 2 — Advice output (`RotationAdvicePayload`) + guardrails

New Pydantic schema in `app/services/guru/schemas.py` (mirrors
`OrsoAdvicePayload`), forced via `generate_structured`:

- **`market_view: str`** — a short headline framing the current rotation stance,
  explicitly hedged as a view (not a certainty).
- **`groups: list[GroupObservation]`** — one per group: `name`, `weight_pct`,
  a one-line `observation` (drift + momentum + news theme drawn from §1), and
  `signal: Literal["favour", "trim", "hold"]`.
- **`rotations: list[Rotation]`** — `from_group: str`, `to_group: str`,
  `rationale: str`, `conviction: Literal["low", "medium", "high"]`. Directional
  only — schema carries **no amount/quantity/price fields**.
- **`caveats: list[str]`** — thin history, sparse news, a down feed, or high
  macro uncertainty, surfaced honestly.
- **`disclaimer: str`** — the standard "educational, not regulated financial
  advice" line.

**Guardrails** (this is the most speculative mode built; the
`_ROTATION_INSTRUCTION` is strict):

- The market view **must reason only from the provided grounding context** — it
  may not invent live prices, rates, or figures not present in the context. If
  the data doesn't support a call, it must say so via a `caveat` rather than
  guess.
- **No specific trade instructions** — directional rotation language only, never
  "sell N shares at £X."
- `from_group`/`to_group` **must be names of the user's actual groups** (incl.
  "Ungrouped"); a post-generation validation re-prompts once (like the ORSO
  fund-code retry) if a rotation references an unknown group, then raises
  `LLMError` if still invalid.
- Runs on the **`advice_model`**, on-demand only. Reuses `check_budget` (429),
  `_lock("rotation")` (409), degrade mapping (502/503). Encrypted `GuruReport`
  persistence keeps it private at rest.

## Section 3 — API, persistence & regeneration

New service method `GuruService.generate_rotation(db, user, quote_service, fx)`
alongside `generate_orso`, plus a `_ROTATION_INSTRUCTION` constant and the
existing `PERSONA_V1`. Endpoints live in `app/api/groups.py` (same router):

- **`POST /api/groups/rotation`** — builds the §1 context, calls the advice
  model with `RotationAdvicePayload`, runs the group-name validation/retry,
  persists `GuruReport(kind="rotation", portfolio_id=None, payload=…encrypted)`,
  records usage/cost, returns `{ payload, generated_at, model }`. Budget-gated
  (429), single-flight (409), degrade-never (502/503). This is the
  Generate/Regenerate action.
- **`GET /api/groups/rotation`** — returns the latest saved rotation report for
  the user (`{ payload, generated_at, model }`) or `null` if none yet. Cheap
  read, **no LLM call** — the panel shows the last view on page load and only
  spends budget when the user regenerates.

Both auth-required and user-scoped (`GuruReport.user_id == user.id`,
`kind == "rotation"`, latest by `created_at`). **No new table** — reuses
`GuruReport` + the usage ledger. **No migration.**

## Section 4 — Frontend (Sectors-page rotation panel)

A **"Guru's rotation view"** card at the bottom of `SectorsPage.tsx` (below the
Trend card), matching the app card style and the ORSO advice panel:

- **Header** — title + **Generate / Regenerate** primary button; `generated_at`
  timestamp beside it once a view exists.
- **Empty state** — a short prompt + Generate button; **no LLM call until
  clicked**.
- **Populated** — the `market_view` headline; a **rotations list**
  (`from_group → to_group`, rationale, conviction chip low/med/high, group
  colour dots consistent with the rest of the page via the existing
  `group_id`-keyed colour helper — matched by group name→id); a compact
  **per-group signal row** (favour/trim/hold); `caveats` (muted) + `disclaimer`
  at the foot.
- **States** — loading (button spinner), **429** ("daily AI budget reached — try
  tomorrow"), **409** ("a view is already generating"), generic degrade — reusing
  the existing ORSO/take panel patterns. `vitest-axe` on the panel.
- New `getRotation` / `generateRotation` in `lib/api.ts` + `RotationAdvice`
  types mirroring the payload.

**Figma gate (standing rule):** the rotation panel (empty + populated states)
gets a Figma pass in `0gU58wfjttdZS0NXQeEtuD` for user approval **before** the
frontend build.

## Section 5 — Error handling, testing, rollout

**Error handling**
- Context builder degrades per input (down feed / missing signal / no history →
  field dropped, `availability` records it); never blocks generation.
- Generation: 429 (budget), 409 (in-progress lock), 502/503 (provider/feed) via
  the existing `map_guru_errors`; unknown-group rotations re-prompted once then
  `LLMError`.
- Reads and writes user-scoped; 404/scope on another user's data.

**Testing**
- **Context builder:** per-group aggregation (weight, drift-from-snapshots,
  momentum-from-signals, news themes); graceful degrade (no history / no news /
  down signal → field dropped, `availability` correct); user-scoping.
- **Service (`generate_rotation`):** `FakeLLMProvider` returning a
  `RotationAdvicePayload`; asserts encrypted `GuruReport(kind="rotation")`
  persisted, usage recorded, budget path (429), lock (409), unknown-group
  retry→raise, and that the payload carries **no amount/quantity/price** fields.
- **API:** `POST` generates + persists; `GET` returns latest or null; both
  auth-gated + cross-user 404/scope.
- **Guardrails:** an instruction test asserting `_ROTATION_INSTRUCTION` forbids
  invented figures + specific trade instructions and constrains reasoning to the
  provided context; the disclaimer is always present.
- **Frontend:** `SectorsPage` rotation panel empty → generate → populated;
  429/409/error states; `vitest-axe`; fetch mocked.

**Rollout**
- **No migration** (reuses `GuruReport` + usage ledger) → low-risk push seam
  (Vercel frontend + Railway backend, no DB change).
- Encryption at rest inherited.
- Pipeline: subagent-driven TDD, per-task review, **Figma gate** (§4) before the
  frontend, final Opus whole-branch review, live smoke (endpoints 401 + one real
  generation), docs + memory refresh.

**Build order (rough):**
1. `RotationAdvicePayload` (+ `GroupObservation`/`Rotation`) schema +
   `_ROTATION_INSTRUCTION`.
2. `build_rotation_context` (aggregate exposure/trend/signals/news per group,
   with `availability` + degradation).
3. `GuruService.generate_rotation` (advice-model call + group-name validation
   retry + encrypted persistence + usage).
4. `POST`/`GET /api/groups/rotation` API (budget/lock/degrade, user-scoped).
5. Figma gate (USER GATE).
6. Frontend rotation panel on the Sectors page (push seam).
7. Docs + live smoke + final Opus review.

## Out of scope

- Target-weight rebalancing (user-set per-group targets) and pure drift-only
  advice — the chosen basis is the macro rotation view (§Decisions 1).
- An external macro data feed (rates/ETF performance/economic indicators) — the
  view is grounded in the app's own data (§Decisions 2).
- Auto-refresh / scheduler / daily-digest inclusion — on-demand only.
- A dedicated rotation chat scope — the existing general Guru chat can already
  discuss the saved view; no new chat surface.
- Executing or sizing any trade — directional, educational commentary only.
