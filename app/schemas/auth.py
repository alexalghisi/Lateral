"""Authentication response schemas."""

from pydantic import BaseModel


class TokenResponse(BaseModel):
    """An issued access token.

    The field names are dictated by RFC 6749 (OAuth 2.0), not chosen: they are
    `access_token` and `token_type`, and `token_type` is the literal string
    "bearer". Renaming either to something more pleasant would break every
    standard OAuth2 client, including the "Authorize" control in the generated
    API documentation.

    No refresh token is issued. Refresh tokens exist to keep access tokens
    short-lived while sparing the user frequent logins, and they bring real
    machinery with them: secure storage, rotation, reuse detection, and
    server-side revocation state. That is a deliberate feature to design, not
    something to add by reflex.
    """

    access_token: str
    token_type: str = "bearer"
