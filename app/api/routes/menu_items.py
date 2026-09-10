"""Menu item routes addressed by item id.

Kept separate from the restaurant router because these operate on an item
directly, without needing its restaurant in the path. Nesting them as
`/restaurants/{id}/menu/{item_id}` would force every caller to carry a
redundant identifier and would invite the two to disagree -- with the API then
having to decide what an item id belonging to a different restaurant means.
"""

from fastapi import APIRouter, Response, status

from app.api.deps import CurrentAdmin, DbSession
from app.schemas.restaurant import MenuItemResponse, MenuItemUpdateRequest
from app.services.catalog import CatalogService

router = APIRouter(prefix="/menu-items", tags=["menu management"])


@router.patch(
    "/{menu_item_id}",
    response_model=MenuItemResponse,
    summary="Update a menu item (staff only)",
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Requires the admin role"},
        404: {"description": "No such menu item"},
    },
)
def update_menu_item(
    menu_item_id: int,
    payload: MenuItemUpdateRequest,
    session: DbSession,
    _: CurrentAdmin,
) -> MenuItemResponse:
    """Partially update a menu item.

    This is the endpoint staff use constantly, most often to flip
    `is_available` when a dish sells out.

    Changing a price here affects FUTURE orders only. Existing orders keep the
    price they were placed at, because order lines snapshot the unit price at
    purchase time -- without that, a price rise would retroactively rewrite
    what every past customer appears to have paid.
    """
    menu_item = CatalogService(session).update_menu_item(
        menu_item_id, payload.model_dump(exclude_unset=True)
    )

    return MenuItemResponse.model_validate(menu_item)


@router.delete(
    "/{menu_item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a menu item (staff only)",
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Requires the admin role"},
        404: {"description": "No such menu item"},
        409: {"description": "The item has been ordered and cannot be deleted"},
    },
)
def delete_menu_item(menu_item_id: int, session: DbSession, _: CurrentAdmin) -> Response:
    """Delete a menu item.

    Succeeds only while no order references the item. PostgreSQL refuses
    otherwise, which is what keeps historical orders meaningful; staff withdraw
    an item that has already been sold by setting `is_available` to false.

    204 with no body: there is nothing meaningful to return about a resource
    that no longer exists.
    """
    CatalogService(session).delete_menu_item(menu_item_id)

    return Response(status_code=status.HTTP_204_NO_CONTENT)
