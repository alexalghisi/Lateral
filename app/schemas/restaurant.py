"""Request and response schemas for restaurants and menu items."""

from pydantic import BaseModel, ConfigDict, Field

# Money is carried as an integer number of cents on the wire, exactly as it is
# stored. Serialising as a float would reintroduce binary rounding error at the
# API boundary after the database had carefully avoided it, and the unit is
# encoded in every field name so 1050 cannot be misread as ten euros.
MAXIMUM_PRICE_CENTS = 1_000_000  # EUR 10,000 -- a sanity bound, not a business rule.


class RestaurantCreateRequest(BaseModel):
    """Body for creating a restaurant. Staff only."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)


class RestaurantUpdateRequest(BaseModel):
    """Body for a partial update. Staff only.

    ARCHITECTURAL DECISION -- every field is optional, and "absent" is
    distinguished from "explicitly null".

    This is what makes PATCH behave correctly. The handler applies
    `model_dump(exclude_unset=True)`, so a field the client did not mention is
    left alone. Without that distinction, updating a name would silently erase
    the description, because an omitted optional field would arrive as `None`
    and be written as `NULL`.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None


class RestaurantResponse(BaseModel):
    """A restaurant as exposed by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    is_active: bool


class MenuItemCreateRequest(BaseModel):
    """Body for adding an item to a menu. Staff only."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)

    # `ge=0`, not `gt=0`: zero is a legitimate price. Promotional items and
    # freebies exist, and tightening this to "greater than zero" would break a
    # real use case while looking like stricter validation.
    price_cents: int = Field(ge=0, le=MAXIMUM_PRICE_CENTS)

    is_available: bool = True


class MenuItemUpdateRequest(BaseModel):
    """Body for a partial update of a menu item. Staff only."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    price_cents: int | None = Field(default=None, ge=0, le=MAXIMUM_PRICE_CENTS)
    is_available: bool | None = None


class MenuItemResponse(BaseModel):
    """A menu item as exposed by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    restaurant_id: int
    name: str
    description: str | None
    price_cents: int
    is_available: bool
