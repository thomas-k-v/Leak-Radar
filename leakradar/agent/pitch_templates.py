"""Pitch drafting: the LLM writes prose with {placeholders}; code fills every number.

The template is rejected if it contains any raw figure (number guard with an empty allow-list) or
an unknown placeholder, so a draft can never carry an LLM-authored number.
"""
from __future__ import annotations

import re
from string import Formatter

from leakradar.common.money import fmt_inr_short, fmt_pct

from . import number_guard

PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")

BUILTIN = {
    "concise": (
        "Hi {merchant_name} team,\n\n"
        "{issue_summary}. On our numbers that is about {monthly_inr_at_risk} a month at risk, "
        "of which roughly {recoverable_inr_monthly} looks recoverable.\n\n"
        "{product_name} is built for this: {product_value} "
        "Could we take a few minutes this week to walk through the data?\n\n"
        "Best,\n{am_name}"
    ),
    "consultative": (
        "Hi {merchant_name} team,\n\n"
        "While reviewing your recent payments we noticed a pattern worth a closer look: {issue_detail}.\n\n"
        "Based on the last {days_observed} days, we estimate about {monthly_inr_at_risk} a month is at risk, "
        "and around {recoverable_inr_monthly} of that is realistically recoverable. These are estimates from "
        "your transaction data, not guarantees — happy to share exactly how we calculated them.\n\n"
        "One option is {product_name}. {product_value} "
        "Would you be open to a short call to look at the evidence together and decide whether it is worth "
        "testing?\n\n"
        "Best regards,\n{am_name}"
    ),
}
CONFIG_REVIEW = (
    "Hi {merchant_name} team,\n\n"
    "{issue_summary}. You already have {product_name} enabled, so this looks like a configuration "
    "question rather than a missing product. We estimate about {monthly_inr_at_risk} a month at risk "
    "({recoverable_inr_monthly} recoverable).\n\n"
    "Could we review your {product_name} setup together for this traffic?\n\n"
    "Best,\n{am_name}"
)


class TemplateError(ValueError):
    pass


def values_for(lk: dict) -> dict[str, str]:
    s, d, det = lk["stats"], lk["dimension"], lk["detector"]
    v = {
        "merchant_name": lk["merchant_name"],
        "am_name": lk["am_owner"],
        "monthly_inr_at_risk": fmt_inr_short(lk["monthly_inr_at_risk"]),
        "recoverable_inr_monthly": fmt_inr_short(lk["recoverable_inr_monthly"]),
        "product_name": lk["fit_product_info"]["name"],
        "product_value": lk["fit_product_info"]["one_line_value"],
        "days_observed": str(lk["assumptions_used"]["days_observed"]),
        "issue_summary": lk["headline"],
    }
    if det == "d1_bank_spike":
        v.update({
            "method": d["method"].replace("_", " "), "bank": d["issuer_bank"], "window": d["window"]["label"],
            "failure_rate": fmt_pct(s["failure_rate"]), "baseline_rate": fmt_pct(s["baseline_rate"]),
            "issue_detail": (f"{d['method'].replace('_', ' ')} payments from {d['issuer_bank']} "
                             f"({d['window']['label']}) fail at {fmt_pct(s['failure_rate'])}, against "
                             f"{fmt_pct(s['baseline_rate'])} for the same method on other banks"),
        })
    elif det == "d2_upi_collect":
        v.update({
            "collect_share": fmt_pct(s["collect_share"]), "intent_sr": fmt_pct(s["intent_sr"]),
            "collect_sr": fmt_pct(s["collect_sr"]),
            "issue_detail": (f"{fmt_pct(s['collect_share'])} of your UPI payments use the collect flow, which "
                             f"succeeds {fmt_pct(s['collect_sr'])} of the time versus {fmt_pct(s['intent_sr'])} "
                             f"for UPI intent"),
        })
    else:
        v.update({
            "recurring_fail_rate": fmt_pct(s["recurring_fail_rate"]), "baseline_rate": fmt_pct(s["baseline_rate"]),
            "annual_churn_inr": fmt_inr_short(lk["annual_churn_inr"]),
            "issue_detail": (f"{fmt_pct(s['recurring_fail_rate'])} of subscription renewals fail for system "
                             f"reasons such as pre-debit notification issues, against a typical "
                             f"{fmt_pct(s['baseline_rate'])}"),
        })
    return v


def placeholders_for(lk: dict) -> list[str]:
    return sorted(values_for(lk))


def render(lk: dict, tone: str, template: str | None) -> str:
    if template is None:
        template = CONFIG_REVIEW if lk["recommendation_type"] == "config_review" else BUILTIN[tone]
    vals = values_for(lk)
    unknown = sorted({m for m in PLACEHOLDER.findall(template) if m not in vals})
    if unknown:
        raise TemplateError(f"unknown placeholders {unknown}; allowed: {sorted(vals)}")
    r = number_guard.check(template, set())
    if not r.ok:
        raise TemplateError(f"template contains raw numbers {r.violations}; use placeholders such as "
                            f"{{monthly_inr_at_risk}} instead")
    try:
        names = {f for _, f, _, _ in Formatter().parse(template) if f}
        return template.format(**{k: vals[k] for k in names})
    except (ValueError, KeyError, IndexError) as e:
        raise TemplateError(f"could not fill template: {e}") from None
