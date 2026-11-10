"""Lớp bảo vệ: các quyết định mà node tools áp dụng cho mỗi lời gọi tool của agent.

PHẦN NÀY DO BẠN TỰ VIẾT. Mỗi hàm có mô tả yêu cầu ở docstring, và test tương ứng ở `tests/test_guard.py`.
Chạy `python -m pytest tests/test_guard.py -q` để xem còn hàm nào chưa đạt.
Node tools ở `agent/harness/tools_node.py` gọi các hàm này theo thứ tự ở mục 3 của tài liệu thiết kế.
"""
import asyncio
import json
import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from langchain_core.messages import ToolMessage

MAX_STEPS = 40
WARN_AT_REPEAT = 3  # lần thứ 3 gọi cùng tool với cùng tham số: cảnh báo
STOP_AT_REPEAT = 4  # lần thứ 4: dừng nhiệm vụ
MAX_ATTEMPTS = 3  # tổng số lần thử cho lỗi tạm thời, gồm cả lần đầu


@dataclass(frozen=True)
class Decision:
    action: Literal["ok", "warn", "stop"]
    reason: str | None = None


def fingerprint(name: str, args: dict) -> str:
    """Chuỗi đại diện cho một lời gọi, để nhận ra hai lời gọi giống nhau.

    - Hai lời gọi cùng tên và cùng tham số phải cho cùng một chuỗi, dù thứ tự khóa trong `args` khác nhau.
    - Bỏ qua `actor_id` và `task_id`, vì hai trường này do lớp bảo vệ điền, không phải do LLM chọn.
    - Khác tên hoặc khác bất kỳ tham số nào khác thì phải cho chuỗi khác.
    Gợi ý: `json.dumps(..., sort_keys=True)`.
    """
    kept = {}
    for key, value in args.items():
        if key not in ("actor_id", "task_id"):
            kept[key] = value
    return json.dumps([name, kept], sort_keys=True, ensure_ascii=False)



def check_limits(step_count: int, fingerprints: list[str], name: str, args: dict, max_steps: int = MAX_STEPS) -> Decision:
    """Quyết định có cho chạy lời gọi này không, TRƯỚC khi chạy.

    - `step_count` là số lời gọi đã chạy trong nhiệm vụ. Nếu `step_count >= max_steps`: "stop".
    - `fingerprints` là dấu của các lời gọi đã chạy. Đếm số lần lời gọi này đã xuất hiện:
      đây sẽ là lần thứ (số lần đã có + 1).
      - Lần thứ STOP_AT_REPEAT trở đi: "stop".
      - Lần thứ WARN_AT_REPEAT: "warn" (vẫn chạy, nhưng agent được nhắc).
      - Còn lại: "ok".
    - Với "warn" và "stop", `reason` là một câu tiếng Việt giải thích cho agent và người xem trace.
    """
    if step_count >= max_steps:
        return Decision("stop", f"Đã chạy {step_count} bước, chạm giới hạn {max_steps} bước")
    attempt = fingerprints.count(fingerprint(name, args)) + 1
    if attempt >= STOP_AT_REPEAT:
        return Decision("stop", f"Gọi {name} với cùng tham số lần thứ {attempt}, có thể agent đang bị kẹt")
    if attempt == WARN_AT_REPEAT:
        return Decision("warn", f"Đây là lần thứ {attempt} gọi {name} với cùng tham số, hãy thử cách khác")
    return Decision("ok")



def override_identity(args: dict, accepted: set[str], actor_id: int, task_id: str) -> dict:
    """Trả về bản sao của `args` với danh tính thật, bất kể LLM điền gì.

    - Không sửa `args` gốc.
    - Luôn bỏ `actor_id` và `task_id` mà LLM tự điền.
    - Nếu tool nhận `actor_id` (có trong `accepted`), đặt `actor_id` = giá trị thật. Tương tự với `task_id`.
    - Tool không nhận trường nào thì không thêm trường đó, vì MCP sẽ báo lỗi tham số lạ.
    """
    result = {}
    for key, value in args.items():
        if key not in ("actor_id", "task_id"):
            result[key] = value
    if "actor_id" in accepted:
        result["actor_id"] = actor_id
    if "task_id" in accepted:
        result["task_id"] = task_id
    return result



def parse_result(message: ToolMessage) -> dict:
    """Đổi kết quả của MCP thành dạng hợp đồng tool chung (mục 4.2).

    `message.content` là danh sách khối dạng {"type": "text", "text": "..."}; nối các phần text lại.
    - Nếu `message.status == "error"`: thư viện MCP đã chặn trước khi vào tool, thường do sai tên hay sai kiểu tham số.
      Trả về {"ok": False, "error_type": "validation", "message": <text, tối đa 500 ký tự>, "hint": <gợi ý kiểm tra tham số>}.
    - Nếu text là JSON có khóa "ok": trả về đúng dict đó.
    - Còn lại (không phải JSON, hay JSON thiếu "ok"): trả về lỗi "validation" như trên.
    """
    text = "".join(block["text"] for block in message.content)
    invalid = {
        "ok": False,
        "error_type": "validation",
        "message": text[:500],
        "hint": "Kiểm tra lại tên tool và kiểu tham số",
    }
    if message.status == "error":
        return invalid
    try:
        data = json.loads(text)
    except ValueError:
        return invalid
    if isinstance(data, dict) and "ok" in data:
        return data
    return invalid



async def call_with_retry(
    invoke: Callable[[dict], Awaitable[ToolMessage]],
    args: dict,
    *,
    max_attempts: int = MAX_ATTEMPTS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict:
    """Gọi tool và chỉ thử lại lỗi tạm thời. Trả về dict theo hợp đồng chung.

    - Gọi `await invoke(args)`, đưa kết quả qua `parse_result`.
    - Nếu kết quả là lỗi `error_type == "transient"`, hoặc `invoke` ném exception (mất kết nối với server):
      chờ rồi thử lại, tối đa `max_attempts` lần tổng cộng. Thời gian chờ tăng dần: 0.5, 1, 2... giây,
      và luôn chờ bằng `await sleep(...)` để test chạy nhanh được.
    - Lỗi khác (validation, policy, permission, not_found) và kết quả thành công: trả về ngay, không thử lại.
    - Hết số lần mà vẫn lỗi: trả về lỗi "transient" cuối cùng. Nếu lần cuối là exception thì trả về
      {"ok": False, "error_type": "transient", "message": ..., "hint": "Hãy thử lại sau"}. Không để exception lọt ra ngoài.
    """
    result = None
    for attempt in range(max_attempts):
        if attempt > 0:
            await sleep(0.5 * 2 ** (attempt - 1))
        try:
            message = await invoke(args)
        except Exception as exc:
            result = {
                "ok": False,
                "error_type": "transient",
                "message": str(exc)[:500],
                "hint": "Hãy thử lại sau",
            }
            continue
        result = parse_result(message)
        if result.get("error_type") != "transient":
            return result
    return result



def pending_updates(name: str, result: dict) -> tuple[list[int], list[int]]:
    """Việc đang chờ người mà lời gọi này tạo ra: (id yêu cầu chờ duyệt, id câu hỏi chờ trả lời).

    - `submit_for_approval` thành công: ([result["data"]["request_id"]], []).
    - `ask_requester` thành công: ([], [result["data"]["clarification_id"]]).
    - Mọi trường hợp khác, kể cả hai tool trên bị lỗi: ([], []).
    """
    if not result["ok"]:
        return [], []
    if name == "submit_for_approval": 
        return [result["data"]["request_id"]], []
    if name == "ask_requester":
        return [], [result["data"]["clarification_id"]]
    return [], []

