"""Model thật cho agent chính. Mỗi lần gọi là một lần tính tiền API."""

from langchain_anthropic import ChatAnthropic

from quarantine.model import model_id_for

# USD cho 1 triệu token (vào, ra), theo trang giá đọc ngày 2026-11-03. Cần kiểm tra lại khi đổi model.
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.00, 10.00)}
# So với giá token vào thường: đọc cache khoảng 0,1 lần, ghi cache 5 phút khoảng 1,25 lần.
CACHE_READ_FACTOR = 0.1
CACHE_WRITE_FACTOR = 1.25


def make_main_model() -> ChatAnthropic:
    # Cache tự động: API lưu phần đầu prompt đến khối cuối cùng có thể cache. Mỗi bước sau chỉ trả giá đọc cache
    # cho phần lặp lại (tool, system prompt, lịch sử cũ), miễn là phần đầu giữ nguyên.
    return ChatAnthropic(
        model=model_id_for("main_agent"),
        max_tokens=4000,
        max_retries=2,
        model_kwargs={"cache_control": {"type": "ephemeral"}},
    )


def cache_counts(usage: dict | None) -> tuple[int, int]:
    """(token đọc cache, token ghi cache) từ `usage_metadata` của langchain-anthropic.

    Khi API báo thời hạn cache, thư viện chuyển số token ghi cache sang `ephemeral_5m_input_tokens`
    và `ephemeral_1h_input_tokens`, còn `cache_creation` thành 0, nên phải cộng cả ba.
    """
    details = (usage or {}).get("input_token_details") or {}
    read = details.get("cache_read") or 0
    write = sum(details.get(name) or 0 for name in ("cache_creation", "ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens"))
    return read, write


def estimate_cost(
    model_id: str, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0
) -> float | None:
    """`input_tokens` là tổng token vào, đã gồm phần đọc và ghi cache, như langchain-anthropic báo."""
    if model_id not in PRICES:
        return None
    input_price, output_price = PRICES[model_id]
    plain = input_tokens - cache_read - cache_write
    cost = plain * input_price + cache_read * input_price * CACHE_READ_FACTOR + cache_write * input_price * CACHE_WRITE_FACTOR
    return (cost + output_tokens * output_price) / 1_000_000
