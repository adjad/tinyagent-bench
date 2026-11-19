"""Score one captured Brief against the ledger. Pure.

The captured Brief is the `proactive` snapshot (GET /assistant/proactive after
an Analyze), i.e. what the Brief tab's READY FOR YOU and YOUR LISTS zones show:
cards, to-dos and threads, in display order. Each becomes one item with its
visible text and its source kind.

Model-written vs code-written: to-dos whose source is calendar, reminders or
notes come from deterministic code (service/proactive/cycle.py
`deterministic_todos`) and are the same for every model. Cards, mail/message
to-dos and thread summaries come from the model's triage and judge. Recall is
reported for both, and the model score uses only ledger items whose sources
are mail or messages (the part a model can win or lose).

Measures:
  recall           expected attention items found (any anchor in any item)
  hidden_recall    the subset with hidden=true (exists only inside mail/message text)
  false_alarms     planted noise items surfaced
  stale            superseded values shown without the current one
  order            share of correctly ordered pairs (by ledger priority) among found items
  length           items shown and visible words
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from scripts.productbench import matcher
from scripts.productbench.profile import Profile

DETERMINISTIC_SOURCES = {"calendar", "reminders", "notes"}
_MODEL_KINDS = ("emails", "threads")


def items_from_snapshot(snapshot: dict) -> list[dict]:
    """Flatten a proactive snapshot into display-order items: {zone, text, source}."""
    out = []
    for c in snapshot.get("cards") or []:
        quotes = " ".join(str(s.get("quote") or "") + " " + str(s.get("who") or "")
                          for s in c.get("sources") or [])
        action = c.get("action") or {}
        out.append({"zone": "card", "source": "model",
                    "text": " ".join(filter(None, [c.get("title"), quotes, action.get("draft")]))})
    for t in snapshot.get("todos") or []:
        src = str(t.get("source") or "")
        out.append({"zone": "todo",
                    "source": "code" if src in DETERMINISTIC_SOURCES else "model",
                    "text": " ".join(filter(None, [t.get("title"), t.get("who"), t.get("quote"),
                                                   t.get("due_label")]))})
    for th in snapshot.get("threads") or []:
        out.append({"zone": "thread", "source": "model",
                    "text": " ".join(filter(None, [th.get("title"), th.get("who"),
                                                   th.get("summary")]))})
    return out


def _source_kinds(profile: Profile, item: dict) -> set[str]:
    kinds = set()
    for src in item.get("sources", []):
        for coll in ("calendar", "reminders", "emails", "threads", "notes"):
            if any(r["id"] == src for r in profile.data.get(coll, [])):
                kinds.add(coll)
    return kinds


@dataclass
class BriefScore:
    score: float = 0.0
    recall: dict = field(default_factory=dict)
    model_recall: dict = field(default_factory=dict)
    hidden_recall: dict = field(default_factory=dict)
    false_alarms: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    order: dict = field(default_factory=dict)
    length: dict = field(default_factory=dict)
    state: str = ""
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _rate(found: list[str], expected: list[str]) -> dict:
    return {"found": found, "missed": [e for e in expected if e not in found],
            "rate": (round(len(found) / len(expected), 3) if expected else None)}


def score_brief(snapshot: dict, profile: Profile, checks: dict) -> BriefScore:
    """`checks`: {"expect": [ledger ids] (default: attention items not superseded),
    "noise": [ids] (default: type noise), "max_items": int, "max_words": int}."""
    today = profile.anchor.date()
    items = items_from_snapshot(snapshot)
    s = BriefScore(state=str(snapshot.get("state") or ""))
    superseded = {it["supersedes"] for it in profile.ledger if it.get("supersedes")}
    expect = checks.get("expect") or [it["id"] for it in profile.ledger
                                      if it.get("attention") and it["id"] not in superseded]
    noise = checks.get("noise") or [it["id"] for it in profile.ledger if it["type"] == "noise"]

    first_pos: dict[str, int] = {}
    for lid in expect:
        anchors = profile.item(lid).get("anchors", [])
        for pos, it in enumerate(items):
            if matcher.any_hit(anchors, it["text"], today):
                first_pos[lid] = pos
                break
    found = [lid for lid in expect if lid in first_pos]
    s.recall = _rate(found, expect)
    model_expect = [lid for lid in expect
                    if _source_kinds(profile, profile.item(lid)) & set(_MODEL_KINDS)]
    s.model_recall = _rate([lid for lid in found if lid in model_expect], model_expect)
    hidden = [lid for lid in expect if profile.item(lid).get("hidden")]
    s.hidden_recall = _rate([lid for lid in found if lid in hidden], hidden)

    all_text = "\n".join(it["text"] for it in items)
    s.false_alarms = [lid for lid in noise
                      if matcher.any_hit(profile.item(lid).get("anchors", []), all_text, today)]
    for it in profile.ledger:
        stale = it.get("stale_anchors") or []
        if not stale:
            continue
        for row in items:
            if (matcher.any_hit(stale, row["text"], today)
                    and not matcher.any_hit(it.get("current_anchors") or it.get("anchors", []),
                                            row["text"], today)):
                s.stale.append(f"{it['id']}: {row['text'][:80]}")
                break

    pairs = ok = 0
    for i, a in enumerate(found):
        for b in found[i + 1:]:
            pa, pb = profile.item(a).get("priority", 3), profile.item(b).get("priority", 3)
            if pa == pb:
                continue
            pairs += 1
            if (pa < pb) == (first_pos[a] < first_pos[b]):
                ok += 1
    s.order = {"pairs": pairs, "correct": ok, "rate": round(ok / pairs, 3) if pairs else None}

    words = len(re.findall(r"\w+", all_text))
    s.length = {"items": len(items), "model_items": sum(1 for it in items if it["source"] == "model"),
                "words": words, "max_items": checks.get("max_items"),
                "max_words": checks.get("max_words"),
                "over": bool((checks.get("max_items") and len(items) > checks["max_items"])
                             or (checks.get("max_words") and words > checks["max_words"]))}

    base = s.model_recall["rate"] if s.model_recall["rate"] is not None else (s.recall["rate"] or 0.0)
    penalty = (0.1 * len(s.false_alarms) + 0.15 * len(s.stale)
               + (0.05 if s.length["over"] else 0.0))
    order_bonus = 0.0 if s.order["rate"] is None else 0.1 * (s.order["rate"] - 0.5)
    s.score = round(max(0.0, min(1.0, base - penalty + order_bonus)), 3)
    if s.state != "ready":
        s.notes.append(f"brief state {s.state!r}: scored as captured")
    return s
