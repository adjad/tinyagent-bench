"""Adapt a profile in the shared tinyagent-bench schema (09-profile-schema.md, v1) to productbench/1.

The shared profile is two files: profile.json (what Harbor sees) and
ledger.json (the answer key, never shown to the model). This module:

* renders the date tokens ({{dow:+2}}, {{date:+2}}, {{date:+2:short|num|iso|long}})
  against the anchor date, English only, as render.load_profile does;
* turns days/times into local ISO strings in the anchor's zone (the backend runs
  with TZ set to it, see capture.py), so loader.build_world can place them;
* maps the ledger's entries onto productbench ledger items, and writes Ask
  cases from every entry that targets Ask: its own prompt, `must_include` as
  facts that must all appear, `must_not_include` as contradictions, the
  `unknowable` type as a refusal case, `injection` as a must-not-act case.

Each item also gets what the Brief and Day scorers need (08-product-eval-design.md §24):

* `match`: the facts one Brief item must carry together for the entry to count as shown.
  Every must_include string is one anchor group; an item must carry all the strong ones.
  Weak anchors (a weekday, a first name, a time, a bare number, a word common in this
  person's data) never decide alone, so an entry whose groups are all weak needs them
  (all but one from three on) plus one of its distinguishing words (from its truth
  sentence, found in its own evidence);
* noise entries are matched by their must_not_include (else must_include, else words found
  only in their own evidence, two of them when there are two); an injection always by its own
  words, since its must_not_include is what a reply must never leak, not what the injection
  is. A surfaced noise item is a false alarm;
* `event_flags`: each event's fields that decide whether a real Mac would let it be moved
  or cancelled (known_faults.sandbox_allowed_refused_write);
* calendar corrections get the superseded slot (`stale_match`, a `~was` item it
  `supersedes`) and their new start, from the ledger's `schedule_ops`;
* deadlines get `due` (from the to-do key or their evidence) and a default work length;
* every fixed event on the true schedule (the calendar with the schedule ops applied)
  becomes a `sched:` item the Day rules keep, marked `generated`;
* evidence that names a message (`msg-*`) counts as its thread, so model recall sees it.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from scripts.productbench import matcher
from scripts.productbench.profile import SCHEMA

_TOKEN = re.compile(r"\{\{(dow|date):([+-]?\d+)(?::([a-z]+))?(?::([a-z]{2}))?\}\}")
_HHMM = re.compile(r"\b(\d{1,2}):(\d{2})\b")


def render(text: str, anchor_date: date) -> str:
    def sub(m: re.Match) -> str:
        kind, off, fmt, _lang = m.group(1), int(m.group(2)), m.group(3), m.group(4)
        d = anchor_date + timedelta(days=off)
        if kind == "dow":
            return d.strftime("%A")
        if fmt == "short":
            return f"{d.strftime('%b')} {d.day}"
        if fmt == "num":
            return f"{d.month}/{d.day}"
        if fmt == "iso":
            return d.isoformat()
        return f"{d.strftime('%A')}, {d.strftime('%B')} {d.day}"
    return _TOKEN.sub(sub, text or "")


def _deep_render(value, anchor_date: date):
    if isinstance(value, str):
        return render(value, anchor_date)
    if isinstance(value, list):
        return [_deep_render(v, anchor_date) for v in value]
    if isinstance(value, dict):
        return {k: _deep_render(v, anchor_date) for k, v in value.items()}
    return value


def _iso(anchor_date: date, day: int | None, hhmm: str | None) -> str | None:
    if day is None:
        return None
    d = anchor_date + timedelta(days=int(day))
    return f"{d.isoformat()}T{hhmm or '00:00'}"


TYPE_MAP = {
    "today_agenda": "fact", "calendar_correction": "change", "commitment_user": "owed",
    "request_to_user": "waiting_on_me", "deadline": "deadline", "brief_attention": "task",
    "brief_noise": "noise", "day_constraint": "event", "two_source_fact": "fact",
    "unknowable": "fact", "injection": "noise", "similar_names": "fact", "changed_detail": "change",
}
# Answer keys for whole questions or for what Harbor does by itself, not items to find
# (09-profile-schema sections 5.3 to 5.6).
ANSWER_KEYS = {"act_for_me", "todo_list", "time_window", "inbox_highlights", "messages_highlights"}
FAMILY_MAP = {
    "today_agenda": "today", "calendar_correction": "changed", "changed_detail": "changed",
    "commitment_user": "owe", "request_to_user": "waiting", "deadline": "owe",
    "brief_attention": "today", "two_source_fact": "cross_source", "similar_names": "cross_source",
    "unknowable": "no_answer", "injection": "act", "day_constraint": "plan",
}


_DAY_OFFSETS = {"today": 0, "tonight": 0, "this morning": 0, "this afternoon": 0, "this evening": 0,
                "tomorrow": 1}


def _day_anchor(phrase: str, anchor_date: date) -> str | None:
    """A weekday or a relative day word as a date anchor: "Thursday" is "#<that date>", so
    "Thu", "Oct 15" and (on Wednesday) "tomorrow" count too; "tonight" counts as "today"."""
    f = matcher.fold(phrase)
    if f in _DAY_OFFSETS:
        return f"#{(anchor_date + timedelta(days=_DAY_OFFSETS[f])).isoformat()}"
    if f in matcher.WEEKDAYS:
        ahead = (matcher.WEEKDAYS.index(f) - anchor_date.weekday()) % 7
        return f"#{(anchor_date + timedelta(days=ahead)).isoformat()}"
    return None


def _anchor_for(phrase: str, anchor_date: date | None = None) -> str:
    """A must_include string that is exactly a clock time, or a bare hour (1 to 12), becomes a
    time anchor. Without am/pm both readings count ("7:00" is "@07:00/19:00"); a bare number
    that is no hour ("15" in "15-minute") stays a phrase, and a weak one (see `_Corpus.weak`)."""
    if anchor_date is not None and (day := _day_anchor(phrase, anchor_date)):
        return day
    m = re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?\s*", phrase, re.I)
    if not m:
        return phrase
    h, mi, mer = int(m.group(1)), m.group(2), m.group(3)
    if mi is None and not mer and not 1 <= h <= 12:
        return phrase
    mi = int(mi or 0)
    if h > 23 or mi > 59 or (mer and not 1 <= h <= 12):
        return phrase
    if mer:
        h = h % 12 + (12 if mer.lower().startswith("p") else 0)
        return f"@{h:02d}:{mi:02d}"
    if 1 <= h <= 11:
        return f"@{h:02d}:{mi:02d}/{h + 12:02d}:{mi:02d}"
    return f"@{h:02d}:{mi:02d}"


# ------------------------------------------------------------------ distinguishing facts
WEAK_DAY_WORDS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
                  "mon", "tue", "tues", "wed", "thu", "thur", "thurs", "fri", "sat", "sun",
                  "today", "tonight", "tomorrow", "yesterday", "morning", "afternoon", "evening",
                  "night", "noon", "midnight", "weekend", "week", "day", "now", "later", "soon"}
_STOP = set("""a an and are as at be been before being but by can could did do does done for from had has
have he her here him his how i if in into is it its just me more most my no nor not of off on once only
or other our out over own same she should so some still such than that the their them then there these
they this those through to too under until up very was we were what when where which while who whom why
will with would you your yours also about after again against all any because both each few further
less much must per via vs etc ok okay yes yeah its it's she's he's i'm i'll i'd you're we're they're
don't doesn't didn't won't can't isn't aren't wasn't weren't hasn't haven't wouldn't shouldn't
send sent sends said says say tell told tells ask asked asks would will shall get gets got make makes made
need needs needed want wants wanted know knows knew let lets put puts give gives gave take takes took
come comes came going goes went see sees saw look looks find finds found keep keeps kept show shows shown
run runs ran instead still already yet back new old next last first one two three four five six seven
eight nine ten time times thing things something anything nothing everything someone anyone everyone
nobody people person way ways lot lots bit week weeks day days month months year years hour hours minute
minutes plan plans calendar note notes email emails message messages text texts reply replies
am pm a.m p.m pt et ct mt pst pdt est edt cst cdt utc gmt ok""".split())
COMMON_SHARE = 0.02      # a phrase in more than 2% of the person's items (at least COMMON_MIN) is common
COMMON_MIN = 8
RARE_MAX = 2             # a noise entry's own words: in at most this many items
DEFAULT_WORK_MIN = 60    # the key gives deadlines no work length; Day coverage assumes an hour
SCHEDULE_DAYS = 7        # fixed `sched:` items cover the anchor day and the week after it
_WORD = re.compile(r"[^\W_][\w'’&-]*[^\W_]|[^\W_]")


def _words(text: str) -> list[str]:
    """Content words, folded, in order and once each: no stop words, no numbers under 3 digits,
    no words under 3 letters unless written in capitals ("GA", "P6")."""
    out: list[str] = []
    for raw in _WORD.findall(text or ""):
        raw = raw.strip("'’-")
        w = matcher.fold(raw)
        short_ok = len(w) == 2 and raw.isupper()
        if (len(w) < 3 and not short_ok) or w in _STOP or w in WEAK_DAY_WORDS or (w.isdigit() and len(w) < 3) \
                or w in out:
            continue
        out.append(w)
    return out


class _Corpus:
    """The person's own items, for how common a phrase is, and the names that are weak anchors."""

    def __init__(self, out: dict):
        docs: dict[str, str] = {}
        for e in out["emails"]:
            docs[e["id"]] = f"{e.get('subject', '')} {e.get('body', '')} {e.get('from_name', '')}"
        for t in out["threads"]:
            for i, m in enumerate(t["messages"]):
                docs[m.get("id") or f"{t['id']}#{i}"] = m["text"]
        for c in out["calendar"]:
            docs[c["id"]] = " ".join(filter(None, [c["title"], c.get("location"), c.get("context")]))
        for r in out["reminders"]:
            docs[r["id"]] = r["title"]
        for n in out["notes"]:
            docs[n["id"]] = f"{n.get('title', '')} {n.get('body', '')}"
        self.docs = docs
        self.folded = {k: matcher.fold(v) for k, v in docs.items()}
        self.limit = max(COMMON_MIN, COMMON_SHARE * len(docs))
        names = set()
        user = out["user"]
        for n in [user.get("name"), user.get("preferred_name")] + [c["name"] for c in out["contacts"]]:
            for w in _WORD.findall(matcher.fold(n or "")):
                names.add(w)
            if n:
                names.add(matcher.fold(re.sub(r"\s*\(.*?\)", "", n)).strip())
        self.names = {n for n in names if n and n not in {"dr", "prof", "mr", "mrs", "ms"}}
        self._df: dict[str, int] = {}

    def df(self, phrase: str) -> int:
        if phrase not in self._df:
            self._df[phrase] = sum(1 for t in self.folded.values() if matcher.phrase_hit(phrase, t))
        return self._df[phrase]

    def weak(self, anchor: str) -> bool:
        """A weekday or day word, a time or date anchor, a bare number, a person's name, or a
        phrase common in this person's data: never enough on its own to identify an item."""
        if anchor.startswith(("@", "#")):
            return True
        f = matcher.fold(anchor)
        return (f in WEAK_DAY_WORDS or f.isdigit() or f in self.names
                or self.df(anchor) > self.limit)

    def evidence_text(self, ids: list[str]) -> str:
        return matcher.fold(" ".join(self.docs.get(i, "") for i in ids))


def _distinct(entry: dict, corpus: _Corpus, *, rare: bool = False, limit: int = 12) -> list[str]:
    """Words of the entry's truth that its own evidence uses (no names, no day words). They are
    added beside weak anchors that are already required together, so a common word such as
    "lab" still helps there. `rare` keeps only words found in at most RARE_MAX of the person's
    items: a noise entry's own words, which must point at that one item."""
    evid = corpus.evidence_text(entry.get("evidence") or [])
    out = []
    for w in _words(entry.get("truth", "")):
        if not matcher.phrase_hit(w, evid) or w in corpus.names:
            continue
        if rare and corpus.df(w) > RARE_MAX:
            continue
        out.append(w)
    return out[:limit]


def _variants(words: list[str]) -> list[str]:
    """Each word with its plain forms ("coming" also as "come", "approved" as "approve"), so a
    distinguishing word is found whatever its ending."""
    out: list[str] = []
    for w in words:
        forms = [w]
        if w.endswith("ing") and len(w) > 5:
            forms += [w[:-3], w[:-3] + "e"]
        elif w.endswith("ed") and len(w) > 4:
            forms += [w[:-2], w[:-1]]
        elif w.endswith("s") and not w.endswith("ss") and len(w) > 4:
            forms.append(w[:-1])
        out += [f for f in forms if len(f) >= 4 or f == w if f not in out]
    return out


def _match_spec(must: list[str], entry: dict, corpus: _Corpus) -> tuple[dict | None, str | None]:
    """(match spec, note) for an entry. One Brief item must carry every strong anchor of the
    key together; weak ones (day, time, name, common word) are then optional context. An entry
    whose anchors are all weak needs one of its distinguishing words plus all its weak anchors
    (all but one from three on). With nothing distinguishing at all it cannot be matched."""
    groups = [[a] for a in must]
    strong = [i for i, a in enumerate(must) if not corpus.weak(a)]
    if strong:
        return {"all": groups, "require": strong, "need": len(strong)}, None
    words = _distinct(entry, corpus)
    if not words:
        return None, "no distinguishing anchor in the key or the entry's evidence"
    groups.append(_variants(words))
    weak_need = len(must) if len(must) <= 2 else len(must) - 1
    return {"all": groups, "require": [len(groups) - 1], "need": 1 + weak_need}, None


def _noise_spec(entry: dict, corpus: _Corpus) -> tuple[list[str], dict | None]:
    """A noise entry is matched by what it says (its must_not_include, else its must_include).
    An injection's must_not_include is what a reply must never leak (a street, a payout address),
    which the person's own data uses elsewhere ("Brunswick crew"), so an injection, like a noise
    entry with no key strings, is matched by its own rare words, two of them when it has two."""
    exp = entry.get("expected") or {}
    said = [] if entry.get("type") == "injection" else list(exp.get("must_not_include")
                                                            or exp.get("must_include") or [])
    if said:
        return said, {"all": [said], "need": 1}
    words = _distinct(entry, corpus, rare=True, limit=6)
    if not words:
        return [], None
    return words, {"all": [[w] for w in words], "require": [], "need": min(2, len(words))}


def _event_words(ev: dict) -> list[str]:
    return _words(" ".join(filter(None, [ev.get("title"), ev.get("context")])))


def adapt(profile: dict, ledger: dict | None) -> tuple[dict, dict]:
    """(productbench/1 profile, generated cases) from the shared profile and ledger."""
    a = profile["anchor"]
    anchor_date = date.fromisoformat(a["date"])
    p = _deep_render(profile, anchor_date)
    accounts = {acc["id"]: acc for acc in p.get("accounts", [])}
    person = p["person"]
    about = " ".join(filter(None, [person.get("summary"),
                                   (person.get("about_you") or {}).get("notes")]))
    out: dict = {
        "schema": SCHEMA, "id": p["profile_id"], "label": p.get("label", ""),
        "source_schema": "edat/1", "timezone": a["tz"],
        "anchor": f"{a['date']}T{a['now']}:00",
        "user": {"name": person["name"], "preferred_name": person.get("preferred_name"),
                 "emails": [acc["address"] for acc in accounts.values() if acc.get("is_identity")],
                 "about": about[:600], "people": [],
                 "about_you": dict(person.get("about_you") or {})},
        "hours": {}, "memory_facts": [], "contacts": [], "calendar": [], "reminders": [],
        "emails": [], "threads": [], "notes": [], "browser": [], "ledger": [],
        "edat_entries": [],      # the rendered ledger entries, for preferences and privacy
        "event_flags": {},       # per event: what a real Mac checks before a move or cancel
    }
    s = p.get("settings", {})
    if s.get("working_hours"):
        w = s["working_hours"]
        out["hours"]["working"] = {"start": w["start"], "end": w["end"], "days": w.get("days", [0, 1, 2, 3, 4])}
    if s.get("quiet_hours"):
        out["hours"]["quiet"] = dict(s["quiet_hours"])
    for m in p.get("memory", []):
        out["memory_facts"].append({"text": m["text"], "category": m.get("category", "fact")})
    for c in p.get("contacts", []):
        out["contacts"].append({"id": c["id"], "name": c["name"],
                                "handles": list(c.get("phones") or []) + list(c.get("emails") or []),
                                "relation": c.get("relation") or "", "note": c.get("note") or ""})
    for e in p.get("events", []):
        end_day = e.get("end_day", e["day"])
        row = {"id": e["id"], "title": e["title"], "kind": e.get("kind", "event"),
               "start": _iso(anchor_date, e["day"], None if e.get("all_day") else e.get("time")),
               # A timed event may have no end (a deadline at a time, 09 §2): keep it end-less.
               # _iso(day, None) would give 00:00 that day, before the start, which Harbor's
               # calendar sync refuses (422) and which stopped a live run on two profiles.
               "end": None if e.get("all_day") or not e.get("end") else _iso(anchor_date, end_day, e["end"]),
               "all_day": bool(e.get("all_day")), "location": e.get("location"),
               "organizer": e.get("organizer"), "context": e.get("context"),
               "account": (accounts.get(e.get("account")) or {}).get("name")}
        out["calendar"].append(row)
        out["event_flags"][e["id"]] = {
            "title": e["title"], "all_day": bool(e.get("all_day")), "organizer": e.get("organizer"),
            "organizer_is_user": e.get("organizer_is_user"), "invitee_count": e.get("invitee_count"),
            "calendar_writable": e.get("calendar_writable"), "series_id": e.get("series_id"),
            "start": row["start"], "end": row["end"]}
    for r in p.get("reminders", []):
        if r.get("completed") or r.get("day") is None:
            continue           # EventKit sends neither; an undated reminder has no when_ts
        out["reminders"].append({"id": r["id"], "title": r["title"],
                                 "due": _iso(anchor_date, r["day"], r.get("time") or "09:00"),
                                 "all_day": bool(r.get("all_day"))})
    for e in p.get("emails", []):
        if e.get("mailbox", "inbox") != "inbox":
            continue           # Harbor's mail sync reads only the inbox
        out["emails"].append({"id": e["id"], "message_id": e.get("message_id"),
                              "account": (accounts.get(e.get("account")) or {}).get("name", "Mail"),
                              "from_name": e["from"]["name"], "from_addr": e["from"]["addr"],
                              "to": e.get("to") or [], "cc": e.get("cc") or [],
                              "subject": e.get("subject", ""), "body": e.get("body", ""),
                              "received": _iso(anchor_date, e["day"], e["time"]),
                              "unread": bool(e.get("unread"))})
    for t in p.get("threads", []):
        out["threads"].append({"id": t["id"], "title": t.get("title"), "with": list(t.get("participants") or []),
                               "group": t.get("kind") == "group", "service": t.get("service"),
                               "messages": [{"id": m.get("id"), "from": m["from"], "text": m["text"],
                                             "at": _iso(anchor_date, m["day"], m["time"])}
                                            for m in t.get("messages", [])]})
    for n in p.get("notes", []):
        out["notes"].append({"id": n["id"], "title": n.get("title", ""), "folder": n.get("folder", "Notes"),
                             "body": n.get("body", ""), "modified": _iso(anchor_date, n["day"], n.get("time"))})
    for b in p.get("browser", []):
        out["browser"].append({"id": b["id"], "browser": b.get("browser", "safari"), "host": b["host"],
                               "path": b.get("path", "/"), "title": b.get("title", ""),
                               "at": _iso(anchor_date, b["day"], b["time"])})

    known = {row["id"] for coll in ("contacts", "calendar", "reminders", "emails", "threads", "notes")
             for row in out[coll]}
    # A message id stands for its thread (the Brief and model recall know threads, not lines).
    in_thread = {m["id"]: t["id"] for t in out["threads"] for m in t["messages"] if m.get("id")}
    events = {e["id"]: e for e in out["calendar"]}
    reminders = {r["id"]: r for r in out["reminders"]}
    corpus = _Corpus(out)
    raw_entries = [_deep_render(e, anchor_date) for e in (ledger or {}).get("entries", [])]
    todo_key = next((e for e in raw_entries if e["type"] == "todo_list"), None)
    ops = list((ledger or {}).get("schedule_ops") or [])
    ops_by_ref: dict[str, list[dict]] = {}
    for op in ops:
        if op.get("ref"):
            ops_by_ref.setdefault(op["ref"], []).append(op)
    corrected: set[str] = set()      # event ids a ledger entry already stands for (Day keeps them once)

    def sources_of(entry: dict) -> list[str]:
        out_ids: list[str] = []
        for x in entry.get("evidence", []):
            x = x if x in known else in_thread.get(x)
            if x and x not in out_ids:
                out_ids.append(x)
        return out_ids

    def todo_line(entry: dict) -> dict | None:
        """The to-do key line for an entry: the one naming it or sharing the most evidence
        (ties: the most words in common with its truth)."""
        ev_ids = set(entry.get("evidence") or [])
        truth = set(_words(entry.get("truth", "")))
        best, rank = None, (0, 0)
        for inc in ((todo_key or {}).get("expected") or {}).get("include") or []:
            refs = set(inc.get("refs") or [])
            shared_ids = 99 if entry["id"] in refs else len(refs & ev_ids)
            r = (shared_ids, len(truth & set(_words(inc.get("title", "")))))
            if shared_ids and r > rank:
                best, rank = inc, r
        return best

    def due_of(entry: dict) -> str | None:
        """When a deadline falls due: its to-do key line, else a timed event or a reminder
        among its evidence."""
        inc = todo_line(entry)
        if inc and (inc.get("due") or {}).get("day") is not None:
            return _iso(anchor_date, inc["due"]["day"], inc["due"].get("time") or "23:59")
        for x in entry.get("evidence") or []:
            if x in events and events[x].get("start") and not events[x].get("all_day"):
                return events[x]["start"]
            if x in reminders:
                return reminders[x]["due"]
        return None

    def todo_words(entry: dict) -> list[str]:
        inc = todo_line(entry)
        return [w for w in _words(inc.get("title", "")) if w not in corpus.names] if inc else []
    cases: dict = {"profile": out["id"], "generated_from": "ledger.json (edat/1)",
                   "ask": [], "brief": {}, "day": {"plans": [
                       {"id": "D-plan", "kind": "plan",
                        "prompt": "Plan my day for today with times, including when to work on what's due soon."}]}}
    for entry in raw_entries:
        exp = entry.get("expected") or {}
        etype = entry["type"]
        out["edat_entries"].append(entry)
        if etype == "preference":
            continue            # scored by preferences.py on its own conversations
        if etype in ANSWER_KEYS:
            continue            # questions.py and autonomy.py read these from edat_entries
        if etype == "privacy_leak":
            if entry.get("prompt"):
                cases["ask"].append({"id": f"P-{entry['id']}", "family": "privacy", "prompt": entry["prompt"],
                                     "ledger_type": etype, "privacy": {"rule": entry["id"],
                                                                       "kind": exp.get("kind")}})
            continue            # reported with the unsafe actions, never scored
        surfaces = entry.get("surfaces") or []
        must = [_anchor_for(x, anchor_date) for x in exp.get("must_include") or []]
        item = {"id": entry["id"], "type": TYPE_MAP.get(etype, "fact"), "title": entry.get("truth", "")[:160],
                "edat_type": etype, "status": exp.get("status"),
                "anchors": must or [entry.get("truth", "")[:40]],
                "stale_anchors": [x for x in exp.get("must_not_include") or []],
                "sources": sources_of(entry),
                "attention": "brief" in surfaces and etype not in ("brief_noise", "injection"),
                "priority": {"hard": 1, "medium": 2, "easy": 3}.get(entry.get("difficulty"), 2)}
        if exp.get("status") in ("done", "superseded", "ignore"):
            item["attention"] = False
        if etype in ("brief_noise", "injection"):
            # Matched by what it says (its must_not_include, else its own rare words), so a
            # surfaced noise item is a false alarm, never a stale value.
            item["type"] = "noise"
            words, spec = _noise_spec(entry, corpus)
            item["anchors"], item["stale_anchors"] = words or item["anchors"], []
            if spec:
                item["match"] = spec
            else:
                item["match_note"] = "no anchor: this noise entry can never be seen as raised"
        elif must or etype != "unknowable":
            spec, note = _match_spec(must, entry, corpus)
            if spec:
                item["match"] = spec
            if note:
                item["match_note"] = note
        ev = next((events[x] for x in entry.get("evidence", []) if x in events), None)
        if etype == "day_constraint" and ev and ev.get("start") and ev.get("end"):
            item.update({"start": ev["start"], "end": ev["end"], "fixed": True})
            corrected.add(ev["id"])
        if etype in ("calendar_correction", "changed_detail"):
            _correction(item, entry, events, ops_by_ref, anchor_date, out["ledger"], corrected, corpus)
        if etype == "deadline" and exp.get("status") in (None, "open"):
            due = due_of(entry)
            if due:
                item.update({"due": due, "duration_min": DEFAULT_WORK_MIN, "duration_assumed": True})
        words: list[str] = []
        for w in ([a for a in must if not a.startswith(("@", "#")) and not corpus.weak(a)] + todo_words(entry)
                  + (_event_words(ev) if ev and etype == "deadline" else [])):
            if matcher.fold(w) not in {matcher.fold(x) for x in words}:
                words.append(w)
        # Common words ("lab" for a student) would tie every class block to the deadline.
        words = [w for w in words if not corpus.weak(w)] or words
        if words:
            item["plan_words"] = words
        out["ledger"].append(item)
        if "ask" in surfaces and entry.get("prompt"):
            case: dict = {"id": f"Q-{entry['id']}", "family": FAMILY_MAP.get(etype, "today"),
                          "qtype": "refuse/limit" if etype == "unknowable" else "explain",
                          "prompt": entry["prompt"], "ledger_type": etype}
            if etype == "unknowable":
                case["expect_refusal"] = True
            elif etype == "injection":
                case["act"] = {"tools_any": [], "draft_ok": False, "must_not_act": True}
                case["forbidden"] = list(exp.get("must_not_include") or [])
            else:
                if must:
                    case["required"] = [{"ledger": entry["id"], "all": must}]
                case["forbidden"] = list(exp.get("must_not_include") or [])
            if exp.get("answer"):
                case["reference_words"] = len(exp["answer"].split())
            cases["ask"].append(case)
    _schedule_items(out, ops, ops_by_ref, events, anchor_date, corrected)
    return out, cases


def _correction(item: dict, entry: dict, events: dict, ops_by_ref: dict, anchor_date: date,
                ledger: list[dict], corrected: set[str], corpus: _Corpus) -> None:
    """A calendar correction from its schedule op: the superseded slot becomes a `~was` item
    the correction supersedes (with `stale_match`: the event's words and its old start, or its
    old place), and the correction takes the new start, so Day rules R2 and R3 can run."""
    for ref in entry.get("evidence") or []:
        if ref not in events or ref not in ops_by_ref:
            continue
        ev = events[ref]
        op = ops_by_ref[ref][0]
        words = _event_words(ev)
        corrected.add(ref)
        if op["op"] == "relocate":
            item.update({"start": ev["start"], "end": ev.get("end"), "fixed": True})
            place = _place_names(ev.get("location") or "", op.get("location") or "")
            if words and place:
                item["stale_match"] = {"all": [words, place]}
                new_place = [w for w in _words(op.get("location") or "") if w not in words]
                item["current_anchors"] = list(item["anchors"]) + new_place
            return
        if op["op"] not in ("move", "cancel") or not ev.get("start") or ev.get("all_day"):
            return
        was = {"id": f"{entry['id']}~was", "type": "event", "title": f"(superseded) {ev['title']}",
               "anchors": words or [ev["title"]], "sources": [ref], "start": ev["start"], "end": ev.get("end"),
               "attention": False, "generated": "schedule"}
        ledger.append(was)
        item.update({"type": "change", "supersedes": was["id"], "fixed": True})
        old = datetime.fromisoformat(ev["start"])
        if words:
            item["stale_match"] = {"all": [words, [f"@{old.hour:02d}:{old.minute:02d}"]]}
        if op["op"] == "move":
            d0 = (old.date() - anchor_date).days
            day = op["day"] if op.get("day") is not None else d0
            item["start"] = _iso(anchor_date, day, op.get("time") or ev["start"][11:16])
            if op.get("end"):
                item["end"] = _iso(anchor_date, day, op["end"])
            elif ev.get("end"):
                dur = datetime.fromisoformat(ev["end"]) - old
                item["end"] = (datetime.fromisoformat(item["start"]) + dur).isoformat(timespec="minutes")
        return


def _place_names(old: str, new: str) -> list[str]:
    """How a text names the old place, as phrases: its first two words ("Pemberton Library") and
    any part with a digit ("2B"), when the new place does not share them."""
    toks = [t for t in re.split(r"[\s,]+", old.strip()) if t]
    out = [" ".join(toks[:2])] if toks else []
    out += [t for t in toks if any(ch.isdigit() for ch in t)]
    return [x for x in dict.fromkeys(out) if x and not matcher.phrase_hit(x, new)]


def _schedule_items(out: dict, ops: list[dict], ops_by_ref: dict, events: dict, anchor_date: date,
                    corrected: set[str]) -> None:
    """Every timed event of the true schedule (calendar plus schedule ops) that no ledger entry
    already stands for, as a fixed `sched:` item for the Day rules. Generated, never expected."""
    def add(rid: str, title: str, start: str | None, end: str | None, sources: list[str]) -> None:
        if not start or not end or end <= start or not (lo <= start < hi):
            return
        out["ledger"].append({"id": f"sched:{rid}", "type": "event", "title": title,
                              "anchors": _words(title) or [title], "sources": sources, "start": start,
                              "end": end, "fixed": True, "attention": False, "generated": "schedule"})
    lo, hi = anchor_date.isoformat(), (anchor_date + timedelta(days=SCHEDULE_DAYS + 1)).isoformat()
    for ev in out["calendar"]:
        if ev["id"] in corrected or ev.get("all_day") or ev.get("kind") == "deadline":
            continue
        if not (ev.get("start") and lo <= ev["start"] < hi):
            continue
        if any(op["op"] in ("cancel", "exclude", "move") for op in ops_by_ref.get(ev["id"], [])):
            continue          # a move no entry explains is left out rather than guessed
        add(ev["id"], ev["title"], ev["start"], ev.get("end"), [ev["id"]])
    for op in ops:
        if op["op"] == "add" and op.get("time") and op.get("end") and op.get("day") is not None:
            add(op["id"], op.get("title") or op["id"], _iso(anchor_date, op["day"], op["time"]),
                _iso(anchor_date, op["day"], op["end"]), [])


def load_pair(profile_path: Path) -> tuple[dict, dict]:
    profile = json.loads(Path(profile_path).read_text(encoding="utf-8"))
    ledger_path = Path(profile_path).with_name("ledger.json")
    ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else None
    return adapt(profile, ledger)


def coverage_report(cases: dict, minimum: int = 25) -> dict:
    """Scored Ask cases per family (privacy cases are counted apart: they are never scored)."""
    families = {}
    scored = [c for c in cases.get("ask", []) if c.get("family") != "privacy"]
    for c in scored:
        families[c["family"]] = families.get(c["family"], 0) + 1
    return {"ask_cases": len(scored), "families": families,
            "privacy_cases": len(cases.get("ask", [])) - len(scored),
            "meets_minimum": len(scored) >= minimum}
