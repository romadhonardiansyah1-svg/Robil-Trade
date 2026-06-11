# Robil Trade — task runner (Linux/VPS/git-bash).
# Di Windows PowerShell tanpa make, jalankan perintah `uv run ...` di bawah secara langsung
# (tabel padanan ada di README.md).

.PHONY: dev down lint format typecheck test test-unit test-integration migrate ci

dev:            ## Start dev stack (TimescaleDB + Redis)
	docker compose up -d

down:           ## Stop dev stack
	docker compose down

lint:           ## Ruff check + format check
	uv run ruff check src tests migrations
	uv run ruff format --check src tests migrations

format:         ## Auto-format + autofix
	uv run ruff format src tests migrations
	uv run ruff check --fix src tests migrations

typecheck:      ## mypy --strict on src/
	uv run mypy

test-unit:      ## Unit tests only
	uv run pytest tests/unit -q

test-integration: ## Integration tests (needs `make dev` running)
	uv run pytest -m integration -q

test:           ## All tests
	uv run pytest -q

migrate:        ## Apply DB migrations
	uv run alembic upgrade head

ci: lint typecheck test-unit  ## Local CI gate (per PLAN P0-T6)
