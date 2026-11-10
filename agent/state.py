from langgraph.graph import MessagesState


class AgentState(MessagesState):
    """Trạng thái của một nhiệm vụ, được checkpointer lưu sau mỗi bước.

    `actor_id` và `task_id` do backend gắn khi bắt đầu, LLM không sửa được.
    """

    actor_id: int
    task_id: str
    # Số lời gọi tool đã chạy, không tính lời gọi submit_final_report.
    step_count: int
    tool_fingerprints: list[str]
    pending_approvals: list[int]  # id các yêu cầu đang chờ duyệt
    pending_replies: list[int]  # id các câu hỏi đang chờ người yêu cầu trả lời
    final_report: dict | None
    stop_reason: str | None
    nudged: bool


def initial_state(actor_id: int, task_id: str, goal_message) -> dict:
    return {
        "messages": [goal_message],
        "actor_id": actor_id,
        "task_id": task_id,
        "step_count": 0,
        "tool_fingerprints": [],
        "pending_approvals": [],
        "pending_replies": [],
        "final_report": None,
        "stop_reason": None,
        "nudged": False,
    }
