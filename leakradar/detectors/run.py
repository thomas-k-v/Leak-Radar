"""python -m leakradar.detectors.run — runs D1–D3 + recommender and rewrites the leaks table."""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import datetime, timezone

import pandas as pd

from leakradar.common import config
from leakradar.common.db import connect
from leakradar.common.models import Leak

from . import d1_bank_spike, d2_upi_collect, d3_subscription_churn
from .base import load_frames
from .recommender import recommend

DETECTORS = [d1_bank_spike, d2_upi_collect, d3_subscription_churn]
LEAK_COLS = ["leak_id", "merchant_id", "detector", "severity", "monthly_inr_at_risk", "recoverable_inr_monthly",
             "annual_churn_inr", "assumptions_used", "evidence_query", "dimension", "stats", "root_cause",
             "recommended_product", "recommendation_type", "fit_product", "created_at"]


def run_all(payments: pd.DataFrame, merchants: pd.DataFrame, assumptions: dict, catalogue: dict) -> list[Leak]:
    leaks: list[Leak] = []
    for d in DETECTORS:
        leaks.extend(d.detect(payments, merchants, assumptions))
    products = dict(zip(merchants["merchant_id"], merchants["products_active"]))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for lk in leaks:
        recommend(lk, products[lk.merchant_id], catalogue)
        lk.created_at = now
    # Deterministic IDs: rank by recoverable ₹ desc, then merchant, detector, dimension.
    leaks.sort(key=lambda x: (-x.recoverable_inr_monthly, x.merchant_id, x.detector,
                              json.dumps(x.dimension, sort_keys=True)))
    for i, lk in enumerate(leaks, start=1):
        lk.leak_id = f"L{i:04d}"
    return leaks


def write_leaks(conn: sqlite3.Connection, leaks: list[Leak]) -> None:
    conn.execute("DELETE FROM pitches")
    conn.execute("DELETE FROM leaks")
    rows = []
    for lk in leaks:
        d = lk.model_dump()
        rows.append(tuple(json.dumps(d[c]) if isinstance(d[c], (dict, list)) else d[c] for c in LEAK_COLS))
    conn.executemany(f"INSERT INTO leaks ({','.join(LEAK_COLS)}) VALUES ({','.join('?' * len(LEAK_COLS))})", rows)
    conn.commit()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    args = ap.parse_args()
    t0 = time.time()
    conn = connect(args.db)
    payments, merchants = load_frames(conn)
    leaks = run_all(payments, merchants, config.assumptions(), config.products())
    write_leaks(conn, leaks)
    by = pd.Series([lk.detector for lk in leaks]).value_counts().to_dict() if leaks else {}
    print(f"leaks: {len(leaks)} {by}  ({time.time() - t0:.1f}s)")


if __name__ == "__main__":
    main()
