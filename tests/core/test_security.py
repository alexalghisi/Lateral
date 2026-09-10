"""Tests for password hashing and JWT handling.

Security primitives get the most rigorous tests in the codebase, and for a
specific reason: their failures are silent. A broken sort order shows up as a
visibly wrong list; a broken signature check shows up as nothing at all, until
someone forges a token. There is no user-visible symptom to notice, so the
tests are the only line of defence.

These are pure unit tests -- no database, no HTTP. Cryptographic behaviour does
not depend on either.
"""

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.security import (
    InvalidTokenError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.models.user import UserRole


class TestPasswordHashing:
    """Argon2id hashing."""

    def test_hash_then_verify_succeeds(self) -> None:
        password = "correct horse battery staple"

        assert verify_password(password, hash_password(password))

    def test_verify_rejects_a_wrong_password(self) -> None:
        assert not verify_password("wrong", hash_password("right"))

    def test_hash_is_not_the_plaintext(self) -> None:
        """The most basic property, and worth asserting explicitly."""
        password = "s3cret-value"
        digest = hash_password(password)

        assert password not in digest

    def test_same_password_hashes_differently_every_time(self) -> None:
        """Each hash must carry a unique random salt.

        Without per-hash salts, identical passwords produce identical digests.
        An attacker who steals the table then learns which users share a
        password, and one cracked digest unlocks all of them. Salting also
        defeats precomputed rainbow tables.
        """
        first = hash_password("same-password")
        second = hash_password("same-password")

        assert first != second
        assert verify_password("same-password", first)
        assert verify_password("same-password", second)

    def test_uses_argon2id(self) -> None:
        """Pin the algorithm identifier.

        Argon2 digests are self-describing: the prefix names the variant. This
        asserts we use argon2id -- the variant that resists both side-channel
        and GPU-based attacks -- rather than argon2i or argon2d.
        """
        assert hash_password("x").startswith("$argon2id$")

    def test_verify_returns_false_for_a_malformed_hash(self) -> None:
        """A corrupted or truncated digest must not raise.

        Verification runs inside the login path. An exception escaping here
        turns a bad row in the database into a 500 and hands the caller a
        stack trace instead of a clean authentication failure.
        """
        assert not verify_password("any-password", "not-a-valid-argon2-hash")

    def test_rejects_empty_hash(self) -> None:
        assert not verify_password("any-password", "")


class TestAccessTokenCreation:
    """Issuing JWTs."""

    def test_token_round_trips(self) -> None:
        token = create_access_token(subject="42", role=UserRole.ADMIN)
        payload = decode_access_token(token)

        assert payload.sub == "42"
        assert payload.role is UserRole.ADMIN

    def test_token_carries_an_expiry(self) -> None:
        token = create_access_token(subject="1", role=UserRole.CUSTOMER)
        payload = decode_access_token(token)

        assert payload.exp > datetime.now(UTC)

    def test_expiry_can_be_overridden(self) -> None:
        token = create_access_token(
            subject="1", role=UserRole.CUSTOMER, expires_delta=timedelta(minutes=1)
        )
        payload = decode_access_token(token)

        assert payload.exp < datetime.now(UTC) + timedelta(minutes=2)

    def test_subject_is_a_string(self) -> None:
        """RFC 7519 requires `sub` to be a string.

        Passing an integer produces a token some libraries reject outright and
        others silently coerce, which is exactly the sort of interoperability
        trap that surfaces only once a second client is written.
        """
        token = create_access_token(subject="7", role=UserRole.CUSTOMER)
        raw = jwt.decode(token, options={"verify_signature": False})

        assert isinstance(raw["sub"], str)


class TestTokenRejection:
    """Everything that must NOT be accepted. The security-critical half."""

    def test_rejects_a_token_signed_with_another_key(self) -> None:
        """The core guarantee: an attacker cannot mint their own tokens."""
        forged = jwt.encode(
            {
                "sub": "1",
                "role": "admin",
                "exp": datetime.now(UTC) + timedelta(hours=1),
                "type": "access",
            },
            "an-attackers-key",
            algorithm="HS256",
        )

        with pytest.raises(InvalidTokenError):
            decode_access_token(forged)

    def test_rejects_an_expired_token(self) -> None:
        expired = create_access_token(
            subject="1", role=UserRole.CUSTOMER, expires_delta=timedelta(minutes=-5)
        )

        with pytest.raises(InvalidTokenError):
            decode_access_token(expired)

    def test_rejects_the_none_algorithm(self) -> None:
        """Guards against the classic JWT `alg: none` downgrade attack.

        An attacker takes a valid token, rewrites the header to declare no
        algorithm, strips the signature, and edits the claims at will. A
        decoder that trusts the header believes them. Pinning the accepted
        algorithm on the verifying side is the only correct defence.
        """
        unsigned = jwt.encode(
            {"sub": "1", "role": "admin", "exp": datetime.now(UTC) + timedelta(hours=1)},
            key="",
            algorithm="none",
        )

        with pytest.raises(InvalidTokenError):
            decode_access_token(unsigned)

    def test_rejects_structural_garbage(self) -> None:
        for garbage in ("", "not-a-token", "a.b.c", "..."):
            with pytest.raises(InvalidTokenError):
                decode_access_token(garbage)

    def test_rejects_a_token_missing_the_role_claim(self) -> None:
        """Authorisation data is mandatory, not optional.

        A token without a role must be refused outright. The dangerous
        alternative is defaulting it -- default to admin and anyone can
        escalate; default to customer and the failure is silent and confusing.
        """
        from app.core.config import get_settings

        incomplete = jwt.encode(
            {"sub": "1", "exp": datetime.now(UTC) + timedelta(hours=1), "type": "access"},
            get_settings().SECRET_KEY.get_secret_value(),
            algorithm="HS256",
        )

        with pytest.raises(InvalidTokenError):
            decode_access_token(incomplete)

    def test_rejects_an_unknown_role_value(self) -> None:
        """A role outside the enum must not be coerced into a valid one."""
        from app.core.config import get_settings

        bogus = jwt.encode(
            {
                "sub": "1",
                "role": "superuser",
                "exp": datetime.now(UTC) + timedelta(hours=1),
                "type": "access",
            },
            get_settings().SECRET_KEY.get_secret_value(),
            algorithm="HS256",
        )

        with pytest.raises(InvalidTokenError):
            decode_access_token(bogus)
