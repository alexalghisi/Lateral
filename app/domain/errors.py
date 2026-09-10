"""Domain-level exceptions.

ARCHITECTURAL DECISION -- the domain raises domain errors, never HTTPException.

Importing `fastapi.HTTPException` here would be the fastest way to write this
code and the fastest way to ruin the design. It would weld the business rules
to a specific transport: the same rules could no longer be invoked from a
background worker, a CLI command or a scheduled job without dragging an HTTP
framework into a process that serves no HTTP.

Instead the domain raises errors that describe *what went wrong in business
terms*, and a single exception handler at the API boundary translates them into
status codes. The mapping lives in exactly one place, so the correspondence
between a business failure and its HTTP representation is auditable rather than
scattered across dozens of `raise HTTPException(...)` calls.
"""


class DomainError(Exception):
    """Base class for every violation of a business rule.

    A single root allows the API layer to register one handler per *category*
    of failure rather than one per concrete exception, and lets callers
    distinguish "the request was invalid" from "the infrastructure failed"
    with a single `except`.
    """


class ConflictError(DomainError):
    """The requested operation contradicts the current state of a resource.

    Maps to HTTP 409. Distinct from a validation error: the request is
    well-formed and the caller is authorised, but the state of the world makes
    the operation meaningless right now.
    """


class NotFoundError(DomainError):
    """The referenced resource does not exist. Maps to HTTP 404."""


class PermissionDeniedError(DomainError):
    """The caller is authenticated but not allowed to perform this operation.

    Maps to HTTP 403. Deliberately separate from an authentication failure
    (401): conflating "I do not know who you are" with "I know who you are and
    the answer is no" leads clients to retry with the same credentials forever.
    """
