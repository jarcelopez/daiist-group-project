# Design Document

## 1. Overview

A receipt photo goes through three steps in one flow:
1. A **quality gate** made of plain code.
2. The **vision component**: Gemini multimodal, which returns structured fields.
3. The **agent**: Gemini with tools, which checks the trip, the policy and the paid ledger.

**Guardrails** in code then enforce the hard policy rules and compute the final decision and amount. A **Thompson-sampling bandit** picks one of three agent prompt styles, and learns from the finance reviewers' 👍/👎.

The main design rule is: **the LLM judges plausibility, the code enforces the limits.** No prompt variant can pay more than the policy cap, and an LLM never rejects a receipt on its own.

Scope v1: Spanish receipts in euros. Categories: meals, supermarket, fuel, parking and tolls, taxi and public transport.

## 2. Decisions taken during planning

| # | Decision | Why |
|---|---|---|
| D1 | Gemini multimodal for vision, not the Roboflow detector | The available detectors were trained on US, Indian and Thai receipts, and a detector only finds boxes without reading them. The professor accepted Gemini on condition that the comparison is documented (REQ-13). |
| D2 | A 4-way vision comparison in the evaluation: detector alone, detector + Tesseract, detector + Gemini on crops, Gemini on the full image | Separates *finding* a field from *reading* it. Tesseract was suggested by the professor. |
| D3 | v1 = Spain and euros only. Non-euro receipts → review | The eval data is Spanish. Foreign receipts become real drift for the shifted set. |
| D4 | Baseline = approve everything at face value | Measurable on the cases without a human study. The 3 min/receipt is stated as an assumption. |
| D5 | A trip register, and the trip ID in the filename (`r07__T-104.jpg`) | "Justified" needs context. `check.py` sends a single input to `/run` and checks that `eval/...` paths exist. |
| D6 | The LLM judges plausibility, code enforces caps; an LLM never rejects alone | Safety, and it is easy to defend in the go/no-go decision. It also bounds the cost of the bandit's exploration. |
| D7 | Hotels, trains and flights out of scope (→ `rejected`, "booked centrally") | Their receipts carry the traveller's name. |
| D8 | Duplicates checked against a fixed paid ledger, not against the live logs | Keeps the evaluation repeatable when one image is used under two trips. |
| D9 | Bandit over 3 prompt styles; reward 1 / 0.5 / 0; Thompson sampling; state rebuilt from the logs | See §11. |
| D10 | An LLM judge only for the agent's free-text explanation | Decisions and amounts are checked in code. |
| D11 | Cumulative daily caps are a nice-to-have | v1 checks each receipt on its own. |
| D12 | Payment through MCP is out of scope | A person approves the `to_pay` queue in a batch. |

## 3. System architecture

### 3.1 Component map

| ID | Name | File | Type | Responsibility | Talks to |
|---|---|---|---|---|---|
| COMP-1 | App UI | `app.py` | Gradio | App tab (upload, trip, result, feedback), Monitoring tab | COMP-2, COMP-8, COMP-9 |
| COMP-2 | Pipeline | `pipeline.py` | Orchestrator | `run()`: parse the input, call COMP-3 → 4 → 5 → 6, build the record | COMP-3…7 |
| COMP-3 | Quality gate | `receipts/quality.py` | Plain code | Image statistics; resize | — |
| COMP-4 | Vision extractor | `receipts/vision.py` | Gemini multimodal | Photo → structured fields + confidences | COMP-10, Gemini |
| COMP-5 | Policy agent | `receipts/agent.py` | Gemini with function calling | Fields + trip → recommendation, reason codes, explanation | COMP-7, COMP-10, Gemini |
| COMP-6 | Guardrails | `receipts/rules.py` | Plain code | Hard rules → final decision and amount | COMP-7 |
| COMP-7 | Policy data and tools | `receipts/policy.py`, `data/*.csv` | Data + functions | `get_trip`, `get_policy`, `check_duplicate`, caps | — |
| COMP-8 | Bandit | `receipts/bandit.py` | RL | Choose the variant; rebuild posteriors from the logs and the prior | COMP-9 |
| COMP-9 | Logbook | `logbook.py` (template) | Storage | Requests and feedback, in Postgres online or JSONL locally | Postgres |
| COMP-10 | Gemini client | `receipts/llm.py` | Wrapper | Calls, retries, token and € accounting, disk cache | Gemini API |
| COMP-11 | Monitor | `monitor.py` (template) | Analytics | Quality signals, PSI drift, alerts, bandit views | COMP-9 |
| COMP-12 | Evaluation | `evaluate.py`, `eval/compare_vision.py`, `eval/make_shifted.py` | Offline | Component, comparison, end-to-end, judge, cost | COMP-2…7, Roboflow, Tesseract |

### 3.2 Architecture diagram

```
                 ┌──────────────────────────── App tab (COMP-1) ───────────────────────────┐
                 │ photo + trip dropdown ──► copy as upload__T-104.jpg ──► serve(path)      │
                 └───────────────────────────────────┬─────────────────────────────────────┘
                                                     ▼
 pipeline.run(path, variant=None)  (COMP-2)
   │
   ├─► parse trip_id from filename ──(none)──► pending_review NO_TRIP
   │
   ├─► [COMP-3] quality gate: brightness, contrast, blur, size; resize to ≤1600 px
   │
   ├─► [COMP-4] VISION  Gemini multimodal ──► fields{merchant, city, category, date, time,
   │                                                 total, currency, vat, is_receipt} + conf
   │
   ├─► [COMP-8] bandit picks variant (live only) ◄── posteriors from logs + prior
   │
   ├─► [COMP-5] AGENT  Gemini + tools ──► get_trip / get_policy / check_duplicate  [COMP-7]
   │            ──► recommendation, reason_codes, explanation
   │
   ├─► [COMP-6] GUARDRAILS (code): hard rules + cap ──► decision, amount_eur, reason_codes
   │
   └─► record {input_summary, features, outputs, versions, review, tokens, cost_eur, ...}
                                                     │
                       serve() adds latency ──► logbook.log_request() [COMP-9] ──► Postgres
                                                     │
            👍/👎 ──► logbook.log_feedback() ──► reward ──► bandit posteriors (next request)
                                                     │
                                         Monitoring tab [COMP-11]
```

## 4. Data flows

### 4.1 Live request
1. **User → COMP-1:** image (any name) + `trip_id` from the dropdown.
2. **COMP-1:** copies the image to a temp file `upload__<trip_id>.<ext>` → `serve(path)` → `pipeline.run(path)`.
3. **COMP-2:** splits the stem on the last `__`; the trip ID must match `^T-\d{3}$`.
4. **COMP-3:** opens the image with PIL, applies the EXIF orientation, makes a grey copy downscaled to ≤1,000 px for the statistics, and an RGB copy at ≤1,600 px (JPEG, quality 85) for Gemini.
5. **COMP-4:** sends the image and the vision prompt to Gemini, with a JSON schema for the response. Results are cached by `sha256(image bytes) + model + prompt version`.
6. **COMP-8:** chooses a variant (Thompson sampling) when `variant is None`.
7. **COMP-5:** sends the fields and the trip ID, with the variant's system prompt and the 3 tool declarations. Tool calls run locally against COMP-7. At most 4 rounds. Cached by `hash(fields, trip_id) + model + prompt version + variant`.
8. **COMP-6:** recomputes the hard rules from the same COMP-7 data, combines them with the agent's recommendation (§7.3), and computes the amount.
9. **COMP-2:** assembles the record (§7.5).
10. **`serve()`:** adds the latency and logs the record.

### 4.2 Feedback and the bandit
1. The reviewer presses 👍/👎 → `log_feedback(id, ±1)`.
2. On the next request, COMP-8 calls `logbook.load()` and keeps live requests that have a `variant` and feedback.
3. Reward: `r = 1` if 👍 and the decision ≠ `pending_review`; `0.5` if 👍 and `pending_review`; `0` if 👎.
4. Posterior per arm: `α = α_prior + Σr`, `β = β_prior + Σ(1−r)`. Sample θ from each; take the largest.

### 4.3 Offline evaluation
`evaluate.py` produces:
- vision metrics on `vision_labels.csv`
- the agent on labelled fields × 3 variants
- end to end on `cases.csv` with variant `balanced`
- the baseline
- the judge on the explanations
- cost and latency

Every file is written to `eval/` and committed. `eval/compare_vision.py` runs the 4-way comparison locally (Roboflow HTTP API + Tesseract).

### 4.4 Drift
`main.py reference` runs `pipeline.run(x, variant="balanced")` over `reference.csv` → `monitoring/reference_features.csv`. `drift-demo` does the same for the `normal` cases and `shifted.csv`, with the template's PSI rule. The committed cache makes this repeatable and cheap for the professor.

## 5. Integration points

### 5.1 Internal
| Source | Target | Interface | Format |
|---|---|---|---|
| App tab | Pipeline | `serve(path) → record` | dict |
| Pipeline | Vision | `extract(image_jpeg: bytes) → VisionResult` | dataclass → dict |
| Pipeline | Agent | `decide(fields: dict, trip_id: str, variant: str) → AgentResult` | dataclass → dict |
| Agent | Policy tools | Python functions exposed as Gemini function declarations | JSON args → dict |
| Pipeline | Guardrails | `apply(fields, conf, quality, trip_id, agent) → Decision` | dataclass |
| Pipeline | Bandit | `choose() → (variant, posteriors)` | str, dict |
| Bandit | Logbook | `logbook.load()` | DataFrame |

### 5.2 External
| System | Used by | Purpose | Auth | Failure handling |
|---|---|---|---|---|
| **Gemini API** (`google-genai` SDK; a current Flash model, exact ID pinned in `VERSIONS` when we implement, prices checked at the same time) | Vision, Agent, Judge | Extraction, tool calling, judging | `GEMINI_API_KEY` | 1 retry with backoff on 429/5xx; then `TOOL_ERROR` → review. Free-tier limits: disk cache; split the evaluation across days or keys |
| **Render Postgres** | Logbook | Logs | `DATABASE_URL` (set by Render) | Template behaviour |
| **Roboflow serverless API** `POST https://serverless.roboflow.com/receipt-rlnml/1` (base64 image, plain HTTP, no SDK) | Evaluation only | Detector comparison | `ROBOFLOW_API_KEY` (optional) | Skip the detector rows if the key is missing; results cached |
| **Tesseract 5 + `spa` language data** via `pytesseract` (dev group) | Evaluation only | Classic OCR comparison | — | Skip the row if not installed |

## 6. Components

### COMP-3 Quality gate
- `stats(img) → {brightness, contrast, blur, width, height}`.
- Blur = variance of a 3×3 Laplacian convolution done with numpy slicing, on the grey image downscaled to ≤1,000 px.
- `passes(stats) → (bool, failed: list[str])`. The thresholds (`MIN_BRIGHTNESS`, `MIN_CONTRAST`, `MIN_BLUR`, `MIN_SIDE`) start as placeholders. They are set to roughly the 2nd percentile of the reference set, and justified in REPORT.md.

### COMP-4 Vision extractor
- Prompt `prompts/vision_v1.txt`:
  - return the schema in §7.1, with a confidence for each field
  - classify into the policy categories
  - use `is_receipt=false` for menus, screenshots and other non-receipts
  - **never transcribe card numbers, people's names or personal tax IDs**
- The structured output is enforced with the SDK's response schema. Pydantic validates it; on failure, one retry.

### COMP-5 Policy agent
- Prompts:
  - a shared base, `prompts/agent_base_v1.txt`: the tools, the output schema, the reason codes
  - plus one of `agent_strict_v1.txt` ("when unsure → `pending_review`"), `agent_balanced_v1.txt`, or `agent_lenient_v1.txt` ("approve when plausible; review only clear problems")
- The tool-calling loop is handled by us (automatic function calling turned off), so tool calls are logged and the rounds are capped at 4.
- The agent sees the fields and their confidences. It does **not** see the image.

### COMP-6 Guardrails
Pure functions over the trip, the policy and the ledger (§8). They return the hard-rule hits as `(code, outcome)` pairs, where outcome is `rejected`, `review` or `cap`. The combination is described in §7.3.

### COMP-8 Bandit
- `choose(rng) → variant`.
- `posteriors() → {arm: {alpha, beta, mean, n_real}}`.
- `prior` is read from `monitoring/bandit_prior.json`: `{arm: {alpha, beta, source: "sim", weight: 0.2}}`.
- If the logs can't be read, the bandit uses the prior only, and records `bandit_state: "prior_only"`.

### COMP-10 Gemini client
- `call(model, contents, config, cache_key) → (response, usage{in, out, eur}, seconds, cache_hit)`.
- The € cost comes from a price table in code: `PRICES = {model: (eur_per_1M_in, eur_per_1M_out)}`, with the date the prices were checked.
- The cache is JSON files under `eval/cache/<component>/<key>.json`. A cache hit costs €0 and is flagged in the record.

## 7. Data models and contracts

### 7.1 Vision output
```json
{
  "is_receipt": true,
  "merchant": "Bar Casa Paco", "city": "Valencia", "category": "meals",
  "date": "2026-11-12", "time": "14:35",
  "total": 23.50, "currency": "EUR", "vat": 2.14,
  "confidence": {"is_receipt": 0.99, "merchant": 0.9, "city": 0.7, "category": 0.95,
                 "date": 0.97, "time": 0.9, "total": 0.98, "currency": 0.99}
}
```
`category` ∈ {`meals`, `supermarket`, `fuel`, `parking_tolls`, `taxi_transport`, `hotel`, `train_flight`, `personal`, `unknown`}.

### 7.2 Agent output
```json
{"recommendation": "to_pay", "reason_codes": ["OK"],
 "explanation": "Lunch in Valencia on day 2 of trip T-104, within the meal cap.",
 "tool_calls": ["get_trip", "get_policy", "check_duplicate"]}
```

### 7.3 Combining into the final decision
```
if not is_receipt (conf ≥ T)                → rejected  NOT_A_RECEIPT
hard 'rejected' hits whose fields have conf ≥ T → rejected
hard 'rejected' hits with low-conf fields    → pending_review LOW_CONFIDENCE
any hard 'review' hit                       → pending_review
agent recommends rejected                   → pending_review AGENT_REQUESTED_REVIEW
agent recommends pending_review             → pending_review
else                                        → to_pay, amount = min(total, cap)  (+OVER_CAP)
```
`review = (decision == "pending_review")`. T is the confidence threshold, set from the calibration in REQ-13.2.

### 7.4 Reason codes
| Code | Source | Outcome |
|---|---|---|
| `OK` | agent | — |
| `NOT_A_RECEIPT` | code | rejected |
| `CATEGORY_NOT_ALLOWED` (personal) | code | rejected |
| `CATEGORY_OUT_OF_SCOPE` (hotel, train, flight) | code | rejected |
| `DATE_OUTSIDE_TRIP` (> 1 day outside) | code | rejected |
| `DUPLICATE` | code | rejected |
| `NO_TRIP` | code | review |
| `LOW_IMAGE_QUALITY` | code | review |
| `LOW_CONFIDENCE` / `MISSING_FIELD` | code | review |
| `NOT_EUR` | code | review |
| `DATE_NEAR_TRIP_EDGE` (±1 day) | code | review |
| `ODD_HOUR` (00:00–05:59) | code | review |
| `OVER_AUTO_LIMIT` (> €150) | code | review |
| `OVER_CATEGORY_THRESHOLD` (fuel > €120, taxi > €60, supermarket > €15) | code | review |
| `TRANSPORT_MISMATCH` (fuel, parking or tolls on a trip not by `company_car`) | code | review |
| `TOOL_ERROR` | code | review |
| `OVER_CAP` | code | amount capped |
| `CITY_MISMATCH` | agent | agent decides |
| `CLIENT_MEAL_SUSPECTED` | agent | review |
| `NOT_A_MEAL` (supermarket) | agent | review |
| `AGENT_REQUESTED_REVIEW` | code | review |

### 7.5 Log record (on top of `REQUIRED_FIELDS`)
```
input_summary: "receipt image 1200x1600, trip T-104"
features: {brightness, contrast, blur, min_confidence, is_eur, log_total, n_missing}   # missing ones left out, never NaN
outputs:  {quality:{...}, vision:{fields, confidence, tokens, eur, s, cache_hit},
           agent:{recommendation, reason_codes, explanation, tool_calls, tokens, eur, s, cache_hit},
           guardrails:{hits:[...], decision, amount_eur}}
versions: {vision_model, vision_prompt:"v1", agent_model, agent_prompt:"v1", variant, policy:"v1", bandit:"v1"}
review, tokens, cost_eur, latency_s
extra:    trip_id, decision, amount_eur, reason_codes, variant, bandit_state
```

### 7.6 Data files
| File | Columns |
|---|---|
| `data/trips.csv` | `trip_id, employee_id, origin, destination, start_date, end_date, overnight, transport` (`company_car` / `train` / `plane`) |
| `data/policy.csv` | `category, allowed, daily_cap_day_trip, daily_cap_overnight, review_above, needs_car` |
| `data/paid_receipts.csv` | `merchant, date, total, trip_id, paid_on` |
| `eval/vision_labels.csv` | `image, receipt_id, is_receipt, merchant, city, category, date, time, total, currency` |
| `eval/cases.csv` | the template's columns. `expected` = `to_pay;<amount>` / `pending_review` / `rejected`. `baseline` = `to_pay;<total>` |
| `eval/human_labels.csv` | `id, label (good/bad), comment` |

## 8. Expense policy v1

| Category | Allowed | Cap / rule | Review if |
|---|---|---|---|
| meals | yes | One receipt ≤ €26.67 (day trip) or €53.34 (trip with an overnight stay); excess not paid (`OVER_CAP`) | Agent suspects a client meal |
| supermarket | yes, counted as meals | Same caps as meals | > €15, or the agent judges it is not a meal |
| fuel | only if `transport = company_car` | — | > €120 |
| parking_tolls | only for `company_car` | Parking ≤ €40/day (invented) | — |
| taxi_transport | yes | — | > €60 |
| hotel, train_flight | no (`CATEGORY_OUT_OF_SCOPE`, booked centrally) | — | — |
| personal | no | — | — |
| unknown | — | — | always |

**Rules for every receipt:**
- Date inside the trip → ok; ±1 day → review; further → rejected.
- 00:00–05:59 → review.
- Not EUR → review.
- > €150 → review.
- Already in the paid ledger → rejected.
- City differs from the destination → the agent judges whether it is on the route.

**Sources and status:**
- €53.34 and €0.26/km are confirmed as the 2026 IRPF exempt amounts.
- **€26.67 still has to be checked against art. 9 RIRPF.**
- €40, €60, €120, €15 and €150 are team choices.

## 9. Monitoring design

| View | Content |
|---|---|
| Alerts | Drift (PSI rule), 👎 share > threshold over the last 20+ rated requests |
| Summary | Template table, plus € auto-approved, review rate, the arm in use |
| Quality over time | Review share, 👎 share, mean `min_confidence`, not-a-receipt share |
| Queues per day | Counts of `to_pay` / `pending_review` / `rejected`; € auto-approved |
| Drift table | PSI per feature, the last 100 against the reference |
| Bandit | Posterior mean and 90% interval per arm; selection share over time; real against simulated counts |

**Threshold choice:**
- `PSI_ALERT` stays at 0.25, with the noise multiple at 2. With 30 reference inputs and about 10 normal ones, the effective bar is about 1.07.
- The false-alarm rate is estimated by resampling 20-request windows from the normal cases plus the reference set.
- Discrete features (`n_missing`, `is_eur`) are watched for false alarms. If one causes them, its values are grouped or it is dropped.

## 10. Evaluation design
| Part | Data | Metric | Output file |
|---|---|---|---|
| Vision (Gemini) | `vision_labels.csv` (~30–40 images) | Exact match on total, date and time; category accuracy; merchant CER; `is_receipt` accuracy; accuracy by confidence band | `eval/vision_results.csv` |
| Vision comparison | Same images | Detector: share with a `TotalPrice` box, mean confidence. Total accuracy for detector + Tesseract, detector + Gemini on the crop, Gemini on the full image | `eval/vision_comparison.csv` |
| Agent | Cases with labelled fields | Accuracy, confusion matrix, amount exact match, × 3 variants | `eval/agent_results.csv` |
| End to end | `cases.csv` | Accuracy; € leaked; € wrongly held back; wrong auto-approvals; safe automation rate; review rate; by kind; against the baseline | `eval/results.csv`, `eval/summary.md` |
| Judge | Explanations from the end-to-end run | Agreement and κ against `human_labels.csv` | `eval/judge_results.csv` |
| Cost | All runs | Tokens, €, seconds per component (median, p95) | in `summary.md` |
| Bandit prior | Agent results × 3 | Reward per case → Beta prior × 0.2 | `monitoring/bandit_prior.json` |

## 11. Bandit design
- **Arms:** strict / balanced / lenient. **Policy:** Thompson sampling, Beta–Bernoulli with fractional rewards.
- **Reward:**
  - 1 for a correct automatic decision
  - 0.5 for a correct `pending_review`, which costs about 3 min of a reviewer
  - 0 for a wrong decision
- **What the reward could reward by accident:**
  - If employees rated, lenient would win: only the reviewer rates.
  - Without the 0.5, strict would win by sending everything to review.
  - A reviewer who rates only clear cases makes the arms look better than they are.
- **Delay:** immediate in the demo; in production, about 1 day (the reviewer's batch).
- **Cost of exploring:** bounded by the guardrails. Lenient can't pay above a cap; it can only approve plausible-looking grey-zone cases. Strict costs reviewer minutes.
- **Real against simulated:** the prior is simulated, from the eval cases. Every live feedback row counts as real. The Monitoring tab shows `n_real` per arm.
- **Risks:**
  - Feedback loop: the favoured arm collects more ratings.
  - Non-response bias.
  - Reviewers who disagree with each other.
  - Rewards that change over time, e.g. after a policy change. Mitigation: reset the prior when the prompt or policy version changes.

## 12. Error handling
| Failure | Handling |
|---|---|
| Missing `GEMINI_API_KEY` | Banner (template) + `gr.Error` naming the key |
| Gemini 429/5xx/timeout | 1 retry with backoff, then `TOOL_ERROR` → `pending_review`, and still logged |
| Invalid JSON from vision or agent | 1 retry, then `TOOL_ERROR` |
| Unknown trip ID | `NO_TRIP` → review |
| Image unreadable by PIL | `gr.Error`; not logged |
| Logs unreachable for the bandit | Prior only; `bandit_state: "prior_only"` |
| Roboflow or Tesseract missing (evaluation) | Skip that comparison row, with a warning |

## 13. Testing
- **Smoke:** `check.py` (local and `--url`) and `main.py drift-demo`.
- **Unit tests** (pytest, dev only):
  - guardrail rules and decision precedence: one test per reason code
  - trip-ID parsing
  - quality statistics on synthetic images
  - bandit update and reward mapping
- **First day:** check that the upload filename survives `check.py --url` (D5).

## 14. Deployment
- `render.yaml`: `name` → the product's name.
- Runtime dependencies: `google-genai`, `pillow`, `pydantic` (plus the template's).
- Dev group: `pytesseract`, `pytest`.
- `.env.example`:
  - `GEMINI_API_KEY=  # vision extraction, policy agent, judge`
  - `ROBOFLOW_API_KEY=  # (optional) detector comparison in the evaluation only`
  - `DISABLE_CACHE=  # (optional) 1 = always call Gemini`
- Repo layout:
  ```
  receipts/  quality.py vision.py agent.py rules.py policy.py bandit.py llm.py
  prompts/   vision_v1.txt agent_base_v1.txt agent_{strict,balanced,lenient}_v1.txt judge_v1.txt
  data/      trips.csv policy.csv paid_receipts.csv
  eval/      inputs/ cache/ vision_labels.csv cases.csv reference.csv shifted.csv human_labels.csv
             compare_vision.py make_shifted.py  (+ result files)
  monitoring/ reference_features.csv bandit_prior.json
  docs/      requirements.md design.md tasks.md
  ```
- Memory: PIL on a 12 MP photo needs about 40 MB; there is no ML framework at runtime. Well under 512 MB.

## 15. Security and privacy
- Keys only from the environment; `.env` is git-ignored.
- No raw OCR text and no images in the logs; pseudonymous employee IDs.
- Personal data covered on images before they are committed. The vision prompt forbids transcribing personal data.
- Money: the product only fills queues. Payment stays a human, batched action (D12).
