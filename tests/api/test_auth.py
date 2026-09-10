"""End-to-end tests for registration and login.

These exercise the full stack -- HTTP, router, service, repository, PostgreSQL
-- inside a transaction that is rolled back afterwards. They are the tests that
would catch a layer being wired up incorrectly, which unit tests by
construction cannot.

The emphasis is heavily on what must be REFUSED. A registration endpoint that
creates users is easy; one that cannot be used to mint an administrator, to
enumerate existing accounts, or to leak a password hash takes deliberate work,
and each of those properties is asserted below.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.roles import UserRole
from app.models.user import User
from tests import factories

pytestmark = pytest.mark.integration

REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
ME_URL = "/api/v1/auth/me"


def login(client: TestClient, email: str, password: str) -> str:
    """Log in and return the raw access token.

    The login endpoint consumes form-encoded data rather than JSON because it
    implements the OAuth2 password flow, which specifies that encoding. Honouring
    it is what makes the "Authorize" button in the generated documentation work
    against this API without custom glue.
    """
    response = client.post(LOGIN_URL, data={"username": email, "password": password})

    assert response.status_code == 200, response.text

    return str(response.json()["access_token"])


class TestRegistration:
    def test_creates_a_customer_account(self, client: TestClient, db_session: Session) -> None:
        response = client.post(
            REGISTER_URL,
            json={
                "email": "new.customer@example.com",
                "password": "a-sufficiently-long-password",
                "full_name": "New Customer",
            },
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["email"] == "new.customer@example.com"
        assert body["role"] == "customer"
        assert body["id"] > 0

        persisted = db_session.execute(
            select(User).where(User.email == "new.customer@example.com")
        ).scalar_one()
        assert persisted.full_name == "New Customer"

    def test_never_returns_the_password_or_its_hash(self, client: TestClient) -> None:
        """The response schema must not expose credentials.

        Returning the ORM object directly is the easy mistake here, and it
        would serialise `hashed_password` straight into the response body. A
        separate read schema is what prevents it, and this test is what keeps
        the schema honest.
        """
        response = client.post(
            REGISTER_URL,
            json={
                "email": "leak.check@example.com",
                "password": "a-sufficiently-long-password",
                "full_name": "Leak Check",
            },
        )

        body = response.text
        assert "password" not in body
        assert "argon2" not in body

    def test_stores_the_password_hashed_never_in_plaintext(
        self, client: TestClient, db_session: Session
    ) -> None:
        password = "a-sufficiently-long-password"

        client.post(
            REGISTER_URL,
            json={
                "email": "hash.check@example.com",
                "password": password,
                "full_name": "Hash Check",
            },
        )

        persisted = db_session.execute(
            select(User).where(User.email == "hash.check@example.com")
        ).scalar_one()

        assert persisted.hashed_password != password
        assert persisted.hashed_password.startswith("$argon2id$")

    def test_a_client_supplied_role_is_rejected_outright(
        self, client: TestClient, db_session: Session
    ) -> None:
        """PRIVILEGE ESCALATION GUARD -- the most important test in this file.

        If the endpoint honoured a client-supplied `role`, anyone on the
        internet could mint themselves an admin account and take control of
        every menu and every order.

        The request is REFUSED with 422 rather than succeeding with the field
        ignored. Both are safe, and refusal is the better of the two: a caller
        who believes they created an administrator is told plainly that they
        did not, instead of receiving a 201 and discovering the truth later.

        Crucially, no account is created at all -- asserted below, because
        "the role was ignored but the user exists" would be a materially
        different outcome from what this response claims.
        """
        response = client.post(
            REGISTER_URL,
            json={
                "email": "aspiring.admin@example.com",
                "password": "a-sufficiently-long-password",
                "full_name": "Aspiring Admin",
                "role": "admin",
            },
        )

        assert response.status_code == 422

        assert (
            db_session.execute(
                select(User).where(User.email == "aspiring.admin@example.com")
            ).scalar_one_or_none()
            is None
        )

    def test_registration_always_produces_a_customer(
        self, client: TestClient, db_session: Session
    ) -> None:
        """The role is assigned by the server, never negotiated.

        Complements the test above: that one proves the field cannot be
        supplied, this one proves the value the server chooses. Together they
        cover the property regardless of how the schema is later refactored.
        """
        client.post(
            REGISTER_URL,
            json={
                "email": "ordinary@example.com",
                "password": "a-sufficiently-long-password",
                "full_name": "Ordinary Person",
            },
        )

        persisted = db_session.execute(
            select(User).where(User.email == "ordinary@example.com")
        ).scalar_one()

        assert persisted.role is UserRole.CUSTOMER

    def test_rejects_a_duplicate_email(self, client: TestClient, db_session: Session) -> None:
        factories.create_user(db_session, email="taken@example.com")

        response = client.post(
            REGISTER_URL,
            json={
                "email": "taken@example.com",
                "password": "a-sufficiently-long-password",
                "full_name": "Impostor",
            },
        )

        assert response.status_code == 409

    def test_rejects_a_malformed_email(self, client: TestClient) -> None:
        response = client.post(
            REGISTER_URL,
            json={
                "email": "not-an-email",
                "password": "a-sufficiently-long-password",
                "full_name": "Bad Email",
            },
        )

        assert response.status_code == 422

    def test_rejects_a_password_that_is_too_short(self, client: TestClient) -> None:
        response = client.post(
            REGISTER_URL,
            json={"email": "short@example.com", "password": "short", "full_name": "Short"},
        )

        assert response.status_code == 422

    def test_normalises_the_email_to_lowercase(
        self, client: TestClient, db_session: Session
    ) -> None:
        """Email case must not create distinct accounts.

        Without normalisation, `User@example.com` and `user@example.com` become
        two accounts, the unique index permits both, and the owner cannot
        reliably log in because it depends how they typed it that day.
        """
        client.post(
            REGISTER_URL,
            json={
                "email": "MiXeD.CaSe@Example.COM",
                "password": "a-sufficiently-long-password",
                "full_name": "Mixed Case",
            },
        )

        persisted = db_session.execute(
            select(User).where(User.email == "mixed.case@example.com")
        ).scalar_one()

        assert persisted.email == "mixed.case@example.com"


class TestLogin:
    def test_returns_a_bearer_token_for_valid_credentials(
        self, client: TestClient, db_session: Session
    ) -> None:
        user = factories.create_user(db_session, email="valid@example.com")

        response = client.post(
            LOGIN_URL,
            data={"username": user.email, "password": factories.DEFAULT_PASSWORD},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["token_type"] == "bearer"
        assert body["access_token"]

    def test_rejects_a_wrong_password(self, client: TestClient, db_session: Session) -> None:
        user = factories.create_user(db_session, email="wrongpass@example.com")

        response = client.post(
            LOGIN_URL, data={"username": user.email, "password": "not-the-password"}
        )

        assert response.status_code == 401

    def test_unknown_email_and_wrong_password_are_indistinguishable(
        self, client: TestClient, db_session: Session
    ) -> None:
        """ACCOUNT ENUMERATION GUARD.

        If "no such user" and "wrong password" produced different responses,
        the login form would become a tool for discovering which email
        addresses hold accounts -- valuable for targeted phishing and for
        credential-stuffing attacks. Both cases must be indistinguishable to
        the client.
        """
        factories.create_user(db_session, email="known@example.com")

        wrong_password = client.post(
            LOGIN_URL, data={"username": "known@example.com", "password": "wrong"}
        )
        unknown_user = client.post(
            LOGIN_URL, data={"username": "unknown@example.com", "password": "wrong"}
        )

        assert wrong_password.status_code == unknown_user.status_code == 401
        assert wrong_password.json() == unknown_user.json()

    def test_rejects_a_deactivated_account(self, client: TestClient, db_session: Session) -> None:
        user = factories.create_user(db_session, email="disabled@example.com", is_active=False)

        response = client.post(
            LOGIN_URL,
            data={"username": user.email, "password": factories.DEFAULT_PASSWORD},
        )

        assert response.status_code == 401

    def test_login_is_case_insensitive_for_the_email(
        self, client: TestClient, db_session: Session
    ) -> None:
        factories.create_user(db_session, email="casing@example.com")

        response = client.post(
            LOGIN_URL,
            data={"username": "CASING@Example.com", "password": factories.DEFAULT_PASSWORD},
        )

        assert response.status_code == 200


class TestAuthenticatedIdentity:
    """`/auth/me` -- the smallest possible exercise of the auth dependency."""

    def test_returns_the_authenticated_user(self, client: TestClient, db_session: Session) -> None:
        user = factories.create_user(db_session, email="whoami@example.com")
        token = login(client, user.email, factories.DEFAULT_PASSWORD)

        response = client.get(ME_URL, headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        assert response.json()["email"] == "whoami@example.com"

    def test_requires_a_token(self, client: TestClient) -> None:
        assert client.get(ME_URL).status_code == 401

    def test_rejects_a_malformed_token(self, client: TestClient) -> None:
        response = client.get(ME_URL, headers={"Authorization": "Bearer not-a-real-token"})

        assert response.status_code == 401
        # The header that tells a client HOW to authenticate. Omitting it is a
        # common oversight that breaks standard HTTP tooling.
        assert response.headers["WWW-Authenticate"] == "Bearer"

    def test_a_token_stops_working_once_the_account_is_deactivated(
        self, client: TestClient, db_session: Session
    ) -> None:
        """Deactivation takes effect immediately, not when the token expires.

        This is the property bought by loading the user row on every request
        instead of trusting the token alone. Without it, a dismissed staff
        member would keep working access for the remaining token lifetime.
        """
        user = factories.create_user(db_session, email="revoked@example.com")
        token = login(client, user.email, factories.DEFAULT_PASSWORD)

        assert client.get(ME_URL, headers={"Authorization": f"Bearer {token}"}).status_code == 200

        user.is_active = False
        db_session.flush()

        assert client.get(ME_URL, headers={"Authorization": f"Bearer {token}"}).status_code == 401
