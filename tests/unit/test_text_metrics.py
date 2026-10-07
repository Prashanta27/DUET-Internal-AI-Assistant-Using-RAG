"""Metric tests, carried over from the M0 self-test.

If the maths here is wrong every number in the M1 report is wrong, and
nothing downstream will say so.
"""

from __future__ import annotations

import unicodedata

from evaluation.text_metrics import cer, normalise, verdict, wer


def test_identical_text_scores_zero():
    assert cer("hello world", "hello world") == 0.0


def test_one_wrong_character_in_six():
    assert abs(cer("abcdef", "abcdXf") - 1 / 6) < 1e-9


def test_one_wrong_word_in_four():
    assert abs(wer("the exam is monday", "the exam is friday") - 0.25) < 1e-9


def test_whitespace_and_case_are_not_errors():
    assert cer("a b", "a      b") == 0.0
    assert cer("Exam Routine", "exam routine") == 0.0
    assert cer("Exam Routine", "exam routine", fold_case=False) > 0


def test_bangla_zero_width_is_ignored():
    """A correct read must not be scored as an error because an engine
    emitted a ZWNJ."""
    bn = "\u09aa\u09b0\u09c0\u0995\u09cd\u09b7\u09be\u09b0 \u09b0\u09c1\u099f\u09bf\u09a8"
    with_zwnj = bn.replace(" ", "  ") + "\u200c"
    assert cer(bn, with_zwnj) == 0.0


def test_a_real_bangla_substitution_is_still_caught():
    bn = "\u09aa\u09b0\u09c0\u0995\u09cd\u09b7\u09be"        # পরীক্ষা
    wrong = "\u09aa\u09b0\u09c0\u00ff\u09be"                  # পরীÿা
    assert cer(bn, wrong) > 0


def test_bangla_and_latin_digits_are_not_folded_together():
    """০৭ is not 07. This is the one fold that would hide the failure the
    project exists to prevent."""
    assert cer("\u09e6\u09ed", "07") > 0


def test_nfc_composition_is_not_an_error():
    assert cer("é", unicodedata.normalize("NFD", "é")) == 0.0


def test_empty_reference_edge_cases():
    assert cer("", "") == 0.0
    assert cer("", "junk") == 1.0


def test_verdict_bands_match_the_m0_report():
    assert "excellent" in verdict(0.02)
    assert "usable" in verdict(0.10)
    assert "marginal" in verdict(0.20)
    assert "unusable" in verdict(0.35)


def test_normalise_folds_danda_variants():
    assert normalise("\u09aa\u09f7") == normalise("\u09aa\u0964")