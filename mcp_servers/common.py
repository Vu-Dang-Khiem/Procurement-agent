"""Phần dùng chung của các MCP server: hợp đồng tool, kết nối database và audit log.

Hợp đồng ở mục 4.2 của tài liệu thiết kế: mọi tool trả về cùng một dạng, kể cả khi lỗi.
"""

import json
import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from rules.errors import InvalidInput, NotFound, PermissionDenied, PolicyViolation, RuleError, Transient
from rules.permissions import can_view_request, load_actor
from rules.policy import load_request
from simulator.clock import SimClock
from simulator.db import connect

DB_ENV = "PROCUREMENT_DB"

_ERROR_TYPES = {
    NotFound: "not_found",
    InvalidInput: "validation",
    PolicyViolation: "policy",
    PermissionDenied: "permission",
    Transient: "transient",
}


def ok(data: Any, truncated: bool = False, ref: str | None = None) -> dict:
    result = {"ok": True, "data": data, "truncated": truncated}
    if ref is not None:
        result["ref"] = ref
    return result


def error(error_type: str, message: str, hint: str | None = None) -> dict:
    result = {"ok": False, "error_type": error_type, "message": message}
    if hint is not None:
        result["hint"] = hint
    return result


def dumps(value: Any) -> str:
    # Thứ tự khóa cố định để giữ được prompt cache; không escape tiếng Việt để đỡ tốn token.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def contract(fn: Callable[..., Any]) -> Callable[..., dict]:
    """Đổi giá trị trả về và lỗi nghiệp vụ của tool sang dạng hợp đồng chung."""

    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict:
        try:
            return ok(fn(*args, **kwargs))
        except RuleError as rule_error:
            return error(_ERROR_TYPES[type(rule_error)], rule_error.message, rule_error.hint)
        except sqlite3.OperationalError as db_error:
            if "locked" in str(db_error) or "busy" in str(db_error):
                return error("transient", "Database đang bận", "Hãy thử lại")
            raise

    return wrapper


def register_tool(server: FastMCP) -> Callable[[Callable[..., Any]], Callable[..., dict]]:
    """Đăng ký tool với MCP dưới dạng JSON gọn, và trả về hàm trả dict để gọi trực tiếp khi test."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., dict]:
        tool = contract(fn)

        @wraps(fn)
        def as_text(*args: Any, **kwargs: Any) -> str:
            return dumps(tool(*args, **kwargs))

        server.tool(structured_output=False)(as_text)
        return tool

    return decorator


@contextmanager
def open_db() -> Iterator[sqlite3.Connection]:
    path = os.environ.get(DB_ENV)
    # sqlite3 tự tạo file rỗng nếu đường dẫn sai, nên phải kiểm tra trước.
    if not path or not Path(path).is_file():
        raise RuntimeError(f"Biến môi trường {DB_ENV} phải trỏ đến một database đã tạo, hiện là {path!r}")
    conn = connect(path)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def write_transaction(conn: sqlite3.Connection) -> Iterator[None]:
    # Giành quyền ghi ngay từ đầu, để hai tiến trình không cùng đọc rồi cùng dùng một khoản ngân sách.
    conn.execute("BEGIN IMMEDIATE")
    with conn:
        yield


def load_for_actor(conn: sqlite3.Connection, actor_id: int, request_id: int) -> sqlite3.Row:
    request = load_request(conn, request_id)
    actor = load_actor(conn, actor_id)
    if not can_view_request(actor, request["requester_id"], request["department_id"]):
        raise PermissionDenied(f"Nhân viên #{actor_id} không có quyền với yêu cầu #{request_id}")
    return request


def load_for_write(conn: sqlite3.Connection, actor_id: int, request_id: int, version: int) -> sqlite3.Row:
    request = load_for_actor(conn, actor_id, request_id)
    if request["version"] != version:
        raise InvalidInput(
            f"Yêu cầu #{request_id} đã đổi sang version {request['version']}, bạn đang dùng version {version}",
            "Hãy gọi get_request để xem dữ liệu mới rồi quyết định lại",
        )
    return request


def save_request(conn: sqlite3.Connection, request_id: int, **fields: object) -> int:
    """Ghi các cột đã đổi, tăng version và trả về version mới. Tên cột chỉ đến từ code của server."""
    columns = "".join(f"{name} = ?, " for name in fields)
    conn.execute(
        f"UPDATE purchase_requests SET {columns}version = version + 1, updated_at = ? WHERE id = ?",
        (*fields.values(), SimClock(conn).now_iso(), request_id),
    )
    return conn.execute("SELECT version FROM purchase_requests WHERE id = ?", (request_id,)).fetchone()[0]


def audit(
    conn: sqlite3.Connection,
    task_id: str | None,
    request_id: int | None,
    actor_id: int,
    tool_name: str,
    tool_input: dict,
    output: dict,
) -> None:
    conn.execute(
        "INSERT INTO audit_logs (task_id, request_id, actor, action, tool_name, input, output, ts)"
        " VALUES (?, ?, ?, 'tool_call', ?, ?, ?, ?)",
        (task_id, request_id, f"employee:{actor_id}", tool_name, dumps(tool_input), dumps(output), SimClock(conn).now_iso()),
    )
