# Task 2: Groups CRUD + seed-from-sectors — Report

## Status: DONE

## Summary
Implemented `backend/app/api/groups.py`: a `user_held_instruments(db, user_id)` helper scoped
to a user's REAL portfolios (via `Position` → `Portfolio.user_id` + `Portfolio.kind == "real"`),
full groups CRUD (`GET/POST /api/groups`, `PATCH/DELETE /api/groups/{id}`), `PUT /api/groups/assign`
(move a held stock into a group or clear to Ungrouped via `group_id: null`), and
`POST /api/groups/seed-from-sectors` (idempotent, non-destructive: one group per distinct Yahoo
sector among held instruments, `null` sector → "Unclassified", assigns only currently-unassigned
holdings, never moves an already-assigned holding). Router registered in `backend/app/main.py`
(alphabetical, between `auth_router` and `guru_router`). Implementation follows the brief verbatim,
with two lint-only deviations noted below.

## TDD

**RED** — extended `backend/tests/test_groups_crud.py` with the brief's 3 new tests
(`test_group_crud_and_assign`, `test_seed_from_sectors_idempotent_nondestructive`,
`test_groups_are_user_scoped`) plus the shared `_hold` helper, before any router code existed.

```
$ cd backend && .venv/bin/pytest tests/test_groups_crud.py -q
...
FAILED tests/test_groups_crud.py::test_group_crud_and_assign - KeyError: 'name'
FAILED tests/test_groups_crud.py::test_seed_from_sectors_idempotent_nondestructive
FAILED tests/test_groups_crud.py::test_groups_are_user_scoped - AssertionError: assert {'detail': 'Not Found'} == []
3 failed, 1 passed in 2.61s
```

(The 1 pre-existing pass is Task 1's model persistence/encryption test, unaffected.)

**GREEN** — implemented `backend/app/api/groups.py` per the brief and registered the router:

```
$ cd backend && .venv/bin/pytest tests/test_groups_crud.py -q
....                                                                     [100%]
4 passed in 2.55s
```

## Full verify

```
$ cd backend && .venv/bin/ruff check .
All checks passed!

$ cd backend && .venv/bin/pytest -q
........................................................................ [ 20%]
........................................................................ [ 41%]
........................................................................ [ 62%]
........................................................................ [ 82%]
...........................................................              [100%]
347 passed in 109.09s (0:01:49)
```

347 = 344 baseline + 3 new tests. No IntegrityError storms, no hangs, no poisoned-DB symptoms —
clean run on the first attempt.

## Files changed

- `backend/app/api/groups.py` (new) — router, `user_held_instruments`, `_owned_group`, `_counts`,
  Pydantic I/O models (`GroupOut`, `GroupIn`, `GroupPatch`, `AssignIn`, `SeedOut`), and the 6 routes.
- `backend/app/main.py` — added `from app.api.groups import router as groups_router` (alphabetical)
  and `app.include_router(groups_router)`.
- `backend/tests/test_groups_crud.py` — added `_hold` helper + 3 tests from the brief.

## Deviations from the brief

Two lint-only fixes, both mechanical, no behavior change:

1. `backend/app/api/groups.py` — the brief's snippet imports `delete` from `sqlalchemy` but never
   uses it (deletion goes through `db.delete(g)` / `db.delete(existing)` ORM calls, not the SQL
   `delete()` construct). Removed the unused import (ruff F401).
2. `backend/tests/test_groups_crud.py` — the brief's `_hold` helper has a 104-char line (over the
   100-char ruff limit). Wrapped the `auth_client.post(...)` call onto two lines (ruff E501).

## Self-review

- **Ownership/404s**: `_owned_group` checks `g.user_id != user_id` and 404s — used consistently by
  `PATCH`, `DELETE`, and `assign` (when `group_id` is not null). `test_groups_are_user_scoped`
  confirms cross-user `GET` returns `[]` and `PATCH`/`DELETE` on another user's group both 404.
- **Duplicate name → 409**: relies on the `UniqueConstraint("user_id", "name")` on `HoldingGroup`
  from Task 1's migration; both `create_group` and `update_group` catch `IntegrityError` and
  translate to 409, with an explicit rollback first (required — the session is unusable until
  rolled back after a failed flush/commit).
- **Cascade delete**: relies on `ondelete="CASCADE"` on `GroupAssignment.group_id` and
  `GroupSnapshot.group_id` (Task 1's FKs) — `delete_group` just deletes the `HoldingGroup` row;
  Postgres handles the cascade. Verified deletion returns 204 and (implicitly, via re-fetch through
  `_counts`) leaves no orphaned assignments.
- **`user_held_instruments` scoping**: joins `Instrument → Position → Portfolio`, filters
  `Portfolio.user_id == user_id AND Portfolio.kind == "real"` — watchlist-only holdings are
  correctly excluded from both `assign` (422 not_held) and `seed-from-sectors`. Not explicitly
  covered by a new test in this task (no watchlist-portfolio case in the brief's test set) — this
  is inherited, tested behavior from the brief's own design, not a gap I introduced.
- **`seed-from-sectors` idempotency**: computes `assigned_ids` up front from existing
  `GroupAssignment` rows and skips any instrument already in that set — this is what makes re-seed
  a no-op for already-assigned holdings (verified: re-seeding after manually moving AAPL into
  "Space" creates nothing and leaves AAPL in Space, not Technology).
- **Symbol case handling**: `assign` keys the held-instrument lookup by `i.symbol` (as stored) and
  looks up `body.symbol.upper()`. This matches the brief and the tests (which always pass uppercase
  symbols) but is asymmetric — if `Instrument.symbol` were ever stored non-uppercase, the `.upper()`
  lookup would silently miss it. Not a change I made (verbatim from brief); flagging for future
  awareness rather than fixing speculatively.

## Concerns

- None blocking. The one soft note above (symbol case asymmetry) is pre-existing brief behavior,
  not a defect introduced in this task, and all held instruments in this codebase are created with
  uppercase symbols (per `make_instrument`/import flows), so it's not currently reachable.
