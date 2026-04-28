"""
LLM provider abstraction — swap model backends via LLM_PROVIDER env var or --llm CLI flag.

Supported providers: mistral (default, free), anthropic (Claude), openai (GPT)

Usage:
    # .env
    LLM_PROVIDER=anthropic
    ANTHROPIC_API_KEY=sk-ant-...

    # CLI
    uv run python -m goldroger.cli --company "NVIDIA" --llm claude
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)

    @property
    def wants_tool(self) -> bool:
        return bool(self.tool_calls)


class LLMProvider(ABC):
    """Abstract LLM provider. Implement to add a new model backend."""

    name: str = "base"

    # Map "small" / "large" tiers to actual model names per provider.
    # Agents declare model_tier; provider resolves the real model name.
    MODELS: dict[str, str] = {}

    def resolve_model(self, tier: str) -> str:
        return self.MODELS.get(tier, self.MODELS.get("small", ""))

    @abstractmethod
    def complete(
        self,
        messages: list[dict],
        model: str,
        max_tokens: int,
        tools: Optional[list[dict]] = None,
        timeout_ms: int = 60_000,
    ) -> LLMResponse:
        ...

    @abstractmethod
    def format_tool_result(self, tool_call_id: str, content: str) -> dict:
        """Return a message dict for a completed tool call."""
        ...

    @abstractmethod
    def format_assistant_with_tools(self, response: LLMResponse) -> dict:
        """Return an assistant message dict that includes pending tool calls."""
        ...


def build_llm_provider(override: Optional[str] = None) -> LLMProvider:
    """
    Build the active LLM provider.

    Priority: override arg > LLM_PROVIDER env var > "mistral" default.
    """
    from .providers.mistral_provider import MistralProvider
    from .providers.anthropic_provider import AnthropicProvider
    from .providers.openai_provider import OpenAIProvider

    name = (override or os.getenv("LLM_PROVIDER", "mistral")).lower()

    if name == "mistral":
        return MistralProvider(os.getenv("MISTRAL_API_KEY", ""))
    if name in ("anthropic", "claude"):
        return AnthropicProvider(os.getenv("ANTHROPIC_API_KEY", ""))
    if name in ("openai", "gpt"):
        return OpenAIProvider(os.getenv("OPENAI_API_KEY", ""))

    raise ValueError(
        f"Unknown LLM_PROVIDER '{name}'. Choose: mistral, anthropic, openai"
    )
