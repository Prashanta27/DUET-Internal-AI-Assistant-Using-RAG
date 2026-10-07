"""Convert legacy ASCII-mapped Bangla back to Unicode.

This wraps the community bijoy2unicode glyph map. Measured behaviour of that
map on real DUET text, before this wrapper:

    XvKv cÖ+KŠkj I cÖhyw3 wek¦we`¨vjq  -> ঢাকা প্রকৌশল ও প্রযুক্তি বিশ্ববিদ্যালয়   correct
    c„ôv bs 15                        -> পৃষ্ঠা নং ১৫                        correct
    cixÿv                             -> পরীÿা      (should be পরীক্ষা)      glyph unmapped
    AvÛviMÖ¨vRy+qU                    -> আন্ডারগর্যাজুয়েট (should be আন্ডারগ্র্যাজুয়েট)  reph misplaced
    fwZ©                              -> IndexError                        crash
    Vector: Scalar and vector...      -> ঠবপঃড়ৎ: ঝপধষধৎ...                  ENGLISH DESTROYED

The last one is the dangerous one. The library maps every ASCII character it
is given; it does not check whether the text was ever Bijoy. Run it over the
admission syllabus and it turns 361 lines of correct English physics
vocabulary into Bengali-looking noise, while fixing the 20 Bangla lines.

So conversion is gated per line by legacy_font.is_legacy_line(). A line that
was not detected as legacy is returned byte-identical -- not "probably
unchanged", identical. There is a test for exactly that.

WHAT IS STILL WRONG AFTER THIS WRAPPER
    Conjunct and reph errors remain. They are a smaller, quieter problem
    than destroyed English, but they are real: পরীÿা will not match a query
    for পরীক্ষা any better than the Bijoy original did. Measure the residual
    error the way M0 measured OCR -- hand-type ground truth for 15-20
    converted lines and compute CER. Do not assume this is solved because
    the output now looks like Bangla.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ingestion.legacy_font import is_legacy_fragment, is_legacy_line

#: Glyphs the upstream map leaves untouched, with the Unicode they stand for.
#:
#: Each entry here is a claim about a font's glyph slot, and a wrong claim
#: produces fluent-looking Bangla that is silently incorrect. So: add an
#: entry only after reading the glyph on the rendered page image and
#: confirming the word it belongs to. The first entry was confirmed from
#: `cixÿv`, which is unambiguously পরীক্ষা.
POST_MAP = {
    "\u00ff": "\u0995\u09cd\u09b7",   # ÿ -> ক্ষ
}

# --- reph reordering -------------------------------------------------------
#
# In Bijoy the reph is the byte `©`, typed AFTER the consonant it belongs to.
# In Unicode it is stored BEFORE that consonant. The upstream converter emits
# it where it found it, so every reph in the output lands one cluster late:
#
#     ভতির্      should be  ভর্তি
#     নিবার্চন    should be  নির্বাচন
#     শিক্ষাথীর্   should be  শিক্ষার্থী
#     কাডর্       should be  কার্ড
#
# A pre-base vowel (ে ৈ ো ৌ) is displaced too, because it visually precedes
# its consonant and Bijoy stores it that way:
#
#     সবের্শষ     should be  সর্বশেষ      (the ে belongs to শ, not ব)
#     আকিের্টকচার  should be  আর্কিটেকচার
#
# DANGER, AND WHY THIS IS NOT A GENERAL-PURPOSE FIXER
#     `কার্ড` is already correct, and it has exactly the shape this rule
#     rewrites -- applied to it the rule would produce `র্কাড`. The rule is
#     only sound on text that just came out of the Bijoy glyph map, where
#     the reph is known to be systematically late. It must never be run over
#     Unicode Bangla of unknown origin, which is why it lives inside
#     convert_line() and is not exported as a utility.
#
# Verify it the way M0 verified OCR: measure CER against hand-typed truth
# with the rule on and off. `scripts/legacy_bakeoff.py --no-reph-fix` exists
# for exactly that comparison.

_C = "\u0995-\u09b9\u09ce\u09dc-\u09df"          # consonants
_POST = "\u09be-\u09c4"                            # া ি ী ু ূ ৃ ৄ
_PRE = "\u09c7\u09c8\u09cb\u09cc"                # ে ৈ ো ৌ
_REPH = "\u09b0\u09cd"                             # র্

_CLUSTER = f"((?:[{_C}]\u09cd)*[{_C}])"

# One pattern, one left-to-right pass. Two separate passes do not work: the
# first pass produces a correctly-placed reph, and the second pass then sees
# `সর্বশেষ`, matches it again and yields `র্সবশেষ`. re.sub never rescans what
# it has already replaced, so a single alternation is the fix.
_REPH_RE = re.compile(
    f"{_CLUSTER}([{_POST}]*)([{_PRE}]?){_REPH}(?:{_CLUSTER})?"
)


def _reorder(m: re.Match) -> str:
    cluster, post, pre, following = m.group(1), m.group(2), m.group(3), m.group(4)
    following = following or ""
    # The pre-base vowel belongs to the consonant AFTER the reph, not before
    # it: সবের্শষ is স + ব + ে + র্ + শ, and the ে is শ's.
    return f"\u09b0\u09cd{cluster}{post}{following}{pre}"


def fix_reph(text: str) -> str:
    """Move a late reph in front of the consonant it belongs to.

    Only sound on freshly converted Bijoy output -- see the note above.
    """
    return _REPH_RE.sub(_reorder, text)


@dataclass
class ConversionReport:
    text: str
    lines_total: int = 0
    lines_legacy: int = 0
    lines_converted: int = 0
    lines_failed: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.lines_failed == 0


class BijoyConverter:
    """Adapter over bijoy2unicode. The only module that imports it."""

    name = "bijoy2unicode"

    def __init__(self) -> None:
        self._impl = None

    def available(self) -> tuple[bool, str]:
        try:
            from bijoy2unicode import converter  # noqa: F401
        except ImportError as e:
            return False, f"pip install bijoy2unicode ({e})"
        return True, "bijoy2unicode glyph map"

    def _ensure(self):
        if self._impl is None:
            from bijoy2unicode.converter import Unicode

            self._impl = Unicode()
        return self._impl

    def convert_line(self, line: str, *, reph_fix: bool = True) -> str:
        """Convert one line known to be legacy. Never raises.

        The upstream converter raises IndexError on some short inputs (`fwZ©`
        is one). A crash mid-ingest over a two-word heading is not worth a
        failed job, so a failure returns the original line and is counted.
        """
        impl = self._ensure()
        out = impl.convertBijoyToUnicode(line)
        for src, dst in POST_MAP.items():
            out = out.replace(src, dst)
        return fix_reph(out) if reph_fix else out


def convert_fragment(text: str, converter: BijoyConverter | None = None,
                     *, reph_fix: bool = True) -> tuple[str, bool]:
    """Convert text already known to be legacy -- by font, not by guesswork.

    Returns (result, ok). On failure the ORIGINAL text comes back unchanged
    rather than half-converted, because a partially mapped string looks
    plausible and would pass a casual glance.
    """
    conv = converter or BijoyConverter()
    if not text.strip():
        return text, True
    try:
        return conv.convert_line(text, reph_fix=reph_fix), True
    except Exception:  # noqa: BLE001 -- upstream raises IndexError on some inputs
        return text, False


def convert_text(text: str, converter: BijoyConverter | None = None) -> ConversionReport:
    """Convert only the legacy lines in `text`, leaving every other line alone."""
    conv = converter or BijoyConverter()
    ok, detail = conv.available()
    if not ok:
        raise RuntimeError(f"legacy converter unavailable: {detail}")

    lines = text.splitlines()
    # Establish the document's character first. Short lines are only
    # converted inside a document that already has a confidently-legacy
    # line, so a stray "Ref." in an English memo is never touched.
    confident = any(is_legacy_line(ln) for ln in lines)

    out_lines: list[str] = []
    report = ConversionReport(text="")
    for line in lines:
        report.lines_total += 1
        gated = is_legacy_line(line) or (confident and is_legacy_fragment(line))
        if not gated:
            out_lines.append(line)
            continue
        report.lines_legacy += 1
        try:
            converted = conv.convert_line(line)
        except Exception as e:  # noqa: BLE001 -- upstream raises IndexError
            report.lines_failed += 1
            if len(report.failures) < 20:
                report.failures.append(f"{type(e).__name__}: {line[:60]}")
            out_lines.append(line)
            continue
        report.lines_converted += 1
        out_lines.append(converted)

    report.text = "\n".join(out_lines)
    return report