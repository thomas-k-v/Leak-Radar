"""Read/write access used by both the REST API and the agent tools — one source for every number."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

import pandas as pd

from . import config
from .evidence import compile_where
from .models import DETECTOR_LABEL
from .money import fmt_inr, fmt_inr_short, fmt_pct

_JSON_COLS = ("assumptions_used", "evidence_query", "dimension", "stats", "root_cause")


class NotFound(LookupError):
    pass


def _leak_row(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    for c in _JSON_COLS:
        d[c] = json.loads(d[c])
    return d


def product_name(key: str) -> str:
    return config.products()["products"].get(key, {}).get("name", key)


def product_info(key: str) -> dict[str, Any]:
    p = config.products()["products"].get(key, {})
    return {"key": key, "name": p.get("name", key), "one_line_value": p.get("one_line_value", ""),
            "verified": p.get("verified", False)}


def headline(lk: dict) -> str:
    """Plain-language issue summary for a leak (no numbers — those come from fields)."""
    d, det = lk["dimension"], lk["detector"]
    if det == "d1_bank_spike":
        return (f"{d['method'].replace('_', ' ')} payments from {d['issuer_bank']} fail far more often than "
                f"other banks ({d['window']['label']})")
    if det == "d2_upi_collect":
        return "Many UPI payments use the collect flow on mobile, which fails more often than intent"
    return "Recurring debits fail for system reasons (mandate / pre-debit notification), not customer funds"


# ---------- merchants ----------

def get_merchant(conn: sqlite3.Connection, merchant_id: str) -> dict[str, Any]:
    r = conn.execute("SELECT * FROM merchants WHERE merchant_id = ?", (merchant_id,)).fetchone()
    if not r:
        raise NotFound(f"merchant {merchant_id} not found")
    d = dict(r)
    d["products_active"] = json.loads(d["products_active"])
    d["has_subscriptions"] = bool(d["has_subscriptions"])
    d["leak_ids"] = [x[0] for x in conn.execute(
        "SELECT leak_id FROM leaks WHERE merchant_id = ? ORDER BY recoverable_inr_monthly DESC", (merchant_id,))]
    return d


def merchant_names(conn: sqlite3.Connection) -> dict[str, str]:
    return {r[0]: r[1] for r in conn.execute("SELECT merchant_id, name FROM merchants")}


# ---------- leaks ----------

def get_leak(conn: sqlite3.Connection, leak_id: str) -> dict[str, Any]:
    r = conn.execute("SELECT * FROM leaks WHERE leak_id = ?", (leak_id,)).fetchone()
    if not r:
        raise NotFound(f"leak {leak_id} not found")
    lk = _leak_row(r)
    m = get_merchant(conn, lk["merchant_id"])
    lk["merchant_name"] = m["name"]
    lk["am_owner"] = m["am_owner"]
    lk["detector_label"] = DETECTOR_LABEL[lk["detector"]]
    lk["headline"] = headline(lk)
    lk["fit_product_info"] = product_info(lk["fit_product"])
    lk["recommended_product_info"] = product_info(lk["recommended_product"])
    lk["calculation"] = calculation(lk)
    return lk


def leak_exists(conn: sqlite3.Connection, leak_id: str) -> str | None:
    r = conn.execute("SELECT detector FROM leaks WHERE leak_id = ?", (leak_id,)).fetchone()
    return r[0] if r else None


def list_leaks(conn: sqlite3.Connection, *, ids: list[str] | None = None, detector: str | None = None,
               merchant_id: str | None = None, recommendation: str | None = None, product: str | None = None,
               min_inr: int | None = None, limit: int = 25) -> list[dict[str, Any]]:
    q, params = "SELECT * FROM leaks WHERE 1=1", []
    if ids:
        q += f" AND leak_id IN ({','.join('?' * len(ids))})"
        params += ids
    if detector:
        q += " AND detector = ?"
        params.append(detector)
    if merchant_id:
        q += " AND merchant_id = ?"
        params.append(merchant_id)
    if recommendation:
        q += " AND recommendation_type = ?"
        params.append(recommendation)
    if product:
        q += " AND fit_product = ?"
        params.append(product)
    if min_inr is not None:
        q += " AND recoverable_inr_monthly >= ?"
        params.append(min_inr)
    q += " ORDER BY recoverable_inr_monthly DESC, leak_id LIMIT ?"
    params.append(max(1, min(int(limit), 200)))
    names = merchant_names(conn)
    out = []
    for r in conn.execute(q, params):
        lk = _leak_row(r)
        out.append({
            "leak_id": lk["leak_id"], "merchant_id": lk["merchant_id"], "merchant_name": names[lk["merchant_id"]],
            "detector": lk["detector"], "detector_label": DETECTOR_LABEL[lk["detector"]], "severity": lk["severity"],
            "monthly_inr_at_risk": lk["monthly_inr_at_risk"], "recoverable_inr_monthly": lk["recoverable_inr_monthly"],
            "recommended_product": lk["recommended_product"],
            "recommended_product_name": product_name(lk["recommended_product"]),
            "fit_product": lk["fit_product"], "fit_product_name": product_name(lk["fit_product"]),
            "recommendation_type": lk["recommendation_type"], "headline": headline(lk),
        })
    if ids:   # keep the caller's order (the agent ranks them)
        order = {i: n for n, i in enumerate(ids)}
        out.sort(key=lambda x: order.get(x["leak_id"], 1e9))
    return out


def calculation(lk: dict) -> dict[str, Any]:
    """Step-by-step 'how we calculated this' — every value is a stored field."""
    s, a, det = lk["stats"], lk["assumptions_used"], lk["detector"]
    month = a["month_days"]
    days = a["days_observed"]
    steps: list[dict[str, str]] = []
    if det == "d1_bank_spike":
        steps = [
            {"label": "Failure rate on this bank", "value": f"{fmt_pct(s['failure_rate'])} of {s['attempts']:,} attempts"},
            {"label": "Same method, other banks (baseline)", "value": f"{fmt_pct(s['baseline_rate'])} of {s['baseline_attempts']:,} attempts"},
            {"label": "Excess failures", "value": f"{s['attempts']:,} × ({fmt_pct(s['failure_rate'])} − {fmt_pct(s['baseline_rate'])}) = {s['excess_failures']:,}"},
            {"label": "₹ at risk per month", "value": f"{s['excess_failures']:,} × {fmt_inr(s['avg_ticket_inr'])} avg ticket × {month}/{days} days = {fmt_inr(lk['monthly_inr_at_risk'])}"},
            {"label": "Recoverable per month", "value": f"{fmt_inr(lk['monthly_inr_at_risk'])} × {a['routing_recovery_rate']} routing recovery × (1 − {a['already_recovered_by_retry_share']} already recovered by retries) = {fmt_inr(lk['recoverable_inr_monthly'])}"},
            {"label": "Significance", "value": f"two-proportion z = {s['z']}, p = {s['p_value']:.2g}; {s['windows_tested']} time windows tested, Benjamini–Hochberg adjusted p = {s['p_adjusted']:.2g}"},
        ]
    elif det == "d2_upi_collect":
        steps = [
            {"label": "Collect share of UPI", "value": f"{s['collect_attempts']:,} of {s['upi_attempts']:,} UPI attempts = {fmt_pct(s['collect_share'])}"},
            {"label": "Success-rate gap", "value": f"intent {fmt_pct(s['intent_sr'])} − collect {fmt_pct(s['collect_sr'])} = {s['sr_gap_pts']} pts ({s['intent_sr_source'].replace('_', ' ')} intent rate)"},
            {"label": "₹ at risk per month", "value": f"{s['collect_attempts_on_mobile']:,} mobile collect attempts × {s['sr_gap_pts']} pts × {fmt_inr(s['avg_upi_ticket_inr'])} avg UPI ticket × {month}/{days} days = {fmt_inr(lk['monthly_inr_at_risk'])}"},
            {"label": "Recoverable per month", "value": f"{fmt_inr(lk['monthly_inr_at_risk'])} × {a['switchable_share']} switchable share = {fmt_inr(lk['recoverable_inr_monthly'])}"},
        ]
    else:
        steps = [
            {"label": "System failure rate on renewals", "value": f"{s['system_failed_renewals']:,} of {s['recurring_debits']:,} debits = {fmt_pct(s['recurring_fail_rate'])} vs platform median {fmt_pct(s['baseline_rate'])}"},
            {"label": "Customer-caused failures (excluded)", "value": f"{s['customer_failed_renewals']:,} debits (insufficient funds / cancelled)"},
            {"label": "₹ at risk per month", "value": f"{s['system_failed_renewals']:,} failed renewals × {fmt_inr(s['avg_failed_plan_inr'])} avg plan × {month}/{days} days = {fmt_inr(lk['monthly_inr_at_risk'])}"},
            {"label": "Annual churn exposure", "value": f"{fmt_inr(lk['monthly_inr_at_risk'])} × 12 × {a['churn_given_failure']} churn given failure = {fmt_inr(lk['annual_churn_inr'])}"},
            {"label": "Recoverable per month", "value": f"{fmt_inr(lk['monthly_inr_at_risk'])} × {a['retry_recovery_rate']} retry recovery rate = {fmt_inr(lk['recoverable_inr_monthly'])}"},
            {"label": "Significance", "value": f"one-sided binomial test vs baseline, Benjamini–Hochberg adjusted p = {s['p_adjusted']:.2g}"},
        ]
    return {"steps": steps, "assumptions": a}


# ---------- evidence / trend ----------

def evidence(conn: sqlite3.Connection, leak_id: str, page: int = 1, page_size: int = 25) -> dict[str, Any]:
    lk = get_leak(conn, leak_id)
    where, params = compile_where(lk["evidence_query"])
    total = conn.execute(f"SELECT COUNT(*), SUM(status='failed') FROM payments WHERE {where}", params).fetchone()
    page_size = max(1, min(int(page_size), 100))
    page = max(1, int(page))
    rows = conn.execute(
        f"SELECT payment_id, ts, amount_inr, method, issuer_bank, device, status, error_code, error_layer, attempt_no "
        f"FROM payments WHERE {where} ORDER BY (status = 'failed') DESC, ts LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size]).fetchall()
    return {"leak_id": leak_id, "filter": lk["evidence_query"], "total": int(total[0]), "failed": int(total[1] or 0),
            "page": page, "page_size": page_size, "rows": [dict(r) for r in rows]}


METRIC_FOR_DETECTOR = {"d1_bank_spike": "failure_rate", "d2_upi_collect": "collect_share",
                       "d3_subscription_churn": "recurring_fail_rate"}


@lru_cache(maxsize=4)
def _platform_collect_share(conn: sqlite3.Connection) -> float:
    df = pd.read_sql_query(
        "SELECT merchant_id, SUM(method='upi_collect') * 1.0 / COUNT(*) AS share FROM payments "
        "WHERE is_recurring = 0 AND attempt_no = 1 AND method IN ('upi_intent','upi_collect') GROUP BY merchant_id", conn)
    return float(df["share"].median())


def trend(conn: sqlite3.Connection, leak_id: str, metric: str | None = None) -> dict[str, Any]:
    lk = get_leak(conn, leak_id)
    metric = metric or METRIC_FOR_DETECTOR[lk["detector"]]
    if METRIC_FOR_DETECTOR[lk["detector"]] != metric:
        raise ValueError(f"metric {metric} does not apply to {lk['detector']}")
    mid = lk["merchant_id"]
    cust = config.assumptions()["common"]["customer_codes"]
    ph = ",".join("?" * len(cust))
    if lk["detector"] == "d1_bank_spike":
        d = lk["dimension"]
        hours_sql, hp = "", []
        if d["window"]["kind"] == "hours":
            hours_sql = " AND CAST(substr(ts, 12, 2) AS INTEGER) >= ? AND CAST(substr(ts, 12, 2) AS INTEGER) < ?"
            hp = d["window"]["hours"]
        df = pd.read_sql_query(
            f"SELECT substr(ts,1,10) AS date, (issuer_bank = ?) AS is_cell, COUNT(*) AS n, "
            f"SUM(status='failed' AND error_code NOT IN ({ph})) AS f FROM payments "
            f"WHERE merchant_id = ? AND method = ? AND is_recurring = 0 AND attempt_no = 1{hours_sql} "
            f"GROUP BY date, is_cell", conn, params=[d["issuer_bank"], *cust, mid, d["method"], *hp])
        piv = df.pivot_table(index="date", columns="is_cell", values=["n", "f"], aggfunc="sum").fillna(0)
        series = []
        for date, r in piv.iterrows():
            n1, f1 = r.get(("n", 1), 0), r.get(("f", 1), 0)
            n0, f0 = r.get(("n", 0), 0), r.get(("f", 0), 0)
            series.append({"date": date, "value": round(f1 / n1, 4) if n1 else None,
                           "baseline": round(f0 / n0, 4) if n0 else None, "n": int(n1)})
        label, base_label = f"{d['issuer_bank']} {d['method']}", f"other banks, {d['method']}"
        highlight = d["window"].get("dates", [])
    elif lk["detector"] == "d2_upi_collect":
        df = pd.read_sql_query(
            "SELECT substr(ts,1,10) AS date, COUNT(*) AS n, SUM(method='upi_collect') AS c FROM payments "
            "WHERE merchant_id = ? AND is_recurring = 0 AND attempt_no = 1 AND method IN ('upi_intent','upi_collect') "
            "GROUP BY date", conn, params=[mid])
        base = round(_platform_collect_share(conn), 4)
        series = [{"date": r.date, "value": round(r.c / r.n, 4), "baseline": base, "n": int(r.n)} for r in df.itertuples()]
        label, base_label, highlight = "Collect share of UPI", "platform median merchant", []
    else:
        codes = lk["assumptions_used"]["system_codes"]
        cp = ",".join("?" * len(codes))
        df = pd.read_sql_query(
            f"SELECT substr(ts,1,10) AS date, COUNT(*) AS n, TOTAL(COALESCE(error_code IN ({cp}), 0)) AS f FROM payments "
            f"WHERE merchant_id = ? AND is_recurring = 1 GROUP BY date", conn, params=[*codes, mid])
        base = lk["stats"]["baseline_rate"]
        series = [{"date": r.date, "value": round(r.f / r.n, 4), "baseline": base, "n": int(r.n)}
                  for r in df.itertuples() if r.n >= 3]
        label, base_label, highlight = "System failure rate on renewals", "platform median", []
    return {"leak_id": leak_id, "metric": metric, "label": label, "baseline_label": base_label,
            "highlight_dates": highlight, "series": series}


# ---------- pitches ----------

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_pitch(conn: sqlite3.Connection, leak_id: str, tone: str, text: str) -> dict[str, Any]:
    n = conn.execute("SELECT COUNT(*) FROM pitches").fetchone()[0]
    pid = f"P{n + 1:04d}"
    while conn.execute("SELECT 1 FROM pitches WHERE pitch_id = ?", (pid,)).fetchone():
        n += 1
        pid = f"P{n + 1:04d}"
    now = _now()
    conn.execute("INSERT INTO pitches (pitch_id, leak_id, tone, draft_text, status, edited_text, created_at, updated_at) "
                 "VALUES (?, ?, ?, ?, 'draft', NULL, ?, ?)", (pid, leak_id, tone, text, now, now))
    conn.commit()
    return get_pitch(conn, pid)


def get_pitch(conn: sqlite3.Connection, pitch_id: str) -> dict[str, Any]:
    r = conn.execute("SELECT * FROM pitches WHERE pitch_id = ?", (pitch_id,)).fetchone()
    if not r:
        raise NotFound(f"pitch {pitch_id} not found")
    d = dict(r)
    lk = get_leak(conn, d["leak_id"])
    d["merchant_id"] = lk["merchant_id"]
    d["merchant_name"] = lk["merchant_name"]
    d["recommended_product_name"] = lk["recommended_product_info"]["name"]
    return d


def update_pitch(conn: sqlite3.Connection, pitch_id: str, status: str | None, edited_text: str | None) -> dict[str, Any]:
    get_pitch(conn, pitch_id)
    if status is not None:
        conn.execute("UPDATE pitches SET status = ?, updated_at = ? WHERE pitch_id = ?", (status, _now(), pitch_id))
    if edited_text is not None:
        conn.execute("UPDATE pitches SET edited_text = ?, updated_at = ? WHERE pitch_id = ?", (edited_text, _now(), pitch_id))
    conn.commit()
    return get_pitch(conn, pitch_id)


def fmt_fields(lk: dict) -> dict[str, str]:
    """Pre-formatted strings the agent may quote verbatim."""
    out = {"monthly_inr_at_risk_fmt": fmt_inr_short(lk["monthly_inr_at_risk"]),
           "recoverable_inr_monthly_fmt": fmt_inr_short(lk["recoverable_inr_monthly"])}
    if lk.get("annual_churn_inr"):
        out["annual_churn_inr_fmt"] = fmt_inr_short(lk["annual_churn_inr"])
    return out
