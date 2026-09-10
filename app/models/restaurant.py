"""Restaurants and their menu items."""

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import IdentityMixin, TimestampMixin


class Restaurant(IdentityMixin, TimestampMixin, Base):
    """A restaurant customers can browse and order from."""

    __tablename__ = "restaurants"

    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Whether the restaurant currently accepts orders. Distinct from deletion:
    # a temporarily closed restaurant must keep its menu and its order history.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    menu_items: Mapped[list["MenuItem"]] = relationship(
        back_populates="restaurant",
        # Deleting a restaurant deletes its menu. `delete-orphan` additionally
        # removes an item detached from its parent collection, so an item can
        # never linger pointing at nothing.
        cascade="all, delete-orphan",
        # lazy="selectin" issues ONE additional query for the whole collection
        # instead of one per parent row. The default ("select") produces the
        # N+1 query problem: listing 50 restaurants with their menus would emit
        # 51 queries. This is the single most common performance defect in ORM
        # code and it is worth pre-empting at the mapping.
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Restaurant id={self.id} name={self.name!r}>"


class MenuItem(IdentityMixin, TimestampMixin, Base):
    """A single purchasable item on a restaurant's menu."""

    __tablename__ = "menu_items"

    __table_args__ = (
        # Enforced by the DATABASE, not only by Pydantic. Validation at the API
        # boundary protects against well-behaved clients; a constraint protects
        # against every other path into the data -- a migration, a maintenance
        # script, a future service, a mistake in psql. Defence in depth applies
        # to data integrity exactly as it does to security.
        CheckConstraint("price_cents >= 0", name="price_non_negative"),
    )

    restaurant_id: Mapped[int] = mapped_column(
        # ondelete="CASCADE" instructs PostgreSQL itself, complementing the
        # ORM-level cascade above. The ORM rule only applies to objects loaded
        # into a session; the database rule applies to every deletion, however
        # it is issued.
        ForeignKey("restaurants.id", ondelete="CASCADE"),
        nullable=False,
        # Indexed because "all items for restaurant X" is the dominant query in
        # this system. PostgreSQL does not index foreign keys automatically.
        index=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ARCHITECTURAL DECISION -- money is stored as an integer number of cents.
    #
    # Never float: 0.1 + 0.2 != 0.3 in binary floating point, and applied to
    # money that discrepancy becomes a real accounting error that compounds
    # across a basket. NUMERIC would also be exact, but integer cents are
    # simpler to reason about, immune to rounding-mode surprises, and map
    # cleanly onto what every payment provider actually expects.
    #
    # The unit is encoded in the COLUMN NAME. `price` invites ambiguity about
    # whether 500 means five euros or five hundred; `price_cents` cannot be
    # misread.
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)

    # Whether the item can be ordered right now -- the everyday "sold out"
    # toggle staff need, without destroying the item or the history of orders
    # that reference it.
    is_available: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    restaurant: Mapped[Restaurant] = relationship(back_populates="menu_items")

    def __repr__(self) -> str:
        return f"<MenuItem id={self.id} name={self.name!r} price_cents={self.price_cents}>"
