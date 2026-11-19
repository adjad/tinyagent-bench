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

Every Brief item is classified against the key (`classify`, shared with the findings page):

  found            it shows an expected attention item: ONE Brief item carries that entry's
                   facts together (its `match` spec; a productbench/1 item without one: any
                   one of its anchors)
  wrongly raised   it shows a planted noise item (a key entry of type noise): a false alarm
  stale            it shows a superseded value (`stale_match`, or any `stale_anchors`) without
                   the current one. For a key entry that records no change (a request, an
                   unknowable, two similar names...), its must_not_include is a trap for an
                   answer about it, so it counts only in an item that shows that entry
                   ("Tunde's wedding is in April" is not a stale date for Kofi's)
  in the key       it matches some other key entry (an Ask-only fact, a done or superseded item)
  not in the key   it matches no key entry at all. Reported, never penalised: the key is not
                   exhaustive, so an item it does not list is unjudged, not noise.

Two figures (never one without the other):
  score            the model's Brief: model recall, less 0.10 per false alarm and 0.15 per stale
                   value in an item the model wrote (cards, needs attention, message and mail
                   to-dos)
  seen_score       the Brief as the user sees it: recall over everything on screen, less the same
                   penalties for every item, Harbor's own calendar, reminders and notes rows
                   included; `harbor_stale` lists the stale rows Harbor's code wrote ("Harbor
                   showed a stale calendar slot")

Measures:
  recall           expected attention items found
  hidden_recall    the subset with hidden=true (exists only inside mail/message text)
  false_alarms     planted noise items surfaced
  stale            superseded values shown without the current one
  not_in_key       Brief items that match no key entry (all, and model-written)
  unmatchable      expected entries the key gives nothing distinguishing to match on: left
                   out of recall and listed, never silently counted either way
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


def match_of(it: dict) -> dict | None:
    """What one Brief item must carry to show this ledger item (None: nothing to match on)."""
    if it.get("match"):
        return it["match"]
    if it.get("match_note"):
        return None
    anchors = list(it.get("anchors") or [])
    return {"all": [anchors], "need": 1} if anchors else None


def shows(it: dict, text: str, today) -> bool:
    return matcher.spec_hit(match_of(it), text, today)


# An item that says the old slot changed is not showing it as current ("the library cancelled 2B").
_CHANGED = re.compile(r"\b(moved|cancel+ed|cancels|instead|no longer|rescheduled|swapped|not the|"
                      r"now (?:at|on|in))\b", re.I)


# Shared-profile entry types whose must_not_include is a value the entry replaced.
CHANGE_TYPES = {"calendar_correction", "changed_detail", "two_source_fact"}
CHANGE_STATUSES = {"moved", "superseded", "cancelled", "changed"}


def records_a_change(it: dict) -> bool:
    """Whether this key item's `stale_anchors` are a replaced value. A productbench/1 item (no
    `edat_type`) always says so; a shared-profile entry when its type or status records a change."""
    return not it.get("edat_type") or it["edat_type"] in CHANGE_TYPES or it.get("status") in CHANGE_STATUSES


def shows_stale(it: dict, text: str, today) -> bool:
    """The superseded value shown without the current one ("moved from 2pm to 3pm" is right).
    A superseded calendar slot (`stale_match`) also needs no word saying it changed. For an entry
    that records no change, its must_not_include counts only in an item that shows the entry."""
    slot = bool(it.get("stale_match")) and matcher.spec_hit(it["stale_match"], text, today) \
        and not _CHANGED.search(text)
    said = matcher.any_hit(it.get("stale_anchors") or [], text, today) is not None
    if not records_a_change(it):
        return slot or (said and shows(it, text, today) and not _CHANGED.search(text))
    old = slot or said
    return old and matcher.any_hit(it.get("current_anchors") or it.get("anchors", []), text, today) is None


def expected_ids(profile: Profile, checks: dict) -> tuple[list[str], list[str]]:
    """(expected attention ids, unmatchable ids) for this Brief."""
    superseded = {it["supersedes"] for it in profile.ledger if it.get("supersedes")}
    expect = checks.get("expect") or [it["id"] for it in profile.ledger
                                      if it.get("attention") and it["id"] not in superseded]
    unmatchable = [lid for lid in expect if match_of(profile.item(lid)) is None]
    return [lid for lid in expect if lid not in unmatchable], unmatchable


def classify(items: list[dict], profile: Profile, checks: dict | None = None) -> dict:
    """Mark every Brief item against the key (see the module doc). Returns expect, unmatchable,
    first_pos {lid: position}, false_alarms [lid], stale [(lid, position)], marks (per item, a list
    of (mark, lid)) and not_in_key [positions]."""
    checks = checks or {}
    today = profile.anchor.date()
    expect, unmatchable = expected_ids(profile, checks)
    noise = checks.get("noise") or [it["id"] for it in profile.ledger if it["type"] == "noise"]
    keyed = [it for it in profile.ledger if not it.get("generated")]
    marks: list[list[tuple[str, str | None]]] = [[] for _ in items]
    first_pos: dict[str, int] = {}
    for lid in expect:
        it = profile.item(lid)
        for pos, row in enumerate(items):
            if shows(it, row["text"], today):
                first_pos[lid] = pos
                marks[pos].append(("found", lid))
                break
    false_alarms = []
    for lid in noise:
        it = profile.item(lid)
        hit = [pos for pos, row in enumerate(items) if shows(it, row["text"], today)]
        if hit:
            false_alarms.append(lid)
            for pos in hit:
                marks[pos].append(("wrongly_raised", lid))
    stale = []
    for it in keyed:
        if it["type"] == "noise" or not (it.get("stale_match") or it.get("stale_anchors")):
            continue
        for pos, row in enumerate(items):
            if shows_stale(it, row["text"], today):
                stale.append((it["id"], pos))
                marks[pos].append(("stale", it["id"]))
                break
    taken = set(expect) | set(noise)
    for it in keyed:
        if it["id"] in taken or it["id"] in unmatchable:
            continue
        for pos, row in enumerate(items):
            if not marks[pos] and shows(it, row["text"], today):
                marks[pos].append(("in_key", it["id"]))
    return {"expect": expect, "unmatchable": unmatchable, "first_pos": first_pos,
            "false_alarms": false_alarms, "stale": stale, "marks": marks,
            "not_in_key": [pos for pos, m in enumerate(marks) if not m]}


@dataclass
class BriefScore:
    score: float = 0.0
    recall: dict = field(default_factory=dict)
    model_recall: dict = field(default_factory=dict)
    hidden_recall: dict = field(default_factory=dict)
    false_alarms: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    model_false_alarms: list[str] = field(default_factory=list)
    model_stale: list[str] = field(default_factory=list)
    harbor_stale: list[str] = field(default_factory=list)
    seen_score: float | None = None
    not_in_key: dict = field(default_factory=dict)
    unmatchable: list[str] = field(default_factory=list)
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
    items = items_from_snapshot(snapshot)
    s = BriefScore(state=str(snapshot.get("state") or ""))
    c = classify(items, profile, checks)
    expect, first_pos = c["expect"], c["first_pos"]
    s.unmatchable = c["unmatchable"]
    found = [lid for lid in expect if lid in first_pos]
    s.recall = _rate(found, expect)
    model_expect = [lid for lid in expect
                    if _source_kinds(profile, profile.item(lid)) & set(_MODEL_KINDS)]
    s.model_recall = _rate([lid for lid in found if lid in model_expect], model_expect)
    hidden = [lid for lid in expect if profile.item(lid).get("hidden")]
    s.hidden_recall = _rate([lid for lid in found if lid in hidden], hidden)

    all_text = "\n".join(it["text"] for it in items)
    s.false_alarms = c["false_alarms"]
    s.stale = [f"{lid}: {items[pos]['text'][:80]}" for lid, pos in c["stale"]]
    # Who wrote what: the model's Brief is scored on the items the model wrote; the rows Harbor's
    # own code writes (calendar, reminders and notes to-dos) count only in the Brief as seen.
    raised_by_model = {lid for pos, row in enumerate(c["marks"]) if items[pos]["source"] == "model"
                       for mark, lid in row if mark == "wrongly_raised"}
    s.model_false_alarms = [lid for lid in c["false_alarms"] if lid in raised_by_model]
    s.model_stale = [f"{lid}: {items[pos]['text'][:80]}" for lid, pos in c["stale"]
                     if items[pos]["source"] == "model"]
    s.harbor_stale = [f"{lid}: {items[pos]['text'][:80]}" for lid, pos in c["stale"]
                      if items[pos]["source"] != "model"]
    nik = c["not_in_key"]
    model_items = sum(1 for it in items if it["source"] == "model")
    nik_model = sum(1 for pos in nik if items[pos]["source"] == "model")
    s.not_in_key = {"items": len(nik), "model_items": nik_model,
                    "model_share": round(nik_model / model_items, 3) if model_items else None}

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

    over = 0.05 if s.length["over"] else 0.0
    order_bonus = 0.0 if s.order["rate"] is None else 0.1 * (s.order["rate"] - 0.5)

    def _score(base: float, false_alarms: list, stale: list) -> float:
        return round(max(0.0, min(1.0, base - 0.1 * len(false_alarms) - 0.15 * len(stale) - over + order_bonus)), 3)

    # The model's Brief: recall of what the model writes, penalised only for items it wrote.
    base = s.model_recall["rate"] if s.model_recall["rate"] is not None else (s.recall["rate"] or 0.0)
    s.score = _score(base, s.model_false_alarms, s.model_stale)
    # The Brief as the user sees it: everything on screen, Harbor's own rows included.
    s.seen_score = _score(s.recall["rate"] or 0.0, s.false_alarms, s.stale)
    if s.state != "ready":
        s.notes.append(f"brief state {s.state!r}: scored as captured")
    return s
