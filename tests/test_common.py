import sqlite3

import pytest

from mcp_servers.common import DB_ENV, Partial, contract, dumps, open_db, write_transaction
from rules.errors import InvalidInput, NotFound, PermissionDenied, PolicyViolation
from simulator.db import connect
from simulator.seed import build_database


def raising(error):
    def tool():
        raise error

    return contract(tool)


def test_success_follows_contract():
    assert contract(lambda: {"a": 1})() == {"ok": True, "data": {"a": 1}, "truncated": False}


@pytest.mark.parametrize(
    ("rule_error", "error_type"),
    [
        (NotFound("thiếu"), "not_found"),
        (InvalidInput("thiếu"), "validation"),
        (PolicyViolation("thiếu"), "policy"),
        (PermissionDenied("thiếu"), "permission"),
    ],
)
def test_rule_errors_follow_contract(rule_error, error_type):
    assert raising(rule_error)() == {"ok": False, "error_type": error_type, "message": "thiếu"}


def test_partial_result_is_marked_truncated():
    assert contract(lambda: Partial({"a": 1}, ref="doc_1"))() == {
        "ok": True,
        "data": {"a": 1},
        "truncated": True,
        "ref": "doc_1",
    }


def test_error_keeps_hint():
    assert raising(InvalidInput("thiếu", "hãy thêm"))()["hint"] == "hãy thêm"


def test_locked_database_is_transient():
    assert raising(sqlite3.OperationalError("database is locked"))()["error_type"] == "transient"


def test_other_database_errors_are_not_hidden():
    with pytest.raises(sqlite3.OperationalError):
        raising(sqlite3.OperationalError("no such table: budgets"))()


def test_dumps_is_stable_and_compact():
    assert dumps({"b": "Màn hình", "a": 1}) == '{"a":1,"b":"Màn hình"}'


def test_open_db_refuses_missing_file(monkeypatch, tmp_path):
    missing = tmp_path / "missing.db"
    monkeypatch.setenv(DB_ENV, str(missing))
    with pytest.raises(RuntimeError), open_db():
        pass
    assert not missing.exists()


def test_write_transaction_takes_write_lock_immediately(tmp_path):
    # Không dựa vào thời điểm chạy: kết nối thứ hai phải bị chặn ngay khi kết nối đầu vừa mở transaction.
    path = tmp_path / "sim.db"
    build_database(path).close()
    first, second = connect(path), connect(path)
    second.execute("PRAGMA busy_timeout = 0")
    with write_transaction(first):
        with pytest.raises(sqlite3.OperationalError, match="locked"), write_transaction(second):
            pass
    first.close()
    second.close()


def test_write_transaction_rolls_back_on_error():
    conn = build_database()
    with pytest.raises(ValueError), write_transaction(conn):
        conn.execute("UPDATE budgets SET spent = 0")
        raise ValueError
    assert conn.execute("SELECT SUM(spent) FROM budgets").fetchone()[0] > 0
    conn.close()
