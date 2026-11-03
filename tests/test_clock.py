from datetime import datetime, timedelta, timezone

import pytest

from simulator.clock import SimClock, quarter_of
from simulator.db import connect, create_schema

START = datetime(2026, 11, 3, 9, 0, 0)


@pytest.fixture
def conn():
    conn = connect()
    create_schema(conn)
    yield conn
    conn.close()


def test_now_fails_until_set(conn):
    with pytest.raises(RuntimeError):
        SimClock(conn).now()


def test_advance_fails_until_set(conn):
    with pytest.raises(RuntimeError):
        SimClock(conn).advance(timedelta(days=1))


def test_time_stands_still_until_advanced(conn):
    clock = SimClock(conn)
    clock.set(START)
    assert clock.now() == START
    assert clock.now() == START


def test_advance_moves_time_forward(conn):
    clock = SimClock(conn)
    clock.set(START)
    assert clock.advance(timedelta(days=14)) == datetime(2026, 11, 17, 9, 0, 0)
    assert clock.now() == datetime(2026, 11, 17, 9, 0, 0)


def test_advance_rejects_going_backwards(conn):
    clock = SimClock(conn)
    clock.set(START)
    with pytest.raises(ValueError):
        clock.advance(timedelta(seconds=-1))
    assert clock.now() == START


def test_set_rejects_timezone(conn):
    with pytest.raises(ValueError):
        SimClock(conn).set(START.replace(tzinfo=timezone.utc))


def test_now_iso_format(conn):
    clock = SimClock(conn)
    clock.set(START)
    assert clock.now_iso() == "2026-11-03T09:00:00"


def test_shared_between_connections_and_survives_restart(tmp_path):
    path = tmp_path / "sim.db"
    first = connect(path)
    create_schema(first)
    SimClock(first).set(START)

    second = connect(path)
    SimClock(second).advance(timedelta(hours=2))
    assert SimClock(first).now() == datetime(2026, 11, 3, 11, 0, 0)
    first.close()
    second.close()

    reopened = connect(path)
    assert SimClock(reopened).now() == datetime(2026, 11, 3, 11, 0, 0)
    reopened.close()


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 1, 1), "2026-Q1"),
        (datetime(2026, 3, 31, 23, 59, 59), "2026-Q1"),
        (datetime(2026, 4, 1), "2026-Q2"),
        (datetime(2026, 9, 30), "2026-Q3"),
        (datetime(2026, 10, 1), "2026-Q4"),
        (datetime(2026, 12, 31), "2026-Q4"),
    ],
)
def test_quarter_of(moment, expected):
    assert quarter_of(moment) == expected
