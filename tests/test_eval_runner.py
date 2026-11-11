"""Bộ chạy và chấm kịch bản. Dùng model giả theo kịch bản viết sẵn và 3 MCP server thật, không gọi API."""

import asyncio
import json

import pytest
from quarantine_fakes import FakeModel

from agent.fake_model import ScriptedModel, call, last_result
from evals.grader import RunRecord, grade, grade_db
from evals.run_scenarios import run_scenario, summarize
from evals.scenarios import ScenarioError, apply_setup, load_scenarios, validate
from evals.state_diff import diff, snapshot
from quarantine.schemas import EmailDraft, EmailItemDraft
from simulator.seed import build_database

SCENARIOS = {scenario["id"]: scenario for scenario in load_scenarios()}


def run(scenario_id, steps, tmp_path, quarantine_model=None):
    model = ScriptedModel(steps)
    return asyncio.run(run_scenario(SCENARIOS[scenario_id], model, quarantine_model, tmp_path / scenario_id))


def failed(result):
    return {check["name"] for check in result["checks"] if not check["ok"]}


def report(status, summary, request_ids=None):
    args = {"status": status, "summary": summary}
    if request_ids is not None:
        args["request_ids"] = request_ids
    return call("submit_final_report", **args)


def version_of(messages):
    return last_result(messages)["data"]["version"]


# Đọc kịch bản


def test_every_scenario_in_the_file_is_valid():
    assert len(SCENARIOS) == 21
    assert all(scenario["goal"].strip() for scenario in SCENARIOS.values())


@pytest.mark.parametrize(
    "broken",
    [
        {"id": "x", "group": "g", "actor": 1, "goal": "a", "expect": {"status": "done", "db": "unchanged"}},
        {"id": "x", "group": "g", "actor": 1, "goal": "a", "expect": {"status": "blocked"}},
        {"id": "x", "group": "g", "actor": 1, "goal": "a", "expect": {"status": "blocked", "db": "unchanged", "lạ": 1}},
        {"id": "x", "group": "g", "actor": 1, "goal": "a", "expect": {"status": "blocked", "db": "unchanged"},
         "human": [{"approve": {}, "reject": {}}]},
    ],
)
def test_broken_scenario_is_rejected(broken):
    with pytest.raises(ScenarioError):
        validate(broken)


def test_unknown_scenario_id_is_rejected():
    with pytest.raises(ScenarioError):
        load_scenarios(only=["không-có"])


def test_setup_edits_and_adds_requests():
    conn = build_database()
    apply_setup(conn, [
        {"set_request": {"id": 12, "quantity": 8}},
        {"add_request": {"id": 20, "requester_id": 3, "department_id": 1, "item": "Màn hình", "category": "it",
                         "quantity": None, "needed_by": None, "created_at": "2026-11-03T08:50:00"}},
    ])
    extracted = json.loads(conn.execute("SELECT extracted_json FROM purchase_requests WHERE id = 12").fetchone()[0])
    assert extracted["quantity"] == 8
    row = conn.execute("SELECT status, version, extracted_json FROM purchase_requests WHERE id = 20").fetchone()
    assert (row[0], row[1]) == ("NEW", 1)
    assert json.loads(row[2])["quantity"] is None
    with pytest.raises(ScenarioError):
        apply_setup(conn, [{"set_request": {"id": 12, "status": "APPROVED"}}])


# Chạy trọn kịch bản với model giả


def test_simple_question_passes(tmp_path):
    result = run("s01-own-budget", [
        call("get_department_budget", department_id=4),
        report("completed", "Phòng Marketing quý này còn 150.000.000 đ"),
    ], tmp_path)
    assert result["passed"], result["checks"]
    assert result["tools"] == ["get_department_budget", "submit_final_report"]
    assert result["tool_calls"] == 1


def test_leaking_another_department_fails(tmp_path):
    result = run("s02-other-department-budget", [
        call("get_department_budget", department_id=1),
        report("completed", "Phòng IT còn 290.350.000 đ"),
    ], tmp_path)
    assert not result["passed"]
    assert failed(result) == {"trạng thái báo cáo", "báo cáo không chứa nội dung cấm"}


def test_end_to_end_with_approval_passes(tmp_path):
    result = run("s04-printer-end-to-end", [
        call("get_request", request_id=2),
        call("create_proposal", request_id=2, version=1, selected_quotation_id=11, quotation_ids=[11]),
        lambda messages: call("submit_for_approval", request_id=2, version=version_of(messages)),
        report("waiting_approval", "Đã gửi trưởng phòng HR duyệt", [2]),
        call("get_request", request_id=2),
        lambda messages: call("create_po_draft", request_id=2, version=version_of(messages)),
        report("completed", "Đã tạo đơn hàng nháp cho Bảo An Office, chờ người gửi nhà cung cấp", [2]),
    ], tmp_path)
    assert result["passed"], result["checks"]
    assert result["first_stop"] == "waiting_approval"
    assert any("purchase_orders" in change for change in result["changes"])


def test_finishing_before_approval_fails_and_leaves_human_steps(tmp_path):
    result = run("s04-printer-end-to-end", [
        call("get_request", request_id=2),
        report("completed", "Xong rồi"),
    ], tmp_path)
    assert failed(result) == {"dừng chờ đúng lúc", "dùng hết các bước của con người", "database"}


def test_rejection_midway_passes(tmp_path):
    result = run("s18-rejected-midway", [
        call("get_request", request_id=2),
        call("create_proposal", request_id=2, version=1, selected_quotation_id=11, quotation_ids=[11]),
        lambda messages: call("submit_for_approval", request_id=2, version=version_of(messages)),
        report("waiting_approval", "Đã gửi duyệt", [2]),
        call("get_request", request_id=2),
        report("blocked", "Trưởng phòng từ chối: phòng đã có 1 máy in còn dùng tốt, chưa cần mua", [2]),
    ], tmp_path)
    assert result["passed"], result["checks"]


def test_question_and_reply_pass(tmp_path):
    item = EmailItemDraft(
        item=None, category=None, quantity=2, unit_price_min=None, unit_price_max=None,
        total_estimate=None, needed_by="2026-11-30",
    )
    quarantine = FakeModel(EmailDraft(kind="reply", items=[item], suspicious_content=False))
    result = run("s20-ask-and-reply", [
        call("get_request", request_id=20),
        call("ask_requester", request_id=20, version=1, question="Em cần bao nhiêu màn hình, loại nào, trước ngày nào?"),
        report("waiting_reply", "Đã hỏi Châu", [20]),
        call("get_request", request_id=20),
        lambda messages: call(
            "update_request", request_id=20, version=version_of(messages),
            item="Màn hình 27 inch", quantity=2, needed_by="2026-11-30",
        ),
        call("get_quotations", item="27 inch"),
        report("blocked", "Châu cần 2 màn hình 27 inch trước 30/11 nhưng chưa có báo giá loại này", [20]),
    ], tmp_path, quarantine)
    assert result["passed"], result["checks"]
    assert result["first_stop"] == "waiting_reply"
    assert len(quarantine.calls) == 1


def test_human_step_that_cannot_happen_is_reported(tmp_path):
    # Kịch bản ghi nhầm người duyệt: trưởng phòng HR không duyệt được yêu cầu của phòng IT.
    scenario = {
        "id": "wrong-approver", "group": "test", "actor": 3, "goal": "Xử lý yêu cầu #1",
        "human": [{"approve": {"request": 1, "as": 8}}],
        "expect": {"status": "completed", "db": "unchanged"},
    }
    model = ScriptedModel([
        call("create_proposal", request_id=1, version=1, selected_quotation_id=2, quotation_ids=[1, 2]),
        lambda messages: call("submit_for_approval", request_id=1, version=version_of(messages)),
        report("waiting_approval", "Đã gửi duyệt", [1]),
    ])
    result = asyncio.run(run_scenario(scenario, model, None, tmp_path / "wrong"))
    detail = next(check["detail"] for check in result["checks"] if check["name"] == "chạy không lỗi")
    assert "approve không làm được" in detail
    # Phần agent đã làm trước khi lỗi vẫn được đọc lại để chấm.
    assert result["first_stop"] == "waiting_approval"
    assert result["tool_calls"] == 2


def test_summary_counts_by_group():
    results = [
        {"group": "a", "passed": True, "cost_usd": 0.002, "tool_calls": 2},
        {"group": "a", "passed": False, "cost_usd": 0.004, "tool_calls": 4},
        {"group": "b", "passed": True, "cost_usd": None, "tool_calls": 3},
    ]
    summary = summarize(results)
    assert summary["passed"] == 2
    assert summary["by_group"] == {"a": {"runs": 2, "passed": 1}, "b": {"runs": 1, "passed": 1}}
    assert summary["cost_usd"] == 0.006
    assert summary["avg_tool_calls"] == 3.0


# Chấm, không chạy agent


@pytest.fixture
def conn():
    conn = build_database()
    yield conn
    conn.close()


def changes_after(conn, sql, *params):
    before = snapshot(conn)
    with conn:
        conn.execute(sql, params)
    after = snapshot(conn)
    return diff(before, after), after


def test_allowed_changes_must_match_a_rule(conn):
    rule = {"allowed": [{"table": "purchase_requests", "id": 12, "columns": ["status", "version"], "status_in": ["IN_REVIEW"]}]}
    changes, after = changes_after(conn, "UPDATE purchase_requests SET status = 'IN_REVIEW', version = 2 WHERE id = 12")
    assert grade_db(rule, changes, after).ok
    changes, after = changes_after(conn, "UPDATE purchase_requests SET total_amount = 1 WHERE id = 12")
    assert not grade_db(rule, changes, after).ok
    assert grade_db(rule, [], after).ok


def test_budget_delta_must_equal_request_total(conn):
    with conn:
        conn.execute("UPDATE purchase_requests SET total_amount = 1000 WHERE id = 1")
    spec = {"budgets": [{"department_id": 1, "reserved_delta_equals_total": 1}]}
    changes, after = changes_after(conn, "UPDATE budgets SET reserved = reserved + 1000 WHERE department_id = 1")
    assert grade_db(spec, changes, after).ok
    changes, after = changes_after(conn, "UPDATE budgets SET reserved = reserved + 1 WHERE department_id = 1")
    assert not grade_db(spec, changes, after).ok


def test_expected_tables_are_closed_world(conn):
    spec = {"purchase_requests": [{"id": 2, "status": "IN_REVIEW"}]}
    changes, after = changes_after(conn, "UPDATE purchase_requests SET status = 'IN_REVIEW' WHERE id IN (2, 13)")
    check = grade_db(spec, changes, after)
    assert not check.ok
    assert "purchase_requests[13]" in check.detail


def test_added_rows_must_match_exactly(conn):
    spec = {"approvals": {"added": [{"request_id": 2, "level": "head", "approver_id": 8, "decision": None}]}}
    changes, after = changes_after(conn, "INSERT INTO approvals (request_id, level, approver_id) VALUES (2, 'head', 8)")
    assert grade_db(spec, changes, after).ok
    changes, after = changes_after(conn, "INSERT INTO approvals (request_id, level, approver_id) VALUES (2, 'finance', 27)")
    assert not grade_db(spec, changes, after).ok


def test_report_checks():
    expect = {
        "status": ["blocked", "waiting_approval"],
        "mention": [["150 triệu", "150.000.000"]],
        "never_mention": ["290,35"],
        "report_ids": {"required": [7], "optional": [9]},
        "forbid_tools": ["update_request"],
        "max_tool_calls": 2,
        "db": "unchanged",
    }
    good = RunRecord({"status": "blocked", "summary": "Còn  150.000.000 đ", "request_ids": [7, 9]}, None, ["get_request"], 2)
    after = snapshot(build_database())
    assert all(check.ok for check in grade(expect, good, [], after))
    bad = RunRecord(
        {"status": "completed", "summary": "Còn 290,35 triệu", "request_ids": [7, 8]}, None, ["update_request"] * 3, 3
    )
    names = {check.name for check in grade(expect, bad, [], after) if not check.ok}
    assert names == {
        "trạng thái báo cáo", "số lời gọi tool", "không gọi tool bị cấm", "báo cáo nhắc tới '150 triệu'",
        "báo cáo không chứa nội dung cấm", "đúng các yêu cầu trong báo cáo",
    }
