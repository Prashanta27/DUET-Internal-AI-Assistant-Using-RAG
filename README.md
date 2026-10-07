# Dependencies

**One virtual environment for the whole project.** Not one per milestone.

Milestones are stages of a single system, not separate projects. The OCR
adapters written for the M0 bake-off get promoted into `ingestion/ocr/` at
M3 — with a separate environment they would run against different library
versions, and the CER you measured in week 1 would no longer describe the
engine you actually shipped.

So the split is by **capability**, not by milestone. Install a group when a
milestone first needs it.

| Group | Contains | First needed | Weight |
|---|---|---|---|
| `base.txt` | config, PDF/DOCX parsing, text utils | M0 | light |
| `ocr.txt` | OCR engine wrappers | M0 | light (Tesseract) / heavy (EasyOCR, Surya) |
| `dev.txt` | pytest, ruff, mypy | Phase 1 | light |
| `ml.txt` | torch, sentence-transformers, bge-m3 | M1 | **heavy (~3 GB)** |
| `api.txt` | FastAPI, SQLAlchemy, pgvector, auth | M1 | light |

## Setup

Create the environment **once, at the repository root**:

```bash
cd duet-ai-assistant
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
```

Then install what the current milestone needs.

**For M0** — the bake-off never touches torch, so skip `ml.txt`:

```bash
pip install -r requirements/base.txt -r requirements/ocr.txt -r requirements/dev.txt
```

**From M1 onward:**

```bash
pip install -r requirements.txt
```

## Verifying the environment

```bash
python -c "import pymupdf; print('pdf ok')"
tesseract --list-langs | grep ben          # must print 'ben'
python m0_reality_check/selftest.py        # end-to-end harness check
```

After installing `ml.txt` on the server, confirm the GPU is actually visible:

```bash
python -c "import torch; print(torch.cuda.is_available())"
```

If that prints `False` on a machine with a GPU, you installed the CPU wheel.
Reinstall torch from the CUDA index — see the note in `ml.txt`.

## Rules

- **Always activate the venv before running anything.** A script that works
  for you and fails for your adviser is almost always a missing activation.
- **Pin versions when a milestone closes.** Run `pip freeze > requirements/lock-M0.txt`
  after M0 passes, and again after each milestone. Your measured results are
  only reproducible against the versions that produced them, and reproducibility
  is the difference between a research report and a claim.
- **Never `pip install` without adding it to a group file.** A dependency that
  exists only in your venv is a dependency that will not exist on the
  department server.
