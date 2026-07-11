# Multi-provider LLM + Admin Config Panel — Design Spec

_Date: 2026-07-12 · Enhancement Project 2 of Investment Guru._

## Goal

Let the admin (only `lee_ashmore@hotmail.co.uk`) choose which LLM **provider**
(Anthropic, OpenAI, or Google Gemini), which **models**, and supply the **API
key** from an admin panel — so the Guru's AI features can run on any of the
three without a code change or redeploy. The key is stored encrypted at rest and
never returned to the browser.

## Context & constraints (what already exists)

- **`LLMProvider` ABC** (`app/services/guru/llm/base.py`): `generate_structured(*, system, messages, schema, model, max_tokens) -> (BaseModel, Usage)` and `stream_text(...) -> TextStream`. Only `AnthropicProvider` implements it; callers build **Anthropic-shaped** `messages` (role/content, with Anthropic-format image blocks for the ORSO screenshot-vision path) and pass them straight through.
- **`get_guru_service()`** (`app/services/guru/service.py`): a process-lifetime singleton (`_service`) built **once** from `settings.anthropic_api_key`, holding the provider + `QuoteService` + `FxService`. Per-call model comes from `settings.guru_advice_model` (Opus) / `settings.guru_scan_model` (Haiku).
- **Consumers of the LLM:** Guru reviews, daily digest, dashboard take, SSE chat, ORSO switching/goal-gap advice, and the ORSO **statement-screenshot vision ingest** (`generate_structured` with an image block). All must keep working on whichever provider is active.
- **Usage/cost:** `app/services/guru/usage.py` — `estimate_cost(model, usage)` keyed by model-id prefix; `record_usage(...)`; the per-user **daily budget** (`check_budget` → 429 `budget_exhausted`) sums estimated USD spend.
- **Error contract:** `map_guru_errors()` maps `LLMNotConfigured`→503, `GenerationInProgress`→409, `LLMError`→502, `BudgetExhausted`→429. Endpoints **degrade, never 500**.
- **From Project 1:** email-allowlist admin role + `AdminUser` dependency (403 `admin_only`) + `/admin` shell; Fernet encryption-at-rest (`EncryptedText`, key `DATA_ENCRYPTION_KEY`). Alembic head **0009**.

**Golden rules that apply:** public repo — never commit real keys/secrets (the admin key lives only in the encrypted DB column, entered by the user in the panel — Claude never handles it); `Decimal` for money; DB change = one hand-written chained Alembic migration; providers fixture-mocked in tests, endpoints degrade-never-500; Figma-first for the panel.

## Decisions (resolved during brainstorming)

1. **One active provider, two model roles.** A single provider powers everything; it has an `advice_model` and a `scan_model` (mirrors today's Opus/Haiku split).
2. **Providers: Anthropic + OpenAI + Google Gemini.**
3. **Free-text model IDs** (future-proof) + **optional per-role pricing** (input/output USD per 1M tokens) so budget tracking stays accurate for models not in the built-in table.
4. **Test-connection** button: one minimal live call validates provider+key+model before save.
5. **Message-format handling = Approach A:** Anthropic-shape stays canonical; the OpenAI and Google adapters translate from it. Callers are unchanged.
6. **Precedence:** a saved `llm_config` row is authoritative; with no row, fall back to today's env-based behaviour.

---

## Section 1 — Data model (`llm_config`, migration 0010)

A single active-config row (admin-owned; not per-user). New table `llm_config`:

| Column | Type | Notes |
|---|---|---|
| `id` | PK | always one row (id=1 convention; upsert) |
| `provider` | `String(16)` | `anthropic` \| `openai` \| `google` |
| `advice_model` | `String(64)` | free-text model id |
| `scan_model` | `String(64)` | free-text model id |
| `api_key` | `EncryptedText` | Fernet at rest; **never returned to the frontend** |
| `advice_input_price` | `Numeric(10,4)` null | USD per 1M input tokens (optional) |
| `advice_output_price` | `Numeric(10,4)` null | USD per 1M output tokens (optional) |
| `scan_input_price` | `Numeric(10,4)` null | optional |
| `scan_output_price` | `Numeric(10,4)` null | optional |
| `updated_at` | datetime | |
| `updated_by` | `String(255)` | admin email |

Migration **0010** (chained on 0009) creates the table. `api_key` is `EncryptedText`
(the same non-nullable-nullable passthrough as other encrypted columns). Prices
are plaintext `Numeric`.

**Precedence:** `load_active_config(db)` returns the row if present, else a
synthetic default from env (`provider="anthropic"`, models from
`settings.guru_advice_model`/`guru_scan_model`, key from
`settings.anthropic_api_key`). So existing prod keeps running until the panel is
first saved.

## Section 2 — Provider adapters

Three implementations of the unchanged `LLMProvider` ABC in `app/services/guru/llm/`:

- **`AnthropicProvider`** — unchanged (canonical Anthropic shape).
- **`OpenAIProvider`** (`openai` SDK, `AsyncOpenAI`) — translate Anthropic-style messages → OpenAI: `system` becomes a `{"role":"system"}` message; text content passes through; an Anthropic image block `{"type":"image","source":{"type":"base64","media_type":m,"data":d}}` → `{"type":"image_url","image_url":{"url":"data:{m};base64,{d}"}}`. Structured output via `client.beta.chat.completions.parse(response_format=<pydantic schema>)`; streaming via `client.chat.completions.create(stream=True)` accumulating `choices[].delta.content`; usage from `resp.usage` (`prompt_tokens`/`completion_tokens`) → `Usage`.
- **`GoogleProvider`** (`google-genai` SDK) — translate to `contents` (+ `system_instruction`); image → `inline_data` (mime + bytes); structured output via `generate_content(config={response_mime_type:"application/json", response_schema:<pydantic>})` then validate into the schema; streaming via the streaming generate call; usage from `usage_metadata` (`prompt_token_count`/`candidates_token_count`) → `Usage`.

Every adapter wraps SDK/network/parse/validation failures in the existing
uniform `LLMError`. A `build_provider(provider: str, api_key: str) -> LLMProvider`
factory maps the provider string to the right adapter. New dependencies:
`openai`, `google-genai` (added to `pyproject.toml`; both are optional at runtime
— only the selected provider's SDK is instantiated).

**Vision note:** statement-reading requires image + structured output together.
All three providers support it, but only on vision-capable models — the panel's
help text notes this; a non-vision model simply surfaces a clean `LLMError`
(→ 502) on the screenshot path, and CSV/manual entry remain unaffected.

## Section 3 — Config-aware, rebuildable factory

`get_guru_service()` becomes config-driven:

- It loads the active config (`load_active_config(db)`) and calls
  `build_provider(config.provider, config.decrypted_key)`; with no key it
  yields `provider=None` (→ existing `LLMNotConfigured`/503 path).
- Because config load is async (DB) and today's factory is sync, the service is
  built with the current config and **cached with a version stamp**. Saving the
  panel calls `invalidate_guru_service()` which bumps an in-process version
  (single Railway replica), so the next request rebuilds the service against the
  new provider/model/key. **No redeploy or restart needed.**
- Per-call model is chosen by **role**: advice paths use `config.advice_model`,
  scan/digest use `config.scan_model` (replacing direct `settings.guru_*_model`
  reads in `service.py`). The vision ingest uses `advice_model` (accuracy over
  cost for statement reading).

## Section 4 — Cost / budget

`estimate_cost` resolves price per (model, role) in this order:
1. **Config per-role price** (`advice_*`/`scan_*_price`) if set;
2. **Built-in table** — known Anthropic + OpenAI + Gemini model prefixes with
   published $/1M rates;
3. **`None`** — unknown free-text model with no price entered.

When `None`, usage is still recorded (tokens logged) but that call is **not**
counted toward the daily budget, and a one-line `log.warning("uncosted model …")`
makes it visible. Entering the optional prices restores exact budgeting for any
model. `record_usage`/`check_budget` are otherwise unchanged.

## Section 5 — Admin API + test connection

Under the existing `AdminUser` gate (email allowlist → 403 for non-admins):

- `GET /api/admin/llm-config` → `{provider, advice_model, scan_model, advice_input_price, advice_output_price, scan_input_price, scan_output_price, key_set: bool, updated_at, updated_by}` — **never the key value**.
- `PUT /api/admin/llm-config` (body: provider, models, optional prices, optional `api_key`) → upsert the single row; **if `api_key` is omitted/blank on an edit, the stored key is preserved** (change the model without re-entering the key); on success calls `invalidate_guru_service()`. Validates `provider ∈ {anthropic,openai,google}` and model non-empty.
- `POST /api/admin/llm-config/test` (body: same shape; `api_key` optional → falls back to stored) → performs **one minimal structured call** (a trivial schema, `max_tokens` small) with the candidate provider+key+model; returns `{ok: bool, detail: str}`. Wrapped so any provider/auth failure returns `{ok:false, detail}` — never a 500. Reuses the login-style throttle (bounded attempts) to cap cost/abuse.

The key is never written to logs or responses; on `PUT` it flows straight into
the `EncryptedText` column.

## Section 6 — Admin panel (Figma gate)

In the existing `/admin` shell (frontend), an "AI provider" section:
- Provider `<select>` (Anthropic / OpenAI / Google); advice + scan model text
  inputs; four optional price inputs (collapsible "advanced / budget"); a
  write-only API-key input that shows **"configured"** when `key_set` (blank
  means "keep existing"); a **Test** button (calls the test endpoint, shows ✓/✗
  with detail); Save. Reachable only by the admin (nav item gated on
  `me.is_admin`, backend-enforced by `AdminUser`).
- **Figma pass for user approval before the frontend build** (standing rule),
  reusing the existing admin-shell styling.

## Section 7 — Error handling, testing, rollout

**Error handling**
- API key never logged or returned (only `key_set`); at rest it is Fernet-encrypted.
- Degrade-never-500 preserved across all three providers (all failures → `LLMError`→502 / `LLMNotConfigured`→503 / `BudgetExhausted`→429).
- Test endpoint returns a clean `{ok:false}` on bad key/model, throttled.
- Unknown-model budget behaviour logged (§4).

**Testing**
- Each adapter unit-tested against a **mocked SDK**: message translation (incl. the image block), structured-parse happy path, streaming accumulation, and error→`LLMError`. No real network/keys in tests.
- `FakeLLMProvider` continues to back the higher-level Guru tests (reviews/digest/chat/ORSO), now selected via the config-aware factory in tests.
- Config precedence (row vs env fallback), rebuild-on-save (`invalidate_guru_service`), and role→model selection.
- Cost resolution order (config price → table → None-not-budgeted).
- Admin API: 403 for non-admins; `GET` never returns the key; `PUT` preserves an omitted key; `test` returns ok/failure without 500.
- Frontend: vitest-axe on the panel; key input is write-only; masked state renders.

**Migration/deploy**: 0010 additive (new table only), reversible.

**Build order (rough):**
1. Migration 0010 + `llm_config` model + `load_active_config` (env fallback).
2. Cost/pricing resolution (`estimate_cost` order + built-in OpenAI/Gemini table entries).
3. `OpenAIProvider` adapter (+ `openai` dep).
4. `GoogleProvider` adapter (+ `google-genai` dep).
5. Config-aware rebuildable factory + role→model wiring + `invalidate_guru_service`.
6. Admin API (`GET`/`PUT`/`test`) under `AdminUser`.
7. Figma gate (USER GATE).
8. Admin panel frontend (push seam).
9. Docs + live smoke + final Opus review.

## Out of scope

- Per-role / per-feature provider mixing (one active provider only).
- Streaming for providers where the Guru doesn't stream (only chat streams; if a provider's streaming is unavailable it degrades to a single-shot response — but all three support streaming, so this is a fallback, not a feature).
- A curated model dropdown (free-text + optional pricing chosen instead).
- Multi-tenant / per-user provider config (single global admin config).
