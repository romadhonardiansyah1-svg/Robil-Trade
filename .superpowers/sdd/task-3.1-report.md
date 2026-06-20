# Task 3.1 — Serialize audit hash-chain appends with an advisory lock (D1)

Branch: `fix/audit-remediation`

## The fork mechanism

`AuditRepo.add` builds a tamper-evident hash chain: it reads the latest audit
row (`SELECT ... ORDER BY id DESC LIMIT 1`), takes that row's `_chain.row_hash`
as `prev_hash`, computes a new `row_hash = SHA256(prev_hash, stage, ok,
signal_id, detail)`, and inserts a new row carrying `{prev_hash, row_hash}`.

The read and the insert were not serialized. The scheduler runs overlapping
scans plus calendar/paper/health jobs on the same event loop against the same
DB. Two appends interleaving like this:

```
task A: SELECT latest -> sees row N (prev_hash = H_N)
task B: SELECT latest -> sees row N (prev_hash = H_N)   # SAME parent
task A: INSERT row N+1 with prev_hash = H_N
task B: INSERT row N+2 with prev_hash = H_N             # FORK
```

…produce two rows that both chain off `H_N`. The chain forks. `verify_chain`
walks rows in id order expecting each `stored_prev == previous row_hash`; the
second of the two forked rows has `prev_hash = H_N` instead of the first forked
row's `row_hash`, so verification reports a break. Consequence:
`audit_chain_verify_job` raises a false CRITICAL "tampering" alert, and a real
tamper becomes indistinguishable from a benign concurrency fork.

## The fix — transaction-scoped advisory lock

Before the read-then-insert, `AuditRepo.add` now executes:

```python
await self._session.execute(
    text("SELECT pg_advisory_xact_lock(:k)"),
    {"k": self._CHAIN_LOCK_KEY},
)
```

`pg_advisory_xact_lock` is a Postgres transaction-scoped advisory lock. Only one
appender can hold the key at a time; a second appender blocks at this statement
until the holder's transaction ends.

### Why xact-scoped + the commit boundary makes it correct

The lock auto-releases at **COMMIT/ROLLBACK**, so it is held for the entire
remainder of the caller's transaction — through the latest-row SELECT, the
INSERT, and right up to COMMIT. This is exactly what we need: the next appender
must not be allowed to run its SELECT until the previous appender's INSERT is
**committed and visible**. If the lock released before commit, the next writer
could read a stale head and still fork. Because the lock spans read → insert →
commit, the critical section is genuinely serialized and the chain stays linear.

This relies on the caller doing read + insert + COMMIT in the **same
transaction**. The scan pipeline does exactly this — each `AuditRepo.add` is
followed (per `async with session_factory()` block) by `await session.commit()`
on the same session (see `src/rtrade/pipeline/scan.py`). The commit boundary is
documented in the code comment so future callers preserve it. Multiple
`add` calls inside one transaction simply re-acquire the same key they already
hold (advisory locks are re-entrant within a session), so single-writer
behaviour is unchanged.

### Lock key choice

`_CHAIN_LOCK_KEY = 0x52545241` — the ASCII bytes of `"RTRA"`
(R=0x52, T=0x54, R=0x52, A=0x41) read as a signed 32-bit int. A small, fixed,
documented constant dedicated to the rtrade audit chain. `pg_advisory_xact_lock`
takes a bigint, so the value is comfortably in range, and the chain is the only
consumer of this key so there is no collision risk. The choice is documented in
a comment on the constant.

### detail-dict mutation decision (D5 Low)

The old code did `detail["_chain"] = chain_entry`, mutating the caller's input
dict in place. The fix was trivial, so it was done here: the stored dict is now
built non-destructively with `stored_detail = {**detail, "_chain": chain_entry}`
and only `stored_detail` is persisted. The caller's `detail` is left untouched.
A unit test (`test_add_does_not_mutate_caller_detail`) locks this in.

`text()` with a bound parameter (`:k`) is used — no string interpolation.

## Tests

### Unit (no DB) — proves lock-before-read ordering
`tests/unit/test_audit_chain.py::TestAuditRepoSerialization`
- `test_advisory_lock_issued_before_latest_select`: a recording fake
  `AsyncSession` captures the order of `execute()` calls. Asserts the first
  statement contains `pg_advisory_xact_lock` and that its index strictly
  precedes the `signal_audits` latest-row SELECT. This proves the serialization
  is wired without a live DB.
- `test_add_does_not_mutate_caller_detail`: asserts the caller's `detail` dict
  is not mutated (no injected `_chain`).

### Integration (`@pytest.mark.integration`) — true concurrency
`tests/integration/test_audit_chain_concurrency.py::test_concurrent_appends_form_linear_chain`
- Two `AuditRepo.add` calls run via `asyncio.gather`, each on its **own**
  session/connection (a true concurrent writer — asyncpg sessions are not
  concurrency-safe, so separate connections are required).
- Asserts the two new rows do **not** share a `prev_hash` (no fork) and that the
  later row chains off the earlier (`second.prev_hash == first.row_hash`,
  linear).
- Self-skips when no DB is reachable; cleans up its rows in a `finally` block.
  Not run in the default suite (deselected via `-m "not integration"`).

## RED / GREEN / suite / lint

- RED: new unit tests failed as expected — `pg_advisory_xact_lock` not in first
  statement; caller `detail` mutated with `_chain`.
- GREEN: after adding the advisory lock and non-destructive detail build, the
  audit unit tests pass (7 passed).
- Default suite: `.venv\Scripts\pytest.exe -q tests -m "not integration"` →
  all passed (800 tests, exit 0).
- Integration test collects and skips cleanly with no DB.
- `.venv\Scripts\ruff.exe check src tests` → All checks passed!
- `.venv\Scripts\mypy.exe src` → Success: no issues found in 129 source files.

## Concerns

- The serialization correctness depends on callers committing in the same
  transaction as the `add`. All current callers do; this is documented in code.
  If a future caller batches many appends without committing, the lock is held
  for the whole batch (correct but longer-held). This is acceptable given audit
  writes are small and infrequent.
- The true-concurrency assertion is integration-gated (needs a live Postgres);
  it was not executed in this environment (no DB reachable), but it is a real
  concurrency test, not a faked sequential pass.
