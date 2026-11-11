"""Dữ liệu mẫu của công ty giả lập. Mọi tên người và công ty đều hư cấu.

Dữ liệu viết tay, không dùng số ngẫu nhiên, nên lần nào tạo cũng giống hệt nhau
và người viết kịch bản eval biết trước đáp án. Tiền là VND.
"""

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from rules.policy import proposal_snapshot
from simulator.clock import SimClock
from simulator.db import connect, create_schema

START = datetime(2026, 11, 3, 9, 0, 0)
QUARTER = "2026-Q4"

IT, HR, SALES, MARKETING, FINANCE = 1, 2, 3, 4, 5

# id, tên, id trưởng phòng
DEPARTMENTS = [
    (IT, "IT", 1),
    (HR, "HR", 8),
    (SALES, "Sales", 18),
    (MARKETING, "Marketing", 12),
    (FINANCE, "Finance", 26),
]

# id, tên, phòng ban, vai trò
EMPLOYEES = [
    (1, "Nguyễn Văn An", IT, "head"),
    (2, "Trần Thị Bình", IT, "staff"),
    (3, "Lê Minh Châu", IT, "staff"),
    (4, "Phạm Quốc Dũng", IT, "staff"),
    (5, "Hoàng Thu Hà", IT, "staff"),
    (6, "Vũ Đức Hải", IT, "staff"),
    (7, "Đặng Ngọc Hân", IT, "staff"),
    (8, "Bùi Thị Hoa", HR, "head"),
    (9, "Đỗ Văn Hùng", HR, "staff"),
    (10, "Ngô Thanh Huyền", HR, "staff"),
    (11, "Dương Minh Khang", HR, "staff"),
    (12, "Lý Thị Lan", MARKETING, "head"),
    (13, "Mai Văn Long", MARKETING, "staff"),
    (14, "Đinh Khánh Linh", MARKETING, "staff"),
    (15, "Tô Hoàng Minh", MARKETING, "staff"),
    (16, "Hồ Thị Nga", MARKETING, "staff"),
    (17, "Cao Đình Nam", MARKETING, "staff"),
    (18, "Phan Thị Oanh", SALES, "head"),
    (19, "Trịnh Văn Phong", SALES, "staff"),
    (20, "Lương Thu Phương", SALES, "staff"),
    (21, "Tạ Quang Sơn", SALES, "staff"),
    (22, "Kiều Thị Tâm", SALES, "staff"),
    (23, "Chu Văn Thắng", SALES, "staff"),
    (24, "Quách Mỹ Trang", SALES, "staff"),
    (25, "Võ Anh Tuấn", SALES, "staff"),
    (26, "Nguyễn Thị Uyên", FINANCE, "head"),
    (27, "Trần Văn Việt", FINANCE, "finance"),
    (28, "Lê Thị Xuân", FINANCE, "finance"),
    (29, "Phạm Hải Yến", FINANCE, "finance"),
    (30, "Hoàng Gia Bảo", None, "director"),
]

# phòng ban, tổng, đã chi, đang giữ chỗ
# Giữ chỗ bằng tổng tiền các yêu cầu đang chờ duyệt, đã duyệt và đã có đơn nháp.
BUDGETS = [
    (IT, 500_000_000, 120_000_000, 89_650_000),
    (HR, 150_000_000, 40_000_000, 8_250_000),
    (SALES, 250_000_000, 90_000_000, 36_720_000),
    (MARKETING, 300_000_000, 150_000_000, 0),
    (FINANCE, 100_000_000, 20_000_000, 11_440_000),
]

# id, tên, được duyệt, nhóm hàng, đánh giá
SUPPLIERS = [
    (1, "Phúc Thành", 1, ["it"], 3.2),  # rẻ nhưng hay giao trễ
    (2, "Minh Long Tech", 1, ["it"], 4.6),
    (3, "An Khang IT", 1, ["it"], 4.2),
    (4, "Hoàng Gia Computer", 1, ["it"], 3.9),
    (5, "Bình Minh Văn Phòng", 1, ["office"], 4.4),
    (6, "Bảo An Office", 1, ["office", "it"], 4.0),
    (7, "Thiên Phúc Nội Thất", 1, ["furniture"], 4.3),
    (8, "Tân Việt Furniture", 1, ["furniture"], 3.8),
    (9, "Sao Mai Media", 1, ["marketing"], 4.5),
    (10, "Ánh Dương Print", 1, ["marketing"], 4.1),
    (11, "Đại Dương Software", 1, ["software"], 4.0),
    (12, "Trường An Supply", 1, ["office", "furniture"], 3.7),
    (13, "Giá Rẻ 24h", 0, ["it", "office"], 2.9),
    (14, "Đại Lợi Trading", 0, ["furniture"], 3.0),
    (15, "Nam Tiến Supply", 0, ["it"], None),
]

MONITOR = "Màn hình 24 inch"
LAPTOP = "Laptop văn phòng 14 inch"
PROJECTOR = "Máy chiếu"
PRINTER = "Máy in laser A4"
CHAIR = "Ghế công thái học"
DESK = "Bàn làm việc 1m2"
HEADSET = "Tai nghe họp trực tuyến"
FLYER = "In tờ rơi A5 (1000 tờ)"
LICENSE = "Giấy phép phần mềm thiết kế (1 năm)"
PAPER = "Giấy in A4 (thùng)"

# id, nhà cung cấp, mặt hàng, đơn giá trước VAT, VAT %, ngày giao, tháng bảo hành, hạn báo giá
QUOTATIONS = [
    (1, 2, MONITOR, 5_200_000, 10, 5, 36, "2026-12-01"),
    (2, 3, MONITOR, 5_000_000, 10, 7, 24, "2026-11-30"),
    (3, 1, MONITOR, 4_800_000, 8, 14, 24, "2026-11-20"),
    (4, 4, MONITOR, 4_600_000, 10, 3, 24, "2026-10-15"),  # đã hết hạn (R7)
    (5, 13, MONITOR, 4_200_000, 10, 2, 12, "2026-12-15"),  # rẻ nhất nhưng chưa được duyệt (R3)
    (6, 2, LAPTOP, 18_500_000, 10, 7, 24, "2026-12-10"),
    (7, 3, LAPTOP, 17_900_000, 10, 10, 24, "2026-11-25"),
    (8, 1, LAPTOP, 17_000_000, 8, 21, 12, "2026-12-05"),
    (9, 4, PROJECTOR, 7_800_000, 10, 7, 24, "2026-11-28"),
    (10, 2, PROJECTOR, 8_200_000, 10, 5, 24, "2026-12-01"),
    (11, 6, PRINTER, 6_500_000, 10, 5, 12, "2026-12-01"),
    (12, 7, CHAIR, 3_200_000, 10, 7, 36, "2026-12-15"),
    (13, 8, CHAIR, 3_000_000, 10, 10, 24, "2026-10-20"),  # đã hết hạn (R7)
    (14, 14, CHAIR, 2_500_000, 10, 5, 12, "2026-12-20"),  # chưa được duyệt (R3)
    (15, 7, DESK, 2_500_000, 10, 7, 24, "2026-12-15"),
    (16, 12, DESK, 2_400_000, 10, 10, 12, "2026-12-10"),
    (17, 1, HEADSET, 1_400_000, 10, 14, 12, "2026-12-01"),
    (18, 3, HEADSET, 1_500_000, 10, 5, 12, "2026-11-30"),
    (19, 9, FLYER, 900_000, 8, 3, 0, "2026-11-25"),
    (20, 10, FLYER, 850_000, 8, 5, 0, "2026-11-20"),
    (21, 11, LICENSE, 12_000_000, 10, 1, 12, "2026-12-31"),
    (22, 5, PAPER, 420_000, 10, 2, 0, "2026-12-31"),
]

# id, người tạo, phòng ban, trạng thái, mặt hàng, nhóm hàng, số lượng, cần trước ngày,
# báo giá trong hồ sơ (báo giá đầu tiên là báo giá được chọn), tạo lúc, cập nhật lúc
REQUESTS = [
    (1, 3, IT, "NEW", MONITOR, "it", 10, "2026-11-20", (), "2026-11-03T08:30:00", "2026-11-03T08:30:00"),
    (2, 9, HR, "NEW", PRINTER, "it", 1, "2026-11-30", (), "2026-11-02T14:00:00", "2026-11-02T14:00:00"),
    # Thiếu số lượng, đang chờ người yêu cầu trả lời.
    (3, 20, SALES, "NEEDS_INFO", HEADSET, "it", None, None, (), "2026-11-02T09:00:00", "2026-11-02T10:00:00"),
    (4, 14, MARKETING, "IN_REVIEW", FLYER, "marketing", 15, "2026-11-15", (20,), "2026-11-02T11:00:00", "2026-11-02T15:00:00"),
    # Treo 5 ngày ở cấp Finance.
    (5, 4, IT, "PENDING_APPROVAL", LAPTOP, "it", 4, "2026-11-25", (6, 7), "2026-10-28T10:00:00", "2026-10-29T09:00:00"),
    (6, 10, HR, "PENDING_APPROVAL", DESK, "furniture", 3, "2026-11-20", (15,), "2026-11-02T09:30:00", "2026-11-02T16:00:00"),
    (7, 27, FINANCE, "APPROVED", MONITOR, "it", 2, "2026-11-20", (1,), "2026-10-30T09:00:00", "2026-11-02T11:00:00"),
    # Finance từ chối 85,8 triệu, đề nghị giảm còn 6 cái.
    (8, 21, SALES, "REJECTED", PROJECTOR, "it", 10, "2026-11-30", (9, 10), "2026-10-27T09:00:00", "2026-11-02T17:00:00"),
    (9, 5, IT, "PO_DRAFTED", HEADSET, "it", 5, "2026-11-15", (18,), "2026-10-26T09:00:00", "2026-10-30T14:00:00"),
    (10, 15, MARKETING, "COMPLETED", MONITOR, "it", 6, "2026-11-05", (1, 2), "2026-10-22T09:00:00", "2026-10-29T16:00:00"),
    (11, 22, SALES, "CANCELLED", PAPER, "office", 10, "2026-11-10", (), "2026-10-20T09:00:00", "2026-10-21T09:00:00"),
    # Vượt ngân sách của Marketing với mọi báo giá (R1).
    (12, 17, MARKETING, "NEW", LAPTOP, "it", 20, "2026-12-01", (), "2026-11-02T13:00:00", "2026-11-02T13:00:00"),
    # Nghi trùng với yêu cầu 10: cùng phòng, cùng mặt hàng, trong 14 ngày (R6).
    (13, 16, MARKETING, "NEW", MONITOR, "it", 5, "2026-11-20", (), "2026-11-02T15:30:00", "2026-11-02T15:30:00"),
    # Ba yêu cầu 14-16: mỗi cái dưới 20 triệu, cộng lại vượt ngưỡng (R8).
    (14, 19, SALES, "IN_REVIEW", DESK, "furniture", 7, "2026-11-25", (16,), "2026-11-01T09:00:00", "2026-11-01T10:00:00"),
    (15, 19, SALES, "IN_REVIEW", DESK, "furniture", 7, "2026-11-25", (16,), "2026-11-02T09:00:00", "2026-11-02T10:00:00"),
    (16, 19, SALES, "IN_REVIEW", DESK, "furniture", 7, "2026-11-25", (16,), "2026-11-03T08:00:00", "2026-11-03T08:30:00"),
    # Trưởng phòng tự tạo yêu cầu nên không được tự duyệt (R5).
    (17, 1, IT, "NEW", PRINTER, "it", 2, "2026-11-30", (), "2026-11-03T08:45:00", "2026-11-03T08:45:00"),
    (18, 23, SALES, "PO_DRAFTED", LAPTOP, "it", 2, "2026-12-05", (8, 7), "2026-10-29T09:00:00", "2026-11-02T10:00:00"),
    # Chỉ có một báo giá dùng được, trong khi mức tiền cần hai báo giá (R2).
    (19, 11, HR, "NEW", CHAIR, "furniture", 20, "2026-12-15", (), "2026-11-02T16:30:00", "2026-11-02T16:30:00"),
]

# yêu cầu, cấp duyệt, người duyệt, quyết định, lý do, thời điểm
APPROVALS = [
    (5, "head", 1, "approved", None, "2026-10-29T09:00:00"),
    (5, "finance", 27, None, None, None),
    (6, "head", 8, None, None, None),
    (7, "head", 26, "approved", None, "2026-11-02T11:00:00"),
    (8, "head", 18, "approved", None, "2026-10-29T10:00:00"),
    (8, "finance", 28, "rejected", "Giảm còn 6 cái", "2026-11-02T17:00:00"),
    (9, "head", 1, "approved", None, "2026-10-28T09:00:00"),
    (10, "head", 12, "approved", None, "2026-10-23T09:00:00"),
    (10, "finance", 28, "approved", None, "2026-10-26T09:00:00"),
    (18, "head", 18, "approved", None, "2026-10-30T09:00:00"),
    (18, "finance", 28, "approved", None, "2026-11-02T09:00:00"),
]

# yêu cầu, câu hỏi, thời điểm hỏi
CLARIFICATIONS = [
    (3, "Bạn cần bao nhiêu tai nghe, và cần trước ngày nào?", "2026-11-02T10:00:00"),
]

# yêu cầu, trạng thái
PURCHASE_ORDERS = [
    (9, "draft"),
    (10, "confirmed"),
    (18, "draft"),
]


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _total(quotation_id: int, quantity: int) -> int:
    quotation = QUOTATIONS[quotation_id - 1]
    unit_price, vat_rate = quotation[3], quotation[4]
    return unit_price * quantity * (100 + vat_rate) // 100


def _request_row(row: tuple) -> tuple:
    id_, requester, department, status, item, category, quantity, needed_by, quotes, created, updated = row
    extracted = {"item": item, "category": category, "quantity": quantity, "needed_by": needed_by}
    proposal = None
    total = None
    if quotes:
        proposal = _dumps({"selected_quotation_id": quotes[0], "quotation_ids": list(quotes)})
        total = _total(quotes[0], quantity)
    return (id_, requester, department, "form", _dumps(extracted), proposal, status, total, created, updated)


def _approval_row(row: tuple) -> tuple:
    """Dòng từ chối kèm ảnh chụp hồ sơ lúc đó, như `decide_approval` lưu, để R9 có cái mà so sánh."""
    request_id, _, _, decision, _, _ = row
    snapshot = None
    if decision == "rejected":
        request = REQUESTS[request_id - 1]
        quotes = request[8]
        snapshot = _dumps(proposal_snapshot(request[4], request[6], quotes[0], list(quotes)))
    return (*row, snapshot)


def _purchase_order_row(request_id: int, status: str) -> tuple:
    request = REQUESTS[request_id - 1]
    selected = request[8][0]
    supplier_id = QUOTATIONS[selected - 1][1]
    return (request_id, supplier_id, _total(selected, request[6]), f"req-{request_id}-v1", status)


def seed(conn: sqlite3.Connection) -> None:
    with conn:
        conn.executemany("INSERT INTO departments (id, name, head_id) VALUES (?, ?, ?)", DEPARTMENTS)
        conn.executemany(
            "INSERT INTO employees (id, name, email, department_id, role) VALUES (?, ?, ?, ?, ?)",
            [(id_, name, f"nv{id_:02d}@corp.test", dept, role) for id_, name, dept, role in EMPLOYEES],
        )
        conn.executemany(
            "INSERT INTO budgets (department_id, quarter, total, spent, reserved) VALUES (?, ?, ?, ?, ?)",
            [(dept, QUARTER, total, spent, reserved) for dept, total, spent, reserved in BUDGETS],
        )
        conn.executemany(
            "INSERT INTO suppliers (id, name, approved, categories, rating) VALUES (?, ?, ?, ?, ?)",
            [(id_, name, approved, _dumps(cats), rating) for id_, name, approved, cats, rating in SUPPLIERS],
        )
        conn.executemany(
            "INSERT INTO quotations (id, supplier_id, item, unit_price, vat_rate, delivery_days,"
            " warranty_months, valid_until, source_file) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(*row, f"quotes/q{row[0]:03d}.pdf") for row in QUOTATIONS],
        )
        conn.executemany(
            "INSERT INTO purchase_requests (id, requester_id, department_id, source, extracted_json,"
            " proposal_json, status, total_amount, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [_request_row(row) for row in REQUESTS],
        )
        conn.executemany(
            "INSERT INTO approvals (request_id, level, approver_id, decision, reason, decided_at, snapshot_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [_approval_row(row) for row in APPROVALS],
        )
        conn.executemany(
            "INSERT INTO clarifications (request_id, question, asked_at) VALUES (?, ?, ?)",
            CLARIFICATIONS,
        )
        conn.executemany(
            "INSERT INTO purchase_orders (request_id, supplier_id, amount, idempotency_key, status)"
            " VALUES (?, ?, ?, ?, ?)",
            [_purchase_order_row(*row) for row in PURCHASE_ORDERS],
        )
    SimClock(conn).set(START)


def build_database(path: str | Path = ":memory:") -> sqlite3.Connection:
    conn = connect(path)
    create_schema(conn)
    seed(conn)
    return conn


if __name__ == "__main__":
    build_database(sys.argv[1]).close()
