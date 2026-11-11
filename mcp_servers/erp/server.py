"""erp-mcp, chạy bằng `python -m mcp_servers.erp.server`.

`actor_id` và `task_id` do lớp bảo vệ của agent ghi đè, nên LLM không giả danh được.
Mỗi lần ghi đều tăng `version`, nên gọi lại với version cũ sẽ bị từ chối thay vì ghi hai lần.
"""

import json
import re
import sqlite3
from dataclasses import asdict
from datetime import date

from mcp.server.fastmcp import FastMCP

from mcp_servers.common import (
    Partial,
    audit,
    dumps,
    load_for_actor,
    load_for_write,
    open_db,
    register_tool,
    save_request,
    write_transaction,
)
from rules import policy
from rules.approvers import fully_approved, pick_approvers
from rules.errors import InvalidInput, NotFound, PermissionDenied, PolicyViolation
from rules.permissions import can_view_budget, load_actor, request_scope
from rules.status import ensure_transition
from simulator.clock import SimClock, quarter_of

CATEGORIES = ("furniture", "it", "marketing", "office", "software")
QUARTER_PATTERN = re.compile(r"\d{4}-Q[1-4]")
MAX_TEXT_LENGTH = 1000
# Yêu cầu đã gửi duyệt thì không được sửa nội dung nữa.
EDITABLE_STATUSES = ("NEW", "NEEDS_INFO", "IN_REVIEW", "REJECTED")
PROPOSAL_STATUSES = ("NEW", "IN_REVIEW", "REJECTED")
REQUEST_STATUSES = (
    "APPROVED", "CANCELLED", "COMPLETED", "ESCALATED", "IN_REVIEW",
    "NEEDS_INFO", "NEW", "PENDING_APPROVAL", "PO_DRAFTED", "REJECTED",
)
DEFAULT_LIST_LIMIT = 20
MAX_LIST_LIMIT = 50

mcp = FastMCP("erp", log_level="WARNING")
tool = register_tool(mcp)


def _findings(findings: list[policy.Finding]) -> list[dict]:
    return [asdict(finding) for finding in findings]


def _policy_output(result: policy.PolicyResult) -> dict:
    return {
        "total_amount": result.total_amount,
        "required_quotes": policy.required_quotes(result.total_amount),
        "required_levels": list(policy.required_levels(result.total_amount)),
        "passed": result.passed,
        "violations": _findings(result.violations),
        "flags": _findings(result.flags),
    }


@tool
def get_request(actor_id: int, request_id: int) -> dict:
    """Xem một yêu cầu mua hàng: nội dung, trạng thái, version, hồ sơ đề xuất, các phê duyệt và câu hỏi đã gửi.

    Gọi trước khi xử lý yêu cầu, và khi tool ghi báo version đã đổi. Lấy `version` ở đây để truyền cho tool ghi.
    `total_amount` đã gồm VAT, `null` nghĩa là chưa có hồ sơ đề xuất.
    """
    with open_db() as conn:
        request = load_for_actor(conn, actor_id, request_id)
        approvals = conn.execute(
            "SELECT level, approver_id, decision, reason, decided_at FROM approvals WHERE request_id = ? ORDER BY id",
            (request_id,),
        ).fetchall()
        clarifications = conn.execute(
            "SELECT question, status, reply_json FROM clarifications WHERE request_id = ? ORDER BY id",
            (request_id,),
        ).fetchall()
    extracted = json.loads(request["extracted_json"] or "{}")
    return {
        "id": request["id"],
        "status": request["status"],
        "version": request["version"],
        "requester_id": request["requester_id"],
        "department_id": request["department_id"],
        "item": extracted.get("item"),
        "category": extracted.get("category"),
        "quantity": extracted.get("quantity"),
        "needed_by": extracted.get("needed_by"),
        "total_amount": request["total_amount"],
        "created_at": request["created_at"],
        "updated_at": request["updated_at"],
        "proposal": json.loads(request["proposal_json"]) if request["proposal_json"] else None,
        "approvals": [dict(row) for row in approvals],
        "clarifications": [
            {
                "question": row["question"],
                "status": row["status"],
                "reply": json.loads(row["reply_json"]) if row["reply_json"] else None,
            }
            for row in clarifications
        ],
    }


@tool
def list_requests(
    actor_id: int,
    status: str | None = None,
    department_id: int | None = None,
    item: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
) -> dict:
    """Tìm các yêu cầu mua hàng mà bạn được xem, mới nhất trước. Mọi bộ lọc đều không bắt buộc.

    Dùng khi mục tiêu không nêu số yêu cầu, hoặc cần rà soát nhiều yêu cầu. Muốn xem chi tiết một yêu cầu thì dùng `get_request`.
    `item` khớp một phần tên mặt hàng, không phân biệt hoa thường. `created_from`, `created_to` có dạng 2026-11-01, tính cả hai đầu.
    `truncated: true` nghĩa là còn nhiều kết quả hơn `limit`: hãy thêm bộ lọc. `total` là tổng số yêu cầu khớp.
    """
    if status is not None and status not in REQUEST_STATUSES:
        raise InvalidInput(f"Trạng thái {status!r} không hợp lệ", f"Các trạng thái: {', '.join(REQUEST_STATUSES)}")
    if not 1 <= limit <= MAX_LIST_LIMIT:
        raise InvalidInput(f"limit phải từ 1 đến {MAX_LIST_LIMIT}")
    bounds = {}
    for name, value in (("created_from", created_from), ("created_to", created_to)):
        if value is not None:
            try:
                bounds[name] = date.fromisoformat(value).isoformat()
            except ValueError as cause:
                raise InvalidInput(f"Ngày {value!r} không hợp lệ", "Hãy dùng dạng 2026-11-01") from cause

    with open_db() as conn:
        scope, params = request_scope(load_actor(conn, actor_id))
        conditions, values = [scope], list(params)
        if status is not None:
            conditions.append("status = ?")
            values.append(status)
        if department_id is not None:
            conditions.append("department_id = ?")
            values.append(department_id)
        if "created_from" in bounds:
            conditions.append("substr(created_at, 1, 10) >= ?")
            values.append(bounds["created_from"])
        if "created_to" in bounds:
            conditions.append("substr(created_at, 1, 10) <= ?")
            values.append(bounds["created_to"])
        rows = conn.execute(
            f"SELECT * FROM purchase_requests WHERE {' AND '.join(conditions)} ORDER BY created_at DESC, id DESC",
            values,
        ).fetchall()

    needle = item.strip().casefold() if item else None
    found = []
    for row in rows:
        extracted = json.loads(row["extracted_json"] or "{}")
        if needle and needle not in (extracted.get("item") or "").casefold():
            continue
        found.append(
            {
                "id": row["id"],
                "status": row["status"],
                "department_id": row["department_id"],
                "requester_id": row["requester_id"],
                "item": extracted.get("item"),
                "quantity": extracted.get("quantity"),
                "total_amount": row["total_amount"],
                "created_at": row["created_at"],
            }
        )
    data = {"requests": found[:limit], "total": len(found)}
    return Partial(data) if len(found) > limit else data


@tool
def get_department_budget(actor_id: int, department_id: int, quarter: str | None = None) -> dict:
    """Xem ngân sách của một phòng ban trong một quý, bỏ trống `quarter` để lấy quý hiện tại, dạng 2026-Q4.

    Chỉ để xem. Không cần gọi để kiểm tra một hồ sơ, vì `check_policy` đã tự kiểm tra ngân sách.
    `available` = `total` - `spent` - `reserved`.
    """
    with open_db() as conn:
        if not can_view_budget(load_actor(conn, actor_id), department_id):
            raise PermissionDenied(f"Nhân viên #{actor_id} không có quyền xem ngân sách phòng ban #{department_id}")
        quarter = quarter or quarter_of(SimClock(conn).now())
        if not QUARTER_PATTERN.fullmatch(quarter):
            raise InvalidInput(f"Quý {quarter!r} không hợp lệ", "Hãy dùng dạng 2026-Q4")
        budget = conn.execute(
            "SELECT total, spent, reserved FROM budgets WHERE department_id = ? AND quarter = ?",
            (department_id, quarter),
        ).fetchone()
    if budget is None:
        raise NotFound(f"Phòng ban #{department_id} không có ngân sách quý {quarter}")
    return {
        "department_id": department_id,
        "quarter": quarter,
        "total": budget["total"],
        "spent": budget["spent"],
        "reserved": budget["reserved"],
        "available": budget["total"] - budget["spent"] - budget["reserved"],
    }


@tool
def get_quotations(item: str) -> dict:
    """Tìm các báo giá theo tên mặt hàng, không phân biệt hoa thường, khớp một phần tên là đủ.

    Gọi khi cần chọn báo giá cho hồ sơ đề xuất. Không đọc được nội dung file PDF, việc đó do `read_quotation_pdf` làm.
    `unit_price` chưa gồm VAT. `expired` và `supplier_approved` cho biết báo giá có dùng được theo R3, R7 không.
    """
    needle = item.strip().casefold()
    if not needle:
        raise InvalidInput("Thiếu tên mặt hàng")
    with open_db() as conn:
        rows = conn.execute(
            "SELECT q.*, s.name AS supplier, s.approved, s.rating FROM quotations q"
            " JOIN suppliers s ON s.id = q.supplier_id ORDER BY q.id"
        ).fetchall()
        today = SimClock(conn).now().date().isoformat()
    matches = [row for row in rows if needle in row["item"].casefold()]
    if not matches:
        items = ", ".join(sorted({row["item"] for row in rows}))
        raise NotFound(f"Không có báo giá nào cho {item!r}", f"Các mặt hàng có báo giá: {items}")
    return {
        "quotations": [
            {
                "id": row["id"],
                "supplier_id": row["supplier_id"],
                "supplier": row["supplier"],
                "supplier_approved": bool(row["approved"]),
                "supplier_rating": row["rating"],
                "item": row["item"],
                "unit_price": row["unit_price"],
                "vat_rate": row["vat_rate"],
                "delivery_days": row["delivery_days"],
                "warranty_months": row["warranty_months"],
                "valid_until": row["valid_until"],
                "expired": row["valid_until"] < today,
            }
            for row in matches
        ]
    }


@tool
def check_policy(actor_id: int, request_id: int, selected_quotation_id: int, quotation_ids: list[int]) -> dict:
    """Thử một phương án đề xuất với các quy tắc R1–R8, không ghi gì. Dùng để so sánh trước khi tạo hồ sơ.

    `quotation_ids` là mọi báo giá đưa vào hồ sơ, gồm cả báo giá được chọn.
    `violations` phải được xử lý thì mới gửi duyệt được. `flags` là dấu hiệu nghi ngờ, không chặn việc gửi duyệt.
    """
    with open_db() as conn:
        load_for_actor(conn, actor_id, request_id)
        result = policy.check_policy(conn, request_id, selected_quotation_id, quotation_ids)
    return _policy_output(result)


@tool
def update_request(
    actor_id: int,
    request_id: int,
    version: int,
    item: str | None = None,
    category: str | None = None,
    quantity: int | None = None,
    needed_by: str | None = None,
    status: str | None = None,
    task_id: str | None = None,
) -> dict:
    """Sửa nội dung yêu cầu khi đã rõ thông tin, hoặc chuyển yêu cầu sang `IN_REVIEW` bằng `status`.

    Chỉ sửa được khi chưa gửi duyệt, truyền trường nào thì sửa trường đó. `needed_by` có dạng 2026-11-30.
    Không dùng để duyệt, hủy hay gửi duyệt. Đổi `item` hoặc `quantity` sẽ xóa hồ sơ đề xuất cũ, phải tạo lại.
    """
    changes = {"item": item, "category": category, "quantity": quantity, "needed_by": needed_by}
    changes = {name: value for name, value in changes.items() if value is not None}
    if not changes and status is None:
        raise InvalidInput("Không có gì để sửa", "Hãy truyền ít nhất một trường cần sửa")
    if status not in (None, "IN_REVIEW"):
        raise PolicyViolation(f"Tool này không chuyển được sang {status}", "Chỉ có thể chuyển sang IN_REVIEW")
    if item is not None and not 0 < len(item.strip()) <= MAX_TEXT_LENGTH:
        raise InvalidInput("Tên mặt hàng không hợp lệ")
    if category is not None and category not in CATEGORIES:
        raise InvalidInput(f"Nhóm hàng {category!r} không hợp lệ", f"Các nhóm hàng: {', '.join(CATEGORIES)}")
    if quantity is not None and quantity <= 0:
        raise InvalidInput("Số lượng phải lớn hơn 0")
    if needed_by is not None:
        try:
            changes["needed_by"] = date.fromisoformat(needed_by).isoformat()
        except ValueError as cause:
            raise InvalidInput(f"Ngày {needed_by!r} không hợp lệ", "Hãy dùng dạng 2026-11-30") from cause

    with open_db() as conn, write_transaction(conn):
        request = load_for_write(conn, actor_id, request_id, version)
        if request["status"] not in EDITABLE_STATUSES:
            raise PolicyViolation(f"Không thể sửa yêu cầu đang ở trạng thái {request['status']}")
        fields: dict[str, object] = {}
        if changes:
            fields["extracted_json"] = dumps(json.loads(request["extracted_json"] or "{}") | changes)
        # Tổng tiền và hồ sơ cũ được tính theo mặt hàng và số lượng cũ.
        proposal_cleared = bool({"item", "quantity"} & changes.keys()) and request["proposal_json"] is not None
        if proposal_cleared:
            fields |= {"proposal_json": None, "total_amount": None}
        if status is not None:
            ensure_transition(request["status"], status)
            fields["status"] = status
        new_version = save_request(conn, request_id, **fields)
        output = {
            "request_id": request_id,
            "status": status or request["status"],
            "version": new_version,
            "proposal_cleared": proposal_cleared,
        }
        audit(conn, task_id, request_id, actor_id, "update_request", {"changes": changes, "status": status}, output)
    return output


@tool
def create_proposal(
    actor_id: int,
    request_id: int,
    version: int,
    selected_quotation_id: int,
    quotation_ids: list[int],
    rationale: str = "",
    task_id: str | None = None,
) -> dict:
    """Lưu hồ sơ đề xuất và đưa yêu cầu về `IN_REVIEW`. Gọi lại sẽ thay hồ sơ cũ.

    Hồ sơ vi phạm quy tắc vẫn được lưu kèm `violations` để người xem thấy rõ, nhưng chưa gửi duyệt được.
    `rationale` nói ngắn gọn vì sao chọn báo giá này. Gọi `submit_for_approval` ở bước sau bằng version mới.
    """
    if len(rationale) > MAX_TEXT_LENGTH:
        raise InvalidInput(f"Lý do dài quá {MAX_TEXT_LENGTH} ký tự", "Hãy viết ngắn lại")
    quotation_ids = list(dict.fromkeys(quotation_ids))

    with open_db() as conn, write_transaction(conn):
        request = load_for_write(conn, actor_id, request_id, version)
        if request["status"] not in PROPOSAL_STATUSES:
            raise PolicyViolation(f"Không thể tạo hồ sơ khi yêu cầu đang ở trạng thái {request['status']}")
        result = policy.check_policy(conn, request_id, selected_quotation_id, quotation_ids)
        proposal = {
            "selected_quotation_id": selected_quotation_id,
            "quotation_ids": quotation_ids,
            "rationale": rationale,
            "violations": _findings(result.violations),
            "flags": _findings(result.flags),
        }
        new_version = save_request(
            conn, request_id, status="IN_REVIEW", proposal_json=dumps(proposal), total_amount=result.total_amount
        )
        output = {"request_id": request_id, "status": "IN_REVIEW", "version": new_version} | _policy_output(result)
        audit(conn, task_id, request_id, actor_id, "create_proposal", proposal, output)
    return output


@tool
def submit_for_approval(actor_id: int, request_id: int, version: int, task_id: str | None = None) -> dict:
    """Gửi hồ sơ đề xuất đi duyệt: kiểm tra lại quy tắc, giữ chỗ ngân sách và tạo các dòng phê duyệt.

    Hệ thống tự chọn người duyệt theo hạn mức, agent không chọn được. Bị từ chối nếu hồ sơ còn vi phạm.
    Sau khi gửi, yêu cầu chờ người duyệt, không thể tự chuyển sang đã duyệt. `flags` sẽ được chuyển cho người duyệt.
    """
    with open_db() as conn, write_transaction(conn):
        request = load_for_write(conn, actor_id, request_id, version)
        ensure_transition(request["status"], "PENDING_APPROVAL")
        if request["proposal_json"] is None:
            raise InvalidInput(f"Yêu cầu #{request_id} chưa có hồ sơ đề xuất", "Hãy gọi create_proposal trước")
        proposal = json.loads(request["proposal_json"])
        # Kiểm tra lại vì ngân sách và hạn báo giá có thể đã đổi từ lúc tạo hồ sơ.
        result = policy.check_policy(conn, request_id, proposal["selected_quotation_id"], proposal["quotation_ids"])
        if result.violations:
            messages = "; ".join(f"{finding.rule}: {finding.message}" for finding in result.violations)
            raise PolicyViolation(
                f"Hồ sơ còn vi phạm: {messages}", "Hãy đổi báo giá bằng create_proposal hoặc báo cho người yêu cầu"
            )
        total = result.total_amount
        approvers = pick_approvers(conn, request["department_id"], request["requester_id"], total)
        quarter = quarter_of(SimClock(conn).now())
        conn.execute(
            "UPDATE budgets SET reserved = reserved + ? WHERE department_id = ? AND quarter = ?",
            (total, request["department_id"], quarter),
        )
        # Dòng chưa quyết định còn sót lại thuộc về lần gửi trước, khi yêu cầu đã bị từ chối ở cấp khác.
        conn.execute("DELETE FROM approvals WHERE request_id = ? AND decision IS NULL", (request_id,))
        conn.executemany(
            "INSERT INTO approvals (request_id, task_id, level, approver_id) VALUES (?, ?, ?, ?)",
            [(request_id, task_id, level, approver_id) for level, approver_id in approvers],
        )
        proposal["violations"], proposal["flags"] = [], _findings(result.flags)
        new_version = save_request(
            conn, request_id, status="PENDING_APPROVAL", proposal_json=dumps(proposal), total_amount=total
        )
        output = {
            "request_id": request_id,
            "status": "PENDING_APPROVAL",
            "version": new_version,
            "total_amount": total,
            "reserved_in_quarter": quarter,
            "approvers": [{"level": level, "approver_id": approver_id} for level, approver_id in approvers],
            "flags": proposal["flags"],
        }
        audit(conn, task_id, request_id, actor_id, "submit_for_approval", {"version": version}, output)
    return output


@tool
def create_po_draft(actor_id: int, request_id: int, version: int, task_id: str | None = None) -> dict:
    """Tạo bản nháp đơn hàng cho yêu cầu đã được duyệt đủ các cấp. Đây chỉ là bản nháp, chưa gửi cho nhà cung cấp.

    Nhà cung cấp và số tiền lấy từ hồ sơ đã duyệt, không truyền vào được. Bị từ chối nếu chưa duyệt đủ.
    Gọi lại với cùng `version` trả về đúng đơn đã tạo (`already_existed: true`), không tạo đơn thứ hai.
    """
    idempotency_key = f"req-{request_id}-v{version}"
    with open_db() as conn, write_transaction(conn):
        request = load_for_actor(conn, actor_id, request_id)
        existing = conn.execute(
            "SELECT o.id, o.supplier_id, s.name AS supplier, o.amount, o.status FROM purchase_orders o"
            " JOIN suppliers s ON s.id = o.supplier_id WHERE o.idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if existing is not None:
            return dict(existing) | {
                "request_id": request_id,
                "idempotency_key": idempotency_key,
                "already_existed": True,
            }

        request = load_for_write(conn, actor_id, request_id, version)
        ensure_transition(request["status"], "PO_DRAFTED")
        if not fully_approved(conn, request_id):
            raise PolicyViolation(f"Yêu cầu #{request_id} chưa được duyệt đủ các cấp")
        selected = json.loads(request["proposal_json"])["selected_quotation_id"]
        supplier = conn.execute(
            "SELECT s.id, s.name, s.approved FROM quotations q JOIN suppliers s ON s.id = q.supplier_id WHERE q.id = ?",
            (selected,),
        ).fetchone()
        # Nhà cung cấp có thể bị gỡ khỏi danh sách được duyệt sau khi người duyệt đã đồng ý.
        if not supplier["approved"]:
            raise PolicyViolation(f"Nhà cung cấp {supplier['name']} không còn trong danh sách được duyệt (R3)")
        cursor = conn.execute(
            "INSERT INTO purchase_orders (request_id, supplier_id, amount, idempotency_key) VALUES (?, ?, ?, ?)",
            (request_id, supplier["id"], request["total_amount"], idempotency_key),
        )
        new_version = save_request(conn, request_id, status="PO_DRAFTED")
        output = {
            "id": cursor.lastrowid,
            "request_id": request_id,
            "supplier_id": supplier["id"],
            "supplier": supplier["name"],
            "amount": request["total_amount"],
            "status": "draft",
            "idempotency_key": idempotency_key,
            "request_version": new_version,
            "already_existed": False,
        }
        audit(conn, task_id, request_id, actor_id, "create_po_draft", {"version": version}, output)
    return output


if __name__ == "__main__":
    mcp.run(transport="stdio")
