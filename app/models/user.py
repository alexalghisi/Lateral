"""User accounts and roles."""

from enum import StrEnum

from sqlalchemy import Boolean, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import IdentityMixin, TimestampMixin


class UserRole(StrEnum):
    """Authorisation roles.

    Deliberately a closed set of two rather than a general permission system.
    The specification distinguishes exactly two kinds of actor -- customers who
    browse and order, and internal staff who manage menus and advance order
    statuses. Building a role/permission/grant framework for two roles is
    speculative generality: it triples the surface area to secure and test in
    exchange for flexibility nobody has asked for. YAGNI, applied to the part
    of the system where extra complexity is most dangerous.

    If per-permission granularity is ever needed, this enum becomes a foreign
    key to a roles table; nothing outside the authorisation dependency changes,
    because routes declare the role they require rather than inspecting users
    directly.
    """

    CUSTOMER = "customer"
    ADMIN = "admin"


class User(IdentityMixin, TimestampMixin, Base):
    """A person who can authenticate.

    One table for both roles rather than separate `customers` and `staff`
    tables. Authentication is identical for both, so splitting them would mean
    two lookups on every login and an ambiguous answer to "does this email
    already exist?".
    """

    __tablename__ = "users"

    # Length-bounded rather than unbounded TEXT. PostgreSQL stores both
    # identically, but the limit documents intent and stops a malformed client
    # from writing a megabyte into an email column.
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)

    # NOTE the column name. Calling it `password` invites someone to assign a
    # plaintext value to it; `hashed_password` makes the mistake visible at the
    # point of assignment and in every code review thereafter.
    #
    # Argon2id digests are variable-length and encode their own parameters;
    # 255 leaves ample room for future parameter increases.
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    full_name: Mapped[str] = mapped_column(String(255), nullable=False)

    role: Mapped[UserRole] = mapped_column(
        Enum(
            UserRole,
            name="user_role",
            # CRITICAL: without `values_callable`, SQLAlchemy persists enum
            # *member names* ("CUSTOMER") while the application compares
            # against *values* ("customer"). The mismatch is invisible until a
            # row written by one path is read by another. This forces the
            # stored representation to be the value, matching the JSON contract.
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=UserRole.CUSTOMER,
        server_default=UserRole.CUSTOMER.value,
    )

    # Soft deactivation instead of row deletion. Orders reference their
    # customer, so deleting a user would either orphan or cascade away order
    # history that the business needs to keep for accounting and disputes.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    def __repr__(self) -> str:
        # Email is included but the hash never is. Reprs end up in logs and
        # exception reports; a password digest in a log is a credential in a log.
        return f"<User id={self.id} email={self.email!r} role={self.role}>"
