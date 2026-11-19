"""Text graders: key facts, commit messages, diff bug questions (spec 12 §3.2).

Key facts (explain_error, diff_review "what changed", commit_msg):
    facts:  {name: [regex, ...]}: every fact must be matched by at least one of its regexes
    forbid: [regex]: none may match (a listed wrong cause)
Matching is case-insensitive over the normalised visible answer: reasoning
removed, backticks and bold/italic markers removed, whitespace collapsed.

commit_msg adds:
    subject_max: 72
    diff_names: identifiers and file names that appear in the diff
    The subject has no trailing period, does not start with a past-tense or
    -ing word from a fixed list or with "This commit", a blank line separates
    any body, and every file-name or identifier-like token in the message is
    in `diff_names` (no invented names).

diff_bug (diff_review "is there a bug"):
    has_bug: bool
    facts:   location and cause facts (only when has_bug)
    The first word must be Yes or No.
"""
from __future__ import annotations

import re

from edat.sidekick.extract import fences, visible_answer
from edat.sidekick.graders.common import GradeResult


def normalise(text: str) -> str:
    t = visible_answer(text)
    t = t.replace("`", "")
    t = re.sub(r"(\*\*|__)(.+?)\1", r"\2", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", t)
    t = re.sub(r"[ \t]+", " ", t)
    return t.strip()


def match_facts(text: str, facts: dict, forbid: list | None = None) -> tuple[dict, list[str]]:
    t = normalise(text)
    found = {name: any(re.search(rx, t, re.I | re.S) for rx in rxs) for name, rxs in (facts or {}).items()}
    bad = [rx for rx in forbid or [] if re.search(rx, t, re.I | re.S)]
    return found, bad


def grade_keyfacts(check: dict, answer: str) -> GradeResult:
    gr = GradeResult(True)
    if not normalise(answer):
        return gr.fail("empty answer")
    found, bad = match_facts(answer, check.get("facts") or {}, check.get("forbid"))
    gr.metrics["facts_found"] = sum(found.values())
    gr.metrics["facts_total"] = len(found)
    missing = [k for k, v in found.items() if not v]
    if missing:
        gr.fail("missing key facts: " + ", ".join(missing))
    if bad:
        gr.metrics["forbidden_fact"] = True
        gr.fail("states a wrong cause")
    return gr


BAD_START = {"added", "adds", "adding", "fixed", "fixes", "fixing", "changed", "changes", "changing",
             "updated", "updates", "updating", "removed", "removes", "removing", "renamed", "renames",
             "renaming", "refactored", "refactoring", "implemented", "implementing", "created", "creating",
             "made", "making", "moved", "moving", "deleted", "deleting", "improved", "improving"}

NAME_TOKEN_RE = re.compile(r"\b[\w./-]+\.(?:py|js|mjs|ts|json|md|txt|yaml|yml|toml|sh|csv|html|css)\b|\b[a-z]+(?:_[a-z0-9]+)+\b|\b[a-z]+[A-Z][A-Za-z0-9]*\b")


def commit_message_text(answer: str) -> str:
    vis = visible_answer(answer)
    blocks = fences(vis)
    if blocks:
        return blocks[0][1].strip("\n")
    lines = vis.strip().splitlines()
    # Drop a leading "Commit message:" style label line.
    if lines and re.match(r"^\s*(here'?s|commit message|suggested)\b.*:\s*$", lines[0], re.I):
        lines = lines[1:]
    return "\n".join(lines).strip()


def grade_commit_msg(check: dict, answer: str) -> GradeResult:
    gr = GradeResult(True)
    msg = commit_message_text(answer)
    if not msg:
        return gr.fail("empty message")
    lines = msg.splitlines()
    subject = re.sub(r"^\s*(subject|title)\s*:\s*", "", lines[0], flags=re.I).strip()
    rules = []
    if len(subject) > check.get("subject_max", 72):
        rules.append(f"subject is {len(subject)} characters")
    if subject.endswith("."):
        rules.append("subject ends with a period")
    first = re.sub(r"^[\w-]+(\([^)]*\))?!?:\s*", "", subject)       # allow "fix(scope): ..."
    first_word = (re.findall(r"[A-Za-z']+", first) or [""])[0].lower()
    if first_word in BAD_START or re.match(r"this commit\b", first, re.I):
        rules.append(f"subject starts with {first_word!r}")
    if len(lines) > 1 and lines[1].strip():
        rules.append("no blank line before the body")
    gr.metrics["format_ok"] = not rules
    for r in rules:
        gr.fail(r)
    found, bad = match_facts(msg, check.get("facts") or {}, check.get("forbid"))
    missing = [k for k, v in found.items() if not v]
    if missing:
        gr.fail("missing key facts: " + ", ".join(missing))
    gr.metrics["facts_found"] = sum(found.values())
    gr.metrics["facts_total"] = len(found)
    names = set(check.get("diff_names") or [])
    lowered = {n.lower() for n in names}
    invented = []
    for tok in NAME_TOKEN_RE.findall(msg):
        base = tok.rsplit("/", 1)[-1]
        if tok not in names and base not in names and tok.lower() not in lowered and base.lower() not in lowered:
            invented.append(tok)
    if invented:
        gr.metrics["invented_names"] = len(invented)
        gr.fail("names not in the diff: " + ", ".join(sorted(set(invented))[:5]))
    return gr


def first_word(answer: str) -> str:
    t = normalise(answer)
    m = re.match(r"^[^A-Za-z]*([A-Za-z]+)", t)
    return m.group(1).lower() if m else ""


def grade_diff_bug(check: dict, answer: str) -> GradeResult:
    gr = GradeResult(True)
    w = first_word(answer)
    if w not in ("yes", "no"):
        gr.metrics["extraction_failed"] = True
        return gr.fail("answer does not start with Yes or No")
    said_bug = w == "yes"
    gr.metrics["said_bug"] = said_bug
    gr.metrics["false_alarm"] = said_bug and not check["has_bug"]
    if said_bug != check["has_bug"]:
        return gr.fail(f"said {w}, the diff {'has' if check['has_bug'] else 'has no'} bug")
    if check["has_bug"]:
        found, bad = match_facts(answer, check.get("facts") or {}, check.get("forbid"))
        missing = [k for k, v in found.items() if not v]
        if missing:
            gr.fail("missing: " + ", ".join(missing))
    return gr
