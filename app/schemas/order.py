"""Request and response schemas for orders."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.order_state import OrderStatus

# A basket has to hold something, and it must not be able to hold a warehouse.
# The upper bound is a denial-of-service guard: each line triggers a lookup and
# a row insert, so an unbounded list is an invitation to send ten thousand.
MAXIMUM_LINES_PER_ORDER = 50
MAXIMUM_QUANTITY_PER_LINE = 100


class OrderLineRequest(BaseModel):
    """One line of a basket: what to buy, and how much of it.

    NOTE what is ABSENT: there is no price field, and there is no name field.

    This is the single most important design decision in the ordering flow.
    The client states WHAT it wants, never WHAT IT INTENDS TO PAY. Prices are
    read from the menu on the server at the moment the order is placed. A
    schema that accepted a price would let anyone buy anything for one cent,
    and no validation elsewhere could rescue it -- the server would be
    faithfully recording an amount the attacker chose.

    `extra="forbid"` makes the attempt fail loudly rather than silently: a
    client sending `unit_price_cents` is told the field is not accepted, rather
    than receiving a 201 and believing it got a discount.
    """

    model_config = ConfigDict(extra="forbid")

    menu_item_id: int = Field(gt=0)
    quantity: int = Field(ge=1, le=MAXIMUM_QUANTITY_PER_LINE)


class OrderCreateRequest(BaseModel):
    """Body for placing an order.

    Carries no total, for the same reason lines carry no price.
    """

    model_config = ConfigDict(extra="forbid")

    restaurant_id: int = Field(gt=0)

    # `min_length=1`: an empty basket is not an order. Without this the API
    # would happily create a zero-value order that no kitchen can act on.
    items: list[OrderLineRequest] = Field(min_length=1, max_length=MAXIMUM_LINES_PER_ORDER)


class OrderStatusUpdateRequest(BaseModel):
    """Body for advancing an order's status. Staff only.

    Typing the field as `OrderStatus` means an unrecognised value is rejected
    with 422 by schema validation, before any business rule runs. The state
    machine then decides whether a RECOGNISED value is reachable from where the
    order currently is -- two distinct questions, answered in the right order
    and reported with different status codes.
    """

    model_config = ConfigDict(extra="forbid")

    status: OrderStatus


class OrderLineResponse(BaseModel):
    """One line of a placed order, as recorded at purchase time.

    `item_name` and `unit_price_cents` are the SNAPSHOT, not a live view of the
    menu. They are what makes this a receipt rather than a query: later edits
    to the catalogue cannot rewrite what was actually bought.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    menu_item_id: int
    item_name: str
    unit_price_cents: int
    quantity: int
    subtotal_cents: int


class OrderResponse(BaseModel):
    """An order as exposed by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int
    restaurant_id: int
    status: OrderStatus
    total_cents: int
    created_at: datetime
    updated_at: datetime
    items: list[OrderLineResponse]
