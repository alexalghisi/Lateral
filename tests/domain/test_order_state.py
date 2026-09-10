"""Tests for the order lifecycle state machine.

These tests deliberately touch no database, no HTTP layer and no fixtures. The
state machine is pure logic, and keeping it pure is the entire point: the rules
governing which status may follow which are the most business-critical part of
this system, so they must be verifiable in microseconds, exhaustively, without
provisioning anything.

That purity is what allows the exhaustive test below. Every one of the 25
status pairs is asserted explicitly, rather than spot-checking the happy path.
A partially tested state machine is how orders end up delivered before they
were ever accepted.
"""

from itertools import pairwise
from typing import ClassVar

import pytest

from app.domain.order_state import (
    ALLOWED_TRANSITIONS,
    InvalidOrderTransition,
    OrderStatus,
    assert_can_transition,
    can_transition,
)


class TestHappyPath:
    """The lifecycle described by the specification, walked end to end."""

    def test_full_lifecycle_is_permitted_step_by_step(self) -> None:
        lifecycle = [
            OrderStatus.PENDING,
            OrderStatus.ACCEPTED,
            OrderStatus.OUT_FOR_DELIVERY,
            OrderStatus.DELIVERED,
        ]

        for current, following in pairwise(lifecycle):
            assert can_transition(current, following), f"{current} -> {following} must be allowed"

    def test_new_orders_start_as_pending(self) -> None:
        """`PENDING` is the sole entry point of the lifecycle.

        Asserted by construction: no transition anywhere in the table leads
        back into PENDING, so it can only ever be an initial state.
        """
        assert all(
            OrderStatus.PENDING not in destinations for destinations in ALLOWED_TRANSITIONS.values()
        )


class TestForbiddenTransitions:
    """Everything the state machine must refuse."""

    def test_cannot_skip_a_step(self) -> None:
        """Skipping ahead is the failure mode with real financial consequences.

        A courier marking an order delivered without it ever being dispatched
        means a customer is charged for food that was never sent.
        """
        assert not can_transition(OrderStatus.PENDING, OrderStatus.OUT_FOR_DELIVERY)
        assert not can_transition(OrderStatus.PENDING, OrderStatus.DELIVERED)
        assert not can_transition(OrderStatus.ACCEPTED, OrderStatus.DELIVERED)

    def test_cannot_move_backwards(self) -> None:
        """The lifecycle is a one-way ratchet. History is not rewritten."""
        assert not can_transition(OrderStatus.ACCEPTED, OrderStatus.PENDING)
        assert not can_transition(OrderStatus.OUT_FOR_DELIVERY, OrderStatus.ACCEPTED)
        assert not can_transition(OrderStatus.DELIVERED, OrderStatus.OUT_FOR_DELIVERY)

    def test_cannot_transition_to_itself(self) -> None:
        """A no-op status update is rejected rather than silently accepted.

        Two staff members clicking "accept" simultaneously must not both
        succeed: the second attempt is a lost update, and treating it as a
        success hides the race instead of surfacing it.
        """
        for status in OrderStatus:
            assert not can_transition(status, status)

    @pytest.mark.parametrize("terminal", [OrderStatus.DELIVERED, OrderStatus.CANCELLED])
    def test_terminal_states_admit_no_further_transition(self, terminal: OrderStatus) -> None:
        assert ALLOWED_TRANSITIONS[terminal] == frozenset()

        for target in OrderStatus:
            assert not can_transition(terminal, target)


class TestCancellation:
    """Cancellation is a deliberate extension of the specified lifecycle.

    The brief lists pending -> accepted -> out_for_delivery -> delivered. A
    takeaway platform without any way to cancel an order is not operable in
    reality: restaurants reject orders when they are out of stock or closing.

    It is modelled as a terminal state reachable only *before* dispatch. Once
    a courier holds the food, the money and the goods are already committed and
    a refund flow -- not a status change -- is the correct mechanism.
    """

    def test_can_be_cancelled_before_dispatch(self) -> None:
        assert can_transition(OrderStatus.PENDING, OrderStatus.CANCELLED)
        assert can_transition(OrderStatus.ACCEPTED, OrderStatus.CANCELLED)

    def test_cannot_be_cancelled_once_dispatched(self) -> None:
        assert not can_transition(OrderStatus.OUT_FOR_DELIVERY, OrderStatus.CANCELLED)
        assert not can_transition(OrderStatus.DELIVERED, OrderStatus.CANCELLED)


class TestExhaustiveTransitionMatrix:
    """Every ordered pair of statuses, asserted explicitly."""

    # The complete specification of the machine, written independently of the
    # implementation. If someone edits ALLOWED_TRANSITIONS, this matrix must be
    # edited too -- which is exactly the deliberate friction we want around a
    # change to the business rules.
    PERMITTED_PAIRS: ClassVar[set[tuple[OrderStatus, OrderStatus]]] = {
        (OrderStatus.PENDING, OrderStatus.ACCEPTED),
        (OrderStatus.PENDING, OrderStatus.CANCELLED),
        (OrderStatus.ACCEPTED, OrderStatus.OUT_FOR_DELIVERY),
        (OrderStatus.ACCEPTED, OrderStatus.CANCELLED),
        (OrderStatus.OUT_FOR_DELIVERY, OrderStatus.DELIVERED),
    }

    def test_matrix_matches_the_implementation(self) -> None:
        actual = {
            (source, target)
            for source in OrderStatus
            for target in OrderStatus
            if can_transition(source, target)
        }

        assert actual == self.PERMITTED_PAIRS

    def test_every_status_is_present_in_the_table(self) -> None:
        """A status missing from the table would raise KeyError at runtime."""
        assert set(ALLOWED_TRANSITIONS) == set(OrderStatus)


class TestAssertCanTransition:
    """The raising variant, used by the service layer."""

    def test_permitted_transition_does_not_raise(self) -> None:
        assert_can_transition(OrderStatus.PENDING, OrderStatus.ACCEPTED)

    def test_forbidden_transition_raises_invalid_order_transition(self) -> None:
        with pytest.raises(InvalidOrderTransition) as exc_info:
            assert_can_transition(OrderStatus.PENDING, OrderStatus.DELIVERED)

        error = exc_info.value
        assert error.current is OrderStatus.PENDING
        assert error.requested is OrderStatus.DELIVERED

    def test_error_message_names_both_states_and_the_legal_alternatives(self) -> None:
        """The message is an API response body, not just a log line.

        A client that receives "cannot go from pending to delivered" learns
        nothing actionable. Listing the permitted targets turns the rejection
        into usable guidance.
        """
        with pytest.raises(InvalidOrderTransition) as exc_info:
            assert_can_transition(OrderStatus.PENDING, OrderStatus.DELIVERED)

        message = str(exc_info.value)
        assert "pending" in message
        assert "delivered" in message
        assert "accepted" in message


class TestOrderStatusEnum:
    """The enum is a wire format, not only an internal convention."""

    def test_values_are_the_exact_strings_from_the_specification(self) -> None:
        assert OrderStatus.PENDING.value == "pending"
        assert OrderStatus.ACCEPTED.value == "accepted"
        assert OrderStatus.OUT_FOR_DELIVERY.value == "out_for_delivery"
        assert OrderStatus.DELIVERED.value == "delivered"

    def test_is_a_string_enum_so_it_serialises_as_its_value(self) -> None:
        """Guards the JSON contract.

        Without a str mixin, serialising would emit "OrderStatus.PENDING" and
        break every client. Cheap test, expensive regression.
        """
        assert isinstance(OrderStatus.PENDING, str)
        assert f"{OrderStatus.PENDING}" == "pending"
