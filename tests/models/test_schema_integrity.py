"""Integration tests for the database schema's integrity guarantees.

These tests assert that the DATABASE refuses invalid data, not merely that the
application avoids writing it. The distinction is the whole point.

Validation in Pydantic protects against well-behaved clients coming through the
API. A constraint protects against every other path into the data: a
maintenance script, a data migration, a future service, a colleague in psql at
2am. Defence in depth applies to data integrity exactly as it does to security,
and the constraint is the last line that cannot be forgotten.

They also serve a second purpose: they are the proof that the migration chain
produces the schema the models describe. Every one of these assertions runs
against a database built by `alembic upgrade head`, so a constraint that exists
in a model but never made it into a migration fails here.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.order_state import OrderStatus
from app.domain.roles import UserRole
from app.models.order import Order, OrderItem
from app.models.restaurant import MenuItem
from app.models.user import User
from tests import factories

# Every test in this module talks to PostgreSQL.
pytestmark = pytest.mark.integration


class TestUserConstraints:
    def test_email_must_be_unique(self, db_session: Session) -> None:
        """Two accounts sharing an email would make login ambiguous."""
        factories.create_user(db_session, email="duplicate@example.com")

        with pytest.raises(IntegrityError):
            factories.create_user(db_session, email="duplicate@example.com")

    def test_role_defaults_to_customer(self, db_session: Session) -> None:
        """The default must be the LEAST privileged role.

        If a bug ever omits the role on insert, the failure mode has to be a
        user who cannot do enough, not one who can do everything. Privilege
        escalation by accident is the outcome this default rules out.
        """
        db_session.execute(
            text(
                "INSERT INTO users (email, hashed_password, full_name) "
                "VALUES ('defaulted@example.com', 'x', 'Defaulted')"
            )
        )

        user = db_session.query(User).filter(User.email == "defaulted@example.com").one()

        assert user.role is UserRole.CUSTOMER

    def test_role_is_stored_as_its_value_not_its_member_name(self, db_session: Session) -> None:
        """Guards the `values_callable` mapping on the enum column.

        Without it SQLAlchemy persists the member NAME ("ADMIN") while the
        application compares against the VALUE ("admin"). Nothing fails at
        write time; the mismatch only appears when a row written by one path is
        read by another, which makes it exactly the kind of bug that reaches
        production.
        """
        admin = factories.create_admin(db_session)

        stored = db_session.execute(
            text("SELECT role::text FROM users WHERE id = :id"), {"id": admin.id}
        ).scalar_one()

        assert stored == "admin"


class TestMenuItemConstraints:
    def test_price_cannot_be_negative(self, db_session: Session) -> None:
        """A negative price would pay the customer to take the food."""
        restaurant = factories.create_restaurant(db_session)

        db_session.add(
            MenuItem(
                restaurant_id=restaurant.id,
                name="Impossible Item",
                price_cents=-1,
                is_available=True,
            )
        )

        with pytest.raises(IntegrityError):
            db_session.flush()

    def test_price_of_zero_is_permitted(self, db_session: Session) -> None:
        """Zero is legitimate: promotional items and freebies exist.

        Asserted explicitly so nobody "tightens" the constraint to `> 0` and
        breaks a real use case while believing they improved validation.
        """
        item = factories.create_menu_item(db_session, price_cents=0)

        assert item.price_cents == 0

    def test_deleting_a_restaurant_deletes_its_menu(self, db_session: Session) -> None:
        """CASCADE: a menu has no meaning without its restaurant."""
        restaurant = factories.create_restaurant(db_session)
        factories.create_menu_item(db_session, restaurant=restaurant)

        db_session.delete(restaurant)
        db_session.flush()

        assert db_session.query(MenuItem).count() == 0


class TestOrderItemConstraints:
    def test_quantity_must_be_positive(self, db_session: Session) -> None:
        order = factories.create_order(db_session)
        menu_item = factories.create_menu_item(db_session)

        db_session.add(
            OrderItem(
                order_id=order.id,
                menu_item_id=menu_item.id,
                item_name=menu_item.name,
                unit_price_cents=menu_item.price_cents,
                quantity=0,
            )
        )

        with pytest.raises(IntegrityError):
            db_session.flush()

    def test_deleting_an_order_deletes_its_line_items(self, db_session: Session) -> None:
        order = factories.create_order(db_session)

        db_session.delete(order)
        db_session.flush()

        assert db_session.query(OrderItem).count() == 0


class TestHistoricalRecordProtection:
    """The rules that stop financial history from being destroyed."""

    def test_a_customer_with_orders_cannot_be_deleted(self, db_session: Session) -> None:
        """RESTRICT, not CASCADE.

        Orders are financial records. Deleting a customer must not silently
        erase them: the database refuses, which forces the correct conversation
        about anonymisation or archival instead of quietly destroying evidence
        that an accountant or a court may later need.
        """
        customer = factories.create_user(db_session)
        factories.create_order(db_session, customer=customer)

        db_session.delete(customer)

        with pytest.raises(IntegrityError):
            db_session.flush()

    def test_a_menu_item_referenced_by_an_order_cannot_be_deleted(
        self, db_session: Session
    ) -> None:
        """Staff remove items from sale with `is_available`, not by deleting.

        Deleting would break the reference from historical order lines. The
        snapshotted name and price mean the receipt still reads correctly, but
        the link back to the catalogue is worth keeping intact.
        """
        menu_item = factories.create_menu_item(db_session)
        factories.create_order(db_session, menu_item=menu_item)

        db_session.delete(menu_item)

        with pytest.raises(IntegrityError):
            db_session.flush()


class TestOrderDefaults:
    def test_status_defaults_to_pending(self, db_session: Session) -> None:
        customer = factories.create_user(db_session)
        restaurant = factories.create_restaurant(db_session)

        order = Order(customer_id=customer.id, restaurant_id=restaurant.id, total_cents=100)
        db_session.add(order)
        db_session.flush()

        assert order.status is OrderStatus.PENDING

    def test_timestamps_are_populated_by_the_database(self, db_session: Session) -> None:
        """Server-side defaults, so all writers share one authoritative clock.

        With Python-side defaults, several API containers stamp rows from their
        own drifting clocks and rows written seconds apart can carry timestamps
        in the wrong order -- which makes reconstructing an order's history
        during a dispute unreliable.
        """
        order = factories.create_order(db_session)

        assert order.created_at is not None
        assert order.updated_at is not None
        # TIMESTAMPTZ: aware, so it cannot be misread in the wrong zone.
        assert order.created_at.tzinfo is not None


class TestSnapshotIndependence:
    """The most valuable assertion in this module."""

    def test_changing_a_menu_price_does_not_alter_existing_orders(
        self, db_session: Session
    ) -> None:
        """Historical orders must be immune to later price changes.

        This is the bug that snapshotting exists to prevent, and it is worth
        seeing stated as an executable assertion: without the snapshot, raising
        a price would retroactively change what every past customer appears to
        have paid, corrupting revenue reports and refund amounts alike.
        """
        menu_item = factories.create_menu_item(db_session, price_cents=1000)
        order = factories.create_order(db_session, menu_item=menu_item, quantity=2)

        original_total = order.total_cents
        original_unit_price = order.items[0].unit_price_cents

        menu_item.price_cents = 1500
        db_session.flush()
        db_session.refresh(order)

        assert order.total_cents == original_total == 2000
        assert order.items[0].unit_price_cents == original_unit_price == 1000

    def test_renaming_a_menu_item_does_not_alter_existing_orders(self, db_session: Session) -> None:
        """Old receipts must keep describing the food that was actually sold."""
        menu_item = factories.create_menu_item(db_session, name="Margherita")
        order = factories.create_order(db_session, menu_item=menu_item)

        menu_item.name = "Margherita Classica"
        db_session.flush()
        db_session.refresh(order)

        assert order.items[0].item_name == "Margherita"
