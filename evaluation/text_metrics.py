"""Character and word error rate for Bangla/English text.

Promoted out of m0_reality_check/ocr_bakeoff.py so that OCR accuracy and
legacy-font conversion accuracy are scored by the same code. Two error rates
computed by two different implementations are not comparable, and comparing
them is the whole point.

NORMALISATION IS SCORING-ONLY
    This module folds things that clean.py deliberately does not -- the
    ASCII pipe to a danda, case, whitespace. That is correct here and wrong
    there: a cosmetic danda confusion would otherwise add one error per
    sentence and tell you nothing about whether the text is usable, while in
    stored text a pipe is usually a real table delimiter.

BANGLA AND LATIN DIGITS ARE NEVER FOLDED TOGETHER
    ০৭ is not 07. A conversion that turns one into the other has made an
    error and must be scored as one.
"""

from __future__ import annotations

import unicodedata

_INVISIBLES = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff\u00ad\u2060"), None)

_PUNCT = {
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-", "\u2212": "-",
    "|": "\u0964", "\u09f7": "\u0964", "\u0965": "\u0964",
}


def normalise(text: str, fold_case: bool = True) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    text = text.translate(_INVISIBLES)
    for src, dst in _PUNCT.items():
        text = text.replace(src, dst)
    if fold_case:
        text = text.lower()
    return " ".join(text.split())


def _levenshtein(a, b) -> int:
    try:
        from rapidfuzz.distance import Levenshtein

        if isinstance(a, str) and isinstance(b, str):
            return Levenshtein.distance(a, b)
    except ImportError:
        pass
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(reference: str, hypothesis: str, fold_case: bool = True) -> float:
    ref = normalise(reference, fold_case)
    hyp = normalise(hypothesis, fold_case)
    if not ref:
        return 0.0 if not hyp else 1.0
    return _levenshtein(ref, hyp) / len(ref)


def wer(reference: str, hypothesis: str, fold_case: bool = True) -> float:
    ref = normalise(reference, fold_case).split()
    hyp = normalise(hypothesis, fold_case).split()
    if not ref:
        return 0.0 if not hyp else 1.0
    return _levenshtein(ref, hyp) / len(ref)


def verdict(mean_cer: float) -> str:
    """The same bands the M0 report used, so the two numbers mean the same."""
    if mean_cer < 0.05:
        return "excellent -- usable as-is"
    if mean_cer < 0.15:
        return "usable, expect retrieval misses on rare terms"
    if mean_cer < 0.30:
        return "marginal -- retrieval will suffer badly"
    return "unusable -- no better than the OCR path"