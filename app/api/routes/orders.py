"""Order routes: placement, tracking, and staff-driven status transitions."""

from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentAdmin, CurrentUser, DbSession
from app.domain.order_state import OrderStatus
from app.schemas.order import OrderCreateRequest, OrderResponse, OrderStatusUpdateRequest
from app.services.order import OrderService

router = APIRouter(prefix="/orders", tags=["orders"])

PageLimit = Annotated[int, Query(ge=1, le=100, description="Maximum results to return.")]
PageOffset = Annotated[int, Query(ge=0, description="Results to skip.")]


@router.post(
    "",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Place an order",
    responses={
        401: {"description": "Authentication required"},
        404: {"description": "No such restaurant or menu item"},
        409: {"description": "An item is unavailable or belongs to another restaurant"},
    },
)
def place_order(
    payload: OrderCreateRequest, session: DbSession, customer: CurrentUser
) -> OrderResponse:
    """Place an order for the authenticated customer.

    The customer is taken from the TOKEN, never from the request body. A
    `customer_id` field would let anyone place orders in someone else's name,
    and the schema does not have one.

    Prices are read from the menu on the server. The request says what to buy,
    not what to pay.
    """
    order = OrderService(session).place_order(
        customer=customer,
        restaurant_id=payload.restaurant_id,
        requested_lines=payload.items,
    )

    return OrderResponse.model_validate(order)


@router.get(
    "",
    response_model=list[OrderResponse],
    summary="List orders",
    responses={401: {"description": "Authentication required"}},
)
def list_orders(
    session: DbSession,
    requester: CurrentUser,
    limit: PageLimit = 20,
    offset: PageOffset = 0,
    order_status: Annotated[
        OrderStatus | None, Query(alias="status", description="Filter by lifecycle status.")
    ] = None,
) -> list[OrderResponse]:
    """List orders visible to the caller.

    One route serves both audiences, and the service decides what each may
    see: customers get their own orders, staff get every order. Two separate
    endpoints would duplicate pagination, filtering and serialisation to
    express a difference of one predicate.

    The `status` filter is what a staff dashboard runs constantly -- "show me
    everything still pending" -- and is the reason the column is indexed.
    """
    orders = OrderService(session).list_orders(
        requester=requester, limit=limit, offset=offset, status=order_status
    )

    return [OrderResponse.model_validate(order) for order in orders]


@router.get(
    "/{order_id}",
    response_model=OrderResponse,
    summary="Track an order",
    responses={
        401: {"description": "Authentication required"},
        404: {"description": "No such order, or it belongs to another customer"},
    },
)
def get_order(order_id: int, session: DbSession, requester: CurrentUser) -> OrderResponse:
    """Retrieve a single order, including its current status.

    This is the tracking endpoint of the brief. Another customer's order
    returns 404 rather than 403: a 403 would confirm the order exists, which
    would turn sequential ids into a way to measure the platform's volume.
    """
    order = OrderService(session).get_order(order_id, requester=requester)

    return OrderResponse.model_validate(order)


@router.patch(
    "/{order_id}/status",
    response_model=OrderResponse,
    summary="Advance an order's status (staff only)",
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Requires the admin role"},
        404: {"description": "No such order"},
        409: {"description": "The transition is not permitted from the current status"},
    },
)
def update_order_status(
    order_id: int,
    payload: OrderStatusUpdateRequest,
    session: DbSession,
    _: CurrentAdmin,
) -> OrderResponse:
    """Move an order to a new status.

    Staff only. A customer able to call this could mark their own order
    delivered when it never arrived, or accepted when the restaurant never
    agreed to make it.

    Two distinct validations happen, in order and with different outcomes: an
    unrecognised status value is rejected as 422 by the schema, while a
    recognised but unreachable one is rejected as 409 by the state machine. The
    409 body names the transitions that ARE permitted, so the rejection is
    usable guidance rather than a dead end.
    """
    order = OrderService(session).update_status(order_id, new_status=payload.status)

    return OrderResponse.model_validate(order)
