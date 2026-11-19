"""Text graders: key facts, commit messages, diff bug questions (spec 12 §3.2).

Key facts (explain_error, diff_review "what changed", commit_msg):
    facts:  {name: [regex, ...]}: every fact must be matched by at least one of its regexes
    forbid: [regex]: none may match (a listed wrong cause)
Matching is case-insensitive over the normalised visible answer: reasoning
removed, backticks and bold/italic markers removed, whitespace collapsed.

Copying the prompt earns nothing (explain_error, diff_review "what changed" and
"is there a bug"): the grader is given the prompt as the source, and before the
facts are matched it blanks every copied run, that is COPY_RUN or more answer
words that follow the source's words in order, allowing COPY_GAP inserted or
skipped word between two of them (words the source never uses are ignored, so
padding an echo with other words does not hide it). The facts must then be
found in what is left, and what is left may use at most MAX_SOURCE_REUSE of the
source's distinct words. An answer longer than MAX_ANSWER_WORDS fails. A quoted
line, a key name or an identifier stays: those runs are short. An answer that
quotes the whole source and also states the facts in its own words passes.

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


def _match(t: str, facts: dict, forbid: list | None) -> tuple[dict, list[str]]:
    found = {name: any(re.search(rx, t, re.I | re.S) for rx in rxs) for name, rxs in (facts or {}).items()}
    bad = [rx for rx in forbid or [] if re.search(rx, t, re.I | re.S)]
    return found, bad


def match_facts(text: str, facts: dict, forbid: list | None = None) -> tuple[dict, list[str]]:
    return _match(normalise(text), facts, forbid)


COPY_RUN = 8
COPY_GAP = 1
MAX_SOURCE_REUSE = 0.5
MAX_ANSWER_WORDS = 2000
WORD_RE = re.compile(r"[A-Za-z0-9_]+")
BLANK = "\u00b7"


def _words(text: str) -> list[str]:
    return [w.lower() for w in WORD_RE.findall(text)]


def blank_copied(text: str, source: str) -> tuple[str, int]:
    """`text` (already normalised) with every word of a copied run of `source` replaced by dots, and how many
    words were blanked. A run is COPY_RUN or more of the answer's source words that follow the source in order,
    with at most COPY_GAP word inserted or skipped between two of them on either side."""
    src = _words(source)
    vocab = set(src)
    at: dict[str, list[int]] = {}
    for j, w in enumerate(src):
        at.setdefault(w, []).append(j)
    ms = [m for m in WORD_RE.finditer(text) if m.group(0).lower() in vocab]
    a = [m.group(0).lower() for m in ms]
    hit = [False] * len(a)
    for i in range(len(a)):
        if hit[i]:
            continue
        for j in at.get(a[i], ()):
            run, ai, pj = [i], i, j
            while True:
                step = next(((ai + da, pj + dp) for da in range(1, COPY_GAP + 2) for dp in range(1, COPY_GAP + 2)
                             if ai + da < len(a) and pj + dp < len(src) and a[ai + da] == src[pj + dp]), None)
                if step is None:
                    break
                ai, pj = step
                run.append(ai)
            if len(run) >= COPY_RUN:
                for k in run:
                    hit[k] = True
                break
    out = list(text)
    for m, h in zip(ms, hit):
        if h:
            out[m.start():m.end()] = BLANK * (m.end() - m.start())
    return "".join(out), sum(hit)


def source_reuse(text: str, source: str) -> float:
    """The share of the source's distinct words that appear in `text`."""
    vocab = set(_words(source))
    return len(set(_words(text)) & vocab) / len(vocab) if vocab else 0.0


def _grade_facts(gr: GradeResult, check: dict, answer: str, source: str | None, missing_label: str) -> GradeResult:
    """Match the facts on the answer's own words; see the module docstring."""
    t = normalise(answer)
    words = len(WORD_RE.findall(t))
    if words > MAX_ANSWER_WORDS:
        gr.metrics["answer_words"] = words
        return gr.fail(f"answer is longer than {MAX_ANSWER_WORDS} words")
    facts, forbid = check.get("facts") or {}, check.get("forbid")
    own = t
    if source:
        own, copied = blank_copied(t, source)
        reuse = source_reuse(own, source)
        gr.metrics["copied_words"] = copied
        gr.metrics["source_reuse"] = round(reuse, 3)
    found, _ = _match(own, facts, None)
    _, bad = _match(t, {}, forbid)           # a wrong cause counts wherever it is, copied text included
    gr.metrics["facts_found"] = sum(found.values())
    gr.metrics["facts_total"] = len(found)
    missing = [k for k, v in found.items() if not v]
    if missing:
        if own is not t and all(_match(t, facts, None)[0].values()):
            gr.metrics["facts_only_in_copy"] = True
            gr.fail(missing_label + ", ".join(missing) + " (found only in text copied from the prompt)")
        else:
            gr.fail(missing_label + ", ".join(missing))
    if source and gr.metrics["source_reuse"] > MAX_SOURCE_REUSE:
        gr.metrics["reproduces_source"] = True
        gr.fail(f"the answer reuses more than {MAX_SOURCE_REUSE:.0%} of the prompt's words")
    if bad:
        gr.metrics["forbidden_fact"] = True
        gr.fail("states a wrong cause")
    return gr


def grade_keyfacts(check: dict, answer: str, source: str | None = None) -> GradeResult:
    """`source` is the prompt the model was shown; grade_answer always passes it."""
    gr = GradeResult(True)
    if not normalise(answer):
        return gr.fail("empty answer")
    return _grade_facts(gr, check, answer, source, "missing key facts: ")


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


def grade_diff_bug(check: dict, answer: str, source: str | None = None) -> GradeResult:
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
        _grade_facts(gr, {"facts": check.get("facts")}, answer, source, "missing: ")
    return gr
