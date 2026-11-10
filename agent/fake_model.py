"""Model giả để debug đồ thị mà không gọi API: trả lần lượt các bước viết sẵn trong kịch bản."""

import itertools
import json
from collections.abc import Callable, Sequence

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

Step = AIMessage | Callable[[list[BaseMessage]], AIMessage]

_ids = itertools.count(1)


def call(name: str, **args) -> AIMessage:
    """Một lượt mà model gọi một tool."""
    return calls((name, args))


def calls(*pairs: tuple[str, dict]) -> AIMessage:
    """Một lượt mà model gọi nhiều tool cùng lúc."""
    tool_calls = [{"name": name, "args": args, "id": f"call_{next(_ids)}", "type": "tool_call"} for name, args in pairs]
    return AIMessage(content="", tool_calls=tool_calls)


def last_result(messages: Sequence[BaseMessage]) -> dict:
    """Kết quả của lời gọi tool gần nhất, để bước sau của kịch bản đọc version hay id."""
    message = next(message for message in reversed(messages) if isinstance(message, ToolMessage))
    return json.loads(message.content)


class ScriptedModel:
    def __init__(self, steps: Sequence[Step]) -> None:
        self._steps = list(steps)
        self.bound_tools: list[dict] | None = None
        self.received: list[list[BaseMessage]] = []

    def bind_tools(self, tools: list[dict]) -> "ScriptedModel":
        self.bound_tools = tools
        return self

    async def ainvoke(self, messages: list[BaseMessage]) -> AIMessage:
        self.received.append(list(messages))
        if not self._steps:
            raise AssertionError("Model giả đã hết bước trong kịch bản")
        step = self._steps.pop(0)
        return step(messages) if callable(step) else step
