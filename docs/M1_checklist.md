# M1 — Vertical slice (weeks 2–3)

**Exit criteria (from ARCHITECTURE.md §13):** Recall@5 measured on the 30
golden questions; a teacher can ask a question in a browser, see a citation,
and open the source page image next to the answer.

**Scope fence:** DIGITAL PDFs only. `INGEST_DIGITAL_ONLY=true`. No OCR, no
tables, no auth, no worker. Those are M3 and M4. If you find yourself
writing OCR code this month, you have left M1.

---

## Phase 1 — foundation ✅ (this drop)

- [x] `config/settings.py` — env-driven, no hard-coded paths or secrets
- [x] `database/models.py` + `migrations/versions/0001` — **the full §5
      schema**, permissions and `embedding_rev` included, even though nothing
      uses them yet
- [x] `llm/base.py`, `embeddings/base.py`, `ingestion/ocr/base.py` — three
      interfaces, one implementation each
- [x] `docker-compose.yml` — postgres + pgvector, healthcheck, volume
- [x] `scripts/create_admin.py`
- [x] `scripts/reindex.py` — exists **before** it is needed
- [x] `tests/` including the import-graph guard

### Run it

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # paste into SECRET_KEY
docker compose up -d
alembic upgrade head
pytest
python scripts/create_admin.py --email you@duet.ac.bd --name "Your Name"
```

`pytest` must report the schema tests as **passed**, not skipped. Skipped
means `TEST_DATABASE_URL` is unset and you have tested nothing.

---

## Phase 2 — ingest

- [ ] `ingestion/loaders/pdf.py` — PyMuPDF text per page, keeps `page_no`
- [ ] `ingestion/clean.py` — NFC, strip invisibles, dehyphenate, whitespace.
      Reuse the normalisation from `m0_reality_check/ocr_bakeoff.py`; it is
      already correct about Bangla zero-width characters and danda variants.
      **Do not normalise Bangla digits to Latin.** ০৭ and 07 are different
      strings and this project exists to answer questions about exactly those.
- [ ] `ingestion/chunker.py` — 450 tokens / 80 overlap, measured with the
      bge-m3 tokeniser. Split priority: heading → blank line → danda `।` →
      English sentence end → newline → hard cut
- [ ] `ingestion/pipeline.py` — classify → extract → clean → chunk → embed →
      commit, synchronous for now (the worker is M4)
- [ ] `scripts/ingest_folder.py` — point at a folder, ingest what is new

**Test that earns its keep:** chunk a Bangla paragraph and assert no chunk
boundary falls mid-sentence. A splitter that only knows `.` will cut Bangla
text in half and you will not notice until retrieval is already bad.

## Phase 3 — retrieve

- [ ] `retrieval/scope.py::visible_document_ids` — the choke point. Write it
      now even though everything is public in M1, so no query is ever written
      without it
- [ ] `retrieval/dense.py` — pgvector cosine, `ORDER BY embedding <=> :q`,
      `WHERE document_id = ANY(:visible_ids) AND version_id = current_version_id`
- [ ] `evaluation/retrieval_eval.py` — Recall@1/@3/@5, MRR, **per language**

**This is the M1 number.** Averaging bn / en / bnglish into one figure hides
the exact failure the project exists to solve.

## Phase 4 — generate + cite

- [ ] `rag/prompts.py` — answer only from numbered sources; cite every claim
      as `[Sn]`; answer in the question's language; preserve dates and
      numerals exactly; the exact refusal string when the sources do not
      contain the answer
- [ ] `rag/context.py` — label sources `[S1]…[Sn]`, dedupe, enforce budget
- [ ] `rag/validate.py` — citation integrity + numeric/date grounding
- [ ] `rag/pipeline.py`

## Phase 5 — the thing that makes it trustworthy

- [ ] `GET /sources/{chunk_id}` — retrieved text + `page_image_url`
- [ ] `GET /sources/{chunk_id}/image` — render on demand with PyMuPDF at
      150 DPI, LRU-bounded disk cache. Do **not** pre-render at ingest
- [ ] `show_image` set by the backend when the answer contains a date,
      number, memo reference or name
- [ ] Minimal Jinja page: ask box, answer, sources, page image inline

M0 measured Tesseract reading `০৭/০৮/২০২৬` as `০৯/০৮/২০২৬`. Every downstream
check passes on that: retrieval finds the right document, the LLM quotes it
faithfully, numeric grounding confirms the digits are in the context, and the
system reports a wrong deadline with a correct citation and full confidence.
The only component with access to the truth is the teacher's eyes. That is
why the page image ships in M1 and not later.

---

## Two schema notes to raise with your adviser

1. **`chunks.content_sha256` was added.** §6 requires chunk-level incremental
   ingestion — "chunks whose hash already exists carry their embedding across
   unchanged" — but the §5 DDL had no column to store that hash in. Without
   it, a re-issued notice with one corrected line costs a full re-embed of
   every chunk instead of one.
2. **`ingestion_jobs (status, created_at)` index was added.** The worker polls
   that table with `FOR UPDATE SKIP LOCKED` on every loop.

Also worth deciding early: `document_versions.doc_class` now stores the
measured DIGITAL / MIXED / SCANNED verdict, so at M3 you can find every
document that was skipped under `INGEST_DIGITAL_ONLY` with one query instead
of re-classifying the corpus.

## What M1 does not need an answer to yet

- Which OCR engine wins — M3, when server hardware is known
- Which LLM — M5, with 150 questions and a committed benchmark script
- Whether the reranker helps — M2, measured

Leave them open. The interfaces are already in place, which is the entire
point of building them before the code that uses them.
