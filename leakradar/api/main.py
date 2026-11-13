"""FastAPI app. Run: uvicorn leakradar.api.main:app --port 8000"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from leakradar.agent import components
from leakradar.agent.llm import get_provider
from leakradar.agent.loop import get_conversation, run_turn
from leakradar.common import config, repo
from leakradar.common.config import REPO_ROOT
from leakradar.common.db import connect, get_meta

load_dotenv(REPO_ROOT / ".env")

app = FastAPI(title="Leak Radar API", version="0.1.0",
              description="Synthetic demo data. Independent concept project; not affiliated with Razorpay.")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


def db() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def _nf(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except repo.NotFound as e:
        raise HTTPException(404, str(e)) from None
    except ValueError as e:
        raise HTTPException(422, str(e)) from None


# ---------- chat ----------

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=64)


@app.post("/api/chat")
def chat(req: ChatRequest) -> StreamingResponse:
    conv = get_conversation(req.conversation_id)
    provider = get_provider()

    def events() -> Iterator[str]:
        conn = connect()
        try:
            yield _sse({"type": "start", "conversation_id": conv.id, "provider": provider.name})
            with conv.lock:
                for ev in run_turn(conn, conv, req.message, provider):
                    yield _sse(ev)
        finally:
            conn.close()
        yield _sse({"type": "done", "conversation_id": conv.id})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _sse(ev: dict) -> str:
    return f"data: {json.dumps(ev, default=str)}\n\n"


# ---------- leaks / merchants ----------

@app.get("/api/leaks")
def leaks(ids: str | None = None, detector: str | None = None, merchant_id: str | None = None,
          recommendation: Literal["cross_sell", "config_review"] | None = None, product: str | None = None,
          min_inr: int | None = None, limit: int = Query(25, ge=1, le=200), conn=Depends(db)):
    id_list = [i for i in (ids or "").split(",") if i] or None
    return repo.list_leaks(conn, ids=id_list, detector=detector, merchant_id=merchant_id,
                           recommendation=recommendation, product=product, min_inr=min_inr, limit=limit)


@app.get("/api/leaks/{leak_id}")
def leak(leak_id: str, conn=Depends(db)):
    return _nf(repo.get_leak, conn, leak_id)


@app.get("/api/leaks/{leak_id}/root-cause")
def root_cause(leak_id: str, conn=Depends(db)):
    lk = _nf(repo.get_leak, conn, leak_id)
    return {"leak_id": leak_id, "detector": lk["detector"], **lk["root_cause"]}


@app.get("/api/leaks/{leak_id}/trend")
def leak_trend(leak_id: str, metric: str | None = None, conn=Depends(db)):
    return _nf(repo.trend, conn, leak_id, metric)


@app.get("/api/leaks/{leak_id}/evidence")
def leak_evidence(leak_id: str, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
                  conn=Depends(db)):
    return _nf(repo.evidence, conn, leak_id, page, page_size)


@app.get("/api/merchants/{merchant_id}")
def merchant(merchant_id: str, conn=Depends(db)):
    return _nf(repo.get_merchant, conn, merchant_id)


# ---------- pitches ----------

class PitchPatch(BaseModel):
    status: Literal["draft", "approved", "rejected"] | None = None
    edited_text: str | None = Field(default=None, max_length=5000)


@app.get("/api/pitches/{pitch_id}")
def pitch(pitch_id: str, conn=Depends(db)):
    return _nf(repo.get_pitch, conn, pitch_id)


@app.patch("/api/pitches/{pitch_id}")
def patch_pitch(pitch_id: str, body: PitchPatch, conn=Depends(db)):
    return _nf(repo.update_pitch, conn, pitch_id, body.status, body.edited_text)


# ---------- meta / eval ----------

@app.get("/api/eval")
def eval_summary():
    p = config.data_dir() / "eval.json"
    if not p.exists():
        raise HTTPException(404, "eval.json not found — run `make eval`")
    return json.loads(p.read_text())


@app.get("/api/meta")
def meta(conn=Depends(db)):
    m = get_meta(conn)
    m.pop("data_hash", None)
    return {**m, "synthetic": True, "llm_provider": get_provider().name,
            "disclaimer": ("Independent concept project. Not affiliated with or endorsed by Razorpay. "
                           "Product names are used only to illustrate fit.")}


@app.get("/api/components/schema")
def component_schema():
    return components.json_schema()


@app.get("/api/health")
def health():
    return {"ok": True}


# Serve the built web app (web/dist) when present, so `make demo` needs one port.
_DIST = Path(REPO_ROOT / "web" / "dist")
if _DIST.exists():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = _DIST / path
        return FileResponse(f if path and f.is_file() else _DIST / "index.html")
