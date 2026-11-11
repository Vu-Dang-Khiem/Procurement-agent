import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier

import pytest

from evals.state_diff import diff, snapshot
from mcp_servers.common import DB_ENV
from mcp_servers.erp import server
from simulator.clock import SimClock
from simulator.db import connect
from simulator.seed import IT, MARKETING, QUARTER, SALES, build_database

IT_HEAD, IT_STAFF, OTHER_IT_STAFF, HR_STAFF, FINANCE_STAFF, DIRECTOR = 1, 3, 4, 9, 27, 30
TASK = "task-1"


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "sim.db"
    build_database(path).close()
    monkeypatch.setenv(DB_ENV, str(path))
    conn = connect(path)
    yield conn
    conn.close()


def budget(conn, department_id):
    return conn.execute(
        "SELECT total, spent, reserved FROM budgets WHERE department_id = ? AND quarter = ?",
        (department_id, QUARTER),
    ).fetchone()


def approve_all(conn, request_id):
    """Việc của backend sau khi người duyệt quyết định, agent không có tool này."""
    with conn:
        conn.execute(
            "UPDATE approvals SET decision = 'approved', decided_at = '2026-11-03T10:00:00'"
            " WHERE request_id = ? AND decision IS NULL",
            (request_id,),
        )
        conn.execute(
            "UPDATE purchase_requests SET status = 'APPROVED', version = version + 1 WHERE id = ?",
            (request_id,),
        )


def version_of(conn, request_id):
    return conn.execute("SELECT version FROM purchase_requests WHERE id = ?", (request_id,)).fetchone()[0]


def fails_with(result, error_type):
    assert result["ok"] is False, result
    assert result["error_type"] == error_type, result
    return True


# get_request


def test_get_request_returns_compact_view(db):
    data = server.get_request(IT_STAFF, 1)["data"]
    assert data["status"] == "NEW"
    assert data["version"] == 1
    assert data["item"] == "Màn hình 24 inch"
    assert data["quantity"] == 10
    assert data["proposal"] is None
    assert "extracted_json" not in data


def test_get_request_shows_when_things_happened(db):
    # Đủ để trả lời "vì sao yêu cầu 5 bị treo": cấp trưởng phòng duyệt ngày 29/10, Finance chưa quyết định.
    data = server.get_request(4, 5)["data"]
    assert data["created_at"] == "2026-10-28T10:00:00"
    assert data["updated_at"] == "2026-10-29T09:00:00"
    assert [row["decided_at"] for row in data["approvals"]] == ["2026-10-29T09:00:00", None]


def test_get_request_shows_rejection_reason_and_question(db):
    assert server.get_request(21, 8)["data"]["approvals"][-1]["reason"] == "Giảm còn 6 cái"
    assert server.get_request(20, 3)["data"]["clarifications"][0]["status"] == "pending"


@pytest.mark.parametrize(
    ("actor_id", "allowed"),
    [(IT_STAFF, True), (OTHER_IT_STAFF, False), (IT_HEAD, True), (HR_STAFF, False), (FINANCE_STAFF, True), (DIRECTOR, True)],
)
def test_get_request_permissions(db, actor_id, allowed):
    result = server.get_request(actor_id, 1)
    assert result["ok"] is allowed
    if not allowed:
        assert fails_with(result, "permission")


def test_get_request_missing_request_or_actor(db):
    assert fails_with(server.get_request(IT_STAFF, 999), "not_found")
    assert fails_with(server.get_request(999, 1), "not_found")


# list_requests


def listed(result):
    return [row["id"] for row in result["data"]["requests"]]


def test_staff_sees_only_own_requests(db):
    assert sorted(listed(server.list_requests(19))) == [14, 15, 16]
    assert listed(server.list_requests(IT_STAFF)) == [1]


def test_head_sees_whole_department(db):
    assert sorted(listed(server.list_requests(IT_HEAD))) == [1, 5, 9, 17]


@pytest.mark.parametrize("actor_id", [FINANCE_STAFF, DIRECTOR, 26])
def test_finance_and_director_see_everything(db, actor_id):
    assert server.list_requests(actor_id, limit=50)["data"]["total"] == 19


def test_newest_first(db):
    ids = listed(server.list_requests(FINANCE_STAFF, limit=50))
    created = [server.get_request(FINANCE_STAFF, id_)["data"]["created_at"] for id_ in ids]
    assert created == sorted(created, reverse=True)


def test_filters(db):
    assert listed(server.list_requests(IT_STAFF, item="màn hình")) == [1]
    assert sorted(listed(server.list_requests(DIRECTOR, item="MÀN HÌNH", limit=50))) == [1, 7, 10, 13]
    assert sorted(listed(server.list_requests(DIRECTOR, status="PENDING_APPROVAL"))) == [5, 6]
    assert sorted(listed(server.list_requests(DIRECTOR, department_id=SALES, status="IN_REVIEW"))) == [14, 15, 16]
    created_today = listed(server.list_requests(DIRECTOR, created_from="2026-11-03", created_to="2026-11-03"))
    assert sorted(created_today) == [1, 16, 17]


def test_department_filter_cannot_widen_permission(db):
    assert listed(server.list_requests(IT_STAFF, department_id=MARKETING)) == []


def test_long_list_is_truncated_with_total(db):
    result = server.list_requests(DIRECTOR, limit=5)
    assert result["truncated"] is True
    assert len(result["data"]["requests"]) == 5
    assert result["data"]["total"] == 19
    assert server.list_requests(DIRECTOR, limit=19)["truncated"] is False


def test_list_rows_are_compact(db):
    row = server.list_requests(IT_STAFF)["data"]["requests"][0]
    assert set(row) == {"id", "status", "department_id", "requester_id", "item", "quantity", "total_amount", "created_at"}


@pytest.mark.parametrize(
    "fields",
    [{"status": "DONE"}, {"created_from": "03/11/2026"}, {"limit": 0}, {"limit": 51}],
)
def test_list_rejects_bad_filters(db, fields):
    result = server.list_requests(DIRECTOR, **fields)
    assert fails_with(result, "validation")
    assert result.get("hint") or "limit" in result["message"]


def test_list_unknown_actor(db):
    assert fails_with(server.list_requests(999), "not_found")


# get_department_budget


def test_budget_defaults_to_current_quarter(db):
    data = server.get_department_budget(IT_STAFF, IT)["data"]
    assert data["quarter"] == QUARTER
    assert data["available"] == 500_000_000 - 120_000_000 - 89_650_000


def test_budget_permissions(db):
    assert fails_with(server.get_department_budget(IT_HEAD, MARKETING), "permission")
    assert server.get_department_budget(FINANCE_STAFF, MARKETING)["ok"]
    assert server.get_department_budget(DIRECTOR, MARKETING)["ok"]


def test_budget_rejects_bad_quarter_with_hint(db):
    result = server.get_department_budget(IT_STAFF, IT, "Q4/2026")
    assert fails_with(result, "validation")
    assert "2026-Q4" in result["hint"]


def test_budget_missing_quarter(db):
    assert fails_with(server.get_department_budget(IT_STAFF, IT, "2030-Q1"), "not_found")


# get_quotations


def test_quotations_mark_unusable_ones(db):
    quotes = {quote["id"]: quote for quote in server.get_quotations("màn hình")["data"]["quotations"]}
    assert set(quotes) == {1, 2, 3, 4, 5}
    assert quotes[4]["expired"] is True
    assert quotes[5]["supplier_approved"] is False
    assert quotes[1]["expired"] is False and quotes[1]["supplier_approved"] is True


def test_quotations_never_expose_source_file(db):
    assert "source_file" not in server.get_quotations("Máy in")["data"]["quotations"][0]


def test_quotations_expiry_follows_sim_clock(db):
    SimClock(db).set(datetime(2026, 12, 2, 9, 0, 0))
    quote = next(q for q in server.get_quotations("Màn hình")["data"]["quotations"] if q["id"] == 1)
    assert quote["expired"] is True


def test_quotations_unknown_item_lists_catalog(db):
    result = server.get_quotations("Xe tải")
    assert fails_with(result, "not_found")
    assert "Màn hình 24 inch" in result["hint"]


def test_quotations_empty_item(db):
    assert fails_with(server.get_quotations("  "), "validation")


# check_policy


def test_check_policy_reports_without_writing(db):
    before = snapshot(db)
    data = server.check_policy(17, 12, 8, [6, 7, 8])["data"]
    assert [finding["rule"] for finding in data["violations"]] == ["R1"]
    assert data["required_levels"] == ["head", "finance", "director"]
    assert diff(before, snapshot(db)) == []


def test_check_policy_needs_permission(db):
    assert fails_with(server.check_policy(HR_STAFF, 1, 1, [1, 2]), "permission")


# update_request


def test_update_changes_only_given_fields(db):
    before = snapshot(db)
    data = server.update_request(20, 3, 1, quantity=8, needed_by="2026-11-15", task_id=TASK)["data"]
    assert data["version"] == 2
    changes = diff(before, snapshot(db))
    assert [(change.kind, change.table) for change in changes] == [("update", "purchase_requests")]
    assert changes[0].fields.keys() == {"extracted_json", "version"}
    extracted = json.loads(changes[0].fields["extracted_json"][1])
    assert extracted == {"item": "Tai nghe họp trực tuyến", "category": "it", "quantity": 8, "needed_by": "2026-11-15"}


def test_update_with_old_version_is_refused(db):
    server.update_request(20, 3, 1, quantity=8)
    result = server.update_request(20, 3, 1, quantity=9)
    assert fails_with(result, "validation")
    assert "get_request" in result["hint"]
    assert server.get_request(20, 3)["data"]["quantity"] == 8


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"quantity": 0},
        {"quantity": -1},
        {"category": "xe"},
        {"needed_by": "30/11/2026"},
        {"item": " "},
    ],
)
def test_update_rejects_bad_values(db, fields):
    before = snapshot(db)
    assert fails_with(server.update_request(20, 3, 1, **fields), "validation")
    assert diff(before, snapshot(db)) == []


@pytest.mark.parametrize("status", ["APPROVED", "PENDING_APPROVAL", "PO_DRAFTED", "COMPLETED", "CANCELLED"])
def test_update_cannot_jump_to_other_statuses(db, status):
    assert fails_with(server.update_request(IT_STAFF, 1, 1, status=status), "policy")


def test_update_moves_new_to_in_review(db):
    assert server.update_request(IT_STAFF, 1, 1, status="IN_REVIEW")["data"]["status"] == "IN_REVIEW"


def test_update_refused_after_submission(db):
    assert fails_with(server.update_request(4, 5, 1, quantity=1), "policy")


def test_update_needs_permission(db):
    assert fails_with(server.update_request(HR_STAFF, 3, 1, quantity=8), "permission")


def test_changing_quantity_clears_old_proposal(db):
    data = server.update_request(14, 4, 1, quantity=20)["data"]
    assert data["proposal_cleared"] is True
    request = server.get_request(14, 4)["data"]
    assert request["proposal"] is None
    assert request["total_amount"] is None


def test_changing_date_keeps_proposal(db):
    assert server.update_request(14, 4, 1, needed_by="2026-11-20")["data"]["proposal_cleared"] is False
    assert server.get_request(14, 4)["data"]["proposal"] is not None


# create_proposal


def test_create_proposal_saves_clean_proposal(db):
    data = server.create_proposal(IT_STAFF, 1, 1, 2, [2, 1], "Giá tốt hơn, cùng bảo hành", TASK)["data"]
    assert data["status"] == "IN_REVIEW"
    assert data["version"] == 2
    assert data["total_amount"] == 55_000_000
    assert data["violations"] == []
    proposal = server.get_request(IT_STAFF, 1)["data"]["proposal"]
    assert proposal["selected_quotation_id"] == 2
    assert proposal["quotation_ids"] == [2, 1]
    assert proposal["rationale"] == "Giá tốt hơn, cùng bảo hành"


def test_create_proposal_records_violations(db):
    data = server.create_proposal(17, 12, 1, 8, [6, 7, 8])["data"]
    assert [finding["rule"] for finding in data["violations"]] == ["R1"]
    proposal = server.get_request(17, 12)["data"]["proposal"]
    assert proposal["violations"][0]["rule"] == "R1"


def test_create_proposal_does_not_touch_budget(db):
    before = budget(db, IT)["reserved"]
    server.create_proposal(IT_STAFF, 1, 1, 2, [2, 1])
    assert budget(db, IT)["reserved"] == before


def test_create_proposal_removes_duplicate_ids(db):
    server.create_proposal(IT_STAFF, 1, 1, 2, [2, 1, 2])
    assert server.get_request(IT_STAFF, 1)["data"]["proposal"]["quotation_ids"] == [2, 1]


def test_create_proposal_refused_while_waiting_for_reply(db):
    assert fails_with(server.create_proposal(20, 3, 1, 18, [18]), "policy")


def test_create_proposal_refused_after_submission(db):
    assert fails_with(server.create_proposal(4, 5, 1, 6, [6, 7]), "policy")


def test_create_proposal_long_rationale(db):
    assert fails_with(server.create_proposal(IT_STAFF, 1, 1, 2, [2, 1], "x" * 1001), "validation")


def test_create_proposal_unknown_quote(db):
    assert fails_with(server.create_proposal(IT_STAFF, 1, 1, 999, [999]), "not_found")


# submit_for_approval


def test_submit_reserves_budget_and_creates_approvals(db):
    version = server.create_proposal(IT_STAFF, 1, 1, 2, [2, 1])["data"]["version"]
    before = snapshot(db)
    data = server.submit_for_approval(IT_STAFF, 1, version, TASK)["data"]
    assert data["status"] == "PENDING_APPROVAL"
    assert data["approvers"] == [{"level": "head", "approver_id": 1}, {"level": "finance", "approver_id": 27}]

    changes = {(change.kind, change.table): change for change in diff(before, snapshot(db))}
    assert changes.keys() == {("update", "budgets"), ("insert", "approvals"), ("update", "purchase_requests")}
    assert changes[("update", "budgets")].fields == {"reserved": (89_650_000, 144_650_000)}
    rows = db.execute("SELECT task_id, decision FROM approvals WHERE request_id = 1").fetchall()
    assert [tuple(row) for row in rows] == [(TASK, None), (TASK, None)]


def test_submit_twice_does_not_reserve_twice(db):
    version = server.create_proposal(IT_STAFF, 1, 1, 2, [2, 1])["data"]["version"]
    server.submit_for_approval(IT_STAFF, 1, version)
    reserved = budget(db, IT)["reserved"]
    assert fails_with(server.submit_for_approval(IT_STAFF, 1, version), "validation")
    assert budget(db, IT)["reserved"] == reserved
    assert db.execute("SELECT COUNT(*) FROM approvals WHERE request_id = 1").fetchone()[0] == 2


def test_submit_blocked_by_violation_changes_nothing(db):
    version = server.create_proposal(17, 12, 1, 8, [6, 7, 8])["data"]["version"]
    before = snapshot(db)
    result = server.submit_for_approval(17, 12, version)
    assert fails_with(result, "policy")
    assert "R1" in result["message"]
    assert diff(before, snapshot(db)) == []


def test_submit_rechecks_quotes_that_expired_later(db):
    version = server.create_proposal(IT_STAFF, 1, 1, 2, [2, 1])["data"]["version"]
    SimClock(db).set(datetime(2026, 12, 15, 9, 0, 0))
    result = server.submit_for_approval(IT_STAFF, 1, version)
    assert fails_with(result, "policy")
    assert "R7" in result["message"]


def test_second_request_cannot_use_same_budget(db):
    with db:
        db.execute("UPDATE budgets SET total = spent + reserved + 60000000 WHERE department_id = ?", (IT,))
    version = server.create_proposal(IT_STAFF, 1, 1, 2, [2, 1])["data"]["version"]
    assert server.submit_for_approval(IT_STAFF, 1, version)["ok"]
    assert budget(db, IT)["total"] - budget(db, IT)["spent"] - budget(db, IT)["reserved"] == 5_000_000
    # Yêu cầu 17 cũng của IT: 14,3 triệu vượt 5 triệu còn lại.
    version = server.create_proposal(IT_HEAD, 17, 1, 11, [11])["data"]["version"]
    result = server.submit_for_approval(IT_HEAD, 17, version)
    assert fails_with(result, "policy")
    assert "R1" in result["message"]


def test_concurrent_submissions_cannot_share_budget(db):
    # Còn 60 triệu: 55 triệu của yêu cầu 1 và 14,3 triệu của yêu cầu 17 không thể cùng được giữ chỗ.
    with db:
        db.execute("UPDATE budgets SET total = spent + reserved + 60000000 WHERE department_id = ?", (IT,))
    first = server.create_proposal(IT_STAFF, 1, 1, 2, [2, 1])["data"]["version"]
    second = server.create_proposal(IT_HEAD, 17, 1, 11, [11])["data"]["version"]
    barrier = Barrier(2)

    def submit(actor_id, request_id, version):
        barrier.wait()
        return server.submit_for_approval(actor_id, request_id, version)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, (IT_STAFF, IT_HEAD), (1, 17), (first, second)))

    assert sorted(result["ok"] for result in results) == [False, True]
    assert fails_with(next(result for result in results if not result["ok"]), "policy")
    row = budget(db, IT)
    assert row["spent"] + row["reserved"] <= row["total"]
    assert db.execute("SELECT COUNT(*) FROM purchase_requests WHERE status = 'PENDING_APPROVAL' AND id IN (1, 17)").fetchone()[0] == 1


def test_submit_without_proposal(db):
    version = server.update_request(IT_STAFF, 1, 1, status="IN_REVIEW")["data"]["version"]
    assert fails_with(server.submit_for_approval(IT_STAFF, 1, version), "validation")


def test_submit_from_new_is_refused(db):
    assert fails_with(server.submit_for_approval(IT_STAFF, 1, 1), "policy")


def test_r5_head_request_goes_to_director(db):
    version = server.create_proposal(IT_HEAD, 17, 1, 11, [11])["data"]["version"]
    data = server.submit_for_approval(IT_HEAD, 17, version)["data"]
    assert data["approvers"] == [{"level": "director", "approver_id": DIRECTOR}]


def test_submit_passes_flags_to_approver(db):
    data = server.submit_for_approval(19, 16, 1)["data"]
    assert {finding["rule"] for finding in data["flags"]} == {"R6", "R8"}
    assert {finding["rule"] for finding in server.get_request(19, 16)["data"]["proposal"]["flags"]} == {"R6", "R8"}


# create_po_draft


def test_po_draft_for_fully_approved_request(db):
    before = snapshot(db)
    data = server.create_po_draft(FINANCE_STAFF, 7, 1, TASK)["data"]
    assert data["already_existed"] is False
    assert data["supplier"] == "Minh Long Tech"
    assert data["amount"] == 11_440_000
    assert data["idempotency_key"] == "req-7-v1"

    changes = diff(before, snapshot(db))
    assert {(change.kind, change.table) for change in changes} == {
        ("insert", "purchase_orders"),
        ("update", "purchase_requests"),
    }
    assert server.get_request(FINANCE_STAFF, 7)["data"]["status"] == "PO_DRAFTED"


def test_po_draft_retry_returns_same_order(db):
    first = server.create_po_draft(FINANCE_STAFF, 7, 1)["data"]
    before = snapshot(db)
    second = server.create_po_draft(FINANCE_STAFF, 7, 1)["data"]
    assert second["already_existed"] is True
    assert second["id"] == first["id"]
    assert diff(before, snapshot(db)) == []


def test_po_draft_with_new_version_does_not_create_second_order(db):
    version = server.create_po_draft(FINANCE_STAFF, 7, 1)["data"]["request_version"]
    assert fails_with(server.create_po_draft(FINANCE_STAFF, 7, version), "policy")
    assert db.execute("SELECT COUNT(*) FROM purchase_orders WHERE request_id = 7").fetchone()[0] == 1


def test_po_draft_refused_before_approval(db):
    assert fails_with(server.create_po_draft(4, 5, 1), "policy")
    assert fails_with(server.create_po_draft(IT_STAFF, 1, 1), "policy")


def test_po_draft_needs_every_approval_even_if_status_says_approved(db):
    with db:
        db.execute("UPDATE purchase_requests SET status = 'APPROVED' WHERE id = 5")
    result = server.create_po_draft(4, 5, 1)
    assert fails_with(result, "policy")
    assert db.execute("SELECT COUNT(*) FROM purchase_orders WHERE request_id = 5").fetchone()[0] == 0


def test_po_draft_rechecks_supplier(db):
    with db:
        db.execute("UPDATE suppliers SET approved = 0 WHERE id = 2")
    result = server.create_po_draft(FINANCE_STAFF, 7, 1)
    assert fails_with(result, "policy")
    assert "R3" in result["message"]


def test_po_draft_with_stale_version(db):
    assert fails_with(server.create_po_draft(FINANCE_STAFF, 7, 99), "validation")


def test_po_draft_needs_permission(db):
    assert fails_with(server.create_po_draft(HR_STAFF, 7, 1), "permission")


# Đi trọn một quy trình


def test_full_flow_from_new_to_po_draft(db):
    start = snapshot(db)
    assert server.get_request(IT_STAFF, 1)["data"]["status"] == "NEW"
    version = server.create_proposal(IT_STAFF, 1, 1, 2, [2, 1], task_id=TASK)["data"]["version"]
    version = server.submit_for_approval(IT_STAFF, 1, version, TASK)["data"]["version"]
    assert fails_with(server.create_po_draft(IT_STAFF, 1, version, TASK), "policy")
    approve_all(db, 1)
    order = server.create_po_draft(IT_STAFF, 1, version + 1, TASK)["data"]

    assert order["amount"] == 55_000_000
    assert order["idempotency_key"] == f"req-1-v{version + 1}"
    request = server.get_request(IT_STAFF, 1)["data"]
    assert request["status"] == "PO_DRAFTED"
    assert budget(db, IT)["reserved"] == 89_650_000 + 55_000_000
    tables = {change.table for change in diff(start, snapshot(db))}
    assert tables == {"approvals", "budgets", "purchase_orders", "purchase_requests"}
    tools = [row[0] for row in db.execute("SELECT tool_name FROM audit_logs WHERE task_id = ? ORDER BY id", (TASK,))]
    assert tools == ["create_proposal", "submit_for_approval", "create_po_draft"]


def test_rejected_request_can_be_fixed_and_resubmitted(db):
    # Finance từ chối yêu cầu 8 và đề nghị giảm còn 6 cái.
    version = server.update_request(21, 8, 1, quantity=6)["data"]["version"]
    version = server.create_proposal(21, 8, version, 9, [9, 10], "Giảm còn 6 cái theo đề nghị của Finance")["data"]["version"]
    before_reserved = budget(db, SALES)["reserved"]
    data = server.submit_for_approval(21, 8, version)["data"]
    assert data["total_amount"] == 51_480_000
    assert budget(db, SALES)["reserved"] == before_reserved + 51_480_000
    # Quyết định từ chối cũ vẫn còn để tra cứu, nhưng lần gửi mới phải được duyệt lại đủ.
    assert fails_with(server.create_po_draft(21, 8, data["version"]), "policy")
    approve_all(db, 8)
    assert server.create_po_draft(21, 8, data["version"] + 1)["ok"]


def test_resubmit_removes_leftover_pending_approval(db):
    with db:
        db.execute("INSERT INTO approvals (request_id, level, approver_id) VALUES (8, 'director', 30)")
    # Chọn báo giá 10 thay cho 9, vì R9 chặn gửi lại y nguyên hồ sơ đã bị từ chối.
    version = server.create_proposal(21, 8, 1, 10, [9, 10])["data"]["version"]
    assert server.submit_for_approval(21, 8, version)["ok"]
    pending = db.execute("SELECT level FROM approvals WHERE request_id = 8 AND decision IS NULL").fetchall()
    assert [row[0] for row in pending] == ["head", "finance"]


def test_failed_write_leaves_no_audit_log(db):
    assert fails_with(server.submit_for_approval(IT_STAFF, 1, 1), "policy")
    assert db.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0] == 0
