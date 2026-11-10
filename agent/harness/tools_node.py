"""Node tools: chạy các lời gọi tool của agent qua lớp bảo vệ ở `guard.py`.

Thứ tự cho mỗi lời gọi: kiểm tra giới hạn, ghi đè danh tính, gọi tool có thử lại lỗi tạm thời,
trả lỗi về dạng thông tin để agent tự xử lý. Các lời gọi chạy lần lượt, không song song, để hai tool ghi không xung đột.
"""

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool

from agent.harness import guard
from agent.report import check_report
from agent.state import AgentState
from agent.tool_specs import REPORT_TOOL, tool_arguments
from mcp_servers.common import dumps, error


def _message(call: dict, result: dict) -> ToolMessage:
    return ToolMessage(
        content=dumps(result),
        tool_call_id=call["id"],
        name=call["name"],
        status="success" if result.get("ok") else "error",
    )


def make_tools_node(tools: dict[str, BaseTool], max_steps: int = guard.MAX_STEPS):
    async def tools_node(state: AgentState) -> dict:
        calls = state["messages"][-1].tool_calls
        approvals = list(state["pending_approvals"])
        replies = list(state["pending_replies"])

        if any(call["name"] == REPORT_TOOL for call in calls):
            report, result = check_report(calls, approvals, replies)
            return {"messages": [_message(call, result) for call in calls], "final_report": report}

        step_count = state["step_count"]
        fingerprints = list(state["tool_fingerprints"])
        messages: list[ToolMessage] = []
        stop_reason = None
        for call in calls:
            if stop_reason is not None:
                # Mọi lời gọi đều phải có kết quả, kể cả khi nhiệm vụ đã dừng.
                messages.append(_message(call, error("policy", "Nhiệm vụ đã dừng, lời gọi này không được chạy")))
                continue
            decision = guard.check_limits(step_count, fingerprints, call["name"], call["args"], max_steps)
            if decision.action == "stop":
                stop_reason = decision.reason
                messages.append(_message(call, error("policy", decision.reason or "Nhiệm vụ đã dừng")))
                continue

            tool = tools.get(call["name"])
            if tool is None:
                result = error("validation", f"Không có tool {call['name']}", "Chỉ dùng các tool trong danh sách đã cho")
            else:
                args = guard.override_identity(call["args"], tool_arguments(tool), state["actor_id"], state["task_id"])

                async def invoke(args: dict, tool: BaseTool = tool, call: dict = call) -> ToolMessage:
                    return await tool.ainvoke({"name": call["name"], "args": args, "id": call["id"], "type": "tool_call"})

                result = await guard.call_with_retry(invoke, args)
                new_approvals, new_replies = guard.pending_updates(call["name"], result)
                approvals += [id_ for id_ in new_approvals if id_ not in approvals]
                replies += [id_ for id_ in new_replies if id_ not in replies]

            if decision.action == "warn":
                result = {**result, "warning": decision.reason}
            step_count += 1
            fingerprints.append(guard.fingerprint(call["name"], call["args"]))
            messages.append(_message(call, result))

        return {
            "messages": messages,
            "step_count": step_count,
            "tool_fingerprints": fingerprints,
            "pending_approvals": approvals,
            "pending_replies": replies,
            "stop_reason": stop_reason,
        }

    return tools_node
