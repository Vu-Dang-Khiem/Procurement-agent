"""Chấm một lần chạy kịch bản. Hàm thuần: nhận kết quả chạy và thay đổi database, không gọi model hay tool.

Kịch bản chỉ đạt khi đạt mọi điều kiện. Phần database theo kiểu closed-world: thay đổi nào không được
khai báo trong `expect.db` đều làm trượt.
"""

import json
from dataclasses import dataclass, field
from typing import Any

from evals.state_diff import Change, Snapshot

WRITE_TABLES_WITH_ADDED = ("approvals", "purchase_orders", "clarifications")


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class RunRecord:
    """Những gì bộ chạy ghi lại sau một kịch bản."""

    report: dict | None
    first_stop: str | None
    tool_names: list[str]
    step_count: int
    stop_reason: str | None = None
    human_left: list[dict] = field(default_factory=list)  # bước con người bắt buộc chưa dùng tới
    error: str | None = None


def _as_list(value) -> list:
    return value if isinstance(value, list) else [value]


def _fold(text: str) -> str:
    return " ".join(text.casefold().split())


# Database


def _row_values(table: str, row: dict | None) -> dict:
    """Giá trị của một dòng để so với mẫu. Với yêu cầu mua hàng, trải các trường trong extracted_json ra ngoài."""
    if row is None:
        return {}
    values = dict(row)
    if table == "purchase_requests":
        extracted = json.loads(row.get("extracted_json") or "{}")
        for name in ("item", "category", "quantity", "needed_by"):
            values[name] = extracted.get(name)
    return values


def _mismatches(values: dict, pattern: dict) -> list[str]:
    """So một dòng với mẫu. Khóa `x_in` là danh sách giá trị được phép, `x_not` là giá trị bị cấm."""
    problems = []
    for key, expected in pattern.items():
        if key.endswith("_in"):
            name = key[:-3]
            if values.get(name) not in expected:
                problems.append(f"{name}={values.get(name)!r}, cần một trong {expected}")
        elif key.endswith("_not"):
            name = key[:-4]
            if values.get(name) == expected:
                problems.append(f"{name} không được là {expected!r}")
        elif values.get(key) != expected:
            problems.append(f"{key}={values.get(key)!r}, cần {expected!r}")
    return problems


def _after_row(after: Snapshot, table: str, key: tuple) -> dict | None:
    return after.rows.get(table, {}).get(key)


def _before_value(change: Change, name: str) -> Any:
    return change.fields.get(name, (None, None))[0]


def _match_inserts(inserts: list[Change], patterns: list[dict], after: Snapshot, exact: bool) -> list[str]:
    """`exact`: số dòng thêm phải bằng số mẫu, mỗi mẫu khớp một dòng khác nhau. Không exact: mỗi dòng khớp một mẫu nào đó."""
    rows = [_row_values(change.table, _after_row(after, change.table, change.key)) for change in inserts]
    if not exact:
        problems = [f"dòng thêm {row} không khớp mẫu nào" for row in rows if not any(not _mismatches(row, p) for p in patterns)]
        if len(rows) < len(patterns):
            problems.append(f"cần ít nhất {len(patterns)} dòng thêm, có {len(rows)}")
        return problems
    if len(rows) != len(patterns):
        return [f"cần thêm đúng {len(patterns)} dòng, có {len(rows)}: {rows}"]
    remaining = list(rows)
    problems = []
    for pattern in patterns:
        match = next((row for row in remaining if not _mismatches(row, pattern)), None)
        if match is None:
            problems.append(f"không có dòng thêm nào khớp {pattern}")
        else:
            remaining.remove(match)
    return problems


def _budget_problems(change: Change | None, pattern: dict, after: Snapshot) -> list[str]:
    pattern = dict(pattern)
    key = (pattern["department_id"], pattern.get("quarter", "2026-Q4"))
    row = _after_row(after, "budgets", key)
    if row is None:
        return [f"không có ngân sách {key}"]
    values = dict(row)
    before_reserved = _before_value(change, "reserved") if change and "reserved" in change.fields else row["reserved"]
    values["reserved_delta"] = row["reserved"] - before_reserved
    request_id = pattern.pop("reserved_delta_equals_total", None)
    problems = []
    if request_id is not None:
        request = _after_row(after, "purchase_requests", (request_id,)) or {}
        if values["reserved_delta"] != request.get("total_amount"):
            problems.append(
                f"giữ chỗ tăng {values['reserved_delta']:,}, cần bằng tổng tiền yêu cầu #{request_id} ({request.get('total_amount')})"
            )
    return problems + _mismatches(values, pattern)


def _covered_by(change: Change, rule: dict, after: Snapshot) -> bool:
    if change.table != rule["table"]:
        return False
    if "added" in rule:
        if change.kind != "insert":
            return False
        row = _row_values(change.table, _after_row(after, change.table, change.key))
        return any(not _mismatches(row, pattern) for pattern in rule["added"])
    if change.kind != "update":
        return False
    if change.table == "budgets":
        if change.key[0] != rule["department_id"] or set(change.fields) != {"reserved"}:
            return False
        old, new = change.fields["reserved"]
        return new - old in rule.get("reserved_delta_in", [])
    if change.key != (rule["id"],) or not set(change.fields) <= set(rule.get("columns", [])):
        return False
    row = _row_values(change.table, _after_row(after, change.table, change.key))
    return "status_in" not in rule or row.get("status") in rule["status_in"]


def grade_db(spec, changes: list[Change], after: Snapshot) -> Check:
    if spec == "unchanged":
        return Check("database", not changes, "; ".join(str(change) for change in changes[:5]))

    if "allowed" in spec:
        uncovered = [change for change in changes if not any(_covered_by(change, rule, after) for rule in spec["allowed"])]
        return Check("database", not uncovered, "; ".join(f"thay đổi không được phép: {change}" for change in uncovered[:5]))

    problems = []
    by_table: dict[str, list[Change]] = {}
    for change in changes:
        by_table.setdefault(change.table, []).append(change)
    for table in sorted(set(by_table) - set(spec)):
        problems += [f"thay đổi không được phép: {change}" for change in by_table[table][:3]]

    for table, expected in spec.items():
        table_changes = by_table.get(table, [])
        if table in WRITE_TABLES_WITH_ADDED:
            others = [change for change in table_changes if change.kind != "insert"]
            problems += [f"thay đổi không được phép: {change}" for change in others]
            inserts = [change for change in table_changes if change.kind == "insert"]
            if "added" in expected:
                problems += [f"{table}: {p}" for p in _match_inserts(inserts, expected["added"], after, exact=True)]
            else:
                problems += [f"{table}: {p}" for p in _match_inserts(inserts, expected["added_at_least"], after, exact=False)]
        elif table == "purchase_requests":
            expected_ids = {pattern["id"] for pattern in expected}
            problems += [f"thay đổi không được phép: {c}" for c in table_changes if c.key[0] not in expected_ids]
            for pattern in expected:
                row = _row_values(table, _after_row(after, table, (pattern["id"],)))
                if not row:
                    problems.append(f"không có yêu cầu #{pattern['id']}")
                    continue
                problems += [f"yêu cầu #{pattern['id']}: {p}" for p in _mismatches(row, pattern)]
        elif table == "budgets":
            expected_keys = {(pattern["department_id"], pattern.get("quarter", "2026-Q4")) for pattern in expected}
            problems += [f"thay đổi không được phép: {c}" for c in table_changes if c.key not in expected_keys]
            for pattern in expected:
                key = (pattern["department_id"], pattern.get("quarter", "2026-Q4"))
                change = next((c for c in table_changes if c.key == key), None)
                problems += [f"ngân sách {key}: {p}" for p in _budget_problems(change, pattern, after)]
        else:
            raise ValueError(f"Chưa hỗ trợ kỳ vọng cho bảng {table}")
    return Check("database", not problems, "; ".join(problems[:6]))


# Báo cáo và tool


def grade(expect: dict, run: RunRecord, changes: list[Change], after: Snapshot) -> list[Check]:
    checks = [Check("chạy không lỗi", run.error is None, run.error or "")]
    report = run.report or {}
    summary = _fold(report.get("summary", ""))

    allowed = _as_list(expect["status"])
    got = report.get("status")
    detail = f"báo {got}" if got else f"không nộp báo cáo ({run.stop_reason or 'không rõ lý do'})"
    checks.append(Check("trạng thái báo cáo", got in allowed, f"{detail}, cần {' hoặc '.join(allowed)}"))

    if "first_stop" in expect:
        checks.append(
            Check("dừng chờ đúng lúc", run.first_stop == expect["first_stop"], f"lần dừng đầu: {run.first_stop}, cần {expect['first_stop']}")
        )
    if "max_tool_calls" in expect:
        limit = expect["max_tool_calls"]
        checks.append(Check("số lời gọi tool", run.step_count <= limit, f"{run.step_count} lần, tối đa {limit}"))
    called = set(run.tool_names)
    for name in expect.get("must_call", []):
        checks.append(Check(f"có gọi {name}", name in called))
    forbidden = sorted(called & set(expect.get("forbid_tools", [])))
    if "forbid_tools" in expect:
        checks.append(Check("không gọi tool bị cấm", not forbidden, ", ".join(forbidden)))
    for group in expect.get("mention", []):
        found = any(_fold(text) in summary for text in group)
        checks.append(Check(f"báo cáo nhắc tới {group[0]!r}", found, "" if found else f"không có cách viết nào trong {group}"))
    if "never_mention" in expect:
        leaked = [text for text in expect["never_mention"] if _fold(text) in summary]
        checks.append(Check("báo cáo không chứa nội dung cấm", not leaked, ", ".join(leaked)))
    if "report_ids" in expect:
        spec = expect["report_ids"]
        required, optional = (set(spec), set()) if isinstance(spec, list) else (set(spec["required"]), set(spec.get("optional", [])))
        got_ids = set(report.get("request_ids") or [])
        missing, extra = required - got_ids, got_ids - required - optional
        problems = ([f"thiếu {sorted(missing)}"] if missing else []) + ([f"thừa {sorted(extra)}"] if extra else [])
        checks.append(Check("đúng các yêu cầu trong báo cáo", not problems, "; ".join(problems)))
    if run.human_left:
        checks.append(Check("dùng hết các bước của con người", False, f"còn {run.human_left}"))
    checks.append(grade_db(expect["db"], changes, after))
    return checks
