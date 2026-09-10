"""Tests for the health probes.

These are the first tests in the suite, and they exist to pin down the
foundation before any domain logic is written: that the application factory
produces a working ASGI app, that configuration resolves, and that the two
probes have genuinely different semantics.

The liveness/readiness split is not decoration. A container orchestrator uses
them for opposite purposes, and conflating them causes a specific and painful
production failure: a brief database outage makes every replica fail its
liveness probe, the orchestrator restarts all of them simultaneously, and a
recoverable dependency blip becomes a full outage with a cold start on top.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings


class TestLiveness:
    """`/health/live` answers: is this process running and able to serve?"""

    def test_returns_ok(self, client: TestClient) -> None:
        response = client.get("/health/live")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_does_not_depend_on_the_database(self, client: TestClient) -> None:
        """Liveness MUST NOT touch external dependencies.

        No database fixture is set up by this test, and none is required. If
        this test ever starts failing because PostgreSQL is unreachable, the
        probe has acquired a dependency it must not have.
        """
        assert client.get("/health/live").status_code == 200


class TestReadiness:
    """`/health/ready` answers: can this process serve traffic *right now*?"""

    @pytest.mark.integration
    def test_reports_ready_when_the_database_is_reachable(self, client: TestClient) -> None:
        response = client.get("/health/ready")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ready"
        assert body["database"] == "ok"

    def test_reports_503_when_the_database_is_unreachable(
        self, unreachable_database_client: TestClient
    ) -> None:
        """A failed dependency check must surface as 503, never as 500.

        503 tells the load balancer to stop routing traffic to this replica
        while leaving it running. A 500 would signal an application defect and
        invites the wrong operational response.

        This test needs no live database: it points the session at a closed
        port, so it runs anywhere, including a laptop with Docker stopped.
        """
        response = unreachable_database_client.get("/health/ready")

        assert response.status_code == 503
        assert response.json()["database"] == "unavailable"


class TestApplicationConfiguration:
    """The settings object is the contract between the environment and the app."""

    def test_database_url_is_assembled_from_discrete_parts(self, settings: Settings) -> None:
        assert settings.database_url.startswith("postgresql+psycopg://")
        assert settings.POSTGRES_DB in settings.database_url

    def test_secrets_are_not_exposed_by_repr_or_dump(self) -> None:
        """Settings get logged during incident triage. Secrets must not leak there.

        Built with explicit sentinel values rather than the ambient settings
        fixture: the deployed username and password can coincide, which would
        let this test pass or fail for reasons unrelated to what it asserts.
        A test that can be right by accident is not a test.

        `model_dump()` is checked alongside `repr()` because they leak
        independently -- a Pydantic computed field is excluded from neither.
        """
        secret_key_sentinel = "signing-key-that-must-never-be-rendered"
        password_sentinel = "password-that-must-never-be-rendered"

        settings = Settings(
            SECRET_KEY=secret_key_sentinel,
            POSTGRES_USER="lateral",
            POSTGRES_PASSWORD=password_sentinel,
            POSTGRES_DB="lateral",
        )

        for rendered in (repr(settings), str(settings), str(settings.model_dump())):
            assert secret_key_sentinel not in rendered
            assert password_sentinel not in rendered

        # The credentials must still be reachable where they are legitimately
        # needed -- masking must not have become data loss.
        assert password_sentinel in settings.database_url
