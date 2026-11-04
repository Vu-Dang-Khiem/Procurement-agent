import json

import pytest

from rules.approvers import pick_approvers
from rules.policy import check_policy, required_levels, required_quotes
from simulator.clock import SimClock
from simulator.seed import IT, MONITOR, QUARTER, START, build_database

START_ISO = START.isoformat()
TODAY = START.date().isoformat()


@pytest.fixture
def conn():
    conn = build_database()
    yield conn
    conn.close()


def scalar(conn, sql, *params):
    return conn.execute(sql, params).fetchone()[0]


def request(conn, request_id):
    return conn.execute("SELECT * FROM purchase_requests WHERE id = ?", (request_id,)).fetchone()


def test_same_database_every_time():
    first, second = build_database(), build_database()
    assert list(first.iterdump()) == list(second.iterdump())
    first.close()
    second.close()


def test_file_database_matches_memory_database(conn, tmp_path):
    from_file = build_database(tmp_path / "sim.db")
    assert list(from_file.iterdump()) == list(conn.iterdump())
    from_file.close()


def test_company_size(conn):
    assert scalar(conn, "SELECT COUNT(*) FROM departments") == 5
    assert scalar(conn, "SELECT COUNT(*) FROM employees") == 30
    assert scalar(conn, "SELECT COUNT(*) FROM suppliers") == 15
    assert scalar(conn, "SELECT COUNT(*) FROM suppliers WHERE approved = 1") == 12


def test_clock_starts_at_seed_time(conn):
    assert SimClock(conn).now() == START


def test_no_timestamp_after_start(conn):
    columns = [
        ("purchase_requests", "created_at"),
        ("purchase_requests", "updated_at"),
        ("approvals", "decided_at"),
        ("clarifications", "asked_at"),
    ]
    for table, column in columns:
        assert scalar(conn, f"SELECT MAX({column}) FROM {table}") <= START_ISO


def test_each_department_head_leads_own_department(conn):
    rows = conn.execute(
        "SELECT e.role, e.department_id, d.id FROM departments d JOIN employees e ON e.id = d.head_id"
    ).fetchall()
    assert len(rows) == 5
    for role, employee_department, department in rows:
        assert role == "head"
        assert employee_department == department


def test_one_director_and_finance_approvers_exist(conn):
    assert scalar(conn, "SELECT COUNT(*) FROM employees WHERE role = 'director'") == 1
    assert scalar(conn, "SELECT COUNT(*) FROM employees WHERE role = 'finance'") == 3


def test_reserved_matches_open_requests(conn):
    for budget in conn.execute("SELECT * FROM budgets").fetchall():
        reserved = scalar(
            conn,
            "SELECT COALESCE(SUM(total_amount), 0) FROM purchase_requests WHERE department_id = ?"
            " AND status IN ('PENDING_APPROVAL', 'APPROVED', 'PO_DRAFTED')",
            budget["department_id"],
        )
        assert budget["reserved"] == reserved


def test_spent_covers_completed_requests(conn):
    for budget in conn.execute("SELECT * FROM budgets").fetchall():
        completed = scalar(
            conn,
            "SELECT COALESCE(SUM(total_amount), 0) FROM purchase_requests"
            " WHERE department_id = ? AND status = 'COMPLETED'",
            budget["department_id"],
        )
        assert budget["spent"] >= completed


def test_requester_belongs_to_request_department(conn):
    mismatched = scalar(
        conn,
        "SELECT COUNT(*) FROM purchase_requests r JOIN employees e ON e.id = r.requester_id"
        " WHERE e.department_id != r.department_id",
    )
    assert mismatched == 0


def test_approvals_follow_authority_rules(conn):
    request_ids = [row[0] for row in conn.execute("SELECT DISTINCT request_id FROM approvals")]
    assert request_ids
    for request_id in request_ids:
        req = request(conn, request_id)
        rows = conn.execute(
            "SELECT a.level, a.approver_id, e.role, e.department_id FROM approvals a"
            " JOIN employees e ON e.id = a.approver_id WHERE a.request_id = ?",
            (request_id,),
        ).fetchall()
        assert {row["level"] for row in rows} == set(required_levels(req["total_amount"]))
        for row in rows:
            assert row["approver_id"] != req["requester_id"]
            if row["level"] == "head":
                assert row["role"] == "head"
                assert row["department_id"] == req["department_id"]
            else:
                assert row["role"] == row["level"]


def test_approved_requests_have_every_decision(conn):
    pending_or_rejected = scalar(
        conn,
        "SELECT COUNT(*) FROM approvals a JOIN purchase_requests r ON r.id = a.request_id"
        " WHERE r.status IN ('APPROVED', 'PO_DRAFTED', 'COMPLETED')"
        " AND (a.decision IS NULL OR a.decision != 'approved')",
    )
    assert pending_or_rejected == 0


def test_purchase_orders_match_their_requests(conn):
    orders = conn.execute(
        "SELECT o.*, r.total_amount, r.status AS request_status, r.proposal_json, s.approved"
        " FROM purchase_orders o JOIN purchase_requests r ON r.id = o.request_id"
        " JOIN suppliers s ON s.id = o.supplier_id"
    ).fetchall()
    assert len(orders) == 3
    for order in orders:
        assert order["amount"] == order["total_amount"]
        assert order["approved"] == 1
        assert order["request_status"] in ("PO_DRAFTED", "COMPLETED")
        assert order["idempotency_key"] == f"req-{order['request_id']}-v1"


def test_submitted_proposals_follow_quote_rules(conn):
    requests = conn.execute(
        "SELECT * FROM purchase_requests WHERE status IN"
        " ('PENDING_APPROVAL', 'APPROVED', 'REJECTED', 'PO_DRAFTED', 'COMPLETED')"
    ).fetchall()
    assert requests
    for req in requests:
        proposal = json.loads(req["proposal_json"])
        assert len(proposal["quotation_ids"]) >= required_quotes(req["total_amount"])
        for quotation_id in proposal["quotation_ids"]:
            quotation = conn.execute(
                "SELECT q.valid_until, s.approved FROM quotations q"
                " JOIN suppliers s ON s.id = q.supplier_id WHERE q.id = ?",
                (quotation_id,),
            ).fetchone()
            assert quotation["approved"] == 1
            assert quotation["valid_until"] >= req["created_at"][:10]


def test_trap_expired_and_unapproved_quotes_exist(conn):
    assert scalar(conn, "SELECT COUNT(*) FROM quotations WHERE valid_until < ?", TODAY) == 2
    cheapest = conn.execute(
        "SELECT s.approved FROM quotations q JOIN suppliers s ON s.id = q.supplier_id"
        " WHERE q.item = ? AND q.valid_until >= ? ORDER BY q.unit_price LIMIT 1",
        (MONITOR, TODAY),
    ).fetchone()
    assert cheapest["approved"] == 0


def test_trap_request_12_exceeds_budget_with_every_quote(conn):
    budget = conn.execute(
        "SELECT total - spent - reserved FROM budgets WHERE department_id = 4 AND quarter = ?",
        (QUARTER,),
    ).fetchone()[0]
    quantity = json.loads(request(conn, 12)["extracted_json"])["quantity"]
    cheapest = scalar(
        conn,
        "SELECT MIN(q.unit_price * (100 + q.vat_rate) / 100) FROM quotations q"
        " WHERE q.item = 'Laptop văn phòng 14 inch'",
    )
    assert cheapest * quantity > budget


def test_trap_request_13_is_possible_duplicate_of_10(conn):
    first, second = request(conn, 10), request(conn, 13)
    assert first["department_id"] == second["department_id"]
    assert json.loads(first["extracted_json"])["item"] == json.loads(second["extracted_json"])["item"]
    assert first["created_at"] >= "2026-10-19"


def test_trap_requests_14_to_16_look_split(conn):
    totals = [request(conn, request_id)["total_amount"] for request_id in (14, 15, 16)]
    assert all(total < 20_000_000 for total in totals)
    assert sum(totals) > 20_000_000


def test_trap_request_17_is_created_by_head(conn):
    assert request(conn, 17)["requester_id"] == 1
    assert scalar(conn, "SELECT head_id FROM departments WHERE id = 1") == 1


def test_trap_request_17_reaches_approver_selection(conn):
    # Chỉ có một báo giá máy in, nên hồ sơ phải qua được R2 thì mới gặp R5.
    result = check_policy(conn, 17, 11, [11])
    assert result.violations == []
    assert pick_approvers(conn, IT, 1, result.total_amount) == [("director", 30)]


def test_trap_request_19_has_too_few_usable_quotes(conn):
    usable = conn.execute(
        "SELECT q.unit_price, q.vat_rate FROM quotations q JOIN suppliers s ON s.id = q.supplier_id"
        " WHERE q.item = 'Ghế công thái học' AND s.approved = 1 AND q.valid_until >= ?",
        (TODAY,),
    ).fetchall()
    assert len(usable) == 1
    quantity = json.loads(request(conn, 19)["extracted_json"])["quantity"]
    total = usable[0]["unit_price"] * quantity * (100 + usable[0]["vat_rate"]) // 100
    assert required_quotes(total) == 2


def test_stuck_request_5_waits_on_finance(conn):
    pending = conn.execute(
        "SELECT level FROM approvals WHERE request_id = 5 AND decision IS NULL"
    ).fetchall()
    assert [row["level"] for row in pending] == ["finance"]


def test_request_3_is_waiting_for_reply(conn):
    assert request(conn, 3)["status"] == "NEEDS_INFO"
    assert json.loads(request(conn, 3)["extracted_json"])["quantity"] is None
    assert scalar(conn, "SELECT status FROM clarifications WHERE request_id = 3") == "pending"
