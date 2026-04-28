from __future__ import annotations

from typing import Optional

try:
    import anthropic as _anthropic
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False

from ..llm_client import LLMProvider, LLMResponse, ToolCall


def _to_anthropic_tool(tool: dict) -> dict:
    fn = tool["function"]
    return {
        "name": fn["name"],
        "description": fn.get("description", ""),
        "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
    }


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    MODELS = {
        "small": "claude-haiku-4-5-20251001",
        "large": "claude-sonnet-4-6",
    }

    def __init__(self, api_key: str):
        if not _AVAILABLE:
            raise RuntimeError("anthropic package not installed — run: uv add anthropic")
        self._client = _anthropic.Anthropic(api_key=api_key)

    def complete(
        self,
        messages: list[dict],
        model: str,
        max_tokens: int,
        tools: Optional[list[dict]] = None,
        timeout_ms: int = 60_000,
    ) -> LLMResponse:
        # Anthropic separates system prompt from message list
        system = ""
        msgs: list[dict] = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"] if isinstance(m["content"], str) else ""
            else:
                msgs.append(m)

        kwargs = {"tools": [_to_anthropic_tool(t) for t in tools]} if tools else {}
        resp = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=msgs,
            **kwargs,
        )

        content_text = ""
        tool_calls: list[ToolCall] = []
        for block in resp.content:
            if block.type == "text":
                content_text = block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=block.input)
                )
        return LLMResponse(content=content_text, tool_calls=tool_calls)

    def format_tool_result(self, tool_call_id: str, content: str) -> dict:
        # Anthropic tool results go inside a user message
        return {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": tool_call_id, "content": content}
            ],
        }

    def format_assistant_with_tools(self, response: LLMResponse) -> dict:
        content: list = []
        if response.content:
            content.append({"type": "text", "text": response.content})
        for tc in response.tool_calls:
            content.append(
                {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
            )
        return {"role": "assistant", "content": content}
