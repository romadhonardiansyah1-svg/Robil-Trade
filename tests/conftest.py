import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def config_dir(repo_root: Path) -> Path:
    return repo_root / "config"


@pytest.fixture(scope="session")
def test_database_url() -> str:
    return os.environ.get(
        "RTRADE_TEST_DATABASE_URL",
        "postgresql+asyncpg://rtrade:rtrade@localhost:5432/rtrade",
    )


@pytest.fixture(scope="session")
def test_redis_url() -> str:
    return os.environ.get("RTRADE_TEST_REDIS_URL", "redis://localhost:6379/0")
