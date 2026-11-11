import json
from datetime import datetime

import pytest

from rules.errors import InvalidInput, NotFound
from rules.policy import check_policy, required_levels, required_quotes, total_with_vat
from simulator.clock import SimClock
from simulator.seed import MARKETING, QUARTER, build_database


@pytest.fixture
def conn():
    conn = build_database()
    yield conn
    conn.close()


def violated(result):
    return [finding.rule for finding in result.violations]


def flagged(result):
    return [finding.rule for finding in result.flags]


def set_budget(conn, department_id, available):
    with conn:
        conn.execute(
            "UPDATE budgets SET total = spent + reserved + ? WHERE department_id = ? AND quarter = ?",
            (available, department_id, QUARTER),
        )


@pytest.mark.parametrize(
    ("amount", "quotes", "levels"),
    [
        (19_999_999, 1, ("head",)),
        (20_000_000, 2, ("head", "finance")),
        (100_000_000, 2, ("head", "finance")),
        (100_000_001, 3, ("head", "finance", "director")),
    ],
)
def test_thresholds_at_boundaries(amount, quotes, levels):
    assert required_quotes(amount) == quotes
    assert required_levels(amount) == levels


def test_total_includes_vat():
    assert total_with_vat(5_200_000, 10, 10) == 57_200_000


def test_clean_proposal_passes_every_rule(conn):
    result = check_policy(conn, 2, 11, [11])
    assert result.total_amount == 7_150_000
    assert result.violations == []
    assert result.flags == []
    assert result.passed == ["R1", "R2", "R3", "R6", "R7", "R8", "R9"]


def test_r1_blocks_request_over_budget(conn):
    result = check_policy(conn, 12, 8, [6, 7, 8])
    assert violated(result) == ["R1"]


def test_r1_allows_total_equal_to_remaining_budget(conn):
    # Yêu cầu 12 chọn báo giá 8: 20 cái × 17 triệu × 1,08.
    set_budget(conn, MARKETING, 367_200_000)
    assert "R1" not in violated(check_policy(conn, 12, 8, [6, 7, 8]))
    set_budget(conn, MARKETING, 367_199_999)
    assert "R1" in violated(check_policy(conn, 12, 8, [6, 7, 8]))


def test_r1_fails_when_quarter_has_no_budget(conn):
    SimClock(conn).set(datetime(2027, 1, 2, 9, 0, 0))
    assert "R1" in violated(check_policy(conn, 2, 11, [11]))


def test_r1_does_not_count_own_reservation_twice(conn):
    # Yêu cầu 5 đã giữ chỗ 81,4 triệu. Chỉ còn đủ khi cộng lại phần của chính nó.
    set_budget(conn, 1, 0)
    assert "R1" not in violated(check_policy(conn, 5, 6, [6, 7]))


def test_r2_needs_second_supplier_at_20_million(conn):
    # Yêu cầu 19 chỉ có một báo giá dùng được, trong khi 70,4 triệu cần hai.
    result = check_policy(conn, 19, 12, [12])
    assert violated(result) == ["R2"]
    assert result.total_amount == 70_400_000


def test_r2_does_not_count_unusable_quotes(conn):
    # Báo giá 13 đã hết hạn, báo giá 14 chưa được duyệt.
    result = check_policy(conn, 19, 12, [12, 13, 14])
    assert violated(result) == ["R2", "R3", "R7"]


def test_r2_counts_quotes_from_same_supplier_once(conn):
    with conn:
        conn.execute(
            "INSERT INTO quotations (id, supplier_id, item, unit_price, vat_rate, delivery_days,"
            " warranty_months, valid_until) VALUES (23, 7, 'Ghế công thái học', 3100000, 10, 7, 36, '2026-12-15')"
        )
    assert "R2" in violated(check_policy(conn, 19, 12, [12, 23]))


def test_r3_blocks_unapproved_supplier(conn):
    assert "R3" in violated(check_policy(conn, 1, 5, [1, 2, 5]))


def test_r7_allows_quote_on_last_valid_day(conn):
    SimClock(conn).set(datetime(2026, 12, 1, 9, 0, 0))
    assert "R7" not in violated(check_policy(conn, 2, 11, [11]))
    SimClock(conn).set(datetime(2026, 12, 2, 9, 0, 0))
    assert "R7" in violated(check_policy(conn, 2, 11, [11]))


def test_r7_blocks_expired_quote(conn):
    assert "R7" in violated(check_policy(conn, 1, 4, [1, 4]))


def test_r6_flags_request_13_as_duplicate_of_10(conn):
    result = check_policy(conn, 13, 1, [1, 2])
    assert "R6" in flagged(result)
    assert "#10" in next(finding.message for finding in result.flags if finding.rule == "R6")


def test_r6_ignores_cancelled_requests(conn):
    with conn:
        conn.execute("UPDATE purchase_requests SET status = 'CANCELLED' WHERE id = 10")
    assert "R6" not in flagged(check_policy(conn, 13, 1, [1, 2]))


@pytest.mark.parametrize(
    ("created_at", "expected"),
    [("2026-10-19T15:30:00", True), ("2026-10-19T15:29:59", False)],
)
def test_r6_window_is_14_days_inclusive(conn, created_at, expected):
    # Yêu cầu 13 tạo lúc 2026-11-02T15:30:00.
    with conn:
        conn.execute("UPDATE purchase_requests SET created_at = ? WHERE id = 10", (created_at,))
    assert ("R6" in flagged(check_policy(conn, 13, 1, [1, 2]))) is expected


def test_r8_flags_requests_14_to_16_as_split(conn):
    result = check_policy(conn, 16, 16, [16])
    assert "R8" in flagged(result)
    message = next(finding.message for finding in result.flags if finding.rule == "R8")
    assert "#14" in message
    assert "#15" in message
    assert "55,440,000" in message


def test_r8_not_flagged_when_sum_stays_in_same_tier(conn):
    # Yêu cầu 13 đã ở mức 2 báo giá, cộng với yêu cầu 10 vẫn ở mức đó.
    assert "R8" not in flagged(check_policy(conn, 13, 1, [1, 2]))


def test_flags_do_not_block_submission(conn):
    assert violated(check_policy(conn, 16, 16, [16])) == []


def test_r9_blocks_resubmitting_rejected_proposal_unchanged(conn):
    # Yêu cầu 8 bị Finance từ chối với 10 cái, báo giá 9 được chọn trong [9, 10].
    result = check_policy(conn, 8, 9, [10, 9])
    assert violated(result) == ["R9"]
    message = result.violations[0].message
    assert "Giảm còn 6 cái" in message


@pytest.mark.parametrize(
    ("change", "selected", "quotes"),
    [("quantity", 9, [9, 10]), (None, 10, [9, 10]), (None, 9, [9])],
)
def test_r9_passes_once_something_changed(conn, change, selected, quotes):
    if change == "quantity":
        with conn:
            conn.execute(
                "UPDATE purchase_requests SET extracted_json = json_set(extracted_json, '$.quantity', 6) WHERE id = 8"
            )
    assert "R9" not in violated(check_policy(conn, 8, selected, quotes))


def test_r9_only_compares_with_latest_rejection(conn):
    # Lần từ chối mới hơn với hồ sơ khác: hồ sơ cũ giờ gửi lại được.
    with conn:
        conn.execute(
            "INSERT INTO approvals (request_id, level, approver_id, decision, reason, decided_at, snapshot_json)"
            " VALUES (8, 'finance', 28, 'rejected', 'Đổi nhà cung cấp', '2026-11-03T08:00:00', ?)",
            (json.dumps({"item": "Máy chiếu", "quantity": 10, "selected_quotation_id": 10, "quotation_ids": [9, 10]}),),
        )
    assert "R9" not in violated(check_policy(conn, 8, 9, [9, 10]))
    assert "R9" in violated(check_policy(conn, 8, 10, [9, 10]))


def test_check_policy_writes_nothing(conn):
    before = list(conn.iterdump())
    check_policy(conn, 12, 8, [6, 7, 8])
    assert list(conn.iterdump()) == before


def test_missing_request_raises_not_found(conn):
    with pytest.raises(NotFound):
        check_policy(conn, 999, 1, [1])


def test_missing_quote_raises_not_found(conn):
    with pytest.raises(NotFound) as error:
        check_policy(conn, 2, 999, [999])
    assert "get_quotations" in error.value.hint


def test_quote_of_other_item_is_rejected(conn):
    with pytest.raises(InvalidInput):
        check_policy(conn, 2, 1, [1])


def test_selected_quote_must_be_in_list(conn):
    with pytest.raises(InvalidInput):
        check_policy(conn, 2, 11, [])


def test_request_without_quantity_asks_for_clarification(conn):
    with pytest.raises(InvalidInput) as error:
        check_policy(conn, 3, 18, [18])
    assert "ask_requester" in error.value.hint


def test_submitted_seed_requests_pass_blocking_rules(conn):
    requests = conn.execute(
        "SELECT id, proposal_json FROM purchase_requests WHERE status IN ('PENDING_APPROVAL', 'APPROVED', 'PO_DRAFTED')"
    ).fetchall()
    assert requests
    for request in requests:
        proposal = json.loads(request["proposal_json"])
        result = check_policy(conn, request["id"], proposal["selected_quotation_id"], proposal["quotation_ids"])
        assert result.violations == []
