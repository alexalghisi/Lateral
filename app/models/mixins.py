"""Reusable model mixins.

DRY applied to schema definition: identity and audit timestamps are needed by
every table, and repeating those column definitions five times invites the
fifth one to differ subtly from the other four.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column


class IdentityMixin:
    """A surrogate primary key.

    `BigInteger` rather than `Integer`. A 32-bit key exhausts at roughly 2.1
    billion rows, which sounds impossibly far away until an `order_items`
    table on a busy platform gets there -- and migrating a primary key type on
    a large live table is genuinely painful. The cost of 8 bytes now is
    negligible; the cost of the migration later is not.

    A surrogate key is used rather than a natural one (such as email) because
    natural keys change. People change their email address, restaurants get
    renamed, and a changing primary key means cascading updates through every
    referencing row.
    """

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)


class TimestampMixin:
    """Creation and modification timestamps, maintained by the database.

    ARCHITECTURAL DECISION -- server-side defaults, not Python-side ones.

    `default=datetime.utcnow` would stamp rows with the *application server's*
    clock. With several API containers, those clocks drift, and rows written
    seconds apart can carry timestamps in the wrong order. Ordering by
    `created_at` then returns a sequence that never happened, which is
    especially unpleasant when reconstructing an order's history during a
    dispute. `func.now()` delegates to PostgreSQL, giving one authoritative
    clock for all writers regardless of how many containers are running.

    `timezone=True` maps to TIMESTAMPTZ. PostgreSQL normalises to UTC on write
    and returns an aware datetime, which removes the entire class of bug where
    a naive timestamp is interpreted in the wrong zone -- and removes the
    twice-yearly ambiguity that daylight saving introduces into naive columns.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        # `onupdate` is applied by SQLAlchemy on ORM-issued UPDATEs. A database
        # trigger would also cover writes made directly in psql; that is
        # deliberately out of scope here, since all writes in this system go
        # through the ORM.
        onupdate=func.now(),
    )
