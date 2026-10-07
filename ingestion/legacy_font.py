"""Detect legacy ASCII-mapped Bangla (Bijoy / SutonnyMJ and relatives).

THE FAILURE THIS CATCHES
    Some PDFs store Bangla as ASCII bytes and rely on the font's glyph map
    to draw Bengali. On screen it reads as Bangla; in the extracted text it
    is Latin:

        AvÛviMÖ¨vRy‡qU †cÖvMÖvg-Gi fwZ© cixÿv welqK Z_¨vejx
        = আন্ডারগ্র্যাজুয়েট প্রোগ্রাম-এর ভর্তি পরীক্ষা বিষয়ক তথ্যাবলী

    The classifier calls such a document DIGITAL, correctly -- the text
    layer is real and extraction is exact. But nothing in it is Bengali, so
    bge-m3 embeds `AvÛviMÖ¨vRy‡qU` as an unfamiliar Latin token and a query
    for আন্ডারগ্র্যাজুয়েট can never match it. Retrieval runs, the LLM answers,
    the citation is correct, and Bangla questions simply never find this
    document.

    Like the OCR digit substitution in §7.10, nothing downstream can see the
    problem. Which is why it gets measured here, before ingestion, rather
    than guessed at after Recall@5 comes back bad.

WHAT THIS MODULE DOES NOT DO
    It does not convert. Bijoy-family fonts do not all share one glyph map,
    and converting with the wrong map produces fluent-looking Bangla that is
    subtly wrong -- the worst possible outcome, because it passes every
    check a human would casually apply. Detection is deterministic and
    testable; conversion needs a chosen library and a measured spot-check.
"""

from __future__ import annotations

from dataclasses import dataclass

# Characters that appear constantly in Bijoy-mapped text and essentially
# never in ordinary English. Mostly Windows-1252 typographic and accented
# codepoints being used as Bengali glyph slots.
_LEGACY_MARKERS = set(
    "\u2020\u2021\u201e\u2030\u2039\u203a\u0192\u02c6\u0161\u0153"
    "\u00a1\u00a2\u00a3\u00a4\u00a5\u00a6\u00a8\u00a9\u00ab\u00ac\u00ae"
    "\u00af\u00b0\u00b1\u00b4\u00b5\u00b6\u00b8\u00bb\u00bc\u00bd\u00be"
    "\u00bf\u00c0\u00c1\u00c2\u00c3\u00c4\u00c5\u00c6\u00c7\u00c8\u00c9"
    "\u00ca\u00cb\u00cc\u00cd\u00ce\u00cf\u00d0\u00d1\u00d2\u00d3\u00d4"
    "\u00d5\u00d6\u00d7\u00d8\u00d9\u00da\u00db\u00dc\u00dd\u00de\u00df"
    "\u00e0\u00e1\u00e2\u00e3\u00e4\u00e5\u00e6\u00e7\u00e8\u00e9\u00ea"
    "\u00eb\u00ec\u00ed\u00ee\u00ef\u00f0\u00f1\u00f2\u00f3\u00f4\u00f5"
    "\u00f6\u00f7\u00f8\u00f9\u00fa\u00fb\u00fc\u00fd\u00fe\u00ff"
)

# Above this share of marker characters, ordinary English prose does not go.
# Real Bijoy text sits around 0.10-0.20. The gap is wide, so the exact
# threshold is not load-bearing -- but report the ratio, not just the verdict.
LEGACY_THRESHOLD = 0.03


#: A line shorter than this is too small to judge reliably.
MIN_LINE_CHARS = 6


@dataclass
class EncodingReport:
    marker_ratio: float
    bengali_chars: int
    total_chars: int
    legacy_lines: int = 0
    judged_lines: int = 0
    sample: str = ""

    @property
    def legacy_line_ratio(self) -> float:
        return round(self.legacy_lines / self.judged_lines, 4) if self.judged_lines else 0.0

    @property
    def is_legacy(self) -> bool:
        """Any legacy line at all means the document needs conversion.

        Not a ratio test. A ten-page English syllabus with a Bangla header
        band on every page averages out to almost nothing -- the first
        version of this module scored exactly that document at 0.0049 and
        called it LATIN_ONLY, while its own printed sample line was visibly
        Bijoy. Averages hide the minority language, which in this corpus is
        the language the project exists to serve.
        """
        return self.legacy_lines > 0

    @property
    def verdict(self) -> str:
        if self.legacy_lines and self.bengali_chars:
            return "MIXED"          # some Unicode Bangla, some legacy
        if self.legacy_line_ratio >= 0.5:
            return "LEGACY"
        if self.legacy_lines:
            return "PARTIAL_LEGACY"  # e.g. a Bijoy header on an English page
        if self.bengali_chars:
            return "UNICODE_BN"
        return "LATIN_ONLY"


def _marker_ratio(s: str) -> tuple[float, int, int]:
    stripped = "".join(s.split())
    total = len(stripped)
    if total == 0:
        return 0.0, 0, 0
    bengali = sum(1 for c in stripped if "\u0980" <= c <= "\u09ff")
    markers = sum(1 for c in stripped if c in _LEGACY_MARKERS)
    return markers / total, bengali, total


def is_legacy_line(line: str) -> bool:
    ratio, bengali, total = _marker_ratio(line)
    if total < MIN_LINE_CHARS or bengali:
        return False
    return ratio >= LEGACY_THRESHOLD


#: Substrings that identify legacy Bangla font families by name. Used as a
#: hint only -- the decisive test is the marker ratio of the text a font
#: actually draws, measured per document in font_verdicts(). A name-only
#: whitelist would miss the next font nobody told us about.
LEGACY_FONT_HINTS = ("sutonny", "bijoy", "boishakhi", "chandrabati", "modhumati")


def font_name_looks_legacy(font: str) -> bool:
    low = font.lower()
    return any(h in low for h in LEGACY_FONT_HINTS)


def font_verdicts(spans: list[tuple[str, str]]) -> set[str]:
    """Given (font_name, text) pairs from a whole document, return the fonts
    whose text is legacy-encoded.

    Deciding per font per document, rather than per line, is what lets
    `সকল Check Box টিক চিহ্ন` survive: the Bangla is drawn in SutonnyMJ and
    the English in TimesNewRomanPSMT, so the two get different treatment
    even though they share a line. Line-level gating had to pick one answer
    for the whole line and was therefore wrong either way.
    """
    totals: dict[str, list[int]] = {}
    for font, text in spans:
        _, bengali, total = _marker_ratio(text)
        markers = sum(1 for c in "".join(text.split()) if c in _LEGACY_MARKERS)
        acc = totals.setdefault(font, [0, 0, 0])
        acc[0] += markers
        acc[1] += total
        acc[2] += bengali

    legacy = set()
    for font, (markers, total, bengali) in totals.items():
        if total == 0 or bengali:
            continue
        if markers / total >= LEGACY_THRESHOLD or font_name_looks_legacy(font):
            legacy.add(font)
    return legacy


def is_legacy_fragment(line: str) -> bool:
    """A relaxed test for lines too short to judge on their own.

    Only meaningful once the surrounding document is already known to
    contain legacy text. A three-word Bangla heading like `fwZ\u00a9` carries
    the marker but not enough characters for the ratio test, so on its own
    it must not be converted -- in an English document it could be a
    reference code. In a document that is already established as Bijoy, it
    is a heading, and leaving it in ASCII means the section titles stay
    unsearchable while the body text becomes searchable.
    """
    ratio, bengali, total = _marker_ratio(line)
    return total > 0 and bengali == 0 and ratio > 0


def analyse_encoding(text: str, sample_len: int = 120) -> EncodingReport:
    """Classify text by counting LEGACY LINES, not by averaging markers."""
    ratio, bengali, total = _marker_ratio(text)
    if total == 0:
        return EncodingReport(0.0, 0, 0)

    judged = 0
    legacy = 0
    first_legacy = ""
    for line in text.splitlines():
        if len("".join(line.split())) < MIN_LINE_CHARS:
            continue
        judged += 1
        if is_legacy_line(line):
            legacy += 1
            if not first_legacy:
                first_legacy = line.strip()

    sample = first_legacy or next(
        (ln.strip() for ln in text.splitlines() if ln.strip()), ""
    )
    return EncodingReport(round(ratio, 4), bengali, total, legacy, judged,
                          sample[:sample_len])


def analyse_pages(texts: list[str]) -> tuple[EncodingReport, list[EncodingReport]]:
    """Whole-document report plus one per page."""
    per_page = [analyse_encoding(t) for t in texts]
    return analyse_encoding("\n".join(texts)), per_page