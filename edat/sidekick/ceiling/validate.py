"""Validator for the ladder's probe templates (the same bar as the sidekick validator, S11):

* every reference answer passes, with no Restraint event;
* every wrong answer fails, and so do the empty answer and "OK.";
* the hidden tests are not trivial: the given starting files alone (the buggy
  version, or nothing) fail them;
* rung 9 (tool mode): the scripted reference session passes, a session that
  only answers "Done." fails, and a session that writes wrong answer 0 fails.

    python -m edat.sidekick.ceiling validate [--rung R] [--template ID] [--trials 3]
"""
from __future__ import annotations

from dataclasses import dataclass, field

from edat.sidekick.ceiling.agent import grade_scripted, reference_turns
from edat.sidekick.ceiling.probe import extract_files, run_probe
from edat.sidekick.ceiling.rungs import LADDER_SEED_PUBLIC, LadderCase, ladder_cases, materialize_probe
from edat.sidekick.graders import grade_answer
from edat.sidekick.sandbox.run import Isolation, TrialArea, get_isolation

NULL_ANSWERS = ("", "OK.")


@dataclass
class Problem:
    case: str
    trial: int
    what: str


@dataclass
class Report:
    checked: int = 0
    problems: list[Problem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def validate_case(case: LadderCase, trial: int, iso: Isolation, rep: Report, seed: str = LADDER_SEED_PUBLIC) -> None:
    inst = materialize_probe(case, trial, seed)
    rep.checked += 1
    bad = lambda what: rep.problems.append(Problem(case.id, trial, what))
    for i, ref in enumerate(inst.references):
        g = grade_answer(inst, ref, iso)
        if not g.passed or g.events:
            bad(f"reference {i} fails: {g.reasons[:2]}")
    for i, w in enumerate(inst.wrong):
        if grade_answer(inst, w, iso).passed:
            bad(f"wrong answer {i} passes")
    for n in NULL_ANSWERS:
        if grade_answer(inst, n, iso).passed:
            bad(f"null answer {n!r} passes")
    given = {t: s for t, s in (inst.check.get("files") or {}).items() if t in inst.check["targets"]}
    if len(given) == len(inst.check["targets"]):
        with TrialArea() as area:
            if run_probe(iso, area, inst.check, given).passed:
                bad("the given files already pass the hidden tests")
    if case.rung.mode == "tool":
        if not inst.tool or inst.tool.get("kind") != "probe" or "fixed" not in inst.tool:
            bad("tool-mode case without tool.kind=probe and tool.fixed")
            return
        if not inst.check.get("visible_tests"):
            bad("tool-mode case without visible tests")
        g = grade_scripted(inst, iso, reference_turns(inst))
        if not g.passed or g.events:
            bad(f"reference tool session fails: {g.reasons[:2]}")
        if grade_scripted(inst, iso, [{"text": "Done."}]).passed:
            bad("a tool session that changes nothing passes")
        files, why = extract_files(inst.wrong[0], inst.check["targets"])
        if not why:
            writes = [{"calls": [{"name": "write_file", "arguments": {"path": t, "content": s}} for t, s in files.items()]},
                      {"text": "Done."}]
            if grade_scripted(inst, iso, writes).passed:
                bad("a tool session that writes wrong answer 0 passes")


def validate(rungs=None, template_ids=None, trials: int = 3, iso: Isolation | None = None) -> Report:
    iso = iso or get_isolation()
    rep = Report()
    for case in ladder_cases(tuple(rungs) if rungs else None):
        if case.template is None:
            continue
        if template_ids and case.template.id not in template_ids:
            continue
        for t in range(1, trials + 1):
            validate_case(case, t, iso, rep)
    return rep
