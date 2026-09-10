"""Application configuration.

ARCHITECTURAL DECISION -- one typed settings object, resolved once.

Configuration is read from the environment into a single validated Pydantic
model. Three properties of this design matter:

1.  FAIL FAST. A missing or malformed variable raises at import time, so the
    container dies on startup instead of serving traffic and then failing on
    the first request that happens to need the bad value. A crash-looping
    container is a loud, obvious, immediately diagnosable failure; a service
    that 500s intermittently at 3am is not.

2.  NO GLOBAL MUTABLE STATE. Modules depend on `get_settings()`, never on a
    module-level `settings` singleton created at import. That indirection is
    what lets tests substitute configuration without reaching into internals.

3.  SECRETS ARE TYPED AS SECRETS. `SecretStr` renders as `**********` in every
    repr, log line and traceback. Credentials leak through exception reporters
    and debug dumps far more often than through deliberate logging; making the
    safe behaviour the default removes a whole class of incident.

Field names are UPPER_CASE to mirror the environment variables they bind to.
This intentionally departs from PEP 8 attribute naming: when an operator is
staring at a settings dump next to a `.env` file during an incident, an exact
one-to-one correspondence is worth more than casing purity.
"""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated, immutable view of the process environment."""

    model_config = SettingsConfigDict(
        # A local `.env` is a developer convenience. In Docker, Compose and the
        # orchestrator inject real environment variables, which take precedence
        # over any file, so the same code path serves both cases.
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        # Ignore unrelated variables (PATH, HOME, Compose-only settings such as
        # NGINX_HOST_PORT). Without this, an unrecognised variable in the
        # process environment would crash startup -- fail-fast taken too far.
        extra="ignore",
        # Configuration must not change under a running process. Immutability
        # means a value read at startup is the value in force at request time.
        frozen=True,
    )

    # --- Application ---------------------------------------------------------
    APP_ENV: str = "local"
    LOG_LEVEL: str = "info"

    # No default. A service that silently falls back to a hard-coded signing key
    # is a service that ships one to production.
    SECRET_KEY: SecretStr

    # --- JSON Web Tokens -----------------------------------------------------
    # HS256 (symmetric) rather than RS256 (asymmetric). Asymmetric signing earns
    # its extra key-management burden when tokens are verified by parties who
    # must not be able to mint them -- separate services, third parties. Here a
    # single service both issues and verifies, so the private/public split would
    # add operational complexity and protect against nothing.
    JWT_ALGORITHM: str = "HS256"

    # Short-lived by design. A JWT cannot be revoked before it expires without
    # introducing a server-side denylist -- which would reintroduce the per-
    # request database lookup that stateless tokens exist to avoid. The lever
    # that remains is lifetime: 30 minutes bounds the damage from a leaked
    # token while keeping re-authentication infrequent enough to be usable.
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=30, ge=1)

    # --- PostgreSQL ----------------------------------------------------------
    POSTGRES_USER: str
    POSTGRES_PASSWORD: SecretStr
    POSTGRES_DB: str
    POSTGRES_HOST: str = "db"
    POSTGRES_PORT: int = Field(default=5432, ge=1, le=65535)

    @property
    def database_url(self) -> str:
        """SQLAlchemy connection URL, assembled from discrete parts.

        DELIBERATELY a plain `@property` and NOT a Pydantic `computed_field`.
        A computed field is included in the model's repr and in `model_dump()`,
        which would reinstate the very leak `SecretStr` exists to prevent: the
        password is masked as its own field, then handed back in clear text
        inside the assembled URL. This was caught by
        `test_secrets_are_not_exposed_by_repr`, which is why that test exists.

        Composed here rather than accepted whole as a `DATABASE_URL` so the
        password stays an individually injectable secret. Secret managers hand
        out credentials as separate values; rebuilding a monolithic URL just to
        take it apart again adds a step where the password can be logged.

        The `+psycopg` suffix selects the psycopg 3 dialect explicitly. Omitting
        it makes SQLAlchemy default to psycopg2, which is not installed -- an
        error that would surface as a confusing import failure at first
        connection rather than at startup.
        """
        return (
            f"postgresql+psycopg://"
            f"{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD.get_secret_value()}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def docs_enabled(self) -> bool:
        """Whether to expose the interactive API documentation.

        Schema exposure is reconnaissance material: it enumerates every route,
        parameter and model for an attacker. Useful everywhere except in front
        of real customer data.
        """
        return self.APP_ENV.lower() != "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, constructing them on first use.

    Cached because parsing and validation should happen once, not per request.
    Exposed as a *function* rather than a module-level instance for two
    reasons: importing this module has no side effects (so tooling and Alembic
    can import it freely), and `get_settings.cache_clear()` gives tests a
    supported way to force re-resolution after changing the environment.
    """
    return Settings()  # type: ignore[call-arg]  # values come from the environment
