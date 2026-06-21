# Task 6.3 — Infra Hardening (E6 + infra Lows)

Branch: `fix/audit-remediation` · Python 3.12 · Windows/PowerShell
Scope: five infra-hygiene defects. Minimal, correct, well-justified changes. One commit.

## Summary of the 5 fixes

### 1. `scripts/backup_db.sh` — add `pipefail`
- **Change:** replaced `set -e` with `set -euo pipefail` (plus a comment).
- **Rationale:** the script runs `pg_dump ... | gzip > "$BACKUP_FILE"`. Without
  `pipefail`, a failing `pg_dump` is masked by `gzip` exiting 0, so a broken
  backup looks successful. `pipefail` makes the pipeline fail if any stage
  fails; `-u` catches unset-variable bugs.
- **Shell-correctness:** the backup container is `postgres:16.8-alpine`
  (per `docker-compose.prod.yml`) and is invoked as `/bin/sh /backup.sh`.
  Alpine's `/bin/sh` is busybox, which **supports** `set -o pipefail`, so this
  is safe (dash would not — but dash is not the runtime here).
- **Shebang:** `#!/bin/sh` remains the first line.

### 2. `scripts/setup_vps.sh` — remove `curl | sh` RCE for Docker install
- **`set -euo pipefail`:** already present at the top of the script (verified) —
  no change needed there.
- **Change:** the Docker install was `curl -fsSL https://get.docker.com | sh`
  (unverified remote code piped straight into a root shell). Replaced with:
  download the official installer to a `mktemp` file, execute the local copy,
  then remove it. Added a comment explaining the hardening.
- **Approach choice:** target OS is Ubuntu 24.04 / Debian. I chose
  "download-to-file then run" over "apt install docker.io" because the script
  later hard-requires the Docker Compose **v2 plugin**
  (`docker compose version` → `exit 1` if missing). The official get.docker.com
  installer reliably installs `docker-ce` + the compose v2 plugin; switching to
  the distro `docker.io` package would risk breaking that compose-plugin check.
  Download-to-file removes the pipe-to-shell while preserving the exact
  provisioning flow.

### 3. `src/rtrade/monitoring/healthcheck.py` — timeouts on DB & Redis probes
- **Change:** added `timeout_s: float = 5.0` to `HealthChecker.__init__`.
  Extracted the connection logic into `_probe_database()` and `_probe_redis()`
  and wrapped both in `asyncio.wait_for(..., timeout=self._timeout_s)`.
  Added an explicit `except TimeoutError` branch that logs and returns an
  `UNHEALTHY` `CheckResult` with a `"timeout after {N}s"` message.
- **Rationale:** previously a hung DB/Redis backend would make the probe (and
  thus the whole `/health` endpoint) hang indefinitely. Now each probe fails
  fast within a few seconds.
- **Result shape preserved:** `CheckResult` fields (`name`, `status`, `message`,
  `latency_ms`, `details`) unchanged; HEALTHY path returns identical data.
- **mypy strict:** clean (`_probe_database -> str | None`,
  `_probe_redis -> tuple[bool, str]`).

### 4. `.github/workflows/ci.yml` — least-privilege `permissions`
- **Change:** added top-level `permissions: { contents: read }` (with comment).
- **Rationale:** makes `GITHUB_TOKEN` read-only by default. Lint/type/test need
  no write scopes, so nothing breaks. Workflow still runs ruff (`check` +
  `format --check`), mypy (`--strict`), and pytest.

### 5. `Dockerfile` — pin `uv` (no floating `:latest`)
- **Change:** `COPY --from=ghcr.io/astral-sh/uv:latest` →
  `COPY --from=ghcr.io/astral-sh/uv:0.5.11` (with comment).
- **Rationale:** `:latest` makes builds non-reproducible and silently pulls new
  uv majors. Pinned to a specific stable release. The pin pattern
  `ghcr.io/astral-sh/uv:<version>` is the documented form (Astral docs).
  CI uses `astral-sh/setup-uv@v5` (the 0.5.x era), so 0.5.11 matches the
  ecosystem. Build flow unchanged (`uv sync --frozen ...`).

## Tested vs verified-by-inspection

- **Unit-tested (#3 only):** added `TestHealthCheckerTimeouts` in
  `tests/unit/test_alerts.py` with two async tests. Each builds a
  `HealthChecker(timeout_s=0.1)`, monkeypatches `_probe_database` / `_probe_redis`
  with a coroutine that `await asyncio.sleep(10)`, then asserts the check returns
  `HealthStatus.UNHEALTHY` and that wall-clock elapsed `< 2.0s` (i.e. bounded by
  the timeout, not the 10s sleep). This proves the probe does not hang.
- **Verified by inspection + suite-stays-green (#1, #2, #4, #5):** these are
  shell/CI/Docker config and cannot be meaningfully unit-tested in this Python
  repo. Verified by reading the files, confirming runtime assumptions (alpine
  busybox sh for `pipefail`; compose-v2-plugin requirement for the Docker
  install approach; valid uv tag), and confirming ruff/mypy/pytest remain clean.

## Verification log

- **RED:** `pytest -q tests/unit/test_alerts.py::TestHealthCheckerTimeouts`
  → 2 failed (`TypeError: ... unexpected keyword argument 'timeout_s'`).
- **GREEN:** after implementing → 2 passed.
- **Full suite:** `.venv\Scripts\pytest.exe -q` → all pass (only pre-existing
  DB/Redis integration tests skipped locally; 1 unrelated Starlette deprecation
  warning).
- **ruff:** `.venv\Scripts\ruff.exe check src tests` → All checks passed.
  `ruff format --check src tests` → all formatted.
- **mypy:** `.venv\Scripts\mypy.exe src` → Success: no issues found in 129 source files.

## Concerns / notes

- `setup_vps.sh` already had `set -euo pipefail`; only the Docker-install line
  was hardened. No behavioral change to the rest of the provisioning flow.
- uv `0.5.11` is a deliberate, reasonable pin matching `setup-uv@v5`. If the
  team prefers a newer line, bump the tag — the mechanism is the same.
- Unrelated working-tree changes (`.superpowers/sdd/progress.md`,
  `task-6.2-report.md`) were left untouched and NOT included in this commit.
