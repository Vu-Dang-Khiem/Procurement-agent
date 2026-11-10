"""Nối 3 MCP server với agent qua `langchain-mcp-adapters`.

Mỗi server giữ một phiên kết nối mở suốt nhiệm vụ. Nếu dùng `get_tools()` mặc định, mỗi lần gọi tool
sẽ khởi động một tiến trình server mới: chậm hơn, và docs-mcp mất kết quả đọc PDF đã lưu.
"""

import os
import sys
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

from mcp_servers.common import DB_ENV

ROOT = Path(__file__).resolve().parent.parent
SERVERS = ("docs", "erp", "mail")


def _connections(db_path: str | Path) -> dict:
    env = {**os.environ, DB_ENV: str(db_path)}
    return {
        name: {
            "transport": "stdio",
            "command": sys.executable,
            "args": ["-m", f"mcp_servers.{name}.server"],
            "env": env,
            "cwd": str(ROOT),
        }
        for name in SERVERS
    }


@asynccontextmanager
async def open_mcp_tools(db_path: str | Path) -> AsyncIterator[dict[str, BaseTool]]:
    """Mở phiên với cả 3 server và trả về các tool theo tên, sắp xếp theo tên. Đóng phiên khi ra khỏi khối."""
    client = MultiServerMCPClient(_connections(db_path))
    async with AsyncExitStack() as stack:
        tools: dict[str, BaseTool] = {}
        for name in SERVERS:
            session = await stack.enter_async_context(client.session(name))
            for tool in await load_mcp_tools(session, server_name=name):
                if tool.name in tools:
                    raise ValueError(f"Hai server cùng có tool {tool.name}")
                tools[tool.name] = tool
        yield dict(sorted(tools.items()))
