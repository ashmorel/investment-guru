# Phase 2b — The Guru (LLM layer) — Design Spec

**Date:** 2026-07-08 · **Status:** Approved pending user spec review
**Parent spec:** `2026-07-07-investment-guru-design.md` §4 (fixes the overall shape)
**Depends on:** Phase 1 (portfolio core) and Phase 2a (signals engine), both live at `865e3f1`.

## 1. Summary

Phase 2b ships the judgment layer: an investor profile, a provider-agnostic LLM
layer (Anthropic first), and four output surfaces — on-demand portfolio review
reports, a scheduled daily digest, the dashboard "Guru's take" panel (filling the
slot reserved in Phase 1), and per-position takes — plus a streaming chat panel.
Signals stay deterministic code (Phase 2a); the LLM only ever judges what the
deterministic layer hands it. One spec, one implementation plan, staged tasks.

**Decisions locked during brainstorm (2026-07-08):**
- One plan with staged tasks (backend LLM core → output modes → scheduler → chat → frontend).
- Digest: APScheduler at a configured morning hour **plus startup catch-up** (generate on boot if today's digest is missing).
- Chat: **SSE streaming**.
- Cost posture: **usage logging + sane max_tokens**, no hard caps.
- **New Figma pass** for the new screens before frontend build (standing rule).
- Architecture: **structured-output pipeline** (mode-specific schemas; chat is the only free-text path).

## 2. Data model (migration 0005)

All tables carry `user_id` (golden rule). Money-like values `Numeric`.

| Table | Purpose | Key columns |
|---|---|---|
| `investor_profiles` | 1:1 with user | `risk_appetite` (str: cautious/balanced/adventurous), `horizon` (str: short/medium/long), `sector_interests` (JSON list of str), `free_text` (Text), `updated_at` |
| `guru_reports` | All generated outputs, versioned by row | `kind` (str: review/digest/take), `portfolio_id` (nullable FK — set for reviews, null for digest/take), `payload` (JSONB, mode schema below), `model` (str), `created_at` |
| `chat_threads` | Conversations | `title`, `portfolio_id` (nullable context ref), `seed_context` (JSON, nullable — the item a "discuss" link seeded), `created_at` |
| `chat_messages` | Turns | `thread_id` FK, `role` (user/assistant), `content` (Text), `created_at` |
| `llm_usage` | One row per API call | `mode` (str), `model`, `input_tokens`, `output_tokens`, `est_cost_usd` (Numeric), `report_id`/`thread_id` (nullable FKs), `created_at` |

History = rows ordered by `created_at`; nothing is updated in place. Reports are
never deleted in 2b (personal app, cheap rows).

## 3. LLM layer — `app/services/guru/llm/`

Mirrors `app/services/market_data/` (provider behind an abstraction, fixture-mocked in tests).

```
app/services/guru/
  llm/base.py        # LLMProvider ABC + Usage dataclass + LLMError/LLMNotConfigured
  llm/anthropic.py   # AnthropicProvider (official `anthropic` SDK, AsyncAnthropic)
  llm/fake.py        # test double (deterministic payloads, scripted streams) — importable by tests
  schemas.py         # Pydantic output schemas per mode (§5)
  context.py         # ContextBuilder (§4)
  persona.py         # PERSONA_V1 + disclaimer constant
  service.py         # GuruService: generate_review / generate_digest / generate_take
  chat.py            # ChatService: thread mgmt + streaming turn
  scheduler.py       # APScheduler wiring + catch-up (§6)
  usage.py           # usage logging + cost estimation
```

### 3.1 Interface

```python
class LLMProvider(ABC):
    async def generate_structured(self, *, system: str, messages: list[dict],
        schema: type[BaseModel], model: str, max_tokens: int) -> tuple[BaseModel, Usage]: ...
    def stream_text(self, *, system: str, messages: list[dict],
        model: str, max_tokens: int) -> AsyncIterator[str]:  # yields text deltas; Usage available after close
```

`AnthropicProvider` implements `generate_structured` via the SDK's structured
outputs (`client.messages.parse(..., output_format=schema)` → validated Pydantic
instance) and `stream_text` via `client.messages.stream(...)`. Schema-invalid
output → one retry, then `LLMError` (logged; nothing persisted).

### 3.2 Config (`app/core/config.py` additions)

| Setting | Default | Notes |
|---|---|---|
| `anthropic_api_key` | `""` | Empty → Guru degrades to `llm_unconfigured` (503-style JSON, friendly UI banner); signals/portfolios unaffected. Never read `.env` directly in code review/tests. |
| `guru_advice_model` | `claude-opus-4-8` | Reviews, Guru's take, chat. $5/$25 per MTok. Fable/other = config swap. |
| `guru_scan_model` | `claude-haiku-4-5` | Daily digest. $1/$5 per MTok. |
| `guru_digest_hour` | `7` | Local hour for the scheduled run |
| `guru_timezone` | `Europe/London` | IANA tz for the scheduler + "today" checks |

Cost estimation uses a small static price table keyed by model prefix (opus-4 →
5/25, haiku-4-5 → 1/5 per MTok); unknown model → cost null, tokens still logged.

## 4. ContextBuilder

One builder feeds every mode. Output: a compact JSON document —
investor profile, per-portfolio valuations (existing valuation service, including
integrity flags: `costed_positions`, `day_change_partial`, currency-mismatch),
latest stored signals with severities, `unavailable_inputs`, and as-of timestamps.
The LLM never fetches data itself. Real portfolios only (watchlists included as a
labeled section, without cost data). Token-bounded by construction (single user;
if a pathological portfolio ever overflows, positions are truncated
largest-value-first with a `context_truncated` flag in the payload).

## 5. Persona and output modes

**Persona:** `PERSONA_V1` (versioned constant): world-class adviser, US/UK/HK
expertise, measured and evidence-first, states conviction, always explains why,
relates every recommendation to the stated profile and flags ideas outside it.
Every payload carries `disclaimer`: "The Guru is not regulated financial advice."
No moderation pass (single adult user — decided at Phase 2 decomposition).

All non-chat modes: `generate_structured` with a mode schema; persisted to
`guru_reports`; usage row written per call. Concurrent generation of the same
kind is blocked by a per-kind asyncio in-flight lock (second request → 409).

| Mode | Model | Trigger | Schema payload (Pydantic) |
|---|---|---|---|
| **Review** (`kind=review`, per-portfolio) | advice | POST from portfolio page | `positions: [{symbol, action: hold/increase/reduce/exit, conviction: low/med/high, rationale}]`, `observations: [str]`, `watch_next: [str]`, `disclaimer` |
| **Digest** (`kind=digest`, global) | scan | Scheduler + catch-up + manual button | `earnings_this_week: [{symbol, date?, note}]`, `movers: [{symbol, note}]`, `news_flags: [{symbol?, headline, comment}]`, `summary: str`, `disclaimer` |
| **Guru's take** (`kind=take`, global) | advice | Right after each digest run; manual refresh | `commentary: str`, `risks: [{kind, note}]`, `ideas: [{symbol?, action, conviction, rationale}]`, `disclaimer` |
| **Per-position take** | — (no LLM call) | Derived: latest review row for that portfolio, sliced by symbol | Rendered in position drawer with generated-at staleness label + "ask in chat" link; refresh = re-run the portfolio review |

Reviews must cover every position in the portfolio — enforced by a post-parse
check (missing symbols → retry once with a corrective message, then error).

## 6. Scheduler

APScheduler `AsyncIOScheduler` started in the FastAPI lifespan (dev-reload safe:
guarded so only one instance runs). One job at `guru_digest_hour` in
`guru_timezone`: generate digest (scan model) → then Guru's take (advice model).
**Startup catch-up:** on lifespan start, if no `kind=digest` row exists for
"today" in `guru_timezone`, run the same job immediately (non-blocking task).
No API key → job logs a skip line, does nothing. Failures are logged and leave
the previous digest/take in place. Phase 5 (cloud, always-on) inherits unchanged.
Cost at rest: ~1 Haiku + 1 Opus call per day.

## 7. Chat

- `chat.py` builds per-turn context: persona + profile + selected portfolio
  snapshot + latest signals + prior thread messages (most recent first within a
  token budget; oldest dropped).
- `POST /api/guru/chat/threads/{id}/messages` streams the reply over **SSE**
  (`text/event-stream`; events: `delta` with text chunks, terminal `done` with
  message id + usage, `error`). The user message persists immediately; the
  assistant message persists only on successful stream completion — a dropped
  stream persists nothing and the UI offers retry.
- "Discuss" links from take ideas / review verdicts create a thread with
  `seed_context` (the idea/verdict JSON) injected into the first turn's context.
- Usage row per turn (streaming usage from the final message).

## 8. API surface — `app/api/guru.py`

All endpoints auth'd + ownership-checked (same dependencies as signals). No rate
limiting (single local user); in-flight locks per §5.

```
GET/PUT  /api/guru/profile
POST     /api/guru/reviews            {portfolio_id} → 201 report
GET      /api/guru/reviews?portfolio_id=&limit=      (list, newest first)
GET      /api/guru/reviews/{id}
GET      /api/guru/digest/latest      · POST /api/guru/digest        (manual generate)
GET      /api/guru/take/latest        · POST /api/guru/take          (manual refresh)
GET/POST /api/guru/chat/threads       · GET /api/guru/chat/threads/{id}
POST     /api/guru/chat/threads/{id}/messages        (SSE stream)
GET      /api/guru/usage/summary      (totals by mode + last-30-days cost)
```

`response_model` set on all non-SSE endpoints (closes a 2a deferred item pattern).
LLM-unconfigured → 503 `{"detail": "llm_unconfigured"}`. Provider failure → 502
`{"detail": "llm_error"}`; nothing persisted; signals untouched.

## 9. Frontend

**Figma first:** mock Guru report reader, chat panel, and Settings investor
profile (+ usage readout) in the existing file (`0gU58wfjttdZS0NXQeEtuD`) using
the approved tokens; user approves before frontend tasks start. The Guru's-take
panel fills the dashboard slot already designed/reserved in Phase 1.

Surfaces:
- **Guru page** (existing nav slot): report reader — latest take + digest,
  versioned review/digest history list, review detail with per-position verdict
  chips (action + conviction); chat panel with thread list + streaming view.
- **Dashboard**: Guru's take panel (commentary, risks, ideas with "discuss"
  links); staleness label; refresh button; `llm_unconfigured` banner state.
- **Position drawer**: per-position take section (from latest review) + "ask in chat".
- **Settings**: investor profile form (structured fields + free text) + usage
  summary readout.

SSE consumed via `fetch` + `ReadableStream` reader (not `EventSource` — POST body
needed); React Query for the rest, keys including the UTC day where daily-reset
content is cached (established gotcha).

## 10. Error handling

| Failure | Behavior |
|---|---|
| No API key | 503 `llm_unconfigured`; friendly banner; everything non-LLM works; scheduler skips quietly |
| Provider error / timeout | One SDK-level retry (SDK default), then 502; nothing persisted; previous reports remain |
| Schema-invalid LLM output | One corrective retry, then 502 (logged with raw text) |
| Review missing positions | One corrective retry, then 502 |
| Dropped chat stream | User message kept, assistant message not persisted; UI retry |
| Concurrent generation same kind | 409 `generation_in_progress` |

## 11. Testing

- **Backend:** `FakeLLMProvider` (deterministic structured payloads + scripted
  streams + failure modes) injected via dependency override — no real API calls
  in tests. Schema round-trips; ContextBuilder snapshots (integrity flags,
  truncation); GuruService per mode incl. retry paths; scheduler catch-up with
  injected clock; API auth/ownership/degradation (401/403/503/502/409); SSE
  endpoint streamed via httpx client; usage rows + cost math. Async tests:
  `pytestmark = pytest.mark.asyncio(loop_scope="session")` + shared fixtures.
- **Frontend:** vitest + RTL for take panel, report reader, chat (mocked stream
  reader), profile form, unconfigured banner; vitest-axe on new UI.
- **Live smoke (final task):** real key in `backend/.env` (user-provided,
  never read by Claude), real digest + review + chat turn against dev DB;
  verify usage rows and Anthropic console spend.

## 12. 2a-deferred cleanup (one early task)

runAnalysis error-state UI; drop redundant `/api/dashboard` refetch; AttentionPanel
severity a11y text; fundamentals stale-row false-negative; per-rule logging in the
signal engine loop; news freshness keyed on `published_at`; multi-portfolio union
test; `filterwarnings=error` in pyproject.

## 13. Out of scope for 2b

Moderation pass, multi-user, rate limiting, hard spend caps, ORSO advice mode
(Phase 4), cloud deploy/always-on scheduler (Phase 5), report deletion/pruning,
non-Anthropic providers (interface only).

## 14. Build order (for the implementation plan)

1. 2a-deferred cleanup → 2. migration 0005 + models → 3. LLM layer (base/anthropic/fake/usage) →
4. profile API → 5. ContextBuilder + persona → 6. review mode + API →
7. digest + take modes + API → 8. scheduler + catch-up → 9. chat backend (SSE) →
10. Figma pass (user gate) → 11. frontend: settings + profile → 12. frontend: Guru page + dashboard take + drawer take →
13. frontend: chat → 14. docs + live smoke (needs user's Anthropic key) → final whole-branch review (Opus per model-mix rule).
