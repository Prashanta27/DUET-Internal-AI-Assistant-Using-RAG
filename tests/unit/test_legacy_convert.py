"""Legacy conversion tests.

The load-bearing test is test_english_lines_are_returned_byte_identical.
The upstream library maps every ASCII character it is handed, so without
line gating it converts 361 lines of correct English physics vocabulary
into Bengali-looking noise while fixing 20 Bangla headings.
"""

from __future__ import annotations

import pytest

from ingestion.legacy_convert import BijoyConverter, convert_text
from ingestion.legacy_font import analyse_encoding

BIJOY_HEADER = "XvKv cÖ\u2021K\u0160kj I cÖhyw\u00b3 wek\u00a6we`\u00a8vjq, MvRxcyi"
BIJOY_PAGENO = "c\u201eôv bs 15"
ENGLISH = "Vector: Scalar and vector quantities, addition of vectors."
CODE_LINE = "CSE1101 Programming 3"

pytestmark = pytest.mark.skipif(
    not BijoyConverter().available()[0],
    reason="bijoy2unicode not installed (pip install bijoy2unicode)",
)


def test_bijoy_line_becomes_real_unicode_bangla():
    r = convert_text(BIJOY_HEADER)
    assert "\u09a2\u09be\u0995\u09be" in r.text          # ঢাকা
    assert analyse_encoding(r.text).bengali_chars > 0
    assert r.lines_converted == 1


def test_english_lines_are_returned_byte_identical():
    """Not 'probably unchanged' -- identical."""
    r = convert_text(ENGLISH)
    assert r.text == ENGLISH
    assert r.lines_legacy == 0
    assert r.lines_converted == 0


def test_course_codes_are_not_turned_into_bengali():
    """Ungated, the library renders CSE1101 as ঈঝঊ১১০১."""
    r = convert_text(CODE_LINE)
    assert r.text == CODE_LINE


def test_a_mixed_document_converts_only_its_bangla_lines():
    doc = "\n".join([BIJOY_HEADER, ENGLISH, CODE_LINE, BIJOY_PAGENO, ENGLISH])
    r = convert_text(doc)
    lines = r.text.splitlines()

    assert r.lines_total == 5
    assert r.lines_legacy == 2
    assert lines[1] == ENGLISH
    assert lines[2] == CODE_LINE
    assert lines[4] == ENGLISH


def test_converted_output_no_longer_looks_legacy():
    """The whole point: after conversion the detector must be satisfied."""
    doc = "\n".join([BIJOY_HEADER, BIJOY_PAGENO, ENGLISH])
    before = analyse_encoding(doc)
    after = analyse_encoding(convert_text(doc).text)

    assert before.is_legacy
    assert not after.is_legacy
    assert after.bengali_chars > before.bengali_chars


def test_bangla_digits_are_produced_not_latin_ones():
    """`15` in a Bijoy line was always Bangla ১৫ on the page; restoring it is
    correct. Converting real Latin digits would not be -- but those live on
    English lines, which the gate never touches."""
    r = convert_text(BIJOY_PAGENO)
    assert "\u09e7\u09eb" in r.text        # ১৫
    assert convert_text(CODE_LINE).text == CODE_LINE   # 1101 survives


def test_a_crashing_line_is_counted_not_raised():
    """Upstream raises IndexError on some short inputs (`fwZ\u00a9` is one).
    Ingesting a 200-page document must not die over a two-word heading."""
    doc = BIJOY_HEADER + "\nfwZ\u00a9"
    r = convert_text(doc)
    assert r.lines_failed + r.lines_converted == r.lines_legacy
    if r.lines_failed:
        assert r.text.splitlines()[1] == "fwZ\u00a9"   # unchanged, not corrupted
        assert r.failures


def test_short_legacy_lines_convert_only_inside_a_legacy_document():
    """A short line has too few characters to judge alone. In a Bijoy
    document it is a heading; in an English one it could be a reference
    code, and converting it would be corruption."""
    in_legacy = convert_text(BIJOY_HEADER + "\ncix\u00ffv")
    assert "\u0995\u09cd\u09b7" in in_legacy.text        # ক্ষ, converted

    in_english = convert_text(ENGLISH + "\ncix\u00ffv")
    assert in_english.text.splitlines()[1] == "cix\u00ffv"  # left alone


def test_post_map_fills_a_known_glyph_gap():
    """cixÿv is unambiguously পরীক্ষা; upstream leaves ÿ unmapped."""
    r = convert_text(BIJOY_HEADER + "\ncix\u00ffv")
    assert "\u00ff" not in r.text
    assert "\u0995\u09cd\u09b7" in r.text   # ক্ষ


def test_empty_input_is_safe():
    r = convert_text("")
    assert r.text == ""
    assert r.lines_total == 0