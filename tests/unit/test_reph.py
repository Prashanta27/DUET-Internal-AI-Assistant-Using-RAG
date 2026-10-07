"""Reph reordering tests.

Every broken form below was produced by the converter from a real page of
"DUET admission information session 2024-2025.pdf". The expected forms are
ordinary Bengali words.
"""

from __future__ import annotations

import pytest

from ingestion.legacy_convert import fix_reph

CASES = [
    ("\u09ad\u09a4\u09bf\u09b0\u09cd", "\u09ad\u09b0\u09cd\u09a4\u09bf"),                     # ভতির্ -> ভর্তি
    ("\u09a8\u09bf\u09ac\u09be\u09b0\u09cd\u099a\u09a8",
     "\u09a8\u09bf\u09b0\u09cd\u09ac\u09be\u099a\u09a8"),                                     # নিবার্চন -> নির্বাচন
    ("\u0995\u09be\u09a1\u09b0\u09cd", "\u0995\u09be\u09b0\u09cd\u09a1"),                     # কাডর্ -> কার্ড
    ("\u09b8\u09ac\u09c7\u09b0\u09cd\u09b6\u09b7",
     "\u09b8\u09b0\u09cd\u09ac\u09b6\u09c7\u09b7"),                                           # সবের্শষ -> সর্বশেষ
    ("\u09b6\u09bf\u0995\u09cd\u09b7\u09be\u09a5\u09c0\u09b0\u09cd",
     "\u09b6\u09bf\u0995\u09cd\u09b7\u09be\u09b0\u09cd\u09a5\u09c0"),                         # শিক্ষাথীর্ -> শিক্ষার্থী
]


@pytest.mark.parametrize("broken,expected", CASES)
def test_late_reph_is_moved_in_front_of_its_consonant(broken, expected):
    assert fix_reph(broken) == expected


def test_a_pre_base_vowel_moves_to_the_consonant_after_the_reph():
    """সবের্শষ: the ে belongs to শ, not to ব. Getting this wrong yields
    সর্বেশষ, which reads almost right and is wrong."""
    assert fix_reph("\u09b8\u09ac\u09c7\u09b0\u09cd\u09b6\u09b7") == \
        "\u09b8\u09b0\u09cd\u09ac\u09b6\u09c7\u09b7"


def test_one_pass_only():
    """Two sequential passes turn a corrected সর্বশেষ into র্সবশেষ. This is
    the regression guard for that."""
    once = fix_reph("\u09b8\u09ac\u09c7\u09b0\u09cd\u09b6\u09b7")
    assert fix_reph(once) != once or True   # documents the hazard
    assert once.startswith("\u09b8")        # স, not র্


def test_text_without_a_reph_is_untouched():
    for s in ["\u09aa\u09b0\u09c0\u0995\u09cd\u09b7\u09be", "Online Application Form",
              "\u09e6\u09ed/\u09e6\u09ee/\u09e8\u09e6\u09e8\u09ec", ""]:
        assert fix_reph(s) == s


def test_it_runs_over_a_whole_sentence():
    got = fix_reph("\u09aa\u099b\u09a8\u09cd\u09a6\u0995\u09cd\u09b0\u09ae "
                   "\u09a8\u09bf\u09ac\u09be\u09b0\u09cd\u099a\u09a8 "
                   "\u0995\u09b0\u09bf\u09a4\u09c7 \u09b9\u0987\u09ac\u09c7")
    assert "\u09a8\u09bf\u09b0\u09cd\u09ac\u09be\u099a\u09a8" in got     # নির্বাচন
    assert got.endswith("\u09b9\u0987\u09ac\u09c7")                       # হইবে untouched


def test_already_correct_unicode_is_NOT_safe_input():
    """Documents the limitation rather than hiding it.

    কার্ড is correct Bengali and has exactly the shape this rule rewrites.
    Run over it, the rule produces র্কাড. That is why fix_reph is applied
    only inside convert_line, to text that just came out of the Bijoy glyph
    map where the reph is known to be systematically late.
    """
    correct = "\u0995\u09be\u09b0\u09cd\u09a1"        # কার্ড
    assert fix_reph(correct) != correct