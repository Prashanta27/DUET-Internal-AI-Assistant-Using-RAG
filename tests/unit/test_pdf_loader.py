"""Loader tests, built around the failure that font gating exists to fix.

A DUET admission notice writes `সকল Check Box টিক চিহ্ন` as one line: Bijoy
bytes in SutonnyMJ next to real English in TimesNewRomanPSMT. Line-level
conversion turned `Check Box` into `ঈযবপশ ইড়ী`. These tests pin the fix.
"""

from __future__ import annotations

import pytest

from ingestion.legacy_font import font_verdicts

BIJOY = "XvKv cÖ\u2021K\u0160kj I cÖhyw\u00b3 wek\u00a6we`\u00a8vjq"
ENGLISH = "Online Application Form"


def test_font_verdicts_separates_the_two_families():
    """The real font mix measured in DUET admission information session
    2024-2025.pdf: SutonnyMJ 0.169 markers, TimesNewRomanPSMT 0.000."""
    spans = [
        ("SutonnyMJ", BIJOY),
        ("SutonnyMJBold", "c\u201eôv bs 1"),
        ("TimesNewRomanPSMT", ENGLISH),
        ("TimesNewRomanPS-BoldMT", "Admission Information (Prospectus)"),
    ]
    legacy = font_verdicts(spans)
    assert legacy == {"SutonnyMJ", "SutonnyMJBold"}


def test_a_font_drawing_real_unicode_bangla_is_never_flagged():
    spans = [("NikoshBAN", "\u09aa\u09b0\u09c0\u0995\u09cd\u09b7\u09be\u09b0 \u09b0\u09c1\u099f\u09bf\u09a8")]
    assert font_verdicts(spans) == set()


def test_a_symbol_font_with_a_few_bullets_is_left_alone():
    assert font_verdicts([("SymbolMT", "\u2022\u2022")]) == set()


def test_font_name_hint_catches_a_short_span():
    """Eight characters is too little for a ratio, but the name is decisive."""
    assert "SutonnyMJ" in font_verdicts([("SutonnyMJ", "bs 1")])


def test_english_span_beside_a_bijoy_span_survives_conversion():
    from ingestion.legacy_convert import BijoyConverter, convert_fragment

    if not BijoyConverter().available()[0]:
        pytest.skip("bijoy2unicode not installed")

    legacy = font_verdicts([("SutonnyMJ", BIJOY), ("TimesNewRomanPSMT", ENGLISH)])
    out = []
    for font, text in [("SutonnyMJ", BIJOY), ("TimesNewRomanPSMT", ENGLISH)]:
        out.append(convert_fragment(text)[0] if font in legacy else text)

    assert "\u09a2\u09be\u0995\u09be" in out[0]       # ঢাকা
    assert out[1] == ENGLISH                          # untouched, byte for byte


def test_convert_fragment_returns_the_original_when_upstream_crashes():
    from ingestion.legacy_convert import BijoyConverter, convert_fragment

    if not BijoyConverter().available()[0]:
        pytest.skip("bijoy2unicode not installed")

    text, ok = convert_fragment("fwZ\u00a9")
    if not ok:
        assert text == "fwZ\u00a9"   # unchanged, never half-converted