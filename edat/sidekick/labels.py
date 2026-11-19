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
    "prompt_echo": "echoing the prompt can pass (the facts are matched anywhere in the answer)",
    "probe_in_process": "the hidden tests run in the same process as the model's code",
}

# grader -> (how it grades, the reason), for graders labelled whatever the case
LABELLED_GRADERS = {
    "git": ("git_on_copy", "git_view"),
    "write_test": ("write_test_exit_codes", "source_compare"),
    "keyfacts": ("keyfacts", "prompt_echo"),
}
# Types every case of which is graded by a labelled grader (a test checks this both ways). A row of one of
# these types is never scored, whatever its tag or metrics say.
LABELLED_TYPES = ("git", "write_test", "explain_error")


def grading_label(grader: str, check: dict | None = None) -> dict:
    """How a case's answer is graded and whether that grading holds against code written to attack it."""
    check = check or {}
    if grader in LABELLED_GRADERS:
        how, why = LABELLED_GRADERS[grader]
        return {"grading": how, "grade_hardened": False, "not_hardened_reason": why}
    if grader == "shell" and any(((e or {}).get("output") or {}).get("mode") == "pairs"
                                 and not ((e or {}).get("output") or {}).get("forbid")
                                 for e in check.get("expect") or []):
        return {"grading": "shell_pairs", "grade_hardened": False, "not_hardened_reason": "task_blind_output"}
    if grader == "diff_bug" and check.get("has_bug"):
        return {"grading": "diff_bug_facts", "grade_hardened": False, "not_hardened_reason": "prompt_echo"}
    return {"grading": grader, "grade_hardened": True}


def may_score(rec: dict) -> bool:
    """Whether a row may enter any score, rate, facet, ranking, export figure or site datum: not tagged, not
    marked, and not of a labelled type. Any one of the three keeps a row out."""
    f = rec.get("facets") or {}
    return (NOT_HARDENED_TAG not in (f.get("tags") or ()) and (rec.get("metrics") or {}).get("grade_hardened") is not False
            and f.get("family") not in LABELLED_TYPES)


def reason_of(rec: dict) -> str | None:
    return (rec.get("metrics") or {}).get("not_hardened_reason")
