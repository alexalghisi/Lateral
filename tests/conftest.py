"""Shared pytest fixtures and the integration test harness.

ARCHITECTURAL DECISION 1 -- the environment is seeded before any application
module is imported.

`Settings` fails loudly when a required variable is missing. That strictness is
a feature in production, but it means import order matters in tests. Seeding
`os.environ` at module scope, above the application imports, guarantees a
deterministic configuration regardless of which test file pytest collects
first, and regardless of whether a developer happens to have a `.env` on disk.

ARCHITECTURAL DECISION 2 -- tests run against a real PostgreSQL, in a
dedicated database, never SQLite.

Substituting SQLite would make the suite trivially portable and quietly
worthless for the things this application actually depends on. SQLite has no
native ENUM type, does not enforce foreign keys unless asked, and has different
transactional and constraint semantics. A suite that passes on SQLite proves
nothing about the CHECK constraints, the `ondelete="RESTRICT"` rules, or the
enum columns that carry this system's integrity guarantees. Testing against the
same engine that runs in production is the only way those assertions mean
anything.

ARCHITECTURAL DECISION 3 -- every test runs inside a transaction that is
rolled back afterwards.

The alternative, truncating tables between tests, is slower and leaves
ordering-dependent residue when it misses a table. Wrapping each test in a
transaction and discarding it gives perfect isolation at close to zero cost:
the database never actually writes anything durable. Tests can therefore run
in any order, and a failing test cannot contaminate the next one.
"""

import os
from collections.abc import Iterator
from pathlib import Path

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

# Redirect the suite to a dedicated database, by ASSIGNMENT rather than
# `setdefault`. This one must override whatever the environment says, and that
# is the entire point: it makes it structurally impossible for a test run to
# drop tables in the development or -- catastrophically -- the production
# database. A safety property should not depend on remembering to export the
# right variable before running pytest.
os.environ["POSTGRES_DB"] = f"{os.environ['POSTGRES_DB']}_test"

# These imports intentionally sit below the environment seeding above; the
# application must not be imported until its configuration is in place.
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.main import create_app

# Repository root, resolved from this file rather than the working directory so
# the harness behaves identically however pytest is invoked.
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# A port that nothing listens on. Connecting fails immediately with a refusal
# rather than hanging until a timeout, which keeps the failure-path test fast.
UNREACHABLE_DATABASE_URL = "postgresql+psycopg://nobody:nobody@127.0.0.1:1/nowhere"


def _create_test_database_if_missing(settings: Settings) -> None:
    """Create the dedicated test database when it does not yet exist.

    CREATE DATABASE cannot run inside a transaction block, hence the AUTOCOMMIT
    isolation level, and it cannot be parameterised, hence the interpolated
    identifier. The value is derived from configuration rather than from any
    request input, and is quoted, so this is not an injection vector.

    The connection is made to the `postgres` maintenance database because one
    cannot connect to a database in order to create it.
    """
    maintenance_url = (
        f"postgresql+psycopg://"
        f"{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD.get_secret_value()}"
        f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/postgres"
    )

    engine = create_engine(maintenance_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            already_exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": settings.POSTGRES_DB},
            ).scalar()

            if not already_exists:
                connection.execute(text(f'CREATE DATABASE "{settings.POSTGRES_DB}"'))
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def settings() -> Settings:
    """The application settings as resolved for the test session."""
    return get_settings()


@pytest.fixture(scope="session")
def db_engine(settings: Settings) -> Iterator[Engine]:
    """Session-wide engine bound to a freshly migrated test database.

    The schema is built by running the MIGRATIONS, not by
    `Base.metadata.create_all()`. This is deliberate and it is the more
    valuable of the two options by a wide margin: it means the migration chain
    is exercised on every single test run. With `create_all`, the schema under
    test is derived from the models, the migrations are never executed, and the
    first time anyone discovers a broken migration is in production.

    A side effect worth stating: because the whole chain is applied here, a
    migration that fails to apply cleanly fails the entire suite immediately.
    """
    _create_test_database_if_missing(settings)

    alembic_config = Config(str(PROJECT_ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))

    # Bring the test database to the current head. env.py resolves the URL from
    # the same Settings object, which now points at the *_test database.
    command.upgrade(alembic_config, "head")

    engine = create_engine(settings.database_url, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(db_engine: Engine) -> Iterator[Session]:
    """A session whose writes are discarded when the test ends.

    The mechanism: an outer transaction is opened on a dedicated connection and
    the session is bound to that connection rather than to the engine. Whatever
    the test does -- including calls to `session.commit()` deep inside a
    service -- happens within that outer transaction, and rolling it back at
    the end returns the database to its exact prior state.

    `join_transaction_mode="create_savepoint"` is what makes committing service
    code work here. Without it, the first `commit()` inside a service would end
    the outer transaction and subsequent statements would run outside any
    transaction, silently defeating the rollback. With it, each commit resolves
    to a SAVEPOINT release, so the service behaves exactly as it does in
    production while the outer transaction retains the power to undo it all.
    """
    connection = db_engine.connect()
    transaction = connection.begin()

    session_factory = sessionmaker(
        bind=connection,
        autoflush=False,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    session = session_factory()

    try:
        yield session
    finally:
        session.close()
        # Rollback rather than commit, unconditionally -- including when the
        # test failed. Nothing a test writes is allowed to outlive it.
        transaction.rollback()
        connection.close()


@pytest.fixture()
def client(db_session: Session) -> Iterator[TestClient]:
    """An HTTP client whose requests share the test's transaction.

    The `get_db` override is the crux. Without it, the application would open
    its own connection from its own pool, its writes would land in a separate
    transaction, and the test's rollback would not touch them -- data would
    leak between tests, and assertions made through `db_session` would not see
    what the request just wrote.

    Overriding the dependency puts the request handler and the test on the same
    connection: the test can arrange data through `db_session`, act through
    HTTP, and assert against `db_session` again, all inside one transaction.
    This is only possible because sessions are injected rather than imported.

    Built through the `create_app` factory rather than importing a module-level
    singleton, so each test gets an application with independent state and no
    override can leak into the next test.
    """
    app = create_app()

    def override_get_db() -> Iterator[Session]:
        # Deliberately does NOT close the session: its lifecycle belongs to the
        # `db_session` fixture. Closing it here would detach objects the test
        # still needs to assert against after the request completes.
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


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
