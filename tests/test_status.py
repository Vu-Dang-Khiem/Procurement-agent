import pytest

from rules.errors import PolicyViolation
from rules.status import TERMINAL_STATUSES, allowed_next, ensure_transition

VALID = [
    ("NEW", "NEEDS_INFO"),
    ("NEW", "IN_REVIEW"),
    ("NEEDS_INFO", "IN_REVIEW"),
    ("IN_REVIEW", "NEEDS_INFO"),
    ("IN_REVIEW", "PENDING_APPROVAL"),
    ("PENDING_APPROVAL", "APPROVED"),
    ("PENDING_APPROVAL", "REJECTED"),
    ("REJECTED", "IN_REVIEW"),
    ("REJECTED", "CANCELLED"),
    ("APPROVED", "PO_DRAFTED"),
    ("PO_DRAFTED", "COMPLETED"),
]
OPEN = ["NEW", "NEEDS_INFO", "IN_REVIEW", "PENDING_APPROVAL", "APPROVED", "REJECTED", "PO_DRAFTED"]
INVALID = [
    ("NEW", "APPROVED"),
    ("NEW", "PENDING_APPROVAL"),
    ("IN_REVIEW", "APPROVED"),
    ("PENDING_APPROVAL", "PO_DRAFTED"),
    ("REJECTED", "APPROVED"),
    ("APPROVED", "COMPLETED"),
]


@pytest.mark.parametrize(("current", "target"), VALID)
def test_valid_transitions(current, target):
    ensure_transition(current, target)


@pytest.mark.parametrize(("current", "target"), INVALID)
def test_invalid_transitions_are_refused(current, target):
    with pytest.raises(PolicyViolation) as error:
        ensure_transition(current, target)
    assert current in error.value.hint


@pytest.mark.parametrize("status", OPEN)
@pytest.mark.parametrize("target", ["ESCALATED", "CANCELLED"])
def test_open_requests_can_be_escalated_or_cancelled(status, target):
    ensure_transition(status, target)


@pytest.mark.parametrize("status", sorted(TERMINAL_STATUSES))
def test_terminal_statuses_cannot_change(status):
    assert allowed_next(status) == frozenset()
    with pytest.raises(PolicyViolation):
        ensure_transition(status, "IN_REVIEW")
