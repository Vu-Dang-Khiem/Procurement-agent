"""Đọc bộ kịch bản ở `scenarios.yaml`, kiểm tra định dạng, và chuẩn bị dữ liệu riêng cho từng kịch bản."""

import json
import sqlite3
from pathlib import Path

import yaml

SCENARIOS_PATH = Path(__file__).with_name("scenarios.yaml")

TOP_KEYS = {"id", "group", "actor", "goal", "setup", "human", "expect", "note"}
EXPECT_KEYS = {
    "status", "first_stop", "max_tool_calls", "must_call", "forbid_tools",
    "mention", "never_mention", "report_ids", "db",
}
REPORT_STATUSES = {"blocked", "completed", "waiting_approval", "waiting_reply"}
HUMAN_KINDS = ("approve", "reject", "reply")
SETUP_KINDS = ("set_request", "add_request")
REQUEST_FIELDS = ("item", "category", "quantity", "needed_by")


class ScenarioError(ValueError):
    """Kịch bản viết sai định dạng, hoặc không chạy được trên dữ liệu."""


def _check(condition: bool, scenario_id: str, message: str) -> None:
    if not condition:
        raise ScenarioError(f"{scenario_id}: {message}")


def validate(scenario: dict) -> None:
    sid = str(scenario.get("id", "?"))
    _check(not set(scenario) - TOP_KEYS, sid, f"khóa lạ {sorted(set(scenario) - TOP_KEYS)}")
    for key in ("id", "group", "actor", "goal", "expect"):
        _check(key in scenario, sid, f"thiếu {key}")
    expect = scenario["expect"]
    _check(not set(expect) - EXPECT_KEYS, sid, f"khóa lạ trong expect {sorted(set(expect) - EXPECT_KEYS)}")
    _check("status" in expect and "db" in expect, sid, "expect phải có status và db")
    statuses = expect["status"] if isinstance(expect["status"], list) else [expect["status"]]
    _check(set(statuses) <= REPORT_STATUSES, sid, f"status lạ {statuses}")
    if "first_stop" in expect:
        _check(expect["first_stop"] in REPORT_STATUSES, sid, f"first_stop lạ {expect['first_stop']}")
    for group in expect.get("mention", []):
        _check(isinstance(group, list) and group, sid, "mỗi nhóm trong mention phải là danh sách không rỗng")
    for action in scenario.get("human", []):
        kinds = [kind for kind in HUMAN_KINDS if kind in action]
        _check(len(kinds) == 1, sid, f"mỗi bước con người phải có đúng một trong {HUMAN_KINDS}")
        _check(not set(action) - {*HUMAN_KINDS, "optional"}, sid, f"khóa lạ trong human {sorted(action)}")
    for step in scenario.get("setup", []):
        _check(len(step) == 1 and next(iter(step)) in SETUP_KINDS, sid, f"bước setup lạ {step}")


def load_scenarios(path: Path = SCENARIOS_PATH, only: list[str] | None = None) -> list[dict]:
    scenarios = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    ids = [scenario.get("id") for scenario in scenarios]
    if len(ids) != len(set(ids)):
        raise ScenarioError("Có id kịch bản bị trùng")
    for scenario in scenarios:
        validate(scenario)
    if only:
        unknown = set(only) - set(ids)
        if unknown:
            raise ScenarioError(f"Không có kịch bản {sorted(unknown)}")
        scenarios = [scenario for scenario in scenarios if scenario["id"] in only]
    return scenarios


def human_action(action: dict) -> tuple[str, dict]:
    kind = next(kind for kind in HUMAN_KINDS if kind in action)
    return kind, action[kind]


def apply_setup(conn: sqlite3.Connection, steps: list[dict]) -> None:
    """Sửa dữ liệu trực tiếp, không qua tool, nên không có dòng audit và không tăng version."""
    with conn:
        for step in steps:
            (kind, args), = step.items()
            if kind == "set_request":
                args = dict(args)
                request_id = args.pop("id")
                unknown = set(args) - set(REQUEST_FIELDS)
                if unknown:
                    raise ScenarioError(f"set_request không sửa được {sorted(unknown)}")
                row = conn.execute("SELECT extracted_json FROM purchase_requests WHERE id = ?", (request_id,)).fetchone()
                if row is None:
                    raise ScenarioError(f"set_request: không có yêu cầu #{request_id}")
                extracted = json.loads(row[0] or "{}") | args
                conn.execute(
                    "UPDATE purchase_requests SET extracted_json = ? WHERE id = ?",
                    (json.dumps(extracted, ensure_ascii=False, sort_keys=True), request_id),
                )
            else:
                extracted = {field: args.get(field) for field in REQUEST_FIELDS}
                conn.execute(
                    "INSERT INTO purchase_requests (id, requester_id, department_id, source, extracted_json, status,"
                    " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        args["id"], args["requester_id"], args["department_id"], args.get("source", "email"),
                        json.dumps(extracted, ensure_ascii=False, sort_keys=True), args.get("status", "NEW"),
                        args["created_at"], args["created_at"],
                    ),
                )
