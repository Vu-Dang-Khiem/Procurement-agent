from datetime import date, timedelta

import pymupdf
import pytest

from evals.extraction_answers import EMAIL_ANSWERS, QUOTE_ANSWERS
from simulator.emails import EMAILS
from simulator.quotes import (
    DATA_DIR,
    INJECTIONS,
    VALID_DAYS,
    _broken_layout,
    _letter_layout,
    _table_layout,
    layout_of,
    render,
)
from simulator.seed import EMPLOYEES, QUOTATIONS, START, build_database

CATALOG = {quotation[2] for quotation in QUOTATIONS}
CATEGORIES = {"it", "office", "furniture", "marketing", "software"}
QUOTE_IDS = [quotation[0] for quotation in QUOTATIONS]
EMAILS_BY_ID = {email.id: email for email in EMAILS}


def spans(data):
    doc = pymupdf.open(stream=data, filetype="pdf")
    for block in doc[0].get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            yield from line["spans"]


@pytest.fixture(scope="module")
def pdfs():
    return {id_: (DATA_DIR / f"quotes/q{id_:03d}.pdf").read_bytes() for id_ in QUOTE_IDS}


def pdf_text(data):
    return pymupdf.open(stream=data, filetype="pdf")[0].get_text()


def test_email_ids_are_unique_and_all_answered():
    assert len(EMAILS_BY_ID) == len(EMAILS)
    assert set(EMAILS_BY_ID) == set(EMAIL_ANSWERS)


def test_emails_arrive_before_clock_starts():
    for email in EMAILS:
        assert email.received_at <= START.isoformat()


def test_only_e14_comes_from_outside_company():
    employees = {f"nv{employee[0]:02d}@corp.test" for employee in EMPLOYEES}
    outsiders = [email.id for email in EMAILS if email.sender not in employees]
    assert outsiders == ["e14"]


def test_email_answers_use_valid_values():
    for email_id, answer in EMAIL_ANSWERS.items():
        assert answer["kind"] in ("request", "reply", "other")
        received = EMAILS_BY_ID[email_id].received_at[:10]
        for item in answer["items"]:
            assert item["item"] is None or item["item"] in CATALOG
            assert item["category"] is None or item["category"] in CATEGORIES
            if item["needed_by"] is not None:
                assert date.fromisoformat(item["needed_by"]).isoformat() >= received


def test_only_request_and_reply_have_items():
    for answer in EMAIL_ANSWERS.values():
        assert bool(answer["items"]) == (answer["kind"] != "other")


def test_suspicious_emails_are_the_injection_samples():
    suspicious = [email_id for email_id, answer in EMAIL_ANSWERS.items() if answer["suspicious_content"]]
    assert suspicious == ["e10", "e11", "e12"]


def test_noise_cases_are_present():
    assert "tam 5tr/c" in EMAILS_BY_ID["e02"].body
    assert "như mấy cái cũ" in EMAILS_BY_ID["e03"].body
    assert EMAIL_ANSWERS["e04"]["items"][0]["quantity"] is None
    assert "mười hai" in EMAILS_BY_ID["e07"].body
    assert len(EMAIL_ANSWERS["e09"]["items"]) == 2


def test_correction_email_keeps_old_value_only_in_quote():
    body = EMAILS_BY_ID["e08"].body
    assert "8 laptop" in body
    assert "> Team dev cần 6 laptop" in body
    assert EMAIL_ANSWERS["e08"]["items"][0]["quantity"] == 8


def test_seed_points_to_existing_pdfs():
    conn = build_database()
    rows = conn.execute("SELECT source_file FROM quotations").fetchall()
    conn.close()
    assert len(rows) == 22
    for row in rows:
        assert (DATA_DIR / row["source_file"]).is_file()


@pytest.mark.parametrize("quotation_id", QUOTE_IDS)
def test_committed_pdf_matches_generator(pdfs, quotation_id):
    assert pdfs[quotation_id] == render(quotation_id)


@pytest.mark.parametrize("quotation_id", QUOTE_IDS)
def test_each_pdf_has_one_page(pdfs, quotation_id):
    assert pymupdf.open(stream=pdfs[quotation_id], filetype="pdf").page_count == 1


def test_quote_answers_cover_every_quotation():
    assert list(QUOTE_ANSWERS) == QUOTE_IDS


def test_prices_with_vat_are_whole_numbers():
    for _, _, _, price, vat, *_ in QUOTATIONS:
        assert price * (100 + vat) % 100 == 0


def test_all_three_layouts_are_used():
    assert {layout_of(id_) for id_ in QUOTE_IDS} == {_table_layout, _letter_layout, _broken_layout}


def test_table_layout_prints_values_directly(pdfs):
    assert layout_of(1) is _table_layout
    text = pdf_text(pdfs[1])
    for expected in ("Minh Long Tech", "Màn hình 24 inch", "5.200.000", "10%", "01/12/2026", "36 tháng"):
        assert expected in text


def test_letter_layout_needs_interpretation(pdfs):
    assert layout_of(2) is _letter_layout
    text = " ".join(pdf_text(pdfs[2]).split())
    assert "5 triệu đồng" in text
    assert "1 tuần" in text
    assert "được bảo hành 2 năm" in text
    # Hạn báo giá 30/11/2026 không được in ra, phải tính từ ngày lập.
    assert "01/10/2026" in text
    assert "60 ngày" in text
    assert "30/11/2026" not in text


def test_letter_quotes_are_dated_before_clock_starts():
    for quotation in QUOTATIONS:
        if layout_of(quotation[0]) is _letter_layout:
            quoted_on = date.fromisoformat(quotation[7]) - timedelta(days=VALID_DAYS)
            assert quoted_on < START.date()


def test_broken_layout_shows_price_with_vat_and_no_accents(pdfs):
    assert layout_of(3) is _broken_layout
    text = pdf_text(pdfs[3])
    assert "Phuc Thanh" in text
    assert "man hinh 24 inch".lower() in text.lower()
    assert "5.184.000" in text  # 4.800.000 cộng VAT 8%
    assert "4.800.000" not in text


def test_expired_quotes_show_past_date(pdfs):
    assert "15/10/2026" in pdf_text(pdfs[4])


def test_hidden_injections_are_white_and_tiny(pdfs):
    for quotation_id, (text, hidden) in INJECTIONS.items():
        extracted = " ".join(pdf_text(pdfs[quotation_id]).split())
        assert text in extracted
        white = [span for span in spans(pdfs[quotation_id]) if span["color"] == 0xFFFFFF]
        if hidden:
            assert white
            assert all(span["size"] <= 1 for span in white)
        else:
            assert not white


def test_clean_pdfs_have_no_hidden_text(pdfs):
    for quotation_id in QUOTE_IDS:
        if quotation_id not in INJECTIONS:
            assert not [span for span in spans(pdfs[quotation_id]) if span["color"] == 0xFFFFFF]


def test_quote_answers_flag_exactly_the_injections():
    flagged = {id_ for id_, answer in QUOTE_ANSWERS.items() if answer["suspicious_content"]}
    assert flagged == set(INJECTIONS)


def test_injection_does_not_change_the_true_value():
    assert QUOTE_ANSWERS[8]["delivery_days"] == 21
