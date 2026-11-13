# Leak Radar — Build Handoff

> Original build spec (sections 9, 10 and 14 — API sketch, repo layout, plan questions — superseded by the approved plan). Implementation decisions and deviations are recorded in
> [`docs/DECISIONS.md`](docs/DECISIONS.md).

---

## 1. What we are building

**Leak Radar** is an AI cross-sell copilot for payment-gateway account managers (AMs). An AM asks a question in chat; the agent finds where a merchant is losing money, sizes it in ₹, shows the evidence, recommends the payment product that fixes it, and drafts an outreach message the AM must approve.

It is a **portfolio project** for an application to Razorpay's AI Builder track. It runs entirely on **synthetic data**.

**Who it's for (persona):** an AM covering ~150 SMB/mid-market merchants who cannot manually analyse each merchant's transactions.

**Core loop:** detect leak → size it (₹/month) → explain root cause → map to product → draft pitch → human approves.

---

## 2. Non-goals (do NOT build)

- No real Razorpay data, APIs, logos, colours or brand styling. The UI must look like a neutral independent tool.
- No auto-sending of messages. "Approve" only changes a status in the DB.
- No lending/credit recommendations (regulatory risk) in v1.
- No MDR simulator, account-freeze kit, TDS, Soundbox — out of scope.
- No auth, multi-user, or deployment infra beyond `docker compose up` / local run.
- No LLM-generated HTML/JSX. The LLM never emits markup.

---

## 3. LOCKED architectural rules

1. **Numbers come from code, never from the LLM.** Every ₹ figure, rate, and count is computed by Python detectors and stored with an ID. UI components receive **IDs** (e.g. `leak_id`, `merchant_id`) and fetch numbers from the API. The LLM may only reference numbers that appear in tool outputs; a post-check must reject any assistant text containing a number not present in that turn's tool results (allow small integers ≤ 10 and dates).
2. **Fixed component library.** The agent renders UI only by calling `render_component(type, props)` where `type` is one of 6 registered components and `props` is validated against a schema (Pydantic on backend, zod/TS types on frontend). Invalid calls are rejected and the error returned to the agent.
3. **Synthetic data is labelled** everywhere: README, UI header badge ("Synthetic demo data"), and footer disclaimer: *"Independent concept project. Not affiliated with or endorsed by Razorpay. Product names are used only to illustrate fit."*
4. **Deterministic generation:** data generator takes a seed; same seed → identical data and identical ground truth.
5. **Evaluation is a first-class deliverable**, not an afterthought (see §7).

---

## 4. Stack (LOCKED unless justified otherwise)

- **Backend:** Python 3.11, FastAPI, pandas, numpy, scipy (stats tests), Pydantic v2, SQLite (via SQLAlchemy or plain sqlite3 — OPEN).
- **LLM:** Anthropic SDK with tool use (model name from env `LLM_MODEL`). Provider wrapped behind an interface so a **mock LLM** can drive tests and an offline demo.
- **Streaming:** Server-Sent Events from backend to frontend. Message parts: `{type: "text", text}` | `{type: "component", component, props}`.
- **Frontend:** React + Vite + TypeScript, Recharts for charts. Plain CSS or Tailwind (OPEN). Neutral palette.
- **Tests:** pytest (backend), vitest for component schema validation (light).
- **Config:** `.env` for `ANTHROPIC_API_KEY`, `LLM_MODEL`; `config/assumptions.yaml` for all sizing assumptions; `config/products.yaml` for product catalogue.

---

## 5. Data model (LOCKED fields; columns may be added)

### `merchants`
| field | type | notes |
|---|---|---|
| merchant_id | str | `M001`… |
| name | str | fictional business names only |
| category | enum | d2c_fashion, electronics, saas, edtech, food, travel, services |
| city_tier | enum | tier1, tier2, tier3 |
| monthly_gmv_band | enum | <10L, 10L-1Cr, 1Cr-10Cr |
| has_subscriptions | bool | |
| products_active | list[str] | e.g. `["payment_gateway"]`; used to avoid recommending a product they already have |
| am_owner | str | fictional AM name |

### `payments` (90 days, ~150 merchants; target 300k–800k rows total)
| field | type | notes |
|---|---|---|
| payment_id | str | |
| merchant_id | str | |
| ts | datetime | IST; include sale-event days with traffic spikes |
| amount_inr | int | paise or rupees — pick one, document it |
| method | enum | upi_intent, upi_collect, card, netbanking, intl_card |
| issuer_bank | enum | ~8 fictional labels: `BANK_A`…`BANK_H` (no real bank names) |
| device | enum | mobile, desktop |
| status | enum | success, failed |
| error_code | str/null | from the **synthetic** taxonomy below |
| error_layer | enum/null | merchant, gateway, bank, network |
| is_recurring | bool | |
| subscription_id | str/null | |
| attempt_no | int | 1 for first try, 2+ for retries |

### Synthetic error taxonomy (`config/error_codes.yaml`) — synthetic, not real NPCI/network codes
- `BANK_TIMEOUT`, `BANK_DOWN` → layer bank
- `NETWORK_DROP` → network
- `OTP_TIMEOUT`, `SESSION_EXPIRED` → network/bank (cards)
- `COLLECT_EXPIRED` (user didn't approve collect request) → merchant (integration choice)
- `MANDATE_PRENOTIFY_FAILED`, `MANDATE_NOT_ACTIVE` → bank (recurring)
- `INSUFFICIENT_FUNDS`, `USER_CANCELLED` → customer (counts as *not* a leak)
- `GATEWAY_ERROR` → gateway

### `subscriptions`
`subscription_id, merchant_id, customer_id, plan_amount_inr, cycle (monthly), start_date, status`

### `leaks` (written by detectors)
`leak_id, merchant_id, detector, severity, monthly_inr_at_risk, recoverable_inr_monthly, assumptions_used (json), evidence_query (json filter to reproduce), recommended_product, created_at`

### `pitches`
`pitch_id, leak_id, draft_text, status (draft|approved|rejected), edited_text, updated_at`

### Ground truth: `data/ground_truth.json`
List of planted leaks: `{merchant_id, detector, planted_params, expected_monthly_inr}`.

---

## 6. Synthetic data generator (`generator/`)

- Baseline success rates by method (configurable): upi_intent ~95%, upi_collect ~80%, card ~88%, netbanking ~91%, intl_card ~75%. Realistic noise, weekday/hour seasonality, 3–4 sale-event days with 3–5× traffic and mild baseline degradation.
- **Planted leaks (~30 merchants)**, with overlap allowed:
  - **10 × Bank/method spike:** one `(issuer_bank, method)` pair fails at baseline + 10–25 pts during specific hours or sale days.
  - **10 × UPI Collect-heavy:** 30–60% of UPI attempts via collect (vs ~5–10% baseline), mostly on mobile.
  - **10 × Subscription silent churn:** elevated `MANDATE_PRENOTIFY_FAILED` failures on recurring debits for customers whose history shows no `INSUFFICIENT_FUNDS`.
- **~10 decoy merchants:** low volume with noisy failure rates but no real pattern. Plus some merchants with high `INSUFFICIENT_FUNDS` (customer-caused, must NOT be flagged as recurring leak).
- Some planted merchants already have the recommended product in `products_active` → detector should still find the leak but the recommender must say "already active — check configuration".
- CLI: `python -m generator --seed 42 --merchants 150 --days 90 --out data/`.

---

## 7. Detectors — formulas LOCKED, thresholds in `assumptions.yaml`

All detectors are pure functions: `(payments_df, merchants_df, config) -> list[Leak]`.

### D1. Bank/method failure spike → product: `optimizer`
- For each merchant × `(method, issuer_bank)`: failure rate (excluding customer-caused codes) vs the merchant's own baseline for that method across other banks.
- Flag if: attempts ≥ `min_attempts` AND excess ≥ `min_excess_pts` AND two-proportion z-test p < 0.01.
- `excess_failures = attempts × (rate − baseline_rate)`
- `monthly_inr_at_risk = excess_failures × avg_ticket × (30 / days_observed)`
- `recoverable = monthly_inr_at_risk × routing_recovery_rate × (1 − already_recovered_by_retry_share)`
- Also store the top error_layer distribution for the RootCauseCard.

### D2. UPI Collect instead of Intent → product: `checkout_upi_intent`
- Per merchant: `collect_share = collect_attempts / upi_attempts`; `sr_gap = intent_sr − collect_sr` (fallback to platform average if intent volume too low).
- Flag if `collect_share ≥ 0.25` AND `sr_gap ≥ 0.08` AND upi_attempts ≥ 300.
- `recoverable = collect_attempts_on_mobile × sr_gap × avg_upi_ticket × switchable_share × (30 / days)`

### D3. Subscription silent churn → product: `subscriptions_autopay_retries`
- Recurring debits only. Split failures into **involuntary-system** (`MANDATE_PRENOTIFY_FAILED`, `MANDATE_NOT_ACTIVE`, `BANK_TIMEOUT`) vs **customer** (`INSUFFICIENT_FUNDS`, `USER_CANCELLED`).
- Flag if system-failure rate on recurring > platform baseline by ≥ `min_excess_pts` AND ≥ `min_failed_renewals`.
- `monthly_inr_at_risk = system_failed_renewals × plan_amount × (30 / days)`
- `annual_churn_inr = monthly_inr_at_risk × 12 × churn_given_failure`
- `recoverable = monthly_inr_at_risk × retry_recovery_rate`

### Common
- Every Leak stores the exact assumptions used.
- `evidence_query` stores filters to reproduce the underlying rows.
- Recommender: map detector → product from `products.yaml`; if product already active, recommend `config_review`.
- `products.yaml` entries have `name`, `one_line_value`, `verified: false` + TODO: *"Verify current product names and features on Razorpay's public site before demo."*

### Evaluation (`eval/`)
- `python -m eval --data data/` → per detector: precision, recall, F1; median absolute % error of `monthly_inr_at_risk` vs `expected_monthly_inr`; false positives on decoys.
- **Targets:** recall ≥ 0.85, precision ≥ 0.85 per detector, ₹ error ≤ 20% median.
- Output `eval/report.md` + JSON summary at `GET /api/eval`, shown in the UI footer.

---

## 8. Agent

System prompt essentials: role = AM copilot; only state numbers from tool results; always show evidence on request; never promise outcomes; drafts are for human approval; say "synthetic data" if asked about data source.

### Tools
| tool | input | returns |
|---|---|---|
| `list_top_leaks` | `{limit, detector?, min_inr?}` | ranked list of `{leak_id, merchant_id, merchant_name, detector, recoverable_inr_monthly}` |
| `get_merchant_profile` | `{merchant_id}` | merchant row + active products + leak_ids |
| `get_leak_detail` | `{leak_id}` | full leak incl. assumptions, root-cause layer split, trend series |
| `get_evidence` | `{leak_id, limit}` | sample of underlying payment rows |
| `draft_pitch` | `{leak_id, tone}` | creates `pitch_id`; numbers injected by template |
| `render_component` | `{type, props}` | validated → streamed to UI |

### Components — props are IDs, not numbers
| type | props | shows |
|---|---|---|
| `MerchantLeakTable` | `{leak_ids}` | ranked table, click row → asks agent for detail |
| `LeakCard` | `{leak_id}` | ₹ at risk, recoverable, product, "how calculated" |
| `RootCauseCard` | `{leak_id}` | stacked bar of failure share by layer |
| `TrendChart` | `{leak_id, metric}` | daily series vs baseline |
| `DraftMessage` | `{pitch_id}` | editable text, Approve / Reject |
| `EvidenceDrawer` | `{leak_id}` | paginated raw rows |

### Guardrail
`number_guard.py`: before streaming assistant text, extract numbers; reject/regenerate (max 1 retry) if any number isn't in the turn's tool outputs. Log violations. Unit-tested.

---

## 11. Milestones (7 working days)

| day | deliverable |
|---|---|
| 1 | Generator + ground truth |
| 2 | D1, D2, D3 + recommender |
| 3 | Eval harness |
| 4 | API + agent tools + mock LLM + number guard |
| 5 | React components + SSE chat |
| 6 | Real LLM wiring + polish |
| 7 | README, screenshots, demo recording |

## 12. Demo script (acceptance test)
1. "Which of my merchants are losing the most this month?" → `MerchantLeakTable`
2. Click top merchant → `LeakCard` + `RootCauseCard` + `TrendChart`
3. "Draft a pitch, consultative tone" → `DraftMessage`; edit one line; Approve
4. "Show me why" → `EvidenceDrawer`
5. "Which merchants already have Optimizer but still leak?" → table with `config_review` recommendations
6. Footer shows eval summary.

## 13. Acceptance criteria
- `make demo` runs generator → detectors → eval → API → web.
- All ₹ values in UI trace to a `leak_id` with stored assumptions.
- Number guard tests pass; no LLM-authored numbers in the demo run.
- Eval targets met or gaps explained in README.
- Disclaimer + synthetic badge visible.
- Works offline with mock LLM (`LLM_PROVIDER=mock`).
