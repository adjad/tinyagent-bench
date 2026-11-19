"""The ceiling ladder: nine rungs of coding difficulty, easiest first.

Rungs 1-3 are the sidekick tier's core cases (spec 12) at levels L1, L2 and L3,
without the Restraint set (that measures safety, not ability; it is reported
apart). Rungs 4-9 are probes written for this ladder. They are reported apart
from the Sidekick Score and never enter it.

Ceiling (one model entry: a model in one thinking mode):

* a case **holds** when it passes on a majority of its trials (2 of 3; 1 of 1);
* a rung **holds** when at least half of its cases hold;
* the **ceiling** is the highest rung that holds (0 when none does);
* **solid up to** is the highest rung R such that every rung from 1 to R holds,
  so a lucky pass high up does not hide a gap lower down.

The pass rate of every rung (passed trials / trials) is reported with it, so
the fall-off is visible.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from edat.sidekick.gen.core import Instance, Template, fence

LADDER_SEED_PUBLIC = "edat-sidekick-ceiling-dev-v1"


@dataclass(frozen=True)
class Rung:
    n: int
    id: str
    title: str              # plain words, for the report
    source: str             # "sidekick" (existing cases at `level`) or "probe" (templates here)
    level: str | None = None
    mode: str = "answer_only"   # answer_only | tool

    @property
    def hardened(self) -> bool:
        """Whether this rung's grading holds against code written to attack the grader. The probe rungs do
        not yet: see NOT_HARDENED."""
        return self.source == "sidekick"


NOT_HARDENED = "not hardened against adversarial model code"
NOT_HARDENED_WHY = ("Rungs 4 to 9 are " + NOT_HARDENED + ": their hidden tests run in the same process as the "
                    "model's code, so a model would have to attack the grader on purpose (read the reference "
                    "from the test process, patch pytest, fake the exit code) to pass one. An honest wrong "
                    "answer cannot pass by accident. Until they are rebuilt they stay out of any published or "
                    "ranked score; code that touches the test machinery is flagged for a person to read. "
                    "On rungs 1 to 3 the labelled sidekick tasks (git, written tests, explanations matched by "
                    "key facts, commit messages, the shell and diff cases noted in the run sheet) carry the same label, so "
                    "rungs 1 to 3 (and the ceiling) are worked out without them.")


RUNGS = (
    Rung(1, "sk-l1", "Simple one-step tasks: a short function, one shell command, a regex", "sidekick", "L1"),
    Rung(2, "sk-l2", "Everyday helper tasks: fix a bug, a regex, a commit message", "sidekick", "L2"),
    Rung(3, "sk-l3", "The harder helper tasks: edge cases and tricky commands", "sidekick", "L3"),
    Rung(4, "rules", "A function with several rules that interact", "probe"),
    Rung(5, "state", "A small class that keeps track of state", "probe"),
    Rung(6, "data", "A short script that reads a data file and reports on it", "probe"),
    Rung(7, "twobugs", "Find and fix two bugs in a 60 to 100 line file", "probe"),
    Rung(8, "twofiles", "A small change that has to be made in two files", "probe"),
    Rung(9, "agent", "Work with tools: read the code, edit it, run the tests, fix what fails", "probe", mode="tool"),
)
RUNG_BY_ID = {r.id: r for r in RUNGS}
PROBE_RUNGS = tuple(r for r in RUNGS if r.source == "probe")


# ------------------------------------------------------------------ authoring helpers

def show_file(name: str, text: str) -> str:
    """A given file as the prompt shows it."""
    return f"`{name}`:\n\n" + fence(text.rstrip("\n") + "\n", "python")


def answer_files(files: dict[str, str], *, style: str = "comment") -> str:
    """A complete answer giving whole files, as a model would write it.
    style "comment": `# file: name` as each block's first line; "label": the name on the line before."""
    parts = []
    for name, text in files.items():
        body = text.rstrip("\n") + "\n"
        if style == "comment" and len(files) > 1:
            parts.append(fence(f"# file: {name}\n" + body, "python"))
        elif style == "label":
            parts.append(f"`{name}`:\n\n" + fence(body, "python"))
        else:
            parts.append(fence(body, "python"))
    return "Here is the code.\n\n" + "\n\n".join(parts)


def format_line(targets: list[str]) -> str:
    if len(targets) == 1:
        return f"Reply with the whole `{targets[0]}` file in one ```python code block."
    names = ", ".join(f"`{t}`" for t in targets)
    return (f"Reply with each changed file whole ({names}), each in its own ```python code block "
            "whose first line is `# file: <name>`.")


def probe_instance(*, prompt: str, targets: list[str], hidden_tests: str, references: list[str],
                   wrong: list[str], files: dict[str, str] | None = None,
                   hidden_files: dict[str, str] | None = None, visible_tests: dict[str, str] | None = None,
                   tool: dict | None = None, time_limit_s: float = 20.0, notes: str = "") -> Instance:
    """One probe trial. `prompt` is the task; the answer-format line is added here."""
    check = {"targets": list(targets), "files": dict(files or {}), "hidden_tests": hidden_tests,
             "hidden_files": dict(hidden_files or {}), "visible_tests": dict(visible_tests or {}),
             "time_limit_s": time_limit_s}
    return Instance(prompt=prompt.rstrip() + "\n\n" + format_line(list(targets)), answer_kind="files",
                    grader="probe", check=check, references=list(references), wrong=list(wrong),
                    tool=tool, notes=notes)


def probe_template(rung: str, tid: str, build) -> Template:
    if rung not in RUNG_BY_ID or RUNG_BY_ID[rung].source != "probe":
        raise ValueError(f"unknown probe rung {rung!r}")
    return Template(id=tid, type=rung, level=f"R{RUNG_BY_ID[rung].n}", lang="python", build=build,
                    tool_mode=RUNG_BY_ID[rung].mode == "tool")


# ------------------------------------------------------------------ cases

@dataclass(frozen=True)
class LadderCase:
    id: str
    rung: Rung
    template: Template | None          # probe rungs
    sidekick_case: object | None       # sidekick rungs: an edat.sidekick.gen.core.Case

    def meta(self, split: str) -> dict:
        if self.sidekick_case is not None:
            return self.sidekick_case.meta()
        return {"id": self.id, "type": self.rung.id, "group": "ceiling", "template": self.template.id,
                "level": f"R{self.rung.n}", "lang": "python", "variant": "", "split": split, "core": True,
                "tool_mode": self.rung.mode == "tool", "restraint": None, "human_graded": False}


def probe_templates() -> dict[str, list[Template]]:
    from edat.sidekick.ceiling import t_agent, t_data, t_rules, t_state, t_twobugs, t_twofiles
    out: dict[str, list[Template]] = {}
    for mod in (t_rules, t_state, t_data, t_twobugs, t_twofiles, t_agent):
        for t in mod.TEMPLATES:
            out.setdefault(t.type, []).append(t)
    ids = [t.id for ts in out.values() for t in ts]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise ValueError(f"duplicate ladder template ids: {sorted(dup)}")
    return out


def _scored_case(case) -> bool:
    """Whether a sidekick case's grading is on the allow-list (the label is the same on every seed)."""
    from edat.sidekick.labels import grading_label
    inst = case.template.build(random.Random(f"scored|{case.template.id}"))
    return bool(grading_label(inst.grader, inst.check)["grade_hardened"])


def ladder_cases(rungs: tuple[str, ...] | None = None) -> list[LadderCase]:
    """Every case of the selected rungs (default all), rung order then id."""
    from edat.sidekick.gen.core import build_cases
    want = set(rungs or RUNG_BY_ID)
    out: list[LadderCase] = []
    # Rungs 1 to 3 are worked out from scored rows only (labels.may_score), so their samples are drawn from
    # the cases whose grading is on the allow-list; a labelled case would spend a trial that cannot count.
    sk = [c for c in build_cases(None) if c.core and c.group != "restraint" and _scored_case(c)]
    by_t = probe_templates()
    for r in RUNGS:
        if r.id not in want:
            continue
        if r.source == "sidekick":
            out += [LadderCase(c.id, r, None, c) for c in sk if c.template.level == r.level]
        else:
            out += [LadderCase(f"lad_{r.id}_{t.id}", r, t, None) for t in by_t.get(r.id, [])]
    return out


def materialize_probe(case: LadderCase, trial: int, master_seed: str) -> Instance:
    from edat.sidekick.gen.core import validate_instance
    inst = case.template.build(random.Random(f"{master_seed}|{case.id}|{trial}"))
    validate_instance(inst, case.template)
    return inst


def fill(text: str, **values) -> str:
    """Replace @NAME@ markers (keeps code free of f-string brace escaping)."""
    import re
    for k, v in values.items():
        text = text.replace(f"@{k}@", str(v))
    left = re.findall(r"@[A-Z][A-Z0-9_]*@", text)
    if left:
        raise ValueError(f"unfilled markers: {sorted(set(left))}")
    return text
