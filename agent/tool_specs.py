"""Mô tả tool gửi cho LLM.

`actor_id` và `task_id` bị bỏ khỏi schema mà LLM nhìn thấy: LLM không cần và không được điền.
Lớp bảo vệ vẫn ghi đè hai trường này, phòng khi LLM tự thêm vào.
"""

import copy

from langchain_core.tools import BaseTool

HIDDEN_ARGS = ("actor_id", "task_id")
REPORT_TOOL = "submit_final_report"
REPORT_STATUSES = ("blocked", "completed", "waiting_approval", "waiting_reply")
MAX_SUMMARY_LENGTH = 2000

REPORT_SPEC = {
    "name": REPORT_TOOL,
    "description": (
        "Nộp báo cáo khi đã xử lý xong mục tiêu, khi phải chờ người, hoặc khi không thể làm tiếp. "
        "Phải là lời gọi duy nhất trong lượt. "
        "status: completed (đã xong), waiting_approval (đã gửi duyệt, đang chờ người duyệt), "
        "waiting_reply (đã hỏi người yêu cầu, đang chờ trả lời), blocked (không thể hoàn thành, nêu rõ lý do). "
        "summary: nói ngắn gọn đã làm gì và kết quả, bằng tiếng Việt."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": list(REPORT_STATUSES)},
            "summary": {"type": "string", "maxLength": MAX_SUMMARY_LENGTH},
            "request_ids": {"type": "array", "items": {"type": "integer"}},
        },
        "required": ["status", "summary"],
        "additionalProperties": False,
    },
}


def tool_arguments(tool: BaseTool) -> set[str]:
    return set(_schema(tool).get("properties", {}))


def _schema(tool: BaseTool) -> dict:
    schema = tool.args_schema
    return schema if isinstance(schema, dict) else schema.model_json_schema()


def model_spec(tool: BaseTool) -> dict:
    schema = copy.deepcopy(_schema(tool))
    schema.pop("title", None)
    for name in HIDDEN_ARGS:
        schema.get("properties", {}).pop(name, None)
    schema["required"] = [name for name in schema.get("required", []) if name not in HIDDEN_ARGS]
    return {"name": tool.name, "description": tool.description, "input_schema": schema}


def tool_specs(tools: dict[str, BaseTool]) -> list[dict]:
    """Danh sách cố định, sắp xếp theo tên, để phần đầu prompt không đổi và giữ được cache."""
    specs = [model_spec(tool) for tool in tools.values()] + [REPORT_SPEC]
    return sorted(specs, key=lambda spec: spec["name"])
