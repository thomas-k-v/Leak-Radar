"""D1 — bank/method failure spike → Optimizer (smart routing).

For every merchant × method × issuer_bank × time window, compare the system-failure rate
(customer-caused codes excluded) of that bank with the merchant's own rate for the same method
across all *other* banks in the same window (leave-one-out baseline), using a one-sided
two-proportion z-test.

Windows scanned (see challenge #1 in docs/DECISIONS.md): whole period, sale days (inferred from
platform traffic spikes), and sliding hour-of-day windows of 2/4/6/8 h at even start hours.
All tests are pooled into one Benjamini–Hochberg family. For each flagged
(merchant, method, bank) the window with the highest z is kept and sized.

Sizing (HANDOFF §7, locked):
    excess_failures      = attempts × (rate − baseline_rate)
    monthly_inr_at_risk  = excess_failures × avg_ticket × (30 / days_observed)
    recoverable          = at_risk × routing_recovery_rate × (1 − already_recovered_by_retry_share)
First attempts of non-recurring payments only (retries would double-count one order).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm

from leakradar.common.models import Leak

from .base import benjamini_hochberg, days_observed, layer_split, prepare_payments, severity, top_codes

DETECTOR = "d1_bank_spike"
KEY = ["merchant_id", "method", "issuer_bank"]


def detect_sale_days(p: pd.DataFrame, ratio: float) -> list[str]:
    daily = p.groupby("date").size()
    return sorted(daily[daily >= ratio * daily.median()].index.tolist())


def _windows(lengths: list[int]) -> list[dict]:
    wins = [{"kind": "all", "hours": None}, {"kind": "sale_days", "hours": None}]
    for L in lengths:
        for s in range(0, 24 - L + 1, 2):
            wins.append({"kind": "hours", "hours": (s, s + L)})
    return wins


def _window_label(w: dict, sale_dates: list[str]) -> str:
    if w["kind"] == "all":
        return "all hours, all days"
    if w["kind"] == "sale_days":
        return f"sale days ({len(sale_dates)} days)"
    return f"{w['hours'][0]:02d}:00–{w['hours'][1]:02d}:00 daily"


def _in_window(df: pd.DataFrame, w: dict) -> pd.Series:
    if w["kind"] == "all":
        return pd.Series(True, index=df.index)
    if w["kind"] == "sale_days":
        return df["is_sale"].astype(bool)
    return (df["hour"] >= w["hours"][0]) & (df["hour"] < w["hours"][1])


def detect(payments: pd.DataFrame, merchants: pd.DataFrame, config: dict) -> list[Leak]:
    a = config[DETECTOR]
    common = config["common"]
    p = prepare_payments(payments)
    days = days_observed(p)
    sale_dates = detect_sale_days(p, a["sale_day_traffic_ratio"])
    customer_codes = set(common["customer_codes"])

    order_ok = (p["status"] == "success").groupby(p["order_id"]).transform("max")
    first = p[(p["attempt_no"] == 1) & (p["is_recurring"] == 0)].copy()
    first["sys_fail"] = (first["status"] == "failed") & ~first["error_code"].isin(customer_codes)
    first["recovered"] = first["sys_fail"] & order_ok.loc[first.index].astype(bool)
    first["is_sale"] = first["date"].isin(sale_dates)

    grid = (first.groupby(KEY + ["is_sale", "hour"])
            .agg(a=("payment_id", "size"), f=("sys_fail", "sum"), amt=("amount_inr", "sum"),
                 rec=("recovered", "sum"))
            .reset_index())

    tests = []
    windows = _windows(a["window_lengths_h"])
    for wi, w in enumerate(windows):
        if w["kind"] == "sale_days" and not sale_dates:
            continue
        cell = grid[_in_window(grid, w)].groupby(KEY)[["a", "f", "amt", "rec"]].sum().reset_index()
        tot = cell.groupby(["merchant_id", "method"])[["a", "f"]].transform("sum")
        cell["base_a"] = tot["a"] - cell["a"]
        cell["base_f"] = tot["f"] - cell["f"]
        cell = cell[(cell["a"] >= a["min_attempts"]) & (cell["base_a"] >= a["min_baseline_attempts"])]
        if cell.empty:
            continue
        rate = cell["f"] / cell["a"]
        base = cell["base_f"] / cell["base_a"]
        pooled = (cell["f"] + cell["base_f"]) / (cell["a"] + cell["base_a"])
        se = np.sqrt(pooled * (1 - pooled) * (1 / cell["a"] + 1 / cell["base_a"]))
        z = np.where(se > 0, (rate - base) / se.replace(0, np.nan), 0.0)
        cell = cell.assign(rate=rate, base=base, z=np.nan_to_num(z), window=wi)
        cell["p"] = norm.sf(cell["z"])
        tests.append(cell)
    if not tests:
        return []
    allt = pd.concat(tests, ignore_index=True)
    # Hierarchical correction: overlapping windows of one cell are one hypothesis family.
    # cell p = min-p across its windows × windows tested (Bonferroni), then BH across cells.
    # (Plain BH over every window lets one real spike's many windows loosen the bar for noise.)
    cells = allt.groupby(KEY).agg(min_p=("p", "min"), n_windows=("p", "size")).reset_index()
    cells["p_cell"] = np.minimum(1.0, cells["min_p"] * cells["n_windows"])
    cells["p_adj"] = benjamini_hochberg(cells["p_cell"].to_numpy())
    allt = allt.merge(cells[KEY + ["n_windows", "p_cell", "p_adj"]], on=KEY)
    flagged = allt[(allt["p"] < a["max_p_value"]) & (allt["p_adj"] <= common["fdr_q"])
                   & ((allt["rate"] - allt["base"]) * 100 >= a["min_excess_pts"])]
    if flagged.empty:
        return []
    best = flagged.loc[flagged.groupby(KEY)["z"].idxmax()]

    by_merchant = {m: g for m, g in first.groupby("merchant_id")}
    leaks = []
    for r in best.itertuples(index=False):
        w = windows[int(r.window)]
        mrows = by_merchant[r.merchant_id]
        in_w = mrows[_in_window(mrows, w) & (mrows["method"] == r.method)]
        cell_rows = in_w[in_w["issuer_bank"] == r.issuer_bank]
        base_rows = in_w[in_w["issuer_bank"] != r.issuer_bank]

        excess_rate = float(r.rate - r.base)
        excess_failures = float(r.a) * excess_rate
        avg_ticket = float(r.amt) / float(r.a)
        retry_share = float(r.rec) / float(r.f) if r.f else 0.0
        scale = common["month_days"] / days
        at_risk = excess_failures * avg_ticket * scale
        recoverable = at_risk * a["routing_recovery_rate"] * (1 - retry_share)

        window = {"kind": w["kind"], "label": _window_label(w, sale_dates)}
        evidence = {"merchant_id": r.merchant_id, "method": [r.method], "issuer_bank": [r.issuer_bank],
                    "is_recurring": 0, "attempt_no": 1}
        if w["kind"] == "hours":
            window["hours"] = list(w["hours"])
            evidence["hours"] = list(w["hours"])
        elif w["kind"] == "sale_days":
            window["dates"] = sale_dates
            evidence["dates"] = sale_dates

        leaks.append(Leak(
            merchant_id=r.merchant_id, detector=DETECTOR,
            severity=severity(recoverable, common),
            monthly_inr_at_risk=round(at_risk), recoverable_inr_monthly=round(recoverable),
            assumptions_used={
                "routing_recovery_rate": a["routing_recovery_rate"],
                "already_recovered_by_retry_share": round(retry_share, 4),
                "month_days": common["month_days"], "days_observed": days,
                "min_attempts": a["min_attempts"], "min_excess_pts": a["min_excess_pts"],
                "max_p_value": a["max_p_value"], "fdr_q": common["fdr_q"],
            },
            evidence_query=evidence,
            dimension={"method": r.method, "issuer_bank": r.issuer_bank, "window": window},
            stats={
                "attempts": int(r.a), "system_failures": int(r.f),
                "failure_rate": round(float(r.rate), 4), "baseline_rate": round(float(r.base), 4),
                "baseline_attempts": int(r.base_a), "excess_pts": round(excess_rate * 100, 2),
                "excess_failures": round(excess_failures, 1), "avg_ticket_inr": round(avg_ticket),
                "z": round(float(r.z), 2), "p_value": float(f"{r.p:.3g}"),
                "windows_tested": int(r.n_windows), "p_cell": float(f"{r.p_cell:.3g}"),
                "p_adjusted": float(f"{r.p_adj:.3g}"), "cells_in_family": int(len(cells)),
            },
            root_cause={"cell": layer_split(cell_rows), "baseline": layer_split(base_rows),
                        "top_codes": top_codes(cell_rows)},
            fit_product="optimizer", score=float(r.z),
        ))
    return leaks
