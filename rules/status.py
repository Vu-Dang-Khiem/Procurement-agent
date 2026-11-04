"""Các bước chuyển trạng thái hợp lệ của yêu cầu mua hàng.

Đây là ràng buộc về dữ liệu, không phải workflow dẫn dắt agent.
"""

from rules.errors import PolicyViolation

TERMINAL_STATUSES = frozenset({"COMPLETED", "ESCALATED", "CANCELLED"})

_FORWARD = {
    "NEW": {"NEEDS_INFO", "IN_REVIEW"},
    "NEEDS_INFO": {"IN_REVIEW"},
    "IN_REVIEW": {"NEEDS_INFO", "PENDING_APPROVAL"},
    "PENDING_APPROVAL": {"APPROVED", "REJECTED"},
    "APPROVED": {"PO_DRAFTED"},
    "REJECTED": {"IN_REVIEW"},
    "PO_DRAFTED": {"COMPLETED"},
}


def allowed_next(status: str) -> frozenset[str]:
    if status in TERMINAL_STATUSES:
        return frozenset()
    return frozenset(_FORWARD[status] | {"ESCALATED", "CANCELLED"})


def ensure_transition(current: str, target: str) -> None:
    allowed = allowed_next(current)
    if target not in allowed:
        hint = f"Từ {current} chỉ chuyển được sang: {', '.join(sorted(allowed))}" if allowed else None
        raise PolicyViolation(f"Không thể chuyển từ {current} sang {target}", hint)
