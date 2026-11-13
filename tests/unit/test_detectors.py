import copy

import numpy as np
import pytest

from leakradar.common import config
from leakradar.common.models import Leak
from leakradar.detectors import d1_bank_spike, d2_upi_collect, d3_subscription_churn
from leakradar.detectors.base import benjamini_hochberg
from leakradar.detectors.recommender import recommend
from tests.helpers import Builder, merchants


@pytest.fixture
def cfg():
    return copy.deepcopy(config.assumptions())


# ---------- D1 ----------

def d1_dataset(spike_fail: int, spike_code: str = "BANK_TIMEOUT", spike_n: int = 600):
    b = Builder(days=30)
    b.add("M1", spike_n, bank="BANK_A", fail=spike_fail, code=spike_code)
    b.add("M1", 1200, bank="BANK_B", fail=60)
    b.add("M1", 1200, bank="BANK_C", fail=60)
    return b.frame()


def test_d1_flags_bank_spike_and_sizes_it(cfg):
    leaks = d1_bank_spike.detect(d1_dataset(spike_fail=180), merchants("M1"), cfg)
    assert len(leaks) == 1
    lk = leaks[0]
    assert lk.dimension["method"] == "card" and lk.dimension["issuer_bank"] == "BANK_A"
    s = lk.stats
    # Whole-period window wins: 30% vs 5% baseline.
    assert s["failure_rate"] == pytest.approx(0.30, abs=1e-3)
    assert s["baseline_rate"] == pytest.approx(0.05, abs=1e-3)
    # excess_failures = attempts × (rate − baseline); at_risk = excess × ticket × 30/days
    expected_excess = s["attempts"] * (s["failure_rate"] - s["baseline_rate"])
    expected_at_risk = expected_excess * 1000 * 30 / 30
    assert lk.monthly_inr_at_risk == pytest.approx(expected_at_risk, rel=0.01)
    # No retries in the fixture → recoverable = at_risk × routing_recovery_rate
    assert lk.recoverable_inr_monthly == pytest.approx(lk.monthly_inr_at_risk * 0.5, abs=1)
    assert lk.evidence_query["merchant_id"] == "M1" and lk.evidence_query["issuer_bank"] == ["BANK_A"]
    assert lk.root_cause["cell"]["bank"] == 1.0


def test_d1_ignores_customer_caused_failures(cfg):
    leaks = d1_bank_spike.detect(d1_dataset(spike_fail=180, spike_code="INSUFFICIENT_FUNDS"), merchants("M1"), cfg)
    assert leaks == []


def test_d1_ignores_low_volume_cells(cfg):
    leaks = d1_bank_spike.detect(d1_dataset(spike_fail=40, spike_n=100), merchants("M1"), cfg)
    assert leaks == []


def test_d1_ignores_small_excess(cfg):
    # 7.5% vs 5%: significant-ish but below min_excess_pts = 8
    leaks = d1_bank_spike.detect(d1_dataset(spike_fail=45), merchants("M1"), cfg)
    assert leaks == []


def test_d1_finds_hour_window_spike(cfg):
    b = Builder(days=30)
    b.add("M1", 1500, bank="BANK_A", fail=75)                                   # normal hours 8–19
    b.add("M1", 400, bank="BANK_A", fail=120, hour=21)                           # 30% at 21:00
    b.add("M1", 1500, bank="BANK_B", fail=75)
    b.add("M1", 400, bank="BANK_B", fail=20, hour=21)
    b.add("M1", 1500, bank="BANK_C", fail=75)
    b.add("M1", 400, bank="BANK_C", fail=20, hour=21)
    leaks = d1_bank_spike.detect(b.frame(), merchants("M1"), cfg)
    assert len(leaks) == 1
    w = leaks[0].dimension["window"]
    assert w["kind"] == "hours" and w["hours"][0] <= 21 < w["hours"][1]


# ---------- D2 ----------

def test_d2_flags_collect_heavy_merchant(cfg):
    b = Builder()
    b.add("M2", 600, method="upi_intent", fail=30, code="BANK_TIMEOUT", amount=500)        # 95% SR
    b.add("M2", 400, method="upi_collect", fail=100, code="COLLECT_EXPIRED", amount=500)   # 75% SR, mobile
    b.add("M3", 900, method="upi_intent", fail=45, amount=500)
    b.add("M3", 80, method="upi_collect", fail=20, code="COLLECT_EXPIRED", amount=500)     # 8% share
    leaks = d2_upi_collect.detect(b.frame(), merchants("M2", "M3"), cfg)
    assert [lk.merchant_id for lk in leaks] == ["M2"]
    lk = leaks[0]
    assert lk.stats["collect_share"] == pytest.approx(0.4)
    assert lk.stats["sr_gap"] == pytest.approx(0.20)
    # at_risk = mobile_collect × gap × avg_upi_ticket × 30/days ; recoverable = × switchable_share
    assert lk.monthly_inr_at_risk == round(400 * 0.20 * 500 * 30 / 30)
    assert lk.recoverable_inr_monthly == round(lk.monthly_inr_at_risk * 0.8)


def test_d2_needs_minimum_upi_volume(cfg):
    b = Builder()
    b.add("M2", 100, method="upi_intent", fail=5)
    b.add("M2", 100, method="upi_collect", fail=25, code="COLLECT_EXPIRED")
    assert d2_upi_collect.detect(b.frame(), merchants("M2"), cfg) == []


# ---------- D3 ----------

def d3_dataset():
    b = Builder()
    for i in range(6):                                     # baseline merchants: 5% system failures
        b.add(f"B{i}", 400, recurring=True, fail=20, code="MANDATE_NOT_ACTIVE", amount=999)
    b.add("LEAK", 400, recurring=True, fail=80, code="MANDATE_PRENOTIFY_FAILED", amount=999)   # 20%
    b.add("INSF", 280, recurring=True, fail=120, code="INSUFFICIENT_FUNDS", amount=999)        # customer-caused
    b.add("INSF", 120, recurring=True, fail=6, code="MANDATE_NOT_ACTIVE", amount=999, customer_prefix="X")
    return b.frame()


def test_d3_flags_system_churn_not_customer_churn(cfg):
    ids = [f"B{i}" for i in range(6)] + ["LEAK", "INSF"]
    leaks = d3_subscription_churn.detect(d3_dataset(), merchants(*ids), cfg)
    assert [lk.merchant_id for lk in leaks] == ["LEAK"]
    lk = leaks[0]
    assert lk.stats["baseline_rate"] == pytest.approx(0.05)
    assert lk.monthly_inr_at_risk == round(80 * 999 * 30 / 30)
    assert lk.annual_churn_inr == round(lk.monthly_inr_at_risk * 12 * 0.35)
    assert lk.recoverable_inr_monthly == round(lk.monthly_inr_at_risk * 0.4)
    assert lk.stats["clean_history_share"] == 1.0


# ---------- recommender / stats ----------

def _leak(det="d1_bank_spike"):
    return Leak(merchant_id="M1", detector=det, monthly_inr_at_risk=1, recoverable_inr_monthly=1,
                assumptions_used={}, evidence_query={}, dimension={}, stats={}, root_cause={}, fit_product="")


def test_recommender_cross_sell_vs_config_review():
    cat = config.products()
    lk = recommend(_leak(), ["payment_gateway"], cat)
    assert (lk.recommended_product, lk.recommendation_type) == ("optimizer", "cross_sell")
    lk = recommend(_leak(), '["payment_gateway", "optimizer"]', cat)
    assert (lk.recommended_product, lk.recommendation_type, lk.fit_product) == ("config_review", "config_review", "optimizer")


def test_benjamini_hochberg_matches_reference():
    p = np.array([0.01, 0.04, 0.03, 0.005])
    # reference (statsmodels fdr_bh): [0.02, 0.04, 0.04, 0.02]
    assert np.allclose(benjamini_hochberg(p), [0.02, 0.04, 0.04, 0.02])
