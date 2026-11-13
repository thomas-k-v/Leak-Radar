"""Vectorised generation of one merchant's non-recurring payments (first attempts + retries).

Failure model per row (probabilities):
    p_customer  customer-caused failure (INSUFFICIENT_FUNDS / USER_CANCELLED) — never a leak
    p_system    baseline system failure = (1 - baseline_sr) - p_customer + merchant jitter
                + sale-day degradation + decoy noise
    p_spike     extra failure from a planted D1 spike (0 outside the planted cell/window)
One uniform draw u decides: u < p_c → customer; < p_c+p_s → system; < p_c+p_s+p_spike → spike.

Ground truth is computed from these *true probabilities* over the realised traffic, not from
realised outcomes, so the eval measures detector error against the planted effect.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .specs import METHODS, MerchantSpec


@dataclass
class Calendar:
    start: pd.Timestamp
    days: int
    sale_idx: np.ndarray          # day indexes that are sale days
    sale_mult: np.ndarray         # traffic multiplier per day (1.0 on normal days)
    dow: np.ndarray               # day-of-week per day index (Mon=0)


def build_calendar(cfg: dict, days: int, rng: np.random.Generator) -> Calendar:
    start = pd.Timestamp(cfg["start_date"])
    dates = pd.date_range(start, periods=days, freq="D")
    sale = np.array([i for i, d in enumerate(dates) if d.strftime("%Y-%m-%d") in set(cfg["sale_days"])], dtype=int)
    mult = np.ones(days)
    mult[sale] = rng.uniform(*cfg["sale_traffic_mult"], size=len(sale))
    return Calendar(start=start, days=days, sale_idx=sale, sale_mult=mult, dow=dates.day_of_week.to_numpy())


def _choice_by_group(rng: np.random.Generator, groups: np.ndarray, mixes: dict[str, dict[str, float]]) -> np.ndarray:
    """Draw a code for each row from the mix of its group (e.g. method)."""
    out = np.empty(len(groups), dtype=object)
    for g, mix in mixes.items():
        m = groups == g
        k = int(m.sum())
        if k:
            codes = list(mix)
            p = np.array(list(mix.values()), dtype=float)
            out[m] = np.array(codes, dtype=object)[rng.choice(len(codes), size=k, p=p / p.sum())]
    return out


def _method_probs(s: MerchantSpec) -> np.ndarray:
    collect = s.d2_collect_share if s.d2_collect_share is not None else s.collect_share
    upi = s.upi_share
    intl = s.intl_share
    rest = max(0.0, 1.0 - upi - intl)
    p = np.array([upi * (1 - collect), upi * collect, rest * s.card_share_of_rest,
                  rest * (1 - s.card_share_of_rest), intl])
    return p / p.sum()


def _row_probs(s: MerchantSpec, cfg: dict, cal: Calendar, method: np.ndarray, bank: np.ndarray,
               day: np.ndarray, hour: np.ndarray, day_noise: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    sr = np.array([cfg["baseline_sr"][m] for m in METHODS])
    pc = np.array([cfg["customer_fail_prob"][m] for m in METHODS])
    jit = np.array([s.sr_noise[m] for m in METHODS])
    midx = pd.Index(METHODS).get_indexer(method)
    p_cust = pc[midx]
    p_sys = (1 - sr[midx]) - p_cust + jit[midx] + day_noise[day]
    is_sale = np.isin(day, cal.sale_idx)
    p_sys = p_sys + is_sale * cfg["sale_fail_degrade_pts"] / 100
    p_spike = np.zeros(len(method))
    if s.d1 is not None:
        mask = (method == s.d1.method) & (bank == s.d1.bank)
        if s.d1.window_kind == "hours":
            mask &= (hour >= s.d1.hours[0]) & (hour < s.d1.hours[1])
        else:
            mask &= is_sale
        p_spike = mask * s.d1.excess
    if s.decoy == "random_bad_days":
        dp = s.decoy_params
        mask = (method == dp["method"]) & (bank == dp["bank"]) & np.isin(day, dp["day_idx"])
        p_spike = p_spike + mask * dp["excess"]
    p_sys = np.clip(p_sys, 0.003, 0.9)
    return p_cust, p_sys, p_spike


def _outcomes(rng, cfg_err, method, p_cust, p_sys, p_spike):
    u = rng.random(len(method))
    is_cust = u < p_cust
    is_sys = (~is_cust) & (u < p_cust + p_sys)
    is_spike = (~is_cust) & (~is_sys) & (u < p_cust + p_sys + p_spike)
    codes = np.full(len(method), None, dtype=object)
    if is_cust.any():
        codes[is_cust] = _choice_by_group(rng, np.full(int(is_cust.sum()), "c", dtype=object),
                                          {"c": cfg_err["customer_code_mix"]})
    if is_sys.any():
        codes[is_sys] = _choice_by_group(rng, method[is_sys], cfg_err["system_code_mix"])
    if is_spike.any():
        codes[is_spike] = _choice_by_group(rng, np.full(int(is_spike.sum()), "s", dtype=object),
                                           {"s": cfg_err["spike_code_mix"]})
    return codes, is_sys | is_spike


def generate_merchant_payments(s: MerchantSpec, cfg: dict, cfg_err: dict, cal: Calendar,
                               rng: np.random.Generator) -> tuple[pd.DataFrame, dict]:
    days = cal.days
    banks = np.array(cfg["banks"], dtype=object)

    # 1. Traffic: Poisson counts on a (day × hour) intensity grid.
    w_day = np.array(cfg["weekday_mult"])[cal.dow] * cal.sale_mult * rng.lognormal(0, cfg["day_noise_sd"], days)
    hp = np.array(cfg["hour_profile"])
    lam = np.outer(w_day, hp)
    lam = s.rows * lam / lam.sum()
    counts = rng.poisson(lam)
    day = np.repeat(np.repeat(np.arange(days), 24), counts.ravel())
    hour = np.repeat(np.tile(np.arange(24), days), counts.ravel())
    n = len(day)
    sec = rng.integers(0, 3600, n)

    # 2. Attributes.
    method = np.array(METHODS, dtype=object)[rng.choice(5, size=n, p=_method_probs(s))]
    bank = banks[rng.choice(len(banks), size=n, p=s.bank_weights)]
    mob = rng.random(n)
    mobile_p = np.where(method == "upi_intent", 1.0,
                        np.where(method == "upi_collect", s.collect_mobile_share, s.mobile_share))
    device = np.where(mob < mobile_p, "mobile", "desktop").astype(object)
    mu = np.log(s.ticket) - cfg["ticket_sigma"] ** 2 / 2
    amount = np.maximum(10, rng.lognormal(mu, cfg["ticket_sigma"], n)).round().astype(np.int64)

    day_noise = np.zeros(days)
    if s.decoy == "low_volume_noisy":
        day_noise = rng.normal(0, s.decoy_params["day_noise_sd"], days)

    # 3. Outcomes for first attempts.
    p_cust, p_sys, p_spike = _row_probs(s, cfg, cal, method, bank, day, hour, day_noise)
    codes, sys_failed = _outcomes(rng, cfg_err, method, p_cust, p_sys, p_spike)

    # Ground-truth contributions (true probabilities × realised traffic).
    truth: dict = {}
    if s.d1 is not None:
        truth["d1_expected_excess_inr"] = float((p_spike * amount).sum())
        truth["d1_planted_attempts"] = int((p_spike > 0).sum())
    if s.d2_collect_share is not None:
        mob_collect = (method == "upi_collect") & (device == "mobile")
        p_c_i, p_s_i, _ = _row_probs(s, cfg, cal, np.full(n, "upi_intent", dtype=object), bank, day, hour, day_noise)
        sr_intent = 1 - p_c_i - p_s_i
        sr_collect = 1 - p_cust - p_sys - p_spike
        truth["d2_expected_gap_inr"] = float(((sr_intent - sr_collect) * amount * mob_collect).sum())
        truth["d2_mobile_collect_attempts"] = int(mob_collect.sum())

    first = pd.DataFrame({
        "day": day, "hour": hour, "sec": sec, "method": method, "issuer_bank": bank, "device": device,
        "amount_inr": amount, "error_code": codes, "attempt_no": 1,
    })
    first["order_id"] = [f"{s.merchant_id}-O{i:06d}" for i in range(n)]
    cust_pool = max(50, n // 2)
    first["customer_id"] = [f"{s.merchant_id}-C{c:05d}" for c in rng.integers(0, cust_pool, n)]

    # 4. Retries for a share of system failures (customer retries the same order a few minutes later).
    rc = cfg["retries"]
    retry_mask = sys_failed & (rng.random(n) < rc["retry_prob_after_system_fail"])
    r = first[retry_mask].copy()
    if len(r):
        k = len(r)
        same = rng.random(k) < rc["same_method_prob"]
        alt = np.array(METHODS, dtype=object)[rng.choice(5, size=k, p=_method_probs(s))]
        r["method"] = np.where(same, r["method"].to_numpy(), alt)
        r.loc[r["method"] == "upi_intent", "device"] = "mobile"
        delay = rng.integers(rc["delay_minutes"][0] * 60, rc["delay_minutes"][1] * 60, k)
        total_sec = r["hour"].to_numpy() * 3600 + r["sec"].to_numpy() + delay
        overflow = total_sec >= 24 * 3600
        total_sec = np.where(overflow, 24 * 3600 - 1, total_sec)
        r["hour"] = total_sec // 3600
        r["sec"] = total_sec % 3600
        rp = _row_probs(s, cfg, cal, r["method"].to_numpy(), r["issuer_bank"].to_numpy(),
                        r["day"].to_numpy(), r["hour"].to_numpy(), day_noise)
        r_codes, _ = _outcomes(rng, cfg_err, r["method"].to_numpy(), *rp)
        r["error_code"] = r_codes
        r["attempt_no"] = 2
        df = pd.concat([first, r], ignore_index=True)
    else:
        df = first
    df["is_recurring"] = 0
    df["subscription_id"] = None
    return df, truth
