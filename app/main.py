"""Application composition root.

ARCHITECTURAL DECISION -- an application *factory*, not a module-level app.

`create_app()` builds and returns a fully wired FastAPI instance. This is the
composition root: the one place that knows how the layers fit together. Every
other module declares what it needs and receives it, rather than reaching out
to grab a global.

The practical payoff is testability. A module-level `app = FastAPI()` is
process-wide shared state: dependency overrides applied by one test leak into
the next, and test outcomes start depending on collection order. With a
factory, each test constructs an isolated instance and that entire category of
flakiness disappears.

A module-level `app` object is still exported at the bottom of this file
because ASGI servers address applications by import string (`app.main:app`).
That single instance is the process's own composition, built exactly once.
"""

import logging

from fastapi import FastAPI

from app.api.routes import health
from app.core.config import Settings, get_settings


def _configure_logging(settings: Settings) -> None:
    """Align the application's logging with the configured level.

    Uvicorn installs its own handlers for its access and error loggers; this
    only sets the root logger so application modules inherit a consistent
    level. Structured JSON logging is intentionally out of scope here -- it
    belongs with a real log aggregator, and adding a formatter nobody consumes
    would be speculative complexity.
    """
    logging.basicConfig(
        level=settings.LOG_LEVEL.upper(),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and wire a FastAPI application.

    Args:
        settings: Configuration to build against. Defaults to the process
            environment. The parameter exists so tests and scripts can compose
            an application against explicit configuration without mutating
            global state -- dependency injection at the top of the object graph.

    Returns:
        A fully configured FastAPI instance.
    """
    settings = settings or get_settings()
    _configure_logging(settings)

    app = FastAPI(
        title="Lateral Takeaway API",
        description="Backend for a small takeaway platform: restaurants, menus and orders.",
        version="0.1.0",
        # Documentation routes are suppressed in production. Exposing the
        # schema hands an attacker a complete map of the API surface.
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )

    # Routers are registered here and only here. Keeping registration in the
    # composition root means the full route surface is auditable in one place,
    # instead of being discovered by importing modules for their side effects.
    app.include_router(health.router)

    return app


# ASGI entrypoint. Referenced as `app.main:app` by Uvicorn.
app = create_app()
