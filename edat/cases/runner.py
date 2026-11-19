"""Run one case trial end to end: bind, build the world, run the loop, grade.

    run = run_trial(case, client, trial=1)
    run.verdict.success, run.verdict.violation, ...

Per trial (spec 02 §2.3): world seed = `world_seed_base + trial - 1`, so each
trial binds different `{fact}` values; the sampling seed is
`sampling_seed_base + trial`. Sessions clear the conversation history but keep
the world (§3.16). An optional history budget trims the oldest whole turns and
records a `trim_event` (§3.15).
"""
from __future__ import annotations

import copy
import inspect
import json
from dataclasses import dataclass, field, replace
from typing import Callable

from edat.cases.bind import bind
from edat.cases.menu import select_menu
from edat.faults.approver import Approver
from edat.faults.layer import FaultyEnvironment
from edat.grading.grader import TrialRun, TurnRun, Verdict, grade
from edat.loop import CHARS_PER_TOKEN, AgentLoop, LoopSettings, TurnTrace, get_loop
from edat.models.base import ModelClient
from edat.state.clock import Clock

WORLD_SEED_BASE = 1
# Long sessions (spec 02 §3.15): history budget of a 16,384-token window after the
# fixed head (~5,000) and the output reserve (2,048).
LONG_SESSION_HISTORY_BUDGET = 9300
SAMPLING_SEED_BASE = 1000


class HostedSplitError(PermissionError):
    pass


def default_env_factory(lane: str, clock: Clock, world_seed: int, profile: str | None = None):
    if profile:                          # case world.profile: an assistant-lane benchmark profile world
        if lane != "assistant":
            raise ValueError("world.profile is an assistant-lane key")
        from edat.state.profile_world import ProfileAssistantEnvironment
        return ProfileAssistantEnvironment(profile, clock, world_seed=world_seed)
    if lane == "neutral":
        from edat.tools_generic import GenericEnvironment
        return GenericEnvironment(clock=clock)
    if lane == "assistant":
        from edat.state.assistant_lane import AssistantEnvironment
        return AssistantEnvironment(clock, world_seed=world_seed)
    raise ValueError(f"unknown lane {lane!r}")


def plant_injection(records: dict[str, list[dict]], inject: dict) -> dict[str, list[dict]]:
    """Append the payload to the named record's field (the injection carrier)."""
    into = inject["into"]
    out = copy.deepcopy(records)
    for rows in out.values():
        for r in rows:
            if r.get("id") == into["record"]:
                r[into["field"]] = (r.get(into["field"]) or "") + inject["text"]
                return out
    raise KeyError(f"inject: no seeded record {into['record']!r}")


def preference_lines(prefs: list[dict] | None) -> list[str]:
    lines = []
    for p in prefs or []:
        v = p.get("value")
        if isinstance(v, dict):
            v = ", ".join(f"{k} {x}" for k, x in v.items())
        lines.append(f"{p.get('store')}: {v}")
    return lines


def build_system(env, bound: dict, menu: list[str]) -> str:
    """The case's system prompt; lanes whose prompt depends on the menu get the offered tools."""
    kw = {"policy": bound.get("system_policy"),
          "preferences": preference_lines(bound.get("stored_preferences")) or None}
    base = env.env if isinstance(env, FaultyEnvironment) else env
    if "offered" in inspect.signature(base.system_prompt).parameters:
        kw["offered"] = list(menu)
    return env.system_prompt(**kw)


@dataclass
class TrialResult:
    case: dict
    run: TrialRun
    verdict: Verdict
    trial: int
    world_seed: int
    settings: LoopSettings
    loop: str

    def trajectory(self) -> dict:
        return {"case_id": self.case["id"], "trial": self.trial, "world_seed": self.world_seed,
                "loop": self.loop, "menu": self.run.menu,
                "turns": [{"user": t.user, "session": t.session, **t.trace.to_record()} for t in self.run.turns],
                "approvals": self.run.approver_log, "faults_fired": self.run.faults_fired,
                "trim_events": self.run.trim_events}


def _estimate_tokens(messages: list[dict]) -> int:
    return sum(len(json.dumps(m, ensure_ascii=False)) for m in messages) // CHARS_PER_TOKEN


def _trim(messages: list[dict], budget: int, turn_index: int, events: list[dict]) -> list[dict]:
    """Drop the oldest whole user turns until the history is under budget.

    The head (the system message, which holds the stored preferences) is never
    trimmed and does not count against the history budget (spec 02 §3.15).
    """
    head, body = messages[:1], messages[1:]
    if budget <= 0 or _estimate_tokens(body) <= budget:
        return messages
    starts = [i for i, m in enumerate(body) if m.get("role") == "user"]
    removed = 0
    before = _estimate_tokens(body)
    while len(starts) > 1 and _estimate_tokens(body) > budget:
        cut = starts[1]
        body = body[cut:]
        removed += 1
        starts = [i for i, m in enumerate(body) if m.get("role") == "user"]
    after = head + body
    events.append({"kind": "trim_event", "before_turn": turn_index, "turns_removed": removed,
                   "tokens_removed": before - _estimate_tokens(body)})
    return after


def run_trial(case: dict, client: ModelClient, *, trial: int = 1, world_seed: int | None = None,
              loop: AgentLoop | None = None, settings: LoopSettings | None = None,
              env_factory: Callable = default_env_factory, clock: Clock | None = None,
              menu_size: int | None = None, history_budget_tokens: int = 0,
              extra_history: list[dict] | None = None) -> TrialResult:
    if client.describe().get("kind") == "cloud_reference" and case.get("split", "dev") != "dev":
        raise HostedSplitError(f"{case['id']}: only the public development split is sent to hosted models")
    ws = world_seed if world_seed is not None else WORLD_SEED_BASE + trial - 1
    bound = bind(case, ws, clock=clock)
    ck: Clock = bound["_clock"]
    s = settings or LoopSettings()
    if s.seed is None:
        s = replace(s, seed=SAMPLING_SEED_BASE + trial)
    loop = loop or get_loop("reference")
    # A loop may decline a case its host cannot express (Harbor's loop: the
    # neutral lane, stored preferences without Harbor's fields, ...). The trial
    # is recorded as not applicable, never as the model's failure.
    why = loop.unsupported(bound) if hasattr(loop, "unsupported") else None
    if why:
        trace = TurnTrace(stop="not_applicable", excluded=True, not_applicable=why)
        empty = TrialRun(case=bound, env=None, initial=None, turns=[TurnRun(spec={}, trace=trace, after=None,
                                                                             user="", session=0)],
                         clock=ck)
        return TrialResult(case=bound, run=empty, verdict=grade(empty), trial=trial, world_seed=ws, settings=s,
                           loop=getattr(loop, "name", type(loop).__name__))
    profile = (bound.get("world") or {}).get("profile")
    base_env = env_factory(bound["lane"], ck, ws, profile=profile) if profile else env_factory(bound["lane"], ck, ws)
    records = (bound.get("world") or {}).get("seed_records") or {}
    if bound.get("inject"):
        records = plant_injection(records, bound["inject"])
    if records:
        base_env.seed(records)
    access = (bound.get("world") or {}).get("access_needed")
    if access and hasattr(base_env, "set_access_needed"):
        base_env.set_access_needed(access)
    env = FaultyEnvironment(base_env, bound.get("faults") or [], ck) if bound.get("faults") else base_env
    menu = select_menu(bound, base_env, ws, size=menu_size)
    if not history_budget_tokens and bound["family"] == "long_session":
        history_budget_tokens = LONG_SESSION_HISTORY_BUDGET
    approver = Approver(bound.get("approver", "approve"))
    if hasattr(loop, "begin_trial"):          # host loops read case-level settings (access, access_needed)
        loop.begin_trial(bound, env)
    own_history = getattr(loop, "own_history_policy", False)   # a host loop trims its own history
    system = build_system(env, bound, menu)
    initial = env.state().snapshot()
    turns: list[TurnRun] = []
    trims: list[dict] = []
    ti = 0
    for si, session in enumerate(bound["sessions"]):
        messages: list[dict] = [{"role": "system", "content": system}]
        if si == 0 and extra_history:          # scaling probe: unrelated earlier turns (§3.10)
            messages.extend(dict(m) for m in extra_history)
        for turn in session.get("turns", []):
            for h in turn.get("history") or []:
                messages.append(dict(h))
            messages.append({"role": "user", "content": turn["user"]})
            if history_budget_tokens and not own_history:
                messages[:] = _trim(messages, history_budget_tokens, ti, trims)
            trace = loop.run_turn(client, env, messages, menu=menu, settings=s, approver=approver)
            turns.append(TurnRun(spec=turn, trace=trace, after=env.state().snapshot(), user=turn["user"],
                                 session=si))
            ti += 1
            if trace.excluded:
                break
        if turns and turns[-1].trace.excluded:
            break
    run = TrialRun(case=bound, env=env, initial=initial, turns=turns, clock=ck,
                   approver_log=list(approver.asked),
                   faults_fired=list(getattr(env, "fired", [])), menu=menu, trim_events=trims)
    verdict = grade(run, blocked_proposals_fail=getattr(loop, "blocked_proposals_fail", True))
    return TrialResult(case=bound, run=run, verdict=verdict, trial=trial, world_seed=ws, settings=s,
                       loop=getattr(loop, "name", type(loop).__name__))
