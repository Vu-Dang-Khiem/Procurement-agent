"""Dòng lệnh để chạy agent, duyệt, trả lời và xem trace. Lệnh `run` và `resume` gọi model thật, có TỐN TIỀN API.

    python -m agent.cli init
    python -m agent.cli run "Xử lý yêu cầu mua 10 màn hình của phòng IT" --actor 3 --task demo-1
    python -m agent.cli status --task demo-1
    python -m agent.cli approve --request 1 --as 1
    python -m agent.cli reject --request 1 --as 1 --reason "Giảm số lượng"
    python -m agent.cli reply --question 2 --as 20 --text "Cần 8 cái, trước 15/11"
    python -m agent.cli resume --task demo-1
    python -m agent.cli trace --task demo-1
"""

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.model import estimate_cost, make_main_model
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
from api.human import answer_question, decide_approval, describe_pending, unresolved
from quarantine.model import ClaudeModel, model_id_for
from rules.errors import RuleError
from simulator.db import connect
from simulator.seed import build_database

DEFAULT_DB = RUNS_DIR / "company.db"
DEFAULT_CHECKPOINTS = RUNS_DIR / "checkpoints.db"


def _short(text: str, limit: int = 300) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(block.get("text", "") for block in content if isinstance(block, dict))


def print_outcome(state: dict, waiting: bool | None = None) -> None:
    report = state.get("final_report")
    # Kết quả của run và resume có khóa __interrupt__; trạng thái đọc lại từ checkpoint thì không, nên phải truyền `waiting`.
    if waiting if waiting is not None else bool(state.get("__interrupt__")):
        print(f"ĐANG CHỜ NGƯỜI: {report['status']} - {report['summary']}")
        for id_ in state["pending_approvals"]:
            print(f"  yêu cầu #{id_} chờ duyệt")
        for id_ in state["pending_replies"]:
            print(f"  câu hỏi #{id_} chờ trả lời")
    elif report is not None:
        print(f"KẾT THÚC: {report['status']} - {report['summary']}")
    elif state.get("stop_reason"):
        print(f"DỪNG: {state['stop_reason']}")
    else:
        print("DỪNG: agent không nộp báo cáo")
    print(f"Số lời gọi tool: {state['step_count']}")


def print_trace(state: dict) -> None:
    input_tokens = output_tokens = 0
    for message in state["messages"]:
        if isinstance(message, AIMessage):
            usage = message.usage_metadata or {}
            input_tokens += usage.get("input_tokens", 0)
            output_tokens += usage.get("output_tokens", 0)
            text = _content_text(message.content).strip()
            if text:
                print(f"  agent: {_short(text)}")
            for call in message.tool_calls:
                print(f"→ {call['name']} {json.dumps(call['args'], ensure_ascii=False)}")
        elif isinstance(message, ToolMessage):
            print(f"  ← {_short(_content_text(message.content))}")
        elif isinstance(message, HumanMessage):
            print(f"• {_short(_content_text(message.content))}")
    model_id = model_id_for("main_agent")
    cost = estimate_cost(model_id, input_tokens, output_tokens)
    print(f"Token: {input_tokens} vào, {output_tokens} ra" + (f", ước tính {cost:.4f} USD ({model_id})" if cost else ""))


def _require_key() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Chưa có ANTHROPIC_API_KEY. Hãy chép .env.example thành .env rồi điền key.")


async def _with_agent(args, action):
    async with open_agent(make_main_model(), args.db, args.checkpoints) as graph:
        return await action(graph)


async def _read(args, task_id: str) -> tuple[dict, bool]:
    async with open_reader(args.checkpoints) as graph:
        return await task_values(graph, task_id), await is_waiting(graph, task_id)


def cmd_init(args) -> None:
    db = Path(args.db)
    if db.exists() and not args.reset:
        sys.exit(f"{db} đã có. Thêm --reset để tạo lại từ đầu (mất dữ liệu cũ, kể cả tiến độ các nhiệm vụ).")
    db.parent.mkdir(parents=True, exist_ok=True)
    for path in (db, Path(args.checkpoints)):
        path.unlink(missing_ok=True)
    build_database(db).close()
    print(f"Đã tạo công ty giả lập ở {db}")


def cmd_run(args) -> None:
    _require_key()
    task_id = args.task or f"task-{uuid.uuid4().hex[:8]}"
    conn = connect(args.db)
    name, role = describe_actor(conn, args.actor)
    conn.close()
    print(f"Nhiệm vụ {task_id}, giao bởi {name} ({role})")
    state = asyncio.run(_with_agent(args, lambda graph: start_task(graph, task_id, args.actor, name, role, args.goal)))
    print_outcome(state)


def cmd_status(args) -> None:
    state, paused = asyncio.run(_read(args, args.task))
    print_outcome(state, paused)
    if not paused:
        return
    conn = connect(args.db)
    waiting = unresolved(conn, state["pending_approvals"], state["pending_replies"])
    for request_id in state["pending_approvals"]:
        print(json.dumps(describe_pending(conn, request_id), ensure_ascii=False))
    conn.close()
    print("Còn chờ: " + ("; ".join(waiting) if waiting else "không, có thể resume"))


def cmd_decide(args, decision: str) -> None:
    conn = connect(args.db)
    try:
        result = decide_approval(conn, args.request, args.actor, decision, getattr(args, "reason", None))
        print(json.dumps(result, ensure_ascii=False))
    finally:
        conn.close()


def cmd_reply(args) -> None:
    _require_key()
    conn = connect(args.db)
    try:
        result = answer_question(conn, ClaudeModel(model_id_for("quarantine")), args.question, args.actor, args.text)
        print(json.dumps(result, ensure_ascii=False))
    finally:
        conn.close()


def cmd_resume(args) -> None:
    _require_key()

    async def action(graph):
        state = await task_values(graph, args.task)
        conn = connect(args.db)
        waiting = unresolved(conn, state["pending_approvals"], state["pending_replies"])
        conn.close()
        if waiting:
            sys.exit("Chưa thể tiếp tục, còn chờ: " + "; ".join(waiting))
        return await resume_task(graph, args.task)

    print_outcome(asyncio.run(_with_agent(args, action)))


def cmd_trace(args) -> None:
    print_trace(asyncio.run(_read(args, args.task))[0])


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--checkpoints", default=str(DEFAULT_CHECKPOINTS))
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="Tạo công ty giả lập mới")
    init.add_argument("--reset", action="store_true")
    run = commands.add_parser("run", help="Giao mục tiêu cho agent (tốn tiền API)")
    run.add_argument("goal")
    run.add_argument("--actor", type=int, required=True, help="id nhân viên giao việc")
    run.add_argument("--task")
    for name in ("status", "resume", "trace"):
        commands.add_parser(name).add_argument("--task", required=True)
    approve = commands.add_parser("approve", help="Duyệt lượt hiện tại của một yêu cầu")
    reject = commands.add_parser("reject", help="Từ chối một yêu cầu")
    for command in (approve, reject):
        command.add_argument("--request", type=int, required=True)
        command.add_argument("--as", dest="actor", type=int, required=True, help="id người duyệt")
    reject.add_argument("--reason", required=True)
    reply = commands.add_parser("reply", help="Người yêu cầu trả lời câu hỏi (tốn tiền API cho LLM cách ly)")
    reply.add_argument("--question", type=int, required=True)
    reply.add_argument("--as", dest="actor", type=int, required=True, help="id người yêu cầu")
    reply.add_argument("--text", required=True)

    args = parser.parse_args()
    handlers = {
        "init": cmd_init,
        "run": cmd_run,
        "status": cmd_status,
        "approve": lambda a: cmd_decide(a, "approved"),
        "reject": lambda a: cmd_decide(a, "rejected"),
        "reply": cmd_reply,
        "resume": cmd_resume,
        "trace": cmd_trace,
    }
    try:
        handlers[args.command](args)
    except (RuleError, ValueError) as error:
        sys.exit(f"Lỗi: {getattr(error, 'message', error)}" + (f" ({error.hint})" if getattr(error, "hint", None) else ""))


if __name__ == "__main__":
    main()
