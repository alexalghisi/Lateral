"""Test data factories.

ARCHITECTURAL DECISION -- plain functions with sensible defaults, not a
factory library.

Every factory takes a session and keyword overrides, fills the rest with valid
defaults, and flushes. The value is in what a test then looks like: it states
only the fields that matter to what it asserts, and a reader can tell at a
glance which values are load-bearing and which are scaffolding.

The alternative -- constructing models inline in each test -- means a new
mandatory column breaks every test that builds that model, and the interesting
value gets buried among six irrelevant ones. DRY applied to test setup.

`flush()` rather than `commit()`: flushing assigns primary keys and triggers
database-level constraints, which is everything a test needs, while leaving the
enclosing transaction open for the harness to roll back.
"""

from itertools import count

from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.domain.order_state import OrderStatus
from app.domain.roles import UserRole
from app.models.order import Order, OrderItem
from app.models.restaurant import MenuItem, Restaurant
from app.models.user import User

# Guarantees unique emails across a session without the caller having to think
# about it. A hard-coded address would collide with the unique index the moment
# a test creates two users, producing a failure that looks like a bug in the
# code under test rather than in the fixture.
_email_sequence = count(1)

# The password every factory-built user has, exposed as a constant so
# authentication tests can log in without duplicating the literal.
DEFAULT_PASSWORD = "correct-horse-battery-staple"


def create_user(
    session: Session,
    *,
    role: UserRole = UserRole.CUSTOMER,
    email: str | None = None,
    password: str = DEFAULT_PASSWORD,
    full_name: str = "Test User",
    is_active: bool = True,
) -> User:
    """Create a persisted user.

    The password is hashed exactly as the application hashes it, rather than a
    fake digest being inserted. Argon2 is intentionally slow, so this is the
    single most expensive thing the suite does -- and it is worth it: a fixture
    that bypassed hashing would let a login bug pass the tests untouched.
    """
    user = User(
        email=email or f"user{next(_email_sequence)}@example.com",
        hashed_password=hash_password(password),
        full_name=full_name,
        role=role,
        is_active=is_active,
    )

    session.add(user)
    session.flush()

    return user


def create_admin(session: Session, **kwargs: object) -> User:
    """Create a staff user. Thin wrapper, because tests read better for it."""
    return create_user(session, role=UserRole.ADMIN, **kwargs)  # type: ignore[arg-type]


def create_restaurant(
    session: Session,
    *,
    name: str = "Trattoria Lateral",
    description: str | None = "Neapolitan pizza and pasta.",
    is_active: bool = True,
) -> Restaurant:
    """Create a persisted restaurant."""
    restaurant = Restaurant(name=name, description=description, is_active=is_active)

    session.add(restaurant)
    session.flush()

    return restaurant


def create_menu_item(
    session: Session,
    *,
    restaurant: Restaurant | None = None,
    name: str = "Margherita",
    description: str | None = "Tomato, mozzarella, basil.",
    price_cents: int = 1050,
    is_available: bool = True,
) -> MenuItem:
    """Create a persisted menu item, creating its restaurant if none is given.

    Prices are in cents throughout: 1050 is EUR 10.50. The unit is in the
    parameter name precisely so nobody has to guess.
    """
    if restaurant is None:
        restaurant = create_restaurant(session)

    menu_item = MenuItem(
        restaurant_id=restaurant.id,
        name=name,
        description=description,
        price_cents=price_cents,
        is_available=is_available,
    )

    session.add(menu_item)
    session.flush()

    return menu_item


def create_order(
    session: Session,
    *,
    customer: User | None = None,
    restaurant: Restaurant | None = None,
    status: OrderStatus = OrderStatus.PENDING,
    menu_item: MenuItem | None = None,
    quantity: int = 2,
) -> Order:
    """Create a persisted order with a single line item.

    The `status` override exists so a test can start from any point in the
    lifecycle without walking through every preceding transition, which would
    make each test depend on the correctness of the transitions before it.
    """
    if customer is None:
        customer = create_user(session)

    if menu_item is None:
        menu_item = create_menu_item(session, restaurant=restaurant)

    if restaurant is None:
        restaurant = menu_item.restaurant

    order = Order(
        customer_id=customer.id,
        restaurant_id=restaurant.id,
        status=status,
        total_cents=menu_item.price_cents * quantity,
    )
    session.add(order)
    session.flush()

    # Mirrors what the ordering service does: the name and price are copied out
    # of the menu item, not referenced through it. See app/models/order.py.
    session.add(
        OrderItem(
            order_id=order.id,
            menu_item_id=menu_item.id,
            item_name=menu_item.name,
            unit_price_cents=menu_item.price_cents,
            quantity=quantity,
        )
    )
    session.flush()
    session.refresh(order)

    return order
