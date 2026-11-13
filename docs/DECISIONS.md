# Implementation decisions & deviations from HANDOFF.md

Each entry says what was decided and why. Spec items marked LOCKED are only deviated from with a reason.

## Stack / layout
- **One installable package `leakradar/`** (generator, detectors, eval, agent, api, common) instead of
  top-level packages. CLIs are `python -m leakradar.generator`, `python -m leakradar.detectors.run`,
  `python -m leakradar.eval`; `make` wraps them.
- **Python:** `requires-python >=3.11`; developed on 3.14.
- **SQLite via plain `sqlite3`** (single file `data/leakradar.db`). SQLAlchemy would add nothing here.
- **Amounts are whole rupees** (`amount_inr` INTEGER). All ₹ outputs are integer rupees.
- **Plain CSS** with variables (neutral palette), no Tailwind.

## Data model additions
- `payments.order_id` links retries to their first attempt (needed for D1's
  `already_recovered_by_retry_share`); `payments.customer_id` supports D3's "clean history" check.
- `leaks` gains `annual_churn_inr` (D3), `dimension`, `stats`, `root_cause`, `fit_product`,
  `recommendation_type` so every UI number traces to a stored, explainable field.
- `pitches.tone`, `pitches.created_at`; a `meta` table (seed, window dates, data hash).
- `ground_truth.json` is an object `{seed, days, planted: [...], decoys: [...]}` rather than a bare list,
  so decoys can be scored for false positives.

## Generator
- Vectorised per merchant: Poisson counts on a day×hour intensity grid → `np.repeat` → categorical
  draws → one uniform draw per row decides customer / system / planted-spike failure.
- Determinism: `SeedSequence(seed)` spawned per merchant; data hash stored in `meta`.
- **Ground truth is not circular:** `expected_monthly_inr` = Σ over realised traffic of the *true planted
  probability* × amount × 30/days. The detector's estimate comes from realised outcomes and its own
  baseline estimate, so the ₹ error measures estimation error against the planted effect.
  - D1 truth = planted excess only. D2 truth = (intent SR − collect SR) on mobile collect rows.
  - D3 truth = expected *all* system failures (the locked D3 formula counts all of them, not the excess).
- Sale-day D1 plants use only card / UPI-intent: four sale days of netbanking traffic for one bank
  rarely clears `min_attempts`, i.e. such a plant is below the detector's stated scope.
- Volume: ~650–720k payment rows. GMV band is *derived* from generated successful GMV.

## Detectors
- **D1 windows (challenge #1):** a +20 pt spike lasting 4 h/day is ~+3 pts over 90 days and would never
  pass `min_excess_pts`. D1 scans the whole period, sale days (inferred from platform traffic ≥ 2.5×
  median day) and sliding 4/6/8 h hour-of-day windows. The highest-z flagged window is kept and sized.
- **D1 multiple testing:** p-values are corrected hierarchically — per (merchant, method, bank) cell,
  min-p across windows × windows tested (Bonferroni), then Benjamini–Hochberg across cells.
  Plain BH over every window was tried first and produced false positives: one real spike's many
  overlapping significant windows inflate the rejection count and loosen the bar for noise.
- D1/D2 use **first attempts of non-recurring payments**; recurring debits belong to D3.
- **D2 at-risk** is not defined in the spec; it is the recoverable formula without `switchable_share`.
  The `sr_gap ≥ 0.08` gate is effectively always true (95% vs 80% baselines); `collect_share` does the work.
- **D3 baseline** is the median merchant rate (not pooled) so planted merchants can't inflate it;
  one-sided binomial test + BH across merchants.

## Evaluation
- Match keys: D1 `(merchant, method, bank)`; D2/D3 `(merchant)`.
- **Dev vs test seeds:** thresholds were tuned while looking at seeds 42 and 1–5. Reported held-out
  numbers use seeds 101–110, which were not looked at during tuning.
