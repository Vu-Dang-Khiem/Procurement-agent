"""Quyền xem và quyền quyết định phê duyệt."""

import sqlite3
from dataclasses import dataclass

from rules.errors import NotFound


@dataclass(frozen=True)
class Actor:
    id: int
    role: str
    department_id: int | None
    company_wide: bool


def load_actor(conn: sqlite3.Connection, actor_id: int) -> Actor:
    row = conn.execute(
        "SELECT e.id, e.role, e.department_id, d.name AS department FROM employees e"
        " LEFT JOIN departments d ON d.id = e.department_id WHERE e.id = ?",
        (actor_id,),
    ).fetchone()
    if row is None:
        raise NotFound(f"Không có nhân viên #{actor_id}")
    # Mọi người trong phòng Finance, gồm cả trưởng phòng, đều xem được toàn công ty.
    company_wide = row["role"] == "director" or row["department"] == "Finance"
    return Actor(row["id"], row["role"], row["department_id"], company_wide)


def can_view_request(actor: Actor, requester_id: int, department_id: int) -> bool:
    if actor.company_wide or actor.id == requester_id:
        return True
    return actor.role == "head" and actor.department_id == department_id


def can_view_budget(actor: Actor, department_id: int) -> bool:
    return actor.company_wide or actor.department_id == department_id


def can_decide(actor_id: int, approver_id: int, requester_id: int) -> bool:
    """R5: chỉ người được giao mới được quyết định, và người tạo yêu cầu không được tự duyệt."""
    return actor_id == approver_id and actor_id != requester_id
