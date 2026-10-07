# DUET Private Teaching Assistant — Architecture & Roadmap v1.0

**A private, local, multilingual RAG assistant for DUET internal documents.**
Authorised teachers upload internal documents and query them in Bangla,
English or Banglish. Everything runs inside the university network. No
external AI API is used at any point, in any environment.

This document supersedes the original 32-phase specification. Section 2
lists every deliberate change and why it was made.

---

## 1. Scope

### In scope for v1

Document ingestion (PDF, DOCX, TXT, images) with OCR for scans · table
extraction and structured storage · hybrid retrieval with reranking ·
grounded answer generation by a local LLM · citations with document name and
page · Bangla / English / Banglish queries · teacher authentication ·
role-based and document-level authorisation · audit logging · document
versioning · LAN-only deployment.

### Explicitly out of scope for v1

Text-to-SQL over extracted tables · vision-language understanding of
diagrams and photographs · student-facing access · multi-department admin
hierarchy · handwriting recognition · internet exposure · high availability.

Each of these is a v2 candidate. Recording them as out of scope is what stops
the project from expanding past what one part-time developer can finish.

### Hard constraints

| Constraint | Consequence |
|---|---|
| No external AI API — LLM, embeddings **and** OCR all local | Embeddings must be local too, or the whole corpus leaves the network during ingestion |
| Runs on university LAN only | No CDN, no cloud object storage, no managed database |
| Development on a slow laptop | Architecture must be hardware-independent; model tier is a config value |
| Single part-time developer | Every component must be independently testable and independently deferrable |

---

## 2. Changes from the original specification

| # | Original | Now | Reason |
|---|---|---|---|
| 1 | Security at phases 23–26 | Permission metadata in the schema from day 1 | Permission filtering is part of retrieval. Retrofitting it forces a full re-index |
| 2 | ChromaDB for dev, Qdrant for prod | **pgvector inside PostgreSQL**, one database | Two stores means two filtering semantics; a filter that behaves differently in prod is a security bug, not a config difference |
| 3 | Text-to-SQL structured retrieval in MVP | Tables stored as JSONB **and** as a whole-table markdown chunk. No SQL generation | Text-to-SQL over heterogeneous OCR-extracted Bangla tables is the highest-effort, lowest-yield component in the spec |
| 4 | Banglish as an evaluation category | Banglish handled by a **query normalisation component** in the retrieval path | BGE-M3 is weak on romanised Bangla. This is a retrieval problem, not a test case |
| 5 | PaddleOCR as a Bangla candidate | PaddleOCR only for English pages / PP-Structure layout | PaddleOCR ships no official Bangla recognition model |
| 6 | OCR as one phase | OCR engine chosen by measurement **before** anything else (M0) | OCR quality is the ceiling of the whole system |
| 7 | Hand-rolled `ingestion/` modules | Evaluate Docling / Marker behind the same interface | Collapses several phases without losing the abstraction |
| 8 | Synchronous upload | Postgres-backed **job queue + worker** | OCR of a 200-page scan cannot happen inside an HTTP request |
| 9 | LLM benchmark at phase 3 | LLM benchmark at M5, smoke test at M1 | Benchmarking before a corpus and an eval set exists produces no usable signal |
| 10 | 300 eval questions up front | 30 at M0, 150 at M5 | 300 hand-written questions before any code is a week you need elsewhere |
| 11 | Frontend at phase 28 | Minimal UI at M1 | Your adviser is testing whether you deliver. Something visible in week 3 |
| 12 | 32 strictly linear phases | 7 milestones, each independently demonstrable | A linear plan where nothing works until the end has no recovery path |

Everything else from the original specification is retained. Section 15 maps
all 26 original requirements to the component that implements them.

---

## 3. System architecture

```
                        ┌──────────────────────────────┐
                        │   Teacher browser (LAN)      │
                        └──────────────┬───────────────┘
                                       │ HTTPS (self-signed, LAN)
                        ┌──────────────▼───────────────┐
                        │  FastAPI application          │
                        │  ─ auth / session             │
                        │  ─ document endpoints         │
                        │  ─ chat endpoint              │
                        │  ─ admin endpoints            │
                        └───┬──────────────────┬────────┘
                            │                  │
              ┌─────────────▼──────┐   ┌───────▼─────────────────┐
              │  RAG pipeline      │   │  Ingestion API          │
              │  (query path)      │   │  (enqueue only)         │
              └─────────┬──────────┘   └───────┬─────────────────┘
                        │                      │ writes job row
                        │                      │
                        │              ┌───────▼─────────────────┐
                        │              │  Ingestion worker        │
                        │              │  (separate process)      │
                        │              │  extract → OCR → tables  │
                        │              │  → clean → chunk → embed │
                        │              └───────┬─────────────────┘
                        │                      │
        ┌───────────────▼──────────────────────▼──────────────────┐
        │             PostgreSQL 16 + pgvector                     │
        │  users · documents · document_versions · permissions     │
        │  chunks (+ embedding vector, + tsvector) · tables        │
        │  ingestion_jobs · audit_logs · query_logs                │
        └──────────────────────────┬───────────────────────────────┘
                                   │
        ┌──────────────────────────▼───────────────────────────────┐
        │  Local model runtime                                      │
        │  Ollama (LLM)  ·  BGE-M3 (embeddings)  ·  reranker  ·  OCR│
        └───────────────────────────────────────────────────────────┘
```

Two processes, one database, no message broker. The API never performs
ingestion work; the worker never serves HTTP. Both are stateless apart from
the database and the file store on disk.

---

## 4. Technology stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | Ecosystem for OCR / embeddings |
| API | FastAPI + Uvicorn | Async, typed, minimal |
| Database | PostgreSQL 16 + **pgvector** ≥ 0.8 | One store for rows, vectors and full-text. Permission filter and vector search in the same query |
| ORM / migrations | SQLAlchemy 2.0 + Alembic | Schema changes will happen; do them properly |
| Job queue | Postgres table + `FOR UPDATE SKIP LOCKED` | No Redis, no Celery. One less service to run on a university machine |
| LLM runtime | Ollama | Required by the adviser. Swappable behind `LLMBackend` |
| Embeddings | BAAI **bge-m3** (1024-dim), local | Strongest open multilingual retriever for Bangla. Confirm at M5 |
| Reranker | BAAI **bge-reranker-v2-m3**, local | Optional at M1, in place by M2 |
| OCR | **Decided by the M0 bake-off** | Behind an `OCREngine` interface. Do not hard-code |
| PDF | PyMuPDF (text + raster), pdfplumber (tables) | Evaluate Docling as a combined replacement at M3 |
| DOCX | python-docx | Native text and tables |
| Frontend | Jinja2 + vanilla JS + `fetch` | No build toolchain. One developer, LAN deployment |
| Auth | JWT access + refresh, **argon2** hashing | Standard, no external identity provider available |
| Tests | pytest | |
| Deployment | docker compose (db + api + worker); Ollama on the host with GPU | |

**Pinned decisions that must not drift:** embedding dimension 1024 · cosine
distance · HNSW index · chunk target 450 tokens with 80 overlap · tables are
never split across chunks.

---

## 5. Data model

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TYPE user_role     AS ENUM ('admin', 'teacher');
CREATE TYPE doc_status    AS ENUM ('uploaded','processing','ready','failed','archived');
CREATE TYPE chunk_kind    AS ENUM ('text','table','ocr_text','caption');
CREATE TYPE job_status    AS ENUM ('queued','running','done','failed');

CREATE TABLE users (
    id            BIGSERIAL PRIMARY KEY,
    email         TEXT UNIQUE NOT NULL,
    full_name     TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role          user_role NOT NULL DEFAULT 'teacher',
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ
);

CREATE TABLE documents (
    id                 BIGSERIAL PRIMARY KEY,
    title              TEXT NOT NULL,
    original_filename  TEXT NOT NULL,
    department         TEXT,
    owner_id           BIGINT NOT NULL REFERENCES users(id),
    is_public          BOOLEAN NOT NULL DEFAULT FALSE,  -- visible to all teachers
    status             doc_status NOT NULL DEFAULT 'uploaded',
    current_version_id BIGINT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE document_versions (
    id             BIGSERIAL PRIMARY KEY,
    document_id    BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version_no     INT NOT NULL,
    sha256         CHAR(64) NOT NULL,
    storage_path   TEXT NOT NULL,
    mime_type      TEXT NOT NULL,
    size_bytes     BIGINT NOT NULL,
    page_count     INT,
    ocr_engine     TEXT,          -- which engine produced the text, for reproducibility
    ingested_at    TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, version_no),
    UNIQUE (sha256)               -- same bytes are never ingested twice
);

-- A grant is to a user OR to a role, never both.
CREATE TABLE document_permissions (
    id           BIGSERIAL PRIMARY KEY,
    document_id  BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    user_id      BIGINT REFERENCES users(id) ON DELETE CASCADE,
    role         user_role,
    granted_by   BIGINT NOT NULL REFERENCES users(id),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((user_id IS NULL) <> (role IS NULL))
);

CREATE TABLE doc_tables (
    id           BIGSERIAL PRIMARY KEY,
    document_id  BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version_id   BIGINT NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    page_no      INT NOT NULL,
    table_index  INT NOT NULL,
    caption      TEXT,
    columns      JSONB NOT NULL,   -- ["Course Code","Course Name","Credit"]
    rows         JSONB NOT NULL,   -- [["CSE1101","Programming","3"], ...]
    markdown     TEXT NOT NULL     -- serialised form that gets embedded
);

CREATE TABLE chunks (
    id              BIGSERIAL PRIMARY KEY,
    document_id     BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version_id      BIGINT NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    table_id        BIGINT REFERENCES doc_tables(id) ON DELETE CASCADE,
    chunk_index     INT NOT NULL,
    kind            chunk_kind NOT NULL DEFAULT 'text',
    page_no         INT,
    section         TEXT,
    content         TEXT NOT NULL,
    content_tsv     TSVECTOR GENERATED ALWAYS AS
                      (to_tsvector('simple', content)) STORED,
    token_count     INT,
    lang            TEXT,                    -- 'bn' | 'en' | 'mixed'
    embedding       VECTOR(1024),
    embedding_model TEXT NOT NULL,           -- 'bge-m3'
    embedding_rev   INT  NOT NULL DEFAULT 1, -- bump to force re-embedding
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX chunks_embedding_hnsw ON chunks
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX chunks_tsv      ON chunks USING gin (content_tsv);
CREATE INDEX chunks_trgm     ON chunks USING gin (content gin_trgm_ops);
CREATE INDEX chunks_doc      ON chunks (document_id, version_id);

CREATE TABLE ingestion_jobs (
    id          BIGSERIAL PRIMARY KEY,
    version_id  BIGINT NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    status      job_status NOT NULL DEFAULT 'queued',
    attempts    INT NOT NULL DEFAULT 0,
    stage       TEXT,                   -- 'extract'|'ocr'|'tables'|'chunk'|'embed'
    error       TEXT,
    locked_by   TEXT,
    locked_at   TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);

CREATE TABLE audit_logs (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id     BIGINT REFERENCES users(id),
    action      TEXT NOT NULL,          -- 'login','upload','query','grant','delete'
    object_type TEXT,
    object_id   BIGINT,
    ip          INET,
    detail      JSONB                   -- never raw document content
);

CREATE TABLE query_logs (
    id            BIGSERIAL PRIMARY KEY,
    ts            TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id       BIGINT REFERENCES users(id),
    question_hash CHAR(64) NOT NULL,
    question_text TEXT,                 -- NULL unless LOG_QUERY_TEXT=true
    lang          TEXT,
    route         TEXT,
    latency_ms    INT,
    n_sources     INT,
    refused       BOOLEAN NOT NULL DEFAULT FALSE
);
```

Two schema decisions worth defending to your adviser:

- **`UNIQUE (sha256)` on versions** satisfies the file-hashing requirement and
  makes re-upload of the same bytes a no-op instead of a duplicate index.
- **`embedding_rev`** exists so that changing the embedding model is a
  migration, not a disaster. `scripts/reindex.py` re-embeds every chunk whose
  `embedding_model`/`embedding_rev` differs from the configured pair.

---

## 6. Ingestion architecture

Upload is fast and does almost nothing. All real work happens in the worker.

```
POST /documents  ──► validate → sha256 → store file → create version
                          → INSERT ingestion_jobs(status='queued') → 202 Accepted
                                                │
worker loop:  SELECT ... FOR UPDATE SKIP LOCKED │
                                                ▼
        ┌───────────────────────────────────────────────────────┐
        │ 1. classify         text layer? chars/page threshold   │
        │ 2. extract          native text (PyMuPDF / python-docx)│
        │ 3. OCR              only pages that need it            │
        │ 4. tables           detect → columns/rows → JSONB      │
        │ 5. clean            NFC, strip invisibles, dehyphenate │
        │ 6. chunk            boundary-aware, tables kept whole  │
        │ 7. embed            batch through bge-m3               │
        │ 8. commit           chunks + doc_tables, status=ready  │
        └───────────────────────────────────────────────────────┘
```

### Document classification (step 1)

Measured characters per page decides the route — never assume:

| chars/page | route |
|---|---|
| ≥ 200 | DIGITAL — native extraction, **no OCR** |
| 20–199 | MIXED — native extraction, OCR only the pages that came back empty |
| < 20 | SCANNED — OCR every page |

This is the same measurement the M0 bake-off prints, reused in production.

### Table handling

Tables are **not** flattened into prose, and **not** exposed to text-to-SQL.
Each table is written twice:

1. `doc_tables` — `columns` and `rows` as JSONB, plus page and table index.
   This is the structured record required by the specification and the basis
   for any future aggregation feature.
2. `chunks` — one chunk of `kind='table'` holding the markdown serialisation,
   with `table_id` set. **Never split a table across chunks.**

A question like *"show the CSE courses with credits"* is answered by
retrieving that single chunk and letting the LLM reformat it. Aggregation
questions (*"how many CSE courses in total"*) are answered in v2 by loading
`doc_tables.rows` into pandas and computing — not by generating SQL.

If the table exceeds the chunk budget, store the full table in `doc_tables`
and embed a header-plus-summary chunk that points at the `table_id`, so the
retrieval hit still resolves to the complete table at context-build time.

### Incremental ingestion

Re-embedding a document that has not changed is pure waste, and on a CPU-only
machine it is waste measured in hours. Ingestion skips at three levels.

**1. File level.** `sha256` of the uploaded bytes, with a `UNIQUE` constraint
on `document_versions.sha256`. An identical file that has already been
ingested returns the existing version id and creates no job at all. This is
what makes "drop the whole folder in again" safe and near-instant.

**2. Chunk level.** Each chunk stores a hash of its normalised text. When a
new *version* of an existing document arrives, chunks whose hash already
exists carry their embedding across unchanged; only genuinely new chunks are
embedded. A notice re-issued with one corrected line costs one embedding, not
forty.

**3. Model level.** Every chunk records `embedding_model` and
`embedding_rev`. `scripts/reindex.py` selects only the rows whose pair differs
from the configured one and re-embeds those. Changing the model does not mean
dropping the table.

**Resumability.** `ingestion_jobs.stage` records the last completed stage, so
a crash during embedding resumes at embedding instead of re-running OCR on
200 pages. On this hardware that distinction is the difference between a
five-minute retry and a lost afternoon.

The practical result is the behaviour the system needs: point it at the
document folder as often as you like, and only what is actually new does any
work.

### Chunking rules

- Target 450 tokens, 80 overlap, measured with the embedding model's tokeniser.
- Split on boundaries in priority order: heading → blank line → Bangla danda
  `।` → English sentence end → newline → hard cut. A naive splitter that only
  knows `.` will cut Bangla text mid-sentence.
- Every chunk inherits `document_id, version_id, page_no, section` so a
  citation can always be produced.

---

## 7. RAG architecture (query path)

```
question + authenticated user
   │
   ├─ 1. language detect        script ratio → bn | en | bnglish | mixed
   ├─ 2. query normalisation    ← the Banglish component
   │        bnglish → phonetic transliteration → Bangla
   │        optional: local LLM paraphrase → English
   │        result: 1–3 query variants
   ├─ 3. permission scope       resolve the user's visible document_ids ONCE
   │
   ├─ 4. retrieval (per variant, all filtered by scope)
   │        a. dense    pgvector cosine, top 30
   │        b. lexical  tsvector + trigram, top 30
   │
   ├─ 5. fusion                 Reciprocal Rank Fusion (k = 60) over all lists
   ├─ 6. rerank                 bge-reranker-v2-m3 → top 6
   ├─ 7. context build          expand table chunks to full tables, dedupe,
   │                            label each source [S1]…[Sn], enforce budget
   ├─ 8. generate               local LLM, temperature 0
   ├─ 9. validate               citation + number/date grounding check
   └─ 10. respond               JSON: answer, language, sources, table
```

### Step 2 — query normalisation (the Banglish component)

This is the part the original spec was missing and the part most likely to
become the project's research contribution.

`exam routine kobe?` and `পরীক্ষার রুটিন কবে?` do not land near each other in
BGE-M3's embedding space, because romanised Bangla is barely represented in
its training data. So normalise before embedding:

1. **Detect**: Latin script, but the token stream fails an English dictionary
   check → treat as Banglish.
2. **Transliterate**: rule-based phonetic mapping (Avro-style) to Bangla
   script. Deterministic, fast, no model needed.
3. **Optionally paraphrase**: one short local-LLM call producing an English
   rendering, for corpora that are partly English.
4. **Embed all variants**, retrieve for each, fuse with RRF.

Measure this at M2 against the golden set: Recall@5 for Banglish queries
before and after. That delta is a reportable result.

### Step 3 — the permission choke point

There is exactly one function that resolves what a user may see:

```python
def visible_document_ids(session, user) -> set[int]: ...
```

Every retrieval query takes its result as a mandatory argument. There is no
code path that reaches `chunks` without it. This is enforced by a test, not
by discipline.

```sql
WHERE c.document_id = ANY(:visible_ids)
  AND c.version_id  = d.current_version_id
```

**pgvector caveat, stated honestly:** an HNSW scan combined with a `WHERE`
clause can return fewer than `k` rows when the filter is selective. Set
`hnsw.ef_search` higher and enable pgvector's iterative index scan
(`SET hnsw.iterative_scan = relaxed_order`), and over-fetch. Verify recall
with a filtered-search test rather than assuming.

### Step 8 — generation contract

The system prompt states three things: answer only from the numbered sources;
cite every claim as `[S1]`; if the sources do not contain the answer, reply
exactly *"I could not find this information in the available university
documents."* Answer in the language the question was asked in, regardless of
the language of the source. Preserve dates, numbers and names exactly —
never convert Bangla numerals to Latin ones or vice versa.

### Step 9 — answer validation

The spec listed `answer_validator.py` but no method. Three concrete checks:

1. **Citation integrity** — every `[Sn]` in the answer maps to a source that
   was actually in the context. A citation to `[S7]` when six sources were
   supplied is a hallucination signal.
2. **Numeric and date grounding** — extract every number and date from the
   answer (normalising Bangla ↔ Latin digits) and require each to appear in
   the context. This catches the most damaging failure mode: a plausible but
   invented deadline.
3. **Refusal honesty** — if reranker scores are all below a threshold, refuse
   before generating rather than after.

Failing check 1 or 2 → one regeneration attempt at temperature 0 → if it
fails again, return the refusal string. Log every validation failure; the
rate is a metric you report at M5.

### Step 10 — page images are part of the citation, not a nicety

Answer validation checks that a number in the answer appears in the retrieved
context. It cannot check that the context is itself correct.

Measured on a real DUET office order at M0: Tesseract read the memo date
`০৭/০৮/২০২৬` as `০৯/০৮/২০২৬`. Not garbage — a different, entirely plausible
date. Every downstream check passes. Retrieval finds the right document, the
LLM quotes it faithfully, the numeric grounding check confirms the digits are
present in the context, and the system reports a wrong deadline with a correct
citation and full confidence.

No amount of prompt engineering or LLM quality fixes this, because nothing
downstream of OCR has access to the truth. The only component that does is the
teacher's own eyes.

**Requirement.** Every citation resolves to the source page image.

- `GET /sources/{chunk_id}` returns the retrieved text **and** `page_image_url`.
- `GET /sources/{chunk_id}/image` renders that page from the stored original.
- The UI shows the page image inline, not behind a second click, whenever the
  answer contains a date, a memo number, an amount or a person's name — the
  fields where an OCR substitution is both most likely and most costly.
- The refusal path also carries citations, so a teacher can check a "not
  found" against the page too.

**Implementation.** Render on demand with PyMuPDF from the stored PDF at
~150 DPI, cached on disk with an LRU bound. Do not pre-render every page at
ingest: a 400-page document produces 400 PNGs that will mostly never be
viewed, and the render takes well under a second when it is actually needed.

This is small — one endpoint, one cache, one `<img>` — and it is the
difference between a system teachers can trust and one that quietly
misinforms them. It ships in **M1**, with the first cited answer, not later.

---

## 8. Security architecture

| Threat | Control |
|---|---|
| Unauthenticated access | JWT access (15 min) + refresh (7 days), argon2id password hashing |
| Privilege escalation | Role checked server-side on every endpoint; never trusted from the token payload alone for admin actions |
| **Unauthorised content reaching the LLM** | Permission filter applied in SQL **before** retrieval — the single choke point in §7.3. Never a post-filter, never a prompt instruction |
| Prompt injection from documents | Retrieved text is wrapped in explicit delimiters and labelled untrusted data; the LLM has **no tools**, so an injected instruction has nothing to act on; output validation catches the rest |
| Malicious upload | Extension allow-list + MIME sniff + size cap + SHA-256; files stored under a generated UUID name, never the user's filename |
| Path traversal | Storage path derived from UUID only; the original filename is a database column, never a filesystem path |
| SQL injection | Parameterised queries throughout; no string-built SQL anywhere, and none is needed since text-to-SQL is out of scope |
| Session abuse | Refresh-token rotation, revocation list, audit on every login |
| Log leakage | `audit_logs.detail` never contains document content; `query_logs` stores a question hash unless `LOG_QUERY_TEXT=true` |
| Secrets | Environment variables only, `.env` git-ignored, no defaults in code |

**Prompt-injection stance:** treat it as a containment problem, not a
detection problem. Pattern-matching for "ignore previous instructions" is
brittle and gives false confidence. The real defence is that the model can
only emit text, has no tool access, and its output is validated against the
context it was given.

---

## 9. Local model strategy

### Abstraction

```python
class LLMBackend(Protocol):
    def generate(self, system: str, user: str, *, temperature: float = 0.0,
                 max_tokens: int = 1024) -> str: ...

class EmbeddingBackend(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...

class OCREngine(Protocol):
    def extract(self, image_path: Path) -> OCRResult: ...
```

Three interfaces, three implementations at a time, chosen by config. No
module outside `llm/`, `embeddings/` and `ingestion/ocr/` may import Ollama,
sentence-transformers or any OCR library. Enforce this with a test that
greps the import graph — it is the only reliable way to keep an abstraction
honest over three months.

### Tiers

| VRAM | LLM | Use |
|---|---|---|
| CPU / < 6 GB | 1–4B q4 | Plumbing only. **Never report answer quality from this tier** |
| 6–10 GB | 7–8B q4 | Realistic minimum for acceptable Bangla RAG answers |
| 10–18 GB | 12–14B q4 | LLM + reranker resident together |
| 18 GB+ | 24–32B q4 | Target for final deployment |

Candidates to benchmark at M5: Qwen-family and Llama-family multilingual
instruct models, plus any Bangla-tuned model available at the time. Choose by
measurement, never by reputation.

### Swapping models by configuration -- what is free and what is not

The goal is a system that moves from a weak laptop to a strong server by
editing config. That works, but the cost is not uniform, and the difference
matters before you pick a model rather than after.

| Change | Cost |
|---|---|
| LLM swap (3B → 32B) | **Free.** Config only. No stored state depends on the LLM |
| LLM temperature, context, prompt | **Free.** Query-time |
| Reranker on/off or swap | **Free.** Reranking happens at query time |
| Embedding model, **same dimension** | Full re-embed of every chunk. Schema unchanged, `reindex.py` handles it |
| Embedding model, **different dimension** | Alembic migration **and** full re-embed |
| OCR engine | Affects newly ingested documents only. Existing ones need re-ingestion |

The asymmetry is the point: **the LLM is stateless, the embedding model is
not.** Every vector in the database lives in one model's space, and vectors
from two different models cannot be compared. So "swap in a heavier embedding
model" is never a config change alone — it is a re-embed.

**Therefore: pick the embedding dimension once and stay on it.** bge-m3 and
multilingual-e5-large are both 1024, which is why the schema fixes
`VECTOR(1024)`. Staying inside that family keeps every future embedding change
to a reindex instead of a migration.

### Configuration

```ini
# --- LLM: swap freely ---
LLM_BACKEND=ollama
LLM_MODEL=llama3.2:3b          # laptop, plumbing only
# LLM_MODEL=qwen3:32b          # server
OLLAMA_HOST=http://localhost:11434

# --- embeddings: changing these costs a reindex ---
EMBEDDING_MODEL=bge-m3
EMBEDDING_DIM=1024             # must match the schema
EMBEDDING_REV=1                # bump to force re-embedding

# --- reranker: free to toggle ---
RERANKER_ENABLED=false         # laptop: off, too slow on CPU
RERANKER_MODEL=bge-reranker-v2-m3

# --- ingestion ---
OCR_ENGINE=tesseract
OCR_LANGS=ben+eng
INGEST_DIGITAL_ONLY=true       # M1 default; false from M3
```

Nothing outside `llm/`, `embeddings/` and `ingestion/ocr/` may read these
values or import a model library. Enforce it with a test over the import
graph — an abstraction that is not tested is an abstraction that will be
bypassed under deadline pressure.

### Concurrency

Ollama serialises requests by default. Set `OLLAMA_NUM_PARALLEL` and
`OLLAMA_NUM_CTX` deliberately, and load-test with the realistic number of
concurrent teachers **before** deployment day. Queue at the application layer
so a burst produces waiting, not timeouts.

### Re-embedding

Changing the embedding model invalidates every vector. `scripts/reindex.py`
must exist from M1 — before you need it — and must be able to re-embed in
batches without dropping the table.

---

## 10. Evaluation strategy

### Dataset

30 questions at M0 (10 bn / 10 en / 10 bnglish, ~5 deliberately
unanswerable). 150 at M5. Every record carries expected answer, expected
document, expected page, language and query type.

### Metrics

| Stage | Metrics |
|---|---|
| Retrieval | Recall@1 / @3 / @5, MRR, nDCG@5 — reported **separately per language** |
| Generation | correctness, faithfulness, citation accuracy, hallucination rate, refusal accuracy on unanswerable questions |
| OCR | CER, WER per engine and per document class |
| Performance | ingestion sec/page, embedding sec/chunk, retrieval ms, rerank ms, generation ms, total p50 / p95, peak RAM / VRAM |

Averaging Bangla and Banglish results into one number hides the exact failure
this project exists to solve. Always report per-language.

### Rules

Golden answers are written before the system is run on them. Every reported
number comes from a committed script and is reproducible. If a number was not
measured, it is not written down — not in the report, not in the slides, not
in a table with a "≈" in front of it.

---

## 11. Folder structure

```
duet-ai-assistant/
├─ README.md             setup, layout, milestone status
├─ ARCHITECTURE.md       this document
├─ .gitignore            blocks all document content from version control
├─ requirements.txt      full install
├─ requirements/         base / ocr / ml / api / dev groups + lock-M*.txt
├─ docs/                 M0_report.md, M1_report.md, runbook.md
├─ m0_reality_check/     OCR bake-off, hardware audit, golden questions
│                        (its OCR adapters are promoted into ingestion/ocr/ at M3)
├─ config/               settings.py (pydantic-settings), .env.example
├─ database/             connection.py, models.py, repositories.py
├─ migrations/           alembic
├─ ingestion/
│   ├─ classify.py       chars/page → DIGITAL | MIXED | SCANNED
│   ├─ loaders/          pdf.py, docx.py, image.py, txt.py
│   ├─ ocr/              base.py (OCREngine), tesseract.py, easyocr.py,
│   │                    surya.py, vlm_ollama.py
│   ├─ tables/           detect.py, normalize.py, serialize.py
│   ├─ clean.py          NFC, invisibles, dehyphenation, whitespace
│   ├─ chunker.py        boundary-aware, Bangla-danda aware
│   └─ pipeline.py       orchestrates 1→8
├─ jobs/                 queue.py, worker.py
├─ embeddings/           base.py, bge_m3.py, batch.py
├─ retrieval/
│   ├─ scope.py          visible_document_ids  ← the security choke point
│   ├─ normalize_query.py  language detect, transliteration, variants
│   ├─ dense.py, lexical.py
│   ├─ fusion.py         RRF
│   └─ rerank.py
├─ rag/
│   ├─ context.py, prompts.py, generate.py
│   ├─ validate.py       citation + numeric grounding
│   └─ pipeline.py
├─ llm/                  base.py, ollama.py, registry.py
├─ security/             auth.py, authz.py, hashing.py, audit.py, uploads.py
├─ api/
│   ├─ main.py
│   └─ routes/           auth.py, documents.py, chat.py, admin.py, health.py
├─ frontend/             templates/, static/
├─ evaluation/           dataset.py, retrieval_eval.py, answer_eval.py,
│                        ocr_eval.py, latency_eval.py, report.py
├─ scripts/              reindex.py, create_admin.py, seed_demo.py, backup.sh
├─ tests/                unit/, integration/, security/
└─ docker-compose.yml
```

---

## 12. API surface

```
POST   /auth/login                 → access + refresh token
POST   /auth/refresh
POST   /auth/logout
GET    /auth/me

POST   /documents                  → 202 { document_id, version_id, job_id }
GET    /documents                  → only documents visible to the caller
GET    /documents/{id}
GET    /documents/{id}/status      → ingestion progress
DELETE /documents/{id}             → archive + delete chunks
POST   /documents/{id}/versions    → new version, re-ingest
POST   /documents/{id}/permissions
DELETE /documents/{id}/permissions/{pid}

POST   /chat                       → answer + sources (+ table)
GET    /chat/history
GET    /sources/{chunk_id}         → the exact retrieved text + page_image_url
GET    /sources/{chunk_id}/image   → the source page rendered from the original

GET    /health                     → db, ollama, embedding model, worker heartbeat

GET    /admin/users      POST /admin/users      DELETE /admin/users/{id}
GET    /admin/audit-logs
GET    /admin/stats
```

`GET /sources/{chunk_id}` is small and disproportionately valuable: it lets a
teacher verify a citation against the exact retrieved text, which is what
converts scepticism into trust during a demo.

### Response contract

```json
{
  "answer": "…",
  "language": "bn",
  "refused": false,
  "sources": [
    { "id": 1, "chunk_id": 8842, "document": "academic_calendar_2026.pdf",
      "page": 4, "section": "Examination Schedule", "table_id": null,
      "score": 0.87, "page_image_url": "/sources/8842/image",
      "show_image": true }
  ],
  "table": {
    "columns": ["Course Code", "Course Name", "Credit"],
    "rows": [["CSE1101", "Programming", 3]]
  },
  "diagnostics": { "route": "hybrid", "latency_ms": 3120, "validated": true }
}
```

`table` is `null` unless a table chunk was used. `diagnostics` is omitted in
production responses unless the caller is an admin.

`show_image` is set by the backend when the answer contains a date, number,
memo reference or name — the cases where an OCR substitution would be
invisible in the text but obvious on the page. The client must honour it.

---

## 13. Roadmap

| Milestone | Weeks | Delivers | Exit criteria | Original phases |
|---|---|---|---|---|
| **M0 Reality check** | 1 | OCR bake-off, hardware audit, 30 golden questions, real document corpus | Chosen OCR engine with measured CER; DIGITAL/SCANNED split known | 1, 19 (partial) |
| **M1 Vertical slice** | 2–3 | **DIGITAL PDFs only** → chunk → pgvector → retrieve → Ollama → **cited answer + page image**. CLI + one minimal page | Recall@5 measured on the golden set; a teacher can ask a question in a browser, see a citation, and open the source page image next to the answer | 2, 4, 5, 9–12, 16, 17, 20 (smoke only) |
| **M2 Multilingual retrieval** | 4 | Query normalisation, Banglish transliteration, RRF, reranker | Banglish Recall@5 measured before and after; the delta is written down | 14, 15, 21 |
| **M3 Documents at full fidelity** | 5–6 | OCR in the pipeline, table extraction, JSONB + markdown chunks, table answers | A table question returns the complete correct table with a page citation | 6, 7, 8, 13, 18 |
| **M4 Security & scale** | 7 | Postgres auth, RBAC, document permissions, audit log, async worker, upload hardening | A test proves an unauthorised document's text can never enter the LLM context | 23–27 |
| **M5 Benchmark** | 8–9 | 150 questions; 3 LLMs × 2 embedding models; hallucination and refusal rates | Comparison tables produced by a committed script, per language | 3, 20, 21, 22 |
| **M6 Deploy & hand over** | 10+ | Server deployment, load test, security test, backup, runbook, research report | Runs on the department machine; another person can restart it from the runbook | 28–32 |

Each milestone is demonstrable on its own. If the project has to stop at M3,
what exists still works and still demos.

### Phase 1 — what to build first

M1 begins with exactly this, and nothing more:

1. `config/settings.py` — env-driven settings, no hard-coded paths or secrets
2. `database/models.py` + first Alembic migration — **the full schema from
   §5, including permissions and `embedding_rev`**, even though nothing uses
   them yet. This is the one thing that must not be deferred.
3. `llm/base.py`, `embeddings/base.py`, `ingestion/ocr/base.py` — the three
   interfaces, with one implementation each
4. `docker-compose.yml` — postgres + pgvector, healthcheck, volume
5. `scripts/create_admin.py` and a `pytest` run that passes against a real
   test database

Then the M1 slice: load 10 PDFs, chunk, embed, retrieve, generate, cite.

---

## 14. Risk register

| Risk | Likelihood | Impact | Mitigation | Trigger to act |
|---|---|---|---|---|
| Bangla OCR quality too low | **Measured at M0: mean CER 0.307, median 0.246 on scanned pages (Tesseract)** | High | M1 ingests DIGITAL documents only, where native extraction is exact. OCR decision deferred to M3, when server hardware is known | already at the 0.30 threshold — do not treat scanned notices as solved |
| **OCR misreads a digit into a valid wrong value** | **Confirmed at M0** | **High** | Page image beside every citation (§7.10); never present a date or memo number as text alone | already observed — treat as present, not potential |
| Banglish retrieval fails | High | High | §7.2 normalisation; fall back to asking teachers to type Bangla or English | Banglish Recall@5 < 0.5 after M2 |
| No GPU available | Medium | High | Ask early; CPU-only means a smaller model and slower ingestion, not failure | M0 hardware audit shows no GPU |
| Local LLM's Bangla generation is weak | Medium | High | M1 smoke test across 5 models; fall back to English answers with Bangla citations | Fluency unacceptable at 8B tier |
| Table extraction mangles structure | Medium | Medium | JSONB + whole-table chunk; fall back to page-image citation so the teacher reads the table themselves | Table questions below 60% at M3 |
| Scope creep | High | High | Milestone gates; §1 non-goals list | Any v2 item entering a milestone |
| Permission leak through retrieval | Low | **Fatal** | Single choke point + a test that asserts unauthorised chunks never appear | Any test failure blocks release |
| Solo developer / bus factor | High | Medium | Runbook and handover doc at M6; README from day 1 | — |

---

## 15. Requirements traceability

Every numbered requirement from the original specification, and where it now lives.

| # | Requirement | Component | Milestone |
|---|---|---|---|
| 1 | Accept PDF, DOCX, TXT, images | `ingestion/loaders/` | M1 (PDF), M3 (rest) |
| 2 | Extract native PDF/DOCX text | `loaders/pdf.py`, `loaders/docx.py` | M1 |
| 3 | Detect scanned PDFs | `ingestion/classify.py` | M1 |
| 4 | OCR scanned pages and images | `ingestion/ocr/` | M3 |
| 5 | Bangla + English OCR | OCR engine chosen at M0 | M0 / M3 |
| 6 | Detect and extract tables | `ingestion/tables/detect.py` | M3 |
| 7 | Preserve rows/columns | `doc_tables.columns` / `.rows` | M3 |
| 8 | Store tables separately | `doc_tables` (JSONB) | M3 |
| 9 | Semantic retrieval | `retrieval/dense.py` (pgvector) | M1 |
| 10 | Structured retrieval | `doc_tables` + whole-table chunks (not SQL) | M3 |
| 11 | Hybrid retrieval | `retrieval/fusion.py` (RRF) | M2 |
| 12 | Reranking | `retrieval/rerank.py` | M2 |
| 13 | Local LLM generation | `llm/ollama.py` | M1 |
| 14 | Answer in the right language | `rag/prompts.py` + language detect | M1 |
| 15 | Understand Banglish | `retrieval/normalize_query.py` | M2 |
| 16 | Source citations | `rag/context.py`, `rag/validate.py` | M1 |
| 17 | Document name and page | `chunks.page_no`, response `sources[]`, page-image endpoint (§7.10) | M1 |
| 18 | Markdown/HTML tables on request | `rag/prompts.py` + response `table` | M3 |
| 19 | Multi-document questions | RRF over the whole visible scope | M2 |
| 20 | Refuse instead of hallucinating | `rag/validate.py` + refusal threshold | M1 |
| 21 | Teacher authentication | `security/auth.py` | M4 |
| 22 | Role-based authorization | `security/authz.py` | M4 |
| 23 | Document-level permissions | `document_permissions` + `retrieval/scope.py` | M4 (schema at M1) |
| 24 | Audit logs | `security/audit.py`, `audit_logs` | M4 |
| 25 | Document versioning | `document_versions` | M4 (schema at M1) |
| 26 | Runs on the local network | docker-compose, no egress | M6 |

---

## 16. Operations

**Backup.** `pg_dump` nightly plus the file store. Test the restore once,
before deployment, and write down how long it took. An untested backup is a
belief, not a backup.

**Monitoring.** `/health` reports database, Ollama, embedding model and
worker heartbeat. Log ingestion failures and validation failures with enough
context to reproduce them.

**Handover.** A runbook covering: how to start and stop the stack, how to add
a teacher, how to re-ingest a document, how to change the LLM, what to do
when the worker dies, and where the backups are. You will graduate; the
system should not leave with you. Your adviser will notice that you thought
about it.

---

## 17. Engineering principles

Privacy first · local inference only, embeddings and OCR included · never
trust retrieved text as instructions · never let unauthorised content reach
the model · abstract every model behind an interface · test every component
independently · no hard-coded secrets · no document content in logs ·
benchmark before choosing · never fabricate a number · prefer a small system
that works to a large one that almost does.
