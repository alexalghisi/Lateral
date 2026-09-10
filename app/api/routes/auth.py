"""Authentication routes: register, login, and current identity.

Note how little these handlers do. Each one parses input, calls a service, and
shapes a response. There is no business rule, no query and no transaction
management in this file -- and that is the measure of whether the layering is
working. A handler that grows an `if` about domain state has taken on a
responsibility belonging to the layer below it.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm

from app.api.deps import CurrentUser, DbSession
from app.schemas.auth import TokenResponse
from app.schemas.user import UserRegistrationRequest, UserResponse
from app.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a customer account",
    responses={409: {"description": "An account with this email already exists"}},
)
def register(payload: UserRegistrationRequest, session: DbSession) -> UserResponse:
    """Create a customer account.

    Always produces a CUSTOMER. Staff accounts are provisioned internally and
    deliberately have no public endpoint -- an internet-facing route capable of
    creating administrators is a liability no amount of validation makes safe.

    Returns 201 with the created resource. `EmailAlreadyRegisteredError` is
    raised by the service and rendered as 409 by the central error handlers;
    this function does not know that, which is the point.
    """
    user = AuthService(session).register_customer(
        email=payload.email,
        password=payload.password,
        full_name=payload.full_name,
    )

    return UserResponse.model_validate(user)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Exchange credentials for an access token",
    responses={401: {"description": "Incorrect email or password"}},
)
def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: DbSession,
) -> TokenResponse:
    """Authenticate and issue a bearer token.

    Consumes form-encoded data rather than JSON because this implements the
    OAuth2 password flow, which specifies that encoding. Honouring the standard
    is what makes the "Authorize" button in the generated documentation work,
    and what lets any off-the-shelf OAuth2 client talk to this API unmodified.

    `OAuth2PasswordRequestForm` names the credential field `username`. The
    standard fixes that name; this API simply carries an email address in it.
    """
    service = AuthService(session)
    user = service.authenticate(email=form_data.username, password=form_data.password)

    return TokenResponse(access_token=service.issue_access_token(user))


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Return the authenticated user",
    responses={401: {"description": "Missing, invalid or expired token"}},
)
def read_current_user(user: CurrentUser) -> UserResponse:
    """Return the account associated with the supplied token.

    The entire authentication flow is expressed by the `CurrentUser` parameter.
    There is no token parsing here and no `if` guarding access: an unauthenticated
    request never reaches this function body at all.
    """
    return UserResponse.model_validate(user)
