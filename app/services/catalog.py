"""Browsing and management of restaurants and menus."""

from typing import Any

from sqlalchemy.orm import Session

from app.domain.errors import NotFoundError
from app.models.restaurant import MenuItem, Restaurant
from app.repositories.restaurant import MenuItemRepository, RestaurantRepository


class RestaurantNotFoundError(NotFoundError):
    """Raised when a restaurant does not exist, or is not visible to the caller."""


class MenuItemNotFoundError(NotFoundError):
    """Raised when a menu item does not exist."""


class CatalogService:
    """The restaurant and menu catalogue.

    One service covers both because they are a single aggregate: a menu has no
    independent existence, its lifecycle is bound to its restaurant, and almost
    every menu operation begins by establishing that the restaurant exists.
    Splitting them would mean two services calling each other for every write,
    which is coupling with extra ceremony rather than separation.

    Read methods take an `as_staff` flag rather than a user object. The service
    is told what level of visibility to apply; it does not inspect roles or
    make authorisation decisions. Authorisation belongs at the route boundary,
    where it is declarative and visible in the schema -- see app/api/deps.py.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._restaurants = RestaurantRepository(session)
        self._menu_items = MenuItemRepository(session)

    # --- Reads ---------------------------------------------------------------

    def list_restaurants(
        self, *, limit: int, offset: int, as_staff: bool = False
    ) -> list[Restaurant]:
        """Return a page of restaurants.

        Customers see only active ones. Staff see everything, because a
        deactivated restaurant that cannot be listed also cannot be
        reactivated through the API.
        """
        return self._restaurants.list(limit=limit, offset=offset, include_inactive=as_staff)

    def get_restaurant(self, restaurant_id: int, *, as_staff: bool = False) -> Restaurant:
        """Return a single restaurant.

        Raises:
            RestaurantNotFoundError: if it does not exist, or is inactive and
                the caller is not staff. Both cases produce the same 404 --
                a customer has no business learning that a hidden restaurant
                exists.
        """
        restaurant = self._restaurants.get_by_id(restaurant_id, include_inactive=as_staff)

        if restaurant is None:
            raise RestaurantNotFoundError(f"Restaurant {restaurant_id} was not found.")

        return restaurant

    def list_menu(
        self, restaurant_id: int, *, include_unavailable: bool = False, as_staff: bool = False
    ) -> list[MenuItem]:
        """Return a restaurant's menu.

        `include_unavailable` is honoured ONLY for staff. Trusting it from any
        caller would make it a trivial bypass: a customer could append a query
        parameter and order sold-out food. The flag is silently downgraded
        rather than rejected, because to a customer the parameter simply has no
        meaning.
        """
        self.get_restaurant(restaurant_id, as_staff=as_staff)

        return self._menu_items.list_for_restaurant(
            restaurant_id, include_unavailable=include_unavailable and as_staff
        )

    # --- Writes (staff only; enforced at the route) --------------------------

    def create_restaurant(self, *, name: str, description: str | None) -> Restaurant:
        """Create a restaurant."""
        restaurant = self._restaurants.add(name=name, description=description)
        self._session.commit()

        return restaurant

    def update_restaurant(self, restaurant_id: int, changes: dict[str, Any]) -> Restaurant:
        """Apply a partial update to a restaurant.

        `changes` contains only the fields the client actually sent -- the
        route builds it with `model_dump(exclude_unset=True)`. Assigning
        attribute by attribute from that dict is what gives PATCH its correct
        semantics: a field the client did not mention is never written, so
        updating a name cannot silently erase a description.
        """
        restaurant = self.get_restaurant(restaurant_id, as_staff=True)

        for field, value in changes.items():
            setattr(restaurant, field, value)

        self._session.commit()

        return restaurant

    def add_menu_item(
        self,
        restaurant_id: int,
        *,
        name: str,
        description: str | None,
        price_cents: int,
        is_available: bool,
    ) -> MenuItem:
        """Add an item to a restaurant's menu.

        Raises:
            RestaurantNotFoundError: if the restaurant does not exist. Checked
                explicitly so the caller gets a clear 404 rather than the
                foreign-key violation the database would otherwise raise.
        """
        self.get_restaurant(restaurant_id, as_staff=True)

        menu_item = self._menu_items.add(
            restaurant_id=restaurant_id,
            name=name,
            description=description,
            price_cents=price_cents,
            is_available=is_available,
        )
        self._session.commit()

        return menu_item

    def get_menu_item(self, menu_item_id: int) -> MenuItem:
        """Return a menu item.

        Raises:
            MenuItemNotFoundError: if it does not exist.
        """
        menu_item = self._menu_items.get_by_id(menu_item_id)

        if menu_item is None:
            raise MenuItemNotFoundError(f"Menu item {menu_item_id} was not found.")

        return menu_item

    def update_menu_item(self, menu_item_id: int, changes: dict[str, Any]) -> MenuItem:
        """Apply a partial update to a menu item.

        NOTE what this does NOT do: it does not touch any existing order.
        Changing a price here affects future orders only, because order lines
        snapshot the name and unit price at purchase time. That is the entire
        reason for the snapshot -- see app/models/order.py.
        """
        menu_item = self.get_menu_item(menu_item_id)

        for field, value in changes.items():
            setattr(menu_item, field, value)

        self._session.commit()

        return menu_item

    def delete_menu_item(self, menu_item_id: int) -> None:
        """Delete a menu item.

        Only possible while no order references it: PostgreSQL refuses
        otherwise, protecting the historical record. Staff withdraw an item
        that has been sold by setting `is_available` to false instead.
        """
        menu_item = self.get_menu_item(menu_item_id)

        self._menu_items.delete(menu_item)
        self._session.commit()
