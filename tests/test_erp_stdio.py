"""Chạy erp-mcp ở tiến trình riêng và gọi qua giao thức MCP thật, không cần agent."""

import json

import pytest
from mcp_session import run_session

from simulator.seed import build_database

MODULE = "mcp_servers.erp.server"
TOOLS = {
    "check_policy",
    "create_po_draft",
    "create_proposal",
    "get_department_budget",
    "get_quotations",
    "get_request",
    "list_requests",
    "submit_for_approval",
    "update_request",
}


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "sim.db"
    build_database(path).close()
    return path


def test_lists_all_tools_with_descriptions(db_path):
    async def scenario(session):
        return (await session.list_tools()).tools

    tools = run_session(MODULE, db_path, scenario)
    assert {tool.name for tool in tools} == TOOLS
    for tool in tools:
        assert tool.description
        assert "wrapper" not in tool.description
        assert tool.inputSchema["properties"]


def test_full_flow_over_mcp(db_path):
    async def scenario(session):
        async def call(name, **arguments):
            result = await session.call_tool(name, arguments)
            assert result.isError is False
            assert len(result.content) == 1
            return result.content[0].text

        request = await call("get_request", actor_id=3, request_id=1)
        proposal = await call("create_proposal", actor_id=3, request_id=1, version=1, selected_quotation_id=2, quotation_ids=[2, 1])
        version = json.loads(proposal)["data"]["version"]
        submitted = await call("submit_for_approval", actor_id=3, request_id=1, version=version, task_id="t1")
        return request, submitted

    request, submitted = run_session(MODULE, db_path, scenario)
    # JSON gọn, sắp xếp khóa và không escape tiếng Việt, để đỡ tốn token và giữ được cache.
    assert request.startswith('{"data":{')
    assert "Màn hình 24 inch" in request
    assert ": " not in request.split('"item"')[0]
    assert json.loads(submitted)["data"]["status"] == "PENDING_APPROVAL"


def test_rule_error_comes_back_as_data_not_crash(db_path):
    async def scenario(session):
        return await session.call_tool("get_request", {"actor_id": 9, "request_id": 1})

    result = run_session(MODULE, db_path, scenario)
    assert result.isError is False
    assert json.loads(result.content[0].text)["error_type"] == "permission"


def test_wrong_argument_type_is_reported_by_mcp(db_path):
    async def scenario(session):
        return await session.call_tool("get_request", {"actor_id": "ai đó", "request_id": 1})

    assert run_session(MODULE, db_path, scenario).isError is True
