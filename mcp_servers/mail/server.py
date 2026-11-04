"""mail-mcp, chạy bằng `python -m mcp_servers.mail.server`.

Hộp thư gửi đi của công ty giả lập chính là bảng `clarifications`. Tool không có tham số người nhận,
người nhận luôn là người tạo yêu cầu, nên agent không thể gửi thư ra ngoài công ty.
"""

from mcp.server.fastmcp import FastMCP

from mcp_servers.common import audit, load_for_write, open_db, register_tool, save_request, write_transaction
from rules.errors import InvalidInput, PolicyViolation
from rules.status import ensure_transition
from simulator.clock import SimClock

MAX_ASKS_PER_REQUEST = 2
MAX_QUESTION_LENGTH = 500

mcp = FastMCP("mail", log_level="WARNING")
tool = register_tool(mcp)


@tool
def ask_requester(actor_id: int, request_id: int, version: int, question: str, task_id: str | None = None) -> dict:
    """Gửi một câu hỏi cho người tạo yêu cầu khi còn thiếu thông tin, và đưa yêu cầu sang `NEEDS_INFO`.

    Chỉ hỏi được tối đa 2 lần cho mỗi yêu cầu, nên hãy gộp mọi điều cần hỏi vào một câu và viết rõ ràng.
    Không dùng để báo tin hay hỏi người khác. Câu trả lời không có ngay: hãy nộp báo cáo `waiting_reply` để chờ.
    """
    question = question.strip()
    if not question:
        raise InvalidInput("Câu hỏi đang để trống")
    if len(question) > MAX_QUESTION_LENGTH:
        raise InvalidInput(f"Câu hỏi dài quá {MAX_QUESTION_LENGTH} ký tự", "Hãy viết ngắn lại")

    with open_db() as conn, write_transaction(conn):
        request = load_for_write(conn, actor_id, request_id, version)
        if request["status"] != "NEEDS_INFO":
            ensure_transition(request["status"], "NEEDS_INFO")
        asked = conn.execute(
            "SELECT COUNT(*) FROM clarifications WHERE request_id = ? AND status != 'cancelled'",
            (request_id,),
        ).fetchone()[0]
        if asked >= MAX_ASKS_PER_REQUEST:
            raise PolicyViolation(
                f"Đã hỏi về yêu cầu #{request_id} đủ {MAX_ASKS_PER_REQUEST} lần",
                "Hãy dùng thông tin đang có, hoặc báo cáo rằng nhiệm vụ đang bị chặn",
            )
        cursor = conn.execute(
            "INSERT INTO clarifications (request_id, task_id, question, asked_at) VALUES (?, ?, ?, ?)",
            (request_id, task_id, question, SimClock(conn).now_iso()),
        )
        new_version = save_request(conn, request_id, status="NEEDS_INFO")
        output = {
            "clarification_id": cursor.lastrowid,
            "request_id": request_id,
            "recipient_id": request["requester_id"],
            "status": "NEEDS_INFO",
            "version": new_version,
            "asks_left": MAX_ASKS_PER_REQUEST - asked - 1,
        }
        audit(conn, task_id, request_id, actor_id, "ask_requester", {"question": question}, output)
    return output


if __name__ == "__main__":
    mcp.run(transport="stdio")
