"""Translation of domain errors into HTTP responses.

ARCHITECTURAL DECISION -- one central mapping, registered on the application.

Services and domain code raise errors that describe what went wrong in business
terms: `InvalidOrderTransition`, `NotFoundError`, `PermissionDeniedError`. None
of them knows what an HTTP status code is. This module is the single place
where that vocabulary is translated.

The alternative -- `raise HTTPException(status_code=409, ...)` scattered through
the service layer -- looks more direct and costs more:

*   It welds business logic to a transport. The same service could no longer be
    called from a worker or a CLI without importing FastAPI.
*   It scatters the mapping. When two endpoints disagree about whether a
    missing restaurant is 404 or 400, there is no single place to look, and no
    way to be sure you found every case.
*   It makes tests dependent on HTTP. Asserting that ordering an unavailable
    item fails should not require an HTTP client.

Registering handlers here keeps the API contract auditable in one screen.
"""

import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.core.security import InvalidTokenError
from app.domain.errors import (
    ConflictError,
    DomainError,
    NotFoundError,
    PermissionDeniedError,
)
from app.services.auth import AuthenticationFailedError

logger = logging.getLogger(__name__)


def _problem(status_code: int, detail: str) -> JSONResponse:
    """Build an error body.

    The `detail` key matches the shape FastAPI already uses for HTTPException
    and validation failures. Consistency matters more than elegance here: a
    client should parse one error format, not two.
    """
    return JSONResponse(status_code=status_code, content={"detail": detail})


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the domain-to-HTTP error mapping to an application.

    Handlers are registered most-specific first. FastAPI dispatches on the
    exception class and walks the MRO, so `NotFoundError` must be registered
    alongside its base `DomainError` for the specific mapping to win over the
    catch-all.
    """

    @app.exception_handler(InvalidTokenError)
    def _handle_invalid_token(_: Request, exc: InvalidTokenError) -> JSONResponse:
        """401, with the WWW-Authenticate header the HTTP specification requires.

        Omitting that header is a common and consequential oversight: it is how
        a client learns *how* to authenticate, and standard HTTP tooling relies
        on it to decide whether retrying with credentials is worthwhile.
        """
        response = _problem(status.HTTP_401_UNAUTHORIZED, str(exc))
        response.headers["WWW-Authenticate"] = "Bearer"
        return response

    @app.exception_handler(AuthenticationFailedError)
    def _handle_authentication_failed(_: Request, exc: AuthenticationFailedError) -> JSONResponse:
        """401 for rejected credentials at login.

        Shares its shape with the invalid-token handler because both mean the
        same thing to a client: you are not authenticated, and here is how to
        become authenticated. The message is identical for every underlying
        cause -- unknown email, wrong password, deactivated account -- so the
        endpoint cannot be used to enumerate which addresses hold accounts.
        """
        response = _problem(status.HTTP_401_UNAUTHORIZED, str(exc))
        response.headers["WWW-Authenticate"] = "Bearer"
        return response

    @app.exception_handler(PermissionDeniedError)
    def _handle_permission_denied(_: Request, exc: PermissionDeniedError) -> JSONResponse:
        """403 -- authenticated, but not allowed.

        Kept distinct from 401. Conflating them leads clients to retry with the
        same credentials indefinitely against an endpoint that will never
        accept them.
        """
        return _problem(status.HTTP_403_FORBIDDEN, str(exc))

    @app.exception_handler(NotFoundError)
    def _handle_not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return _problem(status.HTTP_404_NOT_FOUND, str(exc))

    @app.exception_handler(ConflictError)
    def _handle_conflict(_: Request, exc: ConflictError) -> JSONResponse:
        """409 -- the request is valid but contradicts current state.

        This is where `InvalidOrderTransition` lands. 409 rather than 400
        because nothing about the request is malformed: the same request would
        have succeeded a moment earlier, and may succeed again later.
        """
        return _problem(status.HTTP_409_CONFLICT, str(exc))

    @app.exception_handler(DomainError)
    def _handle_domain_error(_: Request, exc: DomainError) -> JSONResponse:
        """Catch-all for domain errors with no specific mapping.

        Logged at exception level because reaching this handler means a new
        error type was introduced without deciding on its HTTP representation.
        The client gets a sane 400 rather than a 500, but the log entry is the
        signal that this mapping needs extending.
        """
        logger.exception("Unmapped domain error: %s", type(exc).__name__)
        return _problem(status.HTTP_400_BAD_REQUEST, str(exc))
