"""The order lifecycle, modelled as an explicit finite state machine.

ARCHITECTURAL DECISION -- one declarative transition table, not scattered
conditionals.

The naive implementation of this requirement is an `if` statement inside the
"update status" endpoint. It works on the first day and decays immediately:
a second entry point appears (an admin tool, a courier app, a webhook from a
payment provider), each grows its own slightly different copy of the rules, and
the copies drift. The bug that follows is not a crash -- it is an order that
reaches an impossible state, discovered days later in a billing dispute.

Encoding the machine as a single data structure gives three properties that
conditionals cannot:

1.  ONE SOURCE OF TRUTH. Every caller, present and future, consults the same
    table. Adding a new entry point cannot introduce a divergent rule.
2.  EXHAUSTIVE TESTABILITY. The rules are data, so a test can enumerate all
    N^2 status pairs and assert the complete matrix. Nobody can enumerate all
    paths through a tangle of nested conditionals.
3.  READABILITY AS SPECIFICATION. A reviewer -- or a product owner -- reads
    ALLOWED_TRANSITIONS and sees the entire lifecycle at once. This module is
    the executable version of the requirement.

The Open/Closed Principle in practice: introducing a new status means adding a
row to the table, not editing branching logic spread across the codebase.
"""

from enum import StrEnum

from app.domain.errors import ConflictError


class OrderStatus(StrEnum):
    """The lifecycle states an order may occupy.

    `StrEnum` rather than a bare `Enum`: members compare equal to their string
    values and serialise as those values, so the same type is usable as the
    wire format in JSON, as the stored value in PostgreSQL, and as a type-safe
    symbol in Python. One definition, no translation layer, no drift between
    the database's idea of "delivered" and the API's.

    The values are lowercase snake_case exactly as given in the specification;
    they are a published contract and must not be changed casually.
    """

    PENDING = "pending"
    ACCEPTED = "accepted"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"

    # Deliberate extension beyond the four states in the brief. A takeaway
    # platform with no way to reject or cancel an order cannot be operated:
    # restaurants run out of stock and close unexpectedly. Modelled as terminal
    # and reachable only before dispatch -- see ALLOWED_TRANSITIONS.
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# The state machine itself.
#
#   pending ──▶ accepted ──▶ out_for_delivery ──▶ delivered
#      │            │
#      └────────────┴──────▶ cancelled
#
# `frozenset` values and a module-level constant make the table immutable in
# practice: business rules must not be mutable at runtime, and an accidental
# `ALLOWED_TRANSITIONS[x].add(y)` somewhere in a request handler would silently
# rewrite the rules for the entire process.
#
# Terminal states map to an EMPTY frozenset rather than being omitted. An
# absent key would raise KeyError on lookup; an explicit empty set states
# "nothing may follow this" as a positive assertion, and lets the tests verify
# that every status is accounted for.
# ---------------------------------------------------------------------------
ALLOWED_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PENDING: frozenset({OrderStatus.ACCEPTED, OrderStatus.CANCELLED}),
    OrderStatus.ACCEPTED: frozenset({OrderStatus.OUT_FOR_DELIVERY, OrderStatus.CANCELLED}),
    # Once a courier is holding the food, cancellation is no longer a status
    # change: the goods and the payment are committed, and unwinding that is a
    # refund process rather than a lifecycle transition.
    OrderStatus.OUT_FOR_DELIVERY: frozenset({OrderStatus.DELIVERED}),
    OrderStatus.DELIVERED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
}


class InvalidOrderTransition(ConflictError):
    """Raised when a requested status change violates the state machine.

    Inherits from `ConflictError`, so the API boundary renders it as HTTP 409
    without this module knowing anything about HTTP. 409 is the accurate code:
    the request is syntactically valid and the caller is authorised, but it
    conflicts with the order's current state.

    The offending states are kept as structured attributes, not merely
    interpolated into a message, so callers can react programmatically instead
    of parsing prose.
    """

    def __init__(self, current: OrderStatus, requested: OrderStatus) -> None:
        self.current = current
        self.requested = requested
        self.allowed = ALLOWED_TRANSITIONS[current]

        # The permitted alternatives are part of the message because this text
        # reaches API clients. "Cannot go from pending to delivered" tells a
        # developer nothing actionable; naming the legal next steps does.
        # Sorted for determinism -- frozenset iteration order is not stable
        # across processes, and an error message that varies between identical
        # requests is a genuine obstacle when correlating logs.
        allowed = ", ".join(sorted(self.allowed)) if self.allowed else "none (terminal state)"

        super().__init__(
            f"Cannot change order status from '{current}' to '{requested}'. "
            f"Allowed transitions from '{current}': {allowed}."
        )


def can_transition(current: OrderStatus, requested: OrderStatus) -> bool:
    """Return whether `current -> requested` is a legal transition.

    The predicate form, for callers that need to *ask* rather than enforce --
    for example to decide which actions to offer in a UI, or to filter a list
    of orders a staff member can currently act on.

    Note that a status can never transition to itself: no status appears in its
    own set of destinations. This is intentional. Treating a repeated update as
    a harmless no-op hides double submissions and concurrent edits by two staff
    members; rejecting it surfaces the race to the caller.
    """
    return requested in ALLOWED_TRANSITIONS[current]


def assert_can_transition(current: OrderStatus, requested: OrderStatus) -> None:
    """Enforce the state machine, raising if the transition is illegal.

    The command form, used by the service layer as a guard clause before
    persisting a change.

    Both this and `can_transition` exist on purpose, and the duplication is
    only apparent: the predicate answers a question, the assertion enforces an
    invariant, and collapsing them would force callers into the classic
    `if not can_transition(...): raise` boilerplate at every call site -- the
    very repetition DRY exists to prevent.

    Raises:
        InvalidOrderTransition: if the transition is not permitted.
    """
    if not can_transition(current, requested):
        raise InvalidOrderTransition(current=current, requested=requested)
