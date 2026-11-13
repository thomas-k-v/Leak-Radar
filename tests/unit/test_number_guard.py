import pytest

from leakradar.agent.number_guard import allowed_numbers, check, extract_numbers, redact
from leakradar.common.money import fmt_inr, fmt_inr_short, indian_group

TOOL_OUTPUT = {
    "leak_id": "L0003", "merchant_id": "M014",
    "recoverable_inr_monthly": 138420, "monthly_inr_at_risk": 276840,
    "failure_rate": 0.1412, "baseline_rate": 0.0665, "excess_pts": 7.47,
    "attempts": 1630, "annual_churn_inr": 21500000,
    "label": "18:00–22:00 daily", "recoverable_fmt": "₹1.38L",
}
ALLOWED = allowed_numbers([TOOL_OUTPUT])


@pytest.mark.parametrize("text", [
    "M014 could recover about ₹1.4L a month.",
    "That is ₹1.38L per month.",
    "Roughly ₹1.38 lakh recoverable.",
    "₹1,38,420 recoverable each month.",
    "₹138,420 recoverable each month.",
    "₹138k recoverable.",
    "At risk: ₹2.77L.",
    "Failure rate 14% vs 6.7% baseline.",
    "Failure rate 14.1% vs a 6.65% baseline (7.5 pts excess).",
    "Annual churn exposure ₹2.15Cr, or ₹2.15 crore.",
    "Across 1,630 attempts.",
    "Across 1630 attempts.",
])
def test_allows_numbers_present_in_tool_outputs(text):
    r = check(text, ALLOWED)
    assert r.ok, r.violations


@pytest.mark.parametrize("text,bad", [
    ("They could recover ₹2L a month.", "₹2L"),
    ("Roughly ₹1L recoverable.", "₹1L"),                       # rounds too far from 1.38L
    ("Failure rate is 22%.", "22%"),
    ("This will lift revenue by ₹5,00,000.", "₹5,00,000"),
    ("About 4,200 customers affected.", "4,200"),
    ("Expect 3.5x more successful payments.", "3.5x"),
    ("Recovery of 45 pts is likely.", "45 pts"),
])
def test_rejects_numbers_not_in_tool_outputs(text, bad):
    r = check(text, ALLOWED)
    assert not r.ok
    assert bad in r.violations


@pytest.mark.parametrize("text", [
    "Here are the top 5 merchants.",                 # small integer
    "Look at leak L0003 for merchant M014 on BANK_A.",  # identifiers
    "The spike runs 18:00–22:00 daily.",             # clock times
    "Sale days were 15 Aug and Oct 2, 2026.",        # dates
    "Data covers 2026-08-03 to 2026-10-31.",         # ISO dates
    "During October 2026 the rate rose.",            # month-year
    "Template: you could recover {recoverable_inr_monthly} per month.",
])
def test_exemptions(text):
    r = check(text, set())
    assert r.ok, r.violations


def test_small_integer_with_unit_is_still_checked():
    assert not check("A 5% uplift.", set()).ok
    assert not check("Costs ₹5.", set()).ok


def test_numbers_inside_tool_output_strings_are_allowed():
    allowed = allowed_numbers([{"note": "window 18:00–22:00, 412 failed attempts, ₹3.2L"}])
    assert check("412 failed attempts worth ₹3.2L", allowed).ok


def test_tool_outputs_can_be_json_strings():
    allowed = allowed_numbers(['{"recoverable_inr_monthly": 48250}'])
    assert check("₹48,250 a month", allowed).ok


def test_extract_reports_units():
    toks = {t.raw: t for t in extract_numbers("₹1.4L and 14% and 1,40,000 and ₹2Cr")}
    assert toks["₹1.4L"].mult == 1e5 and toks["₹1.4L"].decimals == 1
    assert toks["14%"].kind == "pct"
    assert toks["1,40,000"].value == 140000
    assert toks["₹2Cr"].mult == 1e7


def test_redact():
    assert "withheld" in redact("They could recover ₹2L a month.", ["₹2L"])


def test_money_formatting():
    assert indian_group(13842000) == "1,38,42,000"
    assert indian_group(999) == "999"
    assert fmt_inr(138420) == "₹1,38,420"
    assert fmt_inr_short(138420) == "₹1.38L"
    assert fmt_inr_short(21500000) == "₹2.15Cr"
    assert fmt_inr_short(48250) == "₹48,250"
