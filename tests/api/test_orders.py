"""End-to-end tests for placing, tracking and advancing orders.

This is where the money is, in the literal sense: an order is a financial
record, and the two properties these tests defend are that its value is
computed by the server rather than supplied by the client, and that its
lifecycle cannot be driven anywhere the state machine forbids.

Ownership is the third theme. A customer must be able to track their own order
and must not be able to see anyone else's -- and the failure has to be
indistinguishable from an order that does not exist, or the endpoint becomes a
way to count how much business the platform is doing.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.domain.order_state import OrderStatus
from tests import factories

pytestmark = pytest.mark.integration

ORDERS_URL = "/api/v1/orders"


def auth_header(client: TestClient, email: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": factories.DEFAULT_PASSWORD},
    )
    assert response.status_code == 200, response.text

    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture()
def admin_headers(client: TestClient, db_session: Session) -> dict[str, str]:
    admin = factories.create_admin(db_session, email="orders.staff@lateral.test")

    return auth_header(client, admin.email)


@pytest.fixture()
def customer(client: TestClient, db_session: Session):  # type: ignore[no-untyped-def]
    return factories.create_user(db_session, email="orders.customer@lateral.test")


@pytest.fixture()
def customer_headers(client: TestClient, customer) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return auth_header(client, customer.email)


class TestPlacingAnOrder:
    def test_creates_a_pending_order(
        self, client: TestClient, db_session: Session, customer_headers: dict[str, str]
    ) -> None:
        """Every order starts as pending: the entry point of the lifecycle."""
        menu_item = factories.create_menu_item(db_session, price_cents=1050)

        response = client.post(
            ORDERS_URL,
            json={
                "restaurant_id": menu_item.restaurant_id,
                "items": [{"menu_item_id": menu_item.id, "quantity": 2}],
            },
            headers=customer_headers,
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["status"] == "pending"
        assert body["total_cents"] == 2100

    def test_the_total_is_computed_by_the_server(
        self, client: TestClient, db_session: Session, customer_headers: dict[str, str]
    ) -> None:
        """THE most important test in this file.

        The client sends what it wants to buy, never what it intends to pay.
        A request schema that accepted a price or a total would let anyone buy
        anything for one cent, and no amount of validation elsewhere would
        help: the server would be faithfully recording an amount the attacker
        chose.
        """
        first = factories.create_menu_item(db_session, price_cents=1000)
        second = factories.create_menu_item(
            db_session, restaurant=first.restaurant, name="Second", price_cents=250
        )

        response = client.post(
            ORDERS_URL,
            json={
                "restaurant_id": first.restaurant_id,
                "items": [
                    {"menu_item_id": first.id, "quantity": 2},
                    {"menu_item_id": second.id, "quantity": 3},
                ],
            },
            headers=customer_headers,
        )

        # 2 x 1000 + 3 x 250 = 2750
        assert response.json()["total_cents"] == 2750

    def test_a_client_supplied_price_is_rejected(
        self, client: TestClient, db_session: Session, customer_headers: dict[str, str]
    ) -> None:
        """Attempting to dictate the price is refused outright, not ignored."""
        menu_item = factories.create_menu_item(db_session, price_cents=1050)

        response = client.post(
            ORDERS_URL,
            json={
                "restaurant_id": menu_item.restaurant_id,
                "items": [{"menu_item_id": menu_item.id, "quantity": 1, "unit_price_cents": 1}],
                "total_cents": 1,
            },
            headers=customer_headers,
        )

        assert response.status_code == 422

    def test_line_items_snapshot_the_name_and_price(
        self, client: TestClient, db_session: Session, customer_headers: dict[str, str]
    ) -> None:
        menu_item = factories.create_menu_item(db_session, name="Margherita", price_cents=1050)

        response = client.post(
            ORDERS_URL,
            json={
                "restaurant_id": menu_item.restaurant_id,
                "items": [{"menu_item_id": menu_item.id, "quantity": 1}],
            },
            headers=customer_headers,
        )

        line = response.json()["items"][0]
        assert line["item_name"] == "Margherita"
        assert line["unit_price_cents"] == 1050

    def test_a_later_price_change_does_not_alter_the_placed_order(
        self, client: TestClient, db_session: Session, customer_headers: dict[str, str]
    ) -> None:
        """End-to-end proof of the snapshot, through the API rather than the ORM."""
        menu_item = factories.create_menu_item(db_session, price_cents=1000)

        created = client.post(
            ORDERS_URL,
            json={
                "restaurant_id": menu_item.restaurant_id,
                "items": [{"menu_item_id": menu_item.id, "quantity": 1}],
            },
            headers=customer_headers,
        ).json()

        menu_item.price_cents = 9999
        db_session.flush()

        fetched = client.get(f"{ORDERS_URL}/{created['id']}", headers=customer_headers).json()

        assert fetched["total_cents"] == 1000
        assert fetched["items"][0]["unit_price_cents"] == 1000

    def test_requires_authentication(self, client: TestClient, db_session: Session) -> None:
        menu_item = factories.create_menu_item(db_session)

        response = client.post(
            ORDERS_URL,
            json={
                "restaurant_id": menu_item.restaurant_id,
                "items": [{"menu_item_id": menu_item.id, "quantity": 1}],
            },
        )

        assert response.status_code == 401

    def test_rejects_an_unavailable_item(
        self, client: TestClient, db_session: Session, customer_headers: dict[str, str]
    ) -> None:
        """Ordering sold-out food produces an order nobody can cook."""
        menu_item = factories.create_menu_item(db_session, is_available=False)

        response = client.post(
            ORDERS_URL,
            json={
                "restaurant_id": menu_item.restaurant_id,
                "items": [{"menu_item_id": menu_item.id, "quantity": 1}],
            },
            headers=customer_headers,
        )

        assert response.status_code == 409

    def test_rejects_an_item_from_another_restaurant(
        self, client: TestClient, db_session: Session, customer_headers: dict[str, str]
    ) -> None:
        """An order belongs to exactly one restaurant.

        Without this check a basket could span two kitchens, and there would be
        no coherent answer to who is expected to cook it or deliver it.
        """
        ordered_from = factories.create_restaurant(db_session, name="Chosen")
        elsewhere = factories.create_menu_item(
            db_session, restaurant=factories.create_restaurant(db_session, name="Other")
        )

        response = client.post(
            ORDERS_URL,
            json={
                "restaurant_id": ordered_from.id,
                "items": [{"menu_item_id": elsewhere.id, "quantity": 1}],
            },
            headers=customer_headers,
        )

        assert response.status_code == 409

    def test_rejects_an_unknown_menu_item(
        self, client: TestClient, db_session: Session, customer_headers: dict[str, str]
    ) -> None:
        restaurant = factories.create_restaurant(db_session)

        response = client.post(
            ORDERS_URL,
            json={
                "restaurant_id": restaurant.id,
                "items": [{"menu_item_id": 999999, "quantity": 1}],
            },
            headers=customer_headers,
        )

        assert response.status_code == 404

    def test_rejects_an_empty_basket(
        self, client: TestClient, db_session: Session, customer_headers: dict[str, str]
    ) -> None:
        restaurant = factories.create_restaurant(db_session)

        response = client.post(
            ORDERS_URL,
            json={"restaurant_id": restaurant.id, "items": []},
            headers=customer_headers,
        )

        assert response.status_code == 422

    def test_rejects_a_non_positive_quantity(
        self, client: TestClient, db_session: Session, customer_headers: dict[str, str]
    ) -> None:
        menu_item = factories.create_menu_item(db_session)

        response = client.post(
            ORDERS_URL,
            json={
                "restaurant_id": menu_item.restaurant_id,
                "items": [{"menu_item_id": menu_item.id, "quantity": 0}],
            },
            headers=customer_headers,
        )

        assert response.status_code == 422

    def test_rejects_an_order_from_an_inactive_restaurant(
        self, client: TestClient, db_session: Session, customer_headers: dict[str, str]
    ) -> None:
        restaurant = factories.create_restaurant(db_session, is_active=False)
        menu_item = factories.create_menu_item(db_session, restaurant=restaurant)

        response = client.post(
            ORDERS_URL,
            json={
                "restaurant_id": restaurant.id,
                "items": [{"menu_item_id": menu_item.id, "quantity": 1}],
            },
            headers=customer_headers,
        )

        assert response.status_code == 404

    def test_nothing_is_persisted_when_one_line_is_invalid(
        self, client: TestClient, db_session: Session, customer_headers: dict[str, str]
    ) -> None:
        """ATOMICITY.

        A basket with one good line and one sold-out line must create no order
        at all -- not a partial one containing only what happened to be
        available. The customer would be charged for half of what they asked
        for and would have no idea until it arrived.
        """
        available = factories.create_menu_item(db_session, price_cents=1000)
        sold_out = factories.create_menu_item(
            db_session,
            restaurant=available.restaurant,
            name="Sold Out",
            is_available=False,
        )

        client.post(
            ORDERS_URL,
            json={
                "restaurant_id": available.restaurant_id,
                "items": [
                    {"menu_item_id": available.id, "quantity": 1},
                    {"menu_item_id": sold_out.id, "quantity": 1},
                ],
            },
            headers=customer_headers,
        )

        assert client.get(ORDERS_URL, headers=customer_headers).json() == []


class TestTrackingAnOrder:
    def test_a_customer_can_track_their_own_order(
        self, client: TestClient, db_session: Session, customer, customer_headers: dict[str, str]
    ) -> None:  # type: ignore[no-untyped-def]
        order = factories.create_order(db_session, customer=customer)

        response = client.get(f"{ORDERS_URL}/{order.id}", headers=customer_headers)

        assert response.status_code == 200
        assert response.json()["status"] == "pending"

    def test_a_customer_cannot_see_another_customers_order(
        self, client: TestClient, db_session: Session, customer_headers: dict[str, str]
    ) -> None:
        """404, deliberately NOT 403.

        A 403 would confirm that an order with this id exists, which turns
        sequential ids into a way to measure the platform's order volume. To a
        customer, someone else's order and a nonexistent one are the same
        thing.
        """
        someone_else = factories.create_user(db_session, email="stranger@lateral.test")
        order = factories.create_order(db_session, customer=someone_else)

        response = client.get(f"{ORDERS_URL}/{order.id}", headers=customer_headers)

        assert response.status_code == 404

    def test_staff_can_see_any_order(
        self, client: TestClient, db_session: Session, admin_headers: dict[str, str]
    ) -> None:
        order = factories.create_order(db_session)

        response = client.get(f"{ORDERS_URL}/{order.id}", headers=admin_headers)

        assert response.status_code == 200

    def test_a_customer_lists_only_their_own_orders(
        self, client: TestClient, db_session: Session, customer, customer_headers: dict[str, str]
    ) -> None:  # type: ignore[no-untyped-def]
        factories.create_order(db_session, customer=customer)
        factories.create_order(
            db_session,
            customer=factories.create_user(db_session, email="other@lateral.test"),
        )

        body = client.get(ORDERS_URL, headers=customer_headers).json()

        assert len(body) == 1

    def test_staff_list_every_order(
        self, client: TestClient, db_session: Session, admin_headers: dict[str, str]
    ) -> None:
        factories.create_order(db_session)
        factories.create_order(db_session)

        body = client.get(ORDERS_URL, headers=admin_headers).json()

        assert len(body) >= 2

    def test_listing_requires_authentication(self, client: TestClient) -> None:
        assert client.get(ORDERS_URL).status_code == 401

    def test_staff_can_filter_by_status(
        self, client: TestClient, db_session: Session, admin_headers: dict[str, str]
    ) -> None:
        """The query a staff dashboard actually runs: what needs attention now."""
        factories.create_order(db_session, status=OrderStatus.PENDING)
        factories.create_order(db_session, status=OrderStatus.DELIVERED)

        body = client.get(ORDERS_URL, params={"status": "pending"}, headers=admin_headers).json()

        assert body
        assert all(order["status"] == "pending" for order in body)


class TestAdvancingTheStatus:
    """Point 4 of the brief: staff drive the lifecycle."""

    STATUS_URL = ORDERS_URL + "/{order_id}/status"

    def test_staff_walk_an_order_through_the_full_lifecycle(
        self, client: TestClient, db_session: Session, admin_headers: dict[str, str]
    ) -> None:
        order = factories.create_order(db_session, status=OrderStatus.PENDING)
        url = self.STATUS_URL.format(order_id=order.id)

        for expected in ("accepted", "out_for_delivery", "delivered"):
            response = client.patch(url, json={"status": expected}, headers=admin_headers)

            assert response.status_code == 200, response.text
            assert response.json()["status"] == expected

    def test_a_customer_cannot_advance_their_own_order(
        self, client: TestClient, db_session: Session, customer, customer_headers: dict[str, str]
    ) -> None:  # type: ignore[no-untyped-def]
        """Otherwise a customer could mark their own order delivered.

        The interesting direction is the reverse of the usual worry: not theft
        of data, but a customer declaring an order complete that never arrived,
        or accepting an order the restaurant never agreed to make.
        """
        order = factories.create_order(db_session, customer=customer)

        response = client.patch(
            self.STATUS_URL.format(order_id=order.id),
            json={"status": "accepted"},
            headers=customer_headers,
        )

        assert response.status_code == 403

    def test_skipping_a_step_is_rejected_with_409(
        self, client: TestClient, db_session: Session, admin_headers: dict[str, str]
    ) -> None:
        """The state machine, enforced through the API.

        409 rather than 400: the request is perfectly well-formed and the
        caller is authorised. It conflicts with the order's current state, and
        the very same request would have succeeded one transition later.
        """
        order = factories.create_order(db_session, status=OrderStatus.PENDING)

        response = client.patch(
            self.STATUS_URL.format(order_id=order.id),
            json={"status": "delivered"},
            headers=admin_headers,
        )

        assert response.status_code == 409

    def test_the_rejection_names_the_permitted_transitions(
        self, client: TestClient, db_session: Session, admin_headers: dict[str, str]
    ) -> None:
        """The error body is guidance, not just a refusal."""
        order = factories.create_order(db_session, status=OrderStatus.PENDING)

        detail = client.patch(
            self.STATUS_URL.format(order_id=order.id),
            json={"status": "delivered"},
            headers=admin_headers,
        ).json()["detail"]

        assert "pending" in detail
        assert "accepted" in detail

    def test_moving_backwards_is_rejected(
        self, client: TestClient, db_session: Session, admin_headers: dict[str, str]
    ) -> None:
        order = factories.create_order(db_session, status=OrderStatus.OUT_FOR_DELIVERY)

        response = client.patch(
            self.STATUS_URL.format(order_id=order.id),
            json={"status": "accepted"},
            headers=admin_headers,
        )

        assert response.status_code == 409

    def test_a_delivered_order_is_final(
        self, client: TestClient, db_session: Session, admin_headers: dict[str, str]
    ) -> None:
        order = factories.create_order(db_session, status=OrderStatus.DELIVERED)

        for target in ("accepted", "out_for_delivery", "cancelled"):
            response = client.patch(
                self.STATUS_URL.format(order_id=order.id),
                json={"status": target},
                headers=admin_headers,
            )

            assert response.status_code == 409

    def test_an_order_can_be_cancelled_before_dispatch(
        self, client: TestClient, db_session: Session, admin_headers: dict[str, str]
    ) -> None:
        """The restaurant rejecting an order it cannot fulfil."""
        order = factories.create_order(db_session, status=OrderStatus.PENDING)

        response = client.patch(
            self.STATUS_URL.format(order_id=order.id),
            json={"status": "cancelled"},
            headers=admin_headers,
        )

        assert response.status_code == 200

    def test_an_order_cannot_be_cancelled_once_dispatched(
        self, client: TestClient, db_session: Session, admin_headers: dict[str, str]
    ) -> None:
        """Once a courier holds the food, unwinding is a refund, not a status."""
        order = factories.create_order(db_session, status=OrderStatus.OUT_FOR_DELIVERY)

        response = client.patch(
            self.STATUS_URL.format(order_id=order.id),
            json={"status": "cancelled"},
            headers=admin_headers,
        )

        assert response.status_code == 409

    def test_repeating_the_current_status_is_rejected(
        self, client: TestClient, db_session: Session, admin_headers: dict[str, str]
    ) -> None:
        """Two staff members accepting the same order must not both succeed.

        Treating a repeat as a harmless no-op hides the race; rejecting it
        surfaces the lost update to the caller.
        """
        order = factories.create_order(db_session, status=OrderStatus.ACCEPTED)

        response = client.patch(
            self.STATUS_URL.format(order_id=order.id),
            json={"status": "accepted"},
            headers=admin_headers,
        )

        assert response.status_code == 409

    def test_an_unknown_status_value_is_rejected(
        self, client: TestClient, db_session: Session, admin_headers: dict[str, str]
    ) -> None:
        """422, from schema validation, before any business rule runs."""
        order = factories.create_order(db_session, status=OrderStatus.PENDING)

        response = client.patch(
            self.STATUS_URL.format(order_id=order.id),
            json={"status": "teleported"},
            headers=admin_headers,
        )

        assert response.status_code == 422

    def test_updating_an_unknown_order_returns_404(
        self, client: TestClient, admin_headers: dict[str, str]
    ) -> None:
        response = client.patch(
            self.STATUS_URL.format(order_id=999999),
            json={"status": "accepted"},
            headers=admin_headers,
        )

        assert response.status_code == 404

    def test_a_rejected_transition_leaves_the_status_unchanged(
        self, client: TestClient, db_session: Session, admin_headers: dict[str, str]
    ) -> None:
        """A 409 that had already mutated the row would be worse than no check."""
        order = factories.create_order(db_session, status=OrderStatus.PENDING)

        client.patch(
            self.STATUS_URL.format(order_id=order.id),
            json={"status": "delivered"},
            headers=admin_headers,
        )
        db_session.refresh(order)

        assert order.status is OrderStatus.PENDING
