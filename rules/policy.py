"""Quy tắc mua hàng R1, R2, R3, R6, R7, R8. R4 và R5 nằm trong `rules/approvers.py`.

R1, R2, R3, R7 là vi phạm, chặn việc gửi duyệt. R6, R8 chỉ là dấu hiệu nghi ngờ để người duyệt xem xét.
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from rules.errors import InvalidInput, NotFound
from simulator.clock import SimClock, quarter_of

TIER_ONE_LIMIT = 20_000_000
TIER_TWO_LIMIT = 100_000_000
LEVELS = ("head", "finance", "director")
RELATED_WINDOW = timedelta(days=14)
# Các trạng thái này đã giữ chỗ ngân sách bằng chính total_amount của yêu cầu.
HOLDING_STATUSES = ("PENDING_APPROVAL", "APPROVED", "PO_DRAFTED")


@dataclass(frozen=True)
class Finding:
    rule: str
    message: str


@dataclass(frozen=True)
class PolicyResult:
    total_amount: int
    passed: list[str]
    violations: list[Finding]
    flags: list[Finding]


def total_with_vat(unit_price: int, vat_rate: int, quantity: int) -> int:
    return unit_price * quantity * (100 + vat_rate) // 100


def _tier(amount: int) -> int:
    if amount < TIER_ONE_LIMIT:
        return 1
    return 2 if amount <= TIER_TWO_LIMIT else 3


def required_quotes(amount: int) -> int:
    return _tier(amount)


def required_levels(amount: int) -> tuple[str, ...]:
    return LEVELS[: _tier(amount)]


def available_budget(conn: sqlite3.Connection, department_id: int, quarter: str) -> int | None:
    row = conn.execute(
        "SELECT total - spent - reserved FROM budgets WHERE department_id = ? AND quarter = ?",
        (department_id, quarter),
    ).fetchone()
    return None if row is None else row[0]


def _load_request(conn: sqlite3.Connection, request_id: int) -> sqlite3.Row:
    request = conn.execute("SELECT * FROM purchase_requests WHERE id = ?", (request_id,)).fetchone()
    if request is None:
        raise NotFound(f"Không có yêu cầu #{request_id}")
    return request


def _load_quotes(conn: sqlite3.Connection, quotation_ids: list[int], item: str) -> dict[int, sqlite3.Row]:
    quotes = {}
    for quotation_id in quotation_ids:
        quote = conn.execute(
            "SELECT q.*, s.approved, s.name AS supplier FROM quotations q"
            " JOIN suppliers s ON s.id = q.supplier_id WHERE q.id = ?",
            (quotation_id,),
        ).fetchone()
        if quote is None:
            raise NotFound(f"Không có báo giá #{quotation_id}", "Hãy dùng get_quotations để xem báo giá có sẵn")
        if quote["item"] != item:
            raise InvalidInput(
                f"Báo giá #{quotation_id} là của {quote['item']}, không phải {item}",
                "Hãy dùng get_quotations để lấy đúng báo giá của mặt hàng này",
            )
        quotes[quotation_id] = quote
    return quotes


def _check_budget(conn: sqlite3.Connection, request: sqlite3.Row, total: int, now: datetime) -> Finding | None:
    quarter = quarter_of(now)
    available = available_budget(conn, request["department_id"], quarter)
    if available is None:
        return Finding("R1", f"Phòng ban chưa có ngân sách quý {quarter}")
    if request["status"] in HOLDING_STATUSES:
        available += request["total_amount"]
    if total > available:
        return Finding("R1", f"Tổng {total:,} VND vượt ngân sách còn lại {available:,} VND")
    return None


def _related_requests(conn: sqlite3.Connection, request: sqlite3.Row) -> list[tuple[sqlite3.Row, dict]]:
    """Các yêu cầu khác của cùng phòng ban, tạo cách yêu cầu này không quá 14 ngày, trừ yêu cầu đã hủy."""
    created = datetime.fromisoformat(request["created_at"])
    rows = conn.execute(
        "SELECT * FROM purchase_requests WHERE department_id = ? AND id != ? AND status != 'CANCELLED' ORDER BY id",
        (request["department_id"], request["id"]),
    ).fetchall()
    return [
        (row, json.loads(row["extracted_json"] or "{}"))
        for row in rows
        if abs(datetime.fromisoformat(row["created_at"]) - created) <= RELATED_WINDOW
    ]


def _check_duplicate(related: list[tuple[sqlite3.Row, dict]], item: str) -> Finding | None:
    matches = [row["id"] for row, extracted in related if extracted.get("item") == item]
    if not matches:
        return None
    return Finding("R6", f"Nghi trùng với yêu cầu {_ids(matches)}: cùng phòng ban và mặt hàng trong 14 ngày")


def _check_split(related: list[tuple[sqlite3.Row, dict]], category: str | None, total: int) -> Finding | None:
    if category is None:
        return None
    matches = [
        row for row, extracted in related if extracted.get("category") == category and row["total_amount"] is not None
    ]
    combined = total + sum(row["total_amount"] for row in matches)
    if _tier(combined) <= _tier(total):
        return None
    return Finding(
        "R8",
        f"Nghi chia nhỏ đơn với yêu cầu {_ids([row['id'] for row in matches])}:"
        f" cộng lại {combined:,} VND, vượt ngưỡng của từng yêu cầu",
    )


def _ids(request_ids: list[int]) -> str:
    return ", ".join(f"#{request_id}" for request_id in request_ids)


def check_policy(
    conn: sqlite3.Connection, request_id: int, selected_quotation_id: int, quotation_ids: list[int]
) -> PolicyResult:
    """Kiểm tra một hồ sơ đề xuất, không ghi gì vào database."""
    request = _load_request(conn, request_id)
    extracted = json.loads(request["extracted_json"] or "{}")
    item, quantity = extracted.get("item"), extracted.get("quantity")
    if item is None or quantity is None:
        raise InvalidInput(
            f"Yêu cầu #{request_id} còn thiếu mặt hàng hoặc số lượng",
            "Hãy hỏi người yêu cầu bằng ask_requester",
        )
    if selected_quotation_id not in quotation_ids:
        raise InvalidInput(
            f"Báo giá được chọn #{selected_quotation_id} không nằm trong danh sách báo giá của hồ sơ",
            "Hãy thêm báo giá được chọn vào quotation_ids",
        )

    quotes = _load_quotes(conn, quotation_ids, item)
    selected = quotes[selected_quotation_id]
    total = total_with_vat(selected["unit_price"], selected["vat_rate"], quantity)
    now = SimClock(conn).now()
    today = now.date().isoformat()

    unapproved = [id_ for id_, quote in quotes.items() if not quote["approved"]]
    expired = [id_ for id_, quote in quotes.items() if quote["valid_until"] < today]
    # Nhiều báo giá của cùng một nhà cung cấp chỉ tính là một, vì không so sánh được với nhau.
    usable_suppliers = {
        quote["supplier_id"] for id_, quote in quotes.items() if id_ not in unapproved and id_ not in expired
    }

    findings = {
        "R1": _check_budget(conn, request, total, now),
        "R2": None,
        "R3": None,
        "R7": None,
    }
    needed = required_quotes(total)
    if len(usable_suppliers) < needed:
        findings["R2"] = Finding(
            "R2", f"Tổng {total:,} VND cần báo giá của {needed} nhà cung cấp, hiện chỉ dùng được {len(usable_suppliers)}"
        )
    if unapproved:
        findings["R3"] = Finding("R3", f"Báo giá {_ids(unapproved)} đến từ nhà cung cấp chưa được duyệt")
    if expired:
        findings["R7"] = Finding("R7", f"Báo giá {_ids(expired)} đã hết hạn")

    related = _related_requests(conn, request)
    flags = {
        "R6": _check_duplicate(related, item),
        "R8": _check_split(related, extracted.get("category"), total),
    }
    results = findings | flags
    return PolicyResult(
        total_amount=total,
        passed=[rule for rule, finding in sorted(results.items()) if finding is None],
        violations=[finding for _, finding in sorted(findings.items()) if finding is not None],
        flags=[finding for _, finding in sorted(flags.items()) if finding is not None],
    )
