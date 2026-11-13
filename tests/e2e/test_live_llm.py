"""Milestone 6 acceptance: the §12 demo script against the real model, 3 runs in a row.

Skipped by default (costs API credits). Run with:
    ANTHROPIC_API_KEY=... make live-check
"""
import os

import pytest
from dotenv import load_dotenv

from leakradar.common.config import REPO_ROOT

from tests.conftest import parse_sse

load_dotenv(REPO_ROOT / ".env")

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("LEAKRADAR_LIVE") != "1" or not os.environ.get("ANTHROPIC_API_KEY"),
                       reason="needs LEAKRADAR_LIVE=1 and ANTHROPIC_API_KEY (use `make live-check`)"),
]


def _send(client, message, cid):
    evs = parse_sse(client.post("/api/chat", json={"message": message, "conversation_id": cid}).text)
    assert not [e for e in evs if e["type"] == "error"], evs
    # The guard may ask for one rewrite; a reply that still needed redaction fails the acceptance bar.
    assert not [e for e in evs if e["type"] == "guard" and e["action"] == "redacted"], evs
    return evs, evs[0]["conversation_id"]


def _kinds(evs):
    return [e["component"] for e in evs if e["type"] == "component"]


@pytest.mark.parametrize("run", [1, 2, 3])
def test_demo_script_live(client, run):
    evs, cid = _send(client, "Which of my merchants are losing the most this month?", None)
    tables = [e for e in evs if e["type"] == "component" and e["component"] == "MerchantLeakTable"]
    assert tables, _kinds(evs)
    top = tables[0]["props"]["leak_ids"][0]

    evs, cid = _send(client, f"Show details for leak {top}", cid)
    assert {"LeakCard", "RootCauseCard", "TrendChart"} <= set(_kinds(evs)), _kinds(evs)

    evs, cid = _send(client, "Draft a pitch, consultative tone", cid)
    drafts = [e for e in evs if e["type"] == "component" and e["component"] == "DraftMessage"]
    assert drafts, _kinds(evs)
    pid = drafts[0]["props"]["pitch_id"]
    assert client.patch(f"/api/pitches/{pid}", json={"status": "approved"}).json()["status"] == "approved"

    evs, cid = _send(client, "Show me why", cid)
    assert "EvidenceDrawer" in _kinds(evs), _kinds(evs)

    evs, cid = _send(client, "Which merchants already have Optimizer but still leak?", cid)
    tables = [e for e in evs if e["type"] == "component" and e["component"] == "MerchantLeakTable"]
    assert tables, _kinds(evs)
    rows = client.get("/api/leaks", params={"ids": ",".join(tables[-1]["props"]["leak_ids"])}).json()
    assert all(r["recommendation_type"] == "config_review" for r in rows)
