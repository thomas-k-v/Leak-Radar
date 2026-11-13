PY      ?= .venv/bin/python
SEED    ?= 42
HOLDOUT ?= 101,102,103,104,105,106,107,108,109,110
PORT    ?= 8000

.PHONY: setup data detect eval test web-build api dev demo live-check clean

setup:            ## create venv, install backend + web deps
	python3 -m venv .venv
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -e ".[dev]"
	npm --prefix web ci --no-audit --no-fund

data:             ## synthetic data + ground truth (deterministic per SEED)
	$(PY) -m leakradar.generator --seed $(SEED) --merchants 150 --days 90 --out data/

detect:           ## run D1-D3 + recommender into the leaks table
	$(PY) -m leakradar.detectors.run

eval:             ## precision/recall/₹ error vs ground truth + held-out seeds
	$(PY) -m leakradar.eval --data data/ --holdout-seeds $(HOLDOUT)

test:             ## backend + frontend tests (mock LLM, no network)
	$(PY) -m pytest -q
	npm --prefix web test

web-build:
	npm --prefix web run build

api:              ## API + built web app on one port
	$(PY) -m uvicorn leakradar.api.main:app --port $(PORT)

dev:              ## API on :8000 and Vite dev server on :5173
	$(PY) -m uvicorn leakradar.api.main:app --port 8000 --reload & npm --prefix web run dev; kill %1

demo: data detect eval web-build api   ## generator → detectors → eval → web → http://localhost:8000

live-check:       ## demo script against the real model, 3 runs (needs ANTHROPIC_API_KEY, costs credits)
	LEAKRADAR_LIVE=1 LLM_PROVIDER=anthropic $(PY) -m pytest -q -m live tests/e2e/test_live_llm.py

clean:
	rm -f data/leakradar.db data/ground_truth.json data/eval.json
	rm -rf web/dist logs
