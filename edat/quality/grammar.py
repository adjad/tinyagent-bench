"""Offline grammar signal for the Writing family (spec 03 section 9.3).

LanguageTool (a local server, LGPL-2.1, run as its own process) is the primary checker and
Harper (a local binary, Apache-2.0) the second opinion. Neither is installed in the build
environment, so nothing here starts a server or runs a real binary on its own: both wrappers
take an injectable transport or runner, and an unavailable tool yields no issues and
`available == False`. The signal is then recorded as None by `writing_checks.score_writing`.

Counting rule (spec): an issue counts only when LanguageTool and Harper flag overlapping spans,
or the LanguageTool rule is on the allow-list. Names from the brief and the profile are added
to the spelling dictionary (`custom_dictionary`), so they are never spelling errors. Before use,
each enabled rule is measured on 200 human-labelled sentences and a rule below 90 % precision is
disabled (`rule_precision`, `enabled_rules`).
"""
from __future__ import annotations

import json
import re
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from typing import Callable, Iterable, Protocol

GRAMMAR_VERSION = "edat-grammar/1"

LT_CATEGORIES = "GRAMMAR,TYPOS,PUNCTUATION"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


@dataclass(frozen=True)
class GrammarIssue:
    start: int
    end: int
    rule_id: str
    category: str
    message: str
    source: str            # "languagetool" | "harper"

    def to_dict(self) -> dict:
        return asdict(self)


class GrammarChecker(Protocol):
    name: str
    version: str

    def check(self, text: str) -> list[GrammarIssue]: ...


def _is_local(url: str) -> bool:
    host = urllib.parse.urlparse(url).hostname or ""
    return host in _LOCAL_HOSTS or host.endswith(".localhost")


def _default_transport(url: str, fields: dict) -> dict:
    """POST form fields to a local URL with urllib. Refuses anything that is not local."""
    if not _is_local(url):
        raise ValueError(f"grammar server must be local, got {url!r}")
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=10) as r:            # noqa: S310 - local only, checked above
        return json.loads(r.read().decode())


class LanguageToolChecker:
    """LanguageTool over its local HTTP API: POST /v2/check, language en-US, style rules off."""

    name = "languagetool"

    def __init__(self, base_url: str = "http://127.0.0.1:8081", *, transport: Callable[[str, dict], dict] | None = None,
                 version: str = "unpinned", disabled_rules: Iterable[str] = (), dictionary: Iterable[str] = ()):
        if not _is_local(base_url):
            raise ValueError(f"LanguageTool must run on localhost, got {base_url!r}")
        self.base_url = base_url.rstrip("/")
        self.transport = transport or _default_transport
        self.version = version
        self.disabled_rules = tuple(disabled_rules)
        self.dictionary = tuple(dictionary)
        self.available = True

    def check(self, text: str) -> list[GrammarIssue]:
        fields = {"language": "en-US", "text": text, "enabledCategories": LT_CATEGORIES,
                  "enabledOnly": "false", "level": "default"}
        if self.disabled_rules:
            fields["disabledRules"] = ",".join(self.disabled_rules)
        try:
            resp = self.transport(self.base_url + "/v2/check", fields)
        except (OSError, ValueError, json.JSONDecodeError):
            self.available = False
            return []
        self.available = True
        words = {w.lower() for w in self.dictionary}
        out: list[GrammarIssue] = []
        for m in resp.get("matches", []):
            rule = m.get("rule") or {}
            cat = (rule.get("category") or {}).get("id", "")
            if cat not in LT_CATEGORIES.split(","):
                continue                                         # style, redundancy and the like stay off
            start, length = int(m.get("offset", 0)), int(m.get("length", 0))
            if cat == "TYPOS" and text[start:start + length].lower() in words:
                continue
            out.append(GrammarIssue(start, start + length, rule.get("id", ""), cat, m.get("message", ""),
                                    "languagetool"))
        return out


def _default_runner(argv: list[str], stdin: str) -> str:
    return subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=20, check=True).stdout


class HarperChecker:
    """Harper through its command-line binary, reading the text on stdin and printing JSON lints."""

    name = "harper"

    def __init__(self, binary: str = "harper-cli", *, runner: Callable[[list[str], str], str] | None = None,
                 version: str = "unpinned"):
        self.binary = binary
        self.runner = runner or _default_runner
        self.version = version
        self.available = True

    def check(self, text: str) -> list[GrammarIssue]:
        try:
            raw = self.runner([self.binary, "lint", "--format", "json", "-"], text)
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            self.available = False
            return []
        self.available = True
        try:
            data = json.loads(raw) if raw.strip() else []
        except json.JSONDecodeError:
            return []
        lints = data.get("lints", []) if isinstance(data, dict) else data
        out = []
        for m in lints:
            span = m.get("span") or {}
            start, end = int(span.get("start", m.get("start", 0))), int(span.get("end", m.get("end", 0)))
            out.append(GrammarIssue(start, end, str(m.get("rule") or m.get("kind") or ""),
                                    str(m.get("kind") or "").upper(), str(m.get("message", "")), "harper"))
        return out


def _overlap(a: GrammarIssue, b: GrammarIssue) -> bool:
    return a.start < b.end and b.start < a.end


def combine(lt_issues: list[GrammarIssue], harper_issues: list[GrammarIssue],
            allow_list: Iterable[str] = ()) -> list[GrammarIssue]:
    """An issue counts when both tools flag overlapping spans, or the LanguageTool rule is allow-listed."""
    allow = set(allow_list)
    out = []
    for i in lt_issues:
        if i.rule_id in allow or any(_overlap(i, h) for h in harper_issues):
            out.append(i)
    return out


_NAME_WORD = re.compile(r"[^\W\d_][\w'’-]*")


def custom_dictionary(case: dict) -> set[str]:
    """Words that are never spelling errors: the brief's and the profile's names and capitalised anchors."""
    texts: list[str] = []
    s = case.get("sender") or {}
    texts += [s.get("name", ""), s.get("preferred_name", "")]
    r = case.get("recipient") or {}
    texts.append(r.get("name", ""))
    for grp in (case.get("points") or [], case.get("facts") or []):
        for p in grp:
            for a in p.get("anchors") or []:
                texts.append(str(a["value"]) if isinstance(a, dict) else str(a))
    texts += list(case.get("entities") or [])
    texts.append(case.get("allowed_text") or "")
    words: set[str] = set()
    for t in texts:
        for w in _NAME_WORD.findall(t):
            if w[:1].isupper():
                words.add(w)
    return words


def unavailable() -> dict:
    return {"errors_per_100w": None, "issues": [], "available": False}


def grammar_signal(text: str, case: dict, checkers: list, *, allow_list: Iterable[str] = ()) -> dict:
    """{errors_per_100w, issues, available}. Needs LanguageTool; Harper is the second opinion.

    With only one working checker the issues are those of LanguageTool allow-listed rules (or none
    when it is missing), so a lone tool never inflates the count.
    """
    lt = next((c for c in checkers if getattr(c, "name", "") == "languagetool"), None)
    hp = next((c for c in checkers if getattr(c, "name", "") == "harper"), None)
    if lt is None:
        return unavailable()
    lt_issues = lt.check(text)
    if not getattr(lt, "available", True):
        return unavailable()
    hp_issues = hp.check(text) if hp is not None else []
    dictionary = custom_dictionary(case)
    lt_issues = [i for i in lt_issues
                 if not (i.category == "TYPOS" and text[i.start:i.end] in dictionary)]
    hp_issues = [i for i in hp_issues if text[i.start:i.end] not in dictionary]
    counted = combine(lt_issues, hp_issues, allow_list)
    n_words = len(text.split())
    rate = (100.0 * len(counted) / n_words) if n_words else 0.0
    issues = [dict(i.to_dict(), text=text[i.start:i.end]) for i in counted]
    return {"errors_per_100w": rate, "issues": issues, "available": True}


def rule_precision(checker: GrammarChecker, labelled_sentences: list[dict]) -> dict[str, float]:
    """Per-rule precision on labelled sentences.

    Each item is {"text": str, "errors": [{"start": int, "end": int}]}: the human-marked errors.
    A flagged issue is a true positive when its span overlaps a marked error.
    """
    tp: dict[str, int] = {}
    n: dict[str, int] = {}
    for item in labelled_sentences:
        gold = item.get("errors") or []
        for i in checker.check(item["text"]):
            n[i.rule_id] = n.get(i.rule_id, 0) + 1
            if any(i.start < g["end"] and g["start"] < i.end for g in gold):
                tp[i.rule_id] = tp.get(i.rule_id, 0) + 1
    return {r: tp.get(r, 0) / c for r, c in n.items()}


def enabled_rules(precisions: dict[str, float], threshold: float = 0.9) -> set[str]:
    return {r for r, p in precisions.items() if p >= threshold}
