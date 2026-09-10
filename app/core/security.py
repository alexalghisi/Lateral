"""Password hashing and JSON Web Token handling.

ARCHITECTURAL DECISION 1 -- Argon2id, bound directly, with no passlib.

Argon2id is the current OWASP recommendation for password storage. Unlike
bcrypt it is *memory*-hard as well as CPU-hard, which is what makes large-scale
GPU and ASIC cracking economically unattractive: an attacker cannot simply
throw more parallel cores at it, because each core needs its own megabytes of
memory. It also has no silent input truncation, whereas bcrypt ignores
everything past 72 bytes -- meaning a long passphrase is quietly weaker than
its owner believes.

passlib is deliberately not used as a wrapper. It has been effectively
unmaintained for years, and its bcrypt backend is a well-known source of
version-detection breakage. `argon2-cffi` is maintained, does exactly one
thing, and its API is small enough that the abstraction layer would add risk
rather than remove it.

ARCHITECTURAL DECISION 2 -- stateless JWTs, with the trade-off stated plainly.

Tokens carry the user id and role, so the token itself is self-describing and
requires no server-side session store. The cost is real and must not be glossed
over: a token cannot be revoked before it expires. Logging out or a role
downgrade does not invalidate tokens already issued.

(See app/api/deps.py for the related decision to still load the user row on
each request, which buys back immediate effect for account deactivation.)

For this system that trade-off is acceptable because the mitigation -- a short
lifetime -- is sufficient. Any deployment needing immediate revocation should
add a denylist keyed by token id, which reintroduces a per-request lookup and
should therefore be a conscious decision rather than a default.
"""

from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error
from pydantic import BaseModel, ValidationError

from app.core.config import get_settings
from app.domain.errors import DomainError
from app.domain.roles import UserRole

# A single hasher instance, reused across the process. Constructing one is not
# free, and its parameters (memory cost, time cost, parallelism) are exactly the
# kind of security-relevant configuration that must be defined in one place
# rather than at each call site. Defaults track the argon2-cffi maintainers'
# current recommendation, which is a better default than any number invented
# here would be.
_password_hasher = PasswordHasher()

# Marks a token as an access token. There is only one type today, but the claim
# is written and verified from the start: the moment refresh tokens appear, an
# unchecked type claim means a refresh token is silently accepted as an access
# token, granting a long-lived credential where a short-lived one was intended.
_TOKEN_TYPE_ACCESS = "access"


class InvalidTokenError(DomainError):
    """Raised for any token that is not valid, for any reason.

    ARCHITECTURAL DECISION -- one error for every failure mode.

    Forged signature, expired, malformed, wrong type, unknown role: all raise
    the same exception with the same message. This is intentional. Reporting
    "signature invalid" versus "token expired" hands an attacker a probing
    oracle that tells them which part of a forgery attempt failed. The server
    logs the specific cause; the client is told only that the token is not
    valid.
    """


class TokenPayload(BaseModel):
    """The validated contents of an access token.

    Modelled with Pydantic rather than passed around as a raw dict. A dict from
    `jwt.decode` is attacker-influenced input: every key is optional and every
    value is whatever the token said. Validating it into a typed model at the
    boundary means the rest of the application works with a guaranteed shape,
    and an unknown role becomes a validation error here instead of a confusing
    comparison failure three layers away.
    """

    sub: str
    role: UserRole
    exp: datetime
    iat: datetime


def hash_password(password: str) -> str:
    """Hash a plaintext password for storage.

    The returned digest embeds the algorithm, its parameters and a per-hash
    random salt. Storing those alongside the hash is what allows the cost
    parameters to be raised later without invalidating existing passwords:
    each digest remains verifiable against the parameters it was created with.
    """
    return _password_hasher.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    """Check a plaintext password against a stored digest.

    Returns False rather than raising on malformed input. This function runs in
    the login path, where a corrupted or truncated digest in the database must
    produce a clean "authentication failed", not a 500 with a stack trace. An
    unparseable hash means the same thing to the caller as a wrong password:
    access is denied.

    The underlying comparison is constant-time, so it does not leak information
    about how much of the digest matched through response timing.
    """
    try:
        return _password_hasher.verify(hashed_password, password)
    except (Argon2Error, ValueError, TypeError):
        # Argon2Error covers a genuine mismatch and most corruption cases;
        # ValueError/TypeError cover input that is not a parseable hash at all,
        # such as an empty string.
        return False


def create_access_token(
    subject: str,
    role: UserRole,
    expires_delta: timedelta | None = None,
) -> str:
    """Issue a signed access token.

    Args:
        subject: The user id, as a string. RFC 7519 defines `sub` as a string
            claim; passing an integer produces a token that some libraries
            reject and others silently coerce -- an interoperability trap that
            only surfaces once a second client exists.
        role: The role embedded in the token, so authorisation needs no
            database lookup.
        expires_delta: Overrides the configured lifetime. Intended for tests
            and for deliberately short-lived tokens.

    Returns:
        The encoded JWT.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    expires_at = now + (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )

    payload = {
        "sub": subject,
        "role": role.value,
        # `iat` is not merely informational: it is what allows a future
        # "invalidate everything issued before X" check, which is the cheapest
        # available response to a suspected key compromise.
        "iat": now,
        "exp": expires_at,
        "type": _TOKEN_TYPE_ACCESS,
    }

    return jwt.encode(payload, settings.SECRET_KEY.get_secret_value(), settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> TokenPayload:
    """Verify a token's signature and claims, returning its validated payload.

    Raises:
        InvalidTokenError: for every failure mode, without distinguishing them.
    """
    settings = get_settings()

    try:
        raw = jwt.decode(
            token,
            settings.SECRET_KEY.get_secret_value(),
            # CRITICAL: the accepted algorithm is pinned by the VERIFIER and
            # the token's own header is not trusted to choose it. This is the
            # defence against the `alg: none` downgrade attack, in which an
            # attacker rewrites the header to declare no signature, strips it,
            # and edits the claims freely. A decoder that believes the header
            # accepts the result. Passing a list of permitted algorithms is the
            # only correct way to call this function.
            algorithms=[settings.JWT_ALGORITHM],
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.PyJWTError as exc:
        # Deliberately collapses expiry, bad signature and malformed structure
        # into one error: see InvalidTokenError for why the caller is not told
        # which one occurred.
        raise InvalidTokenError("Could not validate credentials") from exc

    if raw.get("type") != _TOKEN_TYPE_ACCESS:
        raise InvalidTokenError("Could not validate credentials")

    try:
        return TokenPayload.model_validate(raw)
    except ValidationError as exc:
        # Reached when a claim is missing or holds a value outside its type --
        # an unknown role, for instance. Refusing here is what stops an
        # unrecognised role from being coerced into a valid one.
        raise InvalidTokenError("Could not validate credentials") from exc
