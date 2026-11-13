"""Builders for tiny hand-made payment datasets used by detector unit tests."""
from __future__ import annotations

import json

import pandas as pd

LAYER = {"BANK_TIMEOUT": "bank", "BANK_DOWN": "bank", "COLLECT_EXPIRED": "merchant", "INSUFFICIENT_FUNDS": "customer",
         "USER_CANCELLED": "customer", "MANDATE_PRENOTIFY_FAILED": "bank", "GATEWAY_ERROR": "gateway"}


class Builder:
    def __init__(self, days: int = 30):
        self.rows: list[dict] = []
        self.days = days

    def add(self, merchant_id: str, n: int, *, method: str = "card", bank: str = "BANK_A", fail: int = 0,
            code: str = "BANK_TIMEOUT", amount: int = 1000, device: str = "mobile", recurring: bool = False,
            hour: int | None = None, customer_prefix: str = "C") -> "Builder":
        """n attempts spread evenly over `days`; the first `fail` of them fail with `code`."""
        for i in range(n):
            day = i % self.days
            h = hour if hour is not None else (8 + i % 12)
            k = len(self.rows)
            failed = i < fail
            self.rows.append({
                "payment_id": f"pay_{k:07d}", "merchant_id": merchant_id, "order_id": f"O{k:07d}",
                "customer_id": f"{merchant_id}-{customer_prefix}{i:05d}",
                "ts": f"2026-08-{1 + day:02d} {h:02d}:{i % 60:02d}:00" if day < 31 else None,
                "amount_inr": amount, "method": method, "issuer_bank": bank, "device": device,
                "status": "failed" if failed else "success", "error_code": code if failed else None,
                "error_layer": LAYER.get(code) if failed else None, "is_recurring": int(recurring),
                "subscription_id": f"sub_{merchant_id}_{i:05d}" if recurring else None, "attempt_no": 1,
            })
        return self

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows)


def merchants(*ids: str, products: dict[str, list[str]] | None = None) -> pd.DataFrame:
    products = products or {}
    return pd.DataFrame([{"merchant_id": m, "name": f"Test {m}", "category": "saas", "city_tier": "tier1",
                          "monthly_gmv_band": "<10L", "has_subscriptions": 1,
                          "products_active": json.dumps(products.get(m, ["payment_gateway"])), "am_owner": "T"}
                         for m in ids])
