"""LLM provider interface + the Anthropic implementation.

The agent loop only needs one call per round: given system prompt, history and tools, return the assistant
turn. Text is checked by the number guard before the UI sees it, so per-token streaming would buy nothing;
the Anthropic provider still uses the streaming API (`messages.stream` + `get_final_message`) for timeout
safety. Tool inputs are small IDs, so eager input streaming is left off and the API keeps validating
inputs; the tool layer validates them again anyway.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class AssistantTurn:
    content: list[Any]                # exactly what goes back into history (SDK blocks or dicts)
    texts: list[str]
    tool_calls: list[ToolCall]
    stop_reason: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Context:
    """Per-request context the mock uses; real providers ignore it."""
    conversation_id: str
    focus: dict[str, Any]
    user_text: str
    tool_results: list[tuple[str, dict, Any]]   # (tool name, input, output) this turn


class LLMProvider(Protocol):
    name: str

    def complete(self, system: str, messages: list[dict], tools: list[dict], ctx: Context) -> AssistantTurn: ...


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str | None = None, effort: str | None = None):
        import anthropic

        self.client = anthropic.Anthropic()
        self.model = model or os.environ.get("LLM_MODEL", "claude-sonnet-5-5")
        self.effort = effort or os.environ.get("LLM_EFFORT", "medium")
        self.use_fallbacks = os.environ.get("LLM_FALLBACKS", "default") != "off"

    def complete(self, system: str, messages: list[dict], tools: list[dict], ctx: Context) -> AssistantTurn:
        kwargs: dict[str, Any] = dict(
            model=self.model, max_tokens=16000,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=messages, tools=tools, output_config={"effort": self.effort},
        )
        if self.use_fallbacks:
            # Server-side refusal fallback: a policy decline re-runs on a fallback model within the call.
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
            stream_fn = self.client.beta.messages.stream
        else:
            stream_fn = self.client.messages.stream
        with stream_fn(**kwargs) as stream:
            msg = stream.get_final_message()
        texts = [b.text for b in msg.content if b.type == "text" and b.text.strip()]
        calls = [ToolCall(id=b.id, name=b.name, input=b.input) for b in msg.content if b.type == "tool_use"]
        return AssistantTurn(content=list(msg.content), texts=texts, tool_calls=calls, stop_reason=msg.stop_reason,
                             meta={"model": msg.model, "usage": msg.usage.model_dump() if msg.usage else None})


def get_provider() -> LLMProvider:
    kind = os.environ.get("LLM_PROVIDER", "mock").lower()
    if kind == "anthropic":
        return AnthropicProvider()
    from .mock_llm import MockProvider

    return MockProvider()
