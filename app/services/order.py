"""Placing, tracking and advancing orders.

This module is where the two halves of the system meet: the catalogue supplies
prices, and the state machine governs what may happen to an order afterwards.
"""

import logging

from sqlalchemy.orm import Session

from app.domain.errors import ConflictError, NotFoundError
from app.domain.order_state import OrderStatus, assert_can_transition
from app.domain.roles import UserRole
from app.models.order import Order, OrderItem
from app.models.user import User
from app.repositories.order import OrderRepository
from app.repositories.restaurant import MenuItemRepository, RestaurantRepository
from app.schemas.order import OrderLineRequest

logger = logging.getLogger(__name__)


class OrderNotFoundError(NotFoundError):
    """Raised when an order does not exist, or is not visible to the caller.

    The two cases are deliberately indistinguishable. Reporting 403 for
    someone else's order would confirm that an order with that id exists,
    turning sequential identifiers into a way to measure the platform's order
    volume. To a customer, another person's order and a nonexistent one are
    the same thing.
    """


class MenuItemUnavailableError(ConflictError):
    """Raised when a requested item cannot currently be ordered.

    A conflict (409), not a validation error: the request is well-formed and
    the item genuinely exists. It simply cannot be bought right now, and the
    same request may well succeed tomorrow.
    """


class OrderService:
    """Order placement, retrieval and lifecycle transitions."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._orders = OrderRepository(session)
        self._restaurants = RestaurantRepository(session)
        self._menu_items = MenuItemRepository(session)

    def place_order(
        self, *, customer: User, restaurant_id: int, requested_lines: list[OrderLineRequest]
    ) -> Order:
        """Place an order on behalf of a customer.

        The whole method is one transaction, committed once at the end. That is
        what makes a basket atomic: if the last line turns out to be sold out,
        NOTHING is written. A partially created order would charge the customer
        for some of what they asked for and give them no way to know until it
        arrived.

        PRICES ARE READ HERE, from the menu, on the server. Nothing in the
        request influences the amount charged.

        Raises:
            OrderNotFoundError: if the restaurant does not exist or is inactive.
            MenuItemUnavailableError: if an item is unavailable or belongs to a
                different restaurant.
        """
        restaurant = self._restaurants.get_by_id(restaurant_id)

        if restaurant is None:
            # Inactive restaurants are excluded by the repository default, so
            # ordering from a closed restaurant is indistinguishable from
            # ordering from one that does not exist. Both are equally
            # impossible, and the customer needs no finer detail.
            raise OrderNotFoundError(f"Restaurant {restaurant_id} was not found.")

        lines: list[OrderItem] = []
        total_cents = 0

        for requested in requested_lines:
            menu_item = self._menu_items.get_by_id(requested.menu_item_id)

            if menu_item is None:
                raise OrderNotFoundError(f"Menu item {requested.menu_item_id} was not found.")

            if menu_item.restaurant_id != restaurant_id:
                # An order belongs to exactly one restaurant. A basket spanning
                # two kitchens has no coherent answer to who cooks it, who
                # delivers it, or which of them may accept it.
                raise MenuItemUnavailableError(
                    f"Menu item {menu_item.id} does not belong to restaurant {restaurant_id}."
                )

            if not menu_item.is_available:
                raise MenuItemUnavailableError(f"'{menu_item.name}' is currently unavailable.")

            # THE SNAPSHOT. The name and unit price are COPIED onto the line
            # rather than referenced through the menu item. This is what makes
            # the order an immutable record of what was agreed: a later price
            # change cannot rewrite the value of an order already placed.
            lines.append(
                OrderItem(
                    menu_item_id=menu_item.id,
                    item_name=menu_item.name,
                    unit_price_cents=menu_item.price_cents,
                    quantity=requested.quantity,
                )
            )
            total_cents += menu_item.price_cents * requested.quantity

        order = self._orders.add(
            customer_id=customer.id,
            restaurant_id=restaurant_id,
            total_cents=total_cents,
            lines=lines,
        )
        self._session.commit()

        logger.info(
            "Order placed: id=%s customer_id=%s restaurant_id=%s total_cents=%s",
            order.id,
            customer.id,
            restaurant_id,
            total_cents,
        )

        return order

    def get_order(self, order_id: int, *, requester: User) -> Order:
        """Return an order the requester is entitled to see.

        The ownership rule lives HERE rather than in the route, because it is a
        business rule about who an order belongs to, not a transport concern.
        Any future caller -- a worker, a support tool -- gets the same
        enforcement for free.

        Raises:
            OrderNotFoundError: if it does not exist, or belongs to someone
                else and the requester is not staff.
        """
        order = self._orders.get_by_id(order_id)

        if order is None:
            raise OrderNotFoundError(f"Order {order_id} was not found.")

        if requester.role is not UserRole.ADMIN and order.customer_id != requester.id:
            logger.warning(
                "Order access denied: order_id=%s requested by user_id=%s",
                order_id,
                requester.id,
            )
            raise OrderNotFoundError(f"Order {order_id} was not found.")

        return order

    def list_orders(
        self,
        *,
        requester: User,
        limit: int,
        offset: int,
        status: OrderStatus | None = None,
    ) -> list[Order]:
        """List the orders the requester is entitled to see.

        Staff see everything, which is what a dispatch dashboard requires.
        Customers see only their own -- enforced by calling a different
        repository method rather than by filtering afterwards, so there is no
        moment at which another customer's rows are loaded at all.
        """
        if requester.role is UserRole.ADMIN:
            return self._orders.list_all(limit=limit, offset=offset, status=status)

        return self._orders.list_for_customer(
            requester.id, limit=limit, offset=offset, status=status
        )

    def update_status(self, order_id: int, *, new_status: OrderStatus) -> Order:
        """Advance an order to a new status. Staff only, enforced at the route.

        The row is locked for the duration of the read-decide-write sequence.
        Without that lock, two staff members acting simultaneously would both
        read the old status, both find their transition legal, and both write
        -- one update silently lost and the audit trail showing a step that
        never happened.

        The transition itself is delegated to the domain state machine. This
        method contains no rule about which status may follow which, and that
        is the point: the rules live in one place, tested exhaustively, and
        every entry point consults the same table.

        Raises:
            OrderNotFoundError: if no such order exists.
            InvalidOrderTransition: if the move is not permitted from the
                order's current status. Rendered as 409 by the API layer.
        """
        order = self._orders.get_for_update(order_id)

        if order is None:
            raise OrderNotFoundError(f"Order {order_id} was not found.")

        previous_status = order.status

        # Raises before anything is mutated, so a rejected transition leaves
        # the row exactly as it was.
        assert_can_transition(previous_status, new_status)

        order.status = new_status
        self._session.commit()

        logger.info("Order status changed: id=%s %s -> %s", order.id, previous_status, new_status)

        return order
