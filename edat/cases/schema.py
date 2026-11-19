"""The extended case schema (spec 02 §2.4) and its normalised form.

A case file is YAML: a list of cases, or `{defaults: {...}, cases: [...]}`
where `defaults` fills keys every case leaves out (lane, family, split, ...).

Every case is normalised to `sessions -> turns`, whatever form it was written
in:

* a single `prompt` (with optional `history`) is one session with one turn;
  the case-level checks belong to that turn;
* `followup_turn` adds a second turn with its own checks;
* `turns: [...]` is one session of scripted turns; case-level checks, if any,
  join the last turn;
* `sessions: [{turns: [...]}, ...]` is several sessions: the conversation
  history is cleared between sessions, the world (and its memory store) is
  kept (§3.16).

A turn is graded when it carries at least one check. A case passes a trial
only if every graded turn passes and the trial has no violation.
"""
from __future__ import annotations

import copy

LANES = ("assistant", "neutral")
FAMILIES = ("select", "parallel", "sequential", "mixed", "pev", "restraint", "recovery", "abstain",
            "preferences", "long_session", "memory")
# The seven families in the Agentic Score (§5.3 #1).
HEADLINE_FAMILIES = ("select", "parallel", "sequential", "mixed", "pev", "recovery", "abstain")
SPLITS = ("dev", "heldout")
TAGS = ("privacy", "datetime", "noisy", "oversized", "injection", "deny", "overreach", "confirm",
        "counter", "buried", "overflow", "similar_name", "third_party", "web_exfil", "authorized",
        "irrelevance", "missing_param", "missing_function", "ungrounded", "transient", "persistent",
        "empty", "timeout", "stall", "partial", "unavailable", "unknown_outcome", "wrong_result",
        "core", "phone", "remember", "update", "forget", "gate")
APPROVERS = ("approve", "deny", "timeout")
FAULT_KINDS = ("error", "empty", "timeout", "stall", "partial", "unavailable", "unknown_outcome")
CARRIERS = ("email", "message", "web", "file", "calendar", "contact", "note", "image", "spoken")

# Checks that belong to one user turn.
TURN_CHECK_KEYS = ("expect", "final_state", "answer", "claims", "clarify", "alt_pass", "verify",
                   "grounded_args")
TURN_KEYS = TURN_CHECK_KEYS + ("user", "oracle", "band", "probe", "history")

CASE_KEYS = frozenset({
    # identity and grouping
    "id", "lane", "family", "split", "sub", "tags", "trap", "core", "notes", "source",
    "pair_id", "variant_of", "variant", "carrier", "dimension", "arrival", "pref_item", "band",
    "media", "actionable",
    # the conversation
    "prompt", "history", "followup_turn", "turns", "sessions", "oracle",
    # the world
    "facts", "world", "stored_preferences", "system_policy", "menu_pin", "menu_extra",
    # host behaviour
    "approver", "faults", "inject", "confirm", "no_confirm",
    # checks
    "expect", "forbid", "answer", "final_state", "unchanged", "claims", "clarify", "alt_pass",
    "verify", "grounded_args", "optimal", "max_calls",
    "intended_recipients", "canaries_forbidden_in_outbound", "authorized_disclosures",
    "no_tool", "refused", "access",
})
WORLD_KEYS = frozenset({"seed_records", "clock", "tz", "access_needed"})


class CaseError(ValueError):
    pass


def problems(case: dict) -> list[str]:
    """Structural problems of one raw (unbound) case; empty when it is valid."""
    out = []
    cid = case.get("id")
    if not cid or not isinstance(cid, str):
        out.append("missing id")
    unknown = sorted(set(case) - CASE_KEYS)
    if unknown:
        out.append(f"unknown keys {unknown}")
    if case.get("lane") not in LANES:
        out.append(f"lane must be one of {LANES}")
    if case.get("family") not in FAMILIES:
        out.append(f"family must be one of {FAMILIES}")
    if case.get("split", "dev") not in SPLITS:
        out.append(f"split must be one of {SPLITS}")
    forms = [k for k in ("prompt", "turns", "sessions") if k in case]
    if len(forms) != 1:
        out.append(f"exactly one of prompt / turns / sessions, got {forms}")
    if "followup_turn" in case and "prompt" not in case:
        out.append("followup_turn needs prompt")
    world = case.get("world") or {}
    bad_world = sorted(set(world) - WORLD_KEYS)
    if bad_world:
        out.append(f"unknown world keys {bad_world}")
    if case.get("approver", "approve") not in APPROVERS:
        out.append(f"approver must be one of {APPROVERS}")
    for f in case.get("faults") or []:
        if f.get("kind") not in FAULT_KINDS:
            out.append(f"fault kind {f.get('kind')!r}")
        if "tool" not in f:
            out.append("fault without tool")
    for t in case.get("tags") or []:
        if t not in TAGS:
            out.append(f"unknown tag {t!r}")
    inj = case.get("inject")
    if inj is not None:
        if "into" not in inj or "text" not in inj or "attack_success" not in inj:
            out.append("inject needs into, text and attack_success")
    return out


def validate(case: dict) -> None:
    errs = problems(case)
    if errs:
        raise CaseError(f"{case.get('id', '?')}: " + "; ".join(errs))


def _turn_from_case(case: dict, user: str, history=None) -> dict:
    turn = {"user": user}
    if history:
        turn["history"] = history
    for k in TURN_CHECK_KEYS + ("oracle",):
        if k in case:
            turn[k] = copy.deepcopy(case[k])
    return turn


def normalise(case: dict) -> dict:
    """Return a copy with `sessions` filled in and turn checks moved into turns."""
    c = copy.deepcopy(case)
    if "sessions" in c:
        sessions = c["sessions"]
    elif "turns" in c:
        turns = c["turns"]
        if turns:
            last = turns[-1]
            for k in TURN_CHECK_KEYS:
                if k in c and k not in last:
                    last[k] = c[k]
        sessions = [{"turns": turns}]
    else:
        turns = [_turn_from_case(c, c["prompt"], c.get("history"))]
        if "followup_turn" in c:
            turns.append(copy.deepcopy(c["followup_turn"]))
        sessions = [{"turns": turns}]
    for s in sessions:
        for t in s.get("turns", []):
            bad = sorted(set(t) - set(TURN_KEYS))
            if bad:
                raise CaseError(f"{c.get('id')}: unknown turn keys {bad}")
    c["sessions"] = sessions
    return c


def graded(turn: dict) -> bool:
    return any(k in turn for k in ("expect", "final_state", "answer", "claims", "clarify", "alt_pass"))


def all_turns(case: dict) -> list[dict]:
    return [t for s in case["sessions"] for t in s.get("turns", [])]
