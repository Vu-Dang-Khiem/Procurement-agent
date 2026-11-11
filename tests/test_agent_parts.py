"""Các phần của agent không phụ thuộc lớp bảo vệ. Không gọi model."""

import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.fake_model import ScriptedModel, call, calls, last_result
from agent.mcp_tools import open_mcp_tools
from agent.model import cache_counts, estimate_cost, make_main_model
from agent.prompts import SYSTEM_PROMPT
from agent.report import check_report
from agent.tool_specs import HIDDEN_ARGS, REPORT_TOOL, tool_specs
from simulator.seed import build_database

ALL_TOOLS = [
    "ask_requester",
    "check_policy",
    "create_po_draft",
    "create_proposal",
    "get_department_budget",
    "get_quotations",
    "get_request",
    "list_requests",
    "read_quotation_pdf",
    "submit_for_approval",
    "update_request",
]


@pytest.fixture(scope="module")
def tools(tmp_path_factory):
    """Mở 3 server thật một lần, lấy danh sách tool và mô tả, rồi đóng lại."""
    path = tmp_path_factory.mktemp("db") / "sim.db"
    build_database(path).close()

    async def load():
        async with open_mcp_tools(path) as tools:
            result = await tools["get_request"].ainvoke(
                {"name": "get_request", "args": {"actor_id": 3, "request_id": 1}, "id": "c1", "type": "tool_call"}
            )
            return list(tools), tool_specs(tools), result

    return asyncio.run(load())


def test_all_tools_are_loaded_in_name_order(tools):
    names, _, _ = tools
    assert names == ALL_TOOLS


def test_tool_call_returns_tool_message(tools):
    _, _, result = tools
    assert isinstance(result, ToolMessage)
    assert '"ok":true' in result.content[0]["text"]


def test_model_never_sees_identity_arguments(tools):
    _, specs, _ = tools
    for spec in specs:
        assert not set(HIDDEN_ARGS) & set(spec["input_schema"].get("properties", {})), spec["name"]
        assert not set(HIDDEN_ARGS) & set(spec["input_schema"].get("required", []))


def test_specs_are_sorted_and_include_report(tools):
    _, specs, _ = tools
    names = [spec["name"] for spec in specs]
    assert names == sorted(ALL_TOOLS + [REPORT_TOOL])
    assert all(spec["description"] for spec in specs)


def test_specs_are_identical_every_time(tools):
    # Phần đầu prompt phải giống hệt giữa các lần để trúng cache.
    _, specs, _ = tools
    assert specs == tools[1]


def test_system_prompt_has_no_dates():
    assert "2026" not in SYSTEM_PROMPT


def test_main_model_turns_on_prompt_cache():
    model = make_main_model()
    assert model.model == "claude-haiku-5-5"
    assert model.model_kwargs["cache_control"] == {"type": "ephemeral"}


def test_cache_writes_reported_under_ttl_are_counted():
    # Dạng thật mà langchain-anthropic trả về ở lần gọi đầu của một nhiệm vụ.
    usage = {
        "input_tokens": 5162,
        "output_tokens": 96,
        "input_token_details": {
            "cache_creation": 0,
            "cache_read": 0,
            "ephemeral_5m_input_tokens": 5158,
            "ephemeral_1h_input_tokens": 0,
        },
    }
    assert cache_counts(usage) == (0, 5158)
    assert cache_counts({"input_token_details": {"cache_read": 7, "cache_creation": 3}}) == (7, 3)
    assert cache_counts(None) == (0, 0)


def test_cost_counts_cache_at_its_own_price():
    # 10.000 token vào, trong đó 8.000 đọc từ cache và 1.000 ghi cache, cùng 500 token ra, trên Haiku 5.5.
    cost = estimate_cost("claude-haiku-5-5", 10_000, 500, cache_read=8_000, cache_write=1_000)
    expected = (1_000 * 0.10 + 8_000 * 0.01 + 1_000 * 0.125 + 500 * 0.50) / 1_000_000
    assert cost == pytest.approx(expected)
    assert estimate_cost("model-la", 1, 1) is None


# check_report


def report_call(**args):
    return {"name": REPORT_TOOL, "args": args, "id": "r1", "type": "tool_call"}


def test_completed_report_is_accepted():
    report, result = check_report([report_call(status="completed", summary="Đã tạo đơn nháp", request_ids=[1])], [], [])
    assert report == {"status": "completed", "summary": "Đã tạo đơn nháp", "request_ids": [1]}
    assert result["ok"] is True


def test_report_must_be_alone():
    other = {"name": "get_request", "args": {"request_id": 1}, "id": "c1", "type": "tool_call"}
    report, result = check_report([other, report_call(status="completed", summary="xong")], [], [])
    assert report is None
    assert result["error_type"] == "validation"


@pytest.mark.parametrize(
    ("status", "approvals", "replies", "accepted"),
    [
        ("waiting_approval", [], [], False),
        ("waiting_approval", [1], [], True),
        ("waiting_reply", [], [], False),
        ("waiting_reply", [], [4], True),
        ("blocked", [], [], True),
    ],
)
def test_waiting_status_needs_something_pending(status, approvals, replies, accepted):
    report, result = check_report([report_call(status=status, summary="lý do")], approvals, replies)
    assert (report is not None) is accepted
    assert result["ok"] is accepted


@pytest.mark.parametrize("args", [{"status": "done", "summary": "x"}, {"status": "completed", "summary": "  "}, {"status": "completed", "summary": "x" * 2001}])
def test_bad_report_is_rejected(args):
    report, result = check_report([report_call(**args)], [], [])
    assert report is None
    assert result["error_type"] == "validation"


# model giả


def test_scripted_model_replays_steps_and_records_input():
    model = ScriptedModel([call("get_request", request_id=1), lambda messages: AIMessage(content=str(len(messages)))])
    assert model.bind_tools([{"name": "x"}]) is model
    first = asyncio.run(model.ainvoke([HumanMessage(content="a")]))
    second = asyncio.run(model.ainvoke([HumanMessage(content="a"), HumanMessage(content="b")]))
    assert first.tool_calls[0]["name"] == "get_request"
    assert second.content == "2"
    assert len(model.received) == 2
    with pytest.raises(AssertionError):
        asyncio.run(model.ainvoke([]))


def test_call_ids_are_unique():
    message = calls(("get_request", {"request_id": 1}), ("get_request", {"request_id": 2}))
    ids = [tool_call["id"] for tool_call in message.tool_calls]
    assert len(set(ids)) == 2


def test_last_result_reads_latest_tool_message():
    messages = [
        ToolMessage(content='{"ok":true,"data":{"version":1}}', tool_call_id="a"),
        AIMessage(content=""),
        ToolMessage(content='{"ok":true,"data":{"version":2}}', tool_call_id="b"),
    ]
    assert last_result(messages)["data"]["version"] == 2
