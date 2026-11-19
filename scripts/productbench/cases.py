"""Load and check a question file against its profile.

A question file (test_fixtures/productbench/<profile>/cases.json) holds:

  ask    Ask cases: id, family, qtype, prompt, required, bonus, forbidden,
         forbidden_patterns, expect_refusal, tools_any, act, followup, reference_words,
         at ("HH:MM": ask it later on the anchor day; the harness restarts on that clock)
  brief  Brief checks: expect (ledger ids; default every attention item that is
         not superseded), noise (default every noise item), max_items, max_words
  day    plans: planning requests sent through Ask ({id, kind: plan|slot, prompt,
         minutes}); rules: optional overrides ({tasks: [...]})

Every ledger id a case names must exist, and no case may mention the Study tab
or its tools: the benchmark excludes Study.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from scripts.productbench.profile import Profile

FAMILIES = {"today", "changed", "owe", "waiting", "cross_source", "no_answer", "followup", "act",
            "memory", "plan", "privacy", "todo", "inbox", "messages", "window"}
# The Study tab's own features, not the word "study" (a student's "study group" is fine).
_STUDY = re.compile(r"\b(study tab|flash ?cards?|quiz me|lecture slides?|study guide|study_slides|study_make)\b", re.I)


class CaseError(ValueError):
    pass


def _ids(entries) -> list[str]:
    out = []
    for e in entries or []:
        if isinstance(e, str):
            out.append(e[:-6] if e.endswith(".stale") else e)
        elif isinstance(e, dict) and e.get("ledger"):
            out.append(e["ledger"])
    return [i for i in out if not i.startswith(("@", "#")) and re.fullmatch(r"[A-Za-z][-_][\w-]+", i)]


def check(data: dict, profile: Profile) -> None:
    lids = {it["id"] for it in profile.ledger}
    seen: set[str] = set()

    def need(cond, msg):
        if not cond:
            raise CaseError(msg)

    def check_case(c, where):
        need(c.get("id") and c["id"] not in seen, f"{where}: missing or duplicate id {c.get('id')!r}")
        seen.add(c["id"])
        need(bool(c.get("prompt")), f"{c['id']}: no prompt")
        need(not _STUDY.search(c["prompt"]), f"{c['id']}: Study is out of scope")
        for key in ("required", "bonus", "forbidden"):
            for lid in _ids(c.get(key)):
                need(lid in lids, f"{c['id']}: unknown ledger id {lid!r} in {key}")
        for p in c.get("forbidden_patterns", []):
            re.compile(p)
        if c.get("at") is not None:
            need(re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(c["at"])) is not None,
                 f"{c['id']}: at must be HH:MM")
            need(str(c["at"]) > profile.data["anchor"][11:16],
                 f"{c['id']}: at {c['at']} must be later than the anchor's now")

    for c in data.get("ask", []):
        check_case(c, "ask")
        need(c.get("family") in FAMILIES, f"{c['id']}: unknown family {c.get('family')!r}")
        if c.get("followup"):
            check_case(c["followup"], f"{c['id']} followup")
    for c in data.get("day", {}).get("plans", []):
        check_case(c, "day")
        need(c.get("kind") in ("plan", "slot"), f"{c['id']}: kind must be plan or slot")
    b = data.get("brief", {})
    for key in ("expect", "noise"):
        for lid in b.get(key) or []:
            need(lid in lids, f"brief: unknown ledger id {lid!r} in {key}")
    for lid in (data.get("day", {}).get("rules") or {}).get("tasks") or []:
        need(lid in lids, f"day rules: unknown ledger id {lid!r}")


def load(path: str | Path, profile: Profile) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    check(data, profile)
    return data


FIXTURES = Path(__file__).resolve().parents[2] / "test_fixtures" / "productbench"


def for_profile(profile: Profile, explicit: str | Path | None = None) -> tuple[dict, list[str]]:
    """The question set for a profile, and where it came from.

    An explicit file wins. Otherwise: cases.json beside a productbench/1 profile;
    for a shared profile, the cases generated from its ledger plus any
    hand-written test_fixtures/productbench/<id>/cases.json (appended, ids unique).
    """
    if explicit:
        return load(explicit, profile), [str(explicit)]
    sources: list[str] = []
    data: dict = {"ask": [], "brief": {}, "day": {"plans": []}}
    if profile.generated_cases:
        data = json.loads(json.dumps(profile.generated_cases))
        sources.append("generated from ledger.json")
    beside = profile.path.with_name("cases.json") if profile.path else None
    hand = FIXTURES / profile.id / "cases.json"
    for path in (beside, hand):
        if path and path.exists() and str(path) not in sources:
            extra = json.loads(path.read_text(encoding="utf-8"))
            have = {c["id"] for c in data["ask"]}
            data["ask"] += [c for c in extra.get("ask", []) if c["id"] not in have]
            data["brief"] = {**data.get("brief", {}), **extra.get("brief", {})}
            plans = extra.get("day", {}).get("plans")
            if plans:
                data["day"]["plans"] = plans
            if extra.get("day", {}).get("rules"):
                data["day"]["rules"] = extra["day"]["rules"]
            sources.append(str(path))
    check(data, profile)
    return data, sources
