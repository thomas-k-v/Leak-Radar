"""D2 — UPI Collect used where Intent would work → Optimised checkout (UPI Intent).

Per merchant (first attempts, non-recurring UPI):
    collect_share = collect_attempts / upi_attempts
    sr_gap        = intent_sr − collect_sr   (merchant's own intent SR; platform median if too few)
Flag if collect_share ≥ min_collect_share AND sr_gap ≥ min_sr_gap AND upi_attempts ≥ min_upi_attempts.

Sizing (HANDOFF §7 gives only `recoverable`; at_risk is the same expression without switchable_share):
    monthly_inr_at_risk = collect_attempts_on_mobile × sr_gap × avg_upi_ticket × (30 / days)
    recoverable         = monthly_inr_at_risk × switchable_share
Note: with baseline SRs of ~95% vs ~80% almost every merchant clears the sr_gap gate; the
collect_share gate is what actually separates leaks from normal merchants.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from leakradar.common.models import Leak

from .base import days_observed, layer_split, prepare_payments, severity, top_codes

DETECTOR = "d2_upi_collect"


def detect(payments: pd.DataFrame, merchants: pd.DataFrame, config: dict) -> list[Leak]:
    a = config[DETECTOR]
    common = config["common"]
    p = prepare_payments(payments)
    days = days_observed(p)
    u = p[(p["attempt_no"] == 1) & (p["is_recurring"] == 0) & p["method"].isin(["upi_intent", "upi_collect"])].copy()
    u["ok"] = u["status"] == "success"
    u["is_collect"] = u["method"] == "upi_collect"
    u["mob_collect"] = u["is_collect"] & (u["device"] == "mobile")

    g = u.groupby("merchant_id")
    per = pd.DataFrame({
        "upi_attempts": g.size(),
        "collect_attempts": g["is_collect"].sum(),
        "mobile_collect": g["mob_collect"].sum(),
        "avg_upi_ticket": g["amount_inr"].mean(),
    })
    intent = u[~u["is_collect"]].groupby("merchant_id")["ok"].agg(["size", "mean"])
    collect = u[u["is_collect"]].groupby("merchant_id")["ok"].agg(["size", "mean"])
    per["intent_attempts"] = intent["size"].reindex(per.index).fillna(0).astype(int)
    per["intent_sr_own"] = intent["mean"].reindex(per.index)
    per["collect_sr"] = collect["mean"].reindex(per.index)
    enough = per["intent_attempts"] >= a["min_intent_attempts_for_own_rate"]
    platform_intent_sr = float(per.loc[enough, "intent_sr_own"].median()) if enough.any() else np.nan
    per["intent_sr"] = np.where(enough, per["intent_sr_own"], platform_intent_sr)
    per["intent_sr_source"] = np.where(enough, "merchant", "platform_median")
    per["collect_share"] = per["collect_attempts"] / per["upi_attempts"]
    per["sr_gap"] = per["intent_sr"] - per["collect_sr"]

    flagged = per[(per["upi_attempts"] >= a["min_upi_attempts"])
                  & (per["collect_share"] >= a["min_collect_share"])
                  & (per["sr_gap"] >= a["min_sr_gap"])]
    scale = common["month_days"] / days
    leaks = []
    for mid, r in flagged.iterrows():
        at_risk = r["mobile_collect"] * r["sr_gap"] * r["avg_upi_ticket"] * scale
        recoverable = at_risk * a["switchable_share"]
        rows = u[u["merchant_id"] == mid]
        collect_rows = rows[rows["is_collect"]]
        intent_rows = rows[~rows["is_collect"]]
        leaks.append(Leak(
            merchant_id=mid, detector=DETECTOR, severity=severity(recoverable, common),
            monthly_inr_at_risk=round(at_risk), recoverable_inr_monthly=round(recoverable),
            assumptions_used={"switchable_share": a["switchable_share"], "month_days": common["month_days"],
                              "days_observed": days, "min_collect_share": a["min_collect_share"],
                              "min_sr_gap": a["min_sr_gap"], "min_upi_attempts": a["min_upi_attempts"]},
            evidence_query={"merchant_id": mid, "method": ["upi_collect"], "device": ["mobile"],
                            "is_recurring": 0, "attempt_no": 1},
            dimension={"method": "upi_collect", "device": "mobile"},
            stats={"upi_attempts": int(r["upi_attempts"]), "collect_attempts": int(r["collect_attempts"]),
                   "collect_attempts_on_mobile": int(r["mobile_collect"]),
                   "collect_share": round(float(r["collect_share"]), 4),
                   "intent_sr": round(float(r["intent_sr"]), 4), "collect_sr": round(float(r["collect_sr"]), 4),
                   "intent_sr_source": r["intent_sr_source"], "sr_gap": round(float(r["sr_gap"]), 4),
                   "sr_gap_pts": round(float(r["sr_gap"]) * 100, 2),
                   "avg_upi_ticket_inr": round(float(r["avg_upi_ticket"]))},
            root_cause={"cell": layer_split(collect_rows), "baseline": layer_split(intent_rows),
                        "top_codes": top_codes(collect_rows), "cell_label": "UPI Collect",
                        "baseline_label": "UPI Intent"},
            fit_product="checkout_upi_intent", score=float(r["collect_share"]),
        ))
    return leaks
