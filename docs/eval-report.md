# Leak Radar — detector evaluation

> Synthetic data. Ground truth is the set of leaks planted by the generator; expected ₹ is computed
> from the *true* planted probabilities over the realised traffic (see docs/DECISIONS.md).

Targets: precision ≥ 0.85, recall ≥ 0.85, median ₹ error ≤ 20% — per detector.

## Demo dataset (seed 42)

| Detector | Planted | Detected | TP | FP | FN | Precision | Recall | F1 | Median ₹ error | FP on decoys | Meets targets |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Bank/method failure spike | 10 | 10 | 10 | 0 | 0 | 1.00 | 1.00 | 1.00 | 11.1% | 0 | ✅ |
| UPI Collect instead of Intent | 10 | 10 | 10 | 0 | 0 | 1.00 | 1.00 | 1.00 | 7.5% | 0 | ✅ |
| Subscription silent churn | 10 | 10 | 10 | 0 | 0 | 1.00 | 1.00 | 1.00 | 7.3% | 0 | ✅ |

Overall: **30/30 planted leaks found, 0 false positives** (0 on decoy merchants). Median ₹ error 7.8%. Recommendation (cross-sell vs config review) correct on 100.0% of matches.


## Held-out seeds 101, 102, 103, 104, 105, 106, 107, 108, 109, 110 — untouched test seeds (thresholds were tuned while looking at seeds 42 and 1–5)

| Detector | Precision mean (min) | Recall mean (min) | Median ₹ error mean (max) | Total FP | Total FN |
|---|---|---|---|---|---|
| Bank/method failure spike | 1.00 (1.00) | 0.94 (0.80) | 11.4% (16.3%) | 0 | 6 |
| UPI Collect instead of Intent | 1.00 (1.00) | 1.00 (1.00) | 8.2% (11.8%) | 0 | 0 |
| Subscription silent churn | 1.00 (1.00) | 0.98 (0.90) | 5.9% (9.4%) | 0 | 2 |

Regenerate with `make eval`.
