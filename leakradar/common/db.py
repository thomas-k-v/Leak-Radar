"""SQLite access. Plain sqlite3; one file at data/leakradar.db (override with LEAKRADAR_DB)."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .config import data_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS merchants (
  merchant_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  category TEXT NOT NULL,
  city_tier TEXT NOT NULL,
  monthly_gmv_band TEXT NOT NULL,
  has_subscriptions INTEGER NOT NULL,
  products_active TEXT NOT NULL,          -- JSON list
  am_owner TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payments (
  payment_id TEXT PRIMARY KEY,
  merchant_id TEXT NOT NULL,
  order_id TEXT NOT NULL,                 -- added: links retries to the first attempt
  customer_id TEXT NOT NULL,              -- added
  ts TEXT NOT NULL,                       -- IST, 'YYYY-MM-DD HH:MM:SS'
  amount_inr INTEGER NOT NULL,            -- whole rupees
  method TEXT NOT NULL,
  issuer_bank TEXT NOT NULL,
  device TEXT NOT NULL,
  status TEXT NOT NULL,
  error_code TEXT,
  error_layer TEXT,
  is_recurring INTEGER NOT NULL,
  subscription_id TEXT,
  attempt_no INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_pay_merchant ON payments (merchant_id, method, issuer_bank);
CREATE INDEX IF NOT EXISTS ix_pay_merchant_ts ON payments (merchant_id, ts);
CREATE TABLE IF NOT EXISTS subscriptions (
  subscription_id TEXT PRIMARY KEY,
  merchant_id TEXT NOT NULL,
  customer_id TEXT NOT NULL,
  plan_amount_inr INTEGER NOT NULL,
  cycle TEXT NOT NULL,
  start_date TEXT NOT NULL,
  status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS leaks (
  leak_id TEXT PRIMARY KEY,
  merchant_id TEXT NOT NULL,
  detector TEXT NOT NULL,
  severity TEXT NOT NULL,
  monthly_inr_at_risk INTEGER NOT NULL,
  recoverable_inr_monthly INTEGER NOT NULL,
  annual_churn_inr INTEGER,               -- D3 only
  assumptions_used TEXT NOT NULL,         -- JSON
  evidence_query TEXT NOT NULL,           -- JSON filter DSL, see leakradar.common.evidence
  dimension TEXT NOT NULL,                -- JSON: what the leak is about (method/bank/window…)
  stats TEXT NOT NULL,                    -- JSON: rates, counts, p-values used for the decision
  root_cause TEXT NOT NULL,               -- JSON: failure share by error_layer
  recommended_product TEXT NOT NULL,      -- product key (or config_review)
  recommendation_type TEXT NOT NULL,      -- cross_sell | config_review
  fit_product TEXT NOT NULL,              -- the product that addresses this detector
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pitches (
  pitch_id TEXT PRIMARY KEY,
  leak_id TEXT NOT NULL,
  tone TEXT NOT NULL,
  draft_text TEXT NOT NULL,
  status TEXT NOT NULL,                   -- draft | approved | rejected
  edited_text TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


def db_path() -> Path:
    return Path(os.environ.get("LEAKRADAR_DB", data_dir() / "leakradar.db"))


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(path) if path else db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def insert_many(conn: sqlite3.Connection, table: str, columns: list[str], rows: Iterable[tuple]) -> None:
    placeholders = ",".join("?" for _ in columns)
    conn.executemany(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})", rows)


def get_meta(conn: sqlite3.Connection) -> dict[str, Any]:
    out = {}
    for r in conn.execute("SELECT key, value FROM meta"):
        out[r["key"]] = json.loads(r["value"])
    return out


def set_meta(conn: sqlite3.Connection, **kv: Any) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        [(k, json.dumps(v)) for k, v in kv.items()],
    )
