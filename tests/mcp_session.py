"""Chạy một MCP server ở tiến trình riêng và gọi qua giao thức MCP thật, không cần agent."""

import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mcp_servers.common import DB_ENV

ROOT = Path(__file__).resolve().parent.parent


def run_session(module, db_path, scenario, extra_env=None):
    async def main():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", module],
            env={**os.environ, DB_ENV: str(db_path), **(extra_env or {})},
            cwd=ROOT,
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            return await scenario(session)

    return asyncio.run(main())
