"""End-to-end tests for browsing restaurants and managing menus.

Two audiences share these resources and must be kept strictly apart:

*   CUSTOMERS browse. Reads are public, and what they can see is curated --
    an inactive restaurant is not on the list, and neither is a sold-out dish
    on a menu.
*   STAFF manage. Every write requires the admin role, and every one of those
    routes is asserted below to reject both an anonymous caller and an
    authenticated customer.

The authorisation tests matter more than the happy paths. A missing write
endpoint is noticed immediately; a write endpoint missing its role check works
perfectly and is noticed only when a customer edits a menu.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.restaurant import MenuItem
from tests import factories

pytestmark = pytest.mark.integration

RESTAURANTS_URL = "/api/v1/restaurants"


def auth_header(client: TestClient, email: str) -> dict[str, str]:
    """Log in and return an Authorization header."""
    response = client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": factories.DEFAULT_PASSWORD},
    )
    assert response.status_code == 200, response.text

    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture()
def admin_headers(client: TestClient, db_session: Session) -> dict[str, str]:
    admin = factories.create_admin(db_session, email="staff@lateral.test")

    return auth_header(client, admin.email)


@pytest.fixture()
def customer_headers(client: TestClient, db_session: Session) -> dict[str, str]:
    customer = factories.create_user(db_session, email="customer@lateral.test")

    return auth_header(client, customer.email)


class TestBrowsingRestaurants:
    """Customer-facing reads."""

    def test_lists_restaurants(self, client: TestClient, db_session: Session) -> None:
        factories.create_restaurant(db_session, name="Trattoria Uno")
        factories.create_restaurant(db_session, name="Sushi Due")

        response = client.get(RESTAURANTS_URL)

        assert response.status_code == 200
        names = {item["name"] for item in response.json()}
        assert {"Trattoria Uno", "Sushi Due"} <= names

    def test_browsing_does_not_require_authentication(self, client: TestClient) -> None:
        """Menus are marketing material: requiring a login to see them would
        mean nobody can decide whether the service is worth signing up for."""
        assert client.get(RESTAURANTS_URL).status_code == 200

    def test_inactive_restaurants_are_hidden(self, client: TestClient, db_session: Session) -> None:
        """A closed restaurant must not be offered.

        Showing it produces orders nobody will cook. The row is kept -- its
        menu and order history stay intact -- but it is filtered from the
        customer's view.
        """
        factories.create_restaurant(db_session, name="Closed Forever", is_active=False)

        names = {item["name"] for item in client.get(RESTAURANTS_URL).json()}

        assert "Closed Forever" not in names

    def test_returns_a_single_restaurant(self, client: TestClient, db_session: Session) -> None:
        restaurant = factories.create_restaurant(db_session, name="Solo Dining")

        response = client.get(f"{RESTAURANTS_URL}/{restaurant.id}")

        assert response.status_code == 200
        assert response.json()["name"] == "Solo Dining"

    def test_unknown_restaurant_returns_404(self, client: TestClient) -> None:
        assert client.get(f"{RESTAURANTS_URL}/999999").status_code == 404

    def test_results_are_paginated(self, client: TestClient, db_session: Session) -> None:
        """Unbounded list endpoints are a latent outage.

        They work perfectly with the seed data everyone develops against and
        fall over once the table is large -- and they fail on the database, the
        serialiser and the client simultaneously. A default limit means the
        endpoint cannot be the cause.
        """
        for index in range(5):
            factories.create_restaurant(db_session, name=f"Restaurant {index}")

        response = client.get(RESTAURANTS_URL, params={"limit": 2})

        assert response.status_code == 200
        assert len(response.json()) == 2

    def test_rejects_an_excessive_page_size(self, client: TestClient) -> None:
        """The cap is enforced, not merely suggested.

        Without an upper bound, `?limit=1000000` reintroduces exactly the
        problem pagination was added to prevent.
        """
        assert client.get(RESTAURANTS_URL, params={"limit": 10_000}).status_code == 422


class TestBrowsingMenus:
    def test_returns_the_menu_of_a_restaurant(
        self, client: TestClient, db_session: Session
    ) -> None:
        restaurant = factories.create_restaurant(db_session)
        factories.create_menu_item(db_session, restaurant=restaurant, name="Margherita")

        response = client.get(f"{RESTAURANTS_URL}/{restaurant.id}/menu")

        assert response.status_code == 200
        assert [item["name"] for item in response.json()] == ["Margherita"]

    def test_unavailable_items_are_hidden_from_customers(
        self, client: TestClient, db_session: Session
    ) -> None:
        """A sold-out dish must not be orderable.

        Kept in the database rather than deleted, because historical orders
        reference it -- see the RESTRICT rule on order lines.
        """
        restaurant = factories.create_restaurant(db_session)
        factories.create_menu_item(
            db_session, restaurant=restaurant, name="Sold Out", is_available=False
        )

        names = [
            item["name"] for item in client.get(f"{RESTAURANTS_URL}/{restaurant.id}/menu").json()
        ]

        assert "Sold Out" not in names

    def test_prices_are_returned_in_cents(self, client: TestClient, db_session: Session) -> None:
        """The wire format carries integer cents, never a float.

        Serialising money as a float would reintroduce binary rounding error
        at the API boundary, after the database had carefully avoided it.
        """
        restaurant = factories.create_restaurant(db_session)
        factories.create_menu_item(db_session, restaurant=restaurant, price_cents=1050)

        item = client.get(f"{RESTAURANTS_URL}/{restaurant.id}/menu").json()[0]

        assert item["price_cents"] == 1050
        assert isinstance(item["price_cents"], int)

    def test_menu_of_an_unknown_restaurant_returns_404(self, client: TestClient) -> None:
        assert client.get(f"{RESTAURANTS_URL}/999999/menu").status_code == 404


class TestMenuManagementRequiresAdmin:
    """The authorisation boundary. Every write, against every caller."""

    def test_anonymous_cannot_create_a_restaurant(self, client: TestClient) -> None:
        response = client.post(RESTAURANTS_URL, json={"name": "Pirate Kitchen"})

        assert response.status_code == 401

    def test_customer_cannot_create_a_restaurant(
        self, client: TestClient, customer_headers: dict[str, str]
    ) -> None:
        response = client.post(
            RESTAURANTS_URL, json={"name": "Pirate Kitchen"}, headers=customer_headers
        )

        assert response.status_code == 403

    def test_customer_cannot_add_a_menu_item(
        self, client: TestClient, db_session: Session, customer_headers: dict[str, str]
    ) -> None:
        restaurant = factories.create_restaurant(db_session)

        response = client.post(
            f"{RESTAURANTS_URL}/{restaurant.id}/menu",
            json={"name": "Free Pizza", "price_cents": 0},
            headers=customer_headers,
        )

        assert response.status_code == 403

    def test_customer_cannot_update_a_menu_item(
        self, client: TestClient, db_session: Session, customer_headers: dict[str, str]
    ) -> None:
        menu_item = factories.create_menu_item(db_session)

        response = client.patch(
            f"/api/v1/menu-items/{menu_item.id}",
            json={"price_cents": 1},
            headers=customer_headers,
        )

        assert response.status_code == 403

    def test_customer_cannot_change_a_price(
        self, client: TestClient, db_session: Session, customer_headers: dict[str, str]
    ) -> None:
        """The assertion that matters commercially: a rejected request must
        also leave the data untouched."""
        menu_item = factories.create_menu_item(db_session, price_cents=1050)

        client.patch(
            f"/api/v1/menu-items/{menu_item.id}",
            json={"price_cents": 1},
            headers=customer_headers,
        )
        db_session.refresh(menu_item)

        assert menu_item.price_cents == 1050


class TestMenuManagementAsAdmin:
    def test_creates_a_restaurant(self, client: TestClient, admin_headers: dict[str, str]) -> None:
        response = client.post(
            RESTAURANTS_URL,
            json={"name": "Osteria Nuova", "description": "Roman classics."},
            headers=admin_headers,
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["name"] == "Osteria Nuova"
        assert body["is_active"] is True

    def test_adds_a_menu_item(
        self, client: TestClient, db_session: Session, admin_headers: dict[str, str]
    ) -> None:
        restaurant = factories.create_restaurant(db_session)

        response = client.post(
            f"{RESTAURANTS_URL}/{restaurant.id}/menu",
            json={"name": "Carbonara", "description": "Guanciale, pecorino.", "price_cents": 1400},
            headers=admin_headers,
        )

        assert response.status_code == 201, response.text
        assert response.json()["price_cents"] == 1400

    def test_rejects_a_negative_price(
        self, client: TestClient, db_session: Session, admin_headers: dict[str, str]
    ) -> None:
        """Rejected by the schema, before it ever reaches the CHECK constraint.

        Both layers exist deliberately: validation gives the caller a clear
        422, and the constraint guarantees integrity for every other path into
        the data.
        """
        restaurant = factories.create_restaurant(db_session)

        response = client.post(
            f"{RESTAURANTS_URL}/{restaurant.id}/menu",
            json={"name": "Impossible", "price_cents": -100},
            headers=admin_headers,
        )

        assert response.status_code == 422

    def test_updates_a_menu_item_price(
        self, client: TestClient, db_session: Session, admin_headers: dict[str, str]
    ) -> None:
        menu_item = factories.create_menu_item(db_session, price_cents=1050)

        response = client.patch(
            f"/api/v1/menu-items/{menu_item.id}",
            json={"price_cents": 1200},
            headers=admin_headers,
        )

        assert response.status_code == 200
        assert response.json()["price_cents"] == 1200

    def test_partial_update_leaves_other_fields_untouched(
        self, client: TestClient, db_session: Session, admin_headers: dict[str, str]
    ) -> None:
        """PATCH semantics: an omitted field means "leave it alone".

        The distinction matters. If omission were treated as null, updating a
        price would silently erase the description -- which is exactly why the
        update schema distinguishes "absent" from "explicitly null".
        """
        menu_item = factories.create_menu_item(
            db_session, name="Margherita", description="Tomato, mozzarella.", price_cents=1050
        )

        client.patch(
            f"/api/v1/menu-items/{menu_item.id}",
            json={"price_cents": 1100},
            headers=admin_headers,
        )
        db_session.refresh(menu_item)

        assert menu_item.price_cents == 1100
        assert menu_item.name == "Margherita"
        assert menu_item.description == "Tomato, mozzarella."

    def test_marks_an_item_unavailable(
        self, client: TestClient, db_session: Session, admin_headers: dict[str, str]
    ) -> None:
        """The everyday "sold out" operation staff actually need."""
        menu_item = factories.create_menu_item(db_session)

        response = client.patch(
            f"/api/v1/menu-items/{menu_item.id}",
            json={"is_available": False},
            headers=admin_headers,
        )

        assert response.status_code == 200
        assert response.json()["is_available"] is False

    def test_admin_sees_unavailable_items_on_the_management_view(
        self, client: TestClient, db_session: Session, admin_headers: dict[str, str]
    ) -> None:
        """Staff must see the whole menu, including what is sold out.

        The customer view filters those out; the management view cannot, or an
        item could never be marked available again through the API.
        """
        restaurant = factories.create_restaurant(db_session)
        factories.create_menu_item(
            db_session, restaurant=restaurant, name="Hidden", is_available=False
        )

        response = client.get(
            f"{RESTAURANTS_URL}/{restaurant.id}/menu",
            params={"include_unavailable": True},
            headers=admin_headers,
        )

        assert [item["name"] for item in response.json()] == ["Hidden"]

    def test_customer_cannot_request_unavailable_items(
        self, client: TestClient, db_session: Session, customer_headers: dict[str, str]
    ) -> None:
        """The parameter is not a loophole: only staff may use it."""
        restaurant = factories.create_restaurant(db_session)
        factories.create_menu_item(
            db_session, restaurant=restaurant, name="Hidden", is_available=False
        )

        response = client.get(
            f"{RESTAURANTS_URL}/{restaurant.id}/menu",
            params={"include_unavailable": True},
            headers=customer_headers,
        )

        assert response.status_code == 200
        assert response.json() == []

    def test_deactivates_a_restaurant(
        self, client: TestClient, db_session: Session, admin_headers: dict[str, str]
    ) -> None:
        restaurant = factories.create_restaurant(db_session)

        response = client.patch(
            f"{RESTAURANTS_URL}/{restaurant.id}",
            json={"is_active": False},
            headers=admin_headers,
        )

        assert response.status_code == 200
        assert response.json()["is_active"] is False

    def test_adding_an_item_to_an_unknown_restaurant_returns_404(
        self, client: TestClient, admin_headers: dict[str, str]
    ) -> None:
        response = client.post(
            f"{RESTAURANTS_URL}/999999/menu",
            json={"name": "Orphan", "price_cents": 100},
            headers=admin_headers,
        )

        assert response.status_code == 404

    def test_deleted_item_is_removed_from_the_menu(
        self, client: TestClient, db_session: Session, admin_headers: dict[str, str]
    ) -> None:
        """Deletion is permitted only while nothing references the item.

        An item that has been ordered cannot be deleted -- the RESTRICT rule
        protects the historical record -- and staff use `is_available` instead.
        """
        menu_item = factories.create_menu_item(db_session)

        response = client.delete(f"/api/v1/menu-items/{menu_item.id}", headers=admin_headers)

        assert response.status_code == 204
        assert db_session.get(MenuItem, menu_item.id) is None
