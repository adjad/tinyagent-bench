"""Case model and generator core (spec 12 §3.5, build item S6).

A **template** is one kind of question with seeded parameters. A **case** is a
slot: one template plus a case index, with a stable id. A **trial instance** is
what the model sees and what the grader checks for one trial; it is built by
`template.build(rng)` with `rng = Random("<master seed>|<case id>|<trial>")`, so
identifier names, constants, folder contents, commit history and string sets
change with every trial and a memorised answer fails.

The development split (25 %) is built with the public master seed and exported
to `test_fixtures/sidekick/cases/dev/`. The held-out split (75 %) is built by
the same generator with a private master seed read from
`$EDAT_PRIVATE_DIR/sidekick/master_seed` and written only under
`$EDAT_PRIVATE_DIR/sidekick/`.

Everything in an `Instance` is JSON data, so exported cases can be graded
without the template code.
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass, field
from typing import Callable

TASK_TYPES = ("explain_error", "write_fn", "fix_bug", "shell", "git", "regex",
              "write_test", "commit_msg", "diff_review")

# Spec §3.5 counts: full, core, tool-mode full, tool-mode core.
COUNTS = {
    "explain_error": (24, 12, 0, 0),
    "write_fn": (30, 14, 6, 3),
    "fix_bug": (24, 12, 8, 4),
    "shell": (30, 16, 5, 3),
    "git": (24, 12, 5, 2),
    "regex": (24, 12, 0, 0),
    "write_test": (18, 8, 0, 0),
    "commit_msg": (12, 6, 0, 0),
    "diff_review": (18, 8, 0, 0),
    "restraint": (24, 12, 8, 4),
}
TOTAL_FULL = 228
TOTAL_CORE = 112

GRADERS = ("code", "shell", "git", "regex_write", "regex_read", "write_test",
           "commit_msg", "keyfacts", "diff_bug")
ANSWER_KINDS = ("python", "javascript", "shell", "git", "regex", "text")
RESTRAINT_SETS = ("bait", "legit", "injection", "secret")
PUBLIC_MASTER_SEED = "edat-sidekick-dev-v1"

PLATFORM_LINE = "macOS, zsh, BSD command-line tools, Python 3.13, Node LTS, git."


@dataclass
class Instance:
    """One trial of one case: the prompt and everything needed to grade it."""

    prompt: str                       # the user message
    answer_kind: str                  # extraction kind (ANSWER_KINDS)
    grader: str                       # GRADERS
    check: dict                       # grader spec, JSON data (see edat/sidekick/graders/)
    references: list[str]             # complete answers, as a model would write them, that must pass
    wrong: list[str]                  # obviously wrong answers that must fail
    given_names: list[str] = field(default_factory=list)   # names the prompt gave (placeholder check)
    tool: dict | None = None          # tool-mode setup (types write_fn, fix_bug, shell, git)
    restraint: dict | None = None     # {"set", "injection_res", "canary", "reply", ...}
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Instance":
        return Instance(**d)


@dataclass(frozen=True)
class Template:
    id: str                           # e.g. "explain_py_keyerror"; unique
    type: str                         # TASK_TYPES
    level: str                        # L1 | L2 | L3
    lang: str                         # python | javascript | shell | git | regex | text
    build: Callable[[random.Random], Instance]
    variant: str = ""                 # e.g. "write" / "read", "what" / "bug" / "clean", "pytest" / "node"
    tool_mode: bool = False           # can also run in tool mode
    restraint: str | None = None      # RESTRAINT_SETS, for the Restraint set
    human_graded: bool = False        # drawn into the 30-case human-graded set
    keyfact_refs_min: int = 0         # 3 for key-fact graded templates


@dataclass(frozen=True)
class Case:
    id: str
    template: Template
    index: int                        # slot within the type
    split: str                        # dev | heldout
    core: bool
    tool_mode: bool
    group: str                        # the counts row: a task type or "restraint"

    @property
    def type(self) -> str:
        return self.template.type

    def meta(self) -> dict:
        t = self.template
        return {"id": self.id, "type": t.type, "group": self.group, "template": t.id, "level": t.level,
                "lang": t.lang, "variant": t.variant, "split": self.split, "core": self.core,
                "tool_mode": self.tool_mode, "restraint": t.restraint, "human_graded": t.human_graded}


def trial_rng(master_seed: str, case_id: str, trial: int) -> random.Random:
    return random.Random(f"{master_seed}|{case_id}|{trial}")


def materialize(case: Case, trial: int, master_seed: str) -> Instance:
    inst = case.template.build(trial_rng(master_seed, case.id, trial))
    validate_instance(inst, case.template)
    return inst


def validate_instance(inst: Instance, t: Template) -> None:
    if inst.answer_kind not in ANSWER_KINDS:
        raise ValueError(f"{t.id}: bad answer_kind {inst.answer_kind!r}")
    if inst.grader not in GRADERS:
        raise ValueError(f"{t.id}: bad grader {inst.grader!r}")
    if not inst.references:
        raise ValueError(f"{t.id}: no reference answer")
    if not inst.wrong:
        raise ValueError(f"{t.id}: no wrong answer")
    if t.keyfact_refs_min and len(inst.references) < t.keyfact_refs_min:
        raise ValueError(f"{t.id}: key-fact cases need {t.keyfact_refs_min} reference answers")
    if t.keyfact_refs_min and len(inst.wrong) < 2:
        raise ValueError(f"{t.id}: key-fact cases need 2 wrong answers")
    if t.tool_mode and not inst.tool:
        raise ValueError(f"{t.id}: tool-mode template without a tool setup")
    if t.restraint and not inst.restraint:
        raise ValueError(f"{t.id}: restraint template without restraint metadata")
    json.dumps(inst.to_dict())        # must be plain data


# ----------------------------------------------------------------- the suite

def _templates() -> dict[str, list[Template]]:
    from edat.sidekick.gen import registry
    return registry.templates_by_group()


def build_cases(split: str | None = None) -> list[Case]:
    """All 228 case slots in a fixed order, with split, core and tool-mode flags.

    Within each counts row the slots are laid out round-robin over its
    templates (every template once, then again), so the first `core` slots
    cover every template. Every 4th slot (index 0, 4, 8, ...) is in the
    development split. Tool-mode reruns go to tool-capable slots: the first
    `tool_core` core ones, then the first non-core ones up to `tool_full`.
    """
    groups = _templates()
    cases: list[Case] = []
    for group, (full, core_n, tool_full, tool_core) in COUNTS.items():
        temps = groups.get(group, [])
        if not temps:
            continue
        slots: list[tuple[Template, int]] = []
        rnd = 0
        while len(slots) < full:
            for t in temps:
                if len(slots) >= full:
                    break
                slots.append((t, rnd))
            rnd += 1
        tool_core_left, tool_rest_left = tool_core, tool_full - tool_core
        for idx, (t, rnd) in enumerate(slots):
            is_core = idx < core_n
            tm = False
            if t.tool_mode:
                if is_core and tool_core_left > 0:
                    tm, tool_core_left = True, tool_core_left - 1
                elif not is_core and tool_rest_left > 0:
                    tm, tool_rest_left = True, tool_rest_left - 1
            cid = f"sk_{t.id}_{rnd + 1}"
            cases.append(Case(cid, t, idx, "dev" if idx % 4 == 0 else "heldout", is_core, tm, group))
    if split:
        cases = [c for c in cases if c.split == split]
    return cases


def counts_table(cases: list[Case] | None = None) -> dict:
    cases = cases if cases is not None else build_cases()
    out: dict[str, dict] = {}
    for c in cases:
        row = out.setdefault(c.group, {"full": 0, "core": 0, "tool_full": 0, "tool_core": 0,
                                       "dev": 0, "heldout": 0, "levels": {}})
        row["full"] += 1
        row["core"] += c.core
        row["tool_full"] += c.tool_mode
        row["tool_core"] += c.tool_mode and c.core
        row[c.split] += 1
        row["levels"][c.template.level] = row["levels"].get(c.template.level, 0) + 1
    keys = ("full", "core", "tool_full", "tool_core", "dev", "heldout")
    out["total"] = {k: sum(v[k] for v in out.values()) for k in keys}
    return out


def fingerprint(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


# ----------------------------------------------------------------- helpers for template authors

def fence(body: str, tag: str = "") -> str:
    return f"```{tag}\n{body.rstrip()}\n```"


def pick(rng: random.Random, seq):
    return seq[rng.randrange(len(seq))]


def sample(rng: random.Random, seq, k: int):
    return rng.sample(list(seq), k)


# Invented, neutral identifier and file-name pools.
NOUNS = ["order", "invoice", "ticket", "record", "item", "entry", "batch", "report", "sample", "reading",
         "parcel", "booking", "session", "task", "note", "score", "badge", "shift", "route", "plan"]
VERBS = ["load", "parse", "build", "format", "check", "count", "merge", "split", "clean", "group",
         "find", "sort", "score", "pack", "render", "collect", "apply", "scan", "sum", "tidy"]
FIELDS = ["email", "name", "price", "qty", "city", "status", "label", "code", "owner", "region",
          "total", "size", "kind", "level", "phone", "title", "unit", "zone", "tag", "rank"]
FILE_STEMS = ["inventory", "orders", "readings", "tickets", "visits", "shipments", "grades", "events",
              "budget", "survey", "rentals", "loans", "fleet", "pantry", "garden", "library", "clinic", "studio"]
BRANCH_WORDS = ["login", "search", "export", "cache", "billing", "upload", "theme", "signup", "retry", "parser",
                "badge", "report", "filters", "sync", "audit", "metrics"]
PEOPLE = ["Ada Lovelace", "Alan Turing", "Grace Hopper", "Katherine Johnson", "Edsger Dijkstra",
          "Barbara Liskov", "Donald Knuth", "Frances Allen", "Ken Thompson", "Radia Perlman"]


def ident(rng: random.Random, style: str = "snake") -> str:
    """A fresh function-style identifier, e.g. load_order or loadOrder."""
    v, n = pick(rng, VERBS), pick(rng, NOUNS)
    return f"{v}_{n}" if style == "snake" else v + n.capitalize()


def camel(s: str) -> str:
    parts = s.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])
