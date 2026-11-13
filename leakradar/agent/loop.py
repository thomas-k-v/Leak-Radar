"""Agent loop: provider round → number guard on text → run tools → emit UI events → repeat.

Events (streamed to the browser as SSE):
  {"type": "status", "text"}                 progress ("Looking up leak L0003…")
  {"type": "text", "text"}                   assistant prose that passed the number guard
  {"type": "component", "component", "props"} a validated render_component call
  {"type": "guard", "action", "violations"}   guard intervened (regenerate | redacted)
  {"type": "error", "text"}
  {"type": "done", "conversation_id"}
History is append-only (thinking blocks and rejected drafts stay in place) so it replays cleanly.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from leakradar.common.config import REPO_ROOT

from . import number_guard, tools
from .llm import Context, LLMProvider
from .prompts import SYSTEM_PROMPT

MAX_ROUNDS = 10
GUARD_PREFIX = "[number-guard]"
STATUS = {
    "list_top_leaks": "Ranking leaks…", "get_merchant_profile": "Loading merchant {merchant_id}…",
    "get_leak_detail": "Loading leak {leak_id}…", "get_evidence": "Pulling evidence for {leak_id}…",
    "draft_pitch": "Drafting outreach for {leak_id}…", "render_component": "Rendering {type}…",
}


@dataclass
class Conversation:
    id: str
    history: list[dict] = field(default_factory=list)
    focus: dict[str, Any] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)


_STORE: dict[str, Conversation] = {}
_STORE_LOCK = threading.Lock()


def get_conversation(conversation_id: str | None) -> Conversation:
    with _STORE_LOCK:
        if conversation_id and conversation_id in _STORE:
            return _STORE[conversation_id]
        c = Conversation(id=conversation_id or uuid.uuid4().hex[:12])
        _STORE[c.id] = c
        return c


def _log_guard(record: dict) -> None:
    path = Path(os.environ.get("LEAKRADAR_GUARD_LOG", REPO_ROOT / "logs" / "guard.jsonl"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _update_focus(focus: dict, name: str, args: dict, out: dict) -> None:
    if name == "list_top_leaks" and out.get("leaks"):
        focus["leak_ids"] = [x["leak_id"] for x in out["leaks"]]
    elif name in ("get_leak_detail", "get_evidence"):
        focus["leak_id"] = args.get("leak_id")
        if "merchant_id" in out:
            focus["merchant_id"] = out["merchant_id"]
    elif name == "draft_pitch":
        focus["leak_id"] = out["leak_id"]
        focus["pitch_id"] = out["pitch_id"]
    elif name == "get_merchant_profile":
        focus["merchant_id"] = out["merchant_id"]
        if out.get("leak_ids"):
            focus["leak_ids"] = out["leak_ids"]


def run_turn(conn: sqlite3.Connection, conv: Conversation, user_text: str, provider: LLMProvider) -> Iterator[dict]:
    conv.history.append({"role": "user", "content": user_text})
    outputs_this_turn: list[Any] = [user_text]         # the AM's own figures may be echoed back
    results_ctx: list[tuple[str, dict, Any]] = []
    retries_used = 0
    for _round in range(MAX_ROUNDS):
        ctx = Context(conversation_id=conv.id, focus=conv.focus, user_text=user_text, tool_results=results_ctx)
        try:
            turn = provider.complete(SYSTEM_PROMPT, conv.history, tools.TOOL_SPECS, ctx)
        except Exception as e:  # provider/network failure: surface it, keep history replayable
            if _round == 0 and conv.history and conv.history[-1].get("content") == user_text:
                conv.history.pop()          # nothing happened yet: let the AM simply resend
            yield {"type": "error", "text": f"The language model call failed ({type(e).__name__}). Please retry."}
            return
        conv.history.append({"role": "assistant", "content": turn.content})

        if turn.stop_reason == "refusal":
            yield {"type": "text", "text": "I can't help with that request."}
            return

        guard_note = None
        text = "\n\n".join(t.strip() for t in turn.texts if t.strip())
        if text:
            res = number_guard.check(text, number_guard.allowed_numbers(outputs_this_turn))
            if res.ok:
                yield {"type": "text", "text": text}
            else:
                action = "regenerate" if retries_used == 0 else "redacted"
                _log_guard({"ts": time.time(), "conversation_id": conv.id, "provider": provider.name,
                            "action": action, "violations": res.violations, "text": text})
                yield {"type": "guard", "action": action, "violations": res.violations}
                if action == "regenerate":
                    retries_used += 1
                    guard_note = (f"{GUARD_PREFIX} Your last reply was not shown to the AM because it contained figures "
                                  f"that are not in this turn's tool results: {', '.join(res.violations)}. Rewrite it "
                                  f"using only figures from tool results (call a tool again if you need one), or "
                                  f"without those figures.")
                else:
                    yield {"type": "text", "text": number_guard.redact(text, res.violations)}

        results_blocks: list[dict] = []
        for call in turn.tool_calls:
            if turn.stop_reason == "max_tokens":
                results_blocks.append({"type": "tool_result", "tool_use_id": call.id, "is_error": True,
                                       "content": "tool input was truncated; call the tool again"})
                continue
            yield {"type": "status", "text": STATUS.get(call.name, call.name).format_map(_SafeDict(call.input))}
            try:
                out = tools.execute(conn, call.name, call.input)
            except tools.ToolError as e:
                results_blocks.append({"type": "tool_result", "tool_use_id": call.id, "is_error": True,
                                       "content": str(e)})
                results_ctx.append((call.name, call.input, {"error": str(e)}))
                continue
            outputs_this_turn.append(out)
            results_ctx.append((call.name, call.input, out))
            _update_focus(conv.focus, call.name, call.input, out)
            if call.name == "render_component":
                yield {"type": "component", "component": out["rendered"], "props": out["props"]}
            results_blocks.append({"type": "tool_result", "tool_use_id": call.id,
                                   "content": json.dumps(out, default=str)})

        if results_blocks:
            content: list[dict] = list(results_blocks)
            if guard_note:
                content.append({"type": "text", "text": guard_note})
            conv.history.append({"role": "user", "content": content})
            continue
        if guard_note:
            conv.history.append({"role": "user", "content": guard_note})
            continue
        return
    yield {"type": "error", "text": "Stopped after too many steps. Try a narrower question."}


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "…"
