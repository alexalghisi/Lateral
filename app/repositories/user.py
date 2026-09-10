"""Persistence operations for user accounts."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.roles import UserRole
from app.models.user import User


class UserRepository:
    """Queries and writes against the `users` table.

    Constructed with a session rather than creating one. The session is
    request-scoped and owned by the caller, so the repository participates in
    whatever transaction is already in progress instead of opening its own.
    This is what allows a service to perform several repository operations and
    commit them as a single atomic unit -- and what allows tests to run the
    whole thing inside a transaction they later roll back.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_id(self, user_id: int) -> User | None:
        """Return the user with this id, or None."""
        return self._session.get(User, user_id)

    def get_by_email(self, email: str) -> User | None:
        """Return the user with this email, or None.

        The email is lowercased before comparison. Addresses are stored
        canonically at registration, so this keeps lookups consistent for
        callers that have not normalised -- notably the login form, where the
        user types the address by hand each time.
        """
        statement = select(User).where(User.email == email.lower())

        return self._session.execute(statement).scalar_one_or_none()

    def email_exists(self, email: str) -> bool:
        """Whether an account already uses this email.

        Selects a constant rather than the whole row: this is a question about
        existence, and there is no reason to transfer a user record to answer
        it. `limit(1)` lets PostgreSQL stop at the first match.
        """
        statement = select(1).where(User.email == email.lower()).limit(1)

        return self._session.execute(statement).scalar_one_or_none() is not None

    def add(
        self,
        *,
        email: str,
        hashed_password: str,
        full_name: str,
        role: UserRole = UserRole.CUSTOMER,
    ) -> User:
        """Insert a new user and return it with its generated id.

        The role defaults to CUSTOMER, and callers must pass ADMIN explicitly.
        Defaults in security-relevant code should always be the least
        privileged option: a call site that forgets the argument then creates
        an under-privileged user, which is a visible inconvenience, rather than
        an over-privileged one, which is a silent breach.

        The password arrives ALREADY HASHED. Hashing is a security-policy
        decision that belongs to the service layer, and a repository that
        accepted plaintext would be one refactor away from storing it.

        `flush` -- not `commit`. It sends the INSERT so PostgreSQL assigns the
        primary key and enforces constraints, while leaving the transaction
        open for the service to complete or abandon.
        """
        user = User(
            email=email.lower(),
            hashed_password=hashed_password,
            full_name=full_name,
            role=role,
        )

        self._session.add(user)
        self._session.flush()

        return user
