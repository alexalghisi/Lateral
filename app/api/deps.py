"""Reusable FastAPI dependency aliases.

ARCHITECTURAL DECISION -- `Annotated` aliases instead of `Depends()` defaults.

The historic FastAPI idiom, `session: Session = Depends(get_db)`, has two real
problems beyond the lint warning it provokes:

1.  It puts a *call* in a default argument. Outside FastAPI's introspection the
    function has a nonsensical signature, and static analysers rightly flag it.
2.  It cannot be reused. Every route repeats the same wiring, so changing how
    sessions are obtained means editing every endpoint that takes one.

`Annotated` moves the dependency into the type, which makes it a named,
importable, single-source-of-truth alias. Routes then read as plain typed
Python -- `session: DbSession` -- and the wiring lives in exactly one place.
This is the DRY principle applied to dependency declarations.

This module belongs to the API layer on purpose: it is the seam where FastAPI's
vocabulary meets the framework-agnostic layers beneath it. `app.db` must stay
free of FastAPI imports so that repositories and services remain usable from
scripts, workers and migrations that have no HTTP context at all.
"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.session import get_db

# A request-scoped SQLAlchemy session. Opened before the handler runs and closed
# afterwards, whatever the outcome. Remember that it does NOT commit -- the
# transaction boundary belongs to the service layer (see app/db/session.py).
DbSession = Annotated[Session, Depends(get_db)]
