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

import ast
import json
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


# How a row was graded decides its label, not its rung number. A grading method joins this set only after
# its conversion has merged and passed its own adversarial review; "host_probes" is not in it yet.
HARDENED_GRADING: frozenset[str] = frozenset()


def row_hardened(rung: "Rung", metrics: dict) -> bool:
    """Whether one row's grade holds against code written to attack the grader: a sidekick rung, or a row
    graded by a method in HARDENED_GRADING (a row that failed before grading has no method, so it is not)."""
    return rung.source == "sidekick" or (metrics or {}).get("grading") in HARDENED_GRADING


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
                   tool: dict | None = None, time_limit_s: float = 20.0, notes: str = "",
                   probes: list[dict] | None = None, solution: dict[str, str] | None = None,
                   reference_names: tuple[str, ...] | None = None,
                   hand_probes: dict[str, list[dict]] | None = None) -> Instance:
    """One probe trial. `prompt` is the task; the answer-format line is added here. With `probes` (and the
    template's own `solution` files) the case is graded on the host (`probes.py`) and has no hidden test file."""
    check = {"targets": list(targets), "files": dict(files or {}), "hidden_tests": hidden_tests,
             "hidden_files": dict(hidden_files or {}), "visible_tests": dict(visible_tests or {}),
             "time_limit_s": time_limit_s}
    if reference_names is not None:
        # Rewrite the hand-written hidden tests into host-driven probes (rewrite.py). Every test the rewrite
        # refuses must have a hand conversion in `hand_probes`, keyed by its name; nothing is dropped silently.
        from edat.sidekick.ceiling.rewrite import rewrite
        auto, refused = rewrite(hidden_tests, reference_names=tuple(reference_names))
        hand = dict(hand_probes or {})
        missing = [str(r) for r in refused if r.test not in hand]
        if missing:
            raise ValueError("the rewrite refused, and no hand conversion is given: " + "; ".join(missing))
        unused = set(hand) - {r.test for r in refused}
        if unused:
            raise ValueError("hand conversions for tests the rewrite converts itself: " + ", ".join(sorted(unused)))
        probes = [dict(p, covers=p.get("covers", "all")) for p in auto]    # the rewrite carries every assertion
        for r in refused:
            probes += [dict(p, test=p.get("test") or r.test, hand=True) for p in hand[r.test]]
    if probes is not None:
        if not solution or set(solution) != set(targets):
            raise ValueError("host-driven probes need the solution's version of every target")
        json.dumps(probes)                                   # plain values only
        if not hidden_tests:
            raise ValueError("host-driven probes need the original hidden tests, to check that none is dropped")
        cov = coverage(hidden_tests, probes)
        if cov["unmapped"]:
            raise ValueError("the conversion drops part of the hidden tests: " + "; ".join(cov["unmapped"]))
        check.pop("hidden_tests")
        check.update(probes=list(probes), solution=dict(solution), coverage=cov)
    return Instance(prompt=prompt.rstrip() + "\n\n" + format_line(list(targets)), answer_kind="files",
                    grader="probe", check=check, references=list(references), wrong=list(wrong),
                    tool=tool, notes=notes)


# ------------------------------------------------------------------ the completeness gate

def _is_assertion(node) -> str | None:
    """The kind of check a statement or expression makes, or None."""
    if isinstance(node, ast.Assert):
        return "assert"
    if isinstance(node, ast.Raise) and node.exc is not None:
        exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
        if isinstance(exc, ast.Name) and exc.id == "AssertionError":
            return "raise AssertionError"
    if isinstance(node, ast.With):
        for it in node.items:
            if "raises" in ast.unparse(it.context_expr) or "warns" in ast.unparse(it.context_expr):
                return "pytest.raises"
    if isinstance(node, ast.Call):
        f = ast.unparse(node.func)
        if f in ("pytest.raises", "pytest.fail", "pytest.warns"):
            return f
    return None


def _units(fn) -> list[str]:
    out = []
    for node in ast.walk(fn):
        kind = _is_assertion(node)
        if kind and not (isinstance(node, ast.Call) and _inside_with_raises(fn, node)):
            out.append(f"{kind} at line {node.lineno}")
    return sorted(set(out), key=lambda u: int(u.rsplit(" ", 1)[1]))


def _inside_with_raises(fn, call) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.With) and any(it.context_expr is call for it in node.items):
            return True
    return False


TEST_PREFIX = "test"            # what pytest collects by default: any function whose name starts with it
_DYNAMIC = {"globals", "locals", "vars", "setattr", "exec", "eval", "__import__", "compile"}


def _is_parametrize(d) -> bool:
    return (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "parametrize"
            and isinstance(d.func.value, ast.Attribute) and d.func.value.attr == "mark"
            and isinstance(d.func.value.value, ast.Name) and d.func.value.value.id == "pytest" and len(d.args) == 2)


def _param_sets(fn, tree) -> int | None:
    """How many times pytest runs `fn`: the product of its parametrize lists' lengths, each given in place or
    by a module-level name bound once, and evaluated with no names (a literal, or sums and comprehensions of
    literals). None when the gate cannot count them."""
    n = 1
    for d in fn.decorator_list:
        if not _is_parametrize(d):
            continue                        # refused on its own by _unrecognised
        vals = d.args[1]
        if isinstance(vals, ast.Name):
            binds = [st.value for st in tree.body if isinstance(st, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == vals.id for t in st.targets)]
            vals = binds[0] if len(binds) == 1 else None
        if vals is None:
            return None
        try:                       # the template's own code (the host already runs it to draw the probes),
            got = eval(compile(ast.Expression(vals), "params", "eval"), {"__builtins__": {}})   # noqa: S307
        except Exception:          # with no names at all: a literal, sums and comprehensions of literals
            return None
        if not isinstance(got, (list, tuple)):
            return None
        n *= len(got)
    return n


def _unrecognised(tree, funcs) -> list[str]:
    """The gate fails closed: anything that could make pytest run a test the map does not see, or run an
    assertion other than through a direct call, is refused, even where no hidden file does it today."""
    out = []
    for st in tree.body:
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in st.decorator_list:
                if not (st.name.startswith(TEST_PREFIX) and _is_parametrize(d)):
                    out.append(f"{st.name}: the decorator at line {d.lineno} is not recognised")
            if st.name == "pytest_generate_tests":
                out.append("pytest_generate_tests makes tests the gate cannot see")
        elif isinstance(st, ast.ClassDef):
            if st.decorator_list:
                out.append(f"class {st.name}: its decorator is not recognised")
        else:
            bound = []
            if isinstance(st, ast.Assign):
                bound = [n.id for t in st.targets for n in ast.walk(t) if isinstance(n, ast.Name)]
            elif isinstance(st, (ast.AnnAssign, ast.AugAssign)):
                bound = [n.id for n in ast.walk(st.target) if isinstance(n, ast.Name)]
            elif isinstance(st, (ast.Import, ast.ImportFrom)):
                bound = [(a.asname or a.name).split(".")[0] for a in st.names]
            elif isinstance(st, (ast.For, ast.While, ast.With, ast.If, ast.Try)):
                bound = [n.id for n in ast.walk(st) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)]
                bound += [n.name for n in ast.walk(st) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
            out += [f"{b} at line {st.lineno} is a test name bound other than by def" for b in bound
                    if b.startswith(TEST_PREFIX) or b.startswith("Test") or b == "pytest_generate_tests"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _DYNAMIC:
            out.append(f"{node.func.id}() at line {node.lineno} can define tests the gate cannot see")
        if isinstance(node, ast.Attribute) and node.attr in ("modules", "__dict__"):
            out.append(f".{node.attr} at line {node.lineno} can define tests the gate cannot see")
    called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in funcs
                and id(node) not in called):
            out.append(f"{node.id} is used as a value at line {node.lineno}, so its assertions could run unseen")
    return out


def coverage(original: str, probes: list[dict]) -> dict:
    """Map the original hidden test file onto the probes. Each test function (and the module itself, for a
    check it makes at import time) has its assertions: its own and those of the helper functions it calls,
    directly or not (`_same`, `_run`). Each probe names the test it stands for ("test") and the
    assertions it carries ("covers": a list of indexes into that test's assertions, or "all" for a probe
    that repeats the whole test body on one input, as a generated or parametrised test does). A test with no
    probe, an assertion no probe covers, a test with no assertion at all, a class of tests, or a probe naming
    a test that is not there: each is "unmapped", and `probe_instance` refuses the case."""
    tree = ast.parse(original)
    funcs = {f.name: f for f in tree.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))}
    unmapped = [f"class {c.name}: tests in classes are not mapped" for c in tree.body
                if isinstance(c, ast.ClassDef) and c.name.startswith("Test")]
    unmapped += _unrecognised(tree, funcs)
    top = [n for n in tree.body if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    units: dict[str, list[str]] = {}
    mod_units = sorted({f"{k} at line {n.lineno}" for st in top for n in ast.walk(st) if (k := _is_assertion(n))})
    if mod_units:
        units["<module>"] = mod_units

    def helper_units(fn, seen) -> list[str]:
        out = []
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                h = funcs.get(node.func.id)
                if h is not None and not h.name.startswith(TEST_PREFIX) and h.name not in seen:
                    seen.add(h.name)
                    out += [f"{h.name}: {u}" for u in _units(h)] + helper_units(h, seen)
        return out

    for name, fn in funcs.items():
        if name.startswith(TEST_PREFIX):
            units[name] = _units(fn) + helper_units(fn, {name})
    per = {t: {"assertions": len(u), "probes": 0, "covered": set()} for t, u in units.items()}
    for i, p in enumerate(probes):
        t, cov = p.get("test"), p.get("covers")
        if t not in per:
            unmapped.append(f"probe {i} names {t!r}, which is not a test of the original file")
            continue
        per[t]["probes"] += 1
        n = len(units[t])
        if cov == "all":
            per[t]["covered"] |= set(range(n))
        elif isinstance(cov, list) and cov and all(isinstance(x, int) and 0 <= x < n for x in cov):
            per[t]["covered"] |= set(cov)
        else:
            unmapped.append(f"probe {i} ({t}) does not say which assertions it covers")
    for t, d in per.items():
        sets = _param_sets(funcs[t], tree) if t in funcs and funcs[t].decorator_list else 1
        if sets is None:
            unmapped.append(f"{t}: its parameters are not a literal list the gate can count")
        elif d["probes"] and d["probes"] < sets:
            unmapped.append(f"{t} has {sets} parameter sets but {d['probes']} probes")
        if not units[t]:
            unmapped.append(f"{t} has no assertion")
        elif not d["probes"]:
            unmapped.append(f"{t} has no probe")
        else:
            unmapped += [f"{t}: {units[t][k]} has no probe" for k in range(len(units[t])) if k not in d["covered"]]
    return {"tests": sum(1 for t in units if t != "<module>"), "assertions": sum(len(u) for u in units.values()),
            "probes": len(probes), "unmapped": unmapped,
            "map": {t: {"assertions": units[t], "probes": d["probes"]} for t, d in per.items()}}


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
