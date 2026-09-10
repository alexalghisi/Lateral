"""Persistence operations for orders."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.order_state import OrderStatus
from app.models.order import Order, OrderItem


class OrderRepository:
    """Queries and writes against the `orders` and `order_items` tables."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_id(self, order_id: int) -> Order | None:
        """Return an order, or None."""
        return self._session.get(Order, order_id)

    def get_for_update(self, order_id: int) -> Order | None:
        """Return an order with its row locked until the transaction ends.

        ARCHITECTURAL DECISION -- pessimistic locking for status transitions.

        Advancing a status is read-then-write: read the current status, ask the
        state machine whether the move is legal, write the new one. Without a
        lock, two staff members acting at once both read `accepted`, both find
        the transition legal, and both write -- one update silently lost, and
        an order that skipped a step in the audit trail.

        `SELECT ... FOR UPDATE` makes the second transaction wait for the
        first, so it re-reads the already-updated status and the state machine
        correctly rejects it. The contention is negligible: the lock is held
        for the microseconds between reading and writing one row, and only
        between staff acting on the SAME order.
        """
        statement = select(Order).where(Order.id == order_id).with_for_update()

        return self._session.execute(statement).scalar_one_or_none()

    def list_for_customer(
        self, customer_id: int, *, limit: int, offset: int, status: OrderStatus | None = None
    ) -> list[Order]:
        """Return a page of one customer's orders, newest first.

        The customer filter is applied HERE rather than left to the caller.
        A caller that forgot it would return every customer's orders to
        whoever asked, which is the kind of defect that becomes a disclosure
        incident rather than a bug report.
        """
        statement = select(Order).where(Order.customer_id == customer_id)

        if status is not None:
            statement = statement.where(Order.status == status)

        statement = statement.order_by(Order.created_at.desc(), Order.id.desc())

        return list(self._session.execute(statement.limit(limit).offset(offset)).scalars())

    def list_all(
        self, *, limit: int, offset: int, status: OrderStatus | None = None
    ) -> list[Order]:
        """Return a page of every order, newest first. Staff only.

        Ordered by creation time descending because a staff dashboard is a
        work queue: the newest orders are the ones needing attention. The id
        is a tiebreaker so pagination stays stable when timestamps collide.
        """
        statement = select(Order)

        if status is not None:
            statement = statement.where(Order.status == status)

        statement = statement.order_by(Order.created_at.desc(), Order.id.desc())

        return list(self._session.execute(statement.limit(limit).offset(offset)).scalars())

    def add(
        self,
        *,
        customer_id: int,
        restaurant_id: int,
        total_cents: int,
        lines: list[OrderItem],
    ) -> Order:
        """Insert an order together with all of its lines.

        The lines are attached through the relationship rather than inserted
        separately, so SQLAlchemy emits them in one flush within one
        transaction. An order and its contents are meaningless apart: a
        half-written order is not a smaller order, it is corrupt data.
        """
        order = Order(
            customer_id=customer_id,
            restaurant_id=restaurant_id,
            status=OrderStatus.PENDING,
            total_cents=total_cents,
            items=lines,
        )

        self._session.add(order)
        self._session.flush()

        return order
