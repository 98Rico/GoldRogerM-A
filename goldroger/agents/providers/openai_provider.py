from __future__ import annotations

import json
from typing import Optional

try:
    import openai as _openai
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False

from ..llm_client import LLMProvider, LLMResponse, ToolCall


class OpenAIProvider(LLMProvider):
    name = "openai"
    MODELS = {
        "small": "gpt-4o-mini",
        "large": "gpt-4o",
    }

    def __init__(self, api_key: str):
        if not _AVAILABLE:
            raise RuntimeError("openai package not installed — run: uv add openai")
        self._client = _openai.OpenAI(api_key=api_key)

    def complete(
        self,
        messages: list[dict],
        model: str,
        max_tokens: int,
        tools: Optional[list[dict]] = None,
        timeout_ms: int = 60_000,
    ) -> LLMResponse:
        kwargs = {"tools": tools, "tool_choice": "auto"} if tools else {}
        resp = self._client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=messages,
            timeout=timeout_ms / 1000,
            **kwargs,
        )
        msg = resp.choices[0].message
        tool_calls: list[ToolCall] = []
        if msg.tool_calls:
            tool_calls = [
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=json.loads(tc.function.arguments),
                )
                for tc in msg.tool_calls
            ]
        return LLMResponse(content=msg.content or "", tool_calls=tool_calls)

    def format_tool_result(self, tool_call_id: str, content: str) -> dict:
        return {"role": "tool", "tool_call_id": tool_call_id, "content": content}

    def format_assistant_with_tools(self, response: LLMResponse) -> dict:
        return {
            "role": "assistant",
            "content": response.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for tc in response.tool_calls
            ],
        }
