# Robil Trade — task runner (Linux/VPS/git-bash).
# Di Windows PowerShell tanpa make, jalankan perintah `uv run ...` di bawah secara langsung
# (tabel padanan ada di README.md).

.PHONY: dev down lint format typecheck test test-unit test-integration migrate ci \
        prod prod-down prod-logs prod-ps deploy backup smoke

# === Development ===

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

# === Production ===

COMPOSE_PROD = docker compose -f docker-compose.yml -f docker-compose.prod.yml

prod:           ## Start production stack
	$(COMPOSE_PROD) up -d --build

prod-down:      ## Stop production stack
	$(COMPOSE_PROD) down

prod-logs:      ## Tail production logs
	$(COMPOSE_PROD) logs --tail=100 -f

prod-ps:        ## Show production container status
	$(COMPOSE_PROD) ps

prod-migrate:   ## Run migrations in production
	$(COMPOSE_PROD) exec app python -m alembic upgrade head

prod-health:    ## Check production health
	$(COMPOSE_PROD) exec app curl -sf http://localhost:8000/health | python3 -m json.tool

deploy:         ## Full deploy: build + up + migrate
	$(COMPOSE_PROD) up -d --build
	$(COMPOSE_PROD) exec app python -m alembic upgrade head
	$(COMPOSE_PROD) ps
	@echo "Deploy complete. Run 'make prod-health' to verify."

backup:         ## Trigger manual backup
	$(COMPOSE_PROD) exec backup /bin/sh /backup.sh

backup-list:    ## List available backups
	$(COMPOSE_PROD) exec backup ls -lh /backups/

smoke:          ## Live smoke test (1 call per provider + LLM)
	uv run python -c "print('TODO: implement smoke test script')"

# === Utilities ===

secret-gen:     ## Generate random secrets for .env
	@echo "RTRADE_DB_PASSWORD=$$(openssl rand -hex 24)"
	@echo "LITELLM_MASTER_KEY=$$(openssl rand -hex 32)"
	@echo "API_AUTH_TOKEN=$$(openssl rand -hex 32)"

secret-check:   ## Check for leaked secrets in git history
	@git log -p --all -- '*.env' '*.env.*' | head -5 && echo "WARNING: found .env in history" || echo "OK: no .env in git history"
	@git log -p --all | grep -i "api_key\|secret\|token" | head -10 || echo "OK: no obvious secrets found"
