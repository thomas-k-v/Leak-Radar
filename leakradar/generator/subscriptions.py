"""Subscriptions + monthly recurring mandate debits for one merchant.

Customers are either "INSUFFICIENT_FUNDS-prone" (hidden flag) or normal. A planted D3 leak adds
extra MANDATE_PRENOTIFY_FAILED failures only for *normal* customers — i.e. customers whose
history shows no funding problem — which is what makes the churn "silent".
Recurring debits are never retried (that missing retry logic is the leak).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .payments import Calendar, _choice_by_group
from .specs import MerchantSpec


def generate_merchant_subscriptions(s: MerchantSpec, cfg: dict, cfg_err: dict, cal: Calendar,
                                    rng: np.random.Generator) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    sc = cfg["subscriptions"]
    n = s.n_subs
    if n == 0:
        return pd.DataFrame(), pd.DataFrame(), {}
    plans = np.array(sc["plan_amounts"][s.category])
    plan = plans[rng.integers(0, len(plans), n)]
    start_offsets = rng.integers(-180, 60, n)           # days relative to window start
    start_dates = cal.start + pd.to_timedelta(start_offsets, unit="D")
    cycle_day = np.minimum(start_dates.day.to_numpy(), 28)
    methods = list(sc["method_mix"])
    mp = np.array(list(sc["method_mix"].values()))
    sub_method = np.array(methods, dtype=object)[rng.choice(len(methods), size=n, p=mp / mp.sum())]
    sub_bank = np.array(cfg["banks"], dtype=object)[rng.choice(len(cfg["banks"]), size=n, p=s.bank_weights)]
    insf_prone = rng.random(n) < s.insf_prone_share
    sub_ids = np.array([f"sub_{s.merchant_id}_{i:05d}" for i in range(n)], dtype=object)
    cust_ids = np.array([f"{s.merchant_id}-S{i:05d}" for i in range(n)], dtype=object)

    # Debit dates: cycle day of every month that falls inside the window, on/after start date.
    end = cal.start + pd.Timedelta(days=cal.days)
    months = pd.period_range(cal.start, end - pd.Timedelta(days=1), freq="M")
    rows_sub, rows_day = [], []
    for mo in months:
        dates = pd.to_datetime([f"{mo.year}-{mo.month:02d}-{d:02d}" for d in cycle_day])
        ok = (dates >= cal.start) & (dates < end) & (dates >= start_dates)
        idx = np.nonzero(ok)[0]
        rows_sub.append(idx)
        rows_day.append(((dates[ok] - cal.start).days).to_numpy())
    si = np.concatenate(rows_sub)
    day = np.concatenate(rows_day)
    k = len(si)
    hour = rng.integers(6, 11, k)                         # mandate debits run early morning
    sec = rng.integers(0, 3600, k)
    amount = plan[si].astype(np.int64)
    prone = insf_prone[si]

    p_cust = np.where(prone, sc["insf_prone_fail_prob"], sc["normal_customer_fail_prob"])
    p_sys = np.full(k, s.sub_system_fail)
    p_spike = np.zeros(k)
    if s.d3_excess is not None:
        p_spike = np.where(prone, 0.0, s.d3_excess)
    u = rng.random(k)
    is_cust = u < p_cust
    is_sys = (~is_cust) & (u < p_cust + p_sys)
    is_spike = (~is_cust) & (~is_sys) & (u < p_cust + p_sys + p_spike)
    codes = np.full(k, None, dtype=object)
    codes[is_cust] = np.where(rng.random(int(is_cust.sum())) < 0.85, "INSUFFICIENT_FUNDS", "USER_CANCELLED")
    if is_sys.any():
        codes[is_sys] = _choice_by_group(rng, np.full(int(is_sys.sum()), "r", dtype=object),
                                         {"r": cfg_err["recurring_system_code_mix"]})
    codes[is_spike] = "MANDATE_PRENOTIFY_FAILED"

    truth = {}
    if s.d3_excess is not None:
        truth["d3_expected_system_fail_inr"] = float(((p_sys + p_spike) * amount).sum())
        truth["d3_debits"] = int(k)

    mob = np.where(rng.random(k) < s.mobile_share, "mobile", "desktop").astype(object)
    pay = pd.DataFrame({
        "day": day, "hour": hour, "sec": sec, "method": sub_method[si], "issuer_bank": sub_bank[si],
        "device": mob, "amount_inr": amount, "error_code": codes, "attempt_no": 1,
        "order_id": [f"{s.merchant_id}-R{i:06d}" for i in range(k)],
        "customer_id": cust_ids[si], "is_recurring": 1, "subscription_id": sub_ids[si],
    })

    # Subscription status: past_due if the most recent debit in the window failed.
    last = pay.sort_values(["day"]).groupby("subscription_id").tail(1)
    failed_last = set(last.loc[last["error_code"].notna(), "subscription_id"])
    status = np.array(["past_due" if sid in failed_last else "active" for sid in sub_ids], dtype=object)
    subs = pd.DataFrame({
        "subscription_id": sub_ids, "merchant_id": s.merchant_id, "customer_id": cust_ids,
        "plan_amount_inr": plan.astype(np.int64), "cycle": "monthly",
        "start_date": start_dates.strftime("%Y-%m-%d"), "status": status,
    })
    return subs, pay, truth
