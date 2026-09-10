"""Orders and their line items."""

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.domain.order_state import OrderStatus
from app.models.mixins import IdentityMixin, TimestampMixin
from app.models.restaurant import MenuItem, Restaurant
from app.models.user import User


class Order(IdentityMixin, TimestampMixin, Base):
    """A customer's order against a single restaurant.

    Note the direction of the dependency: this module imports `OrderStatus`
    from the pure domain layer. The domain does not know that persistence
    exists. The lifecycle rules can therefore be reasoned about, and tested,
    without a database anywhere in sight.
    """

    __tablename__ = "orders"

    customer_id: Mapped[int] = mapped_column(
        # RESTRICT, not CASCADE. Deleting a customer must never silently erase
        # their order history: those rows are financial records. The database
        # refuses the delete, which forces the correct conversation about
        # anonymisation or archival rather than quietly destroying evidence.
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("restaurants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    status: Mapped[OrderStatus] = mapped_column(
        Enum(
            OrderStatus,
            name="order_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=OrderStatus.PENDING,
        server_default=OrderStatus.PENDING.value,
        # Indexed because staff dashboards filter by status constantly
        # ("show me everything pending"), and that query would otherwise
        # sequentially scan a table that only ever grows.
        index=True,
    )

    # A denormalised snapshot of the sum of the line items.
    #
    # ARCHITECTURAL DECISION -- deliberate, justified denormalisation.
    # Normally a derivable value should not be stored. Here it must be: it is
    # the amount the customer agreed to pay at a point in time. Recomputing it
    # later from current data would produce a different number the moment a
    # price changes, and "the total on the receipt does not match the total in
    # the system" is a customer-trust and audit problem, not a rounding detail.
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (CheckConstraint("total_cents >= 0", name="total_non_negative"),)

    customer: Mapped[User] = relationship()
    restaurant: Mapped[Restaurant] = relationship()

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Order id={self.id} status={self.status} total_cents={self.total_cents}>"


class OrderItem(IdentityMixin, TimestampMixin, Base):
    """One line of an order: a quantity of a menu item at an agreed price.

    ARCHITECTURAL DECISION -- prices and names are SNAPSHOTTED here.

    The obvious design stores only `menu_item_id` and reads the price through
    the relationship. It is wrong, and the bug it creates is invisible until
    it matters: when the restaurant raises a price, every historical order
    silently changes value. Last month's revenue report shifts. A refund is
    issued for an amount the customer never paid. If the item is renamed, old
    receipts describe food nobody ordered.

    Copying the name and unit price at the moment of purchase makes the order
    an immutable record of what was actually agreed. This is the standard
    treatment of transactional records in accounting systems, and the reason
    an invoice is a document rather than a query.
    """

    __tablename__ = "order_items"

    __table_args__ = (
        # A zero or negative quantity is not an order line, it is a data
        # corruption. Rejected at the storage layer so no code path can create
        # one, whatever the API layer does or fails to do.
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price_cents >= 0", name="unit_price_non_negative"),
    )

    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # RESTRICT: a menu item that appears in any order cannot be deleted, which
    # is what keeps the reference in the historical record meaningful. Staff
    # remove items from a live menu by clearing `is_available`, which is the
    # operation they actually want -- "stop selling this", not "erase that it
    # ever existed".
    menu_item_id: Mapped[int] = mapped_column(
        ForeignKey("menu_items.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # --- Snapshotted at purchase time; never updated afterwards --------------
    item_name: Mapped[str] = mapped_column(String(255), nullable=False)
    unit_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)

    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    order: Mapped[Order] = relationship(back_populates="items")
    menu_item: Mapped[MenuItem] = relationship()

    @property
    def subtotal_cents(self) -> int:
        """Line total.

        A derived property rather than a stored column: unlike the order total,
        this is a pure function of two values that are themselves already
        frozen, so it cannot drift and storing it would only create an
        opportunity for the three numbers to disagree.
        """
        return self.unit_price_cents * self.quantity

    def __repr__(self) -> str:
        return f"<OrderItem id={self.id} item_name={self.item_name!r} quantity={self.quantity}>"
