"""Model thật cho agent chính. Mỗi lần gọi là một lần tính tiền API."""

from langchain_anthropic import ChatAnthropic

from quarantine.model import model_id_for

# USD cho 1 triệu token (vào, ra), theo trang giá đọc ngày 2026-11-03. Cần kiểm tra lại khi đổi model.
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.00, 10.00)}


def make_main_model() -> ChatAnthropic:
    return ChatAnthropic(model=model_id_for("main_agent"), max_tokens=4000, max_retries=2)


def estimate_cost(model_id: str, input_tokens: int, output_tokens: int) -> float | None:
    if model_id not in PRICES:
        return None
    input_price, output_price = PRICES[model_id]
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000
