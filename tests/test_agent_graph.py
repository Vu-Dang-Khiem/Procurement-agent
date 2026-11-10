"""Chạy cả đồ thị với model giả và 3 MCP server thật. Không gọi API.

Các test này cần lớp bảo vệ trong `agent/harness/guard.py` đã viết xong.
"""

import asyncio
import json

import pytest
from langchain_core.messages import AIMessage

from agent.fake_model import ScriptedModel, call, calls, last_result
from agent.graph import build_graph, run_config
from agent.mcp_tools import open_mcp_tools
from agent.prompts import SYSTEM_PROMPT, goal_message
from agent.state import initial_state
from simulator.db import connect
from simulator.seed import build_database

STAFF = 3  # Lê Minh Châu, người tạo yêu cầu #1
TASK = "task-1"
GOAL = "Xử lý yêu cầu mua 10 màn hình của phòng IT"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "sim.db"
    build_database(path).close()
    return path


def run(db_path, steps, max_steps=40):
    model = ScriptedModel(steps)

    async def main():
        async with open_mcp_tools(db_path) as tools:
            graph = build_graph(model, tools, max_steps=max_steps)
            start = initial_state(STAFF, TASK, goal_message(GOAL, "Lê Minh Châu", "nhân viên IT"))
            return await graph.ainvoke(start, run_config(TASK, max_steps))

    return asyncio.run(main()), model


def results(state):
    return [json.loads(message.content) for message in state["messages"] if message.type == "tool"]


def query(db_path, sql, *params):
    conn = connect(db_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


FULL_FLOW = [
    call("get_request", request_id=1),
    call("get_quotations", item="Màn hình 24 inch"),
    call("create_proposal", request_id=1, version=1, selected_quotation_id=2, quotation_ids=[2, 1], rationale="Giao 7 ngày"),
    lambda messages: call("submit_for_approval", request_id=1, version=last_result(messages)["data"]["version"]),
    call("submit_final_report", status="waiting_approval", summary="Đã gửi duyệt yêu cầu #1", request_ids=[1]),
]


def test_request_goes_from_new_to_waiting_for_approval(db_path):
    state, model = run(db_path, FULL_FLOW)
    assert state["final_report"]["status"] == "waiting_approval"
    assert state["pending_approvals"] == [1]
    assert state["stop_reason"] is None
    assert state["step_count"] == 4
    assert all(result["ok"] for result in results(state))
    assert query(db_path, "SELECT status FROM purchase_requests WHERE id = 1")[0][0] == "PENDING_APPROVAL"
    # Mọi thao tác ghi đều mang danh tính thật và mã nhiệm vụ do lớp bảo vệ gắn vào.
    rows = query(db_path, "SELECT actor, task_id, tool_name FROM audit_logs ORDER BY id")
    assert [tuple(row) for row in rows] == [
        ("employee:3", TASK, "create_proposal"),
        ("employee:3", TASK, "submit_for_approval"),
    ]
    # Model nhận câu lệnh hệ thống ở đầu và không bao giờ thấy actor_id trong mô tả tool.
    assert model.received[0][0].content == SYSTEM_PROMPT
    assert "actor_id" not in json.dumps(model.bound_tools)


def test_llm_cannot_act_as_the_director(db_path):
    steps = [
        call("get_request", request_id=12, actor_id=30),
        call("submit_final_report", status="blocked", summary="Không có quyền xem yêu cầu #12"),
    ]
    state, _ = run(db_path, steps)
    assert results(state)[0]["error_type"] == "permission"


def test_repeated_call_warns_then_stops(db_path):
    # Mỗi bước phải là một tin nhắn mới: LangGraph gộp các tin nhắn trùng id thành một.
    state, _ = run(db_path, [call("get_request", request_id=1) for _ in range(4)])
    found = results(state)
    assert "warning" not in found[1]
    assert found[2]["warning"]
    assert found[3]["error_type"] == "policy"
    assert state["stop_reason"]
    assert state["final_report"] is None
    assert state["step_count"] == 3


def test_step_limit_stops_the_task(db_path):
    steps = [call("get_request", request_id=1), call("get_quotations", item="Máy in"), call("get_request", request_id=2)]
    state, _ = run(db_path, steps, max_steps=2)
    assert state["stop_reason"]
    assert state["step_count"] == 2


def test_errors_come_back_as_information_and_loop_continues(db_path):
    steps = [
        call("approve_request", request_id=1),
        call("get_request", request_id="một"),
        call("submit_final_report", status="waiting_approval", summary="Đã gửi duyệt"),
        call("submit_final_report", status="blocked", summary="Không có tool phê duyệt"),
    ]
    state, _ = run(db_path, steps)
    found = results(state)
    assert [result["error_type"] for result in found[:3]] == ["validation", "validation", "validation"]
    assert state["final_report"]["status"] == "blocked"


def test_report_with_other_calls_is_refused(db_path):
    steps = [
        calls(("get_request", {"request_id": 1}), ("submit_final_report", {"status": "blocked", "summary": "x"})),
        call("submit_final_report", status="blocked", summary="Dừng"),
    ]
    state, _ = run(db_path, steps)
    assert state["final_report"]["summary"] == "Dừng"


def test_text_answer_gets_one_reminder_then_stops(db_path):
    steps = [AIMessage(content="Tôi nghĩ xong rồi."), AIMessage(content="Vẫn xong.")]
    state, model = run(db_path, steps)
    assert state["nudged"] is True
    assert state["final_report"] is None
    assert len(model.received) == 2


def test_parallel_calls_each_get_a_result(db_path):
    steps = [
        calls(("get_request", {"request_id": 1}), ("get_department_budget", {"department_id": 1})),
        call("submit_final_report", status="blocked", summary="Chỉ xem"),
    ]
    state, _ = run(db_path, steps)
    assert [result["ok"] for result in results(state)[:2]] == [True, True]
    assert state["step_count"] == 2
