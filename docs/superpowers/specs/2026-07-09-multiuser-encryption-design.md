# Project 1 — Multi-user accounts + encryption at rest + admin role — Design Spec

**Date:** 2026-07-09 · **Status:** Approved pending user spec review
**Context:** First of a five-project enhancement programme on the shipped Investment Guru (all 5 master-spec phases live in prod). See `AGENTS.md` for system state.
**Enhancement programme (each its own spec→plan→build):** 1) this · 2) multi-provider LLM + admin config panel · 3) dashboard/news UX · 4) user sector grouping · 5) sector-rotation advice.

## 1. Summary

Turn the single-seeded-user app into a real multi-user product: open
self-service registration, strict per-user isolation (already the case — proven
by a test sweep here), encryption of financially-sensitive data at rest with a
server-held key, an email-allowlisted admin role + admin-area shell, a per-user
daily LLM budget, and an opt-in daily digest. No change to what the Guru or any
existing feature *does* — this is a foundation layer the rest of the programme
builds on.

**Decisions locked during brainstorm (2026-07-09):**
- Multi-user auth on the existing session/bcrypt/ownership base.
- **Encrypt-at-rest, server-held key** (keeps server-side Guru + daily digest working).
- **Open self-service signup.**
- **Per-user daily LLM budget + opt-in digest** (so open signup can't run up the shared Anthropic bill).
- **Admin role = email allowlist** (`lee_ashmore@hotmail.co.uk`); admin area is a shell here, its LLM-config content lands in Project 2.
- Encryption scope = **amounts + content** (quantities, costs, report payloads, chat, free-text). Structural FKs + shared market data stay plaintext, so a stolen DB reveals no amounts/analysis/chat but *may* reveal which tickers a user holds (via the instrument link). The stronger full-ticker-secrecy variant was considered and deferred.
- Password reset / email verification **deferred** to a fast-follow (needs an email provider).

## 2. Auth & registration

- `POST /api/auth/register {email, password}` → validates email format + password ≥ 8 chars; duplicate email → 409 `email_taken`; on success creates the user (bcrypt hash, existing `hash_password`) and logs them in (same signed session cookie as login). Registration is rate-limited (reuse the `LoginThrottle` shape keyed by a `register:<ip-or-email>` bucket, or a small dedicated throttle) to stop signup spam.
- Login / logout / `me` unchanged except `GET /api/auth/me` now returns `{id, email, is_admin}`.
- `is_admin` derives from `settings.admin_emails` (see §3) — never a request-controllable field.
- Existing production hardening (secure/lax cookie, throttle, headers) applies unchanged.

## 3. Admin role + area shell

- Config: `admin_emails: list[str] = ["lee_ashmore@hotmail.co.uk"]` (env-overridable, comma-split). `is_admin(user) = user.email.lower() in {e.lower() for e in settings.admin_emails}`.
- `AdminUser` FastAPI dependency: resolves `CurrentUser`, then 403 `admin_only` if not admin.
- Frontend: `/admin` route + an "Admin" nav item rendered only when `me.is_admin`. In this project the page is a shell — an "Admin area" landing that states the account is an admin and lists what will live here (LLM provider config arrives in Project 2). No admin-only data endpoints yet beyond the gate itself + one trivial `GET /api/admin/ping` (200 for admins, 403 otherwise) to prove the gate end-to-end.
- Rationale for allowlist over an `is_admin` DB column: "only my account" is exactly an allowlist; it cannot be escalated by mutating a row, and needs no migration.

## 4. Encryption at rest

- New env var `DATA_ENCRYPTION_KEY` — a urlsafe-base64 32-byte Fernet key (generation documented in the runbook; **distinct from `SECRET_KEY`**). Absent in production → `validate_production_settings` fails hard (extend the existing check). Absent in dev/tests → a fixed test key is used so the suite runs without secrets.
- New dependency: `cryptography` (Fernet = AES-128-CBC + HMAC, authenticated).
- `app/core/crypto.py`: `encrypt(plaintext: str) -> str` / `decrypt(token: str) -> str` using a `MultiFernet` seeded from `DATA_ENCRYPTION_KEY` (list-valued to allow rotation — primary key first). A stored value is `v1:<fernet-token>` (the `v1` marker is the `key_version` hook; decryption dispatches on it, so a future key rotation re-wraps as `v2:` without a data migration flag day).
- Two SQLAlchemy `TypeDecorator`s in `app/core/crypto.py`:
  - `EncryptedDecimal(Numeric-like)` — Python side stays `Decimal`; DB column is `Text`; encrypts `str(Decimal)` on bind, `Decimal(decrypt(...))` on result. Preserves exact decimal semantics (no float).
  - `EncryptedJSON` — Python side stays `dict`/`list`; DB column `Text`; `encrypt(json.dumps(...))` / `json.loads(decrypt(...))`.
  - `EncryptedText` — Python `str`; DB `Text`.
- **Encrypted columns** (sensitive values only): `positions.quantity`, `positions.avg_cost`; `orso_allocations.units`, `orso_allocations.contribution_pct`; `orso_switch_log.old_state`, `orso_switch_log.new_state`; `guru_reports.payload`; `chat_messages.content`; `investor_profiles.free_text`.
- **Left plaintext** (structural / shared / non-sensitive): all `user_id`/`portfolio_id`/`instrument_id` FKs; `instruments` (shared market reference), all price/quote/FX/fundamentals/news caches (shared market data), `orso_funds` menu (public scheme data), `llm_usage` (tokens/cost/mode, no holdings), `signals` (derived; see note), user email + password hash (email is the login key; hash is already one-way).
  - *Signals note:* `signals` rows carry symbols + figures derived from holdings. They are transient (replaced every analysis run) and needed plaintext for the dashboard attention query's severity ranking. Left plaintext in P1; revisit only if the ticker-secrecy variant is ever adopted.
- The migration (§6) converts existing columns to the encrypted `Text` form and encrypts any present plaintext in place.

## 5. Per-user LLM budget + opt-in digest

- Config: `guru_daily_budget_usd: Decimal = Decimal("1.00")`.
- `app/services/guru/budget.py`: `async check_budget(db, user_id) -> None` — sums `llm_usage.est_cost_usd` for the user since local-midnight (`guru_timezone`); if ≥ budget raise `BudgetExhausted`. Mapped to HTTP **429** `budget_exhausted` via the existing `map_guru_errors` (extended). Called at the top of every `GuruService` generate path and the chat turn, before the provider call.
  - Unknown-cost models (est_cost null) count as 0 — acceptable; budgets are a guardrail, not billing.
- `investor_profiles.digest_enabled: bool = False`. Toggled in Settings (`GET/PUT /api/guru/profile` gains the field). Default off.
- Scheduler (`app/services/guru/scheduler.py`): `run_daily_job` and `catch_up` iterate **users with `digest_enabled = true`** (was: first user only), skipping any already at/over budget for the day. Per-user failure isolation (one user's LLM error never aborts the loop). Catch-up on boot generates only missing-today digests for opted-in users. At current scale a simple iteration is fine; a note documents that a large opted-in population would want batching/staggering.

## 6. Data model & migration (0007)

- Alembic `0007` (chains on `0006`): alter the encrypted columns to `Text`; add `investor_profiles.digest_enabled` (Boolean, default false, server_default false). No new tables.
- **In-place encryption:** the upgrade reads each existing row's plaintext value, wraps it with the app crypto, and writes the `v1:` token back. Written to be correct even though prod currently holds ~no real holdings (the seeded ORSO fund *menu* is plaintext and untouched; there are no positions/allocations yet). Downgrade decrypts back to the original column types.
- Migration runs inside the deploy start command (`alembic upgrade head`) as today; the encryption key must be present in the Railway env before the deploy that ships 0007 (runbook step).

## 7. Frontend

- **Login page** gains a "Create account" toggle → registration form (email, password, confirm) hitting `/api/auth/register`; on success routes into the app. Client-side: password length hint, mismatch guard, surfaces 409 `email_taken` / 429 rate-limit cleanly.
- **Settings page** gains a "Daily digest" toggle (writes `digest_enabled`) and a small read-only note of the per-user daily budget.
- **Admin**: nav item + `/admin` shell page (admin-only), per §3.
- Budget-exhausted (429) surfaces on Guru actions as a friendly "daily AI limit reached — resets tomorrow" state, distinct from the existing unconfigured/error states.
- No other visual change; reuse existing card/form/token idioms. Figma: the registration form + admin shell + digest toggle are small and follow existing patterns — a light Figma pass for the registration screen only (standing rule); admin shell + toggle build directly.

## 8. Error handling

| Failure | Behaviour |
|---|---|
| Duplicate registration email | 409 `email_taken` |
| Weak/invalid registration input | 422 |
| Registration spam | 429 (rate-limited) |
| Non-admin hits admin endpoint | 403 `admin_only` |
| User over daily LLM budget | 429 `budget_exhausted` (Guru only; everything else works) |
| `DATA_ENCRYPTION_KEY` missing in prod | Startup fails hard (fail-closed) |
| Decrypt failure (tampered/corrupt/rotated-away key) | 500 logged with row id, never silent plaintext; surfaced as a generic server error (should not occur in normal operation) |

## 9. Testing

- **Encryption:** round-trip for each TypeDecorator (Decimal exactness, JSON structure, text); DB-level assertion that a written column is ciphertext (`v1:` prefix, not the plaintext); key-rotation dispatch (`v1` value still decrypts when a second key is prepended).
- **Isolation sweep:** a parametrised test creating two users and asserting every user-scoped GET/POST/PUT returns 404 (not 403, not data) when user B targets user A's portfolio/report/thread/ORSO fund/allocation — a single test module covering the full route surface, so cross-user leakage is caught centrally.
- **Registration:** success + auto-login cookie; duplicate → 409; short password → 422; rate limit → 429.
- **Admin gate:** allowlisted email → 200 on `/api/admin/ping` + `me.is_admin` true; other user → 403 + `is_admin` false.
- **Budget:** seed `llm_usage` to just under cap → Guru call succeeds; at/over cap → 429; next-day (clock injection) → succeeds again.
- **Scheduler multi-user:** two users, one opted in + one not → only the opted-in user gets a digest; opted-in-but-over-budget user skipped; one user's injected LLM failure doesn't abort the other.
- Frontend: registration form (mocked fetch: success, 409, mismatch), admin nav visibility by `is_admin`, digest toggle PUT, budget-exhausted state; vitest-axe on new UI.
- **Live smoke:** register a throwaway second user in prod; confirm they see none of your data and you see none of theirs; inspect the DB to confirm the encrypted columns are ciphertext; verify `/admin` 403s the throwaway user and loads for you; purge the throwaway user + data afterward.

## 10. Out of scope

Multi-provider LLM + admin LLM-config panel (Project 2). Password reset / email verification (fast-follow, needs an email provider). Per-user admin overrides of budget. The full-ticker-secrecy encryption variant. Team/shared portfolios. Billing/subscriptions.

## 11. Build order (for the implementation plan)

1. `crypto.py` (Fernet + TypeDecorators + versioned tokens) + config key + fail-hard, all TDD, no DB wiring yet →
2. migration 0007 + switch the model columns to encrypted types + in-place encryption →
3. registration endpoint + rate limit + `me.is_admin` →
4. admin allowlist + `AdminUser` dep + `/api/admin/ping` →
5. per-user budget check + `map_guru_errors` 429 + wire into all Guru paths →
6. `digest_enabled` + scheduler multi-user iteration →
7. cross-user isolation test sweep →
8. Figma pass (registration screen) — user gate →
9. frontend: registration, admin shell + nav, digest toggle, budget state →
10. docs (`AGENTS.md`, `PROGRESS.md`, runbook: `DATA_ENCRYPTION_KEY` generation + deploy ordering) + live smoke → final whole-branch review (Opus).
