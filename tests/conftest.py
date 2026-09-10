"""Shared pytest fixtures.

ARCHITECTURAL DECISION: the test environment is established *before* any
application module is imported. `Settings` fails loudly when a required
variable is missing -- that strictness is a feature in production, but it means
import order matters in tests. Seeding `os.environ` at module scope, above the
application imports, guarantees a deterministic configuration regardless of
which test file pytest collects first, and regardless of whether a developer
happens to have a `.env` on disk.

`setdefault` is used rather than assignment so that an explicitly exported
variable (for example, pointing the suite at a real database in CI) still wins.
"""

import os
from collections.abc import Iterator

# --- Environment seeding -----------------------------------------------------
# NOTE: these assignments MUST precede the application imports below.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("LOG_LEVEL", "warning")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-used-outside-tests")
os.environ.setdefault("POSTGRES_USER", "lateral")
os.environ.setdefault("POSTGRES_PASSWORD", "lateral")
os.environ.setdefault("POSTGRES_DB", "lateral")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_PORT", "5432")

# These imports intentionally sit below the environment seeding above; the
# application must not be imported until its configuration is in place.
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.main import create_app

# A port that nothing listens on. Connecting fails immediately with a refusal
# rather than hanging until a timeout, which keeps the failure-path test fast.
UNREACHABLE_DATABASE_URL = "postgresql+psycopg://nobody:nobody@127.0.0.1:1/nowhere"


@pytest.fixture(scope="session")
def settings() -> Settings:
    """The application settings as resolved for the test session."""
    return get_settings()


@pytest.fixture()
def client() -> TestClient:
    """An HTTP client bound to a freshly built application instance.

    Built through the `create_app` factory rather than importing a module-level
    singleton. Each test therefore receives an application with independent
    state, which prevents ordering dependencies between tests -- the single
    most common source of suites that pass locally and fail in CI.
    """
    return TestClient(create_app())


@pytest.fixture()
def unreachable_database_client() -> Iterator[TestClient]:
    """A client whose database sessions point at a closed port.

    This is the payoff of injecting the session through a FastAPI dependency
    rather than importing a module-level `SessionLocal` inside route handlers:
    the infrastructure boundary can be substituted from the outside, with no
    monkeypatching of application internals and no test-only branch in
    production code. Dependency inversion, doing real work.
    """
    app = create_app()

    # `pool_pre_ping` is deliberately omitted here: we *want* the connection
    # attempt to happen inside the route so the handler observes the failure.
    engine = create_engine(UNREACHABLE_DATABASE_URL)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
    engine.dispose()
