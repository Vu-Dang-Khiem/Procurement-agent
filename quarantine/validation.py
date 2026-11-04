"""Kiểm tra bản nháp của LLM cách ly bằng code.

Giá trị sai kiểu, ngoài khoảng hoặc ngoài danh mục đều thành None, nghĩa là chưa rõ và phải hỏi lại.
Phép tính như đổi giá về trước VAT và tính ngày hết hạn cũng do code làm, theo nguyên tắc ở mục 2.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from quarantine.schemas import EmailDraft, EmailItemDraft, QuoteDraft
from rules.policy import total_with_vat

MAX_ITEMS_PER_EMAIL = 10
MAX_QUANTITY = 100_000
MAX_MONEY = 100_000_000_000
MAX_DELIVERY_DAYS = 365
MAX_WARRANTY_MONTHS = 240
MAX_VALID_DAYS = 365


@dataclass(frozen=True)
class Catalog:
    items: frozenset[str]
    suppliers: frozenset[str]


def _in_range(value: int | None, low: int, high: int) -> int | None:
    return value if value is not None and low <= value <= high else None


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _in_catalog(value: str | None, allowed: frozenset[str]) -> str | None:
    return value if value in allowed else None


def _validate_email_item(draft: EmailItemDraft, catalog: Catalog, received_on: date) -> dict:
    price_min = _in_range(draft.unit_price_min, 1, MAX_MONEY)
    price_max = _in_range(draft.unit_price_max, 1, MAX_MONEY)
    if price_min is not None and price_max is not None and price_min > price_max:
        price_min = price_max = None
    needed_by = _parse_date(draft.needed_by)
    # Ngày cần trước khi nhận được thư là đọc sai, thường là sai năm.
    if needed_by is not None and needed_by < received_on:
        needed_by = None
    return {
        "item": _in_catalog(draft.item, catalog.items),
        "category": draft.category,
        "quantity": _in_range(draft.quantity, 1, MAX_QUANTITY),
        "unit_price_min": price_min,
        "unit_price_max": price_max,
        "total_estimate": _in_range(draft.total_estimate, 1, MAX_MONEY),
        "needed_by": needed_by.isoformat() if needed_by else None,
    }


def validate_email(draft: EmailDraft, catalog: Catalog, received_on: date) -> dict:
    items = [] if draft.kind == "other" else draft.items[:MAX_ITEMS_PER_EMAIL]
    return {
        "kind": draft.kind,
        "items": [_validate_email_item(item, catalog, received_on) for item in items],
        # Email dài bất thường về số mặt hàng cũng là dấu hiệu đáng ngờ.
        "suspicious_content": draft.suspicious_content or len(draft.items) > MAX_ITEMS_PER_EMAIL,
    }


def _unit_price_before_vat(draft: QuoteDraft, vat_rate: int | None) -> int | None:
    printed = _in_range(draft.printed_unit_price, 1, MAX_MONEY)
    if printed is None or not draft.price_includes_vat:
        return printed
    if vat_rate is None:
        return None
    # Làm tròn đến đồng gần nhất, không dùng số thực.
    divisor = 100 + vat_rate
    return (printed * 100 + divisor // 2) // divisor


def _valid_until(draft: QuoteDraft) -> str | None:
    printed = _parse_date(draft.valid_until)
    if printed is not None:
        return printed.isoformat()
    issued = _parse_date(draft.issued_on)
    days = _in_range(draft.valid_days, 1, MAX_VALID_DAYS)
    if issued is None or days is None:
        return None
    return (issued + timedelta(days=days)).isoformat()


def validate_quote(draft: QuoteDraft, catalog: Catalog, hidden_text_found: bool = False) -> dict:
    vat_rate = _in_range(draft.vat_rate, 0, 100)
    unit_price = _unit_price_before_vat(draft, vat_rate)
    return {
        "supplier": _in_catalog(draft.supplier, catalog.suppliers),
        "item": _in_catalog(draft.item, catalog.items),
        "unit_price": unit_price,
        "vat_rate": vat_rate,
        "unit_price_with_vat": (
            total_with_vat(unit_price, vat_rate, 1) if unit_price is not None and vat_rate is not None else None
        ),
        "delivery_days": _in_range(draft.delivery_days, 0, MAX_DELIVERY_DAYS),
        "warranty_months": _in_range(draft.warranty_months, 0, MAX_WARRANTY_MONTHS),
        "valid_until": _valid_until(draft),
        "suspicious_content": draft.suspicious_content or hidden_text_found,
        "hidden_text_found": hidden_text_found,
    }
