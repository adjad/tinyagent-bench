"""Shared text building blocks for tinyagent-bench's output-quality checks.

Pure, stdlib-only helpers used by the preference checkers (P1-P9, spec 03
section 4.5) and the hygiene checks (D1-D11, section 4.2): a small Markdown
block parser, the sentence splitter, word counting, the emoji table, filler
lists and the visible-answer extractor.

Nothing here calls a model or touches the network.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

TEXTUTIL_VERSION = "edat-textutil/1"

# --------------------------------------------------------------------------
# Visible answer: strip only the documented reasoning block (spec 03 s1, s6.1)
# --------------------------------------------------------------------------

_THINK_CLOSED = re.compile(r"<think>.*?</think>", re.S | re.I)
_GEMMA_CHANNEL = re.compile(r"<\|channel\|?>?thought.*?<channel\|>", re.S)
_GEMMA_CHANNEL_ALT = re.compile(r"<\|channel>thought.*?<channel\|>", re.S)


def visible_answer(text: str | None, reasoning: str | None = None) -> tuple[str, str]:
    """Return (visible, reasoning_text).

    Removes closed ``<think>...</think>`` blocks and Gemma's thought channel.
    An unclosed ``<think>`` is NOT removed: it is a leak and D6 sees it.
    Separately returned `reasoning` (``reasoning_content``) is appended to the
    reasoning text. No salvage: an empty visible answer stays empty.
    """
    text = text or ""
    parts: list[str] = []
    for rx in (_THINK_CLOSED, _GEMMA_CHANNEL, _GEMMA_CHANNEL_ALT):
        for m in rx.finditer(text):
            parts.append(m.group(0))
        text = rx.sub("", text)
    if reasoning:
        parts.insert(0, reasoning)
    inner = "\n".join(re.sub(r"</?think>|<\|channel\|?>?thought|<channel\|>", "", p) for p in parts)
    return text.strip(), inner.strip()


# --------------------------------------------------------------------------
# Normalization helpers
# --------------------------------------------------------------------------

def nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def fold(text: str) -> str:
    """NFKC, case-fold, unify dashes and quotes, collapse whitespace."""
    t = nfkc(text).casefold()
    t = re.sub(r"[‐-―−]", "-", t)
    t = t.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", t).strip()


_WORD = re.compile(r"[A-Za-z0-9À-ɏ]+(?:['’.\-:,/][A-Za-z0-9À-ɏ]+)*")


def words(text: str) -> list[str]:
    return _WORD.findall(strip_markdown(text))


def word_count(text: str) -> int:
    return len(words(text))


def strip_markdown(text: str) -> str:
    """Remove inline Markdown markers so prose can be counted."""
    t = re.sub(r"```.*?```", " ", text, flags=re.S)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"\*\*|__", "", t)
    t = re.sub(r"(?<!\w)[*_](?=\S)|(?<=\S)[*_](?!\w)", "", t)
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.M)
    t = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", t, flags=re.M)
    t = re.sub(r"^\s*>\s?", "", t, flags=re.M)
    return t


# --------------------------------------------------------------------------
# Sentence splitter (English, version 1)
# --------------------------------------------------------------------------

_ABBREV = {
    "mr", "mrs", "ms", "dr", "prof", "st", "sr", "jr", "vs", "etc", "e.g", "i.e", "approx",
    "no", "fig", "inc", "ltd", "co", "mt", "ave", "rd", "a.m", "p.m", "min", "max", "dept",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    "mon", "tue", "wed", "thu", "fri", "sat", "sun", "approx", "est", "u.s", "u.k",
}


def split_sentences(text: str) -> list[str]:
    """Split prose into sentences. Newlines that end a line are boundaries.

    Protects decimals (1,250.40), times (9:30, 3 p.m.), abbreviations
    (Dr., e.g.), ellipses and codes. Markdown markers are not removed here.
    """
    out: list[str] = []
    for para in re.split(r"\n+", text):
        para = para.strip()
        if not para:
            continue
        buf = ""
        i = 0
        n = len(para)
        while i < n:
            ch = para[i]
            buf += ch
            if ch in ".!?":
                # absorb runs like "?!" or "..." and closing quotes/brackets
                j = i + 1
                while j < n and para[j] in ".!?\"'”’)]*_":
                    buf += para[j]
                    j += 1
                nxt = para[j:j + 1]
                if ch == "." and i + 1 < n and para[i + 1].isdigit():
                    i += 1
                    continue
                if nxt and not nxt.isspace():
                    i = j
                    continue
                if ch == ".":
                    m = re.search(r"([A-Za-z.]+)\.$", buf.rstrip("\"'”’)]*_"))
                    tok = (m.group(1).lower() if m else "")
                    if tok in _ABBREV or (len(tok) == 1 and tok.isalpha()):
                        # "Dr. Varga" / "a.m. tomorrow": not a boundary unless followed by a capital after a.m./p.m.
                        rest = para[j:].lstrip()
                        if tok in ("a.m", "p.m") and rest[:1].isupper():
                            pass
                        else:
                            i = j
                            continue
                rest = para[j:].lstrip()
                if rest and not (rest[0].isupper() or rest[0].isdigit() or rest[0] in "\"'“([*_-"):
                    i = j
                    continue
                s = buf.strip()
                if s:
                    out.append(s)
                buf = ""
                i = j
                continue
            i += 1
        if buf.strip():
            out.append(buf.strip())
    return out


# --------------------------------------------------------------------------
# Markdown blocks
# --------------------------------------------------------------------------

_BULLET = re.compile(r"^(\s*)([-*+])\s+(.*)$")
_ORDERED = re.compile(r"^(\s*)(\d{1,3})([.)])\s+(.*)$")
_HEADING = re.compile(r"^\s{0,3}#{1,6}(\s+|$)")
_HR = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_FENCE = re.compile(r"^\s{0,3}(```+|~~~+)")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$")
_HTML = re.compile(r"</?[A-Za-z][A-Za-z0-9-]*(\s[^<>]*)?/?>")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


@dataclass
class ListItem:
    text: str
    depth: int
    marker: str
    number: int | None = None


@dataclass
class Block:
    kind: str                     # paragraph, bullets, numbered, heading, code, table, quote, hr, html
    lines: list[str] = field(default_factory=list)
    items: list[ListItem] = field(default_factory=list)
    closed: bool = True           # code fences only

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def parse_blocks(text: str) -> list[Block]:
    """A small line-based CommonMark-ish block parser (enough for D1-D3, P2-P4)."""
    lines = text.replace("\r\n", "\n").split("\n")
    blocks: list[Block] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        fm = _FENCE.match(line)
        if fm:
            fence = fm.group(1)
            b = Block("code", [line], closed=False)
            i += 1
            while i < n:
                b.lines.append(lines[i])
                if lines[i].strip().startswith(fence[:3]) and lines[i].strip().strip(fence[0]) == "":
                    b.closed = True
                    i += 1
                    break
                i += 1
            blocks.append(b)
            continue
        if _HEADING.match(line):
            blocks.append(Block("heading", [line]))
            i += 1
            continue
        if _HR.match(line) and not _BULLET.match(line + " x"):
            blocks.append(Block("hr", [line]))
            i += 1
            continue
        if _HR.match(line):
            blocks.append(Block("hr", [line]))
            i += 1
            continue
        if i + 1 < n and "|" in line and _TABLE_SEP.match(lines[i + 1]):
            b = Block("table", [line, lines[i + 1]])
            i += 2
            while i < n and "|" in lines[i] and lines[i].strip():
                b.lines.append(lines[i])
                i += 1
            blocks.append(b)
            continue
        if line.lstrip().startswith(">"):
            b = Block("quote")
            while i < n and lines[i].lstrip().startswith(">"):
                b.lines.append(lines[i])
                i += 1
            blocks.append(b)
            continue
        if _BULLET.match(line) or _ORDERED.match(line):
            ordered = bool(_ORDERED.match(line)) and not _BULLET.match(line)
            b = Block("numbered" if ordered else "bullets")
            base_indent = None
            while i < n:
                cur = lines[i]
                bm = _BULLET.match(cur)
                om = _ORDERED.match(cur)
                if bm or om:
                    indent = len((bm or om).group(1).expandtabs(4))
                    if base_indent is None:
                        base_indent = indent
                    depth = 0 if indent <= base_indent + 1 else 1 + (indent - base_indent - 2) // 2
                    if depth == 0:
                        is_ordered = bool(om) and not bm
                        if is_ordered != ordered:
                            break
                    if bm:
                        b.items.append(ListItem(bm.group(3), depth, bm.group(2)))
                    else:
                        b.items.append(ListItem(om.group(4), depth, om.group(3), int(om.group(2))))
                    b.lines.append(cur)
                    i += 1
                    continue
                if cur.strip() and cur.startswith((" ", "\t")) and b.items:
                    b.items[-1].text += " " + cur.strip()
                    b.lines.append(cur)
                    i += 1
                    continue
                if not cur.strip():
                    # a loose list: blank lines between items of the same list
                    j = i
                    while j < n and not lines[j].strip():
                        j += 1
                    if j < n:
                        nb, no = _BULLET.match(lines[j]), _ORDERED.match(lines[j])
                        if (nb or no) and (bool(no) and not nb) == ordered:
                            i = j
                            continue
                break
            blocks.append(b)
            continue
        if re.match(r"^\s*<(table|div|p|br|span|ul|ol|li|img|html|b|i)\b", line, re.I):
            b = Block("html", [line])
            i += 1
            while i < n and lines[i].strip():
                b.lines.append(lines[i])
                i += 1
            blocks.append(b)
            continue
        b = Block("paragraph")
        while i < n and lines[i].strip():
            cur = lines[i]
            if (_HEADING.match(cur) or _FENCE.match(cur) or _BULLET.match(cur) or _ORDERED.match(cur)
                    or cur.lstrip().startswith(">") or (i + 1 < n and "|" in cur and _TABLE_SEP.match(lines[i + 1]))):
                break
            b.lines.append(cur)
            i += 1
        blocks.append(b)
    return blocks


def list_blocks(blocks: list[Block]) -> list[Block]:
    return [b for b in blocks if b.kind in ("bullets", "numbered")]


_BOLD_LABEL = re.compile(r"^\s*(\*\*|__)[^*_]{1,60}?(\*\*|__)\s*[:—–-]|^\s*(\*\*|__)[^*_]{1,60}?[:—–-]\s*(\*\*|__)")


def is_bold_label(item_text: str) -> bool:
    return bool(_BOLD_LABEL.match(item_text))


def has_html(text: str) -> bool:
    outside = re.sub(r"```.*?```|`[^`]*`", " ", text, flags=re.S)
    return bool(_HTML.search(outside))


def has_image(text: str) -> bool:
    return bool(_IMAGE.search(text))


# --------------------------------------------------------------------------
# Emoji (approximation of Unicode 15.1 emoji-data.txt; see CHECKERS.md)
# --------------------------------------------------------------------------

EMOJI_TABLE_VERSION = "edat-emoji-ranges/15.1-embedded"

_EMOJI_PRESENTATION = [
    (0x231A, 0x231B), (0x23E9, 0x23EC), (0x23F0, 0x23F0), (0x23F3, 0x23F3), (0x25FD, 0x25FE),
    (0x2614, 0x2615), (0x2648, 0x2653), (0x267F, 0x267F), (0x2693, 0x2693), (0x26A1, 0x26A1),
    (0x26AA, 0x26AB), (0x26BD, 0x26BE), (0x26C4, 0x26C5), (0x26CE, 0x26CE), (0x26D4, 0x26D4),
    (0x26EA, 0x26EA), (0x26F2, 0x26F3), (0x26F5, 0x26F5), (0x26FA, 0x26FA), (0x26FD, 0x26FD),
    (0x2705, 0x2705), (0x270A, 0x270B), (0x2728, 0x2728), (0x274C, 0x274C), (0x274E, 0x274E),
    (0x2753, 0x2755), (0x2757, 0x2757), (0x2795, 0x2797), (0x27B0, 0x27B0), (0x27BF, 0x27BF),
    (0x2B1B, 0x2B1C), (0x2B50, 0x2B50), (0x2B55, 0x2B55), (0x1F004, 0x1F004), (0x1F0CF, 0x1F0CF),
    (0x1F18E, 0x1F18E), (0x1F191, 0x1F19A), (0x1F201, 0x1F201), (0x1F21A, 0x1F21A),
    (0x1F22F, 0x1F22F), (0x1F232, 0x1F236), (0x1F238, 0x1F23A), (0x1F250, 0x1F251),
    (0x1F300, 0x1F320), (0x1F32D, 0x1F335), (0x1F337, 0x1F37C), (0x1F37E, 0x1F393),
    (0x1F3A0, 0x1F3CA), (0x1F3CF, 0x1F3D3), (0x1F3E0, 0x1F3F0), (0x1F3F4, 0x1F3F4),
    (0x1F3F8, 0x1F43E), (0x1F440, 0x1F440), (0x1F442, 0x1F4FC), (0x1F4FF, 0x1F53D),
    (0x1F54B, 0x1F54E), (0x1F550, 0x1F567), (0x1F57A, 0x1F57A), (0x1F595, 0x1F596),
    (0x1F5A4, 0x1F5A4), (0x1F5FB, 0x1F64F), (0x1F680, 0x1F6C5), (0x1F6CC, 0x1F6CC),
    (0x1F6D0, 0x1F6D2), (0x1F6D5, 0x1F6D7), (0x1F6DC, 0x1F6DF), (0x1F6EB, 0x1F6EC),
    (0x1F6F4, 0x1F6FC), (0x1F7E0, 0x1F7EB), (0x1F7F0, 0x1F7F0), (0x1F90C, 0x1F93A),
    (0x1F93C, 0x1F945), (0x1F947, 0x1F9FF), (0x1FA70, 0x1FA7C), (0x1FA80, 0x1FA89),
    (0x1FA8F, 0x1FAC6), (0x1FACE, 0x1FADC), (0x1FADF, 0x1FAE9), (0x1FAF0, 0x1FAF8),
]

_EXTENDED_PICTOGRAPHIC = [
    (0x00A9, 0x00A9), (0x00AE, 0x00AE), (0x203C, 0x203C), (0x2049, 0x2049), (0x2122, 0x2122),
    (0x2139, 0x2139), (0x2194, 0x2199), (0x21A9, 0x21AA), (0x231A, 0x231B), (0x2328, 0x2328),
    (0x2388, 0x2388), (0x23CF, 0x23CF), (0x23E9, 0x23F3), (0x23F8, 0x23FA), (0x24C2, 0x24C2),
    (0x25AA, 0x25AB), (0x25B6, 0x25B6), (0x25C0, 0x25C0), (0x25FB, 0x25FE), (0x2600, 0x2605),
    (0x2607, 0x2612), (0x2614, 0x2685), (0x2690, 0x2705), (0x2708, 0x2712), (0x2714, 0x2714),
    (0x2716, 0x2716), (0x271D, 0x271D), (0x2721, 0x2721), (0x2728, 0x2728), (0x2733, 0x2734),
    (0x2744, 0x2744), (0x2747, 0x2747), (0x274C, 0x274C), (0x274E, 0x274E), (0x2753, 0x2755),
    (0x2757, 0x2757), (0x2763, 0x2767), (0x2795, 0x2797), (0x27A1, 0x27A1), (0x27B0, 0x27B0),
    (0x27BF, 0x27BF), (0x2934, 0x2935), (0x2B05, 0x2B07), (0x2B1B, 0x2B1C), (0x2B50, 0x2B50),
    (0x2B55, 0x2B55), (0x3030, 0x3030), (0x303D, 0x303D), (0x3297, 0x3297), (0x3299, 0x3299),
    (0x1F000, 0x1F0FF), (0x1F10D, 0x1F10F), (0x1F12F, 0x1F12F), (0x1F16C, 0x1F171),
    (0x1F17E, 0x1F17F), (0x1F18E, 0x1F18E), (0x1F191, 0x1F19A), (0x1F1AD, 0x1F1E5),
    (0x1F201, 0x1F20F), (0x1F21A, 0x1F21A), (0x1F22F, 0x1F22F), (0x1F232, 0x1F23A),
    (0x1F23C, 0x1F23F), (0x1F249, 0x1F3FA), (0x1F400, 0x1F53D), (0x1F546, 0x1F64F),
    (0x1F680, 0x1F6FF), (0x1F774, 0x1F77F), (0x1F7D5, 0x1F7FF), (0x1F80C, 0x1F80F),
    (0x1F848, 0x1F84F), (0x1F85A, 0x1F85F), (0x1F888, 0x1F88F), (0x1F8AE, 0x1F8FF),
    (0x1F90C, 0x1F93A), (0x1F93C, 0x1F945), (0x1F947, 0x1FAFF), (0x1FC00, 0x1FFFD),
]


def _in(ranges: list[tuple[int, int]], cp: int) -> bool:
    lo, hi = 0, len(ranges) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        a, b = ranges[mid]
        if cp < a:
            hi = mid - 1
        elif cp > b:
            lo = mid + 1
        else:
            return True
    return False


_EMOTICON = re.compile(r"(?<![\w/])(?::-?[)(DPp]|;-?\)|:'\(|<3|\^_\^|xD)(?![\w])")


def find_emoji(text: str) -> list[tuple[int, int, str]]:
    """Return (start, end, kind) for every emoji the P5 rule counts.

    kinds: presentation, pictographic+fe0f, flag, keycap.
    """
    out: list[tuple[int, int, str]] = []
    i = 0
    n = len(text)
    while i < n:
        cp = ord(text[i])
        nxt = text[i + 1] if i + 1 < n else ""
        # keycap: [0-9#*] FE0F? 20E3
        if text[i] in "0123456789#*":
            j = i + 1
            if j < n and text[j] == "️":
                j += 1
            if j < n and text[j] == "⃣":
                out.append((i, j + 1, "keycap"))
                i = j + 1
                continue
        if 0x1F1E6 <= cp <= 0x1F1FF and nxt and 0x1F1E6 <= ord(nxt) <= 0x1F1FF:
            out.append((i, i + 2, "flag"))
            i += 2
            continue
        if 0x1F1E6 <= cp <= 0x1F1FF:
            i += 1
            continue
        if _in(_EMOJI_PRESENTATION, cp):
            out.append((i, i + 1, "presentation"))
            i += 1
            continue
        if nxt == "️" and _in(_EXTENDED_PICTOGRAPHIC, cp):
            out.append((i, i + 2, "pictographic+fe0f"))
            i += 2
            continue
        i += 1
    return out


def find_emoticons(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _EMOTICON.finditer(text)]


# --------------------------------------------------------------------------
# Filler lists (D7, P1) - English, version 1
# --------------------------------------------------------------------------

FILLER_OPENERS = [
    r"great question", r"good question", r"certainly\b", r"sure[!,.]?\s*here'?s", r"sure!",
    r"of course[!,.]", r"absolutely[!,.]", r"as an ai\b", r"i'?d be (?:happy|glad) to help",
    r"i'?m (?:happy|glad) to help", r"happy to help", r"what a (?:great|good|lovely) question",
    r"thanks for (?:asking|your question)", r"no problem[!,.]", r"here'?s what i found",
]
FILLER_CLOSERS = [
    r"let me know if", r"i hope (?:this|that) helps", r"hope (?:this|that) helps", r"feel free to",
    r"is there anything else", r"anything else i can", r"don'?t hesitate to", r"happy to help further",
    r"if you have any (?:other |more )?questions", r"just let me know",
]
_OPENER_RX = re.compile(r"^\W*(?:" + "|".join(FILLER_OPENERS) + r")", re.I)
_CLOSER_RX = re.compile(r"(?:" + "|".join(FILLER_CLOSERS) + r")", re.I)


def is_filler_opener(sentence: str) -> bool:
    return bool(_OPENER_RX.search(strip_markdown(sentence).strip()))


def is_filler_closer(sentence: str) -> bool:
    return bool(_CLOSER_RX.search(strip_markdown(sentence)))


_STOP = {"the", "a", "an", "is", "are", "was", "to", "of", "and", "or", "in", "on", "at", "for",
         "my", "me", "i", "you", "your", "it", "what", "when", "where", "who", "how", "do", "does",
         "did", "be", "with", "this", "that", "there", "any", "can"}


def content_tokens(text: str) -> list[str]:
    return [w for w in (fold(x) for x in words(text)) if w not in _STOP]


def restates_request(sentence: str, request: str | None, threshold: float = 0.6) -> bool:
    """True when the sentence's content tokens overlap the request's by >= threshold.

    Overlap = |sentence tokens that are request tokens| / |sentence tokens|,
    and the sentence must also cover >= threshold of the request tokens, so a
    short answer that reuses one request word does not count.
    """
    if not request:
        return False
    s = content_tokens(sentence)
    r = set(content_tokens(request))
    if not s or not r:
        return False
    if s[0] in ("yes", "no", "yeah", "nope"):
        return False  # a verdict that echoes the question's words is an answer, not a restatement
    hit = sum(1 for w in s if w in r)
    cover = len({w for w in s if w in r}) / len(r)
    return hit / len(s) >= threshold and cover >= threshold
