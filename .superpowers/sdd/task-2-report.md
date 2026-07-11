# Task 2 Report — Migration 0007: encrypt sensitive columns + digest_enabled

> Note: this filename is reused each phase per project convention. This report replaces the
> prior Phase 5 "Dockerfile + DATABASE_URL normalisation" content and documents Project 1
> Task 2 per `.superpowers/sdd/task-2-brief.md`.

## Status: DONE

## Files changed

- `backend/app/models/portfolio.py` — `Position.quantity`/`avg_cost` → `EncryptedDecimal()`; added `@validates` quantizers (see "Regression found" below).
- `backend/app/models/orso.py` — `OrsoAllocation.units`/`contribution_pct` → `EncryptedDecimal()`; `OrsoSwitchLog.old_state`/`new_state` → `EncryptedJSON()`; added `@validates` quantizers for `OrsoAllocation`.
- `backend/app/models/guru.py` — `GuruReport.payload` → `EncryptedJSON()`; `ChatMessage.content` → `EncryptedText()`; `InvestorProfile.free_text` → `EncryptedText()`; added `InvestorProfile.digest_enabled: Mapped[bool]` (`Boolean`, `default=False`, `server_default=false()`).
- `backend/alembic/versions/0007_encrypt_and_digest.py` — new migration, `revision="0007"`, `down_revision="0006"`.
- `backend/tests/test_encrypted_columns.py` — new test file (2 tests), per brief with one required fix (below).

## Deviations from the brief (both required for correctness)

1. **Test fixture fix**: the brief's `Portfolio(...)` call in the test omits `kind`, which is `NOT NULL` with no default — every other test file in the repo passes `kind="real"`/`"watchlist"`. Added `kind="real"` so the test fails/passes for encryption reasons, not an unrelated schema violation.
2. **Migration upgrade cast**: the brief's pseudocode uses `postgresql_using="NULL"` when altering column type. `orso_allocations.units/contribution_pct`, `orso_switch_log.old_state/new_state`, and `guru_reports.payload` are all `NOT NULL` — and importantly, **the dev DB already has real rows in every one of these tables** (12 positions, 5 orso_allocations, 2 orso_switch_log, 5 guru_reports, 4 chat_messages, 1 investor_profile), contrary to the brief's "near-no-op today" assumption (that claim is true for prod, not for this dev DB, which has real prior manual-testing data). `USING NULL` on a `NOT NULL` column with existing rows raises `NotNullViolationError`. Fixed by casting `{col}::text` instead (preserves the existing value as text; the very next step immediately overwrites every non-null row with ciphertext anyway, so the intermediate cast value never matters). Downgrade mirrors this: decrypt to plaintext text first, then `ALTER COLUMN TYPE <original> USING {col}::{numeric(...)|jsonb}`.

## Real regression found and fixed: Decimal precision loss

`EncryptedDecimal.process_bind_param` does `encrypt(str(value))` — unlike `Numeric(18,6)`, it does **not** enforce a fixed scale. Swapping `Position.quantity`/`avg_cost` and `OrsoAllocation.units`/`contribution_pct` to `EncryptedDecimal()` silently dropped trailing-zero padding, breaking 4 pre-existing tests that assert exact formatted output (e.g. `"7.000000"` became `"7"`):

- `tests/test_import_api.py::test_commit_merge_update_and_skip`
- `tests/test_import_api.py::test_commit_intra_payload_duplicate_symbol_deduped`
- `tests/test_import_api.py::test_commit_merge_replace`
- `tests/test_positions.py::test_position_crud`

Confirmed this is a genuine regression (not a bad test) by stashing all changes and re-running: baseline is 232 passed, 0 failed.

**Fix**: added `@validates` decorators on `Position` (quantity → 6dp, avg_cost → 4dp) and `OrsoAllocation` (units → 4dp, contribution_pct → 2dp) that quantize on attribute assignment, reproducing the DB-enforced scale the `Numeric` columns used to guarantee. This only fires on write (ORM attribute set), matching the old `Numeric` column's write-time behavior; reads are unaffected since the already-stored precision round-trips as-is.

`GuruReport.payload`, `ChatMessage.content`, `InvestorProfile.free_text`, `OrsoSwitchLog.old_state/new_state` needed no such fix — they were JSON/Text before, so there's no fixed-scale semantics to lose.

## TDD sequence

1. Wrote `test_encrypted_columns.py` per brief (with the `kind="real"` fix) — ran, failed for the right reasons (`portfolios.kind` NOT NULL was fixed first; then failures were the actual target: raw SQL values weren't `v1:`-prefixed ciphertext, and `digest_enabled` attribute didn't exist).
2. Implemented model column-type swaps + `digest_enabled` — reran, both new tests passed.
3. Ran full suite — found the 4-test precision regression (see above), fixed via `@validates`, reran — full suite green.
4. Wrote migration `0007`, ran the mandatory `upgrade → downgrade → upgrade` chain against the dev DB (see below).

## Test results

```
$ ruff check .
All checks passed!

$ pytest -q
234 passed in 67.48s   (232 pre-existing + 2 new; 0 failed)
```

## Alembic chain verification (mandatory, dev DB `guru`, real pre-existing data)

Snapshotted `positions`, `orso_allocations`, `orso_switch_log`, `guru_reports`, `chat_messages`, `investor_profiles` before running anything (12/5/2/5/4/1 rows respectively — real LLM-generated guru reports and chat transcripts from prior manual testing, not empty).

```
$ alembic upgrade head
INFO  Running upgrade 0006 -> 0007, switch sensitive columns to encrypted Text + digest_enabled
```
Verified via raw SQL: every previously plaintext/typed column now holds `v1:...` Fernet ciphertext; `investor_profiles.digest_enabled` present, `False` for the existing row.

```
$ alembic downgrade 0006
INFO  Running downgrade 0007 -> 0006, switch sensitive columns to encrypted Text + digest_enabled
```
Re-snapshotted all 6 tables and diffed against the pre-migration snapshot: **byte-for-byte identical**. Also confirmed column types restored exactly: `positions.quantity` → `numeric(18,6)`, `avg_cost` → `numeric(18,4)`; `orso_allocations.units` → `numeric(18,4)`, `contribution_pct` → `numeric(5,2)`; `orso_switch_log.old_state` → `jsonb`; `guru_reports.payload` → `jsonb`; `investor_profiles.free_text` → `text`; `digest_enabled` column dropped.

```
$ alembic upgrade head
INFO  Running upgrade 0006 -> 0007, ...
$ alembic current
0007 (head)
```

ORM spot-check post final-upgrade: `Position.quantity`/`avg_cost` decrypt to the original `Decimal` values with correct scale (`10.000000`, `150.0000`); `InvestorProfile.free_text` decrypts to `"Prefer dividend payers in the UK book. Avoid tobacco."`; `OrsoSwitchLog.old_state` decrypts to the original list.

Full `upgrade → downgrade → upgrade` chain: clean, no errors, exact data round-trip.

## Self-review

- **Confidence: high.** Both deviations from the brief's literal pseudocode were forced by concrete, reproduced problems against real dev data — not speculative changes. The precision regression was confirmed via a before/after `git stash` comparison (232 passed baseline vs. 4 failures with the naive column swap); the NOT-NULL cast issue was identified by inspecting actual row counts in the dev DB before running any DDL, avoiding a destructive failure on a non-empty database.
- **Trickiest downgrade type restores**: `orso_switch_log.old_state/new_state` and `guru_reports.payload` (JSONB) — downgrade must decrypt back to the *exact* `json.dumps(...)` text that was encrypted, then `::jsonb`-cast it. Verified exact round-trip on real nested LLM-report payloads (multi-KB JSON with nested lists/dicts) and real switch-log fund-allocation history, not just trivial fixtures.
- **`chat_messages.content` / `investor_profiles.free_text` downgrade** is intentionally a no-op on column *type* (both were `Text` originally) — only values are decrypted in place. Verified exact round-trip.
- Migration runs inside Alembic's single transactional-DDL block (confirmed by the `Will assume transactional DDL` log line), so a mid-migration failure would roll back cleanly; not exercised (no failure occurred).
- Did not touch `EncryptedDecimal`/`EncryptedText`/`EncryptedJSON` in `app/core/crypto.py` — that's Task 1, out of scope. The precision issue was fixed at the model layer instead (`@validates`), the correct layer since only `Position` and `OrsoAllocation` need fixed-scale semantics; quantizing inside `EncryptedDecimal` itself would have been a broader, unrequested behavior change affecting every future consumer of that type.
- No production data touched — everything above ran only against the local dev DB (`guru`, port 5433, `investment-guru-db-1` container). Nothing pushed.

## Concerns

- The brief's claim that the encryption row-loop is "a near-no-op today" is accurate for **prod** but was materially wrong for the **dev DB**, which had 29 total rows across the 6 affected tables from prior manual/LLM testing. Anyone re-running this migration against another populated dev/staging DB should expect the same NOT-NULL-cast consideration already handled here — flagging in case a reviewer assumes the row loop is untested; it was fully exercised against real data.
- `@validates` quantization only applies on ORM attribute assignment, not on values loaded from the DB. This matches the old `Numeric` column's actual behavior (which quantized on write via the DB, not on read) but is worth double-checking if a future consumer writes to these columns via raw SQL/bulk operations that bypass the ORM — such writes would skip quantization (same limitation existed implicitly before, since Postgres NUMERIC would have enforced it at the DB level; now enforcement is Python-side only, in the ORM layer).

## Post-task fix: decimal rounding mode

**Issue discovered**: the `@validates` quantizers added in this task used `Decimal.quantize(Q)` with the default rounding mode `ROUND_HALF_EVEN` (banker's rounding). The previous `Numeric` columns used Postgres's `ROUND_HALF_UP` (half-away-from-zero), causing silent behavior changes on exact-halfway inputs (e.g., `1.00005` rounds to `1.0000` with banker's rounding, but should round to `1.0001` with half-up).

**Fix**: updated all four `@validates` quantize calls in `Position` (quantity, avg_cost) and `OrsoAllocation` (units, contribution_pct) to pass `rounding=ROUND_HALF_UP`. Added 4 new test cases (`test_position_quantity_rounds_half_up`, `test_position_avg_cost_rounds_half_up`, `test_orso_allocation_units_rounds_half_up`, `test_orso_allocation_contribution_pct_rounds_half_up`) asserting that halfway values round up as expected.

**Test results**: `pytest -q` = 238 passed (234 pre-existing + 4 new); `ruff check` clean.

## Commit

Committed (see final message for SHA). Not pushed (per instructions).
