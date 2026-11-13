import json
from pathlib import Path

import pytest

from leakradar.agent import components, pitch_templates, tools
from leakradar.common import repo
from leakradar.common.db import connect

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "components"


@pytest.fixture
def conn(demo_data):
    c = connect(demo_data / "leakradar.db")
    yield c
    c.close()


def first_leak(conn, detector):
    return conn.execute("SELECT leak_id FROM leaks WHERE detector = ? ORDER BY leak_id LIMIT 1", (detector,)).fetchone()[0]


@pytest.mark.parametrize("path", sorted((FIXTURES / "valid").glob("*.json")), ids=lambda p: p.stem)
def test_valid_fixtures_pass_schema(path):
    c = json.loads(path.read_text())
    components.validate_schema(c["type"], c["props"])


@pytest.mark.parametrize("path", sorted((FIXTURES / "invalid").glob("*.json")), ids=lambda p: p.stem)
def test_invalid_fixtures_fail_schema(path):
    c = json.loads(path.read_text())
    with pytest.raises(components.ComponentError):
        components.validate_schema(c["type"], c["props"])


def test_render_component_checks_ids_and_metric(conn):
    d1 = first_leak(conn, "d1_bank_spike")
    assert tools.render_component(conn, "LeakCard", {"leak_id": d1})["rendered"] == "LeakCard"
    with pytest.raises(tools.ToolError, match="unknown leak_id"):
        tools.render_component(conn, "LeakCard", {"leak_id": "L9999"})
    with pytest.raises(tools.ToolError, match="does not apply"):
        tools.render_component(conn, "TrendChart", {"leak_id": d1, "metric": "collect_share"})
    with pytest.raises(tools.ToolError, match="unknown pitch_id"):
        tools.render_component(conn, "DraftMessage", {"pitch_id": "P9999"})


def test_pitch_template_rejects_raw_numbers_and_unknown_placeholders(conn):
    lk = repo.get_leak(conn, first_leak(conn, "d2_upi_collect"))
    with pytest.raises(pitch_templates.TemplateError, match="raw numbers"):
        pitch_templates.render(lk, "concise", "You lose ₹2,00,000 a month.")
    with pytest.raises(pitch_templates.TemplateError, match="unknown placeholders"):
        pitch_templates.render(lk, "concise", "You lose {made_up} a month.")
    text = pitch_templates.render(lk, "concise", "Hi {merchant_name}, about {recoverable_inr_monthly} is recoverable.")
    assert lk["merchant_name"] in text and "₹" in text


@pytest.mark.parametrize("tone", ["concise", "consultative"])
def test_builtin_templates_are_number_free(tone):
    from leakradar.agent import number_guard

    assert number_guard.check(pitch_templates.BUILTIN[tone], set()).ok
    assert number_guard.check(pitch_templates.CONFIG_REVIEW, set()).ok


def test_every_leak_has_traceable_calculation(client, conn):
    for (lid,) in conn.execute("SELECT leak_id FROM leaks"):
        lk = client.get(f"/api/leaks/{lid}").json()
        assert lk["assumptions_used"] and lk["evidence_query"]["merchant_id"] == lk["merchant_id"]
        assert lk["calculation"]["steps"]
        assert client.get(f"/api/leaks/{lid}/trend").status_code == 200


def test_trend_rejects_wrong_metric(client, conn):
    d3 = first_leak(conn, "d3_subscription_churn")
    assert client.get(f"/api/leaks/{d3}/trend", params={"metric": "failure_rate"}).status_code == 422


def test_404s(client):
    assert client.get("/api/leaks/L9999").status_code == 404
    assert client.get("/api/merchants/M999").status_code == 404
    assert client.get("/api/pitches/P9999").status_code == 404


def test_pitch_patch_validates_status(client, conn):
    lid = first_leak(conn, "d1_bank_spike")
    p = tools.draft_pitch(conn, lid, "concise")
    assert client.patch(f"/api/pitches/{p['pitch_id']}", json={"status": "sent"}).status_code == 422
    r = client.patch(f"/api/pitches/{p['pitch_id']}", json={"status": "rejected"})
    assert r.json()["status"] == "rejected"


def test_meta_is_labelled_synthetic(client):
    m = client.get("/api/meta").json()
    assert m["synthetic"] is True and "Not affiliated" in m["disclaimer"]
