"""D3 — subscription silent churn → Subscriptions smart retries.

Recurring debits only. Failures split into involuntary-system codes and customer codes.
Platform baseline = median system-failure rate across merchants with enough debits (median, not
pooled, so planted merchants don't drag the baseline up). One-sided binomial test vs baseline,
Benjamini–Hochberg across merchants.

Flag if (rate − baseline) ≥ min_excess_pts AND system_failed_renewals ≥ min_failed_renewals
AND BH-adjusted p ≤ fdr_q.

Sizing (HANDOFF §7, locked):
    monthly_inr_at_risk = system_failed_renewals × plan_amount × (30 / days)   [Σ failed debit amounts]
    annual_churn_inr    = monthly_inr_at_risk × 12 × churn_given_failure
    recoverable         = monthly_inr_at_risk × retry_recovery_rate
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import binom

from leakradar.common.models import Leak

from .base import benjamini_hochberg, days_observed, layer_split, prepare_payments, severity, top_codes

DETECTOR = "d3_subscription_churn"


def detect(payments: pd.DataFrame, merchants: pd.DataFrame, config: dict) -> list[Leak]:
    a = config[DETECTOR]
    common = config["common"]
    p = prepare_payments(payments)
    days = days_observed(p)
    rec = p[p["is_recurring"] == 1].copy()
    if rec.empty:
        return []
    sys_codes = set(a["system_codes"])
    cust_codes = set(common["customer_codes"])
    rec["sys_fail"] = rec["error_code"].isin(sys_codes)
    rec["cust_fail"] = rec["error_code"].isin(cust_codes)
    rec["sys_amt"] = np.where(rec["sys_fail"], rec["amount_inr"], 0)

    g = rec.groupby("merchant_id")
    per = pd.DataFrame({"debits": g.size(), "sys_fails": g["sys_fail"].sum(), "cust_fails": g["cust_fail"].sum(),
                        "sys_amt": g["sys_amt"].sum()})
    per["rate"] = per["sys_fails"] / per["debits"]
    elig = per["debits"] >= a["min_debits_for_baseline"]
    if not elig.any():
        return []
    baseline = float(per.loc[elig, "rate"].median())
    per = per[elig].copy()
    per["p"] = binom.sf(per["sys_fails"] - 1, per["debits"], baseline)
    per["p_adj"] = benjamini_hochberg(per["p"].to_numpy())
    flagged = per[((per["rate"] - baseline) * 100 >= a["min_excess_pts"])
                  & (per["sys_fails"] >= a["min_failed_renewals"])
                  & (per["p_adj"] <= common["fdr_q"])]

    # Customers with any INSUFFICIENT_FUNDS anywhere in their history.
    insf_customers = set(p.loc[p["error_code"] == "INSUFFICIENT_FUNDS", "customer_id"])
    scale = common["month_days"] / days
    leaks = []
    for mid, r in flagged.iterrows():
        rows = rec[rec["merchant_id"] == mid]
        failed_sys = rows[rows["sys_fail"]]
        clean_share = float((~failed_sys["customer_id"].isin(insf_customers)).mean()) if len(failed_sys) else 0.0
        at_risk = float(r["sys_amt"]) * scale
        annual = at_risk * 12 * a["churn_given_failure"]
        recoverable = at_risk * a["retry_recovery_rate"]
        leaks.append(Leak(
            merchant_id=mid, detector=DETECTOR, severity=severity(recoverable, common),
            monthly_inr_at_risk=round(at_risk), recoverable_inr_monthly=round(recoverable),
            annual_churn_inr=round(annual),
            assumptions_used={"churn_given_failure": a["churn_given_failure"],
                              "retry_recovery_rate": a["retry_recovery_rate"],
                              "month_days": common["month_days"], "days_observed": days,
                              "min_excess_pts": a["min_excess_pts"], "min_failed_renewals": a["min_failed_renewals"],
                              "fdr_q": common["fdr_q"], "system_codes": sorted(sys_codes)},
            evidence_query={"merchant_id": mid, "is_recurring": 1, "error_code": sorted(sys_codes)},
            dimension={"scope": "recurring_debits"},
            stats={"recurring_debits": int(r["debits"]), "system_failed_renewals": int(r["sys_fails"]),
                   "customer_failed_renewals": int(r["cust_fails"]),
                   "recurring_fail_rate": round(float(r["rate"]), 4), "baseline_rate": round(baseline, 4),
                   "excess_pts": round((float(r["rate"]) - baseline) * 100, 2),
                   "avg_failed_plan_inr": round(float(r["sys_amt"]) / max(1, int(r["sys_fails"]))),
                   "clean_history_share": round(clean_share, 4),
                   "p_value": float(f"{r['p']:.3g}"), "p_adjusted": float(f"{r['p_adj']:.3g}")},
            root_cause={"cell": layer_split(rows), "top_codes": top_codes(rows), "cell_label": "Recurring debits"},
            fit_product="subscriptions_autopay_retries", score=float(r["rate"] - baseline),
        ))
    return leaks
