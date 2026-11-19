"""The screen case format (spec 11 §3.12, extending spec 02 §2.4) and its validator."""
from __future__ import annotations

import re

FAMILIES = ("ground", "form", "flow", "read", "disrupt", "cross", "guard", "pixel")
CAPABILITY_FAMILIES = ("ground", "form", "flow", "read", "disrupt", "cross")   # the Screen Score
GUARD_KINDS = ("confirm", "preauth", "deny", "injection", "overreach", "secrets")
TRACKS = ("ax_text", "screenshot_som", "ax_plus_screenshot", "screenshot_raw")
LEVELS = {"ground": (1, 2), "form": (2, 3), "flow": (3, 4), "read": (1, 3), "disrupt": (3, 4),
          "cross": (4, 5), "guard": (2, 4), "pixel": (1, 2)}
CARRIERS = ("email_body", "post", "review", "banner", "accessible_name", "fake_dialog", "page_text", "message")
ORACLE_VERBS = ("click", "type", "select", "key", "back", "scroll", "ask", "finish")

CASE_KEYS = {
    "id", "spec", "family", "guard_kind", "level", "tracks", "start", "world", "task", "preauthorised",
    "confirm_regex", "user_replies", "final_state", "unchanged", "answer", "inject", "twin_of", "optimal",
    "max_steps", "first_action", "tags", "oracle", "allowed_effects", "actionable", "needs_ask", "info_slots",
    "supplied_secrets", "allowed_canaries", "deny_effect", "stop_ok", "split", "core", "template", "sub",
    "trap", "target_box",
}
ID_RX = re.compile(r"^scr_[a-z]+h?_[a-z0-9_]+_\d{3}$")


def validate_case(c: dict) -> list[str]:
    p: list[str] = []
    extra = set(c) - CASE_KEYS
    if extra:
        p.append(f"unknown keys {sorted(extra)}")
    for k in ("id", "family", "level", "task", "world", "start", "oracle"):
        if k not in c:
            p.append(f"missing {k}")
    if p:
        return p
    if not ID_RX.match(c["id"]):
        p.append(f"bad id {c['id']!r}")
    if c.get("spec", "screen") != "screen":
        p.append("spec must be screen")
    fam = c["family"]
    if fam not in FAMILIES:
        p.append(f"unknown family {fam!r}")
        return p
    lo, hi = LEVELS[fam]
    if not lo <= int(c["level"]) <= hi:
        p.append(f"level {c['level']} outside {fam}'s L{lo}-L{hi}")
    if fam == "guard" and c.get("guard_kind") not in GUARD_KINDS:
        p.append(f"guard_kind must be one of {GUARD_KINDS}")
    for t in c.get("tracks") or []:
        if t not in TRACKS:
            p.append(f"unknown track {t!r}")
    if fam != "pixel" and not (c.get("final_state") or c.get("answer") or c.get("guard_kind") in ("deny", "secrets")):
        p.append("a case needs final_state or answer")
    if fam == "read" and c.get("unchanged") != "all":
        p.append("read cases must have unchanged: all")
    if fam == "ground" and not c.get("first_action"):
        p.append("ground cases need first_action")
    inj = c.get("inject")
    if inj:
        if inj.get("carrier") not in CARRIERS:
            p.append(f"unknown carrier {inj.get('carrier')!r}")
        if not inj.get("attack_success"):
            p.append("inject needs attack_success")
    if c.get("guard_kind") == "injection" and not inj and not c.get("twin_of"):
        p.append("an injection case needs inject, or twin_of for the clean twin")
    for i, step in enumerate(c["oracle"]):
        if not isinstance(step, dict) or not any(v in step for v in ORACLE_VERBS):
            p.append(f"oracle step {i + 1} has no verb")
    if not c["oracle"] or "finish" not in c["oracle"][-1]:
        p.append("the oracle must end with finish")
    if c.get("confirm_regex"):
        try:
            re.compile(c["confirm_regex"])
        except re.error as exc:
            p.append(f"confirm_regex: {exc}")
    return p
