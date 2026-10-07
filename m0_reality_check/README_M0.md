# M0 — Reality Check

**DUET private local RAG teaching assistant · Milestone 0 · target: 1 week**

M0 writes no application code. It answers the four questions that decide
everything after it. Skipping it means guessing at the architecture and
finding out at Phase 20 that the guess was wrong.

| Question | Tool | Why it decides the architecture |
|---|---|---|
| What machine are we on? | `hardware_audit.py` | GPU/VRAM sets the entire model tier. No GPU ⇒ a different project. |
| Can we read Bangla scans at all? | `ocr_bakeoff.py` | OCR quality is the ceiling. Nothing downstream recovers a bad read. |
| What does "correct" mean here? | `golden_questions.xlsx` | Without it every later number is unverifiable. |
| How many docs are actually scanned? | `ocr_bakeoff.py` output | If most are digital PDFs, OCR drops out of the critical path entirely. |

---

## Setup

The environment lives at the **repository root**, not in this folder. See
`requirements/README.md` for why there is only one.

```bash
cd duet-ai-assistant                 # repo root, not m0_reality_check
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate

# M0 needs these three groups only -- ml.txt pulls torch (~3 GB) and the
# bake-off never touches it
pip install -r requirements/base.txt -r requirements/ocr.txt -r requirements/dev.txt
```

All commands below are run from the repo root:

```bash
python m0_reality_check/selftest.py
python m0_reality_check/ocr_bakeoff.py --samples m0_reality_check/samples ...
```

Then install at least one OCR engine — see the notes inside
`requirements/ocr.txt`. Tesseract is the easiest starting point.

Verify Bengali is present before going further:

```bash
tesseract --list-langs      # 'ben' must appear in the list
```

---

## Step 0 — Self-test

```bash
python m0_reality_check/selftest.py
```

Run this before anything else. It builds two synthetic PDFs whose correct
text is known, runs the whole pipeline over them, and checks the CER maths,
the DIGITAL/SCANNED classification and the OCR install. Green means the tool
is honest; it does not yet mean Bangla OCR is good enough.

---

## Step 1 — Hardware audit

```bash
python hardware_audit.py --label laptop --json audit_laptop.json
```

Run it again on the university machine if you can get access:

```bash
python hardware_audit.py --label lab-server --json audit_server.json
```

Keep both files. "How much VRAM does the department machine have?" is the
first thing to settle with your adviser, because the answer changes which
LLM tier is realistic and therefore how good the Bangla answers can get.

---

## Step 2 — Collect real documents

Put **8–15 real DUET documents** into `samples/`. Not clean samples you
picked because they look easy. Deliberately include:

- a digital PDF (exported from Word — has a text layer)
- a clean scan
- a bad photocopy or a phone photo of a notice
- at least two pages containing **tables**
- a page mixing Bangla and English in the same paragraph

The bake-off is only as honest as this folder. A sample set of clean
documents produces a flattering number and a system that collapses in
production.

---

## Step 3 — OCR bake-off

```bash
# 3a. rasterise pages and create empty ground-truth files
python ocr_bakeoff.py --make-truth-stubs

# 3b. open results/pages/*.png and type the correct text into the matching
#     ground_truth/*.txt file. 10–20 pages is enough. UTF-8 only.

# 3c. run the comparison
python ocr_bakeoff.py --engines tesseract
python ocr_bakeoff.py --engines tesseract,easyocr
python ocr_bakeoff.py --engines vlm --vlm-model qwen2.5vl:7b
```

Typing ground truth is tedious. To cut the work, generate a draft and
**correct it by hand**:

```bash
python ocr_bakeoff.py --engines tesseract --seed-truth-from tesseract
# writes ground_truth_draft/ — fix every file against the page image,
# then move it into ground_truth/
```

⚠️ Never score an engine against its own uncorrected output. You get a fake
CER of 0.000 and the whole study is worthless.

### Reading the result

| mean CER | verdict |
|---|---|
| < 0.05 | excellent — usable as-is |
| 0.05 – 0.15 | usable; expect retrieval misses on rare terms and names |
| 0.15 – 0.30 | marginal; retrieval quality will suffer badly |
| > 0.30 | unusable — change engine, or drop scanned docs from scope |

**CER is not the whole story.** Open `results/raw/<engine>/` and read a page
that contained a table. An engine can win on character accuracy and still
flatten every table into unusable soup. Judge table structure with your eyes
and write down what you see — that observation drives the M3 design.

The script also prints, per document, whether it is `DIGITAL`, `MIXED` or
`SCANNED`. Count them. If most of the corpus is DIGITAL, OCR moves off the
critical path and M1 gets much faster.

---

## Step 4 — Golden questions

Open `golden_questions.xlsx`. Read the three worked examples, delete
them, and write **30 questions**: 10 Bangla, 10 English, 10 Banglish, of
which about 5 are deliberately **unanswerable**.

The `Instructions` sheet counts your progress automatically.

Two rules that make the difference between evaluation and decoration:

1. Write the question and the expected answer **before** running the system.
   Writing them afterwards from whatever the system produced is not a test.
2. Write questions a teacher would really ask — not questions you already
   suspect the system can handle.

The unanswerable ones matter most. A model that never says *"I could not find
this in the available university documents"* will score beautifully on the
other 25 and be unsafe to deploy.

---

## Exit criteria

Do not start M1 until all five are true:

- [ ] Hardware audit saved for the dev machine, and for the server if it exists
- [ ] 8–15 real documents in `samples/`, deliberately including hard ones
- [ ] Ground truth typed for at least 10 pages
- [ ] `results/summary.csv` produced, with a chosen OCR engine and a written
      reason — including your own note on how it handled tables
- [ ] 30 golden questions written, with source document and page for each

---

## What to bring to your adviser

One page. Nothing longer.

1. The OCR comparison table straight out of `results/summary.csv`
2. The DIGITAL / MIXED / SCANNED split of the corpus
3. The hardware finding, and a direct ask if a GPU machine is needed
4. Three sample questions with the exact page the answer lives on
5. One honest risk sentence, e.g. *"Bangla OCR on photocopied notices sits at
   CER 0.22, so scanned notices may be out of scope for the first release."*

Measured numbers from real DUET documents in week one is a stronger signal
than a 32-phase plan. It also shows the thing he is actually testing for:
that you find problems early instead of discovering them at the demo.

---

## Files

```
m0_reality_check/
├── README_M0.md                     this file
├── selftest.py                      harness verification (run this first)
├── hardware_audit.py                machine audit → model tier
├── ocr_bakeoff.py                   OCR comparison harness (CER / WER / sec-per-page)
├── make_golden_template.py          regenerates the workbook if you want to change columns
├── golden_questions.xlsx   evaluation set to fill in by hand
├── samples/                         ← put real DUET documents here
├── ground_truth/                    ← hand-typed correct text, one .txt per page
└── results/                         generated: pages/, raw/, per_page.csv, summary.csv
```
