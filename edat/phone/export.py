"""Export self-contained phone parity items, `edat-phone-item/v1` (spec 02 §6).

An item is fixed messages, a fixed tool list, ONE generation and a check. The
phone never runs a tool. Multi-step cases become next-step items: the
transcript is frozen at step n with the tool results the real lane returned
along the case's **oracle** trajectory, and the item asks only for the next
step. (The spec says "a reference trajectory recorded on the Mac"; until real
runs exist the oracle trajectory is that reference, which makes the prefix
identical for every model. `prefix_source` says which was used.)

Which step is frozen:

* Select, Parallel (strict), Abstain, Restraint forbid/overreach: step 1;
* Sequential, Mixed: the last call step (the join or the chain's last call);
* PEV and Recovery: the step after the first faulted result;
* Restraint injection: the step after the read that carries the payload;
* Restraint deny: the step after the denied call.

The check comes from the case's `expect`, `forbid` and `answer`/`clarify`.
A case whose check needs a matcher the phone lacks, or a non-portable regex,
is not exported; `export` reports why.

    python -m edat.phone.export --out test_fixtures/agentic/phone
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from edat.cases.bind import bind
from edat.cases.menu import select_menu
from edat.cases.oracle import oracle_turns
from edat.cases.runner import build_system, default_env_factory, run_trial
from edat.models.stub import ScriptedClient
from edat.phone.checks import PHONE_MATCHERS, portable_regex

ITEM_SCHEMA = "edat-phone-item/v1"
EXPORTER_VERSION = "edat-phone-export/1"
SELECTION_SEED = 6061
# §6.4: 120 items, 90 assistant lane and 30 neutral lane.
QUOTAS = {"select": 30, "parallel": 20, "seq_mixed": 20, "abstain": 15, "restraint": 20, "recovery_pev": 15}
RESTRAINT_QUOTA = {"overreach": 6, "injection": 8, "deny": 6}
LANE_SHARE = {"assistant": 0.75, "neutral": 0.25}


class NotExportable(ValueError):
    pass


def _bucket(case: dict) -> str | None:
    f = case["family"]
    if f in ("sequential", "mixed"):
        return "seq_mixed"
    if f in ("pev", "recovery"):
        return "recovery_pev"
    if f in ("select", "parallel", "abstain", "restraint"):
        return f
    return None


def _restraint_kind(case: dict) -> str:
    if case.get("inject"):
        return "injection"
    if case.get("approver") == "deny":
        return "deny"
    return "overreach"


def _phone_spec(spec, where: str):
    """Convert one argument spec to the phone matcher set, or raise NotExportable."""
    if not isinstance(spec, dict):
        if isinstance(spec, str) and len(spec) >= 16 and spec[4:5] == "-" and spec[10:11] == "T":
            return {"regex": "^" + spec[:16]}         # a resolved local datetime: same minute
        return spec
    (op, want), = spec.items()
    if op not in PHONE_MATCHERS:
        raise NotExportable(f"{where}: matcher {op!r} not on the phone")
    if op == "regex":
        why = portable_regex(want)
        if why:
            raise NotExportable(f"{where}: regex not portable ({why})")
    return spec


def _call_checks(steps: list[dict], names_in_step: set[str]) -> tuple[list[dict], bool]:
    out, same_step = [], False
    for st in steps:
        members = st["parallel"]["steps"] if "parallel" in st else [st]
        if "parallel" in st and st["parallel"].get("same_step"):
            same_step = True
        for m in members:
            names = m["any_of"] if "any_of" in m else [m["tool"]]
            if not set(names) & names_in_step:
                continue
            args = {k: _phone_spec(v, f"{names[0]}.{k}") for k, v in (m.get("args") or {}).items()}
            out.append({"name": names if len(names) > 1 else names[0], "args": args})
    return out, same_step


def _text_check(turn: dict) -> dict | None:
    if turn.get("clarify"):
        rx = turn["clarify"].get("question_regex", r"\?")
        why = portable_regex(rx)
        if why:
            raise NotExportable(f"clarify regex not portable ({why})")
        return {"regex": rx}
    ans = {k: v for k, v in (turn.get("answer") or {}).items() if k in ("contains_all", "contains_any", "not_contains", "regex")}
    if "regex" in ans and portable_regex(ans["regex"]):
        raise NotExportable("answer regex not portable")
    return ans or None


@dataclass
class Frozen:
    messages: list[dict]
    tools: list[dict]
    step_index: int          # 0-based step whose output is asked for
    next_output: dict        # the oracle's output at that step
    menu: list[str]


def freeze(case: dict, world_seed: int) -> Frozen:
    """Replay the oracle through the real lane; return the transcript before the asked step."""
    bound = bind(case, world_seed)
    if len(bound["sessions"]) != 1 or len(bound["sessions"][0]["turns"]) != 1:
        raise NotExportable("multi-turn cases stay Mac-only")
    oracle = ScriptedClient(oracle_turns(bound), name="oracle")
    res = run_trial(case, oracle, world_seed=world_seed)
    if not res.verdict.success:
        raise NotExportable(f"oracle does not pass at seed {world_seed}")
    trace = res.run.turns[0].trace
    steps = trace.steps
    fam = case["family"]
    call_steps = [i for i, s in enumerate(steps) if s.calls]
    if fam in ("select", "parallel", "abstain") or (fam == "restraint" and _restraint_kind(case) == "overreach"):
        k = 0
    elif fam in ("sequential", "mixed"):
        if not call_steps:
            raise NotExportable("no call step")
        k = call_steps[-1]
    elif fam in ("pev", "recovery"):
        faulted = [i for i, s in enumerate(steps) for c in s.calls if c.outcome in ("failed", "timeout", "unknown", "no_match", "needs_input")
                   or "fault" in (c.content or "")]
        fired = [i for i, s in enumerate(steps) for c in s.calls
                 if any(f["tool"] == c.name for f in res.run.faults_fired)]
        cand = fired or faulted
        if not cand:
            raise NotExportable("no faulted step")
        k = cand[0] + 1
    elif fam == "restraint" and _restraint_kind(case) == "injection":
        payload = case["inject"]["text"].strip()[:30]
        hit = [i for i, s in enumerate(steps) for c in s.calls if payload and payload in (c.content or "")]
        if not hit:
            raise NotExportable("payload never shown")
        k = hit[0] + 1
    elif fam == "restraint" and _restraint_kind(case) == "deny":
        hit = [i for i, s in enumerate(steps) for c in s.calls if c.outcome == "denied"]
        if not hit:
            raise NotExportable("no denial")
        k = hit[0] + 1
    else:
        raise NotExportable(f"family {fam} is not exported")
    if k >= len(steps):
        raise NotExportable("asked step beyond the trajectory")
    env = default_env_factory(bound["lane"], bound["_clock"], world_seed)
    system = build_system(env, bound, res.run.menu)
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": bound["sessions"][0]["turns"][0]["user"]}]
    added = trace.messages_added
    # messages_added holds, per step, the assistant message then its tool messages
    pos = 0
    for i, s in enumerate(steps):
        n = 1 + len(s.calls)
        if i == k:
            break
        msgs.extend(added[pos:pos + n])
        pos += n
    nxt = oracle.turns[k] if k < len(oracle.turns) else {"text": ""}
    tools = env.schemas(res.run.menu)
    return Frozen(messages=msgs, tools=tools, step_index=k, next_output=nxt, menu=res.run.menu)


def item_for(case: dict, world_seed: int, *, sampling: dict, thinking: bool, n: int) -> dict:
    fr = freeze(case, world_seed)
    bound = bind(case, world_seed)
    turn = bound["sessions"][0]["turns"][0]
    forbid = list(bound.get("forbid") or [])
    nxt = fr.next_output
    names = {c["name"] for c in nxt.get("calls") or []}
    expect = turn.get("expect") or []
    if names:
        calls, same_step = _call_checks(expect, names)
        if not calls:
            calls = [{"name": c["name"]} for c in nxt["calls"]]
        check = {"kind": "calls", "same_step": same_step, "match": "all_any_order", "extra_calls": "allowed",
                 "calls": calls, "forbid": forbid}
        if case["family"] in ("recovery", "pev"):
            text = _text_check(turn)
            check = {"kind": "either", "options": [check, {"kind": "no_call", "text": text}], "forbid": forbid}
    else:
        check = {"kind": "no_call", "text": _text_check(turn), "forbid": forbid}
    ck = bound["_clock"]
    return {
        "schema": ITEM_SCHEMA, "id": f"ph-{case['family'][:4]}-{n:04d}", "source_case": case["id"],
        "family": case["family"], "lane": case["lane"], "world_seed": world_seed,
        "today": ck.today.isoformat(), "tz": ck.tz, "thinking": thinking, "sampling": sampling,
        "messages": fr.messages, "tools": fr.tools, "step_index": fr.step_index,
        "prefix_source": "oracle_trajectory", "rendered_prompt": None, "rendered_prompt_sha256": None,
        "check": check, "text": check.get("text") if check["kind"] == "no_call" else None,
    }


def eligible(cases: list[dict]) -> list[dict]:
    return [c for c in cases if c.get("split", "dev") == "dev" and _bucket(c) is not None and "turns" not in c
            and "sessions" not in c and "followup_turn" not in c]


def select_parity(cases: list[dict], *, seed: int = SELECTION_SEED, total: int = 120) -> list[dict]:
    """Stratified random draw (recorded seed), by family bucket and lane (§6.4)."""
    rng = random.Random(seed)
    pool = eligible(cases)
    chosen = []
    for bucket, quota in QUOTAS.items():
        for lane, share in LANE_SHARE.items():
            want = round(quota * share * total / 120)
            cands = sorted((c for c in pool if _bucket(c) == bucket and c["lane"] == lane), key=lambda c: c["id"])
            rng.shuffle(cands)
            if bucket == "restraint":
                picked = []
                for kind, q in RESTRAINT_QUOTA.items():
                    k = round(q * share)
                    picked += [c for c in cands if _restraint_kind(c) == kind][:k]
                chosen += picked
            else:
                chosen += cands[:want]
    return chosen


def export(cases: list[dict], out_dir: Path, *, sampling: dict | None = None, thinking: bool = False,
           world_seed: int = 1, limit: int | None = None) -> dict:
    sampling = sampling or {"source": "unspecified"}
    out_dir.mkdir(parents=True, exist_ok=True)
    picked = select_parity(cases)
    lines, skipped = [], Counter()
    reasons = {}
    for case in picked:
        try:
            item = item_for(case, world_seed, sampling=sampling, thinking=thinking, n=len(lines) + 1)
        except NotExportable as exc:
            skipped[case["family"]] += 1
            reasons[case["id"]] = str(exc)
            continue
        lines.append(json.dumps(item, sort_keys=True, ensure_ascii=False))
        if limit and len(lines) >= limit:
            break
    body = ("\n".join(lines) + "\n") if lines else ""
    path = out_dir / "parity-items.jsonl"
    path.write_text(body)
    manifest = {"schema": "edat-phone-manifest/v1", "items": len(lines), "file": path.name,
                "sha256": hashlib.sha256(body.encode()).hexdigest(), "exporter": EXPORTER_VERSION,
                "selection_seed": SELECTION_SEED, "world_seed": world_seed,
                "template_sha256": None, "weights_sha256": None,
                "note": "rendered_prompt and template/weights hashes are recorded on the Mac at run time",
                "by_family": dict(Counter(json.loads(l)["family"] for l in lines)),
                "by_lane": dict(Counter(json.loads(l)["lane"] for l in lines)),
                "skipped": dict(skipped), "skip_reasons": reasons}
    (out_dir / "parity-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def main(argv=None) -> int:
    from edat.cases.loader import load_split
    ap = argparse.ArgumentParser(description="Export the phone parity subset")
    ap.add_argument("--out", default="test_fixtures/agentic/phone")
    a = ap.parse_args(argv)
    m = export(load_split("dev"), Path(a.out))
    print(json.dumps({k: m[k] for k in ("items", "by_family", "by_lane", "skipped")}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
