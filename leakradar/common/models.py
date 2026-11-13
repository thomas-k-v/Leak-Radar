"""Shared Pydantic models (API responses, detector output)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Detector = Literal["d1_bank_spike", "d2_upi_collect", "d3_subscription_churn"]
DETECTORS: tuple[str, ...] = ("d1_bank_spike", "d2_upi_collect", "d3_subscription_churn")
DETECTOR_LABEL = {
    "d1_bank_spike": "Bank/method failure spike",
    "d2_upi_collect": "UPI Collect instead of Intent",
    "d3_subscription_churn": "Subscription silent churn",
}


class Leak(BaseModel):
    """A detected leak. All ₹ values are integer rupees computed by detectors."""
    leak_id: str = ""
    merchant_id: str
    detector: Detector
    severity: Literal["high", "medium", "low"] = "low"
    monthly_inr_at_risk: int
    recoverable_inr_monthly: int
    annual_churn_inr: int | None = None
    assumptions_used: dict[str, Any]
    evidence_query: dict[str, Any]
    dimension: dict[str, Any]
    stats: dict[str, Any]
    root_cause: dict[str, Any]
    fit_product: str
    recommended_product: str = ""
    recommendation_type: Literal["cross_sell", "config_review"] = "cross_sell"
    created_at: str = ""
    # Not persisted: used to rank windows/cells before IDs are assigned.
    score: float = Field(default=0.0, exclude=True)
