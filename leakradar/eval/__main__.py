"""python -m leakradar.eval --data data/ [--holdout-seeds 1,2,3,4,5]

Evaluates the leaks stored in data/leakradar.db against data/ground_truth.json (the dataset the
UI shows), then — optionally — regenerates held-out seeds in memory and evaluates those too, so
thresholds tuned on seed 42 are checked on data they never saw.
Writes data/eval.json (served at GET /api/eval) and docs/eval-report.md.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from leakradar.common import config
from leakradar.common.config import REPO_ROOT
from leakradar.common.db import connect, get_meta
from leakradar.common.models import DETECTOR_LABEL, DETECTORS

from .metrics import TARGETS, evaluate


def leaks_from_db(conn) -> list[dict]:
    out = []
    for r in conn.execute("SELECT * FROM leaks"):
        d = dict(r)
        d["dimension"] = json.loads(d["dimension"])
        out.append(d)
    return out


def eval_seed(seed: int, merchants: int, days: int) -> dict:
    from leakradar.detectors.run import run_all
    from leakradar.generator.__main__ import generate

    res = generate(seed, merchants, days)
    leaks = run_all(res["payments"], res["merchants"], config.assumptions(), config.products())
    return evaluate([lk.model_dump() for lk in leaks], res["ground_truth"])


def summarise_holdout(results: dict[int, dict]) -> dict:
    out = {}
    for det in DETECTORS:
        vals = {m: [r["per_detector"][det][m] for r in results.values()] for m in
                ("precision", "recall", "median_abs_pct_error")}
        out[det] = {m: {"mean": round(statistics.mean(v), 4), "min": min(v), "max": max(v)}
                    for m, v in vals.items() if all(x is not None for x in v)}
        out[det]["fp_total"] = sum(r["per_detector"][det]["fp"] for r in results.values())
        out[det]["fn_total"] = sum(r["per_detector"][det]["fn"] for r in results.values())
    return out


def pct(x):
    return "–" if x is None else f"{x * 100:.1f}%"


def render_report(main: dict, holdout: dict | None, seed: int, holdout_seeds: list[int]) -> str:
    L = ["# Leak Radar — detector evaluation", "",
         "> Synthetic data. Ground truth is the set of leaks planted by the generator; expected ₹ is computed",
         "> from the *true* planted probabilities over the realised traffic (see docs/DECISIONS.md).", "",
         f"Targets: precision ≥ {TARGETS['precision']:.2f}, recall ≥ {TARGETS['recall']:.2f}, "
         f"median ₹ error ≤ {TARGETS['median_abs_pct_error']:.0%} — per detector.", "",
         f"## Demo dataset (seed {seed})", "",
         "| Detector | Planted | Detected | TP | FP | FN | Precision | Recall | F1 | Median ₹ error | FP on decoys | Meets targets |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for det in DETECTORS:
        v = main["per_detector"][det]
        L.append(f"| {DETECTOR_LABEL[det]} | {v['planted']} | {v['detected']} | {v['tp']} | {v['fp']} | {v['fn']} | "
                 f"{v['precision']:.2f} | {v['recall']:.2f} | {v['f1']:.2f} | {pct(v['median_abs_pct_error'])} | "
                 f"{v['fp_on_decoys']} | {'✅' if v['meets_targets'] else '❌'} |")
    o = main["overall"]
    L += ["", f"Overall: **{o['found']}/{o['planted']} planted leaks found, {o['false_positives']} false positives** "
              f"({o['fp_on_decoys']} on decoy merchants). Median ₹ error {pct(o['median_abs_pct_error'])}. "
              f"Recommendation (cross-sell vs config review) correct on {pct(o['recommendation_accuracy'])} of matches.", ""]
    for det in DETECTORS:
        v = main["per_detector"][det]
        if v["missed"] or v["false_positives"]:
            L.append(f"- {DETECTOR_LABEL[det]}: missed {v['missed'] or 'none'}; false positives {v['false_positives'] or 'none'}")
    if holdout:
        L += ["", f"## Held-out seeds {', '.join(map(str, holdout_seeds))} (thresholds were tuned on seed {seed} only)", "",
              "| Detector | Precision mean (min) | Recall mean (min) | Median ₹ error mean (max) | Total FP | Total FN |",
              "|---|---|---|---|---|---|"]
        for det in DETECTORS:
            h = holdout[det]
            e = h.get("median_abs_pct_error", {})
            L.append(f"| {DETECTOR_LABEL[det]} | {h['precision']['mean']:.2f} ({h['precision']['min']:.2f}) | "
                     f"{h['recall']['mean']:.2f} ({h['recall']['min']:.2f}) | "
                     f"{pct(e.get('mean'))} ({pct(e.get('max'))}) | {h['fp_total']} | {h['fn_total']} |")
    L += ["", "Regenerate with `make eval`."]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=config.data_dir())
    ap.add_argument("--holdout-seeds", default="", help="comma-separated, e.g. 1,2,3,4,5")
    ap.add_argument("--report", type=Path, default=REPO_ROOT / "docs" / "eval-report.md")
    a = ap.parse_args()
    t0 = time.time()
    conn = connect(a.data / "leakradar.db")
    meta = get_meta(conn)
    gt = json.loads((a.data / "ground_truth.json").read_text())
    main_res = evaluate(leaks_from_db(conn), gt)
    seeds = [int(s) for s in a.holdout_seeds.split(",") if s.strip()]
    holdout_runs = {s: eval_seed(s, len(set(x["merchant_id"] for x in conn.execute("SELECT merchant_id FROM merchants"))),
                                 meta["days"]) for s in seeds}
    holdout = summarise_holdout(holdout_runs) if seeds else None
    o = main_res["overall"]
    summary = {
        "seed": meta["seed"], "synthetic": True, **main_res,
        "holdout_seeds": seeds, "holdout": holdout,
        "headline": f"Detectors: {o['found']}/{o['planted']} planted leaks found, {o['false_positives']} false positives",
    }
    (a.data / "eval.json").write_text(json.dumps(summary, indent=2))
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(render_report(main_res, holdout, meta["seed"], seeds))
    print(summary["headline"])
    for det in DETECTORS:
        v = main_res["per_detector"][det]
        print(f"  {det:24s} P={v['precision']:.2f} R={v['recall']:.2f} ₹err={pct(v['median_abs_pct_error'])} "
              f"FP={v['fp']} FN={v['fn']} missed={v['missed']} fps={v['false_positives']}")
    if holdout:
        for det in DETECTORS:
            h = holdout[det]
            print(f"  holdout {det:16s} P={h['precision']['mean']:.2f}(min {h['precision']['min']:.2f}) "
                  f"R={h['recall']['mean']:.2f}(min {h['recall']['min']:.2f}) "
                  f"₹err={pct(h.get('median_abs_pct_error', {}).get('mean'))} FP={h['fp_total']} FN={h['fn_total']}")
    print(f"wrote {a.data / 'eval.json'} and {a.report} ({time.time() - t0:.1f}s)")


if __name__ == "__main__":
    main()
