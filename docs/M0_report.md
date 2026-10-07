# M0 Report — Reality Check

**Author:** _(Prashanta Das)_
**Date:** _(8/29/2026)_
**Status:** ☐ in progress ☐✔ complete

> Fill this in as you go. This is the document you bring to your adviser and
> the document you paste into the final research report. Every number here
> must come from a script in `m0_reality_check/`, not from memory or
> estimation. If you did not measure it, leave the cell empty.

---

## 1. Corpus

Documents used for the bake-off: _(count)_

| Class | Count | % | Notes |
|---|---|---|---|
| DIGITAL (≥200 chars/page) | | | native text extraction, no OCR needed |
| MIXED (20–199) | | | |
| SCANNED (<20) | | | OCR required |

**Why this matters:** if most of the corpus is DIGITAL, OCR leaves the
critical path and M1 gets significantly faster.

Document types included: _(e.g. exam notices, academic calendar, syllabus,
office orders)_

Deliberately hard samples included: ☐ bad photocopy ☐ phone photo
☐ table-heavy page ☐ mixed Bangla/English paragraph

---

## 2. Hardware

| | Dev laptop | Server |
|---|---|---|
| CPU | | |
| RAM | | |
| GPU / VRAM | | |
| Disk free | | |
| CPU benchmark (s) | | |
| LLM tier | | |

Source: `hardware_audit.py --json`. Attach `audit_laptop.json` and
`audit_server.json`.

**Open question for the adviser:** _(e.g. "Is a GPU machine available for
deployment? Without one the LLM tier caps at 7B and Bangla answer quality
will be measurably worse.")_

---

## 3. OCR bake-off

Ground truth: _(N)_ pages hand-typed. Engines tested: _(list)_.

Paste `results/summary.csv` here:

| engine | pages | fail | scored | mean CER | median CER | worst CER | mean WER | sec/page |
|---|---|---|---|---|---|---|---|---|
| | | | | | | | | |

### Table structure — judged by eye

CER measures characters, not layout. An engine can win on CER and still
destroy every table.

| engine | table structure preserved? | notes |
|---|---|---|
| | | |

### Decision

**Chosen engine:** _(name)_

**Reason:** _(be specific — "lowest CER" is not enough. Mention table
handling, speed and install cost.)_

**Rejected because:** _(one line per engine you dropped)_

---

## 4. Golden question set

| Bucket | Written | Target |
|---|---|---|
| Bangla (bn) | | 10 |
| English (en) | | 10 |
| Banglish (bnglish) | | 10 |
| of which unanswerable | | 5 |

File: `m0_reality_check/golden_questions.xlsx`

Hardest question types to source an answer for: _(this tells you where the
corpus is thin)_

---

## 5. Risks discovered

| Risk | Evidence | Proposed response |
|---|---|---|
| | | |

Write these down even when they are inconvenient. Finding a problem in week
one and naming it is the behaviour your adviser is testing for; discovering
it at the demo is not.

---

## 6. Reproducibility

- Dependency lock: `requirements/lock-M0.txt` (`pip freeze` after M0 passed)
- Commands used: _(paste the exact `ocr_bakeoff.py` invocations)_
- Self-test result: _(paste the final line of `python m0_reality_check/selftest.py`)_

---

## 7. Decision for M1

Based on the above, M1 will: _(e.g. "ingest DIGITAL PDFs only, using native
PyMuPDF extraction. OCR moves to M3. Chosen OCR engine for M3 is X, measured
at CER Y on scanned notices.")_
