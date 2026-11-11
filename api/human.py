"""Việc của con người: duyệt, từ chối và trả lời câu hỏi. Agent không gọi được các hàm này.

Backend kiểm tra quyền rồi mới ghi. Câu trả lời của người yêu cầu đi qua LLM cách ly như mọi nội dung không tin cậy.
"""

import json
import sqlite3

from mcp_servers.common import dumps, save_request, write_transaction
from quarantine.extract import extract_email, load_catalog
from quarantine.model import ExtractionModel
from rules.errors import InvalidInput, NotFound, PermissionDenied, PolicyViolation
from rules.permissions import can_decide
from rules.policy import load_request
from simulator.clock import SimClock, quarter_of

DECISIONS = ("approved", "rejected")
MAX_TEXT_LENGTH = 1000


def _record(conn: sqlite3.Connection, request_id: int, actor_id: int, action: str, details: dict) -> None:
    conn.execute(
        "INSERT INTO audit_logs (request_id, actor, action, input, ts) VALUES (?, ?, ?, ?, ?)",
        (request_id, f"employee:{actor_id}", action, dumps(details), SimClock(conn).now_iso()),
    )


def decide_approval(
    conn: sqlite3.Connection, request_id: int, approver_id: int, decision: str, reason: str | None = None
) -> dict:
    """Ghi quyết định cho cấp duyệt đang đến lượt. Các cấp duyệt lần lượt theo thứ tự đã tạo."""
    if decision not in DECISIONS:
        raise InvalidInput(f"Quyết định {decision!r} không hợp lệ", "Dùng approved hoặc rejected")
    reason = (reason or "").strip() or None
    if decision == "rejected" and reason is None:
        raise InvalidInput("Từ chối thì phải nêu lý do", "Lý do giúp agent biết cần sửa gì")
    if reason is not None and len(reason) > MAX_TEXT_LENGTH:
        raise InvalidInput(f"Lý do dài quá {MAX_TEXT_LENGTH} ký tự")

    with write_transaction(conn):
        request = load_request(conn, request_id)
        if request["status"] != "PENDING_APPROVAL":
            raise PolicyViolation(f"Yêu cầu #{request_id} không chờ duyệt, đang ở trạng thái {request['status']}")
        turn = conn.execute(
            "SELECT id, level, approver_id FROM approvals WHERE request_id = ? AND decision IS NULL ORDER BY id LIMIT 1",
            (request_id,),
        ).fetchone()
        if turn is None:
            raise PolicyViolation(f"Yêu cầu #{request_id} không còn cấp duyệt nào đang chờ")
        if not can_decide(approver_id, turn["approver_id"], request["requester_id"]):
            raise PermissionDenied(
                f"Nhân viên #{approver_id} không được duyệt lượt này",
                f"Lượt hiện tại là cấp {turn['level']}, do nhân viên #{turn['approver_id']} duyệt",
            )
        now = SimClock(conn).now_iso()
        conn.execute(
            "UPDATE approvals SET decision = ?, reason = ?, decided_at = ? WHERE id = ?",
            (decision, reason, now, turn["id"]),
        )
        remaining = conn.execute(
            "SELECT COUNT(*) FROM approvals WHERE request_id = ? AND decision IS NULL", (request_id,)
        ).fetchone()[0]
        if decision == "rejected":
            # Trả lại khoản đã giữ chỗ lúc gửi duyệt.
            conn.execute(
                "UPDATE budgets SET reserved = reserved - ? WHERE department_id = ? AND quarter = ?",
                (request["total_amount"], request["department_id"], quarter_of(SimClock(conn).now())),
            )
            status = "REJECTED"
        else:
            status = "APPROVED" if remaining == 0 else "PENDING_APPROVAL"
        version = save_request(conn, request_id, status=status) if status != request["status"] else request["version"]
        result = {"request_id": request_id, "level": turn["level"], "decision": decision, "status": status, "version": version}
        _record(conn, request_id, approver_id, "approval_decision", result | {"reason": reason})
    return result


def answer_question(
    conn: sqlite3.Connection, model: ExtractionModel, clarification_id: int, responder_id: int, text: str
) -> dict:
    """Ghi câu trả lời của người yêu cầu, sau khi đã qua LLM cách ly. Nội dung gốc không được lưu cho agent đọc."""
    text = text.strip()
    if not text or len(text) > MAX_TEXT_LENGTH:
        raise InvalidInput(f"Câu trả lời phải có nội dung và không quá {MAX_TEXT_LENGTH} ký tự")
    question = conn.execute(
        "SELECT c.*, r.requester_id, e.email FROM clarifications c JOIN purchase_requests r ON r.id = c.request_id"
        " JOIN employees e ON e.id = r.requester_id WHERE c.id = ?",
        (clarification_id,),
    ).fetchone()
    if question is None:
        raise NotFound(f"Không có câu hỏi #{clarification_id}")
    if responder_id != question["requester_id"]:
        raise PermissionDenied("Chỉ người tạo yêu cầu mới trả lời được câu hỏi này")
    if question["status"] != "pending":
        raise PolicyViolation(f"Câu hỏi #{clarification_id} không còn chờ trả lời")

    # Gọi model khi chưa mở transaction ghi, vì lời gọi có thể mất vài giây.
    extracted = extract_email(
        model, load_catalog(conn), question["email"], SimClock(conn).now_iso(), f"Re: {question['question']}", text
    )
    with write_transaction(conn):
        conn.execute(
            "UPDATE clarifications SET reply_json = ?, status = 'answered', replied_at = ? WHERE id = ? AND status = 'pending'",
            (dumps(extracted), SimClock(conn).now_iso(), clarification_id),
        )
        result = {"clarification_id": clarification_id, "request_id": question["request_id"], "reply": extracted}
        _record(conn, question["request_id"], responder_id, "clarification_reply", result)
    return result


def unresolved(conn: sqlite3.Connection, approvals: list[int], replies: list[int]) -> list[str]:
    """Các việc còn đang chờ. Chỉ tiếp tục nhiệm vụ khi danh sách này rỗng."""
    waiting = []
    for request_id in approvals:
        if load_request(conn, request_id)["status"] == "PENDING_APPROVAL":
            waiting.append(f"yêu cầu #{request_id} còn chờ duyệt")
    for clarification_id in replies:
        row = conn.execute(
            "SELECT c.status, r.status AS request_status FROM clarifications c"
            " JOIN purchase_requests r ON r.id = c.request_id WHERE c.id = ?",
            (clarification_id,),
        ).fetchone()
        if row is not None and row["status"] == "pending" and row["request_status"] != "CANCELLED":
            waiting.append(f"câu hỏi #{clarification_id} còn chờ trả lời")
    return waiting


def describe_pending(conn: sqlite3.Connection, request_id: int) -> dict:
    """Thông tin cho người duyệt: lượt duyệt hiện tại và hồ sơ đề xuất."""
    request = load_request(conn, request_id)
    turn = conn.execute(
        "SELECT level, approver_id FROM approvals WHERE request_id = ? AND decision IS NULL ORDER BY id LIMIT 1",
        (request_id,),
    ).fetchone()
    return {
        "request_id": request_id,
        "status": request["status"],
        "total_amount": request["total_amount"],
        "proposal": json.loads(request["proposal_json"]) if request["proposal_json"] else None,
        "turn": dict(turn) if turn else None,
    }
