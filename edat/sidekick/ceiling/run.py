"""Run one model entry (a model in one thinking mode) up the ceiling ladder.

    python -m edat.sidekick.ceiling preflight --out OUT [--engine] [--weights]
    python -m edat.sidekick.ceiling run --entry '<engine id>|thinking-on' --plan smoke|first-pass|full --out OUT
    python -m edat.sidekick.ceiling plan [--measured OUT]

Plans:

* **smoke**: 2 cases from each rung (1-9), 1 trial. About 10 minutes on one model.
* **first-pass**: answer-only mode only (rungs 1-8; rung 9 needs tool mode). Pass A: every rung,
  12 cases a rung (the sidekick rungs are sampled evenly across task types), 1 trial, to find the
  ceiling. Pass B: trials 2 and 3 on the ceiling rung and the rungs either side of it.
* **full**: the sidekick core set (all 112 answer-only cases, Restraint set included, and the 16
  tool-mode cases) and every probe rung (4-9), 3 trials each.

Every model-written line runs only inside the sealed sandbox: the run starts with the sandbox
self-test and refuses to run if it fails. One model entry per command. Results go to
`OUT/<entry>/` (a results-store folder: `runs/ceiling-<entry>-<suite>/`), never into the
repository. Re-running the same command resumes: trials already recorded are skipped.

The run stops itself, between trials, when free memory falls under 15% (`memory_pressure -Q`),
when over 2% of the trials so far were excluded for engine or transport faults (from 50 trials
on), when the file `OUT/STOP` exists, or after `--max-minutes`. `OUT/<entry>/progress.json` says
why it stopped and what is left.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from edat.models import EngineError, NullClient, Sampling, ScriptedClient
from edat.results.writer import RunWriter, utc_now
from edat.sidekick import TIER
from edat.sidekick.ceiling.models import Entry, LocalModel, find_entry, model_dir_from_settings, ordered_entries, scan
from edat.sidekick.ceiling.rungs import (LADDER_SEED_PUBLIC, RUNG_BY_ID, RUNGS, LadderCase, ladder_cases,
                                         materialize_probe, row_hardened)
from edat.sidekick.extract import visible_answer
from edat.sidekick.gen.core import PUBLIC_MASTER_SEED, materialize
from edat.sidekick.graders import grade_answer, grading_label
from edat.sidekick.graders.common import GradeResult
from edat.sidekick.records import harness_commit, make_record
from edat.sidekick.restraint import event_record
from edat.sidekick.sandbox.run import (SandboxUnavailable, TrialArea, get_isolation, leftover_count,
                                       leftover_events, leftovers_over_cap)
from edat.sidekick.sandbox.selftest import require_sandbox
from edat.sidekick.tools_dev import SYSTEM_PROMPT

TRIALS = 3
SAMPLE_PER_RUNG = 12
SMOKE_PER_RUNG = 2
MEMORY_FLOOR_PCT = 15
EXCLUDED_LIMIT = 0.02
EXCLUDED_FROM = 50
# Output budgets (max_tokens) per answer or tool step. Thinking needs room to think; an answer
# cut off at the budget fails as "too_long".
BUDGET = {False: {"answer_only": 1536, "ladder": 4096, "tool": 1024},
          True: {"answer_only": 8192, "ladder": 12288, "tool": 4096}}
LADDER_SYSTEM_PROMPT = SYSTEM_PROMPT.replace("put it in one fenced code block with a language tag",
                                             "put each file in its own fenced code block with a language tag")
SUITE_OF = {"sidekick": "answer_only", "sidekick_tool": "tool_mode", "probe": "ladder", "agent": "ladder_tool"}


# ------------------------------------------------------------------ units of work

@dataclass(frozen=True)
class Unit:
    case: LadderCase
    suite: str            # answer_only | tool_mode | ladder | ladder_tool
    trial: int


def _spread(cases: list[LadderCase], n: int) -> list[LadderCase]:
    """n cases spread evenly across task types (round-robin by type, ids sorted)."""
    by_type: dict[str, list[LadderCase]] = {}
    for c in sorted(cases, key=lambda c: c.id):
        by_type.setdefault(c.sidekick_case.type if c.sidekick_case else c.rung.id, []).append(c)
    out, i = [], 0
    while len(out) < min(n, len(cases)):
        for t in sorted(by_type):
            if i < len(by_type[t]) and len(out) < n:
                out.append(by_type[t][i])
        i += 1
    return out


def _suite(case: LadderCase, tool: bool = False) -> str:
    if case.rung.source == "sidekick":
        return "tool_mode" if tool else "answer_only"
    return "ladder_tool" if case.rung.mode == "tool" else "ladder"


ANSWER_ONLY_RUNGS = tuple(r.n for r in RUNGS if r.mode == "answer_only")      # 1-8
QUICK_RUNGS = tuple(r.n for r in RUNGS)                                         # 1-9: rung 9 only when asked for


@dataclass(frozen=True)
class Quick:
    """A quick look: one trial, a few cases a rung. The choices are flags, not a fixed recipe. By default it
    takes the answer-only rungs 1-8; rung 9 (tool mode) runs only when `--rungs` names it."""
    per_rung: int = 4
    rungs: tuple[int, ...] = ANSWER_ONLY_RUNGS
    thinking: str = "off"              # "off": thinking off for every model; "shipped": each in the mode it ships in

    def __post_init__(self):
        if self.per_rung < 1:
            raise ValueError("--per-rung must be at least 1")
        bad = [n for n in self.rungs if n not in QUICK_RUNGS]
        if bad or not self.rungs:
            raise ValueError(f"a quick look takes rungs {QUICK_RUNGS[0]}-{QUICK_RUNGS[-1]}"
                             + (f"; not {bad}" if bad else ""))
        if self.thinking not in ("off", "shipped"):
            raise ValueError("--thinking is off or shipped")

    def to_dict(self) -> dict:
        return {"per_rung": self.per_rung, "rungs": list(self.rungs), "thinking": self.thinking, "trials": 1,
                "mode": "answer_only" if self.tool_rungs() == () else "answer_only+tool"}

    def tool_rungs(self) -> tuple[int, ...]:
        return tuple(n for n in self.rungs if n not in ANSWER_ONLY_RUNGS)


def parse_rungs(text: str) -> tuple[int, ...]:
    """'1-8', '1,2,3,5' or '4-6,8'."""
    out: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out += list(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return tuple(sorted(set(out)))


def add_quick_args(ap: argparse.ArgumentParser) -> None:
    g = ap.add_argument_group("quick look (with --plan quick)")
    g.add_argument("--per-rung", type=int, default=Quick.per_rung, help="cases a rung (default 4)")
    g.add_argument("--rungs", default=f"{ANSWER_ONLY_RUNGS[0]}-{ANSWER_ONLY_RUNGS[-1]}",
                   help="rungs to include, e.g. 1-8 (default: the answer-only rungs), 1,3,5-8, or 1-9 to add the "
                        "tool-mode rung 9 (adds roughly a quarter to the time)")
    g.add_argument("--thinking", choices=("off", "shipped"), default=Quick.thinking,
                   help="off: thinking off for every model (default); shipped: each model in the mode it ships in")


def quick_from_args(a) -> Quick:
    return Quick(per_rung=a.per_rung, rungs=parse_rungs(a.rungs), thinking=a.thinking)


def results_dir(out: Path, plan: str) -> Path:
    """Smoke, quick-look and full-plan results sit in their own folders, apart from the first pass (in OUT
    itself), so no plan resumes from another plan's trials."""
    return out / plan if plan in ("smoke", "quick", "full") else out


def quick_entries(models: list[LocalModel], q: Quick) -> list[Entry]:
    """One entry a model (standard quantisations only), references first, then smallest first."""
    out = []
    for m in models:
        if not m.standard:
            continue
        th = False if q.thinking == "off" else m.thinking[0]
        out.append(next(e for e in m.entries() if e.thinking == th and e.primary))
    from edat.sidekick.ceiling.models import FIRST
    rank = {mid: i for i, mid in enumerate(FIRST)}
    return sorted(out, key=lambda e: (rank.get(e.model.engine_id, len(FIRST)), e.model.disk_gb, e.key))


def plan_units(plan: str, *, done_ceiling: int | None = None, trials: int = TRIALS,
               quick: Quick | None = None) -> list[Unit]:
    """The units of a plan, in order. first-pass pass B needs the ceiling from pass A."""
    if plan == "quick":
        q = quick or Quick()
        return [Unit(c, _suite(c), 1) for r in RUNGS if r.n in q.rungs
                for c in _spread(ladder_cases((r.id,)), q.per_rung)]
    if plan == "smoke":
        out = []
        for r in RUNGS:
            for c in _spread([c for c in ladder_cases((r.id,))], SMOKE_PER_RUNG):
                out.append(Unit(c, _suite(c), 1))
        return out
    if plan == "first-pass":
        picked = {r.id: _spread(ladder_cases((r.id,)), SAMPLE_PER_RUNG) for r in RUNGS if r.mode == "answer_only"}
        if done_ceiling is None:
            return [Unit(c, _suite(c), 1) for r in RUNGS if r.id in picked for c in picked[r.id]]
        top = max(r.n for r in RUNGS if r.id in picked)
        around = ({1, 2} if done_ceiling < 1 else      # nothing held: the two easiest rungs get the repeats
                  {n for n in (done_ceiling - 1, done_ceiling, done_ceiling + 1) if 1 <= n <= top})
        return [Unit(c, _suite(c), t) for r in RUNGS if r.n in around for c in picked[r.id]
                for t in range(2, trials + 1)]
    if plan == "full":
        from edat.sidekick.gen.core import build_cases
        out = []
        core = [c for c in build_cases(None) if c.core]
        sk = [LadderCase(c.id, RUNG_BY_ID[f"sk-{c.template.level.lower()}"], None, c) for c in core]
        for c in sk:
            out += [Unit(c, "answer_only", t) for t in range(1, trials + 1)]
        for c in sk:
            if c.sidekick_case.tool_mode:
                out += [Unit(c, "tool_mode", t) for t in range(1, trials + 1)]
        for c in ladder_cases(tuple(r.id for r in RUNGS if r.source == "probe")):
            out += [Unit(c, _suite(c), t) for t in range(1, trials + 1)]
        return out
    raise ValueError(f"unknown plan {plan!r}")


# ------------------------------------------------------------------ seeds and instances

def private_dir() -> Path | None:
    p = os.environ.get("EDAT_PRIVATE_DIR")
    return Path(p).expanduser() if p else None


def master_seed(split: str) -> str:
    if split == "dev":
        return PUBLIC_MASTER_SEED
    p = private_dir()
    f = p / "sidekick" / "master_seed" if p else None
    if not f or not f.exists():
        raise RuntimeError("held-out sidekick cases need $EDAT_PRIVATE_DIR/sidekick/master_seed (run export-heldout)")
    return f.read_text().strip()


def seed_fingerprints(plan_has_heldout_cases: bool) -> dict[str, str]:
    """Short sha256 fingerprints of the seeds a run uses: the ladder's (public or private) and, for held-out
    sidekick cases, the private sidekick seed. A results folder is bound to them (`bind_seeds`)."""
    import hashlib
    fp = lambda s: hashlib.sha256(s.encode()).hexdigest()[:16]   # noqa: E731
    seed, split = ladder_seed()
    out = {"ladder": f"{split}:{fp(seed)}"}
    if plan_has_heldout_cases:
        out["sidekick"] = f"heldout:{fp(master_seed('heldout'))}"
    return out


def bind_seeds(folder: Path, seeds: dict[str, str]) -> None:
    """The first run in a results folder records its seeds' fingerprints; a later run with other seeds (a
    replaced private seed, or public instead of private) refuses to resume there: its trials would be
    different instances under the same case ids and the same "heldout" label."""
    f = folder / "seeds.json"
    folder.mkdir(parents=True, exist_ok=True)
    if f.exists():
        had = json.loads(f.read_text())
        if had != seeds:
            raise SystemExit(f"{folder} holds results made with other seeds ({had}); this run's are {seeds}. "
                             "Use a new results folder; nothing was run.")
        return
    f.write_text(json.dumps(seeds, indent=1) + "\n")


def ladder_seed() -> tuple[str, str]:
    """(seed, split): the private ladder seed when there is one (written on first use), else the public one."""
    p = private_dir()
    if not p:
        return LADDER_SEED_PUBLIC, "dev"
    f = p / "sidekick" / "ceiling_seed"
    if not f.exists():
        import secrets
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(secrets.token_hex(16) + "\n")
    return f.read_text().strip(), "heldout"


def instance(unit: Unit):
    c = unit.case
    if c.sidekick_case is not None:
        sc = c.sidekick_case
        return materialize(sc, unit.trial, master_seed(sc.split)), sc.split
    seed, split = ladder_seed()
    return materialize_probe(c, unit.trial, seed), split


def trial_seed(case_id: str, trial: int) -> int:
    import hashlib
    return int(hashlib.sha256(f"ceiling|{case_id}|{trial}".encode()).hexdigest()[:8], 16)


# ------------------------------------------------------------------ one trial

def _chat(client, messages, **kw):
    last = None
    for _ in range(2):
        try:
            return client.chat(messages, **kw), None
        except EngineError as exc:
            last = f"{exc.kind}: {exc.detail}"
            if not exc.retryable:
                break
    return None, last


def classify(g, finish_reason: str | None = None) -> str | None:
    if g.passed:
        return None
    if g.events:
        return "unsafe"
    mode = g.metrics.get("failure_mode")
    if finish_reason == "length" and mode in (None, "no_code", "wrong_files", "syntax", "logic"):
        return "too_long"
    if mode:
        return mode
    if g.metrics.get("extraction_failed"):
        return "no_code"
    if g.metrics.get("blocked"):
        return "unsafe"
    if g.metrics.get("placeholder"):
        return "instruction"
    return "logic"


def answer_trial(client, inst, *, suite: str, seed: int, thinking: bool, sampling, iso) -> tuple[dict, dict, list, str, str | None]:
    system = LADDER_SYSTEM_PROMPT if suite == "ladder" else SYSTEM_PROMPT
    budget = BUDGET[thinking]["ladder" if suite == "ladder" else "answer_only"]
    messages = [{"role": "system", "content": system}, {"role": "user", "content": inst.prompt}]
    t0 = time.monotonic()
    res, err = _chat(client, messages, tools=None, sampling=sampling, seed=seed, max_tokens=budget, thinking=thinking)
    model_s = time.monotonic() - t0
    if res is None:
        return {}, {"error": err}, [], "excluded", f"engine_error: {err}"
    text = res.text or ""
    g = _guarded(lambda: grade_answer(inst, text, iso))
    usage = res.usage.to_record()
    m = {"pass": g.passed, "failure_mode": classify(g, res.finish_reason), "finish_reason": res.finish_reason,
         "time_s": round(model_s, 3), "completion_tokens": usage.get("completion_tokens"),
         "reasoning_tokens": usage.get("reasoning_tokens"), "max_tokens": budget, "model_calls": 1,
         "reasoned": bool((res.reasoning or "").strip() or (usage.get("reasoning_tokens") or 0) > 0
                          or re.search(r"<(think|thinking|reasoning)>", text, re.I))}
    for k in ("partial", "hidden_passed", "hidden_total", "grade_hardened", "grading", "tripwire"):
        if k in g.metrics:
            m[k] = g.metrics[k]
    raw = {"prompt_head": inst.prompt[:600], "answer": visible_answer(text)[:20000],
           "reasoning": (res.reasoning or "")[:20000], "grade": g.to_record(), "usage": usage,
           "timing": res.timing.to_record()}
    return m, raw, g.events, "ok", None


def tool_trial(client, inst, *, suite: str, seed: int, thinking: bool, sampling, iso) -> tuple[dict, dict, list, str, str | None]:
    budget = BUDGET[thinking]["tool"]
    t0 = time.monotonic()
    from edat.sidekick.tool_mode import ToolTrial
    tt, env = ToolTrial(), None
    if suite == "ladder_tool":
        from edat.sidekick.ceiling.agent import run_agent_trial

        def trial():
            nonlocal tt, env
            g_, tt, env = run_agent_trial(client, inst, iso, max_tokens=budget, thinking=thinking,
                                          sampling=sampling, seed=seed)
            return g_
    else:
        from edat.sidekick.tool_mode import grade_tool_trial, prepare, run_tool_trial

        def trial():
            nonlocal tt, env
            with TrialArea() as area:
                env, before = prepare(iso, area, inst)
                tt = run_tool_trial(client, inst, env, max_tokens=budget, thinking=thinking, sampling=sampling,
                                    seed=seed)
                return None if tt.excluded else grade_tool_trial(iso, area, inst, env, before, tt)
    g = _guarded(trial)
    wall = time.monotonic() - t0
    if g is None:
        return {}, {"error": tt.engine_error}, [], "excluded", f"engine_error: {tt.engine_error}"
    if not g.passed and not g.metrics.get("failure_mode"):
        stops = [t.stop for t in tt.turns]
        if g.events:
            g.metrics["failure_mode"] = "unsafe"
        elif g.metrics.get("format_failure"):
            g.metrics["failure_mode"] = "tool_format"
        elif stops and stops[-1] in ("max_calls", "max_steps"):
            g.metrics["failure_mode"] = "tool_loop"
    steps = sum(len(t.steps) for t in tt.turns)
    m = {"pass": g.passed, "failure_mode": classify(g), "time_s": round(wall, 3), "model_calls": steps,
         "max_tokens": budget}
    for k in ("tool_calls", "refused_calls", "malformed_calls", "hallucinated_tools", "partial", "hidden_total",
              "grade_hardened", "grading", "tripwire"):
        if k in g.metrics:
            m[k] = g.metrics[k]
    raw = {"prompt_head": inst.prompt[:600], "final_text": tt.final_text[:5000],
           "turns": [t.to_record() for t in tt.turns], "grade": g.to_record(),
           "commands": [c.__dict__ for c in env.commands][:40] if env else []}
    return m, raw, g.events, "ok", None


def _toolchain_key() -> dict | None:
    """The versions of the Python, Node and git that model code and the graders run with (no paths)."""
    from edat.sidekick.toolchain import ToolchainError, find_toolchain
    try:
        return find_toolchain().run_key()
    except ToolchainError:
        return None


def _guarded(grade):
    """Run a trial's grading; an error in it fails that trial (`grader_error`) instead of ending the run.
    A broken sandbox is not the answer's doing and still stops everything."""
    try:
        return grade()
    except SandboxUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        g = GradeResult(False, [f"the grader could not finish: {exc.__class__.__name__}: {str(exc)[:200]}"])
        g.metrics["failure_mode"] = "grader_error"
        return g


# ------------------------------------------------------------------ host checks

def memory_free_pct() -> int | None:
    try:
        out = subprocess.run(["memory_pressure", "-Q"], capture_output=True, text=True, timeout=20).stdout
    except Exception:  # noqa: BLE001
        return None
    m = re.search(r"free percentage:\s*(\d+)%", out)
    return int(m.group(1)) if m else None


def device_block(device_id: str) -> dict:
    ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    try:
        ver = subprocess.run(["sw_vers", "-productVersion"], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001
        ver = ""
    return {"id": device_id, "class": "mac", "ram_bytes": int(ram), "os": f"macOS {ver}".strip()}


def engine_check(model: LocalModel, base_url: str) -> str | None:
    """Read-only: the engine lists the id and no other chat model is loaded. None when fine."""
    import httpx
    from scripts.productbench.engine_prep import _headers, is_aux
    try:
        with httpx.Client(base_url=base_url, headers=_headers(), timeout=30) as c:
            ids = [m.get("id") for m in c.get("/v1/models").json().get("data", [])]
            status = c.get("/v1/models/status").json().get("models", [])
    except Exception as exc:  # noqa: BLE001
        return f"the engine at {base_url} did not answer ({exc.__class__.__name__})"
    if model.engine_id not in ids:
        return f"the engine does not list {model.engine_id}"
    others = [m["id"] for m in status if m.get("loaded") and m.get("id") != model.engine_id and not is_aux(m["id"])]
    if others:
        return f"other chat models are loaded ({', '.join(others)}): run engine_prep keep {model.engine_id} first"
    return None


# ------------------------------------------------------------------ clients

class OracleClient(ScriptedClient):
    """Answers with each case's reference (tests and dry runs only): the ladder must pass under it."""

    def __init__(self):
        super().__init__([], name="ceiling-oracle")
        self.by_prompt: dict[str, object] = {}

    def learn(self, inst) -> None:
        self.by_prompt[inst.prompt] = inst
        if inst.tool and inst.tool.get("kind") == "probe":
            from edat.sidekick.ceiling.agent import tool_task_prompt
            self.by_prompt[tool_task_prompt(inst)] = inst

    def _turn(self, messages, tools, index):
        user = next(m["content"] for m in messages if m["role"] == "user")
        inst = next((i for p, i in self.by_prompt.items() if user.startswith(p)), None)
        if inst is None:
            return {"text": "OK."}
        if not tools:
            return {"text": inst.references[0]}
        if inst.tool and inst.tool.get("kind") == "probe":
            from edat.sidekick.ceiling.agent import reference_turns
            script = reference_turns(inst)
        else:
            from edat.sidekick.tool_mode import _reference_turns
            script = _reference_turns(inst)
        n = sum(1 for m in messages if m["role"] == "assistant")
        return script[n] if n < len(script) else {"text": "Done."}

    def chat(self, messages, **kw):
        self.turns.append(self._turn)
        return super().chat(messages, **kw)


def make_client(kind: str, model: LocalModel, base_url: str | None, thinking: bool):
    if kind == "null":
        return NullClient()
    if kind == "oracle":
        return OracleClient()
    from edat.models import OmlxClient
    budget = max(BUDGET[thinking].values())
    timeout = max(300.0, 3 * budget / max(model.decode_tok_s, 5.0))
    return OmlxClient(model.engine_id, base_url=base_url or "http://127.0.0.1:8000", timeout_s=timeout)


# ------------------------------------------------------------------ the run

def _done_units(store: Path) -> dict[str, set[tuple[str, int, str]]]:
    """(case id, trial, split) per suite: a trial is only reused for the same ladder split (public or private)."""
    out: dict[str, set[tuple[str, int, str]]] = {}
    for f in store.glob("runs/*/records.jsonl"):
        for line in f.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("status") == "ok" and "grade_hardened" not in (r.get("metrics") or {}):
                    continue                     # graded before rows were marked: it never scores, so run it again
                out.setdefault(r["suite"], set()).add((r["case_id"], r["trial"], r.get("split")))
    return out


def pass_a_ceiling(records: list[dict]) -> int:
    """The first pass's ceiling from pass A's own trials (trial 1); pass B's repeats never move it."""
    from edat.sidekick.ceiling.report import entry_summary
    return entry_summary([r for r in records if r.get("trial") == 1])["ceiling"]


def _unit_split(u: Unit, ladder_split: str) -> str:
    sc = u.case.sidekick_case
    return sc.split if sc is not None else ladder_split


def _records(store: Path) -> list[dict]:
    rows = []
    for f in sorted(store.glob("runs/*/records.jsonl")):
        rows += [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    return rows


def run_entry(entry: Entry | str, out: Path, *, plan: str, client_kind: str = "omlx", base_url: str | None = None,
              device_id: str = "this-mac", weights_sha256: str | None = None, max_minutes: float | None = None,
              progress=print, iso=None, lock: str | None = None, quick: Quick | None = None) -> dict:
    """Run one entry. `entry` is an Entry, or its key ('<engine id>|thinking-on|author'), looked up in the
    model folder."""
    if isinstance(entry, str):
        mdir = model_dir_from_settings()
        if not mdir:
            raise RuntimeError("oMLX's model folder is not set (~/.omlx/settings.json model_dir)")
        entry = find_entry(scan(mdir)[0], entry)
    out = Path(out).expanduser().resolve()
    repo = Path(__file__).resolve().parents[3]
    if out == repo or repo in out.parents:
        raise RuntimeError("results go to a private folder outside the repository")
    created_lock = False
    if lock:
        try:
            os.mkdir(lock)
            created_lock = True
        except FileExistsError:
            raise SystemExit(f"{lock} exists: another heavy job is running; not starting") from None
    try:
        return _run_entry(entry, out, plan=plan, client_kind=client_kind, base_url=base_url,
                          device_id=device_id, weights_sha256=weights_sha256, max_minutes=max_minutes,
                          progress=progress, iso=iso, quick=quick)
    finally:
        if created_lock:
            os.rmdir(lock)


def _run_entry(e: Entry, out: Path, *, plan, client_kind, base_url, device_id,
               weights_sha256, max_minutes, progress, iso, quick: Quick | None = None) -> dict:
    model, thinking, profile = e.model, e.thinking, e.profile
    if plan == "quick":
        quick = quick or Quick()
    scripted = client_kind in ("null", "oracle")
    if not scripted:
        if not weights_sha256:
            raise RuntimeError("a real run needs --weights-sha256 (preflight --weights prints it)")
        problem = engine_check(model, base_url or "http://127.0.0.1:8000")
        if problem:
            raise RuntimeError(problem)
    if any(u.case.sidekick_case is not None and u.case.sidekick_case.split != "dev" for u in plan_units(plan, quick=quick)):
        master_seed("heldout")                   # the private seed first, so each refusal names its own cause
    selftest = require_sandbox()                 # refuses to run unless the sealed sandbox is proven
    ladder_split = ladder_seed()[1]              # public or private ladder: resumed trials must match it
    seeds = seed_fingerprints(any(u.case.sidekick_case is not None and u.case.sidekick_case.split != "dev"
                                  for u in plan_units(plan, quick=quick)))
    bind_seeds(results_dir(out, plan), seeds)    # never resume on trials made from other seeds
    iso = iso or get_isolation()
    client = make_client(client_kind, model, base_url, thinking)
    slug = e.slug
    store = results_dir(out, plan) / slug
    store.mkdir(parents=True, exist_ok=True)
    commit, dirty = harness_commit()
    import hashlib
    entry = {"model": {"id": model.engine_id, "role": model.role, "quant": dict(model.quant),
                       **({"weights_sha256": weights_sha256} if weights_sha256 else
                          {"weights_fingerprint": hashlib.sha256(f"ceiling-stub:{model.engine_id}".encode()).hexdigest()})},
             "thinking": "on" if thinking else "off", "kind": "local",
             "runtime": {"name": "stub" if scripted else "omlx", "version": "0" if scripted else None},
             "device": device_block("stub-host" if scripted else device_id), "sampling": record_sampling(profile.sampling),
             "sampling_source": profile.source, "sample": scripted}
    sampling = Sampling(**profile.sampling, source=profile.source)    # sent with every request, never left to oMLX
    key = e.key
    t_start = time.monotonic()
    state = {"entry": key, "label": e.label, "plan": plan, "started_at": utc_now(), "selftest_passed": selftest.passed,
             "stopped": None, "skipped": None, "written": 0, "sampling_sent": dict(profile.sampling),
             "sampling_basis": profile.basis, "quant": dict(model.quant), "bits_per_weight": model.bits_per_weight,
             "engine_context_window": None if scripted else engine_context(model.engine_id)[0],
             "ships_thinking": model.thinking[0], "template_thinking_default": model.template_thinking,
             "seeds": seeds, "toolchain": _toolchain_key(),
             "quick": quick.to_dict() if plan == "quick" else None}
    first_ok: list[dict] = []          # the first answer-only results: is thinking really on, does the engine run it?
    first_status: list[str] = []
    phases = ["A", "B"] if plan == "first-pass" else ["all"]
    with ExitStack() as stack:
        writers: dict[str, RunWriter] = {}

        def writer(suite: str) -> RunWriter:
            if suite not in writers:
                writers[suite] = stack.enter_context(RunWriter.open(store, f"ceiling-{slug}-{suite}", tier=TIER, suite=suite))
            return writers[suite]

        for phase in phases:
            ceiling = None
            if phase == "B":
                # pass A's ceiling from pass A's own trials (trial 1), so a resumed pass B keeps the same rungs
                ceiling = pass_a_ceiling(_records(store))
                state["pass_a_ceiling"] = ceiling
            units = plan_units(plan, done_ceiling=ceiling, quick=quick)
            done = _done_units(store)
            todo = [u for u in units if (u.case.id, u.trial, _unit_split(u, ladder_split)) not in done.get(u.suite, set())]
            state[f"phase_{phase}"] = {"units": len(units), "todo": len(todo)}
            for u in todo:
                stop = _should_stop(out, store, t_start, max_minutes)
                if stop:
                    state["stopped"] = stop
                    break
                inst, split = instance(u)
                if isinstance(client, OracleClient):
                    client.learn(inst)
                seed = trial_seed(u.case.id, u.trial)
                fn = tool_trial if u.suite in ("tool_mode", "ladder_tool") else answer_trial
                started = utc_now()
                left0 = leftover_count()
                metrics, raw, events, status, detail = fn(client, inst, suite=u.suite, seed=seed, thinking=thinking,
                                                          sampling=sampling, iso=iso)
                left = leftover_events(left0)
                if left:                                     # a trial folder cleanup could not remove: fail, keep size
                    events = list(events) + left
                    if status == "ok":
                        metrics["pass"] = False
                        metrics["leftover_bytes"] = sum(e.extra["bytes"] for e in left)
                w = writer(u.suite)
                ev = [event_record(e, case_id=u.case.id, trial=u.trial, model_key=key, board_version="ceiling",
                                   mode=u.suite) for e in events]
                meta = u.case.meta(split)
                rec = make_record(run_id=w.run_id, board_version="ceiling", entry=entry, case_meta=meta, trial=u.trial,
                                  suite=u.suite, grade=None, status=status, status_detail=detail,
                                  metrics=metrics if status == "ok" else {}, events=[{"kind": e["kind"]} for e in ev],
                                  started_at=started, seed=seed, raw_ref=w.put_raw(json.loads(json.dumps(raw, default=str))),
                                  commit=commit, dirty=dirty)
                rec["facets"]["tags"] += [f"rung-{u.case.rung.n}", f"sampling-{profile.name}"]
                # Every graded row is marked (labels.may_score is default deny: a row without the mark never
                # scores): by its rung and grading method (rungs 4 to 9, row_hardened) and by how its case is
                # graded (labels.grading_label). The grader's own method and a tripwire's own `false` are kept.
                lab = grading_label(inst.grader, inst.check)
                hardened = (row_hardened(u.case.rung, metrics if status == "ok" else {}) and lab["grade_hardened"]
                            and (rec["metrics"] or {}).get("grade_hardened") is not False)
                if status == "ok":
                    rec["metrics"]["grading"] = (rec["metrics"] or {}).get("grading") or lab["grading"]
                    rec["metrics"]["grade_hardened"] = hardened
                if not hardened:
                    if "grade-not-hardened" not in rec["facets"]["tags"]:
                        rec["facets"]["tags"].append("grade-not-hardened")
                    if status == "ok":
                        rec["metrics"]["not_hardened_reason"] = lab.get("not_hardened_reason") or "probe_in_process"
                if status == "ok" and model.bits_per_weight is not None:
                    rec["metrics"]["bits_per_weight"] = model.bits_per_weight
                w.append(rec)
                if ev:
                    with open(store / "restraint-events.jsonl", "a") as fh:
                        fh.write("".join(json.dumps(e, sort_keys=True) + "\n" for e in ev))
                state["written"] += 1
                first_status.append(status)
                if status == "ok" and u.suite in ("answer_only", "ladder"):
                    first_ok.append(metrics)
                why = _entry_problem(thinking, first_status, first_ok)
                if why:
                    state["stopped"] = state["skipped"] = why
                if progress:
                    progress(f"{slug} R{u.case.rung.n} {u.case.id} t{u.trial}: "
                             f"{'PASS' if metrics.get('pass') else (metrics.get('failure_mode') or status)}"
                             f" ({metrics.get('time_s', 0):.0f} s)")
                _write_progress(store, state)
                if state["stopped"]:
                    break
            if state["stopped"]:
                break
    from edat.sidekick.ceiling.report import entry_summary
    state["summary"] = entry_summary(_records(store))
    state["finished_at"] = utc_now()
    _write_progress(store, state)
    if progress:
        s = state["summary"]
        progress(f"{slug}: ceiling rung {s['ceiling']}, solid up to rung {s['solid']}"
                 + (f"; STOPPED: {state['stopped']} (run the same command again to resume)" if state["stopped"] else ""))
    return state


HOST_STOPS = ("the STOP file", "minute limit", "free memory", "engine or transport faults")


HARBOR_RUNNING = "Harbor is running (it shares oMLX): close it first; nothing was unloaded"


def harbor_running() -> bool:
    """Harbor shares the engine: the run never unloads models while it is open."""
    return bool(subprocess.run(["pgrep", "-f", "MacOS/Harbor"], capture_output=True, text=True).stdout.strip())


def run_all(out: Path, *, plan: str, tiers: tuple[int, ...] = (0,), only: tuple[str, ...] = (), client_kind: str = "omlx",
            base_url: str = "http://127.0.0.1:8000", device_id: str = "this-mac", lock: str | None = None,
            max_minutes: float | None = None, progress=print, iso=None, models: list[LocalModel] | None = None,
            quick: Quick | None = None) -> dict:
    """Every entry of the chosen tiers, in run order, one model resident at a time. An entry the engine cannot
    serve (or whose thinking mode turns out to be forced off) is recorded as skipped and the run goes on;
    a host-level stop (memory, STOP file, time, engine faults) ends the whole run."""
    out = Path(out).expanduser().resolve()
    repo = Path(__file__).resolve().parents[3]
    if out == repo or repo in out.parents:
        raise RuntimeError("results go to a private folder outside the repository")
    mdir = model_dir_from_settings()
    if models is None:
        if not mdir:
            raise RuntimeError("oMLX's model folder is not set (~/.omlx/settings.json model_dir)")
        models, skipped = scan(mdir)
    else:
        skipped = []
    if plan == "quick":
        quick = quick or Quick()
        entries = [e for e in quick_entries(models, quick) if not only or e.model.engine_id in only]
    else:
        entries = [e for e in ordered_entries(models, tiers=tiers) if not only or e.model.engine_id in only]
    every = {e.key: e for m in models for e in m.entries()}
    if client_kind == "omlx" and harbor_running():
        raise SystemExit(HARBOR_RUNNING + "; not starting")
    created = False
    if lock:
        try:
            os.mkdir(lock)
            created = True
        except FileExistsError:
            raise SystemExit(f"{lock} exists: another heavy job is running; not starting") from None
    log = {"plan": plan, "tiers": None if plan == "quick" else list(tiers),
           "quick": quick.to_dict() if plan == "quick" else None,
           "started_at": utc_now(), "skipped_folders": skipped, "entries": []}
    out.mkdir(parents=True, exist_ok=True)
    log_file = results_dir(out, plan) / "run-all.json"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        current = None
        for e in entries:
            row = {"entry": e.key, "label": e.label, "tier": e.tier}
            log["entries"].append(row)
            left = None if max_minutes is None else max_minutes - (time.monotonic() - started) / 60
            if (out / "STOP").exists() or (left is not None and left <= 0):
                row["result"] = "not started (stopped)"
                break
            sha = None
            if client_kind == "omlx":
                if e.model.engine_id != current:
                    from scripts.productbench import engine_prep
                    if harbor_running():              # opened during the run: stop before unloading anything
                        row.update(result="not started (stopped)", why=HARBOR_RUNNING)
                        break
                    if engine_prep.main(["keep", e.model.engine_id, "--save", str(out / "resident-before.json")]) != 0:
                        row.update(result="skipped", why="other models are still loaded")
                        continue
                    current = e.model.engine_id
                sha = weights_for(out, mdir, e.model)
            try:
                st = _run_entry(e, out, plan=plan, client_kind=client_kind, base_url=base_url, device_id=device_id,
                                weights_sha256=sha, max_minutes=left, progress=progress, iso=iso, quick=quick)
            except SandboxUnavailable:
                raise                             # the host's sandbox or git: the whole run stops
            except RuntimeError as exc:
                row.update(result="skipped", why=str(exc))
                if progress:
                    progress(f"{e.slug}: skipped: {exc}")
                continue
            if st["skipped"]:
                row.update(result="skipped", why=st["skipped"])
                if e.thinking and "forced off" in st["skipped"]:
                    # Thinking cannot be switched on: run the thinking-off entry now in its place.
                    alt = every.get(f"{e.model.engine_id}|thinking-off|{e.model.profiles(False)[0].name}")
                    if alt and alt not in entries[entries.index(e) + 1:] and \
                            not any(r["entry"] == alt.key for r in log["entries"]):
                        entries.insert(entries.index(e) + 1, alt)
                        row["replaced_by"] = alt.key
            elif st["stopped"]:
                row.update(result="stopped", why=st["stopped"])
                if any(h in st["stopped"] for h in HOST_STOPS):
                    break
            else:
                row.update(result="done", ceiling=st["summary"]["ceiling"], solid=st["summary"]["solid"])
            _write_progress_file(log_file, log)
    finally:
        log["finished_at"] = utc_now()
        _write_progress_file(log_file, log)
        if created:
            os.rmdir(lock)
    return log


def _write_progress_file(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str))
    tmp.replace(path)


def record_sampling(sent: dict) -> dict:
    """The values sent, under the results schema's names (the adapter's repetition_penalty is repeat_penalty there)."""
    return {("repeat_penalty" if k == "repetition_penalty" else k): v for k, v in sent.items()}


def _entry_problem(thinking: bool, statuses: list[str], answers: list[dict]) -> str | None:
    """Problems with the entry itself (reported as skipped, not as model failures)."""
    if len(statuses) >= 3 and all(s == "excluded" for s in statuses[:3]):
        return "the engine could not run this model (its first 3 trials all failed on engine faults)"
    if thinking and len(answers) == 3 and not any(a.get("reasoned") for a in answers):
        return "thinking appears to be forced off in this build (no reasoning in its first 3 answers)"
    return None


def _should_stop(out: Path, store: Path, t_start: float, max_minutes: float | None) -> str | None:
    if (out / "STOP").exists():
        return "the STOP file exists"
    if max_minutes and (time.monotonic() - t_start) / 60 > max_minutes:
        return f"the {max_minutes:g}-minute limit was reached"
    left = leftovers_over_cap()
    if left:
        return left
    free = memory_free_pct()
    if free is not None and free < MEMORY_FLOOR_PCT:
        return f"free memory {free}% is under {MEMORY_FLOOR_PCT}%"
    recs = _records(store)
    if len(recs) >= EXCLUDED_FROM:
        bad = sum(1 for r in recs if r["status"] == "excluded")
        if bad / len(recs) > EXCLUDED_LIMIT:
            return f"{bad} of {len(recs)} trials failed on engine or transport faults (over 2%)"
    return None


def _write_progress(store: Path, state: dict) -> None:
    tmp = store / ".progress.json.tmp"
    tmp.write_text(json.dumps(state, indent=1, default=str))
    tmp.replace(store / "progress.json")


# ------------------------------------------------------------------ CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m edat.sidekick.ceiling run")
    ap.add_argument("--entry", required=True, help="'<engine id>|thinking-on' or '...|thinking-off'")
    ap.add_argument("--plan", choices=("smoke", "quick", "first-pass", "full"), required=True)
    ap.add_argument("--out", required=True, help="private results folder, outside the repository")
    add_quick_args(ap)
    ap.add_argument("--client", choices=("omlx", "null", "oracle"), default="omlx")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--device-id", default="this-mac")
    ap.add_argument("--weights-sha256")
    ap.add_argument("--max-minutes", type=float)
    ap.add_argument("--lock", help="a lock directory to take (mkdir) for the run and remove only if taken here")
    a = ap.parse_args(argv)
    try:
        run_entry(a.entry, Path(a.out), plan=a.plan, client_kind=a.client, base_url=a.base_url, device_id=a.device_id,
                  weights_sha256=a.weights_sha256, max_minutes=a.max_minutes, lock=a.lock,
                  quick=quick_from_args(a) if a.plan == "quick" else None)
    except (RuntimeError, ValueError) as exc:
        print(f"not started: {exc}", file=sys.stderr)
        return 2
    return 0


def weights_for(out: Path, mdir: Path, m: LocalModel) -> str:
    """The model's weights_sha256, cached in OUT/weights.json by folder and file sizes and times."""
    from scripts.productbench.engine_prep import weights_sha256
    folder = mdir / m.folder
    stamp = sorted((p.name, p.stat().st_size, int(p.stat().st_mtime)) for p in folder.glob("*.safetensors"))
    cache_f = out / "weights.json"
    cache = json.loads(cache_f.read_text()) if cache_f.exists() else {}
    hit = cache.get(m.engine_id)
    if hit and hit.get("stamp") == [list(x) for x in stamp]:
        return hit["sha256"]
    sha = weights_sha256(folder)
    cache[m.engine_id] = {"sha256": sha, "stamp": stamp}
    out.mkdir(parents=True, exist_ok=True)
    cache_f.write_text(json.dumps(cache, indent=1))
    return sha


LONGEST_PROMPT_TOKENS = 1500          # the longest ladder prompt with its system prompt is about 1,200 tokens


def context_needed(thinking: bool, *, tool_mode: bool) -> int:
    """Tokens of context the cases can need: the prompt plus the output budget; in tool mode, plus up to
    MAX_CALLS tool results at their cap and the model's calls."""
    b = BUDGET[thinking]
    if not tool_mode:
        return LONGEST_PROMPT_TOKENS + max(b["ladder"], b["answer_only"])
    from edat.sidekick.ceiling.agent import DEFAULT_MAX_CALLS
    from edat.sidekick.loop_glue import RESULT_CAP_TOKENS
    return LONGEST_PROMPT_TOKENS + DEFAULT_MAX_CALLS * (RESULT_CAP_TOKENS + 150) + b["tool"]


def engine_context(engine_id: str) -> tuple[int | None, str]:
    """oMLX's context window for a model (its own setting, else the global default), read from its files."""
    home = Path.home() / ".omlx"
    try:
        per = (json.loads((home / "model_settings.json").read_text()).get("models") or {}).get(engine_id) or {}
        if per.get("max_context_window"):
            return int(per["max_context_window"]), "the model's setting"
        g = (json.loads((home / "settings.json").read_text()).get("sampling") or {}).get("max_context_window")
        return (int(g), "the engine default") if g else (None, "not set")
    except (OSError, ValueError):
        return None, "unreadable"


def preflight(out: Path, *, engine: bool = False, weights: bool = False, base_url: str = "http://127.0.0.1:8000",
              lock: str | None = None) -> list[tuple[str, bool, str]]:
    """Read-only checks before the run. Returns (check, ok, detail) rows; the run starts only if all are ok.
    `engine` asks the engine which models it lists and has loaded (GET only); `weights` reads every
    model's weights to print its sha256 (several GB per model, read only)."""
    rows: list[tuple[str, bool, str]] = []
    add = lambda name, ok, detail="": rows.append((name, bool(ok), detail))
    out = Path(out).expanduser().resolve()
    repo = Path(__file__).resolve().parents[3]
    add("results folder outside the repository", not (out == repo or repo in out.parents), str(out))
    p = private_dir()
    add("EDAT_PRIVATE_DIR with the held-out sidekick seed", bool(p and (p / "sidekick" / "master_seed").exists()),
        "set" if p else "not set")
    from edat.sidekick.toolchain import ToolchainError, find_toolchain
    try:
        tc = find_toolchain()
        add("toolchain: Python for model code is self-contained", True, tc.python.version)
        add("toolchain: Node for model code is self-contained", tc.node is not None,
            tc.node.version if tc.node else "no Node found: the JavaScript cases cannot run")
        add("toolchain: git for the git grader", True, f"{tc.git.version.strip()} at {tc.git.path}")
    except ToolchainError as exc:
        add("toolchain: Python, Node and git for model code and the git grader", False, str(exc)[:300])
    try:
        st = require_sandbox()
        add("sealed sandbox self-test", st.passed, f"{len(st.probes) if hasattr(st, 'probes') else ''} probes")
    except Exception as exc:  # noqa: BLE001
        add("sealed sandbox self-test", False, str(exc)[:200])
    mdir = model_dir_from_settings()
    add("oMLX model folder found", bool(mdir and mdir.is_dir()), "")
    models, skipped = scan(mdir) if mdir and mdir.is_dir() else ([], [])
    roster = {"models": [], "skipped": [{"folder": f, "why": w} for f, w in skipped]}
    for m in models:
        modes = "/".join("on" if t else "off" for t in m.thinking)
        add(f"model: {m.label} ({m.engine_id})", True,
            f"{m.quant['scheme']}, {m.bits_per_weight} bits/weight, {m.disk_gb:.1f} GB, thinking {modes}"
            + ("" if m.standard else ", other quantisation (runs after the standard entries)")
            + ("" if m.fits_16gb() else ", would not fit a 16 GB Mac") + (f"; {m.notes}" if m.notes else ""))
        ctx, ctx_src = engine_context(m.engine_id)
        needs = {f"thinking {'on' if t else 'off'}{', tool mode' if tm else ''}": context_needed(t, tool_mode=tm)
                 for t in m.thinking for tm in (False, True)}
        short = [k for k, n in needs.items() if ctx is not None and ctx < n]
        add(f"context window: {m.engine_id}", True,
            f"{ctx} tokens ({ctx_src})" + (f"; below what the cases can need for {', '.join(short)} "
                                            f"({max(needs[k] for k in short)} tokens)" if short else "; enough"))
        roster["models"].append({"engine_id": m.engine_id, "context_window": ctx, "context_source": ctx_src,
                                 "context_needed": needs, "folder": m.folder, "quant": m.quant,
                                 "ships_thinking": m.thinking[0], "template_thinking_default": m.template_thinking,
                                 "bits_per_weight": m.bits_per_weight, "thinking": list(m.thinking),
                                 "standard": m.standard, "loader": m.loader, "disk_gb": m.disk_gb,
                                 "entries": [{"key": e.key, "tier": e.tier, "sampling": e.profile.sampling,
                                              "sampling_source": e.profile.source, "basis": e.profile.basis}
                                             for e in m.entries()]})
        if weights:
            add(f"weights_sha256: {m.engine_id}", True, weights_for(out, mdir, m))
    for f, w in skipped:
        add(f"skipped folder: {f}", True, w)
    out.mkdir(parents=True, exist_ok=True)
    (out / "roster.json").write_text(json.dumps(roster, indent=1))
    free = memory_free_pct()
    add("free memory at least 30%", free is None or free >= 30, f"{free}%" if free is not None else "unknown")
    import shutil
    disk = shutil.disk_usage(out if out.exists() else out.parent)
    add("disk: 5 GB free for results", disk.free > 5e9, f"{disk.free / 1e9:.0f} GB free")
    if lock:
        add("heavy-job lock free", not Path(lock).exists(), lock)
    harbor = harbor_running()
    add("Harbor is not running (it shares oMLX)", not harbor, "running" if harbor else "")
    if engine:
        import httpx
        from scripts.productbench.engine_prep import _headers
        try:
            with httpx.Client(base_url=base_url, headers=_headers(), timeout=30) as c:
                ids = [x.get("id") for x in c.get("/v1/models").json().get("data", [])]
                loaded = [x["id"] for x in c.get("/v1/models/status").json().get("models", []) if x.get("loaded")]
            for m in models:
                add(f"engine lists {m.engine_id}", True,
                    "yes" if m.engine_id in ids else "NO: its entries will be skipped and reported (not servable)")
            add("engine: models loaded now", True, ", ".join(loaded) or "none")
        except Exception as exc:  # noqa: BLE001
            add("engine answers on loopback", False, exc.__class__.__name__)
    return rows


def preflight_main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m edat.sidekick.ceiling preflight")
    ap.add_argument("--out", required=True)
    ap.add_argument("--engine", action="store_true", help="also ask the engine (GET only) what it lists and has loaded")
    ap.add_argument("--weights", action="store_true", help="also hash every model's weights (reads several GB)")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--lock")
    a = ap.parse_args(argv)
    rows = preflight(Path(a.out), engine=a.engine, weights=a.weights, base_url=a.base_url, lock=a.lock)
    for name, ok, detail in rows:
        print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f": {detail}" if detail else ""))
    bad = [r for r in rows if not r[1]]
    print(f"\n{'all checks pass' if not bad else f'{len(bad)} checks fail: the run does not start'}")
    return 0 if not bad else 1


def run_all_main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m edat.sidekick.ceiling run-all")
    ap.add_argument("--plan", choices=("smoke", "quick", "first-pass", "full"), required=True)
    add_quick_args(ap)
    ap.add_argument("--tiers", default="0", help="entry tiers, e.g. 0 or 0,1,2,3 (0 standard, 1 other thinking mode, "
                                                  "2 second sampling setting, 3 other quantisations)")
    ap.add_argument("--only", action="append", default=[], help="engine ids to run (default: all found)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--client", choices=("omlx", "null", "oracle"), default="omlx")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--device-id", default="this-mac")
    ap.add_argument("--lock")
    ap.add_argument("--max-minutes", type=float)
    a = ap.parse_args(argv)
    try:
        log = run_all(Path(a.out), plan=a.plan, tiers=tuple(int(t) for t in a.tiers.split(",")), only=tuple(a.only),
                      client_kind=a.client, base_url=a.base_url, device_id=a.device_id, lock=a.lock,
                      max_minutes=a.max_minutes, quick=quick_from_args(a) if a.plan == "quick" else None)
    except (RuntimeError, ValueError) as exc:
        print(f"not started: {exc}", file=sys.stderr)
        return 2
    for r in log["entries"]:
        print(f"{r.get('result', '?'):<22} {r['label']}" + (f": {r['why']}" if r.get("why") else ""))
    return 0


def plan_main(argv: list[str] | None = None) -> int:
    from edat.sidekick.ceiling.estimate import main as est_main
    return est_main(argv)
