"""Duyệt, từ chối và trả lời câu hỏi ở phía backend. Dùng model giả cho LLM cách ly."""

import pytest
from quarantine_fakes import FakeModel

from api.human import answer_question, decide_approval, describe_pending, unresolved
from mcp_servers.common import DB_ENV
from mcp_servers.erp import server as erp
from quarantine.schemas import EmailDraft, EmailItemDraft
from rules.errors import InvalidInput, NotFound, PermissionDenied, PolicyViolation
from simulator.db import connect
from simulator.seed import IT, QUARTER, SALES, build_database

STAFF, HEAD, FINANCE = 3, 1, 27
RESERVED_BEFORE = 89_650_000


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "sim.db"
    build_database(path).close()
    monkeypatch.setenv(DB_ENV, str(path))
    conn = connect(path)
    yield conn
    conn.close()


@pytest.fixture
def submitted(db):
    """Yêu cầu #1 đã được gửi duyệt: trưởng phòng IT rồi Finance."""
    version = erp.create_proposal(STAFF, 1, 1, 2, [2, 1])["data"]["version"]
    assert erp.submit_for_approval(STAFF, 1, version)["ok"]
    return db


def reserved(db, department_id=IT):
    return db.execute(
        "SELECT reserved FROM budgets WHERE department_id = ? AND quarter = ?", (department_id, QUARTER)
    ).fetchone()[0]


def status(db, request_id=1):
    return db.execute("SELECT status FROM purchase_requests WHERE id = ?", (request_id,)).fetchone()[0]


def reply_draft(quantity=8, needed_by="2026-11-15"):
    item = EmailItemDraft(
        item=None, category=None, quantity=quantity, unit_price_min=None, unit_price_max=None,
        total_estimate=None, needed_by=needed_by,
    )
    return EmailDraft(kind="reply", items=[item], suspicious_content=False)


def test_approvals_go_in_order_and_finish_as_approved(submitted):
    first = decide_approval(submitted, 1, HEAD, "approved")
    assert first["status"] == "PENDING_APPROVAL"
    assert first["level"] == "head"
    second = decide_approval(submitted, 1, FINANCE, "approved")
    assert second["status"] == "APPROVED"
    assert status(submitted) == "APPROVED"
    # Giữ chỗ vẫn còn, vì tiền chưa chi.
    assert reserved(submitted) == RESERVED_BEFORE + 55_000_000


def test_finance_cannot_skip_the_head(submitted):
    with pytest.raises(PermissionDenied) as error:
        decide_approval(submitted, 1, FINANCE, "approved")
    assert "#1" in error.value.hint


def test_other_people_cannot_decide(submitted):
    for actor_id in (STAFF, 8, 30):
        with pytest.raises(PermissionDenied):
            decide_approval(submitted, 1, actor_id, "approved")


def test_rejection_needs_reason_and_releases_budget(submitted):
    with pytest.raises(InvalidInput):
        decide_approval(submitted, 1, HEAD, "rejected", "  ")
    result = decide_approval(submitted, 1, HEAD, "rejected", "Giảm số lượng")
    assert result["status"] == "REJECTED"
    assert reserved(submitted) == RESERVED_BEFORE
    row = submitted.execute("SELECT reason FROM approvals WHERE request_id = 1 AND decision = 'rejected'").fetchone()
    assert row[0] == "Giảm số lượng"


def test_decision_bumps_version_so_old_agent_writes_fail(submitted):
    old_version = erp.get_request(STAFF, 1)["data"]["version"]
    decide_approval(submitted, 1, HEAD, "approved")
    decide_approval(submitted, 1, FINANCE, "approved")
    assert erp.get_request(STAFF, 1)["data"]["version"] == old_version + 1
    assert erp.create_po_draft(STAFF, 1, old_version)["error_type"] == "validation"


def test_cannot_decide_when_not_waiting(db):
    with pytest.raises(PolicyViolation):
        decide_approval(db, 1, HEAD, "approved")
    with pytest.raises(PolicyViolation):
        decide_approval(db, 7, 26, "approved")


def test_bad_decision_word(submitted):
    with pytest.raises(InvalidInput):
        decide_approval(submitted, 1, HEAD, "ok")


def test_decision_is_audited(submitted):
    decide_approval(submitted, 1, HEAD, "approved")
    row = submitted.execute("SELECT actor, action FROM audit_logs WHERE action = 'approval_decision'").fetchone()
    assert tuple(row) == ("employee:1", "approval_decision")


def test_existing_stuck_request_can_be_approved_by_finance(db):
    # Yêu cầu #5 đã qua trưởng phòng, đang chờ Finance #27.
    assert decide_approval(db, 5, FINANCE, "approved")["status"] == "APPROVED"


def test_answer_goes_through_quarantine_and_is_stored_as_data(db):
    model = FakeModel(reply_draft())
    result = answer_question(db, model, 1, 20, "Bên em cần 8 cái, trước 15/11 nhé.")
    assert result["reply"]["items"][0]["quantity"] == 8
    assert "<document>" in model.calls[0].document
    row = db.execute("SELECT status, reply_json, replied_at FROM clarifications WHERE id = 1").fetchone()
    assert row["status"] == "answered"
    assert "Bên em" not in row["reply_json"]
    assert row["replied_at"]


def test_only_requester_can_answer(db):
    with pytest.raises(PermissionDenied):
        answer_question(db, FakeModel(reply_draft()), 1, 21, "8 cái")


def test_cannot_answer_twice(db):
    answer_question(db, FakeModel(reply_draft()), 1, 20, "8 cái")
    with pytest.raises(PolicyViolation):
        answer_question(db, FakeModel(reply_draft()), 1, 20, "9 cái")


def test_answer_checks(db):
    with pytest.raises(InvalidInput):
        answer_question(db, FakeModel(reply_draft()), 1, 20, "   ")
    with pytest.raises(NotFound):
        answer_question(db, FakeModel(reply_draft()), 999, 20, "8 cái")


def test_unresolved_lists_what_is_still_waiting(submitted):
    assert unresolved(submitted, [1], [1]) == ["yêu cầu #1 còn chờ duyệt", "câu hỏi #1 còn chờ trả lời"]
    decide_approval(submitted, 1, HEAD, "rejected", "Giảm số lượng")
    answer_question(submitted, FakeModel(reply_draft()), 1, 20, "8 cái")
    assert unresolved(submitted, [1], [1]) == []


def test_cancelled_request_does_not_block(db):
    with db:
        db.execute("UPDATE purchase_requests SET status = 'CANCELLED' WHERE id = 3")
    assert unresolved(db, [], [1]) == []


def test_describe_pending_shows_whose_turn(submitted):
    info = describe_pending(submitted, 1)
    assert info["turn"] == {"level": "head", "approver_id": HEAD}
    assert info["total_amount"] == 55_000_000


def test_sales_budget_untouched_by_it_rejection(submitted):
    before = reserved(submitted, SALES)
    decide_approval(submitted, 1, HEAD, "rejected", "Không cần nữa")
    assert reserved(submitted, SALES) == before
