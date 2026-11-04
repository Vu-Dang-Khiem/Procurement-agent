"""Dạng JSON mà LLM cách ly phải trả về. Đây mới là bản nháp, `validation.py` mới quyết định giá trị nào dùng được.

Không đặt giới hạn số ở đây, vì việc kiểm tra thuộc về code ở bước sau.
"""

from typing import Literal

from pydantic import BaseModel

Category = Literal["furniture", "it", "marketing", "office", "software"]


class EmailItemDraft(BaseModel):
    item: str | None
    category: Category | None
    quantity: int | None
    unit_price_min: int | None
    unit_price_max: int | None
    total_estimate: int | None
    needed_by: str | None


class EmailDraft(BaseModel):
    kind: Literal["request", "reply", "other"]
    items: list[EmailItemDraft]
    suspicious_content: bool


class QuoteDraft(BaseModel):
    supplier: str | None
    item: str | None
    printed_unit_price: int | None
    price_includes_vat: bool
    vat_rate: int | None
    delivery_days: int | None
    warranty_months: int | None
    valid_until: str | None
    issued_on: str | None
    valid_days: int | None
    suspicious_content: bool
