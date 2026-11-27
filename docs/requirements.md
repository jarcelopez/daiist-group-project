# Requirements Document

## Introduction

A web app that checks business-trip expense receipts for a medium-large Spanish company. An employee (or the finance clerk on their behalf) uploads a photo of a receipt and picks the trip it belongs to. A multimodal vision model reads the receipt, and an LLM agent checks it against the trip register and the expense policy. The receipt then lands in one of three queues: **to pay**, **pending review** (a person decides) or **rejected**. The app is a public Gradio app on Render's free plan, with an App tab and a Monitoring tab. It logs every request, watches itself for drift, and uses a bandit to learn which agent prompt style the finance reviewers agree with.

Version 1 covers **receipts from Spain in euros** only.

### Business case (summary)

| Item | Value | Status |
|---|---|---|
| Trips per month | 1,000 | Team assumption |
| Receipts per trip (1-day to 1-week trips) | ~6 | Team assumption |
| Receipts per month | ~6,000, about 300 per working day | Derived |
| Reviewer time per receipt today | ~3 min | Team assumption |
| Review effort today | ~300 h/month, about 2 FTE | Derived |
| Loaded cost of a finance clerk | ~€25/h, so ~€7,500/month | Team assumption |
| Average receipt | ~€30, so ~€180k/month of expenses | Team assumption |

- **Today:** employees send receipt photos over WhatsApp. One person reads each photo, checks it against the trip and the policy, and records the payment.
- **Baseline to beat:** "approve everything at face value", meaning every receipt is paid in full. This is what an overloaded reviewer effectively does. Its time cost is the assumed 3 min per receipt.
- **Value:**
  - Reviewer hours saved = safe automation rate × €7,500/month.
  - Plus leakage avoided: money the baseline pays that the policy does not allow.
- **Success metrics:**
  - Safe automation rate
  - € leaked against the baseline
  - Number of wrong auto-approvals
  - Cost and latency per receipt

## Glossary

- **Receipt**: a photo of a till receipt or simplified invoice (*factura simplificada*) from a Spanish merchant.
- **Trip**: one business trip in the trip register (`trip_id`, pseudonymous employee ID, origin, destination, dates, overnight stay, transport mode).
- **Trip register**: `data/trips.csv`. It is synthetic and written around the collected receipts.
- **Policy**: `data/policy.csv`. It holds the allowed categories, caps and thresholds (design §8).
- **Paid ledger**: `data/paid_receipts.csv`. It is a synthetic history of receipts that were already paid, used to detect duplicates.
- **Quality gate**: checks of brightness, contrast, blur and resolution, made with plain code before any model call.
- **Vision component**: Gemini multimodal. It turns the photo into structured fields, each with a confidence value.
- **Agent**: Gemini with function calling. It looks up the trip, the policy and the ledger, and recommends a decision.
- **Guardrails**: deterministic code that enforces the hard policy rules and computes the final decision and amount.
- **Decision**: one of `to_pay` (with an amount in €), `pending_review` or `rejected`.
- **Reason code**: a fixed code for why a decision was made (design §7.4), e.g. `DATE_OUTSIDE_TRIP`.
- **Explanation**: one or two sentences of free text for the reviewer, written by the agent.
- **Prompt variant**: one of the agent's three system prompts: `strict`, `balanced` or `lenient`.
- **Bandit**: a Thompson-sampling policy that picks the prompt variant for each request.
- **Reviewer**: the finance person who checks decisions and gives 👍/👎. This is the only source of reward.
- **Safe automation rate**: the share of receipts decided automatically (`to_pay` or `rejected`) *and* correctly.
- **Leakage**: euros paid that the policy does not allow.
- **Reference set**: `eval/reference.csv`, normal inputs (the second photo of each receipt) used as the base for drift.
- **Shifted set**: `eval/shifted.csv`, inputs shifted on purpose: dark, blurred or rotated copies, and foreign receipts.
- **PSI**: Population Stability Index, the drift measure in `monitor.py`.

## Requirements

### REQ-1 Submit a receipt

**User story:** As a finance clerk, I want to upload a receipt photo and pick its trip, so that the receipt is checked against the right trip.

1. THE App tab SHALL accept one image (JPEG or PNG) and a trip chosen from a dropdown filled from the trip register.
2. WHEN the user runs a request, THE app SHALL save a copy of the image named `<anything>__<TRIP_ID>.<ext>` and pass that path to `serve()`. The `/run` endpoint keeps a single input (needed for `check.py`).
3. THE pipeline SHALL read the trip ID from the part of the filename after the last `__`. IF there is no valid trip ID, THEN THE pipeline SHALL send the receipt to `pending_review` with reason `NO_TRIP`.
4. IF no image is given, THEN THE app SHALL show an error and SHALL NOT log a request.

### REQ-2 Image quality gate

**User story:** As a reviewer, I want unreadable photos caught before any decision, so that the product never decides from a bad image.

1. THE quality gate SHALL compute brightness (mean grey level, 0–255), contrast (standard deviation of grey levels), blur (variance of the Laplacian) and resolution for every image, before any model call.
2. IF any of these is below its threshold, THEN THE guardrails SHALL send the receipt to `pending_review` with reason `LOW_IMAGE_QUALITY`. The thresholds are set from the reference set and justified in REPORT.md.
3. THE quality gate SHALL use only PIL and numpy, so it runs within Render's 512 MB.

### REQ-3 Extract the receipt's fields (vision component)

**User story:** As the agent, I want the receipt's key fields as structured data, so that I can check them against the trip and the policy.

1. THE vision component SHALL return JSON with:
   - `is_receipt`
   - `merchant`
   - `city`
   - `category` (one of the policy categories, or `unknown`)
   - `date` (YYYY-MM-DD)
   - `time` (HH:MM, or null)
   - `total`, `currency`, `vat`
   - a confidence value between 0 and 1 for each field
2. THE vision component SHALL resize the image to at most 1,600 px on its longest side before sending it.
3. THE vision component SHALL return its token counts, cost in € and latency.
4. IF the Gemini call fails or returns invalid JSON after one retry, THEN THE guardrails SHALL send the receipt to `pending_review` with reason `TOOL_ERROR`.

### REQ-4 No personal data

**User story:** As the company, I want no personal data in the product, so that it complies with the assignment and with GDPR.

1. THE vision prompt SHALL tell the model not to transcribe card numbers, names of people, or personal tax IDs.
2. THE log record SHALL contain only the extracted fields, image statistics and decisions. It SHALL NOT contain any OCR text of the whole receipt or the image itself.
3. THE `input_summary` SHALL be of the form `receipt image <W>x<H>, trip <TRIP_ID>`.
4. Every image committed to the repo SHALL have card digits, people's names and personal tax IDs covered before it is committed.
5. THE trip register SHALL use pseudonymous employee IDs such as `E-017`.

### REQ-5 Agent checks the receipt against the trip and the policy

**User story:** As a reviewer, I want each receipt checked against its trip and the policy, so that I only see the cases that need a person.

1. THE agent SHALL receive the vision output, the trip ID and the system prompt of the chosen prompt variant.
2. THE agent SHALL have these tools: `get_trip(trip_id)`, `get_policy(category)` and `check_duplicate(merchant, date, total)`.
3. THE agent SHALL return JSON with:
   - `recommendation`: `to_pay` / `pending_review` / `rejected`
   - `reason_codes`: a list of codes
   - `explanation`: at most 2 sentences
   - `tool_calls`: the names of the tools it called
4. THE agent SHALL use its judgment for:
   - whether a receipt from another city fits the trip's route (`CITY_MISMATCH`)
   - suspected client meals (`CLIENT_MEAL_SUSPECTED`)
   - whether a supermarket receipt is a meal
   - category `unknown`
5. THE agent SHALL make at most 4 tool-call rounds per request. IF the limit is reached, THEN THE guardrails SHALL send the receipt to `pending_review` with reason `TOOL_ERROR`.

### REQ-6 Guardrails and the final decision

**User story:** As the company, I want hard policy rules enforced by code and not by an LLM, so that the product can never pay more than the policy allows.

1. THE guardrails SHALL apply the hard rules of design §8 independently of the agent, using the same trip, policy and ledger data.
2. THE final decision SHALL be, in order of precedence:
   - any hard `rejected` rule → `rejected`
   - else any hard review rule → `pending_review`
   - else the agent's recommendation, where an agent `rejected` is turned into `pending_review`, because an LLM never rejects on its own
3. WHERE the decision is `to_pay`, THE amount SHALL be `min(total, cap)`, computed in code. IF the cap applies, THEN the reason codes SHALL include `OVER_CAP`.
4. A hard `rejected` rule SHALL apply only if the fields it relies on have confidence ≥ the confidence threshold. Otherwise the receipt SHALL go to `pending_review` with `LOW_CONFIDENCE`.
5. THE guardrails SHALL list every rule that fired, as reason codes.

### REQ-7 Show the decision

**User story:** As a finance clerk, I want to see the decision, the amount and why, so that I can trust or challenge it.

1. THE App tab SHALL show:
   - the extracted fields
   - the final decision and amount
   - the reason codes
   - the agent's explanation
   - the prompt variant used
   - latency and cost
2. WHERE the decision is `pending_review`, THE App tab SHALL say so clearly.

### REQ-8 Log every request

**User story:** As the product owner, I want every request logged, so that I can monitor, evaluate and train the bandit.

1. THE app SHALL log every request with all the fields in `logbook.REQUIRED_FIELDS`, plus `trip_id`, `decision`, `amount_eur`, `reason_codes` and `variant`.
2. `outputs` SHALL contain `quality`, `vision`, `agent` and `guardrails`.
3. `versions` SHALL contain the vision model and prompt version, the agent model and prompt version, the variant, the policy version and the bandit version.
4. `tokens` and `cost_eur` SHALL be the sums over all Gemini calls of the request.
5. Features that cannot be computed SHALL be left out of `features` rather than set to NaN, because Postgres JSONB rejects NaN.

### REQ-9 Reviewer feedback

**User story:** As a reviewer, I want to say whether a decision was correct, so that the product learns which style I agree with.

1. THE App tab SHALL show the question "Reviewer: was this decision correct?" with 👍 and 👎 buttons.
2. WHEN a button is pressed, THE app SHALL save the feedback with `logbook.log_feedback`. The latest feedback for a request wins.

### REQ-10 Monitoring: quality over time

**User story:** As the product owner, I want to see quality signals over time, so that I notice when the product gets worse.

1. THE Monitoring tab SHALL plot over time:
   - the share sent to review
   - the share of 👎 among rated requests
   - the mean of the lowest field confidence
   - the share of inputs that are not receipts
2. THE Monitoring tab SHALL show the number of requests per decision per day, and the € approved automatically per day.
3. THE Monitoring tab SHALL show, for each arm of the bandit, its posterior mean and how often it was chosen over time.

### REQ-11 Drift check and alerts

**User story:** As the product owner, I want an alert when today's inputs differ from the ones we tested with, so that I don't trust numbers that no longer apply.

1. THE drift check SHALL compare these features of the last 100 requests with the reference set, using PSI:
   - brightness, contrast, blur
   - lowest field confidence
   - `is_eur`
   - `log_total`
   - number of missing fields
2. THE drift alert SHALL fire when a feature's PSI is above both `PSI_ALERT` and 2× the PSI expected by chance (the template's rule).
3. WHEN `main.py drift-demo` runs, THE alert SHALL fire on `eval/shifted.csv` and stay quiet on the `normal` cases.
4. THE 👎 alert SHALL fire when the share of 👎 among the last 20 or more rated requests is above a threshold set from the evaluation and justified in REPORT.md.
5. REPORT.md SHALL report the false-alarm rate of the drift alert on normal inputs, estimated by resampling.

### REQ-12 Prompt-variant bandit (RL bonus)

**User story:** As the product owner, I want the app to learn which prompt style reviewers agree with, so that it improves from its own feedback.

1. THE bandit SHALL pick one of `strict`, `balanced` or `lenient` for each live request, by Thompson sampling with one Beta posterior per arm.
2. THE reward SHALL be:
   - 1 for 👍 on an automatic decision
   - 0.5 for 👍 on `pending_review`
   - 0 for 👎
   - the update: α += r, β += 1 − r
3. THE posterior SHALL be recomputed from the logs on every request: the prior, plus every rated live request.
4. THE prior SHALL come from each variant's results on the evaluation cases, scaled by 0.2, saved in `monitoring/bandit_prior.json` and labelled as simulated.
5. `pipeline.run(raw_input, variant=None)` SHALL use the bandit only when `variant` is None. The evaluation, the reference set and the drift demo SHALL use a fixed variant.
6. REPORT.md SHALL show:
   - the loop closing
   - the reward and how it could be gamed
   - the cost of exploring
   - real against simulated data
   - the risks (feedback loops, who gives feedback, calibration)

### REQ-13 Vision evaluation and model comparison

**User story:** As the team, I want evidence that Gemini reads Spanish receipts better than a dedicated detector and classic OCR, so that our choice is justified, as the professor asked.

1. THE evaluation SHALL measure Gemini on every labelled image in `eval/vision_labels.csv`:
   - exact match on total, date and time
   - category accuracy
   - merchant character error rate
   - `is_receipt` accuracy on non-receipts
2. THE evaluation SHALL report accuracy by confidence band, and use it to set the confidence threshold.
3. THE evaluation SHALL compare, on the same images:
   - (1) the Roboflow detector `receipt-rlnml/1` alone: the share of images with a `TotalPrice` box, and its mean confidence
   - (2) the detector, then Tesseract on the crop
   - (3) the detector, then Gemini on the crop
   - (4) Gemini on the full image
   - for (2)–(4): accuracy on the total
4. THE results SHALL be written to `eval/vision_comparison.csv` and committed.

### REQ-14 Agent evaluation

**User story:** As the team, I want the agent measured on its own, so that its errors aren't mixed up with vision errors.

1. THE agent evaluation SHALL give the agent the *labelled* fields (not Gemini's output) for each case, with the case's trip.
2. THE agent evaluation SHALL report, for each of the three variants:
   - decision accuracy
   - the 3×3 confusion matrix
   - exact match on the amount for `to_pay`

### REQ-15 End-to-end evaluation against the baseline

**User story:** As the decision maker, I want the whole product compared with today's process, so that I can decide go or no-go.

1. THE evaluation SHALL run every case in `eval/cases.csv` with the fixed variant.
2. A case SHALL be correct when the decision matches `expected` and, for `to_pay`, the amount is within €0.01.
3. THE evaluation SHALL report, for the product and for the baseline:
   - accuracy
   - € leaked
   - € wrongly held back
   - number of wrong auto-approvals
   - safe automation rate
   - review rate
   - all of the above by kind (`normal`, `hard`, `review`, `out_of_scope`)

### REQ-16 LLM judge for the explanations

**User story:** As the team, I want the free-text explanations checked, so that reviewers aren't misled.

1. THE judge SHALL label each explanation `good` or `bad`, using a written rubric: it agrees with the decision and the reason codes, and it states no fact that is not in the vision output or the tool results.
2. THE team SHALL label 20 or more explanations by hand in `eval/human_labels.csv`.
3. THE evaluation SHALL report agreement with the judge (accuracy and Cohen's kappa) in code.

### REQ-17 Cost and latency

**User story:** As the decision maker, I want the cost per receipt, so that I can compare it with the reviewer's time.

1. THE evaluation SHALL report the median and 95th percentile of tokens, € and seconds per request, for the vision component, the agent and the total.
2. REPORT.md SHALL give the monthly cost at 6,000 receipts.

### REQ-18 Deployment and reproducibility

**User story:** As the professor, I want to run the app with my own keys from the `final` tag, so that I can grade it.

1. THE app SHALL run on Render's free plan, with peak memory below 512 MB.
2. `uv run python check.py` SHALL pass in a fresh clone, with a `.env` holding only the keys in `.env.example`.
3. `GEMINI_API_KEY` SHALL be required. `ROBOFLOW_API_KEY` SHALL be marked `(optional)`, because only the evaluation uses it.
4. THE pipeline SHALL cache Gemini outputs in `eval/cache/`, keyed by the image hash, model, prompt version and variant. The cache SHALL be committed so that the evaluation and the drift demo can be repeated. Setting `DISABLE_CACHE=1` SHALL force live calls.
5. IF a required key is missing, THEN THE app SHALL say which one, without crashing.
6. Tesseract and the Roboflow client SHALL be installed only locally for the evaluation, never on Render.

### REQ-19 Evaluation data sets

**User story:** As the team, I want realistic test data, so that the evaluation means something.

1. `eval/cases.csv` SHALL have 30 or more cases: at least 10 `normal`, 5 `hard`, 5 `review` and 3 `out_of_scope`.
2. `eval/reference.csv` SHALL have 30 or more inputs, none of which appears in `cases.csv`. These are the second photos of the receipts.
3. `eval/shifted.csv` SHALL have 10 or more inputs:
   - darkened, blurred and rotated copies made by a script that is committed
   - receipts from outside Spain, from the Roboflow dataset (CC BY 4.0, credited in REPORT.md)
4. The `baseline` column SHALL be `to_pay;<total>` for every case.

### REQ-20 Report and decision

**User story:** As the decision maker, I want a decision backed by evidence, so that I know whether anyone should rely on the product.

1. REPORT.md SHALL state the review rule and the expected number of reviews per day (300 × review rate).
2. REPORT.md SHALL state go, no-go, or go with conditions, the conditions, and the evidence behind them.
3. REPORT.md SHALL disclose the use of AI.

## Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-1 | End-to-end latency for one live request: median < 15 s, 95th percentile < 30 s (not counting the wake-up after sleep) |
| NFR-2 | Cost per receipt < €0.01, to be confirmed with current Gemini prices |
| NFR-3 | Peak memory on Render < 512 MB |
| NFR-4 | No API key in the repo; keys read only from the environment |
| NFR-5 | No personal data in the repo, the logs or the demo inputs |
