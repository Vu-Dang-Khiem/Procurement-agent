-- Schema của công ty giả lập.
-- Tiền là số nguyên VND. Thời gian là chuỗi ISO 8601 do đồng hồ giả lập cấp,
-- nên không có cột nào mặc định lấy giờ thật.

CREATE TABLE departments (
    id      INTEGER PRIMARY KEY,
    name    TEXT NOT NULL UNIQUE,
    -- Phòng ban và trưởng phòng trỏ lẫn nhau, nên chỉ kiểm tra khi commit.
    head_id INTEGER REFERENCES employees(id) DEFERRABLE INITIALLY DEFERRED
) STRICT;

CREATE TABLE employees (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    department_id INTEGER REFERENCES departments(id),
    role          TEXT NOT NULL CHECK (role IN ('staff', 'head', 'finance', 'director')),
    -- Giám đốc không thuộc phòng ban nào.
    CHECK (role = 'director' OR department_id IS NOT NULL)
) STRICT;

CREATE TABLE budgets (
    department_id INTEGER NOT NULL REFERENCES departments(id),
    quarter       TEXT NOT NULL CHECK (quarter GLOB '[0-9][0-9][0-9][0-9]-Q[1-4]'),
    total         INTEGER NOT NULL CHECK (total >= 0),
    spent         INTEGER NOT NULL DEFAULT 0 CHECK (spent >= 0),
    reserved      INTEGER NOT NULL DEFAULT 0 CHECK (reserved >= 0),
    PRIMARY KEY (department_id, quarter),
    -- Chốt chặn cuối cho R1, phòng khi code quy tắc có lỗi.
    CHECK (spent + reserved <= total)
) STRICT;

CREATE TABLE suppliers (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    approved   INTEGER NOT NULL CHECK (approved IN (0, 1)),
    categories TEXT NOT NULL CHECK (json_valid(categories)),  -- mảng JSON
    rating     REAL CHECK (rating BETWEEN 0 AND 5)
) STRICT;

CREATE TABLE quotations (
    id              INTEGER PRIMARY KEY,
    supplier_id     INTEGER NOT NULL REFERENCES suppliers(id),
    item            TEXT NOT NULL,
    unit_price      INTEGER NOT NULL CHECK (unit_price > 0),
    vat_rate        INTEGER NOT NULL CHECK (vat_rate BETWEEN 0 AND 100),  -- phần trăm
    delivery_days   INTEGER NOT NULL CHECK (delivery_days >= 0),
    warranty_months INTEGER NOT NULL CHECK (warranty_months >= 0),
    valid_until     TEXT NOT NULL,
    source_file     TEXT
) STRICT;

CREATE TABLE purchase_requests (
    id             INTEGER PRIMARY KEY,
    requester_id   INTEGER NOT NULL REFERENCES employees(id),
    department_id  INTEGER NOT NULL REFERENCES departments(id),
    source         TEXT NOT NULL,
    extracted_json TEXT CHECK (extracted_json IS NULL OR json_valid(extracted_json)),
    proposal_json  TEXT CHECK (proposal_json IS NULL OR json_valid(proposal_json)),
    status         TEXT NOT NULL DEFAULT 'NEW' CHECK (status IN (
        'NEW', 'NEEDS_INFO', 'IN_REVIEW', 'PENDING_APPROVAL', 'APPROVED', 'REJECTED',
        'PO_DRAFTED', 'COMPLETED', 'ESCALATED', 'CANCELLED'
    )),
    total_amount   INTEGER CHECK (total_amount >= 0),  -- đã gồm VAT
    version        INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
) STRICT;

CREATE TABLE approvals (
    id          INTEGER PRIMARY KEY,
    request_id  INTEGER NOT NULL REFERENCES purchase_requests(id),
    task_id     TEXT,
    level       TEXT NOT NULL CHECK (level IN ('head', 'finance', 'director')),
    approver_id INTEGER NOT NULL REFERENCES employees(id),
    decision    TEXT CHECK (decision IN ('approved', 'rejected')),  -- NULL là đang chờ
    reason      TEXT,
    decided_at  TEXT,
    -- Hồ sơ lúc bị từ chối (mặt hàng, số lượng, báo giá), để R9 chặn việc gửi lại y nguyên.
    snapshot_json TEXT CHECK (snapshot_json IS NULL OR json_valid(snapshot_json)),
    CHECK ((decision IS NULL) = (decided_at IS NULL))
) STRICT;

CREATE TABLE clarifications (
    id         INTEGER PRIMARY KEY,
    request_id INTEGER NOT NULL REFERENCES purchase_requests(id),
    task_id    TEXT,
    question   TEXT NOT NULL,
    reply_json TEXT CHECK (reply_json IS NULL OR json_valid(reply_json)),
    status     TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'answered', 'cancelled')),
    asked_at   TEXT NOT NULL,
    replied_at TEXT
) STRICT;

CREATE TABLE purchase_orders (
    id              INTEGER PRIMARY KEY,
    request_id      INTEGER NOT NULL REFERENCES purchase_requests(id),
    supplier_id     INTEGER NOT NULL REFERENCES suppliers(id),
    amount          INTEGER NOT NULL CHECK (amount > 0),
    idempotency_key TEXT NOT NULL UNIQUE,
    status          TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'confirmed', 'cancelled'))
) STRICT;

CREATE TABLE audit_logs (
    id         INTEGER PRIMARY KEY,
    task_id    TEXT,
    request_id INTEGER REFERENCES purchase_requests(id),
    actor      TEXT NOT NULL,
    action     TEXT NOT NULL,
    tool_name  TEXT,
    input      TEXT,
    output     TEXT,
    ts         TEXT NOT NULL
) STRICT;

-- Bảng kỹ thuật của đồng hồ giả lập, luôn có tối đa một dòng.
CREATE TABLE sim_clock (
    id  INTEGER PRIMARY KEY CHECK (id = 1),
    now TEXT NOT NULL
) STRICT;
