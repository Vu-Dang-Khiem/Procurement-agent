"""Chạy và tiếp tục một nhiệm vụ. Tiến độ lưu trong file checkpoint, tách khỏi database của công ty giả lập."""

import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from agent.graph import build_graph, run_config
from agent.mcp_tools import ROOT, open_mcp_tools
from agent.prompts import goal_message
from agent.state import initial_state
from rules.errors import NotFound

RUNS_DIR = ROOT / "runs"
ROLE_LABELS = {"staff": "nhân viên", "head": "trưởng phòng", "finance": "nhân viên Finance", "director": "giám đốc"}


@asynccontextmanager
async def open_agent(model, db_path: str | Path, checkpoint_path: str | Path) -> AsyncIterator:
    Path(checkpoint_path).parent.mkdir(parents=True, exist_ok=True)
    async with open_mcp_tools(db_path) as tools, AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
        yield build_graph(model, tools, saver)


class _NoModel:
    """Chỉ để dựng đồ thị khi đọc tiến độ. Không bao giờ được gọi."""

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        raise RuntimeError("Đồ thị này chỉ dùng để đọc tiến độ")


@asynccontextmanager
async def open_reader(checkpoint_path: str | Path) -> AsyncIterator:
    """Đọc tiến độ mà không khởi động MCP server hay cần API key."""
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
        yield build_graph(_NoModel(), {}, saver)


def describe_actor(conn: sqlite3.Connection, actor_id: int) -> tuple[str, str]:
    row = conn.execute(
        "SELECT e.name, e.role, d.name AS department FROM employees e"
        " LEFT JOIN departments d ON d.id = e.department_id WHERE e.id = ?",
        (actor_id,),
    ).fetchone()
    if row is None:
        raise NotFound(f"Không có nhân viên #{actor_id}")
    role = ROLE_LABELS[row["role"]] + (f" {row['department']}" if row["department"] and row["role"] != "finance" else "")
    return row["name"], role


async def start_task(graph, task_id: str, actor_id: int, actor_name: str, actor_role: str, goal: str) -> dict:
    snapshot = await graph.aget_state(run_config(task_id))
    if snapshot.values:
        raise ValueError(f"Nhiệm vụ {task_id} đã tồn tại, hãy dùng mã khác hoặc tiếp tục nó")
    start = initial_state(actor_id, task_id, goal_message(goal, actor_name, actor_role))
    return await graph.ainvoke(start, run_config(task_id))


async def resume_task(graph, task_id: str) -> dict:
    if not await is_waiting(graph, task_id):
        raise ValueError(f"Nhiệm vụ {task_id} không ở trạng thái chờ")
    return await graph.ainvoke(Command(resume={"resumed": True}), run_config(task_id))


async def is_waiting(graph, task_id: str) -> bool:
    snapshot = await graph.aget_state(run_config(task_id))
    return "wait_human" in snapshot.next


async def task_values(graph, task_id: str) -> dict:
    snapshot = await graph.aget_state(run_config(task_id))
    if not snapshot.values:
        raise NotFound(f"Không có nhiệm vụ {task_id}")
    return snapshot.values
