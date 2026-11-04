"""Quy tắc R4 và R5: ai phải duyệt một yêu cầu, và yêu cầu đã được duyệt đủ chưa."""

import sqlite3

from rules.errors import PolicyViolation
from rules.policy import required_levels


def _director_id(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT id FROM employees WHERE role = 'director' ORDER BY id LIMIT 1").fetchone()[0]


def _finance_approver(conn: sqlite3.Connection, requester_id: int) -> int:
    row = conn.execute(
        "SELECT id FROM employees WHERE role = 'finance' AND id != ? ORDER BY id LIMIT 1",
        (requester_id,),
    ).fetchone()
    if row is None:
        raise PolicyViolation("Không có nhân viên Finance nào khác người tạo yêu cầu để duyệt")
    return row[0]


def pick_approvers(
    conn: sqlite3.Connection, department_id: int, requester_id: int, amount: int
) -> list[tuple[str, int]]:
    """Trả về các cặp (cấp duyệt, người duyệt) theo thứ tự duyệt. Do code chọn, agent không được chọn."""
    levels = required_levels(amount)
    head_id = conn.execute("SELECT head_id FROM departments WHERE id = ?", (department_id,)).fetchone()[0]
    director_id = _director_id(conn)
    approvers = []
    if head_id != requester_id:
        approvers.append(("head", head_id))
    if "finance" in levels:
        approvers.append(("finance", _finance_approver(conn, requester_id)))
    # Trưởng phòng tự tạo yêu cầu thì giám đốc duyệt thay, kể cả với khoản dưới ngưỡng của giám đốc.
    if "director" in levels or head_id == requester_id:
        if director_id == requester_id:
            raise PolicyViolation("Giám đốc tạo yêu cầu nên không còn ai đủ thẩm quyền duyệt cấp giám đốc")
        approvers.append(("director", director_id))
    return approvers


def fully_approved(conn: sqlite3.Connection, request_id: int) -> bool:
    """Chỉ xét lần gửi duyệt gần nhất, tức các dòng sau lần từ chối cuối cùng."""
    rows = conn.execute(
        "SELECT decision FROM approvals WHERE request_id = ? ORDER BY id",
        (request_id,),
    ).fetchall()
    current = []
    for row in rows:
        current = [] if row["decision"] == "rejected" else [*current, row]
    return bool(current) and all(row["decision"] == "approved" for row in current)
