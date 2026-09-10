"""Reusable FastAPI dependency aliases, including authentication and RBAC.

ARCHITECTURAL DECISION -- `Annotated` aliases instead of `Depends()` defaults.

The historic FastAPI idiom, `session: Session = Depends(get_db)`, has two real
problems beyond the lint warning it provokes:

1.  It puts a *call* in a default argument. Outside FastAPI's introspection the
    function has a nonsensical signature, and static analysers rightly flag it.
2.  It cannot be reused. Every route repeats the same wiring, so changing how
    sessions are obtained means editing every endpoint that takes one.

`Annotated` moves the dependency into the type, which makes it a named,
importable, single-source-of-truth alias. Routes then read as plain typed
Python -- `session: DbSession`, `user: CurrentAdmin` -- and the wiring lives in
exactly one place. DRY applied to dependency declarations.

This module belongs to the API layer on purpose: it is the seam where FastAPI's
vocabulary meets the framework-agnostic layers beneath it. `app.db` and
`app.domain` must stay free of FastAPI imports so that repositories, services
and business rules remain usable from scripts, workers and migrations that have
no HTTP context at all.
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import InvalidTokenError, decode_access_token
from app.db.session import get_db
from app.domain.errors import PermissionDeniedError
from app.domain.roles import UserRole
from app.models.user import User

# A request-scoped SQLAlchemy session. Opened before the handler runs and closed
# afterwards, whatever the outcome. Remember that it does NOT commit -- the
# transaction boundary belongs to the service layer (see app/db/session.py).
DbSession = Annotated[Session, Depends(get_db)]

# Extracts a bearer token from the Authorization header.
#
# `tokenUrl` is documentation, not behaviour: it tells the OpenAPI schema -- and
# therefore the "Authorize" button in the interactive docs -- where to obtain a
# token. Pointing it at a route that does not exist produces docs that look
# fine and cannot actually log anyone in, so it must be kept in step with the
# auth router's real path.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

BearerToken = Annotated[str, Depends(oauth2_scheme)]


def get_current_user(token: BearerToken, session: DbSession) -> User:
    """Resolve the authenticated user from a bearer token.

    ARCHITECTURAL DECISION -- the user row IS loaded on every request, even
    though the token already carries the id and role.

    Skipping this lookup is the headline appeal of stateless JWTs, and it is
    the wrong trade here. Without it, deactivating an account has no effect
    until every outstanding token expires: a dismissed staff member keeps full
    admin access for up to thirty minutes. The cost of avoiding that is a
    single primary-key lookup on an indexed column -- among the cheapest
    queries a database can serve, and one that returns an object the handlers
    generally need anyway.

    The token still earns its keep: it authenticates the request without a
    session store, and it means the *password* is transmitted exactly once, at
    login.

    Raises:
        InvalidTokenError: if the token is invalid, or names a user who no
            longer exists or has been deactivated. All three collapse into the
            same error deliberately -- distinguishing "no such user" from "bad
            token" would turn this endpoint into an account-enumeration oracle.
    """
    payload = decode_access_token(token)

    try:
        user_id = int(payload.sub)
    except ValueError as exc:
        # `sub` is a string by specification, but it is attacker-influenced
        # input: a token could carry "abc". Guarded so it fails as an
        # authentication error rather than an unhandled 500.
        raise InvalidTokenError("Could not validate credentials") from exc

    user = session.execute(select(User).where(User.id == user_id)).scalar_one_or_none()

    if user is None or not user.is_active:
        raise InvalidTokenError("Could not validate credentials")

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]

# The same extractor with `auto_error=False`, so a missing Authorization header
# yields None instead of an automatic 401.
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

OptionalBearerToken = Annotated[str | None, Depends(oauth2_scheme_optional)]


def get_optional_current_user(token: OptionalBearerToken, session: DbSession) -> User | None:
    """Resolve the caller if they supplied a token, otherwise None.

    For endpoints that are PUBLIC but behave differently for staff -- browsing
    a menu is the case here, where staff additionally see unavailable items.

    Note the asymmetry, which is deliberate: a MISSING token yields an
    anonymous caller, but a PRESENT-BUT-INVALID token still raises. Silently
    treating a bad token as anonymous would leave a client with an expired
    session quietly seeing less data than it expects, with no indication that
    anything is wrong. Absent is a legitimate state; invalid is an error.
    """
    if token is None:
        return None

    return get_current_user(token, session)


OptionalCurrentUser = Annotated[User | None, Depends(get_optional_current_user)]


def require_role(*allowed_roles: UserRole) -> Callable[[User], User]:
    """Build a dependency that admits only the given roles.

    ARCHITECTURAL DECISION -- authorisation is declared on the route, not
    checked inside the handler.

    The alternative -- `if user.role != UserRole.ADMIN: raise ...` at the top of
    each protected function -- fails in the worst possible way: by omission.
    Nothing about a handler missing that check looks wrong. The endpoint works
    perfectly in every test, and the defect is invisible until someone notices
    that customers can edit menus.

    Expressed as a dependency, the requirement becomes part of the route's
    signature. It is visible in the OpenAPI schema, enforced before the handler
    body runs at all, and -- most importantly -- an endpoint that forgets it
    does not silently pass: it simply has no `CurrentAdmin` parameter, which is
    conspicuous in review.

    A *factory* rather than one hard-coded admin check so the same mechanism
    covers future combinations without duplication. This is the Open/Closed
    Principle: new authorisation rules are new call sites, not edits here.

    Args:
        *allowed_roles: Roles permitted to call the route. Any one suffices.

    Returns:
        A FastAPI dependency yielding the authenticated user.
    """

    def dependency(user: CurrentUser) -> User:
        if user.role not in allowed_roles:
            # Note 403, not 404. Hiding the existence of admin endpoints from
            # authenticated non-admins buys nothing -- the routes are in the
            # public schema -- while an honest 403 tells a legitimate user with
            # the wrong role exactly what is wrong.
            raise PermissionDeniedError(
                f"This operation requires one of the following roles: "
                f"{', '.join(sorted(role.value for role in allowed_roles))}."
            )

        return user

    return dependency


# Staff-only routes: menu management and order status transitions.
CurrentAdmin = Annotated[User, Depends(require_role(UserRole.ADMIN))]

# Routes any authenticated user may call, where the handler needs to know who
# is calling -- placing an order, listing one's own orders.
CurrentCustomer = Annotated[User, Depends(require_role(UserRole.CUSTOMER))]
