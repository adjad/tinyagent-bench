"""Harbor's own Ask turn as an agent loop: the second agentic column.

The headline agentic score uses the minimal reference loop (`edat/loop.py`).
This module runs the same assistant-lane cases through Harbor's real model-led
Ask turn (`service.agent.loop.run_agent`, prepared exactly as `service/main.py`
does) by calling the Toolbench runner's machinery (`scripts/toolbench/runner.py`:
`isolate`, `install_fakes`, `bind_model`, `set_clock`, `set_thinking`,
`run_case`). The per-model gap between the two columns is what Harbor's
repairs, guards and prompt add. Records carry `suite: harbor-loop` and pair
with the reference-loop records on model, case, trial, world seed, sampling and
thinking mode.

    loop = HarborLoop()
    model = open_session("sample-model", home=throwaway_dir)    # real runs: oMLX on loopback
    result = run_trial(case, model, loop=loop, trial=1, settings=LoopSettings(thinking=False))

How a turn runs
---------------
* The trial's environment (the assistant lane over a private Toolbench World,
  possibly wrapped in the fault layer) stays the source of truth. While a turn
  runs, every Harbor tool the lane knows is installed as a function that calls
  `env.call`, so faults, outbound-recipient bookkeeping and the state the grader
  reads are the same as in the reference loop. Tools the lane does not expose
  (internal ones) keep Toolbench's own fakes.
* `run_case` is called with the turn's prompt and the earlier turns as
  `history`, the case's access mode and `access_needed`, `max_steps` from the
  loop settings, and the trial's approver (deny and timeout modes become
  Harbor confirmations answered "no").
* Sampling (`LoopSettings.sampling`) and the seed are sent on every request;
  `thinking=False` is Toolbench's `off` variant (enable_thinking=false on every
  request), `thinking=True` is Harbor's default.
* Harbor keeps its own prompt, tool menu (it ranks the whole registry), and
  history budget: the case menu, the reference system prompt and the
  long-session trim are not applied.

Scoring (spec 02 §5.6.1)
------------------------
Success judges what the user gets: the final world state, the answer as shown
after Harbor's rewriting (each step's raw model text is kept in the trace), and
no effect-level violation. The one rule that differs from the minimal loop: a
forbidden call the host blocked (never executed) is counted (`unsafe_calls`,
label `blocked_forbidden`) but does not fail the trial
(`blocked_proposals_fail = False`). Each turn logs the repairs that fired
(B28, `TurnTrace.repairs`): stall and text-call re-prompts, forced tool,
empty-step retry, refused duplicate, effect cap, claim rewrite (raw text !=
shown text), discovery call, blocked forbidden call. These are read from the
trajectory, so they are heuristics; see `_repairs`.

What is not applicable (recorded `status: not_applicable`, never a failure)
--------------------------------------------------------------------------
* the neutral lane; cloud references (no Harbor engine); the phone subset;
* Preferences, Long sessions and Memory (not in the column in version 1), noisy
  variants and the scaling probe (minimal loop only, §5.6.2);
* cases with stored preferences or a case policy, until Harbor's real settings
  fields are written (spec 02 B19);
* image or audio input (vision turns are not mirrored by the runner);
* host-blocked cases: an `expect` step whose every tool Harbor refuses for
  every model (`runner.host_refused_tools()`: "no supported completion
  contract"). Tools the route never offers need the dry routing pass (B27),
  which is not built; such a case runs and counts, as §5.6.1 says for a
  `route_fault` outside the list.

The column's case set (§5.6.1) is the assistant-lane core cases of the 7
headline families plus Restraint's privacy cases; the CLI runs that set by
default (`--all` runs every applicable case).

A turn that crashes inside Harbor is excluded (`status_detail: harbor_error`);
a turn the harness guards stopped (network or subprocess) likewise.

Runtime patches (no Toolbench file is edited)
---------------------------------------------
Three module attributes are swapped for the duration of one `run_case` call
and restored after it; each would be cleaner as a keyword on Toolbench's runner
(a change for the Toolbench harness):
* `world.make_fakes` -> the env-routing functions (wanted: `install_fakes(world, fakes=...)`);
* `runner.Approver` -> an adapter over the trial's approver (wanted: `run_case(..., approver=...)`);
* `world.effects.clear` -> a no-op, so a multi-turn case keeps earlier turns'
  effects (wanted: `run_case(..., reset_world=False)`).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
import types
from contextlib import contextmanager
from pathlib import Path

from edat.faults.approver import Approver
from edat.loop import CallRecord, LoopSettings, StepRecord, TurnTrace
from edat.models.base import ConfigurationError

LOOP_NAME = "harbor-ask-v1"
SUITE = "harbor-loop"
DEFAULT_TURN_TIMEOUT_S = 600.0


# ------------------------------------------------------------------ the model

class HarborModel:
    """The model under test as Harbor's own client sees it.

    Passed to `run_trial` in place of a `ModelClient`; only `HarborLoop` can use
    it (`chat` refuses), because Harbor talks to the engine itself.
    """

    def __init__(self, model: str, client, *, profile_as: str | None = None, binding: dict | None = None,
                 scripted: bool = False) -> None:
        self.model, self.client, self.profile_as = model, client, profile_as
        self.binding = binding or {}
        self.scripted = scripted

    def describe(self) -> dict:
        return {"kind": "local", "runtime": "harbor-scripted" if self.scripted else "harbor-omlx",
                "model": self.model, "loop": LOOP_NAME, "profile_as": self.profile_as}

    def chat(self, *a, **k):
        raise ConfigurationError("a HarborModel runs only through HarborLoop (Harbor calls the engine itself)")


def _client_class(base):
    """Toolbench's client plus the reference run's full sampling on every request."""

    class HarborClient(base):
        sampling: dict = {}

        def _thinking_kw(self, kw: dict) -> dict:
            kw = super()._thinking_kw(kw)
            for k, v in (self.sampling or {}).items():
                kw.setdefault(k, v)
            return kw

    return HarborClient


def _throwaway_home_ok() -> bool:
    home = os.environ.get("HARBOR_HOME")
    if not home:
        return False
    real = (Path.home() / ".moe").resolve()
    p = Path(home).resolve()
    return p != real and not p.is_relative_to(real)


def open_session(model: str, *, home: str | Path | None = None, omlx_port: int | None = None,
                 profile_as: str | None = None, scripted: bool = False) -> HarborModel:
    """Prepare this process for Harbor-loop trials and return the model handle.

    `home` (a throwaway directory) runs Toolbench's `isolate()`: it must be
    called before anything imports `service`, and it guards the network
    (loopback to the oMLX port only) and subprocesses for the whole process.
    Without `home`, `HARBOR_HOME` must already name a throwaway directory (as in
    the tests); only the endpoint is pinned. `scripted=True` uses Toolbench's
    ScriptedClient, which plays `client.plan` instead of calling oMLX.
    """
    from scripts.toolbench import runner
    port = omlx_port if omlx_port is not None else runner.DEFAULT_OMLX_PORT
    if home is not None:
        runner.isolate(Path(home), omlx_port=port)
    elif not _throwaway_home_ok():
        raise ConfigurationError("set HARBOR_HOME to a throwaway directory, or pass home=, before a Harbor-loop run")
    else:
        runner.pin_endpoint(port)
    from service.config.endpoints import role_target
    binding = runner.bind_model(model, profile_as=profile_as)
    base = runner.scripted_client_class() if scripted else runner.bench_client_class()
    client = _client_class(base)(target=role_target("agent"))
    return HarborModel(model, client, profile_as=profile_as, binding=binding, scripted=scripted)


# ------------------------------------------------------------------ messages -> prompt, history

def _text(content) -> str | None:
    if isinstance(content, str) or content is None:
        return content or ""
    return None   # a list of parts: media input


def split_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    """(prompt, history) for `run_case` from the loop's message list.

    The system message is dropped (Harbor writes its own). Earlier turns become
    Toolbench history entries `{user, assistant, calls: [{name, args, result}]}`.
    Raises ValueError for media content.
    """
    body = [m for m in messages if m.get("role") != "system"]
    users = [i for i, m in enumerate(body) if m.get("role") == "user"]
    if not users:
        raise ValueError("no user message")
    last = users[-1]
    prompt = _text(body[last].get("content"))
    if prompt is None:
        raise ValueError("media input")
    history: list[dict] = []
    cur: dict | None = None
    by_id: dict[str, dict] = {}
    for m in body[:last]:
        role = m.get("role")
        if role == "user":
            text = _text(m.get("content"))
            if text is None:
                raise ValueError("media input")
            cur = {"user": text}
            history.append(cur)
            continue
        if cur is None:
            cur = {}
            history.append(cur)
        if role == "assistant":
            if m.get("content"):
                cur["assistant"] = str(m["content"])
            for tc in m.get("tool_calls") or ():
                fn = tc.get("function") or {}
                import json
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = {}
                call = {"name": fn.get("name"), "args": args if isinstance(args, dict) else {}, "result": ""}
                cur.setdefault("calls", []).append(call)
                by_id[str(tc.get("id"))] = call
        elif role == "tool":
            call = by_id.get(str(m.get("tool_call_id")))
            if call is not None:
                call["result"] = str(m.get("content") or "")
    for h in history:
        if h.get("calls"):
            h["tools"] = ", ".join(c["name"] for c in h["calls"] if c.get("name"))
    return prompt, history


# ------------------------------------------------------------------ runtime patches

@contextmanager
def _patched(obj, attr: str, value):
    had = attr in getattr(obj, "__dict__", {})
    old = getattr(obj, attr)
    setattr(obj, attr, value)
    try:
        yield
    finally:
        if had or isinstance(obj, types.ModuleType):
            setattr(obj, attr, old)
        else:
            delattr(obj, attr)


class _ApproverAdapter:
    """Harbor's approver interface over the trial's approver."""

    def __init__(self, approver: Approver, step_of) -> None:
        self.approver, self.step_of = approver, step_of
        self.asked: list[str] = []
        self.decisions: list[dict] = []

    async def confirm(self, action: dict) -> bool:
        tool = str(action.get("tool", ""))
        args = action.get("args") if isinstance(action.get("args"), dict) else {}
        self.asked.append(tool)
        step = self.step_of()
        ok, _ = self.approver.decide(tool, dict(args), step)
        self.decisions.append({"tool": tool, "step": step, "approved": ok})
        return ok


# ------------------------------------------------------------------ per-trial reset

_MEMORY_TABLES = ("memory_evidence", "memory_revisions", "memory_suppression", "facts")


def reset_harbor_memory() -> None:
    """Empty the isolated Harbor home's saved facts and profile.

    Toolbench's `seed_memory` gives each world fact its world id and refuses a
    taken id, and an earlier trial's facts would leak into a later trial's
    prompt. Refuses unless the store lives in a throwaway home (never ~/.moe).
    """
    from service import paths
    from service.memory import profile
    from service.memory.facts import store
    home = Path(paths.MOE_DIR).resolve()
    real = (Path.home() / ".moe").resolve()
    if home == real or home.is_relative_to(real) or not _throwaway_home_ok():
        raise RuntimeError(f"harbor loop: refusing to clear memory outside a throwaway Harbor home ({home})")
    with store._write():
        for table in _MEMORY_TABLES:
            store._db.execute(f"DELETE FROM {table}")
        try:
            store._db.execute("DELETE FROM memory_fact_fts")
        except Exception:  # noqa: BLE001 — older stores have no FTS table
            pass
    if Path(profile.PROFILE_PATH).exists():
        Path(profile.PROFILE_PATH).unlink()


def _atomic_steps(step: dict):
    if "parallel" in step:
        for m in step["parallel"].get("steps", []):
            yield from _atomic_steps(m)
    else:
        yield step


def host_blocked_step(case: dict, refused: frozenset[str]) -> list[str]:
    """Tools of the first `expect` step whose every alternative the host refuses ([] if none)."""
    from edat.cases.menu import _names
    for s in case.get("sessions") or []:
        for t in s.get("turns") or []:
            for step in t.get("expect") or []:
                for atom in _atomic_steps(step):
                    names = _names(atom)
                    if names and all(n in refused for n in names):
                        return sorted(names)
    return []


in_column = lambda case: (case.get("lane") == "assistant" and "core" in (case.get("tags") or [])
                          and (case.get("family") in ("select", "parallel", "sequential", "mixed", "pev",
                                                      "recovery", "abstain")
                               or (case.get("family") == "restraint" and case.get("sub") in PRIVACY_SUBS)))
# Restraint's privacy sub-families (edat/cases/generators/restraint.py).
PRIVACY_SUBS = ("similar_name", "third_party", "web_exfil", "authorized", "privacy")

_TEXT_CALL = __import__("re").compile(r"<\|?tool_call|\"name\"\s*:\s*\"|\bfunctions\.[a-z_]+|<function", __import__("re").I)
EFFECT_CAP = 8


def _repairs(trace: TurnTrace, steps: list[dict], forbid: set[str]) -> list[dict]:
    """Repairs Harbor's loop applied this turn, read from the trajectory (B28)."""
    out: list[dict] = []
    add = lambda kind, step: out.append({"kind": kind, "step": step})
    n = len(steps)
    seen: list[tuple] = []
    effects = 0
    for i, st in enumerate(steps, start=1):
        content = str(st.get("content") or "").strip()
        has_calls = bool(st.get("calls"))
        if i < n and not has_calls:
            if not content:
                add("empty_step_retry", i)
            elif _TEXT_CALL.search(content):
                add("text_call_reprompt", i)
            else:
                add("stall_reprompt", i)
        offered = st.get("offered") or []
        if i > 1 and len(offered) == 1 and len(steps[i - 2].get("offered") or []) > 1:
            add("forced_tool", i)
    for c in trace.calls:
        sig = (c.name, c.arguments_raw)
        if c.name == "get_tool_schemas" or c.outcome == "schema_loaded":
            add("discovery_call", c.step)
        if c.origin == "model" and not c.executed and c.outcome == "not_run":
            if sig in seen:
                add("refused_duplicate", c.step)
            elif effects >= EFFECT_CAP:
                add("effect_cap", c.step)
        if c.name in forbid and not c.executed:
            add("blocked_forbidden", c.step)
        if c.executed:
            seen.append(sig)
            effects += 1 if c.effect else 0
    if steps:
        raw = str(steps[-1].get("content") or "").strip()
        if raw and not steps[-1].get("calls") and raw != trace.final_text.strip():
            add("claim_rewrite", n)
    return out


# ------------------------------------------------------------------ the loop

class HarborLoop:
    """`AgentLoop` over Harbor's model-led Ask turn (see the module docstring)."""

    name = LOOP_NAME
    own_history_policy = True
    blocked_proposals_fail = False     # §5.6.1: a host-blocked forbidden call does not fail the trial
    MINIMAL_ONLY_FAMILIES = ("preferences", "memory", "long_session")

    def __init__(self, *, turn_timeout_s: float = DEFAULT_TURN_TIMEOUT_S) -> None:
        self.turn_timeout_s = turn_timeout_s
        self._case: dict = {}
        self._refused: frozenset[str] | None = None

    # -------------------------------------------------- case-level decisions
    def host_refused(self) -> frozenset[str]:
        """Tools Harbor's loop refuses for every model (computed once, fakes installed)."""
        if self._refused is None:
            from scripts.toolbench import runner
            from scripts.toolbench.world import build_world
            installed = runner._INSTALLED is not None
            if not installed:
                runner.install_fakes(build_world(), seed=False)
            try:
                self._refused = runner.host_refused_tools()
            finally:
                if not installed:
                    runner.uninstall_fakes()
        return self._refused

    def unsupported(self, case: dict) -> str | None:
        if case.get("lane") != "assistant":
            return "harbor loop: assistant lane only"
        if case.get("family") in self.MINIMAL_ONLY_FAMILIES:
            return f"harbor loop: {case['family']} is minimal loop only (v1)"
        if case.get("variant_of"):
            return "harbor loop: noisy variants are minimal loop only"
        if case.get("stored_preferences"):
            return "harbor loop: stored preferences need Harbor's settings fields (B19)"
        if case.get("system_policy"):
            return "harbor loop: no Harbor field for a case policy"
        if (case.get("carrier") or "text") in ("image", "spoken"):
            return "harbor loop: media input is not mirrored"
        blocked = host_blocked_step(case, self.host_refused())
        if blocked:
            return f"host-blocked: harbor refuses {', '.join(blocked)}"[:64]
        return None

    def begin_trial(self, case: dict, env) -> None:
        """A trial starts with an empty Harbor memory and profile (the world seeds its own)."""
        self._case = case
        reset_harbor_memory()

    # -------------------------------------------------- one turn
    def run_turn(self, client, env, messages: list[dict], *, menu: list[str],
                 settings: LoopSettings | None = None, approver: Approver | None = None) -> TurnTrace:
        if not isinstance(client, HarborModel):
            raise ConfigurationError("HarborLoop needs a HarborModel (edat.cases.harbor_loop.open_session)")
        if getattr(env, "lane", None) != "assistant":
            return TurnTrace(stop="not_applicable", excluded=True, not_applicable="harbor loop: assistant lane only")
        s = settings or LoopSettings()
        approver = approver or Approver()
        try:
            prompt, history = split_messages(messages)
        except ValueError as exc:
            return TurnTrace(stop="not_applicable", excluded=True, not_applicable=f"harbor loop: {exc}")
        traj, log, decisions = self._run(client, env, prompt, history, s, approver)
        trace = self._trace(traj, log, decisions, env)
        self._append_messages(messages, trace)
        return trace

    def _run(self, hm: HarborModel, env, prompt: str, history: list[dict], s: LoopSettings, approver: Approver):
        from scripts.toolbench import runner
        from scripts.toolbench import world as world_mod
        hc = hm.client
        world = env.world
        lane_tools = set(env.tool_names())
        log: list[dict] = []
        steps_now = lambda: len(hc.steps)

        def route(name: str):
            def fn(**args):
                r = env.call(name, dict(args))
                log.append({"step": steps_now(), "name": name, "args": dict(args), "result": r})
                raw = (r.meta or {}).get("raw_result") if not (r.meta or {}).get("fault") else None
                return raw if raw is not None else r.content
            fn.__name__ = name
            return fn

        real_make_fakes = world_mod.make_fakes

        def make_fakes(w):
            fakes = real_make_fakes(w)
            from service.tools.registry import REGISTRY
            for name in REGISTRY:
                if name in lane_tools:
                    fakes[name] = route(name)
            return fakes

        adapter = _ApproverAdapter(approver, steps_now)
        case = self._case
        access = case.get("access") if case.get("access") in runner.ACCESS_MODES else None
        cobj = types.SimpleNamespace(id=case.get("id", "case"), prompt=prompt, history=history,
                                     access_needed=tuple((case.get("world") or {}).get("access_needed") or ()),
                                     access=access or "")
        hc.seed = s.seed
        hc.sampling = s.sampling.params() if s.sampling else {}
        flags = runner.set_thinking(hc, hm.model, "default" if s.thinking else "off")
        with _patched(world_mod, "make_fakes", make_fakes):
            runner.install_fakes(world)
        runner.set_clock(env.clock.now.replace(tzinfo=None))
        try:
            with _patched(runner, "Approver", lambda: adapter), \
                    _patched(world.effects, "clear", lambda: None):
                traj = asyncio.run(runner.run_case(hc, hm.model, cobj, world, timeout=self.turn_timeout_s,
                                                   max_steps=s.max_steps, access=access))
        finally:
            runner.set_clock(None)
            runner.uninstall_fakes()
        traj["thinking_flags"] = flags
        return traj, log, adapter.decisions

    def _trace(self, traj: dict, log: list[dict], decisions: list[dict], env) -> TurnTrace:
        trace = TurnTrace()
        refused = self.host_refused()
        known = set(env.tool_names())
        pending = list(log)
        for i, st in enumerate(traj.get("steps") or (), start=1):
            rec = StepRecord(step=i, text=str(st.get("content") or ""), reasoning="",
                             finish_reason=None, calls=[],
                             usage={"prompt_tokens": None, "completion_tokens": None,
                                    "prompt_tokens_est": st.get("prompt_tokens_est")},
                             timing={"ttft_s": st.get("ttft"), "total_s": st.get("seconds")},
                             raw={"harbor_step": st, "route": traj.get("route") if i == 1 else None,
                                  "thinking_flags": traj.get("thinking_flags") if i == 1 else None},
                             malformed=sum(1 for c in st.get("calls") or () if c.get("parse_error")))
            for j, c in enumerate(st.get("calls") or ()):
                name = str(c.get("name") or "")
                args = c.get("args") if isinstance(c.get("args"), dict) else None
                import json
                cr = CallRecord(step=i, index=j, id=f"h{i}_{j}", name=name,
                                arguments_raw=json.dumps(c.get("args"), ensure_ascii=False, default=str),
                                arguments=args, parse_error=c.get("parse_error"), known=name in known,
                                offered=name in (st.get("offered") or ()))
                hit = next((e for e in pending if e["name"] == name and e["step"] == i), None)
                dec = next((d for d in decisions if d["tool"] == name and d["step"] == i), None)
                if dec is not None:
                    cr.confirm_asked, cr.approved = True, dec["approved"]
                if hit is not None:
                    pending.remove(hit)
                    self._fill(cr, hit)
                elif dec is not None and not dec["approved"]:
                    cr.outcome = "denied"
                elif name in (st.get("stubs") or ()):
                    cr.outcome = "schema_loaded"     # a name-only stub: loads the schema, runs nothing
                elif name in refused:
                    cr.outcome = "host_refused"
                else:
                    cr.outcome = "not_run"           # Harbor's guards answered without running it
                rec.calls.append(cr)
            trace.steps.append(rec)
        for e in pending:                            # calls Harbor made itself (direct calls, repairs)
            i = max(1, min(e["step"], len(trace.steps)))
            if not trace.steps:
                trace.steps.append(StepRecord(step=1, text="", reasoning="", finish_reason=None, calls=[],
                                              usage={}, timing={}))
            cr = CallRecord(step=i, index=len(trace.steps[i - 1].calls), id=f"host_{i}", name=e["name"],
                            arguments_raw="", arguments=e["args"], parse_error=None, known=True, offered=True,
                            origin="host")
            self._fill(cr, e)
            trace.steps[i - 1].calls.append(cr)
        draft = traj.get("message_draft")
        trace.final_text = str(traj.get("final") or (draft or {}).get("text") or "")
        trace.repairs = _repairs(trace, list(traj.get("steps") or ()), set(self._case.get("forbid") or []))
        if traj.get("escapes"):
            trace.excluded, trace.stop = True, "harness_guard"
            trace.engine_error = f"harbor_guard: {'; '.join(traj['escapes'])[:200]}"
        elif traj.get("error"):
            trace.excluded, trace.stop = True, "engine_error"
            trace.engine_error = f"harbor_error: {traj['error']}"
        elif traj.get("timeout"):
            trace.stop = "timeout"
        elif traj.get("steps") and len(traj["steps"]) >= int(traj.get("max_steps") or 0) and not trace.final_text:
            trace.stop = "max_steps"
        else:
            trace.stop = "answer" if trace.final_text.strip() else "empty"
        return trace

    @staticmethod
    def _fill(cr: CallRecord, entry: dict) -> None:
        r = entry["result"]
        cr.executed, cr.outcome, cr.effect = True, r.outcome, r.effect
        cr.content, cr.virtual_elapsed_s = r.content, r.virtual_elapsed_s

    @staticmethod
    def _append_messages(messages: list[dict], trace: TurnTrace) -> None:
        """The turn as the next turn's history: executed calls with results, then the answer."""
        import json
        calls = [c for c in trace.calls if c.executed]
        if calls:
            msg = {"role": "assistant", "content": "",
                   "tool_calls": [{"id": c.id, "type": "function",
                                   "function": {"name": c.name, "arguments": json.dumps(c.arguments or {})}}
                                  for c in calls]}
            messages.append(msg)
            trace.messages_added.append(msg)
            for c in calls:
                tm = {"role": "tool", "tool_call_id": c.id, "name": c.name, "content": c.content}
                messages.append(tm)
                trace.messages_added.append(tm)
        final = {"role": "assistant", "content": trace.final_text}
        messages.append(final)
        trace.messages_added.append(final)


def _register() -> None:
    from edat.loop import LOOPS
    LOOPS.setdefault(LOOP_NAME, HarborLoop)
    LOOPS.setdefault("harbor", HarborLoop)


_register()


# ------------------------------------------------------------------ CLI (real runs; never in tests)

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m edat.cases.harbor_loop",
        description="Run tinyagent-bench's assistant-lane cases through Harbor's own Ask turn on oMLX "
                    "(the harbor-loop column). Writes edat.result/1.0 records with suite harbor-loop.")
    ap.add_argument("--model", required=True)
    ap.add_argument("--profile-as", default=None)
    ap.add_argument("--split", default="dev", choices=("dev", "heldout"))
    ap.add_argument("--family", action="append", default=None)
    ap.add_argument("--all", action="store_true", help="every applicable case, not only the column's core set")
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--thinking", choices=("on", "off"), default="off")
    ap.add_argument("--max-steps", type=int, default=8)
    ap.add_argument("--omlx-port", type=int, default=None)
    ap.add_argument("--home", default=None, help="throwaway Harbor home (default: a new temp dir)")
    ap.add_argument("--out", required=True, help="results store root (held-out: under $EDAT_PRIVATE_DIR/results)")
    ap.add_argument("--board-version", required=True)
    ap.add_argument("--harness-commit", required=True)
    ap.add_argument("--weights-fingerprint", required=True, help="64 hex characters")
    ap.add_argument("--device-id", required=True)
    ap.add_argument("--device-class", required=True)
    ap.add_argument("--ram-bytes", type=int, default=None)
    ap.add_argument("--os", default=None)
    a = ap.parse_args(argv)
    hm = open_session(a.model, home=a.home or tempfile.mkdtemp(prefix="edat-harbor-"),
                      omlx_port=a.omlx_port, profile_as=a.profile_as)
    from edat.cases.loader import load_split
    from edat.cases.runner import run_trial
    from edat.grading.records import RunWriter
    loop = HarborLoop()
    s = LoopSettings(thinking=a.thinking == "on", max_steps=a.max_steps)
    cases = [c for c in load_split(a.split) if (not a.family or c["family"] in a.family)
             and (a.all or in_column(c))]
    model = {"id": a.model, "role": "candidate", "weights_fingerprint": a.weights_fingerprint}
    device = {"id": a.device_id, "class": a.device_class, "ram_bytes": a.ram_bytes, "os": a.os}
    with RunWriter(a.out) as w:
        for c in cases:
            for t in range(1, a.trials + 1):
                r = run_trial(c, hm, trial=t, loop=loop, settings=s)
                w.append(r, board_version=a.board_version, harness_commit=a.harness_commit, model=model,
                         runtime={"name": "harbor-omlx"}, device=device)
    return 0


if __name__ == "__main__":
    sys.exit(main())
