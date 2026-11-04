import pytest

from rules.approvers import fully_approved, pick_approvers
from rules.errors import PolicyViolation
from simulator.seed import FINANCE, IT, build_database

STAFF, HEAD, DIRECTOR = 3, 1, 30


@pytest.fixture
def conn():
    conn = build_database()
    yield conn
    conn.close()


def add_approval(conn, request_id, level, approver_id, decision):
    with conn:
        conn.execute(
            "INSERT INTO approvals (request_id, level, approver_id, decision, decided_at) VALUES (?, ?, ?, ?, ?)",
            (request_id, level, approver_id, decision, "2026-11-03T09:00:00" if decision else None),
        )


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (19_999_999, [("head", HEAD)]),
        (20_000_000, [("head", HEAD), ("finance", 27)]),
        (100_000_000, [("head", HEAD), ("finance", 27)]),
        (100_000_001, [("head", HEAD), ("finance", 27), ("director", DIRECTOR)]),
    ],
)
def test_r4_levels_at_boundaries(conn, amount, expected):
    assert pick_approvers(conn, IT, STAFF, amount) == expected


def test_r5_director_replaces_head_who_made_the_request(conn):
    assert pick_approvers(conn, IT, HEAD, 14_300_000) == [("director", DIRECTOR)]


def test_r5_director_is_not_listed_twice(conn):
    assert pick_approvers(conn, IT, HEAD, 100_000_001) == [("finance", 27), ("director", DIRECTOR)]


def test_r5_finance_requester_does_not_approve_own_request(conn):
    assert pick_approvers(conn, FINANCE, 27, 20_000_000) == [("head", 26), ("finance", 28)]


def test_r5_no_eligible_director(conn):
    with pytest.raises(PolicyViolation):
        pick_approvers(conn, IT, DIRECTOR, 100_000_001)


def test_r5_no_eligible_finance(conn):
    with conn:
        conn.execute("UPDATE employees SET role = 'staff' WHERE id IN (28, 29)")
    with pytest.raises(PolicyViolation):
        pick_approvers(conn, IT, 27, 20_000_000)


def test_fully_approved_needs_every_decision(conn):
    assert fully_approved(conn, 7)  # đã duyệt đủ
    assert not fully_approved(conn, 5)  # còn chờ Finance
    assert not fully_approved(conn, 8)  # bị Finance từ chối
    assert not fully_approved(conn, 1)  # chưa gửi duyệt


def test_new_round_replaces_earlier_rejection(conn):
    add_approval(conn, 8, "head", 18, "approved")
    add_approval(conn, 8, "finance", 28, None)
    assert not fully_approved(conn, 8)
    with conn:
        conn.execute(
            "UPDATE approvals SET decision = 'approved', decided_at = '2026-11-03T10:00:00'"
            " WHERE request_id = 8 AND decision IS NULL"
        )
    assert fully_approved(conn, 8)
