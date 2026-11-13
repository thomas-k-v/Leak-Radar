"""Shared detector helpers: data loading, BH correction, severity, root-cause splits."""
from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

LAYERS = ["merchant", "gateway", "bank", "network", "customer"]


def load_frames(conn: sqlite3.Connection) -> tuple[pd.DataFrame, pd.DataFrame]:
    payments = pd.read_sql_query("SELECT * FROM payments", conn)
    merchants = pd.read_sql_query("SELECT * FROM merchants", conn)
    return prepare_payments(payments), merchants


def prepare_payments(p: pd.DataFrame) -> pd.DataFrame:
    """Adds derived columns detectors need (date, hour). Idempotent."""
    if "date" not in p.columns:
        p = p.copy()
        p["date"] = p["ts"].str.slice(0, 10)
        p["hour"] = p["ts"].str.slice(11, 13).astype(int)
    return p


def days_observed(p: pd.DataFrame) -> int:
    d = pd.to_datetime(p["date"])
    return int((d.max() - d.min()).days) + 1


def benjamini_hochberg(p: np.ndarray) -> np.ndarray:
    """BH-adjusted p-values (monotone, capped at 1)."""
    p = np.asarray(p, dtype=float)
    n = len(p)
    if n == 0:
        return p
    order = np.argsort(p)
    ranked = p[order] * n / np.arange(1, n + 1)
    adj = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(n)
    out[order] = np.minimum(adj, 1.0)
    return out


def severity(recoverable_inr: float, cfg_common: dict) -> str:
    th = cfg_common["severity_inr"]
    if recoverable_inr >= th["high"]:
        return "high"
    if recoverable_inr >= th["medium"]:
        return "medium"
    return "low"


def layer_split(rows: pd.DataFrame) -> dict[str, float]:
    """Share of failed rows by error_layer (customer included so the card can show it separately)."""
    failed = rows[rows["status"] == "failed"]
    if failed.empty:
        return {k: 0.0 for k in LAYERS}
    vc = failed["error_layer"].value_counts(normalize=True)
    return {k: round(float(vc.get(k, 0.0)), 4) for k in LAYERS}


def top_codes(rows: pd.DataFrame, k: int = 4) -> dict[str, int]:
    failed = rows[rows["status"] == "failed"]
    return {str(c): int(n) for c, n in failed["error_code"].value_counts().head(k).items()}
