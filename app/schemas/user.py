"""Request and response schemas for user accounts."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.domain.roles import UserRole

# NIST SP 800-63B guidance: length is the property that matters, and
# composition rules (an uppercase letter, a digit, a symbol) measurably harm
# security by pushing users towards predictable patterns such as "Password1!".
# A generous minimum with no composition requirement is the current
# recommendation, and it is what this API enforces.
MINIMUM_PASSWORD_LENGTH = 12

# Argon2 has no bcrypt-style 72-byte truncation, but an unbounded password is
# still a denial-of-service vector: hashing is deliberately expensive, so a
# multi-megabyte input would burn CPU and memory on a single request.
MAXIMUM_PASSWORD_LENGTH = 256


class UserRegistrationRequest(BaseModel):
    """Body of a public registration request.

    NOTE what is ABSENT: there is no `role` field.

    This is the privilege-escalation defence, and it is structural rather than
    procedural. The role is not accepted and then overwritten -- it has nowhere
    to be received in the first place. A future edit cannot accidentally start
    honouring it, because adding it back would be a visible change to this
    class rather than the quiet removal of a line of validation elsewhere.

    Making the safe behaviour the only expressible behaviour is worth more than
    any amount of careful checking.
    """

    model_config = ConfigDict(
        # Reject unknown keys instead of ignoring them. If a client sends
        # `role: admin`, it gets a 422 telling it the field is not accepted --
        # far better than a silent success that leaves the caller believing
        # they created an administrator.
        extra="forbid",
        str_strip_whitespace=True,
    )

    email: EmailStr
    password: str = Field(
        min_length=MINIMUM_PASSWORD_LENGTH,
        max_length=MAXIMUM_PASSWORD_LENGTH,
        description=(
            f"At least {MINIMUM_PASSWORD_LENGTH} characters. "
            "No composition rules: length is what provides strength."
        ),
    )
    full_name: str = Field(min_length=1, max_length=255)

    @field_validator("email")
    @classmethod
    def normalise_email(cls, value: str) -> str:
        """Canonicalise the address to lowercase.

        Without this, `User@example.com` and `user@example.com` become two
        distinct accounts -- the unique index permits both -- and the owner's
        ability to log in depends on how they happened to type it. The domain
        part is case-insensitive by specification, and no mail provider in
        practical use treats the local part as case-sensitive.
        """
        return value.lower()


class UserResponse(BaseModel):
    """A user as exposed by the API.

    Contains no credential material of any kind. `hashed_password` is not
    omitted by convention or by a serialiser option that could be toggled: the
    field simply does not exist on this type, so it cannot be serialised.
    """

    # Allows construction from an ORM instance via `model_validate`.
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    full_name: str
    role: UserRole
    is_active: bool
    created_at: datetime
