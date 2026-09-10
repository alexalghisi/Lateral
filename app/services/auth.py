"""Registration and authentication."""

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password, verify_password
from app.domain.errors import ConflictError
from app.domain.roles import UserRole
from app.models.user import User
from app.repositories.user import UserRepository

logger = logging.getLogger(__name__)


class EmailAlreadyRegisteredError(ConflictError):
    """Raised when registration targets an address that already has an account.

    A conflict (409) rather than a validation error (422): the request is
    perfectly well-formed, it just contradicts the current state of the world.
    """


class AuthenticationFailedError(Exception):
    """Raised when credentials are not accepted.

    ARCHITECTURAL DECISION -- ONE error for every login failure.

    Unknown email, wrong password, deactivated account: all raise this, with
    the same message. Distinguishing them would turn the login endpoint into an
    account-enumeration oracle, letting an attacker discover which addresses
    hold accounts -- the reconnaissance step before targeted phishing and
    credential stuffing.

    Deliberately NOT a `DomainError`. It maps to 401 with a
    `WWW-Authenticate` header rather than to the 400-series responses the
    domain hierarchy produces, and it is an authentication concern rather than
    a business-rule violation.
    """


class AuthService:
    """Account creation and credential verification.

    Depends on a `Session` and constructs its own repository. For a service
    with a single collaborator this is the right amount of indirection:
    injecting the repository too would add a constructor parameter and a
    wiring decision at every call site to make substitutable something that is
    already substituted wholesale in tests -- the session itself is the seam.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._users = UserRepository(session)

    def register_customer(self, *, email: str, password: str, full_name: str) -> User:
        """Create a customer account.

        The method is named `register_customer`, not `register_user`, and takes
        no role parameter. Public registration cannot produce staff, and the
        signature says so: there is no argument through which a caller could
        request it, however carelessly this is invoked.

        Raises:
            EmailAlreadyRegisteredError: if the address already has an account.
        """
        if self._users.email_exists(email):
            raise EmailAlreadyRegisteredError("An account with this email already exists.")

        try:
            user = self._users.add(
                email=email,
                hashed_password=hash_password(password),
                full_name=full_name,
                role=UserRole.CUSTOMER,
            )
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()

            # Reached when two registrations for the same address race: both
            # pass the check above, and the unique index rejects the loser.
            # The pre-check is not redundant -- it produces a clear error in
            # the common case -- but the database constraint is what actually
            # guarantees uniqueness, and this branch is how that guarantee
            # surfaces as a sensible response instead of a 500.
            raise EmailAlreadyRegisteredError("An account with this email already exists.") from exc

        return user

    def authenticate(self, *, email: str, password: str) -> User:
        """Verify credentials and return the authenticated user.

        Raises:
            AuthenticationFailedError: for any failure, without distinction.
        """
        user = self._users.get_by_email(email)

        if user is None:
            # A hash is computed against a dummy value even though there is no
            # user, so that a request for an unknown address costs the same as
            # one for a known address with a wrong password. Skipping it would
            # leak account existence through response timing: Argon2 is
            # deliberately slow, so "fast rejection" is a reliable signal that
            # no such account exists. Constant-time behaviour has to include
            # the work not done.
            hash_password(password)
            logger.info("Authentication failed: no account for the supplied email")
            raise AuthenticationFailedError("Incorrect email or password.")

        if not verify_password(password, user.hashed_password):
            logger.info("Authentication failed: incorrect password for user id=%s", user.id)
            raise AuthenticationFailedError("Incorrect email or password.")

        if not user.is_active:
            logger.info("Authentication failed: account deactivated, user id=%s", user.id)
            raise AuthenticationFailedError("Incorrect email or password.")

        return user

    def issue_access_token(self, user: User) -> str:
        """Mint a signed access token for an authenticated user.

        Separate from `authenticate` so that verifying credentials and issuing
        a credential remain distinct operations. Some flows need one without
        the other -- impersonation by support staff, or a future refresh
        exchange that issues a token without re-checking a password.
        """
        return create_access_token(subject=str(user.id), role=user.role)
