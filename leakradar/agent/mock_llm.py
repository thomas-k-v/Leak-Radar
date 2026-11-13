"""Deterministic offline LLM. Routes the AM's message to an intent, calls tools the same way the real model
would, and writes prose only from tool outputs — so it passes the number guard by construction.

MOCK_LLM_BAD_NUMBERS=once|always makes the final reply include a fabricated figure, to exercise the guard
(once: the regenerated reply is clean; always: the guard has to redact).
"""
from __future__ import annotations

import itertools
import os
import re
from typing import Any

from .llm import AssistantTurn, Context, ToolCall

LEAK_RE = re.compile(r"\bL\d{4}\b")
MERCHANT_RE = re.compile(r"\bM\d{3}\b")
PRODUCT_WORDS = {
    "optimizer": "optimizer", "routing": "optimizer",
    "checkout": "checkout_upi_intent", "intent": "checkout_upi_intent",
    "retries": "subscriptions_autopay_retries", "retry": "subscriptions_autopay_retries",
    "subscription": "subscriptions_autopay_retries",
}
PRODUCT_NAMES = {"optimizer": "Optimizer", "checkout_upi_intent": "Optimised Checkout (UPI Intent)",
                 "subscriptions_autopay_retries": "Subscriptions Smart Retries"}
_ids = itertools.count(1)


def _intent(text: str, focus: dict) -> tuple[str, dict]:
    t = text.lower()
    leak = LEAK_RE.search(text)
    merchant = MERCHANT_RE.search(text)
    if ("data" in t or "source" in t) and any(w in t for w in ("real", "synthetic", "source", "where", "fake")):
        return "data_source", {}
    if "already" in t:
        prod = next((p for w, p in PRODUCT_WORDS.items() if w in t), "optimizer")
        return "config_review", {"product": prod}
    if any(w in t for w in ("draft", "pitch", "email", "outreach", "write to")):
        tone = "concise" if "concise" in t or "short" in t else "consultative"
        return "draft", {"leak_id": leak.group(0) if leak else None, "tone": tone}
    if any(w in t for w in ("why", "evidence", "rows", "transactions", "proof", "raw")):
        return "evidence", {"leak_id": leak.group(0) if leak else None}
    if leak:
        return "detail", {"leak_id": leak.group(0)}
    if merchant:
        return "merchant", {"merchant_id": merchant.group(0)}
    if any(w in t for w in ("detail", "tell me more", "drill", "deep dive", "top one", "first one", "biggest one")):
        return "detail", {"leak_id": None}
    if any(w in t for w in ("losing", "leak", "top", "most", "biggest", "worst", "which merchants", "rank")):
        det = None
        if "upi" in t or "collect" in t:
            det = "d2_upi_collect"
        elif "subscription" in t or "renewal" in t or "mandate" in t:
            det = "d3_subscription_churn"
        elif "bank" in t or "spike" in t or "routing" in t:
            det = "d1_bank_spike"
        return "top", {"detector": det}
    return "help", {}


def _focus_leak(args: dict, focus: dict) -> str | None:
    return args.get("leak_id") or focus.get("leak_id") or (focus.get("leak_ids") or [None])[0]


def _out(ctx: Context, name: str) -> Any:
    for n, _i, o in reversed(ctx.tool_results):
        if n == name:
            return o
    return None


def _calls(*specs: tuple[str, dict]) -> list[ToolCall]:
    return [ToolCall(id=f"mock_{next(_ids)}", name=n, input=i) for n, i in specs]


def _plan(intent: str, args: dict, ctx: Context) -> list[ToolCall] | str:
    """Next step for this intent given the tool results so far: tool calls, or the final reply text."""
    done = {n for n, _i, _o in ctx.tool_results}
    focus = ctx.focus

    if intent == "data_source":
        return ("Everything here is synthetic demo data generated from a fixed seed: fictional merchants, fictional "
                "banks (BANK_A to BANK_H) and a synthetic error taxonomy. No real payment data is used. This is an "
                "independent concept project; product names only illustrate fit.")

    if intent == "help":
        return ("I can rank your merchants by recoverable revenue, explain a leak with its evidence, recommend the "
                "product that fits and draft outreach for you to approve. Try: \"Which of my merchants are losing "
                "the most this month?\"")

    if intent in ("top", "config_review"):
        if "list_top_leaks" not in done:
            inp: dict[str, Any] = {"limit": 10}
            if intent == "top" and args.get("detector"):
                inp["detector"] = args["detector"]
            if intent == "config_review":
                inp = {"limit": 25, "recommendation": "config_review", "product": args["product"]}
            return _calls(("list_top_leaks", inp))
        out = _out(ctx, "list_top_leaks")
        if not out or not out.get("leaks"):
            if intent == "config_review":
                return f"No merchant that already has {PRODUCT_NAMES[args['product']]} shows that leak right now."
            return "No leaks match that filter."
        if "render_component" not in done:
            return _calls(("render_component", {"type": "MerchantLeakTable",
                                                "props": {"leak_ids": [x["leak_id"] for x in out["leaks"]]}}))
        top = out["leaks"][0]
        if intent == "config_review":
            return (f"{out['count']} leaks belong to merchants that already have {PRODUCT_NAMES[args['product']]} "
                    f"enabled, worth {out['total_recoverable_inr_monthly_fmt']} a month recoverable in total. These "
                    f"look like configuration issues, not cross-sell opportunities — the largest is "
                    f"{top['merchant_name']} ({top['recoverable_inr_monthly_fmt']} a month). Worth a setup review.")
        return (f"Here are your top {out['count']} leaks, worth {out['total_recoverable_inr_monthly_fmt']} a month "
                f"recoverable in total. The biggest is {top['merchant_name']} ({top['merchant_id']}): "
                f"{top['headline'].lower()}, about {top['recoverable_inr_monthly_fmt']} a month recoverable. "
                f"Click a row to see the detail.")

    if intent == "merchant":
        if "get_merchant_profile" not in done:
            return _calls(("get_merchant_profile", {"merchant_id": args["merchant_id"]}))
        m = _out(ctx, "get_merchant_profile")
        if "error" in m:
            return f"I couldn't find merchant {args['merchant_id']}."
        if m["leak_ids"] and "render_component" not in done:
            return _calls(("render_component", {"type": "MerchantLeakTable", "props": {"leak_ids": m["leak_ids"]}}))
        prods = ", ".join(m["products_active_names"])
        if not m["leak_ids"]:
            return f"{m['name']} ({m['category'].replace('_', ' ')}) has no detected leaks. Active products: {prods}."
        return (f"{m['name']} is a {m['category'].replace('_', ' ')} merchant in the {m['monthly_gmv_band']} GMV band "
                f"with these products active: {prods}. Its leaks are listed above.")

    if intent == "detail":
        leak_id = _focus_leak(args, focus)
        if not leak_id:
            return "Which leak should I open? Ask for your top leaks first, or name a leak ID."
        if "get_leak_detail" not in done:
            return _calls(("get_leak_detail", {"leak_id": leak_id}))
        d = _out(ctx, "get_leak_detail")
        if "error" in d:
            return f"I couldn't find leak {leak_id}."
        if "render_component" not in done:
            return _calls(("render_component", {"type": "LeakCard", "props": {"leak_id": leak_id}}),
                          ("render_component", {"type": "RootCauseCard", "props": {"leak_id": leak_id}}),
                          ("render_component", {"type": "TrendChart",
                                                "props": {"leak_id": leak_id, "metric": d["trend_metric"]}}))
        if d["recommendation_type"] == "config_review":
            rec = (f"They already have {d['fit_product']['name']}, so this looks like a configuration issue — "
                   f"I'd suggest a setup review rather than a pitch.")
        else:
            rec = f"The fitting product is {d['recommended_product']['name']}: {d['recommended_product']['one_line_value']}"
        return (f"{d['merchant_name']}: {d['headline'].lower()}. About {d['monthly_inr_at_risk_fmt']} a month is at "
                f"risk, of which {d['recoverable_inr_monthly_fmt']} looks recoverable. {rec} "
                f"Want me to draft outreach or show the evidence?")

    if intent == "draft":
        leak_id = _focus_leak(args, focus)
        if not leak_id:
            return "Which leak should the draft be about? Open a leak first or name its ID."
        if "draft_pitch" not in done:
            return _calls(("draft_pitch", {"leak_id": leak_id, "tone": args["tone"]}))
        p = _out(ctx, "draft_pitch")
        if "error" in p:
            return f"I couldn't draft that: {p['error']}"
        if "render_component" not in done:
            return _calls(("render_component", {"type": "DraftMessage", "props": {"pitch_id": p["pitch_id"]}}))
        return (f"Here is a {args['tone']} draft ({p['pitch_id']}). Edit anything you like, then approve or reject "
                f"it — nothing is sent automatically.")

    if intent == "evidence":
        leak_id = _focus_leak(args, focus)
        if not leak_id:
            return "Which leak do you want evidence for?"
        if "get_evidence" not in done:
            return _calls(("get_evidence", {"leak_id": leak_id, "limit": 10}))
        ev = _out(ctx, "get_evidence")
        if "error" in ev:
            return f"I couldn't load evidence for {leak_id}."
        if "render_component" not in done:
            return _calls(("render_component", {"type": "EvidenceDrawer", "props": {"leak_id": leak_id}}))
        return (f"These are the underlying payments for {leak_id}: {ev['failed_rows']:,} failed out of "
                f"{ev['total_rows']:,} matching rows, failed ones first. The filter that reproduces them is shown "
                f"at the top of the drawer.")

    return "I'm not sure how to help with that."


class MockProvider:
    name = "mock"

    def __init__(self, bad_numbers: str | None = None):
        self.bad = bad_numbers if bad_numbers is not None else os.environ.get("MOCK_LLM_BAD_NUMBERS", "")

    def complete(self, system: str, messages: list[dict], tools: list[dict], ctx: Context) -> AssistantTurn:
        intent, args = _intent(ctx.user_text, ctx.focus)
        step = _plan(intent, args, ctx)
        if isinstance(step, list):
            content = [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in step]
            return AssistantTurn(content=content, texts=[], tool_calls=step, stop_reason="tool_use")
        retried = _last_is_guard_note(messages)
        if self.bad == "always" or (self.bad == "once" and not retried):
            step = step + " That should lift their revenue by ₹9.87L next quarter."
        return AssistantTurn(content=[{"type": "text", "text": step}], texts=[step], tool_calls=[],
                             stop_reason="end_turn")


def _last_is_guard_note(messages: list[dict]) -> bool:
    last = messages[-1] if messages else None
    if not last or last.get("role") != "user":
        return False
    c = last["content"]
    if isinstance(c, str):
        return c.startswith("[number-guard]")
    return any(isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").startswith("[number-guard]")
               for b in c)
