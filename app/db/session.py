"""Engine and session lifecycle.

ARCHITECTURAL DECISION 1 -- synchronous SQLAlchemy, not async.

This is a considered choice, not a shortcut. The workload is ordinary
transactional CRUD against one database; there is no long-tail fan-out of
concurrent I/O per request that async would meaningfully improve. What async
*would* add is real cost: greenlet-mediated lazy loading, a sharply worse
debugging experience, and a standing invitation to block the event loop with an
innocent-looking synchronous call.

FastAPI runs `def` endpoints in a worker threadpool, so blocking database calls
never stall the event loop. The result is throughput that is entirely adequate
for this domain with materially lower defect risk. Should profiling later prove
otherwise, the migration path is contained precisely because sessions are
injected rather than imported: swap `sessionmaker` for `async_sessionmaker`
behind the same `get_db` seam.

ARCHITECTURAL DECISION 2 -- the dependency does not commit.

`get_db` yields a session and guarantees cleanup; it never commits. Committing
here would make the *transport layer* the transaction boundary, which quietly
destroys atomicity: a request that performs two writes would have them
committed independently, and a failure between them leaves the database in a
state the domain considers impossible.

The transaction boundary belongs to the service layer, which is the only layer
that knows when a business operation is complete. The dependency's rollback is
therefore a safety net for the unhappy path, not the normal control flow.
"""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return the process-wide SQLAlchemy engine.

    Built lazily on first use rather than at import time. Importing this module
    therefore has no side effects, which matters for Alembic and for any tool
    that imports application code without intending to talk to a database.

    The engine owns the connection pool and is deliberately a singleton: one
    pool per process is the point. Creating engines per request would defeat
    pooling entirely and exhaust PostgreSQL's connection limit under load.
    """
    settings = get_settings()

    return create_engine(
        settings.database_url,
        # Verify a pooled connection with a cheap round-trip before handing it
        # out. Without this, connections killed by a database restart or an
        # idle-timeout proxy are returned from the pool and fail on first use,
        # producing mysterious errors after every deployment of the database.
        pool_pre_ping=True,
        # Recycle below the typical infrastructure idle timeout so the pool
        # retires connections before something upstream severs them.
        pool_recycle=1800,
        pool_size=5,
        max_overflow=10,
        # Echo SQL only when explicitly debugging. Query logs at INFO in
        # production are both a performance cost and a PII exposure risk.
        echo=settings.LOG_LEVEL.lower() == "debug",
    )


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    """Return the configured session factory."""
    return sessionmaker(
        bind=get_engine(),
        # autoflush=False: flushes happen where the code asks for them. Implicit
        # flushes mid-query make the order of emitted SQL depend on read
        # patterns, which turns integrity errors into a debugging exercise.
        autoflush=False,
        # expire_on_commit=False: attributes stay readable after commit. With
        # the default, serialising a just-committed ORM object into a response
        # triggers a fresh SELECT per attribute -- and raises outright if the
        # session has already been closed by the dependency's teardown.
        expire_on_commit=False,
    )


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session.

    One session per request, closed unconditionally. This function is the seam
    that makes the persistence layer substitutable: tests override it to inject
    a transactional session bound to a rolled-back connection, and no
    application code changes to accommodate them.
    """
    session = get_session_factory()()
    try:
        yield session
    except Exception:
        # The service layer owns commits, but if an exception escapes we must
        # not return a connection to the pool mid-transaction. Rolling back
        # here prevents one failed request from poisoning the next one that
        # borrows the same connection.
        session.rollback()
        raise
    finally:
        session.close()
