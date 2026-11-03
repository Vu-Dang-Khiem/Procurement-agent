import sqlite3

import pytest

from simulator.db import connect, create_schema

NOW = "2026-11-03T09:00:00"


@pytest.fixture
def conn():
    conn = connect()
    create_schema(conn)
    conn.execute("INSERT INTO departments (id, name, head_id) VALUES (1, 'IT', 1)")
    conn.execute(
        "INSERT INTO employees (id, name, email, department_id, role)"
        " VALUES (1, 'Trưởng phòng IT', 'head@corp.test', 1, 'head')"
    )
    conn.execute(
        "INSERT INTO suppliers (id, name, approved, categories) VALUES (1, 'NCC A', 1, '[\"it\"]')"
    )
    conn.execute(
        "INSERT INTO purchase_requests (id, requester_id, department_id, source, created_at, updated_at)"
        " VALUES (1, 1, 1, 'seed', ?, ?)",
        (NOW, NOW),
    )
    conn.commit()
    yield conn
    conn.close()


def test_creates_all_tables(conn):
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    assert {row["name"] for row in rows} == {
        "departments",
        "employees",
        "budgets",
        "suppliers",
        "quotations",
        "purchase_requests",
        "approvals",
        "clarifications",
        "purchase_orders",
        "audit_logs",
    }


def test_rejects_unknown_foreign_key(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO employees (name, email, department_id, role)"
            " VALUES ('X', 'x@corp.test', 99, 'staff')"
        )


def test_rejects_unknown_department_head_on_commit(conn):
    conn.execute("INSERT INTO departments (name, head_id) VALUES ('HR', 99)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.commit()
    conn.rollback()


def test_only_director_may_have_no_department(conn):
    conn.execute(
        "INSERT INTO employees (name, email, role) VALUES ('Giám đốc', 'ceo@corp.test', 'director')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO employees (name, email, role) VALUES ('X', 'x@corp.test', 'staff')")


def test_rejects_unknown_request_status(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE purchase_requests SET status = 'DONE' WHERE id = 1")


def test_rejects_budget_overrun(conn):
    conn.execute(
        "INSERT INTO budgets (department_id, quarter, total, spent, reserved)"
        " VALUES (1, '2026-Q4', 100, 60, 40)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE budgets SET reserved = reserved + 1 WHERE department_id = 1")


def test_rejects_duplicate_idempotency_key(conn):
    insert = (
        "INSERT INTO purchase_orders (request_id, supplier_id, amount, idempotency_key)"
        " VALUES (1, 1, 1000, 'req-1-v1')"
    )
    conn.execute(insert)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert)


def test_rejects_invalid_json(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE purchase_requests SET proposal_json = 'không phải json' WHERE id = 1")


def test_rejects_wrong_column_type(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE purchase_requests SET total_amount = 'nhiều tiền' WHERE id = 1")


def test_decision_and_decided_at_go_together(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO approvals (request_id, level, approver_id, decision)"
            " VALUES (1, 'head', 1, 'approved')"
        )


def test_timestamps_have_no_real_clock_default(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO purchase_requests (requester_id, department_id, source) VALUES (1, 1, 'seed')"
        )
