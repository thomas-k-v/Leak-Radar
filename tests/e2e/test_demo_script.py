"""HANDOFF §12 demo script, end to end through the API with the mock LLM."""
from leakradar.agent import number_guard


def components(evs):
    return [(e["component"], e["props"]) for e in evs if e["type"] == "component"]


def texts(evs):
    return [e["text"] for e in evs if e["type"] == "text"]


def assert_clean(evs):
    assert not [e for e in evs if e["type"] in ("error", "guard")], evs
    assert evs[-1]["type"] == "done"


def test_demo_script(client, chat):
    # 1. Ranked table
    evs = chat("Which of my merchants are losing the most this month?")
    assert_clean(evs)
    comps = components(evs)
    assert [c for c, _ in comps] == ["MerchantLeakTable"]
    leak_ids = comps[0][1]["leak_ids"]
    assert len(leak_ids) == 10
    rows = client.get("/api/leaks", params={"ids": ",".join(leak_ids)}).json()
    assert [r["leak_id"] for r in rows] == leak_ids
    assert rows == sorted(rows, key=lambda r: -r["recoverable_inr_monthly"])
    assert texts(evs)

    # 2. Click the top row
    top = leak_ids[0]
    evs = chat(f"Show details for leak {top} ({rows[0]['merchant_name']})")
    assert_clean(evs)
    kinds = [c for c, _ in components(evs)]
    assert kinds == ["LeakCard", "RootCauseCard", "TrendChart"]
    for path in (f"/api/leaks/{top}", f"/api/leaks/{top}/root-cause", f"/api/leaks/{top}/trend"):
        assert client.get(path).status_code == 200, path
    trend = client.get(f"/api/leaks/{top}/trend").json()
    assert trend["series"] and {"date", "value", "baseline"} <= set(trend["series"][0])

    # 3. Draft, edit, approve
    evs = chat("Draft a pitch, consultative tone")
    assert_clean(evs)
    (kind, props), = components(evs)
    assert kind == "DraftMessage"
    pid = props["pitch_id"]
    p = client.get(f"/api/pitches/{pid}").json()
    assert p["leak_id"] == top and p["status"] == "draft" and "₹" in p["draft_text"]
    edited = p["draft_text"].replace("Best regards", "Warm regards")
    r = client.patch(f"/api/pitches/{pid}", json={"edited_text": edited, "status": "approved"})
    assert r.status_code == 200
    p = client.get(f"/api/pitches/{pid}").json()
    assert p["status"] == "approved" and p["edited_text"] == edited

    # 4. Evidence
    evs = chat("Show me why")
    assert_clean(evs)
    assert components(evs) == [("EvidenceDrawer", {"leak_id": top})]
    ev = client.get(f"/api/leaks/{top}/evidence", params={"page": 1, "page_size": 10}).json()
    assert ev["total"] > 0 and len(ev["rows"]) == 10 and ev["rows"][0]["status"] == "failed"

    # 5. Config-review merchants
    evs = chat("Which merchants already have Optimizer but still leak?")
    assert_clean(evs)
    (kind, props), = components(evs)
    assert kind == "MerchantLeakTable"
    rows = client.get("/api/leaks", params={"ids": ",".join(props["leak_ids"])}).json()
    assert rows and all(r["recommendation_type"] == "config_review" and r["fit_product"] == "optimizer" for r in rows)

    # 6. Footer eval summary
    e = client.get("/api/eval").json()
    assert e["headline"].startswith("Detectors:")


def test_every_mock_reply_passes_the_guard_independently(chat, client):
    """Re-check the streamed text against the REST data: no figure the API can't back up."""
    evs = chat("Which of my merchants are losing the most this month?")
    leak_ids = components(evs)[0][1]["leak_ids"]
    rows = client.get("/api/leaks", params={"ids": ",".join(leak_ids)}).json()
    allowed = number_guard.allowed_numbers([rows, {"total": sum(r["recoverable_inr_monthly"] for r in rows)},
                                            {"count": len(rows)}])
    for t in texts(evs):
        assert number_guard.check(t, allowed).ok


def test_guard_regenerates_once(client, monkeypatch):
    monkeypatch.setenv("MOCK_LLM_BAD_NUMBERS", "once")
    from tests.conftest import parse_sse

    evs = parse_sse(client.post("/api/chat", json={"message": "Which merchants are losing the most?"}).text)
    guard = [e for e in evs if e["type"] == "guard"]
    assert len(guard) == 1 and guard[0]["action"] == "regenerate" and "₹9.87L" in guard[0]["violations"]
    assert all("9.87" not in t for t in texts(evs))
    assert texts(evs)


def test_guard_redacts_after_failed_retry(client, monkeypatch):
    monkeypatch.setenv("MOCK_LLM_BAD_NUMBERS", "always")
    from tests.conftest import parse_sse

    evs = parse_sse(client.post("/api/chat", json={"message": "Which merchants are losing the most?"}).text)
    assert [e["action"] for e in evs if e["type"] == "guard"] == ["regenerate", "redacted"]
    final = texts(evs)[-1]
    assert "9.87" not in final and "withheld" in final


def test_data_source_question(chat):
    evs = chat("Is this real data? What's the source?")
    assert "synthetic" in " ".join(texts(evs)).lower()
