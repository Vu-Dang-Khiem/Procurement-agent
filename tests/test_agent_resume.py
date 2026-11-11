"""Dừng chờ người, tắt chương trình, rồi tiếp tục ở một lần chạy mới. Model giả, không gọi API.

Mỗi lần `run_part` mở MCP server và checkpointer mới, giống như chạy lại chương trình sau khi tắt.
"""

import asyncio
import json

import pytest
from quarantine_fakes import FakeModel

from agent.fake_model import ScriptedModel, call, last_result
from agent.runner import is_waiting, open_agent, open_reader, resume_task, start_task, task_values
from api.human import answer_question, decide_approval, unresolved
from quarantine.schemas import EmailDraft, EmailItemDraft
from simulator.db import connect
from simulator.seed import build_database

TASK = "demo-1"


@pytest.fixture
def paths(tmp_path):
    db = tmp_path / "company.db"
    build_database(db).close()
    return db, tmp_path / "runs" / "checkpoints.db"


def run_part(paths, steps, action):
    db, checkpoints = paths
    model = ScriptedModel(steps)

    async def main():
        async with open_agent(model, db, checkpoints) as graph:
            return await action(graph)

    return asyncio.run(main()), model


def start(graph, actor_id=3, goal="Xử lý yêu cầu mua 10 màn hình của phòng IT"):
    return start_task(graph, TASK, actor_id, "Người giao việc", "nhân viên", goal)


def query(paths, sql, *params):
    conn = connect(paths[0])
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def with_conn(paths, action):
    conn = connect(paths[0])
    try:
        return action(conn)
    finally:
        conn.close()


UNTIL_SUBMITTED = [
    call("get_request", request_id=1),
    call("create_proposal", request_id=1, version=1, selected_quotation_id=2, quotation_ids=[2, 1]),
    lambda messages: call("submit_for_approval", request_id=1, version=last_result(messages)["data"]["version"]),
    call("submit_final_report", status="waiting_approval", summary="Đã gửi duyệt yêu cầu #1", request_ids=[1]),
]


def test_stops_while_waiting_and_finishes_after_restart(paths):
    first, _ = run_part(paths, UNTIL_SUBMITTED, start)
    assert first["__interrupt__"]
    assert first["pending_approvals"] == [1]
    assert first["final_report"]["status"] == "waiting_approval"

    # Chương trình đã tắt. Con người duyệt qua backend.
    with_conn(paths, lambda conn: decide_approval(conn, 1, 1, "approved"))
    assert with_conn(paths, lambda conn: unresolved(conn, [1], [])) == ["yêu cầu #1 còn chờ duyệt"]
    with_conn(paths, lambda conn: decide_approval(conn, 1, 27, "approved"))
    assert with_conn(paths, lambda conn: unresolved(conn, [1], [])) == []

    after_approval = [
        call("get_request", request_id=1),
        lambda messages: call("create_po_draft", request_id=1, version=last_result(messages)["data"]["version"]),
        call("submit_final_report", status="completed", summary="Đã tạo đơn nháp", request_ids=[1]),
    ]
    final, model = run_part(paths, after_approval, lambda graph: resume_task(graph, TASK))

    assert final["final_report"]["status"] == "completed"
    assert "__interrupt__" not in final
    assert final["pending_approvals"] == []
    order = query(paths, "SELECT amount, idempotency_key, status FROM purchase_orders WHERE request_id = 1")
    assert [tuple(row) for row in order] == [(55_000_000, "req-1-v4", "draft")]
    assert query(paths, "SELECT status FROM purchase_requests WHERE id = 1")[0][0] == "PO_DRAFTED"
    # Lần chạy mới nhận lại toàn bộ lịch sử từ checkpoint, kèm lời báo đã có kết quả.
    history = model.received[0]
    assert history[1].content.startswith("Người giao việc")
    assert "yêu cầu #1" in history[-1].content
    assert final["step_count"] == 5


def test_progress_survives_restart_and_can_be_read_without_model(paths):
    run_part(paths, UNTIL_SUBMITTED, start)
    db, checkpoints = paths

    async def read():
        async with open_reader(checkpoints) as graph:
            return await is_waiting(graph, TASK), await task_values(graph, TASK)

    waiting, values = asyncio.run(read())
    assert waiting is True
    assert values["pending_approvals"] == [1]


def test_rejection_reaches_the_agent(paths):
    run_part(paths, UNTIL_SUBMITTED, start)
    with_conn(paths, lambda conn: decide_approval(conn, 1, 1, "rejected", "Giảm còn 6 cái"))
    after = [
        call("get_request", request_id=1),
        call("submit_final_report", status="blocked", summary="Bị từ chối, cần người yêu cầu xác nhận số lượng"),
    ]
    final, model = run_part(paths, after, lambda graph: resume_task(graph, TASK))
    seen = json.loads(model.received[1][-1].content)
    assert seen["data"]["status"] == "REJECTED"
    assert seen["data"]["approvals"][0]["reason"] == "Giảm còn 6 cái"
    assert final["final_report"]["status"] == "blocked"


def test_reply_flow(paths):
    steps = [
        call("get_request", request_id=3),
        call("ask_requester", request_id=3, version=1, question="Bạn cần bao nhiêu tai nghe, trước ngày nào?"),
        lambda messages: call(
            "submit_final_report", status="waiting_reply", summary="Đã hỏi lại số lượng",
            request_ids=[3],
        ),
    ]
    first, _ = run_part(paths, steps, lambda graph: start(graph, actor_id=20, goal="Xử lý yêu cầu #3"))
    question_id = first["pending_replies"][0]

    draft = EmailDraft(
        kind="reply",
        items=[EmailItemDraft(item=None, category=None, quantity=8, unit_price_min=None, unit_price_max=None,
                              total_estimate=None, needed_by="2026-11-15")],
        suspicious_content=False,
    )
    with_conn(paths, lambda conn: answer_question(conn, FakeModel(draft), question_id, 20, "8 cái, trước 15/11"))

    after = [
        call("get_request", request_id=3),
        lambda messages: call(
            "update_request", request_id=3, version=last_result(messages)["data"]["version"],
            quantity=8, needed_by="2026-11-15", status="IN_REVIEW",
        ),
        call("submit_final_report", status="completed", summary="Đã cập nhật số lượng"),
    ]
    final, model = run_part(paths, after, lambda graph: resume_task(graph, TASK))
    seen = json.loads(model.received[1][-1].content)
    replies = [item["reply"] for item in seen["data"]["clarifications"] if item["reply"]]
    assert replies[0]["items"][0]["quantity"] == 8
    assert final["final_report"]["status"] == "completed"
    row = query(paths, "SELECT status, extracted_json FROM purchase_requests WHERE id = 3")[0]
    assert row[0] == "IN_REVIEW"
    assert json.loads(row[1])["quantity"] == 8


def test_same_task_id_cannot_start_twice(paths):
    run_part(paths, UNTIL_SUBMITTED, start)
    with pytest.raises(ValueError):
        run_part(paths, [], start)


def test_finished_task_cannot_be_resumed(paths):
    steps = [call("submit_final_report", status="blocked", summary="Không làm")]
    run_part(paths, steps, start)
    with pytest.raises(ValueError):
        run_part(paths, [], lambda graph: resume_task(graph, TASK))
