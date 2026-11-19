"""Seeded noisy-request variants (spec 03 s7.8). Sources stay clean; only the
request changes. The generator can be shared with the audio spec.

Kinds:
    typos            one edit per 8 words: swap, drop, double or keyboard-neighbour
    casual           lower case, no punctuation, common shortenings
    voice            fillers, run-on clauses, numbers spoken out, homophones
    self_correction  "in March, no wait, in February": the corrected value is the task's
                     own target, so the gold is unchanged and the case is scored for
                     correctness, not consistency
"""
from __future__ import annotations

import random
import re

NOISE_VERSION = "edat-noise/1"
NOISE_KINDS = ("typos", "casual", "voice", "self_correction")

_NEIGHBOURS = {
    "a": "qs", "b": "vn", "c": "xv", "d": "sf", "e": "wr", "f": "dg", "g": "fh", "h": "gj", "i": "uo",
    "j": "hk", "k": "jl", "l": "k", "m": "n", "n": "bm", "o": "ip", "p": "o", "q": "w", "r": "et",
    "s": "ad", "t": "ry", "u": "yi", "v": "cb", "w": "qe", "x": "zc", "y": "tu", "z": "x",
}
_SHORT = {"tomorrow": "tmrw", "please": "pls", "you": "u", "are": "r", "because": "bc", "before": "b4",
          "tonight": "tonite", "message": "msg", "thanks": "thx", "what's": "whats", "about": "abt",
          "with": "w", "people": "ppl", "really": "rly"}
_HOMOPHONES = {"their": "there", "there": "their", "to": "too", "your": "you're", "its": "it's", "for": "four",
               "know": "no", "right": "write", "here": "hear"}
_SPOKEN = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six", "7": "seven",
           "8": "eight", "9": "nine", "10": "ten", "11": "eleven", "12": "twelve", "15": "fifteen", "20": "twenty",
           "30": "thirty", "45": "forty-five"}
_FILLERS = ["um", "like", "uh", "you know", "so"]

# A protected token is never edited: codes, numbers, times and names the answer depends on.
_PROTECT = re.compile(r"^(?:[A-Z]{1,6}-\d+|\d[\d:.,]*|[A-Z][a-z]+|[A-Z]{2,})$")   # all caps: EUR, GP, PIN


def _typo(word: str, rng: random.Random) -> str:
    """One visible edit. An edit that would leave the word unchanged (swapping two equal
    letters, a neighbour with no entry) is drawn again, so every chosen word changes."""
    if len(word) < 3 or not word.isalpha():
        return word
    for _ in range(12):
        out = _typo_once(word, rng)
        if out != word:
            return out
    return word[:1] + word[2:] if len(word) > 3 else word + word[-1]


def _typo_once(word: str, rng: random.Random) -> str:
    i = rng.randrange(1, len(word) - 1)
    op = rng.choice(["swap", "drop", "double", "neighbour"])
    if op == "swap":
        return word[:i] + word[i + 1] + word[i] + word[i + 2:]
    if op == "drop":
        return word[:i] + word[i + 1:]
    if op == "double":
        return word[:i] + word[i] + word[i:]
    ch = word[i].lower()
    return word[:i] + rng.choice(_NEIGHBOURS.get(ch, ch)) + word[i + 1:]


def typos(text: str, rng: random.Random) -> str:
    words = text.split(" ")
    editable = [i for i, w in enumerate(words)
                if not _PROTECT.match(w.strip("?,.!")) and len(w.strip("?,.!")) >= 3 and w.rstrip("?,.!").isalpha()]
    k = max(1, len(words) // 8)
    for i in sorted(rng.sample(editable, min(k, len(editable)))):
        core = words[i].rstrip("?,.!")
        tail = words[i][len(core):]
        words[i] = _typo(core, rng) + tail
    return " ".join(words)


def casual(text: str, rng: random.Random) -> str:
    out = []
    for w in text.split(" "):
        core = w.strip("?,.!'\"")
        if re.fullmatch(r"[A-Z]{1,6}-\d+", core):     # keep codes exact
            out.append(core)
            continue
        low = core.lower()
        out.append(_SHORT.get(low, low))
    return " ".join(x for x in out if x)


def voice(text: str, rng: random.Random) -> str:
    words = text.rstrip("?.!").split(" ")
    out = []
    for i, w in enumerate(words):
        core = w.strip(",")
        if core.lower() in _HOMOPHONES and rng.random() < 0.5:
            core = _HOMOPHONES[core.lower()]
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", core)
        if m:
            h, mi = m.group(1).lstrip("0") or "0", m.group(2)
            core = _SPOKEN.get(h, h) + ("" if mi == "00" else " " + _SPOKEN.get(mi, mi))
        elif core in _SPOKEN:
            core = _SPOKEN[core]
        out.append(core)
        if i in (0, len(words) // 2) and rng.random() < 0.8:
            out.append(rng.choice(_FILLERS))
    return " ".join(out) + " and yeah that's it"


_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"]


def self_correction(text: str, rng: random.Random, *, alternatives: list[str] | None = None) -> str | None:
    """Insert a wrong value that the speaker then corrects to the original one.

    Returns None when the request has nothing correctable (a month, a weekday,
    a city or a named alternative).
    """
    for m in re.finditer(r"\b(" + "|".join(_MONTHS) + r")\b", text):
        wrong = rng.choice([x for x in _MONTHS if x != m.group(1)])
        return text[:m.start()] + f"{wrong}, no wait, {m.group(1)}" + text[m.end():]
    for alt in alternatives or []:
        m = re.search(r"\b" + re.escape(alt) + r"\b", text)
        if m:
            others = [x for x in (alternatives or []) if x != alt]
            if others:
                return text[:m.start()] + f"{rng.choice(others)}, no wait, {alt}" + text[m.end():]
    m = re.search(r"\bto ([A-Z][a-z]+)\b", text)
    if m:
        from edat.quality.world import CITIES
        wrong = rng.choice([c for c in CITIES if c != m.group(1)])
        return text[:m.start(1)] + f"{wrong}, no wait, to {m.group(1)}" + text[m.end(1):]
    return None


def make_noisy(text: str, kind: str, seed: int, **kw) -> str | None:
    rng = random.Random(seed)
    if kind == "typos":
        return typos(text, rng)
    if kind == "casual":
        return casual(text, rng)
    if kind == "voice":
        return voice(text, rng)
    if kind == "self_correction":
        return self_correction(text, rng, **kw)
    raise ValueError(kind)
