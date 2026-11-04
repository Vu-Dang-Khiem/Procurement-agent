import json

import pytest
from mcp_session import run_session

from evals.state_diff import diff, snapshot
from mcp_servers.common import DB_ENV
from mcp_servers.mail import server
from simulator.db import connect
from simulator.seed import START, build_database

IT_STAFF, HR_STAFF, FINANCE_STAFF = 3, 9, 27
SALES_STAFF = 20  # người tạo yêu cầu 3, đang chờ trả lời
QUESTION = "Bạn cần màn hình loại nào, và trước ngày nào?"
TASK = "task-1"


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "sim.db"
    build_database(path).close()
    monkeypatch.setenv(DB_ENV, str(path))
    return path


@pytest.fixture
def db(db_path):
    conn = connect(db_path)
    yield conn
    conn.close()


def fails_with(result, error_type):
    assert result["ok"] is False, result
    assert result["error_type"] == error_type, result
    return True


def test_ask_records_question_and_moves_to_needs_info(db):
    before = snapshot(db)
    data = server.ask_requester(IT_STAFF, 1, 1, QUESTION, TASK)["data"]
    assert data["status"] == "NEEDS_INFO"
    assert data["version"] == 2
    assert data["asks_left"] == 1

    changes = {(change.kind, change.table): change for change in diff(before, snapshot(db))}
    assert changes.keys() == {("insert", "clarifications"), ("update", "purchase_requests")}
    assert changes[("update", "purchase_requests")].fields == {"status": ("NEW", "NEEDS_INFO"), "version": (1, 2)}
    row = db.execute("SELECT * FROM clarifications WHERE id = ?", (data["clarification_id"],)).fetchone()
    assert row["question"] == QUESTION
    assert row["task_id"] == TASK
    assert row["status"] == "pending"
    assert row["asked_at"] == START.isoformat()


def test_ask_always_goes_to_requester(db):
    # Trưởng phòng hỏi thay thì người nhận vẫn là người tạo yêu cầu.
    assert server.ask_requester(1, 1, 1, QUESTION)["data"]["recipient_id"] == IT_STAFF


def test_ask_from_in_review(db):
    assert server.ask_requester(14, 4, 1, QUESTION)["data"]["status"] == "NEEDS_INFO"


def test_ask_limit_is_two_per_request(db):
    # Yêu cầu 3 đã có sẵn một câu hỏi đang chờ.
    data = server.ask_requester(SALES_STAFF, 3, 1, QUESTION)["data"]
    assert data["asks_left"] == 0
    before = snapshot(db)
    result = server.ask_requester(SALES_STAFF, 3, data["version"], QUESTION)
    assert fails_with(result, "policy")
    assert result["hint"]
    assert diff(before, snapshot(db)) == []


def test_answered_questions_count_toward_limit(db):
    with db:
        db.execute("UPDATE clarifications SET status = 'answered' WHERE request_id = 3")
    version = server.ask_requester(SALES_STAFF, 3, 1, QUESTION)["data"]["version"]
    assert fails_with(server.ask_requester(SALES_STAFF, 3, version, QUESTION), "policy")


def test_cancelled_questions_do_not_count(db):
    with db:
        db.execute("UPDATE clarifications SET status = 'cancelled' WHERE request_id = 3")
    assert server.ask_requester(SALES_STAFF, 3, 1, QUESTION)["data"]["asks_left"] == 1


def test_retry_with_old_version_does_not_send_twice(db):
    assert server.ask_requester(IT_STAFF, 1, 1, QUESTION)["ok"]
    assert fails_with(server.ask_requester(IT_STAFF, 1, 1, QUESTION), "validation")
    assert db.execute("SELECT COUNT(*) FROM clarifications WHERE request_id = 1").fetchone()[0] == 1


@pytest.mark.parametrize(
    ("actor_id", "request_id"),
    [(4, 5), (FINANCE_STAFF, 7), (21, 8), (5, 9), (15, 10), (22, 11)],
)
def test_ask_refused_in_other_statuses(db, actor_id, request_id):
    # Đang chờ duyệt, đã duyệt, bị từ chối, đã có đơn nháp, đã xong, đã hủy.
    before = snapshot(db)
    assert fails_with(server.ask_requester(actor_id, request_id, 1, QUESTION), "policy")
    assert diff(before, snapshot(db)) == []


@pytest.mark.parametrize("question", ["", "   ", "x" * 501])
def test_bad_question_is_rejected(db, question):
    assert fails_with(server.ask_requester(IT_STAFF, 1, 1, question), "validation")


def test_question_of_max_length_is_accepted(db):
    assert server.ask_requester(IT_STAFF, 1, 1, "x" * 500)["ok"]


def test_ask_needs_permission(db):
    assert fails_with(server.ask_requester(HR_STAFF, 1, 1, QUESTION), "permission")


def test_missing_request(db):
    assert fails_with(server.ask_requester(IT_STAFF, 999, 1, QUESTION), "not_found")


def test_ask_writes_audit_log(db):
    server.ask_requester(IT_STAFF, 1, 1, QUESTION, TASK)
    row = db.execute("SELECT * FROM audit_logs").fetchone()
    assert row["task_id"] == TASK
    assert row["actor"] == f"employee:{IT_STAFF}"
    assert row["tool_name"] == "ask_requester"
    assert QUESTION in row["input"]


def test_over_mcp_tool_has_no_recipient_argument(db_path):
    async def scenario(session):
        tools = (await session.list_tools()).tools
        result = await session.call_tool(
            "ask_requester", {"actor_id": IT_STAFF, "request_id": 1, "version": 1, "question": QUESTION}
        )
        return tools, result

    tools, result = run_session("mcp_servers.mail.server", db_path, scenario)
    assert [tool.name for tool in tools] == ["ask_requester"]
    assert set(tools[0].inputSchema["properties"]) == {"actor_id", "request_id", "version", "question", "task_id"}
    assert json.loads(result.content[0].text)["data"]["recipient_id"] == IT_STAFF
