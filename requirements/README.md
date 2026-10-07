# DUET Private Teaching Assistant

A private, local, multilingual RAG assistant for DUET internal documents.
Authorised teachers upload internal documents and ask questions about them in
Bangla, English or Banglish. Everything runs inside the university network.

**No external AI API is used anywhere — not for the LLM, not for embeddings,
not for OCR.** Sending internal documents to a third-party service, even one
that promises not to train on them, means the data leaves the university
network, sits in a foreign jurisdiction for a retention window, and can be
reached by legal process the university has no say in. So it does not leave.

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full design.

---

## Status

| Milestone | Delivers | State |
|---|---|---|
| **M0** Reality check | OCR bake-off, hardware audit, 30 golden questions | in progress |
| **M1** Vertical slice | 10 PDFs → retrieve → local LLM → cited answer | not started |
| **M2** Multilingual retrieval | Banglish normalisation, hybrid search, reranking | not started |
| **M3** Full-fidelity documents | OCR pipeline, table extraction | not started |
| **M4** Security & scale | auth, RBAC, document permissions, async worker | not started |
| **M5** Benchmark | 150 questions, model comparison, hallucination rate | not started |
| **M6** Deploy & hand over | server deployment, runbook, research report | not started |

Update this table as each milestone closes. It is the fastest answer to
"where are you with the project?"

---

## Setup

**One virtual environment, at the repository root.** Not one per milestone —
see [`requirements/README.md`](requirements/README.md) for why.

```bash
git clone <repo> duet-ai-assistant
cd duet-ai-assistant

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# M0 only -- skips torch (~3 GB), which the bake-off does not need
pip install -r requirements/base.txt -r requirements/ocr.txt -r requirements/dev.txt

# From M1 onward
# pip install -r requirements.txt
```

You also need the Tesseract **binary** plus Bengali language data — the pip
package is only a wrapper. Instructions are at the top of
[`requirements/ocr.txt`](requirements/ocr.txt).

Verify:

```bash
tesseract --list-langs | grep ben       # must print 'ben'
python m0_reality_check/selftest.py     # end-to-end harness check
```

---

## Layout

```
duet-ai-assistant/
├─ README.md                    this file
├─ ARCHITECTURE.md              the design, and every decision behind it
├─ .gitignore                   blocks all document content -- read it
├─ .venv/                       the single environment (gitignored)
│
├─ requirements.txt             full install
├─ requirements/
│   ├─ README.md                which group each milestone needs
│   ├─ base.txt  ocr.txt  ml.txt  api.txt  dev.txt
│   └─ lock-M0.txt              pip freeze, written when M0 closes
│
├─ m0_reality_check/            M0 tools (promoted into ingestion/ later)
│   ├─ README_M0.md             how to run the bake-off
│   ├─ selftest.py              verifies the harness before real documents
│   ├─ ocr_bakeoff.py  hardware_audit.py  make_golden_template.py
│   ├─ golden_questions.xlsx
│   ├─ samples/                 🔒 real DUET documents -- gitignored
│   ├─ ground_truth/            🔒 hand-typed text -- gitignored
│   └─ results/                 summary.csv committed, pages/ and raw/ not
│
├─ docs/
│   ├─ M0_report.md             findings: chosen OCR engine, CER, corpus split
│   ├─ M1_report.md             ...
│   └─ runbook.md               written at M6, for whoever inherits this
│
├─ config/  database/  migrations/
├─ ingestion/  jobs/  embeddings/  retrieval/  rag/  llm/  security/
├─ api/  frontend/  evaluation/  scripts/  tests/
└─ docker-compose.yml
```

Tools live in the folder that produced them; **findings** live in `docs/`.
`m0_reality_check/README_M0.md` explains how to run the bake-off;
`docs/M0_report.md` records what it told you. Keeping those separate means
the report survives even after the tool is refactored away.

---

## Data handling

This repository must stay **private**. `samples/`, `ground_truth/` and
`results/raw/` contain DUET internal document content and are gitignored.
Only measurements — CER, WER, latency — are committed.

Building a system to stop university data leaving the network, and then
pushing that same data to GitHub, would defeat the entire point. If you want
a portfolio version later, publish the code with the data folders empty.

---

## Running the tests

```bash
pytest                                  # unit + integration
pytest tests/security -v                # permission filter must never leak
python m0_reality_check/selftest.py     # OCR harness sanity check
```

Integration tests need the PostgreSQL service from `docker-compose.yml`.
They will not run against SQLite — vector search and the permission
pre-filter cannot be tested on a database without pgvector.
