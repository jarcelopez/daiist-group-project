# Implementation Plan

Deadline: **Mon 30 Nov 2026, 23:59**. Presentations are 1–3 Dec. The team builds everything. Requirements are in [requirements.md](requirements.md); components and decisions are in [design.md](design.md).

## Project boundaries

| Tier | Items |
|---|---|
| **Must** | Quality gate → Gemini vision → agent with tools → guardrails; App tab (photo + trip); logging; Monitoring tab with quality signals, drift and alerts; drift demo passing; vision comparison rows 1, 3 and 4; agent evaluation; end-to-end evaluation against the baseline; judge with 20 labels; cost and latency; REPORT.md; `check.py` passing in a fresh clone; `final` tag + PR |
| **Should** | Thompson bandit with 3 variants, prior from the evaluation, Monitoring views, real feedback session |
| **Nice** | Tesseract row (comparison row 2); cumulative daily caps; a page showing the queues |
| **Out** | Payment through MCP; hotels, trains and flights; non-euro handling; submission deadlines; alcohol rules |

**What to cut first if late:** Tesseract row → queue page → bandit charts → the bandit itself. The bandit goes only as a last resort.

---

- [ ] **0. Setup and deploy** — *Fri 27*

  - [x] 0.1 Put the template online under the product's name
    - Change `name` in `render.yaml`; set `TITLE` and `DESCRIPTION` in `app.py`
    - Create the Render Blueprint with `GEMINI_API_KEY`; confirm the placeholder app is online
    - **Live at <https://expenseflow-vkvb.onrender.com>** (`expenseflow` was taken, so Render added `-vkvb`)
    - _Requirements: REQ-18_ · ~1 h

  - [x] 0.2 Test that the trip ID survives the upload (decision D5)
    - Placeholder `run()` returns the filename it received; commit `eval/inputs/test__T-001.jpg`
    - `check.py --url <url>`: the filename must end in `__T-001.jpg`. If it doesn't, switch to a fallback (e.g. a small JSON sidecar) before building on it
    - **Passed 2026-11-27, D5 holds:** the live `/run` (Gradio 6.31, `gr.Image(type="filepath")`) received `test__T-001.jpg` unchanged. Tested with `check.py`'s own `gradio_client` call, because `check.py --url` only reaches that step once the eval files and drift demo pass. An RGBA PNG (re-saved by Gradio) also kept its name locally
    - _Requirements: REQ-1.2, REQ-1.3_ · _Depends on: 0.1_ · ~0.5 h

  - [x] 0.3 Dependencies and keys
    - `uv add google-genai pillow pydantic`; `uv add --group dev pytesseract pytest`
    - `.env.example`: `GEMINI_API_KEY`, `ROBOFLOW_API_KEY (optional)`, `DISABLE_CACHE (optional)`
    - _Requirements: REQ-18.3, REQ-18.6_ · ~0.5 h

  - [x] 0.4 Choose the Gemini model and record its prices
    - List the models available to a free key; pin a Flash model ID in `VERSIONS`; put the € prices and the date checked in `receipts/llm.py`
    - **Chose `gemini-3.5-flash-lite` for vision and agent** (<1 s; 3.6 Flash took 18–298 s on the free key). €0.27 / €2.28 per 1M tokens in/out, checked 2026-11-27. Revisit at 2.4 if it misreads receipts
    - _Requirements: REQ-17, NFR-2_ · ~0.5 h

- [ ] **1. Data foundations** — *Fri 27 – Sat 28*

  - [ ] 1.1 Collect the receipts
    - 15–20 Spanish receipts, **2 photos each** (different light, angle or phone): `eval/inputs/rNNa.jpg`, `rNNb.jpg`
    - Mix: meals (various hours), café, supermarket, fuel, parking, toll, taxi; plus a few to reject (pharmacy, clothes) and some non-receipts (a menu, a screenshot)
    - **Cover card digits, people's names and personal tax IDs before committing**
    - _Requirements: REQ-4.4, REQ-19_ · ~3 h (team)

  - [ ] 1.2 Label the vision ground truth
    - `eval/vision_labels.csv`, one row per image (both photos of a receipt share the same values)
    - _Requirements: REQ-13.1_ · _Depends on: 1.1_ · ~1.5 h

  - [ ] 1.3 Write the policy
    - `data/policy.csv` from design §8
    - **Check €26.67 against art. 9 RIRPF**
    - _Requirements: REQ-6_ · ~0.5 h

  - [ ] 1.4 Write the trip register and the paid ledger
    - `data/trips.csv`, written around the receipts: for each receipt, 1 trip that fits it, and 1–2 that don't (dates shifted, train instead of car, a day trip for a big dinner)
    - Include one 1-day client trip, one 3-day trip by car, one 1-week trip to New York (non-EUR)
    - `data/paid_receipts.csv` with ≥2 receipts that will be resubmitted as duplicates
    - _Requirements: REQ-5, REQ-6_ · _Depends on: 1.2_ · ~1.5 h

  - [ ] 1.5 Write the end-to-end cases
    - `eval/cases.csv`: 30+ cases, at least 10 `normal`, 5 `hard`, 5 `review` and 3 `out_of_scope`
    - Inputs are copies named `rNNa__T-xxx.jpg`
    - `expected` = `to_pay;<amount>` / `pending_review` / `rejected`; `baseline` = `to_pay;<total>`
    - _Requirements: REQ-15, REQ-19.1, REQ-19.4_ · _Depends on: 1.4_ · ~2 h

  - [ ] 1.6 Write the reference set
    - `eval/reference.csv`: the `b` photos, each named with its valid trip; none of them may appear in `cases.csv`
    - _Requirements: REQ-19.2_ · _Depends on: 1.4_ · ~0.5 h

  - [ ] 1.7 Build the shifted set
    - `eval/make_shifted.py`: darkened, blurred and rotated copies of some `a` photos
    - Plus ≥4 foreign receipts from the Roboflow dataset (CC BY 4.0; credit in REPORT.md)
    - `eval/shifted.csv` with 10+ inputs
    - _Requirements: REQ-11.3, REQ-19.3_ · _Depends on: 1.1_ · ~1 h

- [ ] **2. Vision component** — *Fri 27*

  - [ ] 2.1 Gemini client (`receipts/llm.py`)
    - Calls, 1 retry with backoff, token → € accounting, disk cache (`eval/cache/`), `DISABLE_CACHE`
    - _Requirements: REQ-3.3, REQ-17, REQ-18.4_ · _Depends on: 0.3, 0.4_ · ~1.5 h

  - [ ] 2.2 Quality gate (`receipts/quality.py`)
    - EXIF orientation; brightness, contrast, blur (Laplacian in numpy), size; resize to ≤1,600 px
    - Placeholder thresholds
    - _Requirements: REQ-2_ · ~1 h

  - [ ] 2.3 Vision extractor (`receipts/vision.py`, `prompts/vision_v1.txt`)
    - Response schema of design §7.1; Pydantic validation; the no-personal-data instruction
    - _Requirements: REQ-3, REQ-4.1_ · _Depends on: 2.1, 2.2_ · ~1.5 h

  - [ ] 2.4 Try it on 5 receipts and adjust the prompt; write down what changed
    - _Requirements: REQ-3_ · _Depends on: 1.1, 2.3_ · ~0.5 h

- [ ] **3. Agent and guardrails** — *Sat 28*

  - [ ] 3.1 Policy data and tools (`receipts/policy.py`)
    - Load the CSVs; `get_trip`, `get_policy`, `check_duplicate`; cap lookup
    - _Requirements: REQ-5.2_ · _Depends on: 1.3, 1.4_ · ~1 h

  - [ ] 3.2 Guardrails (`receipts/rules.py`)
    - Hard rules and reason codes (design §7.4); precedence (§7.3); `min(total, cap)`
    - Unit tests: one per reason code, plus the precedence
    - _Requirements: REQ-6_ · _Depends on: 3.1_ · ~2.5 h

  - [ ] 3.3 Policy agent (`receipts/agent.py`, `prompts/agent_*_v1.txt`)
    - Our own tool-calling loop (≤4 rounds); output schema §7.2; the strict, balanced and lenient prompts
    - _Requirements: REQ-5_ · _Depends on: 2.1, 3.1_ · ~2.5 h

  - [ ] 3.4 Wire up `pipeline.run(raw_input, variant=None)`
    - Parse the trip ID → quality → vision → (bandit) → agent → guardrails → record (§7.5)
    - Features never NaN
    - _Requirements: REQ-1.3, REQ-8, REQ-12.5_ · _Depends on: 2.3, 3.2, 3.3_ · ~1.5 h

- [ ] **4. App and logging** — *Sat 28*

  - [ ] 4.1 App tab
    - `gr.Image(type="filepath")` + trip dropdown; temp copy `upload__<trip>.<ext>`
    - Show fields, decision, amount, codes, explanation, variant, cost and latency
    - "Reviewer: was this decision correct?" 👍/👎; keep `serve()` and `api_name="run"`
    - _Requirements: REQ-1, REQ-7, REQ-9_ · _Depends on: 3.4_ · ~2 h

  - [ ] 4.2 Extra log fields; `check.py` passes locally (with temporary eval files if needed)
    - _Requirements: REQ-8_ · _Depends on: 4.1_ · ~0.5 h

  - [ ] 4.3 Deploy
    - `check.py --url`; check memory on Render's Metrics page (< 512 MB) and live latency
    - _Requirements: REQ-18.1, NFR-1, NFR-3_ · _Depends on: 4.2_ · ~0.5 h

- [ ] **5. Evaluation** — *Sun 29*

  - [ ] 5.1 Vision evaluation
    - Exact match, category accuracy, merchant CER, `is_receipt`; accuracy by confidence band → choose the threshold T
    - _Requirements: REQ-13.1, REQ-13.2_ · _Depends on: 1.2, 2.3_ · ~1.5 h

  - [ ] 5.2 Vision comparison (`eval/compare_vision.py`)
    - Roboflow over plain HTTP → `TotalPrice` box rate and confidence → crops → Gemini on the crop; Gemini on the full image
    - **Nice:** Tesseract (`spa`) on the crops
    - Writes `vision_comparison.csv`
    - _Requirements: REQ-13.3, REQ-13.4_ · _Depends on: 5.1_ · ~2.5 h (+1.5 h for Tesseract)

  - [ ] 5.3 Agent evaluation
    - Labelled fields + trip × 3 variants → accuracy, confusion matrix, amount match → `agent_results.csv`
    - _Requirements: REQ-14_ · _Depends on: 1.5, 3.3_ · ~1 h

  - [ ] 5.4 End-to-end evaluation against the baseline
    - `is_correct()`; € leaked, € wrongly held back, wrong auto-approvals, safe automation rate, review rate, by kind → `results.csv`, `summary.md`
    - _Requirements: REQ-15_ · _Depends on: 1.5, 3.4_ · ~1.5 h

  - [ ] 5.5 LLM judge
    - `prompts/judge_v1.txt` rubric; 20+ labels by the team in `human_labels.csv`; agreement + Cohen's κ → `judge_results.csv`
    - _Requirements: REQ-16_ · _Depends on: 5.4_ · ~1.5 h

  - [ ] 5.6 Cost and latency
    - Per component, median and p95; the monthly figure at 6,000 receipts
    - _Requirements: REQ-17_ · _Depends on: 5.4_ · ~0.5 h

  - [ ] 5.7 Set the quality-gate thresholds from the reference set (about the 2nd percentile)
    - _Requirements: REQ-2.2_ · _Depends on: 1.6, 2.2_ · ~0.5 h

- [ ] **6. Monitoring and drift** — *Sun 29*

  - [ ] 6.1 `main.py reference` → commit `monitoring/reference_features.csv`
    - _Requirements: REQ-11.1_ · _Depends on: 1.6, 3.4, 5.7_ · ~0.25 h

  - [ ] 6.2 Monitoring tab
    - Quality signals (review share, 👎 share, `min_confidence`, not-a-receipt share); queues per day; € auto-approved; alert thresholds
    - _Requirements: REQ-10.1, REQ-10.2, REQ-11.2, REQ-11.4_ · _Depends on: 4.1_ · ~2 h

  - [ ] 6.3 Drift demo passes
    - Estimate the false-alarm rate by resampling; adjust discrete features if they cause false alarms
    - _Requirements: REQ-11.3, REQ-11.5_ · _Depends on: 1.7, 6.1_ · ~1 h

- [ ] **7. Bandit (RL bonus)** — *Sun 29*

  - [ ] 7.1 `receipts/bandit.py`
    - Reward mapping, posteriors rebuilt from the logs, Thompson choice
    - Prior from 5.3 → `monitoring/bandit_prior.json` (×0.2, `source: sim`)
    - Unit tests
    - _Requirements: REQ-12.1–12.4_ · _Depends on: 5.3_ · ~1.5 h

  - [ ] 7.2 Wire it into the live path
    - Show the variant in the App tab; Monitoring view: posterior means and intervals, selection share, `n_real`
    - _Requirements: REQ-12, REQ-10.3_ · _Depends on: 7.1, 6.2_ · ~1.5 h

  - [ ] 7.3 Real feedback session on the live app
    - The team acts as reviewers, ≥30 rated requests; `export-logs`; posteriors before and after
    - _Requirements: REQ-12.6_ · _Depends on: 7.2, 4.3_ · ~1 h

  - [ ] 7.4 Find a borderline receipt whose decision changes between arms, for the demo
    - _Requirements: REQ-12.6_ · _Depends on: 7.3_ · ~0.5 h

- [ ] **8. Ship** — *Mon 30*

  - [ ] 8.1 `check.py` and `check.py --url` in a **fresh clone**, with only the keys in `.env.example`
    - _Requirements: REQ-18.2_ · ~0.5 h

  - [ ] 8.2 REPORT.md: every section, numbers traced to the `eval/` files
    - Review rule and reviews per day; go / no-go / go with conditions; data licences; AI use
    - _Requirements: REQ-20, REQ-13, REQ-12.6_ · ~4 h

  - [ ] 8.3 CONTRIBUTIONS.md up to date
    - ~0.25 h

  - [ ] 8.4 **Stop deploying by 15:00**; `git tag final && git push --tags`; PR to `ami232/daiist-group-project`; Blackboard (PR link, app link, zip)
    - _Depends on: 8.1, 8.2_ · ~0.5 h

- [ ] **9. Presentation** — *Tue 1 – Thu 3 Dec*

  - [ ] 9.1 Demo script
    - One normal case, one case sent to review (or wrong), the Monitoring tab with drift and the bandit
    - Backup video; wake the app before presenting
    - ~2 h

  - [ ] 9.2 Walkthrough so every member can explain every part
    - ~1 h

---

## Requirement coverage

| Requirement | Tasks |
|---|---|
| REQ-1 Submit | 0.2, 3.4, 4.1 |
| REQ-2 Quality gate | 2.2, 5.7 |
| REQ-3 Vision | 2.1, 2.3, 2.4 |
| REQ-4 Privacy | 1.1, 1.4, 2.3, 3.4 |
| REQ-5 Agent | 3.1, 3.3 |
| REQ-6 Guardrails | 1.3, 3.2 |
| REQ-7 Show the decision | 4.1 |
| REQ-8 Logging | 3.4, 4.2 |
| REQ-9 Feedback | 4.1 |
| REQ-10 Monitoring | 6.2, 7.2 |
| REQ-11 Drift and alerts | 1.7, 6.1, 6.3 |
| REQ-12 Bandit | 3.4, 7.1–7.4 |
| REQ-13 Vision evaluation and comparison | 1.2, 5.1, 5.2 |
| REQ-14 Agent evaluation | 5.3 |
| REQ-15 End to end | 1.5, 5.4 |
| REQ-16 Judge | 5.5 |
| REQ-17 Cost | 0.4, 2.1, 5.6 |
| REQ-18 Deploy and reproducibility | 0.1, 0.3, 4.3, 8.1 |
| REQ-19 Data sets | 1.1, 1.5, 1.6, 1.7 |
| REQ-20 Report and decision | 8.2 |

## Effort per day (build + data)

| Day | Phases | ~Hours |
|---|---|---|
| Fri 27 | 0, 1.1–1.3, 2 | 12 |
| Sat 28 | 1.4–1.7, 3, 4 | 15 |
| Sun 29 | 5, 6, 7 | 20 (Tesseract included) |
| Mon 30 | 8 | 5 |

**Sunday is the overloaded day.** If Saturday slips, move 5.2 Tesseract and 7.2's charts to the cut list right away.
