import pytest

from rules.errors import NotFound
from rules.permissions import can_decide, can_view_budget, can_view_request, load_actor, request_scope
from simulator.seed import HR, IT, build_database

IT_HEAD, IT_STAFF, OTHER_IT_STAFF, HR_HEAD, FINANCE_HEAD, FINANCE_STAFF, DIRECTOR = 1, 3, 4, 8, 26, 27, 30


@pytest.fixture
def conn():
    conn = build_database()
    yield conn
    conn.close()


def test_missing_actor_raises_not_found(conn):
    with pytest.raises(NotFound):
        load_actor(conn, 999)


@pytest.mark.parametrize(
    ("actor_id", "expected"),
    [
        (IT_STAFF, True),  # người tạo
        (OTHER_IT_STAFF, False),  # nhân viên khác cùng phòng
        (IT_HEAD, True),
        (HR_HEAD, False),
        (FINANCE_STAFF, True),
        (FINANCE_HEAD, True),
        (DIRECTOR, True),
    ],
)
def test_who_can_view_a_request(conn, actor_id, expected):
    assert can_view_request(load_actor(conn, actor_id), IT_STAFF, IT) is expected


@pytest.mark.parametrize(
    ("actor_id", "department_id", "expected"),
    [
        (IT_STAFF, IT, True),
        (IT_STAFF, HR, False),
        (IT_HEAD, HR, False),
        (FINANCE_STAFF, HR, True),
        (FINANCE_HEAD, HR, True),
        (DIRECTOR, HR, True),
    ],
)
def test_who_can_view_a_budget(conn, actor_id, department_id, expected):
    assert can_view_budget(load_actor(conn, actor_id), department_id) is expected


def test_request_scope_matches_can_view_request(conn):
    # Danh sách và xem chi tiết phải cho cùng một kết quả với mọi nhân viên và mọi yêu cầu.
    requests = conn.execute("SELECT id, requester_id, department_id FROM purchase_requests").fetchall()
    for (actor_id,) in conn.execute("SELECT id FROM employees").fetchall():
        actor = load_actor(conn, actor_id)
        scope, params = request_scope(actor)
        listed = {row[0] for row in conn.execute(f"SELECT id FROM purchase_requests WHERE {scope}", params)}
        viewable = {row["id"] for row in requests if can_view_request(actor, row["requester_id"], row["department_id"])}
        assert listed == viewable, actor_id


def test_only_assigned_approver_can_decide():
    assert can_decide(IT_HEAD, IT_HEAD, IT_STAFF)
    assert not can_decide(HR_HEAD, IT_HEAD, IT_STAFF)


def test_r5_requester_cannot_decide_own_request():
    assert not can_decide(IT_HEAD, IT_HEAD, IT_HEAD)
