"""Tools exposed to the LLM. Every number the agent can say comes from one of these outputs."""
from __future__ import annotations

import sqlite3
from typing import Any, Callable

from leakradar.common import repo
from leakradar.common.money import fmt_inr_short
from leakradar.common.models import DETECTORS

from . import components, pitch_templates


class ToolError(Exception):
    """Returned to the model as an is_error tool_result."""


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "list_top_leaks",
        "description": ("Ranked list of detected revenue leaks across the AM's merchants, highest recoverable ₹/month "
                        "first. Use for 'who is losing the most', filtering by detector, product or recommendation. "
                        "To find merchants that already have a product but still leak, pass "
                        "recommendation='config_review' and product=<product key>."),
        "input_schema": _obj({
            "limit": {"type": "integer", "description": "1-25, default 10"},
            "detector": {"type": "string", "enum": list(DETECTORS)},
            "min_inr": {"type": "integer", "description": "minimum recoverable ₹/month"},
            "recommendation": {"type": "string", "enum": ["cross_sell", "config_review"]},
            "product": {"type": "string", "enum": ["optimizer", "checkout_upi_intent", "subscriptions_autopay_retries"],
                        "description": "product that fits the leak"},
            "merchant_id": {"type": "string"},
        }, []),
    },
    {
        "name": "get_merchant_profile",
        "description": "Merchant details: category, city tier, GMV band, active products, AM owner and its leak_ids.",
        "input_schema": _obj({"merchant_id": {"type": "string", "description": "e.g. M014"}}, ["merchant_id"]),
    },
    {
        "name": "get_leak_detail",
        "description": ("Full detail for one leak: ₹ at risk and recoverable, the statistics behind the decision, the "
                        "assumptions used, a step-by-step calculation, failure share by layer, the recommended product, "
                        "and the placeholders available to draft_pitch."),
        "input_schema": _obj({"leak_id": {"type": "string", "description": "e.g. L0003"}}, ["leak_id"]),
    },
    {
        "name": "get_evidence",
        "description": "A sample of the underlying payment rows for a leak (failed first) and the total row counts.",
        "input_schema": _obj({"leak_id": {"type": "string"},
                              "limit": {"type": "integer", "description": "1-20, default 10"}}, ["leak_id"]),
    },
    {
        "name": "draft_pitch",
        "description": ("Create an outreach draft for the AM to review and approve (never sent automatically). "
                        "Optionally pass message_template: your own wording with {placeholders} for every figure and "
                        "name (see get_leak_detail.pitch_placeholders). The template must not contain any digits "
                        "except small counts; figures are filled in by code. Omit it to use the standard template."),
        "input_schema": _obj({
            "leak_id": {"type": "string"},
            "tone": {"type": "string", "enum": ["concise", "consultative"]},
            "message_template": {"type": "string"},
        }, ["leak_id", "tone"]),
    },
    {
        "name": "render_component",
        "description": ("Show a UI component to the AM. Props are IDs only, never numbers. Types: "
                        "MerchantLeakTable {leak_ids: string[]}; LeakCard {leak_id}; RootCauseCard {leak_id}; "
                        "TrendChart {leak_id, metric: failure_rate (bank spike) | collect_share (UPI collect) | "
                        "recurring_fail_rate (subscriptions)}; DraftMessage {pitch_id}; EvidenceDrawer {leak_id}."),
        "input_schema": _obj({
            "type": {"type": "string", "enum": components.COMPONENT_TYPES},
            "props": {"type": "object", "description": "component props (IDs only)"},
        }, ["type", "props"]),
    },
]


def list_top_leaks(conn, limit: int = 10, detector=None, min_inr=None, recommendation=None, product=None,
                   merchant_id=None) -> dict:
    rows = repo.list_leaks(conn, detector=detector, min_inr=min_inr, recommendation=recommendation, product=product,
                           merchant_id=merchant_id, limit=max(1, min(int(limit), 25)))
    for r in rows:
        r["recoverable_inr_monthly_fmt"] = fmt_inr_short(r["recoverable_inr_monthly"])
        r["monthly_inr_at_risk_fmt"] = fmt_inr_short(r["monthly_inr_at_risk"])
    total = sum(r["recoverable_inr_monthly"] for r in rows)
    return {"count": len(rows), "leaks": rows, "total_recoverable_inr_monthly": total,
            "total_recoverable_inr_monthly_fmt": fmt_inr_short(total),
            "filters": {k: v for k, v in dict(detector=detector, min_inr=min_inr, recommendation=recommendation,
                                               product=product, merchant_id=merchant_id).items() if v is not None}}


def get_merchant_profile(conn, merchant_id: str) -> dict:
    m = repo.get_merchant(conn, merchant_id)
    m["products_active_names"] = [repo.product_name(p) for p in m["products_active"]]
    m["leaks"] = repo.list_leaks(conn, merchant_id=merchant_id)
    return m


def get_leak_detail(conn, leak_id: str) -> dict:
    lk = repo.get_leak(conn, leak_id)
    out = {k: lk[k] for k in ("leak_id", "merchant_id", "merchant_name", "am_owner", "detector", "detector_label",
                              "headline", "severity", "monthly_inr_at_risk", "recoverable_inr_monthly",
                              "annual_churn_inr", "dimension", "stats", "assumptions_used", "root_cause",
                              "recommendation_type")}
    out.update(repo.fmt_fields(lk))
    out["recommended_product"] = lk["recommended_product_info"]
    out["fit_product"] = lk["fit_product_info"]
    out["calculation_steps"] = lk["calculation"]["steps"]
    out["pitch_placeholders"] = pitch_templates.placeholders_for(lk)
    out["trend_metric"] = repo.METRIC_FOR_DETECTOR[lk["detector"]]
    return out


def get_evidence(conn, leak_id: str, limit: int = 10) -> dict:
    ev = repo.evidence(conn, leak_id, page=1, page_size=max(1, min(int(limit), 20)))
    return {"leak_id": leak_id, "total_rows": ev["total"], "failed_rows": ev["failed"], "filter": ev["filter"],
            "sample": ev["rows"]}


def draft_pitch(conn, leak_id: str, tone: str, message_template: str | None = None) -> dict:
    if tone not in pitch_templates.BUILTIN:
        raise ToolError("tone must be 'concise' or 'consultative'")
    lk = repo.get_leak(conn, leak_id)
    try:
        text = pitch_templates.render(lk, tone, message_template)
    except pitch_templates.TemplateError as e:
        raise ToolError(str(e)) from None
    p = repo.create_pitch(conn, leak_id, tone, text)
    return {"pitch_id": p["pitch_id"], "leak_id": leak_id, "status": p["status"], "tone": tone,
            "draft_text": text, "note": "Draft only. The AM must review and approve it; nothing is sent."}


def render_component(conn, type: str, props: dict) -> dict:
    try:
        c = components.validate(conn, type, props)
    except components.ComponentError as e:
        raise ToolError(str(e)) from None
    return {"rendered": c["type"], "props": c["props"]}


HANDLERS: dict[str, Callable[..., dict]] = {
    "list_top_leaks": list_top_leaks, "get_merchant_profile": get_merchant_profile,
    "get_leak_detail": get_leak_detail, "get_evidence": get_evidence, "draft_pitch": draft_pitch,
    "render_component": render_component,
}


def execute(conn: sqlite3.Connection, name: str, args: dict) -> dict:
    if name not in HANDLERS:
        raise ToolError(f"unknown tool {name}")
    if not isinstance(args, dict):
        raise ToolError("tool input must be an object")
    try:
        return HANDLERS[name](conn, **args)
    except repo.NotFound as e:
        raise ToolError(str(e)) from None
    except TypeError as e:
        raise ToolError(f"bad arguments for {name}: {e}") from None
