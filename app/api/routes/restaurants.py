"""Restaurant and menu routes.

Two audiences, one resource. Reads are public; every write requires the admin
role, declared through the `CurrentAdmin` dependency rather than checked inside
the handler. An endpoint that forgets an inline check looks entirely normal in
review; one missing its `CurrentAdmin` parameter does not.
"""

from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentAdmin, DbSession, OptionalCurrentUser
from app.domain.roles import UserRole
from app.models.user import User
from app.schemas.restaurant import (
    MenuItemCreateRequest,
    MenuItemResponse,
    RestaurantCreateRequest,
    RestaurantResponse,
    RestaurantUpdateRequest,
)
from app.services.catalog import CatalogService

router = APIRouter(prefix="/restaurants", tags=["restaurants"])

# Pagination bounds, defined once.
#
# The cap is enforced rather than suggested: without an upper bound,
# `?limit=1000000` reintroduces exactly the unbounded query that pagination
# exists to prevent, and the endpoint fails on the database, the serialiser and
# the client simultaneously.
PageLimit = Annotated[int, Query(ge=1, le=100, description="Maximum results to return.")]
PageOffset = Annotated[int, Query(ge=0, description="Results to skip.")]


def _is_staff(viewer: User | None) -> bool:
    """Whether the caller should be granted the staff view of the catalogue.

    A single helper rather than the same comparison repeated in each handler.
    Note it decides VISIBILITY, not permission: authorisation for writes is
    enforced by the `CurrentAdmin` dependency, which rejects the request before
    a handler body runs at all.
    """
    return viewer is not None and viewer.role is UserRole.ADMIN


@router.get("", response_model=list[RestaurantResponse], summary="List restaurants")
def list_restaurants(
    session: DbSession,
    viewer: OptionalCurrentUser,
    limit: PageLimit = 20,
    offset: PageOffset = 0,
) -> list[RestaurantResponse]:
    """List restaurants, most relevant to the caller's role.

    Public: browsing must not require an account, or nobody can evaluate the
    service before signing up. Customers see active restaurants; staff also see
    deactivated ones, since a restaurant that cannot be listed also cannot be
    reactivated through the API.
    """
    restaurants = CatalogService(session).list_restaurants(
        limit=limit, offset=offset, as_staff=_is_staff(viewer)
    )

    return [RestaurantResponse.model_validate(restaurant) for restaurant in restaurants]


@router.get(
    "/{restaurant_id}",
    response_model=RestaurantResponse,
    summary="Retrieve a restaurant",
    responses={404: {"description": "No such restaurant"}},
)
def get_restaurant(
    restaurant_id: int, session: DbSession, viewer: OptionalCurrentUser
) -> RestaurantResponse:
    """Retrieve one restaurant.

    A deactivated restaurant is a 404 for customers, identical to one that
    never existed. Distinguishing the two would tell a customer that a hidden
    restaurant exists, which is information they have no use for.
    """
    restaurant = CatalogService(session).get_restaurant(restaurant_id, as_staff=_is_staff(viewer))

    return RestaurantResponse.model_validate(restaurant)


@router.get(
    "/{restaurant_id}/menu",
    response_model=list[MenuItemResponse],
    summary="List a restaurant's menu",
    responses={404: {"description": "No such restaurant"}},
)
def list_menu(
    restaurant_id: int,
    session: DbSession,
    viewer: OptionalCurrentUser,
    include_unavailable: Annotated[
        bool, Query(description="Staff only. Ignored for other callers.")
    ] = False,
) -> list[MenuItemResponse]:
    """List the items on a restaurant's menu.

    Customers see only what can actually be ordered. `include_unavailable` is
    honoured for staff alone -- otherwise it would be a one-parameter bypass
    letting a customer order sold-out food. For a non-staff caller the flag is
    ignored rather than rejected, because to them it simply has no meaning.
    """
    items = CatalogService(session).list_menu(
        restaurant_id,
        include_unavailable=include_unavailable,
        as_staff=_is_staff(viewer),
    )

    return [MenuItemResponse.model_validate(item) for item in items]


@router.post(
    "",
    response_model=RestaurantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a restaurant (staff only)",
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Requires the admin role"},
    },
)
def create_restaurant(
    payload: RestaurantCreateRequest, session: DbSession, _: CurrentAdmin
) -> RestaurantResponse:
    """Create a restaurant.

    The `_: CurrentAdmin` parameter is the authorisation. It is unused by the
    body on purpose -- the handler does not need to know who the admin is, only
    that the caller is one -- but its presence is what makes the requirement
    visible in the signature and in the generated schema.
    """
    restaurant = CatalogService(session).create_restaurant(
        name=payload.name, description=payload.description
    )

    return RestaurantResponse.model_validate(restaurant)


@router.patch(
    "/{restaurant_id}",
    response_model=RestaurantResponse,
    summary="Update a restaurant (staff only)",
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Requires the admin role"},
        404: {"description": "No such restaurant"},
    },
)
def update_restaurant(
    restaurant_id: int,
    payload: RestaurantUpdateRequest,
    session: DbSession,
    _: CurrentAdmin,
) -> RestaurantResponse:
    """Partially update a restaurant.

    `exclude_unset=True` is what makes this a PATCH rather than a disguised
    PUT: only fields the client actually sent are forwarded, so omitting the
    description leaves it untouched instead of nulling it.
    """
    restaurant = CatalogService(session).update_restaurant(
        restaurant_id, payload.model_dump(exclude_unset=True)
    )

    return RestaurantResponse.model_validate(restaurant)


@router.post(
    "/{restaurant_id}/menu",
    response_model=MenuItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add an item to a menu (staff only)",
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Requires the admin role"},
        404: {"description": "No such restaurant"},
    },
)
def add_menu_item(
    restaurant_id: int,
    payload: MenuItemCreateRequest,
    session: DbSession,
    _: CurrentAdmin,
) -> MenuItemResponse:
    """Add an item to a restaurant's menu."""
    menu_item = CatalogService(session).add_menu_item(
        restaurant_id,
        name=payload.name,
        description=payload.description,
        price_cents=payload.price_cents,
        is_available=payload.is_available,
    )

    return MenuItemResponse.model_validate(menu_item)
