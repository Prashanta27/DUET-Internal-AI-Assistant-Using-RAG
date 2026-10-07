"""Legacy-encoding detector tests.

The strings below are real: they came out of a DUET admission-syllabus PDF
via PyMuPDF. Keeping the actual text as the fixture is the point -- a
synthetic approximation would not have the same marker density.
"""

from __future__ import annotations

from ingestion.legacy_font import (
    LEGACY_THRESHOLD, analyse_encoding, analyse_pages,
)

# Real extraction from "DUET Syllabus for Admission Test 2021-2022.pdf",
# page 15. Reads as Bangla on screen, is Latin in the file.
BIJOY = (
    "AvÛviMÖ¨vRy‡qU †cÖvMÖvg-Gi fwZ© cixÿv welqK Z_¨vejx (cÖm‡c±vm), "
    "wkÿvel© 2021-2022\nc„ôv bs 15"
)

UNICODE_BN = "পরীক্ষার রুটিন আগামী ০৭/০৮/২০২৬ তারিখে প্রকাশিত হবে।"

ENGLISH = (
    "Vector: Scalar and vector quantities, addition and subtraction of "
    "vectors, law of parallelogram, resolution of vectors."
)


def test_bijoy_text_is_flagged():
    r = analyse_encoding(BIJOY)
    assert r.is_legacy
    assert r.verdict == "LEGACY"
    assert r.bengali_chars == 0
    assert r.legacy_lines == r.judged_lines


def test_real_unicode_bangla_is_not_flagged():
    r = analyse_encoding(UNICODE_BN)
    assert not r.is_legacy
    assert r.verdict == "UNICODE_BN"
    assert r.bengali_chars > 0


def test_plain_english_is_not_flagged():
    r = analyse_encoding(ENGLISH)
    assert not r.is_legacy
    assert r.verdict == "LATIN_ONLY"
    assert r.marker_ratio == 0.0


def test_unicode_bangla_with_stray_accents_is_not_flagged():
    """Bengali codepoints are decisive. A café or a naïve in the same
    paragraph must not tip a correct document into the LEGACY bucket."""
    r = analyse_encoding(UNICODE_BN + " café naïve Ångström")
    assert not r.is_legacy


def test_empty_text_is_safe():
    r = analyse_encoding("")
    assert not r.is_legacy
    assert r.total_chars == 0


def test_a_bangla_header_band_is_not_diluted_away_by_english_pages():
    """The real failure this module was rewritten for.

    "DUET Syllabus for Admission Test 2021-2022.pdf" is ten pages of English
    with a Bijoy header on each. Averaging markers over the document scored
    it 0.0049 and called it LATIN_ONLY -- while printing a visibly Bijoy
    sample line. Counting legacy LINES catches it.
    """
    doc_text = "\n".join([BIJOY] + [ENGLISH] * 40)
    r = analyse_encoding(doc_text)
    assert r.marker_ratio < LEGACY_THRESHOLD, "the average really is tiny"
    assert r.is_legacy, "and it must still be flagged"
    assert r.verdict == "PARTIAL_LEGACY"
    assert r.legacy_lines >= 1


def test_a_fully_english_document_is_never_flagged():
    r = analyse_encoding("\n".join([ENGLISH] * 40))
    assert not r.is_legacy
    assert r.verdict == "LATIN_ONLY"


def test_unicode_bangla_plus_legacy_is_reported_as_mixed():
    r = analyse_encoding(UNICODE_BN + "\n" + BIJOY)
    assert r.verdict == "MIXED"
    assert r.is_legacy


def test_short_lines_are_not_judged():
    """A stray "Ref." or a page number is too small to classify."""
    r = analyse_encoding("Ref.\nA-1\n" + ENGLISH)
    assert r.judged_lines == 1


def test_per_page_report_is_returned():
    pages = [ENGLISH * 6 + "\n" + BIJOY for _ in range(5)]
    doc, per_page = analyse_pages(pages)
    assert len(per_page) == 5
    assert all(p.is_legacy for p in per_page)
    assert doc.is_legacy