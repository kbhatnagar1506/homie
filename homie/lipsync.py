"""Words → timed mouth shapes, from the CMU Pronouncing Dictionary (126k words, ARPAbet phonemes).

Phonemes map to the 15 standard visemes (the Oculus/ARKit set used by game and film lip sync).
Each viseme's share of a word's duration is weighted: stressed vowels hold longest, plosives
snap shut. Words not in the dictionary fall back to letter rules.
"""

import re
from functools import lru_cache

import cmudict

PHONEME_TO_VISEME = {
    "AA": "aa", "AE": "aa", "AH": "aa", "AO": "O", "AW": "aa", "AY": "aa", "EH": "E", "ER": "RR", "EY": "E",
    "IH": "I", "IY": "I", "OW": "O", "OY": "O", "UH": "U", "UW": "U",
    "B": "PP", "P": "PP", "M": "PP", "F": "FF", "V": "FF", "TH": "TH", "DH": "TH", "T": "DD", "D": "DD",
    "K": "kk", "G": "kk", "NG": "kk", "CH": "CH", "JH": "CH", "SH": "CH", "ZH": "CH", "S": "SS", "Z": "SS",
    "N": "nn", "L": "nn", "R": "RR", "W": "U", "Y": "I", "HH": "aa",
}
# Diphthongs glide into a second shape.
GLIDE = {"AW": "U", "AY": "I", "OY": "I", "EY": "I", "OW": "U"}
LETTERS = {"a": "aa", "e": "E", "i": "I", "o": "O", "u": "U", "y": "I", "b": "PP", "p": "PP", "m": "PP", "f": "FF",
           "v": "FF", "t": "DD", "d": "DD", "k": "kk", "g": "kk", "c": "kk", "q": "kk", "j": "CH", "s": "SS", "z": "SS",
           "x": "SS", "n": "nn", "l": "nn", "r": "RR", "w": "U", "h": "aa"}
_DICT = cmudict.dict()


@lru_cache(maxsize=20000)
def word_visemes(word: str) -> tuple[tuple[str, float], ...]:
    """(viseme, relative weight) for one word."""
    w = re.sub(r"[^a-z']", "", word.lower())
    if not w:
        return ()
    prons = _DICT.get(w)
    out: list[tuple[str, float]] = []
    if prons:
        for ph in prons[0]:
            base, stress = re.sub(r"\d", "", ph), (ph[-1] if ph[-1].isdigit() else "")
            vis = PHONEME_TO_VISEME.get(base, "nn")
            if stress:  # vowel
                weight = 1.6 if stress == "1" else 1.15 if stress == "2" else 0.8
                if base in GLIDE:
                    out += [(vis, weight * 0.6), (GLIDE[base], weight * 0.4)]
                    continue
            else:
                weight = 0.55 if vis in ("PP", "DD", "kk") else 0.75
            out.append((vis, weight))
    else:
        for ch in w:
            vis = LETTERS.get(ch)
            if vis and (not out or out[-1][0] != vis):
                out.append((vis, 1.2 if ch in "aeiou" else 0.7))
    return tuple(out)


def timeline(word: str, duration_ms: float) -> list[dict]:
    """Viseme keyframes for a word spoken over duration_ms."""
    shapes = word_visemes(word)
    total = sum(w for _, w in shapes) or 1
    return [{"v": v, "ms": max(35, duration_ms * w / total)} for v, w in shapes]


def estimate_ms(word: str) -> float:
    """Rough spoken length when the TTS gives no end time (about 75 ms per phoneme-weight unit)."""
    return max(140, 78 * sum(w for _, w in word_visemes(word)))
