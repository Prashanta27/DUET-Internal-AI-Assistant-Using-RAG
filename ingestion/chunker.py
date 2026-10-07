"""Boundary-aware chunking.

Target 450 tokens, 80 overlap, measured with the EMBEDDING MODEL'S tokeniser
-- not by whitespace. A Bangla word is several tokens, so a whitespace-based
budget produces chunks that are far over the real limit and get truncated
silently at embedding time.

Split priority (ARCHITECTURE.md §6):
    heading -> blank line -> Bangla danda -> English sentence end
            -> newline -> hard cut

The danda step is the one that matters. A splitter that only knows "." will
cut Bangla text mid-sentence, and nothing downstream will complain -- you
find out at M5 when Bangla Recall@5 is bad and the cause is three milestones
behind you.

CHUNKS DO NOT CROSS PAGE BOUNDARIES.
    Every citation must resolve to a page image (§7.10). A chunk spanning
    pages 4 and 5 has no answer to "which page do I show the teacher?", and
    a page image that does not contain the quoted line is worse than none.
    The cost is some short chunks at page ends. That is the cheaper mistake.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from ingestion.clean import clean_text, content_sha256, detect_lang

DANDA = "\u0964"

# Sentence ends. Bangla danda first, then Latin terminators. The lookahead
# keeps the terminator attached to the sentence it ends.
_SENTENCE_END = re.compile(rf"(?<=[{DANDA}?!।])\s+|(?<=[.?!])\s+(?=[A-Z\u0980-\u09FF])")

_BLANK_LINE = re.compile(r"\n\s*\n")

# Heading heuristics, in the order they are tried. Deliberately conservative:
# a missed heading costs a slightly worse split, a false heading costs a
# chunk cut in the middle of a sentence.
# Either a dotted multi-level number ("3.1 Course Registration") or a single
# number with an explicit separator ("3. Scope", "৩) পরিধি").
#
# A bare number followed by words is NOT a heading: "12 March 2026 is the
# published date" would otherwise be promoted to a section title and split
# the paragraph it belongs to.
_NUMBERED_HEADING = re.compile(
    r"^\s*(\d+\.\d+(\.\d+)*|\d+[.)]|[\u09E6-\u09EF]+[.)])\s+\S"
)
_ALLCAPS_HEADING = re.compile(r"^[A-Z0-9 \-&/(),.]{4,80}$")


@dataclass
class Page:
    """One page of extracted text, straight from a loader."""

    page_no: int
    text: str


@dataclass
class Chunk:
    text: str
    page_no: int
    chunk_index: int
    section: str | None = None
    token_count: int = 0
    lang: str = "unknown"
    sha256: str = ""
    meta: dict = field(default_factory=dict)


TokenCounter = Callable[[str], int]


def rough_token_count(text: str) -> int:
    """Fallback counter for tests and for code paths that must not load a
    2 GB model. It is an ESTIMATE and says so.

    Production passes embedder.count_tokens instead. Never report a chunk
    size measured with this.
    """
    bengali = sum(1 for c in text if "\u0980" <= c <= "\u09ff")
    other = len(text) - bengali
    # bge-m3's tokeniser averages ~2 chars/token on Bangla, ~4 on Latin.
    return int(bengali / 2 + other / 4) + 1


def is_heading(line: str) -> bool:
    line = line.strip()
    if not line or len(line) > 100:
        return False
    if line.endswith((".", DANDA, ",", ";", ":")) and not line.isupper():
        return False
    if _NUMBERED_HEADING.match(line):
        return True
    if _ALLCAPS_HEADING.match(line) and len(line.split()) <= 12:
        return True
    return False


def _split_into_blocks(text: str) -> list[tuple[str, str | None]]:
    """(block_text, section) pairs, split on headings and blank lines."""
    blocks: list[tuple[str, str | None]] = []
    section: str | None = None

    for para in _BLANK_LINE.split(text):
        para = para.strip()
        if not para:
            continue
        lines = para.split("\n")
        buf: list[str] = []
        for line in lines:
            if is_heading(line):
                if buf:
                    blocks.append(("\n".join(buf).strip(), section))
                    buf = []
                section = line.strip()
                blocks.append((line.strip(), section))
            else:
                buf.append(line)
        if buf:
            blocks.append(("\n".join(buf).strip(), section))
    return [(b, s) for b, s in blocks if b]


def _split_oversized(text: str, budget: int, count: TokenCounter) -> list[str]:
    """One block is bigger than the budget. Descend the priority ladder."""
    if count(text) <= budget:
        return [text]

    for splitter in (_sentences, _lines):
        parts = splitter(text)
        if len(parts) > 1:
            out, buf = [], ""
            for part in parts:
                candidate = f"{buf} {part}".strip() if buf else part
                if buf and count(candidate) > budget:
                    out.append(buf)
                    buf = part
                else:
                    buf = candidate
            if buf:
                out.append(buf)
            # a single sentence can still be over budget
            result: list[str] = []
            for piece in out:
                result.extend(_hard_cut(piece, budget, count)
                              if count(piece) > budget else [piece])
            return result

    return _hard_cut(text, budget, count)


def _sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_END.split(text) if s and s.strip()]


def _lines(text: str) -> list[str]:
    return [ln for ln in text.split("\n") if ln.strip()]


def _hard_cut(text: str, budget: int, count: TokenCounter) -> list[str]:
    """Last resort. Cuts on a space where possible so a word survives."""
    out: list[str] = []
    words = text.split(" ")
    buf = ""
    for w in words:
        candidate = f"{buf} {w}".strip() if buf else w
        if buf and count(candidate) > budget:
            out.append(buf)
            buf = w
        else:
            buf = candidate
    if buf:
        out.append(buf)
    return out or [text]


def _overlap_tail(text: str, overlap: int, count: TokenCounter) -> str:
    """Trailing whole sentences worth roughly `overlap` tokens.

    Whole sentences, not a token slice: the overlap exists so a fact that
    straddles a boundary is retrievable from either chunk, and half a
    sentence is not retrievable from either.
    """
    if overlap <= 0:
        return ""
    parts = _sentences(text) or [text]
    tail: list[str] = []
    for part in reversed(parts):
        tail.insert(0, part)
        if count(" ".join(tail)) >= overlap:
            break
    return " ".join(tail).strip()


def chunk_pages(
    pages: Page | list[Page],
    *,
    target_tokens: int = 450,
    overlap_tokens: int = 80,
    count_tokens: TokenCounter | None = None,
) -> list[Chunk]:
    """Turn extracted pages into chunks ready to embed.

    `count_tokens` should be embedder.count_tokens. It is a parameter rather
    than an import so that this module never loads a model -- the import
    graph test depends on that, and so does test speed.
    """
    if isinstance(pages, Page):
        pages = [pages]
    count = count_tokens or rough_token_count
    chunks: list[Chunk] = []
    index = 0

    for page in pages:
        text = clean_text(page.text)
        if not text:
            continue

        blocks = _split_into_blocks(text)
        buf, buf_section = "", None

        def flush(force: bool = False) -> None:
            nonlocal buf, index, buf_section
            body = buf.strip()
            if not body:
                return
            if not force and count(body) < 20:
                # A 5-token fragment retrieves noise. Let it merge forward.
                return
            chunks.append(Chunk(
                text=body,
                page_no=page.page_no,
                chunk_index=index,
                section=buf_section,
                token_count=count(body),
                lang=detect_lang(body),
                sha256=content_sha256(body),
            ))
            index += 1
            buf = _overlap_tail(body, overlap_tokens, count)

        for block, section in blocks:
            for piece in _split_oversized(block, target_tokens, count):
                candidate = f"{buf}\n\n{piece}".strip() if buf else piece
                if buf and count(candidate) > target_tokens:
                    flush(force=True)
                    candidate = f"{buf}\n\n{piece}".strip() if buf else piece
                buf = candidate
                buf_section = section or buf_section

        flush(force=True)
        buf, buf_section = "", None

    return chunks