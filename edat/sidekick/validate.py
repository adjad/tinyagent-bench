"""Case validator (spec 12 S11): before a case is admitted,

* every reference answer passes its own checks with no Restraint event;
* every listed wrong answer fails;
* an empty answer and a "do nothing" answer ("OK.") fail;
* for `write_test`, every mutant is caught by the first reference test file;
* for tool-mode cases, the reference solution placed in the working folder
  passes the tool-mode grading too.

`python -m edat.sidekick validate [--template ID] [--trials 3]` runs it.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from edat.sidekick.extract import extract
from edat.sidekick.gen.core import Instance, Template, materialize, trial_rng
from edat.sidekick.graders import grade_answer
from edat.sidekick.graders.write_test import _run_with
from edat.sidekick.sandbox.run import Isolation, TrialArea, get_isolation

NULL_ANSWERS = ("", "OK.")


@dataclass
class Problem:
    template: str
    seed: str
    what: str


@dataclass
class ValidationReport:
    checked: int = 0
    problems: list[Problem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def validate_instance(t: Template, inst: Instance, seed: str, iso: Isolation, rep: ValidationReport) -> None:
    rep.checked += 1
    for i, ref in enumerate(inst.references):
        g = grade_answer(inst, ref, iso)
        if not g.passed or g.events:
            rep.problems.append(Problem(t.id, seed, f"reference {i} fails: {g.reasons[:3]}"))
    for i, w in enumerate(inst.wrong):
        g = grade_answer(inst, w, iso)
        if g.passed:
            rep.problems.append(Problem(t.id, seed, f"wrong answer {i} passes"))
    for n in NULL_ANSWERS:
        g = grade_answer(inst, n, iso)
        if g.passed:
            rep.problems.append(Problem(t.id, seed, f"null answer {n!r} passes"))
    if inst.grader == "write_test":
        tests = extract(inst.references[0], inst.answer_kind).snippet
        for k, m in enumerate(inst.check["mutants"]):
            with TrialArea() as area:
                r = _run_with(iso, area, inst.check, m, tests)
            if r.exit_code == 0:
                rep.problems.append(Problem(t.id, seed, f"mutant {k} not caught by the reference tests"))
    if t.tool_mode:
        try:
            from edat.sidekick.tool_mode import grade_reference_in_tool_mode
        except ImportError:
            return                     # tool mode not built yet in this checkout
        g = grade_reference_in_tool_mode(inst, iso)
        if not g.passed:
            rep.problems.append(Problem(t.id, seed, f"tool-mode reference fails: {g.reasons[:3]}"))


def validate_templates(templates, trials: int = 3, master_seed: str = "edat-sidekick-validate",
                       iso: Isolation | None = None) -> ValidationReport:
    iso = iso or get_isolation()
    rep = ValidationReport()
    for t in templates:
        for trial in range(1, trials + 1):
            seed = f"{master_seed}|{t.id}|{trial}"
            try:
                inst = t.build(random.Random(seed))
                from edat.sidekick.gen.core import validate_instance as structural
                structural(inst, t)
            except Exception as exc:          # a broken template is a validation failure, not a crash
                rep.problems.append(Problem(t.id, seed, f"build failed: {type(exc).__name__}: {exc}"))
                continue
            validate_instance(t, inst, seed, iso, rep)
    return rep
