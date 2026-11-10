"""Yêu cầu cho lớp bảo vệ ở `agent/harness/guard.py`. Các test này đỏ cho đến khi bạn viết xong từng hàm."""

import asyncio
import json

import pytest
from langchain_core.messages import ToolMessage

from agent.harness import guard

OK = {"ok": True, "data": {"id": 1}, "truncated": False}
POLICY = {"ok": False, "error_type": "policy", "message": "Hồ sơ còn vi phạm", "hint": "Đổi báo giá"}
TRANSIENT = {"ok": False, "error_type": "transient", "message": "Database đang bận", "hint": "Hãy thử lại"}


def tool_message(text: str, status: str = "success") -> ToolMessage:
    return ToolMessage(content=[{"type": "text", "text": text}], tool_call_id="c1", name="get_request", status=status)


def as_message(result: dict) -> ToolMessage:
    return tool_message(json.dumps(result, ensure_ascii=False))


# fingerprint


def test_same_call_gives_same_fingerprint_whatever_the_key_order():
    first = guard.fingerprint("create_proposal", {"request_id": 1, "quotation_ids": [2, 1], "version": 2})
    second = guard.fingerprint("create_proposal", {"version": 2, "quotation_ids": [2, 1], "request_id": 1})
    assert first == second


def test_fingerprint_ignores_identity_fields():
    plain = guard.fingerprint("get_request", {"request_id": 1})
    assert guard.fingerprint("get_request", {"request_id": 1, "actor_id": 30, "task_id": "x"}) == plain


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("get_request", {"request_id": 2}),
        ("check_policy", {"request_id": 1}),
        ("get_request", {"request_id": 1, "extra": True}),
        ("get_request", {"request_id": "1"}),
    ],
)
def test_fingerprint_differs_when_call_differs(name, args):
    assert guard.fingerprint(name, args) != guard.fingerprint("get_request", {"request_id": 1})


def test_fingerprint_handles_vietnamese_text():
    assert guard.fingerprint("get_quotations", {"item": "Màn hình"}) != guard.fingerprint("get_quotations", {"item": "Máy in"})


# check_limits


def prints(times: int, name: str = "get_request", args: dict | None = None) -> list[str]:
    return [guard.fingerprint(name, args or {"request_id": 1})] * times


@pytest.mark.parametrize("seen", [0, 1])
def test_first_and_second_call_are_ok(seen):
    assert guard.check_limits(seen, prints(seen), "get_request", {"request_id": 1}).action == "ok"


def test_third_identical_call_warns_with_reason():
    decision = guard.check_limits(2, prints(2), "get_request", {"request_id": 1})
    assert decision.action == "warn"
    assert decision.reason


@pytest.mark.parametrize("seen", [3, 5])
def test_fourth_identical_call_and_later_stop(seen):
    decision = guard.check_limits(seen, prints(seen), "get_request", {"request_id": 1})
    assert decision.action == "stop"
    assert decision.reason


def test_other_calls_do_not_count_as_repeats():
    history = prints(3, "get_request", {"request_id": 2}) + prints(3, "check_policy", {"request_id": 1})
    assert guard.check_limits(6, history, "get_request", {"request_id": 1}).action == "ok"


def test_changing_identity_does_not_escape_repeat_detection():
    decision = guard.check_limits(3, prints(3), "get_request", {"request_id": 1, "actor_id": 30})
    assert decision.action == "stop"


def test_step_limit():
    assert guard.check_limits(guard.MAX_STEPS - 1, [], "get_request", {"request_id": 1}).action == "ok"
    decision = guard.check_limits(guard.MAX_STEPS, [], "get_request", {"request_id": 1})
    assert decision.action == "stop"
    assert decision.reason


def test_step_limit_can_be_changed():
    assert guard.check_limits(5, [], "get_request", {"request_id": 1}, max_steps=5).action == "stop"
    assert guard.check_limits(4, [], "get_request", {"request_id": 1}, max_steps=5).action == "ok"


def test_decision_is_the_provided_type():
    assert isinstance(guard.check_limits(0, [], "get_request", {"request_id": 1}), guard.Decision)


# override_identity

ERP_ARGS = {"actor_id", "request_id", "version", "task_id"}


def test_llm_cannot_impersonate_someone_else():
    result = guard.override_identity({"request_id": 1, "actor_id": 30, "task_id": "khac"}, ERP_ARGS, 3, "task-1")
    assert result == {"request_id": 1, "actor_id": 3, "task_id": "task-1"}


def test_identity_is_added_when_llm_leaves_it_out():
    assert guard.override_identity({"request_id": 1}, ERP_ARGS, 3, "task-1") == {
        "request_id": 1,
        "actor_id": 3,
        "task_id": "task-1",
    }


def test_identity_is_not_added_to_tools_without_it():
    # get_quotations chỉ nhận "item".
    assert guard.override_identity({"item": "Máy in", "actor_id": 30}, {"item"}, 3, "task-1") == {"item": "Máy in"}


def test_task_id_only_when_tool_accepts_it():
    result = guard.override_identity({"request_id": 1, "task_id": "khac"}, {"actor_id", "request_id"}, 3, "task-1")
    assert result == {"request_id": 1, "actor_id": 3}


def test_original_args_are_not_changed():
    args = {"request_id": 1, "actor_id": 30}
    guard.override_identity(args, ERP_ARGS, 3, "task-1")
    assert args == {"request_id": 1, "actor_id": 30}


# parse_result


@pytest.mark.parametrize("result", [OK, POLICY, TRANSIENT])
def test_contract_results_pass_through(result):
    assert guard.parse_result(as_message(result)) == result


def test_text_split_into_several_blocks_is_joined():
    text = json.dumps(OK)
    message = ToolMessage(
        content=[{"type": "text", "text": text[:10]}, {"type": "text", "text": text[10:]}],
        tool_call_id="c1",
        status="success",
    )
    assert guard.parse_result(message) == OK


def test_mcp_argument_error_becomes_validation():
    text = "Error executing tool get_request: 1 validation error for get_requestArguments\nactor_id\n  Input should be a valid integer"
    result = guard.parse_result(tool_message(text, status="error"))
    assert result["ok"] is False
    assert result["error_type"] == "validation"
    assert "Input should be a valid integer" in result["message"]
    assert result["hint"]


def test_long_error_text_is_cut_to_500_characters():
    result = guard.parse_result(tool_message("x" * 2000, status="error"))
    assert len(result["message"]) <= 500


@pytest.mark.parametrize("text", ["không phải JSON", '{"data": 1}', "[1, 2]"])
def test_output_outside_contract_becomes_validation(text):
    result = guard.parse_result(tool_message(text))
    assert result["ok"] is False
    assert result["error_type"] == "validation"


# call_with_retry


class FakeInvoke:
    """Trả lần lượt các kết quả cho trước. Một Exception trong danh sách thì bị ném ra."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.received = []

    async def __call__(self, args):
        self.received.append(args)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return as_message(outcome)


class FakeSleep:
    def __init__(self):
        self.waits = []

    async def __call__(self, seconds):
        self.waits.append(seconds)


def retry(invoke, **kwargs):
    sleep = FakeSleep()
    result = asyncio.run(guard.call_with_retry(invoke, {"request_id": 1}, sleep=sleep, **kwargs))
    return result, sleep.waits


def test_success_is_returned_without_retry():
    invoke = FakeInvoke(OK)
    result, waits = retry(invoke)
    assert result == OK
    assert waits == []
    assert invoke.received == [{"request_id": 1}]


def test_transient_error_is_retried_until_success():
    invoke = FakeInvoke(TRANSIENT, OK)
    result, waits = retry(invoke)
    assert result == OK
    assert waits == [0.5]


def test_waits_grow_and_stop_after_max_attempts():
    invoke = FakeInvoke(TRANSIENT, TRANSIENT, TRANSIENT, OK)
    result, waits = retry(invoke)
    assert result["error_type"] == "transient"
    assert waits == [0.5, 1]
    assert len(invoke.received) == 3


@pytest.mark.parametrize("failure", [POLICY, {**POLICY, "error_type": "validation"}, {**POLICY, "error_type": "not_found"}])
def test_other_errors_are_not_retried(failure):
    invoke = FakeInvoke(failure, OK)
    result, waits = retry(invoke)
    assert result == failure
    assert waits == []


def test_lost_connection_is_retried():
    invoke = FakeInvoke(ConnectionError("server đã tắt"), OK)
    result, _ = retry(invoke)
    assert result == OK


def test_exception_never_escapes():
    invoke = FakeInvoke(ConnectionError("a"), ConnectionError("b"), ConnectionError("c"))
    result, waits = retry(invoke)
    assert result["ok"] is False
    assert result["error_type"] == "transient"
    assert result["hint"]
    assert waits == [0.5, 1]


def test_max_attempts_one_means_no_retry():
    invoke = FakeInvoke(TRANSIENT, OK)
    result, waits = retry(invoke, max_attempts=1)
    assert result["error_type"] == "transient"
    assert waits == []


def test_mcp_argument_error_is_not_retried():
    class InvokeError(FakeInvoke):
        async def __call__(self, args):
            self.received.append(args)
            return tool_message("Error executing tool get_request: bad", status="error")

    invoke = InvokeError()
    result, waits = retry(invoke)
    assert result["error_type"] == "validation"
    assert len(invoke.received) == 1


# pending_updates


def test_submission_adds_pending_approval():
    result = {"ok": True, "data": {"request_id": 5, "status": "PENDING_APPROVAL"}, "truncated": False}
    assert guard.pending_updates("submit_for_approval", result) == ([5], [])


def test_question_adds_pending_reply():
    result = {"ok": True, "data": {"clarification_id": 7, "request_id": 3}, "truncated": False}
    assert guard.pending_updates("ask_requester", result) == ([], [7])


@pytest.mark.parametrize("name", ["submit_for_approval", "ask_requester"])
def test_failed_call_adds_nothing(name):
    assert guard.pending_updates(name, POLICY) == ([], [])


def test_other_tools_add_nothing():
    result = {"ok": True, "data": {"request_id": 5}, "truncated": False}
    assert guard.pending_updates("create_proposal", result) == ([], [])
