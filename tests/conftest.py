import json
import os

import pytest


@pytest.fixture(scope="session")
def demo_data(tmp_path_factory):
    """Full seed-42 dataset + detectors + eval in a temp dir; env points the app at it."""
    out = tmp_path_factory.mktemp("data")
    os.environ["LEAKRADAR_DATA_DIR"] = str(out)
    os.environ["LEAKRADAR_DB"] = str(out / "leakradar.db")
    os.environ["LEAKRADAR_GUARD_LOG"] = str(out / "guard.jsonl")
    os.environ["LLM_PROVIDER"] = "mock"

    from leakradar.common import config
    from leakradar.common.db import connect
    from leakradar.detectors.base import load_frames
    from leakradar.detectors.run import run_all, write_leaks
    from leakradar.eval.metrics import evaluate
    from leakradar.generator.__main__ import generate, write

    res = generate(42, 150, 90)
    write(out, res, 42, 90)
    conn = connect(out / "leakradar.db")
    payments, merchants = load_frames(conn)
    leaks = run_all(payments, merchants, config.assumptions(), config.products())
    write_leaks(conn, leaks)
    ev = evaluate([lk.model_dump() for lk in leaks], res["ground_truth"])
    o = ev["overall"]
    (out / "eval.json").write_text(json.dumps({**ev, "headline": f"Detectors: {o['found']}/{o['planted']} planted "
                                                                 f"leaks found, {o['false_positives']} false positives"}))
    conn.close()
    return out


@pytest.fixture
def client(demo_data):
    from fastapi.testclient import TestClient

    from leakradar.api.main import app

    return TestClient(app)


def parse_sse(body: str) -> list[dict]:
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


@pytest.fixture
def chat(client):
    state = {"cid": None}

    def send(message: str) -> list[dict]:
        r = client.post("/api/chat", json={"message": message, "conversation_id": state["cid"]})
        assert r.status_code == 200
        evs = parse_sse(r.text)
        state["cid"] = evs[0]["conversation_id"]
        return evs

    return send
