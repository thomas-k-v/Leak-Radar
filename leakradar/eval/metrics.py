"""Matching detected leaks to planted ground truth, and computing metrics.

Match keys:
  d1_bank_spike          (merchant_id, method, issuer_bank) — right merchant, wrong bank = FP + FN
  d2_upi_collect         (merchant_id)
  d3_subscription_churn  (merchant_id)
₹ error: |detected monthly_inr_at_risk − expected_monthly_inr| / expected_monthly_inr, on true positives.
"""
from __future__ import annotations

import statistics
from typing import Any

from leakradar.common.models import DETECTORS

TARGETS = {"precision": 0.85, "recall": 0.85, "median_abs_pct_error": 0.20}


def match_key(detector: str, merchant_id: str, dim: dict[str, Any]) -> tuple:
    if detector == "d1_bank_spike":
        return (merchant_id, dim.get("method"), dim.get("issuer_bank"))
    return (merchant_id,)


def planted_key(p: dict) -> tuple:
    pp = p["planted_params"]
    if p["detector"] == "d1_bank_spike":
        return (p["merchant_id"], pp["method"], pp["issuer_bank"])
    return (p["merchant_id"],)


def evaluate(leaks: list[dict], ground_truth: dict) -> dict:
    """leaks: dicts with merchant_id, detector, dimension, monthly_inr_at_risk, recommendation_type."""
    decoy_ids = {d["merchant_id"] for d in ground_truth["decoys"]}
    out: dict[str, Any] = {"per_detector": {}, "targets": TARGETS}
    tot_tp = tot_fp = tot_fn = 0
    all_errors: list[float] = []
    rec_correct = rec_total = 0
    for det in DETECTORS:
        planted = {planted_key(p): p for p in ground_truth["planted"] if p["detector"] == det}
        found = {}
        for lk in leaks:
            if lk["detector"] == det:
                found[match_key(det, lk["merchant_id"], lk["dimension"])] = lk
        tp_keys = planted.keys() & found.keys()
        fp_keys = found.keys() - planted.keys()
        fn_keys = planted.keys() - found.keys()
        errors = []
        rows = []
        for k in sorted(tp_keys):
            exp = planted[k]["expected_monthly_inr"]
            est = found[k]["monthly_inr_at_risk"]
            err = abs(est - exp) / exp if exp else 0.0
            errors.append(err)
            want = "config_review" if planted[k]["product_already_active"] else "cross_sell"
            rec_total += 1
            rec_correct += int(found[k]["recommendation_type"] == want)
            rows.append({"key": list(k), "expected_inr": exp, "detected_inr": est, "abs_pct_error": round(err, 4),
                         "recommendation_ok": found[k]["recommendation_type"] == want})
        tp, fp, fn = len(tp_keys), len(fp_keys), len(fn_keys)
        prec = tp / (tp + fp) if tp + fp else 1.0
        rec = tp / (tp + fn) if tp + fn else 1.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        med = statistics.median(errors) if errors else None
        out["per_detector"][det] = {
            "planted": len(planted), "detected": len(found), "tp": tp, "fp": fp, "fn": fn,
            "precision": round(prec, 4), "recall": round(rec, 4), "f1": round(f1, 4),
            "median_abs_pct_error": round(med, 4) if med is not None else None,
            "fp_on_decoys": sum(1 for k in fp_keys if k[0] in decoy_ids),
            "false_positives": [list(k) for k in sorted(fp_keys)],
            "missed": [list(k) for k in sorted(fn_keys)],
            "matches": rows,
            "meets_targets": prec >= TARGETS["precision"] and rec >= TARGETS["recall"]
                             and (med is None or med <= TARGETS["median_abs_pct_error"]),
        }
        tot_tp, tot_fp, tot_fn = tot_tp + tp, tot_fp + fp, tot_fn + fn
        all_errors += errors
    out["overall"] = {
        "planted": tot_tp + tot_fn, "found": tot_tp, "false_positives": tot_fp,
        "fp_on_decoys": sum(v["fp_on_decoys"] for v in out["per_detector"].values()),
        "precision": round(tot_tp / (tot_tp + tot_fp), 4) if tot_tp + tot_fp else 1.0,
        "recall": round(tot_tp / (tot_tp + tot_fn), 4) if tot_tp + tot_fn else 1.0,
        "median_abs_pct_error": round(statistics.median(all_errors), 4) if all_errors else None,
        "recommendation_accuracy": round(rec_correct / rec_total, 4) if rec_total else None,
    }
    out["meets_all_targets"] = all(v["meets_targets"] for v in out["per_detector"].values())
    return out
