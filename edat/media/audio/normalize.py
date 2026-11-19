"""English transcript normalisation for WER and CER (spec 05 §3.2).

The rules follow Whisper's ``EnglishTextNormalizer`` (MIT), which the Open ASR
Leaderboard's pipeline closely follows: lower-case; drop text in brackets and
parentheses; drop the fillers hmm, mm, mhm, mmm, uh, um; expand contractions
and titles; drop symbols and diacritics but keep numeric symbols; spelled-out
numbers to digits; British to American spelling; collapse spaces.

This is EDAT's own implementation, identified by :data:`NORMALIZER_ID`. It is
checked against hand-made pairs (``tests/test_edat_media_audio_wer.py``), not
byte-for-byte against Whisper's code: the number rules below are a documented
subset, and the spelling map is a built-in subset unless the full Whisper map
is supplied (``spelling_map=``; its sha256 then becomes part of the id). Raw
and normalised text are both stored with every result so a result can be
re-scored when the normaliser changes.

Number rules (both reference and hypothesis go through them, so a model that
writes "4:30" and a reference that says "four thirty" meet at "4 30"):

* a run of number words becomes one number: "twenty five" -> 25,
  "one hundred and five" -> 105, "two thousand twenty seven" -> 2027;
* two two-digit groups read as a year: "nineteen eighty four" -> 1984,
  "twenty twenty seven" -> 2027; a single digit followed by a two-digit group
  stays two numbers: "four thirty" -> "4 30";
* ordinals: "twenty third" -> 23rd, "first" -> 1st;
* "point": "two point five" -> 2.5;
* "percent" / "per cent" after a number -> "5%";
* "N dollars [and M cents]" -> "$N[.MM]"; "N cents" -> "¢N";
* digits written with a clock colon or a comma are split or joined as Whisper
  does: "4:30" -> "4 30" (the colon is a symbol), "1,000" -> "1000".
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path

NORMALIZER_BASE = "edat-en-norm/1"

FILLERS = r"\b(hmm|mm|mhm|mmm|uh|um)\b"

REPLACERS: tuple[tuple[str, str], ...] = (
    (r"\bwon't\b", "will not"), (r"\bcan't\b", "can not"), (r"\blet's\b", "let us"),
    (r"\bain't\b", "aint"), (r"\by'all\b", "you all"), (r"\bwanna\b", "want to"),
    (r"\bgotta\b", "got to"), (r"\bgonna\b", "going to"), (r"\bi'ma\b", "i am going to"),
    (r"\bimma\b", "i am going to"), (r"\bwoulda\b", "would have"),
    (r"\bcoulda\b", "could have"), (r"\bshoulda\b", "should have"), (r"\bma'am\b", "madam"),
    (r"\bmr\b", "mister "), (r"\bmrs\b", "missus "), (r"\bst\b", "saint "),
    (r"\bdr\b", "doctor "), (r"\bprof\b", "professor "), (r"\bcapt\b", "captain "),
    (r"\bgov\b", "governor "), (r"\bald\b", "alderman "), (r"\bgen\b", "general "),
    (r"\bsen\b", "senator "), (r"\brep\b", "representative "), (r"\bpres\b", "president "),
    (r"\brev\b", "reverend "), (r"\bhon\b", "honorable "), (r"\basst\b", "assistant "),
    (r"\bassoc\b", "associate "), (r"\blt\b", "lieutenant "), (r"\bcol\b", "colonel "),
    (r"\bjr\b", "junior "), (r"\bsr\b", "senior "), (r"\besq\b", "esquire "),
    (r"'d been\b", " had been"), (r"'s been\b", " has been"), (r"'d gone\b", " had gone"),
    (r"'s gone\b", " has gone"), (r"'d done\b", " had done"), (r"'s got\b", " has got"),
    (r"n't\b", " not"), (r"'re\b", " are"), (r"'s\b", " is"), (r"'d\b", " would"),
    (r"'ll\b", " will"), (r"'t\b", " not"), (r"'ve\b", " have"), (r"'m\b", " am"),
)

# A built-in subset of British -> American spellings. The full map is Whisper's
# english.json (MIT), fetched by edat.media.datasets (manifest whisper-english-spelling).
SPELLINGS_BUILTIN: dict[str, str] = {
    "colour": "color", "colours": "colors", "coloured": "colored", "favourite": "favorite",
    "favourites": "favorites", "centre": "center", "centres": "centers", "theatre": "theater",
    "metre": "meter", "metres": "meters", "kilometre": "kilometer", "kilometres": "kilometers",
    "litre": "liter", "litres": "liters", "programme": "program", "programmes": "programs",
    "organise": "organize", "organised": "organized", "organising": "organizing",
    "realise": "realize", "realised": "realized", "recognise": "recognize",
    "recognised": "recognized", "apologise": "apologize", "analyse": "analyze",
    "analysed": "analyzed", "travelled": "traveled", "travelling": "traveling",
    "traveller": "traveler", "cancelled": "canceled", "cancelling": "canceling",
    "labelled": "labeled", "modelling": "modeling", "grey": "gray", "licence": "license",
    "defence": "defense", "offence": "offense", "catalogue": "catalog", "dialogue": "dialog",
    "behaviour": "behavior", "neighbour": "neighbor", "neighbours": "neighbors",
    "honour": "honor", "humour": "humor", "labour": "labor", "flavour": "flavor",
    "harbour": "harbor", "rumour": "rumor", "tyre": "tire", "tyres": "tires",
    "jewellery": "jewelry", "aluminium": "aluminum", "mum": "mom", "pyjamas": "pajamas",
    "cheque": "check", "practise": "practice", "enrol": "enroll", "fulfil": "fulfill",
}

_ONES = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
         "eight": 8, "nine": 9}
_TEENS = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
          "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
         "eighty": 80, "ninety": 90}
_SCALES = {"hundred": 100, "thousand": 1000, "million": 10**6, "billion": 10**9}
_ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
        "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13,
        "fourteenth": 14, "fifteenth": 15, "sixteenth": 16, "seventeenth": 17,
        "eighteenth": 18, "nineteenth": 19, "twentieth": 20, "thirtieth": 30, "fortieth": 40,
        "fiftieth": 50, "sixtieth": 60, "seventieth": 70, "eightieth": 80, "ninetieth": 90,
        "hundredth": 100, "thousandth": 1000}
_NUMBER_WORDS = set(_ONES) | set(_TEENS) | set(_TENS) | set(_SCALES)
_KEEP = set(".%$¢€£")


def _suffix(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def remove_symbols_and_diacritics(s: str, keep: str = "") -> str:
    """NFKD; drop combining marks; replace other marks, symbols and punctuation by a space."""
    out = []
    for ch in unicodedata.normalize("NFKD", s):
        if ch in keep:
            out.append(ch)
            continue
        cat = unicodedata.category(ch)
        if cat == "Mn":
            continue
        out.append(" " if cat[0] in "MSP" else ch)
    return "".join(out)


class _Group:
    """Accumulates one spoken number: total of completed scale parts plus the current part."""

    def __init__(self) -> None:
        self.total = 0
        self.current = 0
        self.words = 0
        self.last = ""          # "ones", "teens", "tens", "scale"

    def value(self) -> int:
        return self.total + self.current


def _parse_number_words(words: list[str]) -> list[str]:
    """Turn runs of number words into digits, following the rules in the module doc."""
    out: list[str] = []
    i = 0
    n = len(words)
    while i < n:
        w = words[i]
        if w not in _NUMBER_WORDS and w not in _ORD:
            out.append(w)
            i += 1
            continue
        # Collect one run of number words (allowing "and" after a scale word).
        run: list[str] = []
        j = i
        while j < n:
            t = words[j]
            if t in _NUMBER_WORDS:
                run.append(t)
                j += 1
                continue
            if t == "and" and run and run[-1] in _SCALES and j + 1 < n and \
                    (words[j + 1] in _NUMBER_WORDS or words[j + 1] in _ORD):
                j += 1
                continue
            if t in _ORD:
                run.append(t)
                j += 1
            break
        i = j
        out.extend(_run_to_tokens(run))
    return out


def _run_to_tokens(run: list[str]) -> list[str]:
    """Split a run into numbers: a new number starts where English would not continue one."""
    tokens: list[str] = []
    g: _Group | None = None
    ordinal = False

    def flush() -> None:
        nonlocal g
        if g is not None:
            tokens.append(str(g.value()))
            g = None

    k = 0
    while k < len(run):
        w = run[k]
        is_ord = w in _ORD
        base = w
        val = _ORD[w] if is_ord else None
        if is_ord:
            kind = "scale" if val in (100, 1000) else ("tens" if val >= 20 and val % 10 == 0 else
                                                       "teens" if val >= 10 else "ones")
        elif w in _ONES:
            kind, val = "ones", _ONES[w]
        elif w in _TEENS:
            kind, val = "teens", _TEENS[w]
        elif w in _TENS:
            kind, val = "tens", _TENS[w]
        else:
            kind, val = "scale", _SCALES[w]

        # Year-like pairs: a 10-99 group followed by another tens/teens group.
        if g is not None and kind in ("tens", "teens") and g.total == 0 and \
                g.last in ("tens", "teens", "tens+ones") and 10 <= g.current <= 99:
            first = g.current
            g = _Group()
            g.current = val
            g.last = kind
            # Absorb a following ones word ("twenty twenty seven").
            if kind == "tens" and not is_ord and k + 1 < len(run) and run[k + 1] in _ONES:
                g.current += _ONES[run[k + 1]]
                k += 1
                g.last = "tens+ones"
            elif kind == "tens" and not is_ord and k + 1 < len(run) and run[k + 1] in _ORD \
                    and _ORD[run[k + 1]] < 10:
                g.current += _ORD[run[k + 1]]
                k += 1
                ordinal = True
            tokens.append(f"{first}{g.current:02d}" + (_suffix(g.current) if ordinal or is_ord else ""))
            g = None
            ordinal = False
            k += 1
            continue
        if g is None:
            if kind == "scale":
                g = _Group()
                g.current = 1
            else:
                g = _Group()
        if kind == "ones":
            if g.last in ("ones", "teens", "tens+ones") or (g.last == "tens" and False):
                flush()
                g = _Group()
            if g.last == "tens":
                g.current += val
                g.last = "tens+ones"
            else:
                g.current += val
                g.last = "ones"
        elif kind == "teens":
            if g.last in ("ones", "teens", "tens", "tens+ones"):
                flush()
                g = _Group()
            g.current += val
            g.last = "teens"
        elif kind == "tens":
            if g.last in ("ones", "teens", "tens", "tens+ones"):
                flush()
                g = _Group()
            g.current += val
            g.last = "tens"
        else:  # scale
            if val == 100:
                g.current = (g.current or 1) * 100
            else:
                g.total += (g.current or 1) * val
                g.current = 0
            g.last = "scale"
        if is_ord:
            v = g.value()
            g = None
            tokens.append(f"{v}{_suffix(v)}")
        k += 1
    flush()
    return tokens


def _numbers(s: str) -> str:
    words = s.split()
    words = _parse_number_words(words)
    s = " ".join(words)
    # "2 point 5" -> 2.5
    s = re.sub(r"\b(\d+) point (\d+)\b", lambda m: f"{m.group(1)}.{m.group(2)}", s)
    # point followed by single digits "2 point 5 7" is rare; leave it.
    s = re.sub(r"\b(\d+(?:\.\d+)?) (?:percent|per cent)\b", r"\1%", s)
    s = re.sub(r"\b(\d+) dollars? and (\d+) cents?\b",
               lambda m: f"${m.group(1)}.{int(m.group(2)):02d}", s)
    s = re.sub(r"\b(\d+(?:\.\d+)?) dollars?\b", r"$\1", s)
    s = re.sub(r"\b(\d+) cents?\b", r"¢\1", s)
    return s


class EnglishNormalizer:
    """Callable normaliser. ``id`` names the rules and the spelling map in use."""

    def __init__(self, spelling_map: str | Path | None = None) -> None:
        self.spellings = dict(SPELLINGS_BUILTIN)
        self.id = NORMALIZER_BASE + "+builtin-spellings"
        if spelling_map is not None:
            raw = Path(spelling_map).read_bytes()
            self.spellings = json.loads(raw)
            self.id = f"{NORMALIZER_BASE}+spellings-sha256:{hashlib.sha256(raw).hexdigest()[:16]}"

    def __call__(self, s: str) -> str:
        s = (s or "").lower()
        s = re.sub(r"[<\[][^>\]]*[>\]]", "", s)
        s = re.sub(r"\(([^)]+?)\)", "", s)
        s = re.sub(FILLERS, "", s)
        s = re.sub(r"\s+'", "'", s)
        s = s.replace("’", "'")
        for pattern, repl in REPLACERS:
            s = re.sub(pattern, repl, s)
        s = re.sub(r"(\d),(\d)", r"\1\2", s)
        s = re.sub(r"\.([^0-9]|$)", r" \1", s)
        s = remove_symbols_and_diacritics(s, keep=".%$¢€£")
        s = re.sub(r"\s+", " ", s).strip()
        s = _numbers(s)
        s = " ".join(self.spellings.get(w, w) for w in s.split())
        s = re.sub(r"[.$¢€£]([^0-9])", r" \1", s)
        s = re.sub(r"[.$¢€£]$", " ", s)
        s = re.sub(r"([^0-9])%", r"\1 ", s)
        return re.sub(r"\s+", " ", s).strip()


_DEFAULT: EnglishNormalizer | None = None


def normalize(s: str) -> str:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = EnglishNormalizer()
    return _DEFAULT(s)


def normalizer_id() -> str:
    return EnglishNormalizer().id
