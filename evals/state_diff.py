"""Chụp và so sánh trạng thái database, để biết agent đã thay đổi những gì."""

import sqlite3
from dataclasses import dataclass
from typing import Any, Literal

# Các phần không thuộc kết quả nghiệp vụ. Mọi thay đổi khác đều được báo.
IGNORED_TABLES = frozenset({"audit_logs", "sim_clock"})
IGNORED_COLUMNS = frozenset({"purchase_requests.updated_at"})

Row = dict[str, Any]


@dataclass(frozen=True)
class Snapshot:
    schema: dict[str, str]  # tên bảng -> câu lệnh CREATE
    rows: dict[str, dict[tuple, Row]]  # tên bảng -> khóa chính -> dòng


@dataclass(frozen=True)
class Change:
    kind: Literal["insert", "update", "delete"]
    table: str
    key: tuple
    fields: dict[str, tuple[Any, Any]]  # tên cột -> (giá trị trước, giá trị sau)

    def __str__(self) -> str:
        key = ", ".join(str(part) for part in self.key)
        fields = ", ".join(f"{name}: {old!r} -> {new!r}" for name, (old, new) in self.fields.items())
        return f"{self.kind} {self.table}[{key}] {fields}"


def _primary_key(conn: sqlite3.Connection, table: str) -> list[str]:
    columns = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    # Cột `pk` là vị trí trong khóa chính, 0 nghĩa là không thuộc khóa chính.
    primary_key = [column[1] for column in sorted(columns, key=lambda column: column[5]) if column[5]]
    if not primary_key:
        raise ValueError(f"Bảng {table} không có khóa chính nên không so sánh được")
    return primary_key


def snapshot(conn: sqlite3.Connection) -> Snapshot:
    schema = dict(
        tuple(row)
        for row in conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    )
    rows = {}
    for table in schema:
        primary_key = _primary_key(conn, table)
        cursor = conn.execute(f'SELECT * FROM "{table}"')
        names = [column[0] for column in cursor.description]
        table_rows = (dict(zip(names, row)) for row in cursor)
        rows[table] = {tuple(row[name] for name in primary_key): row for row in table_rows}
    return Snapshot(schema, rows)


def _diff_row(
    table: str, key: tuple, old: Row | None, new: Row | None, ignored_columns: frozenset[str]
) -> Change | None:
    if old is None:
        kind = "insert"
    elif new is None:
        kind = "delete"
    else:
        kind = "update"

    fields = {}
    for name in old or new:
        if f"{table}.{name}" in ignored_columns:
            continue
        old_value = old[name] if old else None
        new_value = new[name] if new else None
        if kind != "update" or old_value != new_value:
            fields[name] = (old_value, new_value)

    if kind == "update" and not fields:
        return None
    return Change(kind, table, key, fields)


def diff(
    before: Snapshot,
    after: Snapshot,
    ignored_tables: frozenset[str] = IGNORED_TABLES,
    ignored_columns: frozenset[str] = IGNORED_COLUMNS,
) -> list[Change]:
    if before.schema != after.schema:
        raise ValueError("Schema database đã thay đổi giữa hai lần chụp")

    changes = []
    for table in sorted(before.rows.keys() - ignored_tables):
        old_rows, new_rows = before.rows[table], after.rows[table]
        for key in sorted(old_rows.keys() | new_rows.keys()):
            change = _diff_row(table, key, old_rows.get(key), new_rows.get(key), ignored_columns)
            if change is not None:
                changes.append(change)
    return changes
