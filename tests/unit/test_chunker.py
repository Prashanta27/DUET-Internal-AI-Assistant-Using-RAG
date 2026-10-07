"""Chunker tests.

The test that earns its keep is test_bangla_sentences_are_not_cut_in_half.
Everything else here is guarding an invariant; that one is guarding the
specific failure this project exists to avoid.
"""

from __future__ import annotations

from ingestion.chunker import Page, chunk_pages, is_heading, rough_token_count

DANDA = "\u0964"

BN_SENTENCES = [
    "পরীক্ষার রুটিন আগামী ০৭/০৮/২০২৬ তারিখে প্রকাশিত হবে",
    "সকল বিভাগীয় প্রধানকে যথাসময়ে ফলাফল জমা দিতে অনুরোধ করা হলো",
    "বিলম্বে জমাদানের ক্ষেত্রে লিখিত অনুমতি গ্রহণ করতে হবে",
    "রেজিস্ট্রেশনের শেষ তারিখ ২৮ ফেব্রুয়ারি ২০২৬",
]
BN_PARA = (DANDA + " ").join(BN_SENTENCES) + DANDA


def _bn_page(repeat: int = 12) -> Page:
    return Page(page_no=1, text="\n\n".join([BN_PARA] * repeat))


def test_bangla_sentences_are_not_cut_in_half():
    """A splitter that only knows '.' cuts Bangla mid-sentence and nothing
    downstream complains. This is the guard for that."""
    chunks = chunk_pages(_bn_page(), target_tokens=120, overlap_tokens=20)
    assert len(chunks) > 1, "test is meaningless if it produced one chunk"

    for c in chunks:
        body = c.text.strip()
        # every chunk must end at a real sentence boundary
        assert body.endswith(DANDA), f"chunk does not end on a danda: ...{body[-40:]!r}"
        # and no fragment of a known sentence may appear without its whole self
        for sent in BN_SENTENCES:
            head = sent[: len(sent) // 2]
            if head in body:
                assert sent in body, (
                    f"sentence was split: found {head!r} without the rest"
                )


def test_bangla_digits_survive_chunking():
    """০৭ must never become 07. The whole project answers questions about
    exactly these numbers."""
    chunks = chunk_pages(_bn_page(3), target_tokens=200, overlap_tokens=0)
    joined = " ".join(c.text for c in chunks)
    assert "০৭/০৮/২০২৬" in joined
    assert "07/08/2026" not in joined


def test_chunks_never_cross_a_page_boundary():
    """Every citation resolves to a page image; a chunk spanning two pages
    has no answer to 'which page do I show?'."""
    pages = [Page(1, "Page one text. " * 40), Page(2, "Page two text. " * 40)]
    chunks = chunk_pages(pages, target_tokens=400, overlap_tokens=20)
    for c in chunks:
        if c.page_no == 1:
            assert "two" not in c.text
        else:
            assert "one" not in c.text


def test_token_budget_is_respected():
    chunks = chunk_pages(_bn_page(20), target_tokens=150, overlap_tokens=30)
    over = [c.token_count for c in chunks if c.token_count > 150 * 1.15]
    assert not over, f"chunks over budget: {over}"


def test_overlap_carries_whole_sentences():
    chunks = chunk_pages(_bn_page(12), target_tokens=120, overlap_tokens=40)
    assert len(chunks) > 1
    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        if prev.page_no != nxt.page_no:
            continue
        # the overlap must be a suffix of the previous chunk, not a fragment
        overlap_found = any(
            s in prev.text and s in nxt.text for s in BN_SENTENCES
        )
        assert overlap_found or nxt.text.startswith(BN_SENTENCES[0][:10])


def test_headings_become_sections():
    page = Page(1, "EXAMINATION SCHEDULE\n\n" + "Detail line. " * 30)
    chunks = chunk_pages(page, target_tokens=200, overlap_tokens=0)
    assert any(c.section == "EXAMINATION SCHEDULE" for c in chunks)


def test_heading_detection_does_not_fire_on_ordinary_sentences():
    assert is_heading("EXAMINATION SCHEDULE")
    assert is_heading("3.1 Course Registration")
    assert not is_heading("The examination will begin on 12 March 2026.")
    assert not is_heading("সকল বিভাগীয় প্রধানকে জানানো যাচ্ছে যে " + DANDA)
    # a bare number starting a sentence is not a heading
    assert not is_heading("12 March 2026 is the published date")
    assert is_heading("3. Scope")


def test_every_chunk_carries_what_a_citation_needs():
    chunks = chunk_pages(_bn_page(6), target_tokens=150, overlap_tokens=20)
    for c in chunks:
        assert c.page_no >= 1
        assert c.chunk_index >= 0
        assert len(c.sha256) == 64
        assert c.token_count > 0
        assert c.lang in {"bn", "en", "mixed", "unknown"}


def test_identical_text_hashes_identically_across_chunk_runs():
    """Chunk-level incremental ingestion depends on this: a re-uploaded
    document with one corrected line must cost one embedding, not forty."""
    a = chunk_pages(_bn_page(4), target_tokens=150, overlap_tokens=0)
    b = chunk_pages(_bn_page(4), target_tokens=150, overlap_tokens=0)
    assert [c.sha256 for c in a] == [c.sha256 for c in b]


def test_empty_and_whitespace_pages_produce_nothing():
    assert chunk_pages([Page(1, ""), Page(2, "   \n\n  ")]) == []


def test_english_and_mixed_text_is_labelled():
    en = chunk_pages(Page(1, "The examination begins on 12 March 2026. " * 20),
                     target_tokens=200)
    assert all(c.lang == "en" for c in en)

    mixed = chunk_pages(
        Page(1, ("পরীক্ষা শুরু হবে on 12 March 2026 " + DANDA + " ") * 20),
        target_tokens=200)
    assert any(c.lang == "mixed" for c in mixed)


def test_rough_counter_is_only_an_estimate():
    """Guard against anyone reporting a chunk size measured with it."""
    bn = rough_token_count("পরীক্ষার রুটিন")
    en = rough_token_count("examination routine")
    # Bangla costs more tokens per character; if this inverts, the estimate
    # has been "simplified" into uselessness.
    assert bn > en