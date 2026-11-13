"""python -m leakradar.generator --seed 42 --merchants 150 --days 90 --out data/"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from leakradar.common import config
from leakradar.common.db import connect, insert_many, set_meta

from .payments import build_calendar, generate_merchant_payments
from .specs import MerchantSpec, assign_plants, build_specs
from .subscriptions import generate_merchant_subscriptions

PAY_COLS = ["payment_id", "merchant_id", "order_id", "customer_id", "ts", "amount_inr", "method",
            "issuer_bank", "device", "status", "error_code", "error_layer", "is_recurring",
            "subscription_id", "attempt_no"]


def gmv_band(monthly_gmv: float) -> str:
    if monthly_gmv < 1e6:
        return "<10L"
    if monthly_gmv < 1e7:
        return "10L-1Cr"
    return "1Cr-10Cr"


def generate(seed: int, n_merchants: int, days: int) -> dict:
    cfg = config.generator()
    cfg_err = config.error_codes()
    layer_of = {c: v["layer"] for c, v in cfg_err["codes"].items()}
    root = np.random.SeedSequence(seed)
    global_ss, cal_ss, merch_ss = root.spawn(3)
    rng = np.random.default_rng(global_ss)
    cal = build_calendar(cfg, days, np.random.default_rng(cal_ss))
    specs = build_specs(cfg, n_merchants, rng)
    assign_plants(specs, cfg, rng)

    pay_frames, sub_frames, truths = [], [], {}
    for s, child in zip(specs, merch_ss.spawn(n_merchants)):
        k_pay, k_sub = child.spawn(2)
        r_pay, r_sub = np.random.default_rng(k_pay), np.random.default_rng(k_sub)
        p, t1 = generate_merchant_payments(s, cfg, cfg_err, cal, r_pay)
        subs, rp, t2 = generate_merchant_subscriptions(s, cfg, cfg_err, cal, r_sub)
        df = pd.concat([p, rp], ignore_index=True) if len(rp) else p
        ts = (cal.start + pd.to_timedelta(df["day"], unit="D")
              + pd.to_timedelta(df["hour"] * 3600 + df["sec"], unit="s"))
        df["ts"] = ts.dt.strftime("%Y-%m-%d %H:%M:%S")
        df = df.sort_values(["ts", "order_id", "attempt_no"], kind="stable").reset_index(drop=True)
        df["merchant_id"] = s.merchant_id
        df["payment_id"] = [f"pay_{s.merchant_id}_{i:06d}" for i in range(len(df))]
        df["status"] = np.where(df["error_code"].isna(), "success", "failed")
        df["error_layer"] = df["error_code"].map(layer_of)
        pay_frames.append(df[PAY_COLS])
        if len(subs):
            sub_frames.append(subs)
        truths[s.merchant_id] = {**t1, **t2}

    payments = pd.concat(pay_frames, ignore_index=True)
    subscriptions = pd.concat(sub_frames, ignore_index=True)
    ok = payments[payments["status"] == "success"].groupby("merchant_id")["amount_inr"].sum()
    merchants = pd.DataFrame([{
        "merchant_id": s.merchant_id, "name": s.name, "category": s.category, "city_tier": s.city_tier,
        "monthly_gmv_band": gmv_band(float(ok.get(s.merchant_id, 0)) * 30 / days),
        "has_subscriptions": int(s.has_subscriptions),
        "products_active": json.dumps(sorted(set(s.products_active))), "am_owner": s.am_owner,
    } for s in specs])
    gt = build_ground_truth(specs, truths, seed, days, cal)
    return {"merchants": merchants, "payments": payments, "subscriptions": subscriptions,
            "ground_truth": gt, "calendar": cal}


def build_ground_truth(specs: list[MerchantSpec], truths: dict, seed: int, days: int, cal) -> dict:
    scale = 30 / days
    planted, decoys = [], []
    sale_dates = [(cal.start + pd.Timedelta(days=int(i))).strftime("%Y-%m-%d") for i in cal.sale_idx]
    for s in specs:
        t = truths[s.merchant_id]
        if s.d1:
            planted.append({
                "merchant_id": s.merchant_id, "detector": "d1_bank_spike",
                "planted_params": {"method": s.d1.method, "issuer_bank": s.d1.bank,
                                   "excess_pts": round(s.d1.excess * 100, 2), "window_kind": s.d1.window_kind,
                                   "hours": list(s.d1.hours) if s.d1.hours else None,
                                   "dates": sale_dates if s.d1.window_kind == "sale_days" else None,
                                   "planted_attempts": t["d1_planted_attempts"]},
                "expected_monthly_inr": round(t["d1_expected_excess_inr"] * scale),
                "product_already_active": "optimizer" in s.products_active,
            })
        if s.d2_collect_share is not None:
            planted.append({
                "merchant_id": s.merchant_id, "detector": "d2_upi_collect",
                "planted_params": {"collect_share": round(s.d2_collect_share, 3),
                                   "mobile_share": s.collect_mobile_share,
                                   "mobile_collect_attempts": t["d2_mobile_collect_attempts"]},
                "expected_monthly_inr": round(t["d2_expected_gap_inr"] * scale),
                "product_already_active": "checkout_upi_intent" in s.products_active,
            })
        if s.d3_excess is not None:
            planted.append({
                "merchant_id": s.merchant_id, "detector": "d3_subscription_churn",
                "planted_params": {"excess_pts": round(s.d3_excess * 100, 2), "n_subs": s.n_subs,
                                   "debits": t["d3_debits"]},
                "expected_monthly_inr": round(t["d3_expected_system_fail_inr"] * scale),
                "product_already_active": "subscriptions_autopay_retries" in s.products_active,
            })
        if s.decoy:
            decoys.append({"merchant_id": s.merchant_id, "decoy_type": s.decoy,
                           "params": {k: v for k, v in s.decoy_params.items()}})
    return {"seed": seed, "days": days, "synthetic": True,
            "note": "Synthetic ground truth. expected_monthly_inr = true planted probabilities × realised traffic.",
            "planted": planted, "decoys": decoys}


def write(out: Path, result: dict, seed: int, days: int) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    db = out / "leakradar.db"
    if db.exists():
        db.unlink()
    conn = connect(db)
    m = result["merchants"]
    insert_many(conn, "merchants", list(m.columns), m.itertuples(index=False, name=None))
    p = result["payments"].astype(object).where(result["payments"].notna(), None)
    p["amount_inr"] = p["amount_inr"].astype(int)
    p["is_recurring"] = p["is_recurring"].astype(int)
    p["attempt_no"] = p["attempt_no"].astype(int)
    insert_many(conn, "payments", PAY_COLS, p.itertuples(index=False, name=None))
    s = result["subscriptions"]
    s = s.assign(plan_amount_inr=s["plan_amount_inr"].astype(int))
    insert_many(conn, "subscriptions", list(s.columns), s.itertuples(index=False, name=None))
    cal = result["calendar"]
    set_meta(conn, seed=seed, days=days, synthetic=True,
             start_date=cal.start.strftime("%Y-%m-%d"),
             end_date=(cal.start + pd.Timedelta(days=days - 1)).strftime("%Y-%m-%d"),
             data_hash=data_hash(result))
    conn.commit()
    conn.close()
    with open(out / "ground_truth.json", "w", encoding="utf-8") as f:
        json.dump(result["ground_truth"], f, indent=2, sort_keys=True)
    return db


def data_hash(result: dict) -> str:
    h = hashlib.sha256()
    for key in ("merchants", "payments", "subscriptions"):
        h.update(pd.util.hash_pandas_object(result[key], index=False).values.tobytes())
    h.update(json.dumps(result["ground_truth"], sort_keys=True).encode())
    return h.hexdigest()


def print_summary(result: dict) -> None:
    p, m, gt = result["payments"], result["merchants"], result["ground_truth"]
    first = p[p["attempt_no"] == 1]
    print(f"merchants: {len(m)}  payments: {len(p):,}  subscriptions: {len(result['subscriptions']):,}")
    print("GMV bands:", m["monthly_gmv_band"].value_counts().to_dict())
    sr = first[first["is_recurring"] == 0].groupby("method")["status"].apply(lambda s: (s == "success").mean())
    print("first-attempt success rate by method:", {k: round(v, 3) for k, v in sr.items()})
    print("planted leaks:", pd.Series([x["detector"] for x in gt["planted"]]).value_counts().to_dict())
    print("decoys:", pd.Series([x["decoy_type"] for x in gt["decoys"]]).value_counts().to_dict())


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate Leak Radar synthetic data")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--merchants", type=int, default=150)
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--out", type=Path, default=config.data_dir())
    a = ap.parse_args()
    t0 = time.time()
    result = generate(a.seed, a.merchants, a.days)
    db = write(a.out, result, a.seed, a.days)
    print_summary(result)
    print(f"data hash: {data_hash(result)[:16]}  wrote {db} in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
