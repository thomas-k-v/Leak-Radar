import pytest

from leakradar.common.evidence import EvidenceQueryError, compile_where
from leakradar.generator.__main__ import data_hash, generate


@pytest.fixture(scope="module")
def small():
    return generate(7, 40, 30)


def test_same_seed_same_data_and_truth(small):
    again = generate(7, 40, 30)
    assert data_hash(small) == data_hash(again)
    assert small["ground_truth"] == again["ground_truth"]


def test_different_seed_differs(small):
    assert data_hash(small) != data_hash(generate(8, 40, 30))


def test_schema_and_labels(small):
    p = small["payments"]
    assert set(p["method"]) <= {"upi_intent", "upi_collect", "card", "netbanking", "intl_card"}
    assert set(p["issuer_bank"]) <= {f"BANK_{c}" for c in "ABCDEFGH"}
    assert set(p["status"]) == {"success", "failed"}
    assert p.loc[p["status"] == "success", "error_code"].isna().all()
    assert p.loc[p["status"] == "failed", "error_layer"].notna().all()
    assert (p["amount_inr"] > 0).all()
    assert p["payment_id"].is_unique
    assert small["ground_truth"]["synthetic"] is True


def test_ground_truth_lists_plants_with_positive_expected_inr(small):
    planted = small["ground_truth"]["planted"]
    assert planted, "expected some plants even in a small run"
    for x in planted:
        assert x["expected_monthly_inr"] > 0
        assert x["detector"] in {"d1_bank_spike", "d2_upi_collect", "d3_subscription_churn"}


def test_evidence_dsl_is_whitelisted():
    where, params = compile_where({"merchant_id": "M1", "method": ["card"], "hours": [18, 22]})
    assert where.count("?") == 4
    assert params == [18, 22, "M1", "card"]
    with pytest.raises(EvidenceQueryError):
        compile_where({"merchant_id": "M1", "ts; DROP TABLE payments": 1})
    with pytest.raises(EvidenceQueryError):
        compile_where({"method": ["card"]})
