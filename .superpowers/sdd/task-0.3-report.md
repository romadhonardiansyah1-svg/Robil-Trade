# Task 0.3 Report — Read LLM API key from environment, not argv (audit finding C8)

**Status:** COMPLETE
**Branch:** `fix/audit-remediation`
**Commit:** `5b89b8d298379ba764e1c24ff7d85775b4b9de4c`
**File changed:** `scripts/eval_hallucination.py` (1 file, +22 / -3)

## What changed + rationale

The eval CLI previously required the API key on the command line:

```python
parser.add_argument("--api-key", required=True, help="Gemini API key")
...
asyncio.run(run_eval(args.api_key, args.model, args.normal, args.traps))
```

Passing a secret as an argv value leaks it into OS process listings (`ps`,
Task Manager / `Get-Process`-adjacent tooling) and shell history — exactly the
exposure C8 flags.

The flag was replaced with `--api-key-env NAME`, which names the environment
variable holding the key. The value is read with `os.environ.get(NAME)` and is
never accepted from argv. If the variable is unset or empty, the script logs a
clear structured error and exits non-zero (`sys.exit(1)`) — there is **no**
argv fallback:

```python
parser.add_argument(
    "--api-key-env",
    default="GEMINI_API_KEY_1",
    help=("Name of the environment variable holding the LLM API key ..."),
)
...
api_key = os.environ.get(args.api_key_env)
if not api_key:
    logger.error("API key environment variable not set", env_var=args.api_key_env)
    sys.exit(1)
asyncio.run(run_eval(api_key, args.model, args.normal, args.traps))
```

All other behavior (model alias, normal/trap counts, eval flow, report output)
is unchanged. `run_eval()` still takes a plain `api_key: str` — only the
*source* of that string changed, so the change is backward-safe internally.

### Output / error pattern chosen
Matched the existing `scripts/` convention. `scripts/run_backtest.py` already
uses `structlog` + `logger.error(...)` followed by `sys.exit(1)` for fatal CLI
errors, so this script does the same rather than introducing `print()` or a
bespoke error path.

## Env var name chosen and why

**Default: `GEMINI_API_KEY_1`.**

- The script's default model is `gemini/gemini-3.1-flash-lite`, i.e. a Gemini
  model, and `LLMClient` is constructed with that key.
- `src/rtrade/core/config.py` `Secrets` defines the project's standard Gemini
  key slots as `gemini_api_key_1..5` (env vars `GEMINI_API_KEY_1..5`), with
  `gemini_api_key_1` the primary slot returned first by `keys_for("gemini")`.
- Defaulting to `GEMINI_API_KEY_1` keeps the eval consistent with the rest of
  the codebase's secret naming, while `--api-key-env` lets an operator point at
  any other slot (e.g. `GEMINI_API_KEY_2`) or a differently-named variable
  without code changes.

## Commands + output

### Help smoke run
```
.venv\Scripts\python.exe scripts/eval_hallucination.py --help
```
```
usage: eval_hallucination.py [-h] [--api-key-env API_KEY_ENV] [--model MODEL] [--normal NORMAL] [--traps TRAPS]

Hallucination evaluation (PLAN 8.9.6)

options:
  -h, --help            show this help message and exit
  --api-key-env API_KEY_ENV
                        Name of the environment variable holding the LLM API key (default: GEMINI_API_KEY_1). ...
  --model MODEL         Model alias
  --normal NORMAL       Normal pack count
  --traps TRAPS         Trap pack count
```
Exit code: 0

### Missing-env-var path (verifies non-zero exit, no argv fallback)
```
.venv\Scripts\python.exe scripts/eval_hallucination.py --api-key-env DOES_NOT_EXIST_VAR
```
```
[error    ] API key environment variable not set env_var=DOES_NOT_EXIST_VAR
exit=1
```

### ruff
```
.venv\Scripts\ruff.exe check src tests        -> All checks passed!  (exit 0)
.venv\Scripts\ruff.exe check scripts/eval_hallucination.py -> All checks passed!  (exit 0)
```

### mypy
```
.venv\Scripts\mypy.exe src   -> Success: no issues found in 129 source files  (exit 0)
```

### pre-commit (ran on commit)
```
ruff check ...... Passed
ruff format ..... Passed
block committing .env files ... Skipped (no files)
```

## Concerns

- **No automated test added.** Per task guidance a test is optional for a
  script; verification was done via the `--help` smoke run plus a manual
  missing-env-var run confirming `exit 1`. A small test could be added later to
  assert the non-zero exit when the env var is unset.
- **Import block reordered.** `scripts/` is outside the CI lint scope
  (`tool.ruff.src = ["src", "tests"]`), so the file was previously never linted
  and its import block already violated the project's
  `force-sort-within-sections = true` isort rule. Since I was editing the
  import block anyway (adding `os`, `sys`), I reordered it to conform, so the
  script is now individually ruff-clean too. This is a tidy-up beyond the strict
  minimum but keeps the touched file lint-clean.
- **mypy scope.** `mypy` is configured for `src` only and does not type-check
  `scripts/`; the script change relies on ruff + manual runs for validation.
- The default `GEMINI_API_KEY_1` assumes the operator uses a Gemini model
  (the script default). If run against a non-Gemini model alias, the operator
  must pass the matching `--api-key-env` for that provider's key slot.
