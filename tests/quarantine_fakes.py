"""Model giả và bản nháp mẫu cho LLM cách ly, dùng chung cho các file test. Không gọi API."""

from datetime import date
from types import SimpleNamespace

from evals.extraction_answers import QUOTE_ANSWERS
from quarantine.schemas import QuoteDraft
from simulator.quotes import _broken_layout, _letter_layout, layout_of
from simulator.seed import MONITOR


class FakeModel:
    """Trả về bản nháp cho trước và ghi lại mọi thứ được gửi đi. Truyền `error` để giả lập lỗi."""

    def __init__(self, draft=None, error=None):
        self.draft = draft
        self.error = error
        self.calls = []

    def extract(self, system, document, schema):
        self.calls.append(SimpleNamespace(system=system, document=document, schema=schema))
        if self.error:
            raise self.error
        return self.draft


def quote_draft(**fields):
    values = dict(
        supplier="Minh Long Tech",
        item=MONITOR,
        printed_unit_price=5_200_000,
        price_includes_vat=False,
        vat_rate=10,
        delivery_days=5,
        warranty_months=36,
        valid_until="2026-12-01",
        issued_on=None,
        valid_days=None,
        suspicious_content=False,
    )
    return QuoteDraft(**(values | fields))


def draft_from_answer(quotation_id):
    """Bản nháp mà model đọc đúng sẽ trả về, theo đúng kiểu trình bày của từng file PDF."""
    answer = QUOTE_ANSWERS[quotation_id]
    fields = dict(
        supplier=answer["supplier"],
        item=answer["item"],
        printed_unit_price=answer["unit_price"],
        vat_rate=answer["vat_rate"],
        delivery_days=answer["delivery_days"],
        warranty_months=answer["warranty_months"],
        valid_until=answer["valid_until"],
        suspicious_content=answer["suspicious_content"],
    )
    if layout_of(quotation_id) is _broken_layout:
        fields |= dict(
            printed_unit_price=answer["unit_price"] * (100 + answer["vat_rate"]) // 100,
            price_includes_vat=True,
        )
    if layout_of(quotation_id) is _letter_layout:
        issued = date.fromisoformat(answer["valid_until"]).toordinal() - 60
        fields |= dict(valid_until=None, issued_on=date.fromordinal(issued).isoformat(), valid_days=60)
    return quote_draft(**fields)
