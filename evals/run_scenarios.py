"""Chạy agent trên bộ kịch bản rồi chấm. Chạy bằng model thật thì TỐN TIỀN API.

    python -m evals.run_scenarios                  # chỉ in ước tính chi phí, không chạy
    python -m evals.run_scenarios --yes            # chạy cả bộ
    python -m evals.run_scenarios --only s01-own-budget,s04-printer-end-to-end --yes
    python -m evals.run_scenarios --repeat 3 --yes

Mỗi kịch bản chạy trên một database mới tạo từ seed, nằm ở runs/evals/<lần chạy>/<kịch bản>/.
Xem lại agent đã làm gì:
    python -m agent.cli --checkpoints runs/evals/<lần chạy>/<kịch bản>/checkpoints.db trace --task <kịch bản>
Kết quả chấm được ghi vào evals/reports/.
"""

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import AIMessage

from agent.model import cache_counts, estimate_cost, make_main_model
from agent.runner import (
    RUNS_DIR,
    describe_actor,
    is_waiting,
    open_agent,
    open_reader,
    resume_task,
    start_task,
    task_values,
)
from api.human import answer_question, decide_approval, unresolved
from evals.grader import RunRecord, grade
from evals.scenarios import ScenarioError, apply_setup, human_action, load_scenarios
from evals.state_diff import diff, snapshot
from quarantine.model import ClaudeModel, model_id_for
from rules.errors import RuleError
from simulator.db import connect
from simulator.seed import build_database

REPORT_DIR = Path(__file__).with_name("reports")
# Ước tính thô cho một kịch bản trên Haiku 5.5 khi đã bật cache, dựa trên các lần chạy thật ở tuần 3 và 4.
ESTIMATED_USD_PER_SCENARIO = 0.005


class HumanStepError(Exception):
    """Bước của con người không làm được, thường vì agent không đưa yêu cầu tới đúng chỗ cần duyệt hay trả lời."""


def _apply_human(conn, action: dict, quarantine_model) -> None:
    kind, args = human_action(action)
    try:
        if kind in ("approve", "reject"):
            decision = "approved" if kind == "approve" else "rejected"
            decide_approval(conn, args["request"], args["as"], decision, args.get("reason"))
        else:
            row = conn.execute(
                "SELECT id FROM clarifications WHERE request_id = ? AND status = 'pending' ORDER BY id DESC LIMIT 1",
                (args["request"],),
            ).fetchone()
            if row is None:
                raise HumanStepError(f"Yêu cầu #{args['request']} không có câu hỏi nào đang chờ trả lời")
            answer_question(conn, quarantine_model, row[0], args["as"], args["text"])
    except RuleError as error:
        raise HumanStepError(f"{kind} không làm được: {error.message}") from error


def _usage(messages) -> dict:
    totals = {"model_calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_read": 0, "cache_write": 0}
    for message in messages:
        if isinstance(message, AIMessage):
            usage = message.usage_metadata or {}
            read, write = cache_counts(usage)
            totals["model_calls"] += 1
            totals["input_tokens"] += usage.get("input_tokens", 0)
            totals["output_tokens"] += usage.get("output_tokens", 0)
            totals["cache_read"] += read
            totals["cache_write"] += write
    return totals


def _tool_names(messages) -> list[str]:
    return [call["name"] for message in messages if isinstance(message, AIMessage) for call in message.tool_calls]


async def run_scenario(scenario: dict, model, quarantine_model, workdir: Path, model_id: str | None = None) -> dict:
    """Chạy một kịch bản từ database mới, làm các bước của con người, rồi chấm."""
    started = time.monotonic()
    workdir.mkdir(parents=True, exist_ok=True)
    db, checkpoints = workdir / "company.db", workdir / "checkpoints.db"
    for path in (db, checkpoints):
        path.unlink(missing_ok=True)
    task = scenario["id"]

    conn = build_database(db)
    apply_setup(conn, scenario.get("setup", []))
    before = snapshot(conn)
    name, role = describe_actor(conn, scenario["actor"])
    conn.close()

    human = list(scenario.get("human", []))
    first_stop, error, values = None, None, {}
    try:
        async with open_agent(model, db, checkpoints) as graph:
            await start_task(graph, task, scenario["actor"], name, role, scenario["goal"])
            # Mỗi lần dừng chờ dùng tối đa vài bước của con người; giới hạn để agent không thể lặp mãi.
            for _ in range(len(human) + 3):
                if not await is_waiting(graph, task):
                    break
                state = await task_values(graph, task)
                first_stop = first_stop or state["final_report"]["status"]
                finished = False
                while True:
                    conn = connect(db)
                    try:
                        if not unresolved(conn, state["pending_approvals"], state["pending_replies"]):
                            break
                        if not human:
                            finished = True  # còn chờ nhưng kịch bản hết việc cho con người
                            break
                        _apply_human(conn, human.pop(0), quarantine_model)
                    finally:
                        conn.close()
                if finished:
                    break
                await resume_task(graph, task)
            values = await task_values(graph, task)
    except (HumanStepError, ScenarioError) as cause:
        error = str(cause)
    except Exception as cause:  # lỗi bất ngờ vẫn được ghi để cả bộ chạy tiếp
        error = f"{type(cause).__name__}: {cause}"
    if not values:
        # Đọc lại tiến độ đã lưu để vẫn chấm được phần agent đã làm trước khi lỗi.
        try:
            async with open_reader(checkpoints) as graph:
                values = await task_values(graph, task)
        except Exception:
            values = {}

    messages = values.get("messages", [])
    run = RunRecord(
        report=values.get("final_report"),
        first_stop=first_stop,
        tool_names=_tool_names(messages),
        step_count=values.get("step_count", 0),
        stop_reason=values.get("stop_reason"),
        human_left=[action for action in human if not action.get("optional")],
        error=error,
    )
    conn = connect(db)
    after = snapshot(conn)
    conn.close()
    changes = diff(before, after)
    checks = grade(scenario["expect"], run, changes, after)

    usage = _usage(messages)
    cost = estimate_cost(model_id, usage["input_tokens"], usage["output_tokens"], usage["cache_read"], usage["cache_write"]) if model_id else None
    return {
        "id": scenario["id"],
        "group": scenario["group"],
        "passed": all(check.ok for check in checks),
        "checks": [vars(check) for check in checks],
        "report": run.report,
        "first_stop": first_stop,
        "tools": run.tool_names,
        "tool_calls": run.step_count,
        "changes": [str(change) for change in changes],
        "usage": usage,
        "cost_usd": round(cost, 5) if cost is not None else None,
        "seconds": round(time.monotonic() - started, 1),
        "workdir": str(workdir),
    }


def _print_result(result: dict, repeat: int | None = None) -> None:
    label = "ĐẠT " if result["passed"] else "TRƯỢT"
    cost = f"{result['cost_usd']:.4f} USD" if result["cost_usd"] is not None else "-"
    suffix = f" lần {repeat}" if repeat else ""
    print(f"{label} {result['id']}{suffix}: {result['tool_calls']} tool, {cost}, {result['seconds']} giây", flush=True)
    for check in result["checks"]:
        if not check["ok"]:
            print(f"      ✗ {check['name']}: {check['detail']}", flush=True)


def summarize(results: list[dict]) -> dict:
    by_group: dict[str, Counter] = {}
    for result in results:
        counter = by_group.setdefault(result["group"], Counter())
        counter["runs"] += 1
        counter["passed"] += result["passed"]
    costs = [result["cost_usd"] for result in results if result["cost_usd"] is not None]
    return {
        "runs": len(results),
        "passed": sum(result["passed"] for result in results),
        "by_group": {group: dict(counter) for group, counter in sorted(by_group.items())},
        "cost_usd": round(sum(costs), 4) if costs else None,
        "avg_cost_usd": round(sum(costs) / len(costs), 5) if costs else None,
        "avg_tool_calls": round(sum(result["tool_calls"] for result in results) / len(results), 1) if results else 0,
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="Danh sách id kịch bản, cách nhau bằng dấu phẩy")
    parser.add_argument("--repeat", type=int, default=1, help="Chạy mỗi kịch bản bao nhiêu lần")
    parser.add_argument("--max-usd", type=float, default=0.5, help="Dừng khi tổng chi phí chạm mức này")
    parser.add_argument("--yes", action="store_true", help="Đồng ý chạy bằng model thật (tốn tiền API)")
    args = parser.parse_args()

    scenarios = load_scenarios(only=args.only.split(",") if args.only else None)
    runs = len(scenarios) * args.repeat
    model_id = model_id_for("main_agent")
    print(f"{len(scenarios)} kịch bản × {args.repeat} lần = {runs} lần chạy bằng {model_id}.")
    print(f"Ước tính khoảng {runs * ESTIMATED_USD_PER_SCENARIO:.2f} USD, dừng nếu chạm {args.max_usd} USD.")
    print("Chưa tính tiền LLM cách ly khi đọc PDF và câu trả lời, phần này nhỏ (khoảng 0,0005 USD mỗi lần).")
    if not args.yes:
        print("Thêm --yes để chạy.")
        return

    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Chưa có ANTHROPIC_API_KEY. Hãy chép .env.example thành .env rồi điền key.")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = RUNS_DIR / "evals" / stamp
    model = make_main_model()
    quarantine_model = ClaudeModel(model_id_for("quarantine"))
    results, spent = [], 0.0
    for repeat in range(1, args.repeat + 1):
        for scenario in scenarios:
            if spent >= args.max_usd:
                print(f"Dừng vì đã chạm mức {args.max_usd} USD")
                break
            workdir = run_dir / (scenario["id"] + (f"-{repeat}" if args.repeat > 1 else ""))
            result = asyncio.run(run_scenario(scenario, model, quarantine_model, workdir, model_id))
            result["repeat"] = repeat
            results.append(result)
            spent += result["cost_usd"] or 0
            _print_result(result, repeat if args.repeat > 1 else None)

    summary = summarize(results)
    print(f"\nĐạt {summary['passed']}/{summary['runs']}. Tổng {summary['cost_usd']} USD, trung bình {summary['avg_cost_usd']} USD mỗi lần.")
    for group, counter in summary["by_group"].items():
        print(f"  {group}: {counter['passed']}/{counter['runs']}")
    q = quarantine_model.usage
    REPORT_DIR.mkdir(exist_ok=True)
    report = REPORT_DIR / f"scenarios_{model_id}_{stamp}.json"
    report.write_text(
        json.dumps(
            {
                "model": model_id,
                "started": stamp,
                "summary": summary,
                "quarantine_usage_for_replies": vars(q),
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"Đã ghi {report}")


if __name__ == "__main__":
    main()
