"""Which sidekick rows may be scored: the one decision every scorer uses.

A case is labelled `not hardened against adversarial model code` when its grading is one a model could
pass on purpose without doing the task. The label comes from how the case is graded (its grader, and for
two graders the form of its check), set by the runner before grading starts, so an error in grading
cannot strip it. A labelled row carries the tag `grade-not-hardened`, `grade_hardened: false` and the
reason's short code in `not_hardened_reason`; every scorer (the runner's summary, the results aggregate,
the export and the site data, the ceiling report) asks `may_score` and reports labelled rows apart.

This module imports nothing heavy: the results package uses it too.
"""
from __future__ import annotations

NOT_HARDENED = "not hardened against adversarial model code"
NOT_HARDENED_TAG = "grade-not-hardened"

# Reason codes (a row's metrics keep at most 64 characters) and what each one means, for the report.
REASONS = {
    "git_view": "graded by real git reading a repository the model controls",
    "source_compare": "a test that compares the module against the shown source passes",
    "task_blind_output": "task-blind output can pass (any line holding the expected name and number)",
    "prompt_echo": "fact-matched free text; the guard against copying the prompt awaits its own pass",
    "guessable": "one constant answer passes every case (the Yes/No diffs without a bug, answered No)",
    "probe_in_process": "the hidden tests run in the same process as the model's code",
    "not_allow_listed": "graded by a method not on the allow-list",
    "unmarked": "a graded row with no allow-listed hardened mark (default deny)",
}

# The allow-list: the only grading methods whose verdict holds against code written to attack the grader.
# A graded row scores only when the runner marked it with one of these and `grade_hardened: true`.
#   code         hidden tests and differential inputs, run in the sealed sandbox, verdict on the host
#   regex_write  the model's pattern run on the host against labelled strings
#   regex_read   the parsed set of labels must equal the right set exactly
#   shell        the command's output and folder checked exactly (a number, a set or list of lines, a
#                tree, an exact text, or a forbidden output); the pairs check without a forbidden output
#                is graded `shell_pairs`, which is not on the list
HARDENED_GRADING = ("code", "regex_write", "regex_read", "shell")
# The only task types a row may score in: every case of each is graded by an allow-listed method, except
# the shell cases graded `shell_pairs`, which are labelled row by row. Any other type, a type this list
# does not know included, never scores.
SCORED_TYPES = ("write_fn", "fix_bug", "shell", "regex")

# grader -> (how it grades, the reason), for graders labelled whatever the case
LABELLED_GRADERS = {
    "git": ("git_on_copy", "git_view"),
    "write_test": ("write_test_exit_codes", "source_compare"),
    "keyfacts": ("keyfacts", "prompt_echo"),
    "commit_msg": ("commit_msg", "prompt_echo"),
    "probe": ("probe", "probe_in_process"),
}
# The task types no row of which is scored (a test checks that with SCORED_TYPES they are every type).
LABELLED_TYPES = ("git", "write_test", "explain_error", "commit_msg", "diff_review")


def grading_label(grader: str, check: dict | None = None) -> dict:
    """How a case's answer is graded and whether that grading holds against code written to attack it.
    Only an allow-listed method is marked hardened; anything else is labelled with its reason."""
    check = check or {}
    if grader in LABELLED_GRADERS:
        how, why = LABELLED_GRADERS[grader]
        return {"grading": how, "grade_hardened": False, "not_hardened_reason": why}
    if grader == "shell" and any(((e or {}).get("output") or {}).get("mode") == "pairs"
                                 and not ((e or {}).get("output") or {}).get("forbid")
                                 for e in check.get("expect") or []):
        return {"grading": "shell_pairs", "grade_hardened": False, "not_hardened_reason": "task_blind_output"}
    if grader == "diff_bug":
        if check.get("has_bug"):
            return {"grading": "diff_bug_facts", "grade_hardened": False, "not_hardened_reason": "prompt_echo"}
        return {"grading": "diff_bug_clean", "grade_hardened": False, "not_hardened_reason": "guessable"}
    if grader in HARDENED_GRADING:
        return {"grading": grader, "grade_hardened": True}
    return {"grading": grader, "grade_hardened": False, "not_hardened_reason": "not_allow_listed"}


def may_score(rec: dict) -> bool:
    """Whether a row may enter any score, rate, facet, ranking, export figure or site datum (default deny).
    A graded row (status ok, or no status) scores only when all of these hold: it is marked
    `grade_hardened: true` with an allow-listed `grading`, its type is in SCORED_TYPES, and it is not tagged
    `grade-not-hardened`. A row with no mark, an old or resumed record, or an unknown type is not scored.
    A row that was not graded (excluded, an error) scores nothing either way; it is routed with its type
    (an allow-listed type and no tag) so that it still counts against its entry's exclusion rate."""
    f = rec.get("facets") or {}
    m = rec.get("metrics") or {}
    if NOT_HARDENED_TAG in (f.get("tags") or ()) or f.get("family") not in SCORED_TYPES:
        return False
    if rec.get("status", "ok") != "ok":
        return m.get("grade_hardened") is not False
    return m.get("grade_hardened") is True and m.get("grading") in HARDENED_GRADING


def reason_of(rec: dict) -> str | None:
    m = rec.get("metrics") or {}
    if m.get("not_hardened_reason") or rec.get("status", "ok") != "ok" or may_score(rec):
        return m.get("not_hardened_reason")
    return "unmarked"
