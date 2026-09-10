"""Persistence operations for restaurants and menu items."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.restaurant import MenuItem, Restaurant


class RestaurantRepository:
    """Queries and writes against the `restaurants` table."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_id(self, restaurant_id: int, *, include_inactive: bool = False) -> Restaurant | None:
        """Return a restaurant, or None.

        `include_inactive` defaults to False so that the SAFE behaviour is the
        default one. A caller that forgets the flag gets the customer-visible
        view; it must opt in to see deactivated rows. Defaults that fail closed
        are worth more than defaults that are convenient.
        """
        restaurant = self._session.get(Restaurant, restaurant_id)

        if restaurant is None:
            return None

        if not include_inactive and not restaurant.is_active:
            return None

        return restaurant

    def list(self, *, limit: int, offset: int, include_inactive: bool = False) -> list[Restaurant]:
        """Return a page of restaurants.

        `limit` and `offset` are required parameters rather than defaulted
        ones. An unbounded list endpoint is a latent outage: it behaves
        perfectly against seed data and falls over once the table grows,
        straining the database, the serialiser and the client at once. Forcing
        the caller to state a bound means the omission cannot happen silently.

        Ordered by name so pagination is stable. Without an explicit ORDER BY,
        PostgreSQL may return rows in any order, and two requests for the same
        page can then show the same row twice or skip one entirely.
        """
        statement = select(Restaurant)

        if not include_inactive:
            statement = statement.where(Restaurant.is_active.is_(True))

        statement = statement.order_by(Restaurant.name, Restaurant.id).limit(limit).offset(offset)

        return list(self._session.execute(statement).scalars())

    def add(self, *, name: str, description: str | None) -> Restaurant:
        """Insert a restaurant and return it with its generated id."""
        restaurant = Restaurant(name=name, description=description)

        self._session.add(restaurant)
        self._session.flush()

        return restaurant


class MenuItemRepository:
    """Queries and writes against the `menu_items` table."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_id(self, menu_item_id: int) -> MenuItem | None:
        """Return a menu item, or None.

        Unfiltered by availability on purpose: this is used by staff to edit
        items and by the ordering service to check whether an item may be
        ordered. Both need to distinguish "does not exist" from "exists but is
        unavailable", and filtering here would collapse the two.
        """
        return self._session.get(MenuItem, menu_item_id)

    def list_for_restaurant(
        self, restaurant_id: int, *, include_unavailable: bool = False
    ) -> list[MenuItem]:
        """Return a restaurant's menu.

        Availability filtering happens HERE rather than in the caller. If each
        endpoint filtered for itself, one of them would eventually forget, and
        the symptom would be customers ordering food that cannot be made.
        """
        statement = select(MenuItem).where(MenuItem.restaurant_id == restaurant_id)

        if not include_unavailable:
            statement = statement.where(MenuItem.is_available.is_(True))

        statement = statement.order_by(MenuItem.name, MenuItem.id)

        return list(self._session.execute(statement).scalars())

    def add(
        self,
        *,
        restaurant_id: int,
        name: str,
        description: str | None,
        price_cents: int,
        is_available: bool,
    ) -> MenuItem:
        """Insert a menu item and return it with its generated id."""
        menu_item = MenuItem(
            restaurant_id=restaurant_id,
            name=name,
            description=description,
            price_cents=price_cents,
            is_available=is_available,
        )

        self._session.add(menu_item)
        self._session.flush()

        return menu_item

    def delete(self, menu_item: MenuItem) -> None:
        """Remove a menu item.

        Succeeds only while nothing references it. The `ondelete="RESTRICT"`
        rule on order lines makes PostgreSQL refuse to delete an item that has
        ever been ordered, which is what keeps historical orders meaningful.
        Staff withdraw a sold item from sale with `is_available` instead.
        """
        self._session.delete(menu_item)
        self._session.flush()
