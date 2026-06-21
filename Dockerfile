# ============================================================================
# Robil Trade — Multi-stage Dockerfile
# Stage 1: Build (uv install deps)
# Stage 2: Runtime (slim, no build tools)
# Ref: IMPLEMENTATION_PLAN §13
# ============================================================================

# --- Stage 1: Builder ---
FROM python:3.12-slim AS builder

# Install build essentials for native deps (numpy, xgboost, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency resolution.
# Pin to a specific uv release (not :latest) for reproducible builds.
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /bin/

WORKDIR /app

# Copy dependency files first (layer cache).
COPY pyproject.toml uv.lock README.md ./

# Install dependencies (frozen = use lockfile exactly).
RUN uv sync --frozen --no-dev --no-install-project

# Copy application source.
COPY src/ src/
COPY config/ config/
COPY scripts/ scripts/
COPY migrations/ migrations/
COPY alembic.ini ./

# Install the project itself.
RUN uv sync --frozen --no-dev


# --- Stage 2: Runtime ---
FROM python:3.12-slim AS runtime

# Runtime system deps.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user.
RUN useradd --create-home --shell /bin/bash rtrade

WORKDIR /app

# Copy the virtual environment from builder.
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY --from=builder /app/config /app/config
COPY --from=builder /app/scripts /app/scripts
COPY --from=builder /app/migrations /app/migrations
COPY --from=builder /app/alembic.ini /app/alembic.ini
COPY --from=builder /app/pyproject.toml /app/pyproject.toml

# Set PATH to use the venv.
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Create data directories.
RUN mkdir -p /app/data /app/reports /app/logs /app/models && \
    chown -R rtrade:rtrade /app

USER rtrade

# Per-service health checks are defined in docker-compose.prod.yml.
# A global check here would only be valid for the api container.

EXPOSE 8000

# Default: run the scheduler (main entry point).
# Override with CMD in compose for different services.
CMD ["python", "-m", "rtrade.scheduler.main"]
