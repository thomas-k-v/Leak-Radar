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

## Agent, API and guard
- **Default model `claude-sonnet-5-5`** (approved in the plan, for cost), overridable with `LLM_MODEL`;
  `LLM_EFFORT` defaults to `medium`. Server-side refusal fallback (`fallbacks: "default"`) is on by default,
  `LLM_FALLBACKS=off` disables it.
- **No token-level text streaming.** The guard must see a whole text block before it is shown, so each
  block is released after checking. Status and component events still stream live. The provider uses
  `messages.stream()` + `get_final_message()` for timeout safety.
- Tools are not `strict: true`: `render_component.props` is a free-form object, which strict mode can't
  express. Inputs are validated server-side instead (Pydantic + DB existence + metric/detector fit).
- **Guard allow-list** = numbers in this turn's tool outputs (numeric leaves *and* figures written in their
  string fields), plus figures in the AM's own message. On violation: one regeneration with a note naming
  the offending figures; if it fails again, those figures are redacted. Violations go to `logs/guard.jsonl`.
- **Pitch drafting** happens in one LLM: `draft_pitch` takes an optional `message_template` with
  `{placeholders}`, the guard rejects any raw figure in it, and code fills the values. Without a template a
  built-in one is used (the mock always does this).
- `get_leak_detail` returns summary stats, not daily trend series. The series go only to the UI over REST,
  which keeps the guard's allow-list small (challenge #6 in the plan).
- `list_top_leaks` gains `recommendation` / `product` / `merchant_id` filters (needed for demo step 5) and
  returns a precomputed total, so the model never has to add figures up.
- Conversations live in memory (single-user local demo). The mock LLM is a deterministic intent router that
  writes prose only from tool outputs. `MOCK_LLM_BAD_NUMBERS=once|always` exercises the guard.

## Web
- Charts use the validated categorical palette (slots 1–4: bank, merchant, network, gateway). Customer-caused
  failures are neutral grey because they are not a leak. Light-mode aqua and yellow are under 3:1 contrast
  on the surface, so the root-cause legend shows every share as a visible label.
- Trend for D3 is daily over days with ≥ 3 renewals (debits cluster on cycle days).
