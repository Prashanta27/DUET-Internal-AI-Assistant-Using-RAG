"""Text normalisation before chunking.

Everything here is a decision about what counts as "the same text". Each one
is stated with its reason, because the wrong call is invisible: it does not
crash, it just quietly makes retrieval worse.

THE ONE RULE THAT IS NOT NEGOTIABLE
    Bangla and Latin digits are never converted into each other. ০৭ and 07
    are different strings. A notice that says ০৭/০৮/২০২৬ is not the same as
    one that says 07/08/2026, and answering questions about exactly those
    numbers is what this system is for.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

# Truly invisible characters with no semantic role. PDF extractors and OCR
# engines emit these inconsistently.
_ALWAYS_STRIP = dict.fromkeys(
    map(ord, "\u200b\ufeff\u00ad\u2060\u180e"), None
)  # ZWSP, BOM, soft hyphen, word joiner, Mongolian vowel separator

# ZWNJ / ZWJ affect how Bangla conjuncts RENDER, not what they mean. PyMuPDF
# and every OCR engine emit them inconsistently for the same visual text, so
# leaving them in means the same sentence embeds to two different vectors
# depending on which extractor saw it.
#
# The cost of stripping: a handful of conjuncts render slightly differently
# when the stored text is shown back to a teacher. The cost of keeping:
# silent retrieval misses. Stripping wins, but it IS a lossy choice -- if a
# teacher ever reports text that looks subtly wrong on screen, this is where
# to look.
_JOINERS = dict.fromkeys(map(ord, "\u200c\u200d"), None)

# Danda variants. U+09F7 (Bengali currency numerator four, routinely
# mis-emitted as a full stop) folds to the real danda U+0964.
#
# NOT included: the ASCII pipe. The M0 bake-off folded "|" to a danda so
# that a cosmetic OCR confusion did not inflate CER by one error per
# sentence. That was a SCORING decision. In stored text a pipe is usually a
# real table delimiter, and rewriting it would corrupt every markdown table.
_PUNCT = {
    "\u09f7": "\u0964",
    "\u0965": "\u0964",   # double danda -> danda
    "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-", "\u2212": "-",
}

# A line-ending hyphen joins a word only when BOTH sides are Latin letters.
# Bangla does not hyphenate across lines, so applying this to Bangla would
# delete a real hyphen from things like memo numbers.
_HYPHEN_BREAK = re.compile(r"(?<=[A-Za-z])-\n(?=[a-z])")

_MULTI_BLANK = re.compile(r"\n{3,}")
_TRAILING_WS = re.compile(r"[ \t]+(?=\n)")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")


def clean_text(text: str, *, strip_joiners: bool = True) -> str:
    """Clean extracted text while preserving line and paragraph structure.

    Structure is preserved on purpose: the chunker splits on headings and
    blank lines, so flattening whitespace here would remove the boundaries
    it needs.
    """
    if not text:
        return ""

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = unicodedata.normalize("NFC", text)
    text = text.translate(_ALWAYS_STRIP)
    if strip_joiners:
        text = text.translate(_JOINERS)
    for src, dst in _PUNCT.items():
        text = text.replace(src, dst)

    text = _HYPHEN_BREAK.sub("", text)
    text = _TRAILING_WS.sub("", text)
    text = _MULTI_SPACE.sub(" ", text)
    text = _MULTI_BLANK.sub("\n\n", text)
    return text.strip()


def normalise_for_hash(text: str) -> str:
    """Aggressive normalisation used ONLY for content_sha256.

    Whitespace is collapsed and case is folded here but not in clean_text,
    because this form never reaches the LLM or the teacher. Its only job is
    to decide "is this chunk the same as one we already embedded?" when a
    corrected version of a document is uploaded. A re-issued notice with one
    changed line should cost one embedding, not forty.
    """
    return " ".join(clean_text(text).lower().split())


def content_sha256(text: str) -> str:
    return hashlib.sha256(normalise_for_hash(text).encode("utf-8")).hexdigest()


def detect_lang(text: str) -> str:
    """'bn' | 'en' | 'mixed' by script ratio. Deliberately crude.

    This labels stored chunks. Query-side language detection is a different
    problem with a third case (Banglish) and lives in
    retrieval/normalize_query.py at M2.
    """
    bengali = sum(1 for c in text if "\u0980" <= c <= "\u09ff")
    latin = sum(1 for c in text if c.isascii() and c.isalpha())
    total = bengali + latin
    if total == 0:
        return "unknown"
    ratio = bengali / total
    if ratio >= 0.85:
        return "bn"
    if ratio <= 0.15:
        return "en"
    return "mixed"