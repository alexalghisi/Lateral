"""Authorisation roles.

Lives in the domain layer, alongside `OrderStatus`, for the same reason: who is
allowed to do what is a business rule, not a storage detail.

The placement is load-bearing rather than cosmetic. `app.core.security` needs
this enum to put a role into a token and to validate one coming back out. If
the definition lived on the ORM model, importing it would drag SQLAlchemy into
the security layer, and the token-signing code -- some of the most
security-sensitive code in the system -- could no longer be tested or reused
without a database. Keeping it here inverts that dependency: persistence
imports the domain, never the reverse.
"""

from enum import StrEnum


class UserRole(StrEnum):
    """The kinds of actor the system recognises.

    Deliberately a closed set of two rather than a general permission system.
    The specification distinguishes exactly two actors: customers who browse
    and order, and internal staff who manage menus and advance order statuses.
    Building a role/permission/grant framework for two roles is speculative
    generality -- it multiplies the surface area that must be secured and
    tested in exchange for flexibility nobody has asked for.

    If per-permission granularity is ever required, this enum becomes a foreign
    key to a roles table and nothing outside the authorisation dependency
    changes, because routes declare the role they require rather than
    inspecting user objects directly.
    """

    CUSTOMER = "customer"
    ADMIN = "admin"
