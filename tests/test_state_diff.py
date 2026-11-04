from datetime import timedelta

import pytest

from evals.state_diff import Change, diff, snapshot
from simulator.clock import SimClock
from simulator.db import connect
from simulator.seed import build_database

LATER = "2026-11-03T10:00:00"


@pytest.fixture
def conn():
    conn = build_database()
    yield conn
    conn.close()


def changes_after(conn, *statements):
    before = snapshot(conn)
    for statement in statements:
        conn.execute(statement)
    return diff(before, snapshot(conn))


def test_no_change_gives_empty_diff(conn):
    assert diff(snapshot(conn), snapshot(conn)) == []


def test_snapshot_covers_every_table_and_row(conn):
    rows = snapshot(conn).rows
    assert len(rows) == 11
    assert len(rows["employees"]) == 30
    assert rows["purchase_requests"][(12,)]["requester_id"] == 17


def test_snapshot_does_not_change_database(conn):
    before = list(conn.iterdump())
    snapshot(conn)
    assert list(conn.iterdump()) == before


def test_snapshot_is_not_affected_by_later_writes(conn):
    taken = snapshot(conn)
    conn.execute("UPDATE purchase_requests SET status = 'IN_REVIEW' WHERE id = 12")
    assert taken.rows["purchase_requests"][(12,)]["status"] == "NEW"


def test_detects_update_with_old_and_new_value(conn):
    assert changes_after(conn, "UPDATE purchase_requests SET status = 'IN_REVIEW' WHERE id = 12") == [
        Change("update", "purchase_requests", (12,), {"status": ("NEW", "IN_REVIEW")})
    ]


def test_groups_fields_of_same_row(conn):
    changes = changes_after(
        conn, "UPDATE purchase_requests SET status = 'CANCELLED', version = 2 WHERE id = 12"
    )
    assert changes == [
        Change(
            "update",
            "purchase_requests",
            (12,),
            {"status": ("NEW", "CANCELLED"), "version": (1, 2)},
        )
    ]


def test_detects_null_to_value_and_value_to_null(conn):
    changes = changes_after(
        conn,
        "UPDATE purchase_requests SET total_amount = 1000 WHERE id = 1",
        "UPDATE purchase_requests SET proposal_json = NULL WHERE id = 4",
    )
    assert changes[0].fields == {"total_amount": (None, 1000)}
    assert list(changes[1].fields) == ["proposal_json"]
    assert changes[1].fields["proposal_json"][1] is None


def test_uses_composite_primary_key(conn):
    changes = changes_after(conn, "UPDATE budgets SET spent = spent + 1 WHERE department_id = 1")
    assert changes == [
        Change("update", "budgets", (1, "2026-Q4"), {"spent": (120_000_000, 120_000_001)})
    ]


def test_detects_insert_with_all_fields(conn):
    changes = changes_after(
        conn,
        "INSERT INTO clarifications (id, request_id, question, asked_at)"
        f" VALUES (2, 1, 'Cần loại nào?', '{LATER}')",
    )
    assert changes == [
        Change(
            "insert",
            "clarifications",
            (2,),
            {
                "id": (None, 2),
                "request_id": (None, 1),
                "task_id": (None, None),
                "question": (None, "Cần loại nào?"),
                "reply_json": (None, None),
                "status": (None, "pending"),
                "asked_at": (None, LATER),
                "replied_at": (None, None),
            },
        )
    ]


def test_detects_delete(conn):
    changes = changes_after(conn, "DELETE FROM clarifications WHERE id = 1")
    assert [(change.kind, change.table, change.key) for change in changes] == [
        ("delete", "clarifications", (1,))
    ]
    assert changes[0].fields["status"] == ("pending", None)


def test_ignores_audit_logs(conn):
    changes = changes_after(
        conn,
        f"INSERT INTO audit_logs (actor, action, ts) VALUES ('agent', 'read', '{LATER}')",
    )
    assert changes == []


def test_ignores_clock(conn):
    before = snapshot(conn)
    SimClock(conn).advance(timedelta(days=1))
    assert diff(before, snapshot(conn)) == []


def test_ignores_updated_at_alone(conn):
    changes = changes_after(conn, f"UPDATE purchase_requests SET updated_at = '{LATER}' WHERE id = 1")
    assert changes == []


def test_drops_updated_at_but_keeps_other_fields(conn):
    changes = changes_after(
        conn, f"UPDATE purchase_requests SET status = 'IN_REVIEW', updated_at = '{LATER}' WHERE id = 1"
    )
    assert changes[0].fields == {"status": ("NEW", "IN_REVIEW")}


def test_drops_updated_at_from_inserted_row(conn):
    changes = changes_after(
        conn,
        "INSERT INTO purchase_requests (id, requester_id, department_id, source, created_at, updated_at)"
        f" VALUES (20, 3, 1, 'form', '{LATER}', '{LATER}')",
    )
    assert "updated_at" not in changes[0].fields


def test_same_column_name_in_other_table_is_not_ignored(conn):
    conn.execute("ALTER TABLE suppliers ADD COLUMN updated_at TEXT")
    changes = changes_after(conn, f"UPDATE suppliers SET updated_at = '{LATER}' WHERE id = 1")
    assert changes[0].fields == {"updated_at": (None, LATER)}


def test_ignore_lists_can_be_overridden(conn):
    before = snapshot(conn)
    SimClock(conn).advance(timedelta(days=1))
    changes = diff(before, snapshot(conn), ignored_tables=frozenset())
    assert changes == [
        Change("update", "sim_clock", (1,), {"now": ("2026-11-03T09:00:00", "2026-11-04T09:00:00")})
    ]


def test_schema_change_is_an_error(conn):
    before = snapshot(conn)
    conn.execute("ALTER TABLE suppliers ADD COLUMN note TEXT")
    with pytest.raises(ValueError):
        diff(before, snapshot(conn))


def test_table_without_primary_key_is_an_error(conn):
    conn.execute("CREATE TABLE notes (body TEXT)")
    with pytest.raises(ValueError):
        snapshot(conn)


def test_changes_are_sorted(conn):
    changes = changes_after(
        conn,
        "UPDATE suppliers SET rating = 1 WHERE id = 2",
        "UPDATE purchase_requests SET version = 2 WHERE id = 12",
        "UPDATE purchase_requests SET version = 2 WHERE id = 2",
        "UPDATE budgets SET spent = spent + 1 WHERE department_id = 3",
    )
    assert [(change.table, change.key) for change in changes] == [
        ("budgets", (3, "2026-Q4")),
        ("purchase_requests", (2,)),
        ("purchase_requests", (12,)),
        ("suppliers", (2,)),
    ]


def test_other_connection_sees_only_committed_changes(tmp_path):
    path = tmp_path / "sim.db"
    writer = build_database(path)
    reader = connect(path)
    before = snapshot(reader)

    writer.execute("UPDATE purchase_requests SET status = 'IN_REVIEW' WHERE id = 12")
    assert diff(before, snapshot(reader)) == []

    writer.commit()
    assert len(diff(before, snapshot(reader))) == 1

    writer.close()
    reader.close()


def test_finds_exactly_what_a_submission_changed(conn):
    changes = changes_after(
        conn,
        "UPDATE purchase_requests SET status = 'PENDING_APPROVAL', version = 2 WHERE id = 4",
        "UPDATE budgets SET reserved = reserved + 13770000 WHERE department_id = 4",
        "INSERT INTO approvals (id, request_id, level, approver_id) VALUES (12, 4, 'head', 12)",
        f"INSERT INTO audit_logs (actor, action, ts) VALUES ('agent', 'submit', '{LATER}')",
    )
    assert [(change.kind, change.table, change.key) for change in changes] == [
        ("insert", "approvals", (12,)),
        ("update", "budgets", (4, "2026-Q4")),
        ("update", "purchase_requests", (4,)),
    ]
    assert changes[1].fields == {"reserved": (0, 13_770_000)}


def test_readable_description(conn):
    changes = changes_after(conn, "UPDATE purchase_requests SET status = 'IN_REVIEW' WHERE id = 12")
    assert str(changes[0]) == "update purchase_requests[12] status: 'NEW' -> 'IN_REVIEW'"
