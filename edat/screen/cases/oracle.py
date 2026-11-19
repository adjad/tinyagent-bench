"""The scripted oracle and the solvability check (spec 11 §3.12, §7).

A case's `oracle` is its author's solution as a list of abstract steps over
stable element keys, never per-trial ids:

    - {click: settings.battery.low_power}
    - {type: shop.checkout.name, text: "{ship_name}", submit: false}
    - {select: settings.battery.display_off, option: Never}
    - {key: Enter} | {back: true} | {scroll: down}
    - {ask: "Ready to place the order for ...? Place it?"}
    - {finish: "{open_time} to {close_time}"}      # or {finish: true}

The oracle reads the screen it is shown, like a model: it resolves a key to
the id on the latest observation, and when the element exists but is off the
visible part, it scrolls towards it first (scrolls are real steps and count).

`check_case` proves, on world seeds 1, 2 and 3, that

1. the oracle passes within the case's step limit,
2. a model that does nothing (answers "OK." and calls nothing) fails, and
3. a model that calls `finish` at once fails,

and returns the oracle's step count, from which the build sets `optimal` and
`max_steps`.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field

from edat.screen.adapter import NullClient, PolicyClient, ScriptedClient
from edat.screen.cases.bind import Bound, bind, max_steps_for
from edat.screen.grade.grader import grade
from edat.screen.loop import ScreenLoop, ScreenSettings
from edat.screen.world.core import VIEWPORT_LINES

CHECK_SEEDS = (1, 2, 3)


class OracleStuck(RuntimeError):
    pass


def _call(name: str, args: dict, n: int) -> dict:
    return {"calls": [{"id": f"oracle_{n}", "name": name, "arguments": args}]}


class Oracle:
    def __init__(self, bound: Bound) -> None:
        self.bound = bound
        self.plan = list(bound.case.get("oracle") or [])
        self.pos = 0
        self.n = 0
        self.stuck: str | None = None
        self.scrolls = 0

    def _locate(self, key: str):
        vis = self.bound.world.observe()
        for i, e in vis.by_id.items():
            if e.key == key:
                return i, None
        if any(e.key == key for e in vis.behind.values()):
            return None, None
        for i, e in vis.all_ids.items():
            if e.key == key:
                order = [k for k in vis.all_ids if not vis.all_ids[k].meta.get("dock")]
                idx = order.index(i) if i in order else 0
                return None, ("down" if idx >= vis.offset + VIEWPORT_LINES else "up")
        return None, None

    def __call__(self, messages, tools, index):
        self.n += 1
        if self.pos >= len(self.plan):
            return {"text": "Done."}
        step = self.plan[self.pos]
        if "finish" in step:
            self.pos += 1
            ans = step["finish"]
            return _call("finish", {} if ans in (True, None) else {"answer": str(ans)}, self.n)
        if "ask" in step:
            self.pos += 1
            return _call("ask_user", {"question": step["ask"]}, self.n)
        if "key" in step:
            self.pos += 1
            return _call("press_key", {"key": step["key"]}, self.n)
        if "back" in step:
            self.pos += 1
            return _call("go_back", {}, self.n)
        if "scroll" in step:
            self.pos += 1
            return _call("scroll", {"direction": step["scroll"]}, self.n)
        verb = next(k for k in ("click", "type", "select") if k in step)
        key = step[verb]
        el_id, direction = self._locate(key)
        if el_id is None and direction is not None:
            self.scrolls += 1
            if self.scrolls > 12:
                self.stuck = f"scrolling forever towards {key}"
                return {"text": "stuck"}
            return _call("scroll", {"direction": direction}, self.n)
        if el_id is None:
            self.stuck = f"step {self.pos + 1}: {key} is not on the screen ({self.bound.world.front} / " \
                         f"{self.bound.world.frame()['screen']})"
            return {"text": "stuck"}
        self.scrolls = 0
        self.pos += 1
        if verb == "click":
            return _call("click", {"id": el_id}, self.n)
        if verb == "type":
            args = {"id": el_id, "text": str(step.get("text", ""))}
            if step.get("submit"):
                args["submit"] = True
            return _call("type_text", args, self.n)
        return _call("select_option", {"id": el_id, "option": str(step["option"])}, self.n)


def run_oracle(case: dict, world_seed: int, *, max_steps: int | None = 24, track_loop: ScreenLoop | None = None):
    bound = bind(case, world_seed)
    oracle = Oracle(bound)
    ep = (track_loop or ScreenLoop()).run(PolicyClient(oracle, name="oracle"), bound,
                                          ScreenSettings(max_steps=max_steps))
    return bound, ep, oracle


@dataclass
class CheckResult:
    case_id: str
    ok: bool
    optimal: int = 0
    problems: list[str] = field(default_factory=list)
    per_seed: dict = field(default_factory=dict)


def check_case(case: dict, seeds=CHECK_SEEDS, *, set_budget: bool = False) -> CheckResult:
    res = CheckResult(case_id=case.get("id", "?"), ok=True)
    steps = []
    for s in seeds:
        try:
            bound, ep, oracle = run_oracle(case, s, max_steps=24)
        except Exception as exc:  # a broken case is a finding, not a crash
            res.ok = False
            res.problems.append(f"seed {s}: {type(exc).__name__}: {exc}")
            continue
        if oracle.stuck:
            res.ok = False
            res.problems.append(f"seed {s}: oracle stuck: {oracle.stuck}")
        v = grade(bound, ep, 1)
        res.per_seed[s] = {"steps": ep.steps, "success": v.success, "problems": v.problems,
                           "events": [e["kind"] for e in v.events]}
        steps.append(ep.steps)
        if not v.success:
            res.ok = False
            res.problems.append(f"seed {s}: oracle fails: {v.problems} events={[e['kind'] for e in v.events]} "
                                f"stop={ep.stop}")
    if not steps:
        return res
    res.optimal = max(steps)
    budget = int(case.get("max_steps") or max_steps_for(res.optimal))
    if res.optimal > budget:
        res.ok = False
        res.problems.append(f"oracle needs {res.optimal} steps; the limit is {budget}")
    if set_budget:
        case["optimal"] = {"steps": res.optimal}
        case["max_steps"] = budget
    probe = copy.deepcopy(case)
    probe.setdefault("optimal", {"steps": res.optimal})
    probe.setdefault("max_steps", budget)
    for s in seeds:
        for label, client in (("null", NullClient()),
                              ("finish-at-once", ScriptedClient([{"calls": [{"name": "finish", "arguments": {}}]}],
                                                                on_exhaust="stop"))):
            b = bind(probe, s)
            ep = ScreenLoop().run(client, b, ScreenSettings())
            v = grade(b, ep, 1)
            if v.success:
                res.ok = False
                res.problems.append(f"seed {s}: passes with the {label} model")
    return res


def main(argv=None) -> int:
    import argparse
    import sys
    from edat.screen.cases.store import load_cases
    ap = argparse.ArgumentParser(description="Check that every screen case is solvable and not passable by doing nothing")
    ap.add_argument("paths", nargs="+")
    a = ap.parse_args(argv)
    bad = 0
    cases = [c for p in a.paths for c in load_cases(p)]
    for c in cases:
        r = check_case(c)
        if not r.ok:
            bad += 1
            print(json.dumps({"case": r.case_id, "problems": r.problems}))
    print(json.dumps({"cases": len(cases), "failing": bad}))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
