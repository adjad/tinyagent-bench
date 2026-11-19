"""Deterministic readability checks for an Ask answer.

A subset of the hygiene layer in the output-quality spec
(`docs/benchmark/03-output-quality-spec.md`, section 4.2), applied to what
Harbor's Ask panel shows: the `panel` display profile (narrow, no tables,
headings or nested lists). Check ids keep the spec's numbering so a result can
be read against it:

  D2  renders in the panel        (no table, heading, deep nesting, HTML, rule, image)
  D4  answer first                (a required anchor in the first sentence / first 30 words)
  D5  length fits                 (0.5x-2.0x a reference word count, when the case gives one)
  D6  no leaks                    (reasoning markup, template tokens, planning prose, raw JSON, stall)
  D7  no filler                   (stock openers and closers)
  D8  restrained decoration       (emoji, bold density, exclamation marks)
  D9  no repetition               (repeated sentences, looping lines, 4-gram rate)

Every function here is pure: text in, verdicts out. The weights follow the
spec; the hygiene score is the weighted pass rate over the checks that apply.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

WEIGHTS = {"D2": 2, "D4": 2, "D5": 1, "D6": 3, "D7": 1, "D8": 1, "D9": 2}

_THINK = re.compile(r"</?think\b|</?thought>|<\|channel\|?>|<channel\|>", re.I)
_TEMPLATE = re.compile(r"<end_of_turn>|<start_of_turn>|<\|im_(?:start|end)\|>|</s>|<\|eot_id\|>|"
                       r"<\|tool_call\|?>|<tool_call>|\bcall:[a-z_]+\{", re.I)
_PLANNING = re.compile(
    r"^\s*(?:okay,?\s+(?:so\s+)?the user|the user (?:wants|is asking|said|asked)|"
    r"we need to|let me (?:analyze|think|start|see|check)|first,? i (?:need|should)|"
    r"i need to (?:figure|determine|analyze|check))", re.I)
_STALL = re.compile(r"^\s*(?:one moment|just a moment|let me (?:check|look)|checking)[^.\n]*[.…]*\s*$", re.I)
_ESCAPES = re.compile(r"\\n|\\\"")
_FILLER_OPEN = re.compile(r"^\s*(?:great question|certainly!?|sure!|sure,? here'?s|of course!?|"
                          r"as an ai\b|i'?d be happy to help|absolutely!)", re.I)
_FILLER_CLOSE = re.compile(r"(?:let me know if|i hope this helps|feel free to (?:ask|reach)|"
                           r"is there anything else)[^\n]*$", re.I)
_EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF]")
_BOLD = re.compile(r"\*\*[^*\n]+\*\*|__[^_\n]+__")
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’:.\-]*")


@dataclass
class Check:
    id: str
    passed: bool
    detail: str = ""


@dataclass
class Readability:
    checks: list[Check] = field(default_factory=list)

    @property
    def score(self) -> float | None:
        """Weighted pass rate 0-100 over the checks that applied."""
        if not self.checks:
            return None
        total = sum(WEIGHTS[c.id] for c in self.checks)
        got = sum(WEIGHTS[c.id] for c in self.checks if c.passed)
        return round(100.0 * got / total, 1)

    @property
    def leak(self) -> bool:
        return any(c.id == "D6" and not c.passed for c in self.checks)

    def as_dict(self) -> dict:
        return {"score": self.score, "leak": self.leak,
                "checks": {c.id: {"pass": c.passed, "detail": c.detail} for c in self.checks}}


def words(text: str) -> list[str]:
    return _WORD.findall(text)


def _sentences(text: str) -> list[str]:
    flat = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", text, flags=re.M)
    parts = re.split(r"(?<=[.!?])\s+|\n+", flat)
    return [p.strip() for p in parts if p.strip()]


def d2_panel(text: str) -> Check:
    problems = []
    if re.search(r"^\s*\|.*\|\s*$", text, re.M) and re.search(r"^\s*\|?\s*:?-{3,}", text, re.M):
        problems.append("table")
    if re.search(r"^\s{0,3}#{1,6}\s", text, re.M):
        problems.append("heading")
    if re.search(r"^(?: {4,}|\t+)(?:[-*+]|\d+[.)])\s", text, re.M):
        problems.append("nested list")
    if re.search(r"<(?:div|span|br|table|p|img)\b", text, re.I):
        problems.append("raw html")
    if re.search(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$", text, re.M):
        problems.append("rule")
    if re.search(r"!\[[^\]]*\]\(", text):
        problems.append("image")
    return Check("D2", not problems, ", ".join(problems))


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("’", "'")).strip().casefold()


def d4_answer_first(text: str, anchors: list[str]) -> Check | None:
    """Pass when any anchor lands in the first sentence or first list item, within 30 words."""
    if not anchors:
        return None
    sents = _sentences(text)
    head = _norm(sents[0]) if sents else ""
    opening = _norm(" ".join(text.split()[:30]))
    for a in anchors:
        na = _norm(a)
        if na and na in head and na in opening:
            return Check("D4", True, a)
    return Check("D4", False, "no anchor in the opening sentence")


def d5_length(text: str, reference_words: int | None) -> Check | None:
    if not reference_words:
        return None
    n = len(words(text))
    lo, hi = 0.5 * reference_words, 2.0 * reference_words
    return Check("D5", lo <= n <= hi, f"{n} words vs reference {reference_words}")


def d6_leaks(text: str) -> Check:
    problems = []
    if not text.strip():
        problems.append("empty")
    if _THINK.search(text):
        problems.append("reasoning markup")
    if _TEMPLATE.search(text):
        problems.append("template or tool-call markup")
    if _PLANNING.search(text):
        problems.append("planning prose")
    if _ESCAPES.search(text):
        problems.append("escaped characters")
    stripped = text.strip()
    if stripped.startswith(("{", "[")) and stripped.endswith(("}", "]")):
        problems.append("raw json")
    if _STALL.match(text) and len(words(text)) < 12:
        problems.append("stall")
    return Check("D6", not problems, ", ".join(problems))


def d7_filler(text: str) -> Check:
    problems = []
    if _FILLER_OPEN.search(text):
        problems.append("filler opener")
    if _FILLER_CLOSE.search(text.strip()):
        problems.append("filler closer")
    return Check("D7", not problems, ", ".join(problems))


def d8_decoration(text: str) -> Check:
    n = max(1, len(words(text)))
    problems = []
    if len(_EMOJI.findall(text)) > max(1, n // 100):
        problems.append("emoji")
    label_lines = re.findall(r"^\s*(?:[-*+]|\d+[.)])\s+\*\*", text, re.M)
    bold = len(_BOLD.findall(text)) - len(label_lines)
    if bold > max(1, n // 15):
        problems.append("bold")
    if text.count("!") > 1:
        problems.append("exclamations")
    return Check("D8", not problems, ", ".join(problems))


def d9_repetition(text: str) -> Check:
    problems = []
    sents = [_norm(s) for s in _sentences(text) if len(s.split()) >= 4]
    if len(sents) != len(set(sents)):
        problems.append("repeated sentence")
    lines = [_norm(line) for line in text.splitlines() if line.strip()]
    if any(lines.count(line) >= 3 for line in set(lines)):
        problems.append("looping line")
    toks = [w.casefold() for w in words(text)]
    grams = [tuple(toks[i:i + 4]) for i in range(len(toks) - 3)]
    if len(grams) >= 20:
        rate = 1 - len(set(grams)) / len(grams)
        if rate > 0.05:
            problems.append(f"4-gram repeat {rate:.0%}")
    return Check("D9", not problems, ", ".join(problems))


def score_text(text: str, *, anchors: list[str] | None = None,
               reference_words: int | None = None) -> Readability:
    checks = [d2_panel(text)]
    for c in (d4_answer_first(text, anchors or []), d5_length(text, reference_words)):
        if c is not None:
            checks.append(c)
    checks += [d6_leaks(text), d7_filler(text), d8_decoration(text), d9_repetition(text)]
    return Readability(checks)
