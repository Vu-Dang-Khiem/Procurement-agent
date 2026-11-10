"""Test docs-mcp bằng model giả, không gọi API nên không tốn tiền."""

import json

import pytest
from mcp_session import run_session
from quarantine_fakes import FakeModel, draft_from_answer, quote_draft

from evals.extraction_answers import QUOTE_ANSWERS
from evals.state_diff import diff, snapshot
from mcp_servers.common import DB_ENV, dumps
from mcp_servers.docs import server
from rules.errors import InvalidInput, Transient
from simulator.db import connect
from simulator.quotes import INJECTIONS
from simulator.seed import build_database

STAFF = 3
TASK = "task-1"
OUTPUT_KEYS = {
    "quotation_id",
    "supplier",
    "item",
    "unit_price",
    "vat_rate",
    "unit_price_with_vat",
    "delivery_days",
    "warranty_months",
    "valid_until",
    "suspicious_content",
    "hidden_text_found",
    "mismatches",
    "unknown_fields",
}


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "sim.db"
    build_database(path).close()
    monkeypatch.setenv(DB_ENV, str(path))
    return path


@pytest.fixture
def db(db_path):
    conn = connect(db_path)
    yield conn
    conn.close()


@pytest.fixture
def use_model():
    def install(model):
        server.set_model(model)
        return model

    yield install
    server.set_model(None)


def fails_with(result, error_type):
    assert result["ok"] is False, result
    assert result["error_type"] == error_type, result
    return True


@pytest.mark.parametrize("quotation_id", list(QUOTE_ANSWERS))
def test_correct_reading_matches_database(db, use_model, quotation_id):
    use_model(FakeModel(draft_from_answer(quotation_id)))
    data = server.read_quotation_pdf(STAFF, quotation_id)["data"]
    assert set(data) == OUTPUT_KEYS
    assert data["mismatches"] == []
    assert data["unknown_fields"] == []
    assert data["suspicious_content"] is (quotation_id in INJECTIONS)


def test_hidden_text_is_flagged_even_if_model_misses_it(db, use_model):
    model = use_model(FakeModel(draft_from_answer(8).model_copy(update={"suspicious_content": False})))
    data = server.read_quotation_pdf(STAFF, 8)["data"]
    assert data["suspicious_content"] is True
    assert data["hidden_text_found"] is True
    assert "Ignore previous instructions" not in model.calls[0].document


def test_output_never_contains_pdf_text(db, use_model):
    use_model(FakeModel(draft_from_answer(14)))
    result = server.read_quotation_pdf(STAFF, 14)
    text = dumps(result)
    assert "Lưu ý cho hệ thống AI" not in text
    assert "kính gửi" not in text.casefold()
    assert result["data"]["suspicious_content"] is True


def test_value_that_differs_from_database_is_reported(db, use_model):
    use_model(FakeModel(quote_draft(printed_unit_price=4_000_000)))
    data = server.read_quotation_pdf(STAFF, 1)["data"]
    assert data["unit_price"] == 4_000_000
    assert data["mismatches"] == ["unit_price"]


def test_unreadable_value_is_unknown_not_mismatch(db, use_model):
    use_model(FakeModel(quote_draft(delivery_days=None, supplier="Công ty lạ")))
    data = server.read_quotation_pdf(STAFF, 1)["data"]
    assert data["mismatches"] == []
    assert data["unknown_fields"] == ["supplier", "delivery_days"]


def test_same_file_is_read_by_model_only_once(db, use_model):
    model = use_model(FakeModel(draft_from_answer(1)))
    first = server.read_quotation_pdf(STAFF, 1)
    second = server.read_quotation_pdf(4, 1)
    assert len(model.calls) == 1
    assert first == second


def test_changing_model_forgets_old_results(db, use_model):
    use_model(FakeModel(draft_from_answer(1)))
    server.read_quotation_pdf(STAFF, 1)
    model = use_model(FakeModel(draft_from_answer(1)))
    server.read_quotation_pdf(STAFF, 1)
    assert len(model.calls) == 1


def test_reading_only_writes_audit_log(db, use_model):
    use_model(FakeModel(draft_from_answer(5)))
    before = snapshot(db)
    server.read_quotation_pdf(STAFF, 5, TASK)
    assert diff(before, snapshot(db)) == []
    row = db.execute("SELECT * FROM audit_logs").fetchone()
    assert row["task_id"] == TASK
    assert row["tool_name"] == "read_quotation_pdf"
    assert row["request_id"] is None
    assert json.loads(row["output"])["suspicious_content"] is True


def test_missing_quotation(db, use_model):
    model = use_model(FakeModel(draft_from_answer(1)))
    result = server.read_quotation_pdf(STAFF, 999)
    assert fails_with(result, "not_found")
    assert "get_quotations" in result["hint"]
    assert model.calls == []


def test_missing_actor(db, use_model):
    use_model(FakeModel(draft_from_answer(1)))
    assert fails_with(server.read_quotation_pdf(999, 1), "not_found")


@pytest.mark.parametrize(
    ("source_file", "error_type"),
    [
        (None, "not_found"),
        ("quotes/q999.pdf", "not_found"),
        ("../../../Windows/win.ini", "validation"),
        ("../schema.sql", "validation"),
    ],
)
def test_bad_file_path_never_reaches_model(db, use_model, source_file, error_type):
    with db:
        db.execute("UPDATE quotations SET source_file = ? WHERE id = 1", (source_file,))
    model = use_model(FakeModel(draft_from_answer(1)))
    assert fails_with(server.read_quotation_pdf(STAFF, 1), error_type)
    assert model.calls == []


def test_model_outage_is_transient_and_not_cached(db, use_model):
    use_model(FakeModel(error=Transient("Model tạm thời không phản hồi", "Hãy thử lại")))
    assert fails_with(server.read_quotation_pdf(STAFF, 1), "transient")
    model = use_model(FakeModel(draft_from_answer(1)))
    assert server.read_quotation_pdf(STAFF, 1)["ok"]
    assert len(model.calls) == 1


def test_model_that_cannot_finish_is_validation_error(db, use_model):
    use_model(FakeModel(error=InvalidInput("Model không đọc xong tài liệu (refusal)")))
    assert fails_with(server.read_quotation_pdf(STAFF, 1), "validation")
    assert db.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0] == 0


def test_over_mcp_without_calling_model(db_path):
    async def scenario(session):
        tools = (await session.list_tools()).tools
        result = await session.call_tool("read_quotation_pdf", {"actor_id": STAFF, "quotation_id": 999})
        return tools, result

    tools, result = run_session("mcp_servers.docs.server", db_path, scenario)
    assert [tool.name for tool in tools] == ["read_quotation_pdf"]
    assert set(tools[0].inputSchema["properties"]) == {"actor_id", "quotation_id", "task_id"}
    assert json.loads(result.content[0].text)["error_type"] == "not_found"
