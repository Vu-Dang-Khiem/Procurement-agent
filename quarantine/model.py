"""Gọi model cho LLM cách ly. Lời gọi không có tham số tool, nên model không có cách nào hành động."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeVar

import anthropic
import yaml
from pydantic import BaseModel

from rules.errors import InvalidInput, Transient

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "models.yaml"

Schema = TypeVar("Schema", bound=BaseModel)


class ExtractionModel(Protocol):
    def extract(self, system: str, document: str, schema: type[Schema]) -> Schema: ...


def model_id_for(role: str, config_path: Path = CONFIG_PATH) -> str:
    return yaml.safe_load(config_path.read_text(encoding="utf-8"))["models"][role]


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class ClaudeModel:
    def __init__(self, model_id: str, client: anthropic.Anthropic | None = None) -> None:
        self.model_id = model_id
        self.usage = Usage()
        self._client = client or anthropic.Anthropic()

    def extract(self, system: str, document: str, schema: type[Schema]) -> Schema:
        try:
            response = self._client.messages.parse(
                model=self.model_id,
                max_tokens=4000,
                system=system,
                messages=[{"role": "user", "content": document}],
                output_format=schema,
                output_config={"effort": "medium"},
            )
        except (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError) as cause:
            raise Transient("Model tạm thời không phản hồi", "Hãy thử lại") from cause

        self.usage.calls += 1
        self.usage.input_tokens += response.usage.input_tokens
        self.usage.output_tokens += response.usage.output_tokens
        if response.stop_reason in ("refusal", "max_tokens") or response.parsed_output is None:
            raise InvalidInput(
                f"Model không đọc xong tài liệu ({response.stop_reason})",
                "Hãy chuyển tài liệu này cho người kiểm tra",
            )
        return response.parsed_output
