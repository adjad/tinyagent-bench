"""Validate the benchmark user profiles: python -m scripts.bench_profiles.validate [ids...]

Checks the schema (09-profile-schema.md), id uniqueness, date consistency, that nothing
observed is later than ``anchor.now``, that every ledger entry points at items that
exist, and that no real-looking domain, phone number or brand slipped in.
Exit status 1 on any error. ``--counts`` also checks the volume guide.
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from scripts.bench_profiles.lib import TOKEN_RE
from scripts.bench_profiles.render import FIXTURES, PROFILE_IDS, read_raw

TOP_KEYS = ("schema_version", "profile_id", "synthetic", "label", "anchor", "person", "settings",
            "accounts", "locations", "travel", "contacts", "threads", "emails", "events",
            "reminders", "notes", "documents", "browser", "memory")
LEDGER_TYPES = ("today_agenda", "calendar_correction", "commitment_user", "request_to_user",
                "deadline", "brief_attention", "brief_noise", "day_constraint", "two_source_fact",
                "unknowable", "injection", "similar_names", "changed_detail", "preference", "privacy_leak",
                "act_for_me", "todo_list", "time_window", "inbox_highlights", "messages_highlights")
ACT_DECISIONS = ("act", "ask", "bait")
ACT_ACTIONS = ("add", "add_more", "hold", "follow_up", None)
ACT_GATES = ("act", "card", "never")
BAIT_REASONS = ("not_users", "done", "superseded", "no_explicit_date", "too_soon", "too_far",
                "already_on_calendar", "unknown_sender", "bulk_or_promo", "outbound", "injection",
                "sensitive", "arrived_before_yes", "other")
TODO_KINDS = ("promised", "asked", "deadline")
PREF_HOMES = ("memory", "about_you.tone", "about_you.detail", "about_you.boundaries", "about_you.notes",
              "about_you.people", "settings.working_hours", "settings.quiet_hours", "mid_conversation")
CHECK_ON = ("answer_text", "tool_args", "world_state")
DIMENSIONS = ("persistence", "one_off_override", "supersession", "scope", "no_waiver")
# Harbor's About you limits (service/memory/profile.py)
ABOUT_LIMITS = {"about": 600, "boundaries": 600, "notes": 800}
STATUSES = ("open", "done", "superseded", "moved", "cancelled", "unknown", "ignore")
SURFACES = ("ask", "brief", "day")
EVENT_KINDS = ("meeting", "exam", "assignment", "event")
MAILBOXES = ("inbox", "archive", "sent", "spam", "trash")
CATEGORIES = ("people", "attention", "accounts", "news", "social", "promotions")
DOC_KINDS = ("text", "markdown", "csv", "docx", "pdf", "xlsx")
MEM_CATS = ("fact", "preference", "person", "routine", "project")
ID_PREFIX = {"accounts": "acct", "locations": "loc", "contacts": "ct", "threads": "th",
             "emails": "em", "events": "ev", "reminders": "rem", "notes": "note",
             "documents": "doc", "browser": "web", "memory": "mem"}
WINDOW = (-60, 21)          # widest day offset any item may use
EVENT_WINDOW = (-35, 21)

# Volume guide from the brief (soft: reported with --counts, errors only far outside).
VOLUME = {"emails": (150, 250), "messages": (300, 500), "threads": (10, 15), "events": (40, 70),
          "notes": (20, 40), "reminders": (15, 30), "contacts": (20, 40), "documents": (4, 12)}

HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
URL_RE = re.compile(r"(?:https?://|www\.)([A-Za-z0-9.-]+\.[A-Za-z]{2,})", re.I)
BARE_HOST_RE = re.compile(r"\b((?:[a-z0-9-]+\.)+(?:com|org|net|edu|io|co|ai|app|dev|ca|uk|de|gov|us|me))\b", re.I)
PHONE_RE = re.compile(r"(?<![\w#])(?:\+?1[\s.-]?)?\(?(\d{3})\)?[\s.-](\d{3})[\s.-](\d{4})(?!\d)")
INTL_RE = re.compile(r"\+(?!1[\s(])\d{2,3}[\s.-]?\d{2,4}[\s.-]?\d{3,4}")
RESERVED = (".example", ".test", ".invalid", ".localhost", "example.com", "example.org", "example.net")
# Real organisations and products the data must not name (case-sensitive whole words).
BRANDS = ("Gmail", "Google", "iCloud", "Apple Pay", "Apple ID", "Amazon", "Venmo", "PayPal",
          "Zelle", "Slack", "Zoom", "Instructure", "Blackboard", "Uber", "Lyft", "DoorDash",
          "Netflix", "Spotify", "Starbucks", "Chase Bank", "Wells Fargo", "Microsoft", "Outlook",
          "LinkedIn", "Facebook", "Instagram", "WhatsApp", "GitHub", "Jira", "Figma", "Airbnb",
          "United Airlines", "Walmart", "Costco", "Expedia", "Coursera", "Chegg", "Quizlet",
          "Grubhub", "Hulu", "TikTok", "Snapchat", "Twitter", "Discord", "Reddit", "YouTube",
          "PagerDuty", "Datadog", "Salesforce", "Workday", "Okta", "AWS", "Azure", "OpenAI",
          "ChatGPT", "Anthropic", "Claude", "Tim Hortons", "Shoppers Drug Mart", "Rogers",
          "Presto", "TTC", "Interac", "Wealthsimple", "Harvard", "Stanford", "MIT", "Berkeley",
          "University of Toronto", "Northwestern", "Seattle Times")


def _is_reserved(host: str) -> bool:
    h = host.lower().rstrip(".")
    return any(h.endswith(r) or h == r.lstrip(".") for r in RESERVED)


def _strings(obj, path=""):
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _strings(v, f"{path}[{i}]")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _strings(v, f"{path}.{k}")


def _minutes(t: str) -> int:
    return int(t[:2]) * 60 + int(t[3:])


def validate(profile: dict, ledger: dict, *, check_volume: bool = False) -> list[str]:
    errs: list[str] = []
    E = errs.append
    pid = profile.get("profile_id", "?")

    for k in TOP_KEYS:
        if k not in profile:
            E(f"{pid}: missing top-level key {k}")
    if errs:
        return errs
    if profile["schema_version"] != 1 or profile["synthetic"] is not True:
        E(f"{pid}: schema_version must be 1 and synthetic true")
    a = profile["anchor"]
    try:
        ad = date.fromisoformat(a["date"])
        if ad.strftime("%A") != a["weekday"]:
            E(f"{pid}: anchor.weekday {a['weekday']} != {ad.strftime('%A')}")
        if ad.weekday() > 4:
            E(f"{pid}: anchor day must be a weekday")
    except Exception as exc:  # noqa: BLE001
        E(f"{pid}: bad anchor.date: {exc}")
    for k in ("now", "tz", "locale"):
        if k not in a:
            E(f"{pid}: anchor.{k} missing")
    now = _minutes(a["now"]) if HHMM.match(a.get("now", "")) else 0

    # ---- ids
    ids: dict[str, str] = {}
    for coll, prefix in ID_PREFIX.items():
        for row in profile[coll]:
            iid = row.get("id")
            if not iid or not iid.startswith(prefix + "-"):
                E(f"{pid}: {coll} id {iid!r} must start with {prefix}-")
            if iid in ids:
                E(f"{pid}: duplicate id {iid}")
            ids[iid] = coll
    for th in profile["threads"]:
        for m in th["messages"]:
            if not m["id"].startswith("msg-"):
                E(f"{pid}: message id {m['id']} must start with msg-")
            if m["id"] in ids:
                E(f"{pid}: duplicate id {m['id']}")
            ids[m["id"]] = "messages"
    for s in {e["series_id"] for e in profile["events"] if e.get("series_id")}:
        ids.setdefault(s, "series")

    contact_ids = {c["id"] for c in profile["contacts"]}
    account_ids = {x["id"] for x in profile["accounts"]}
    loc_ids = {x["id"] for x in profile["locations"]}
    doc_ids = {d["id"] for d in profile["documents"]}

    def check_time(where: str, day, t, *, observed: bool, allow_none: bool = False):
        if day is None:
            if not allow_none:
                E(f"{pid}: {where}: day is null")
            return
        if not isinstance(day, int) or not (WINDOW[0] <= day <= WINDOW[1]):
            E(f"{pid}: {where}: day {day!r} outside {WINDOW}")
            return
        if t is None:
            if not allow_none:
                E(f"{pid}: {where}: time is null")
            return
        if not HHMM.match(t):
            E(f"{pid}: {where}: bad time {t!r}")
            return
        if observed and (day > 0 or (day == 0 and _minutes(t) > now)):
            E(f"{pid}: {where}: observed item after anchor.now (day {day} {t})")

    # ---- person / settings / places
    for k in ("name", "preferred_name", "summary", "home_location"):
        if not profile["person"].get(k):
            E(f"{pid}: person.{k} missing")
    if profile["person"].get("home_location") not in loc_ids:
        E(f"{pid}: person.home_location is not a location id")
    wh = profile["settings"].get("working_hours") or {}
    if not (HHMM.match(wh.get("start", "")) and HHMM.match(wh.get("end", ""))):
        E(f"{pid}: settings.working_hours needs start/end HH:MM")
    for r in profile["travel"]:
        if r["from"] not in loc_ids or r["to"] not in loc_ids:
            E(f"{pid}: travel route {r} uses an unknown location")
    if not any(x.get("is_identity") for x in profile["accounts"]):
        E(f"{pid}: no identity account")

    # ---- contacts
    handles: Counter = Counter()
    for c in profile["contacts"]:
        if not c.get("name"):
            E(f"{pid}: contact {c['id']} has no name")
        for h in c["phones"] + c["emails"]:
            handles[h.lower()] += 1
        if c.get("birthday") and not re.match(r"^\d{2}-\d{2}$", c["birthday"]):
            E(f"{pid}: contact {c['id']} birthday must be MM-DD")
    for h, n in handles.items():
        if n > 1:
            E(f"{pid}: handle {h} belongs to {n} contacts")
    names = Counter(c["name"] for c in profile["contacts"])
    for n, k in names.items():
        if k > 1:
            E(f"{pid}: two contacts saved under the same display name {n!r}")

    # ---- threads
    for th in profile["threads"]:
        if th["kind"] not in ("dm", "group"):
            E(f"{pid}: {th['id']} kind")
        for p in th["participants"]:
            if p not in contact_ids:
                E(f"{pid}: {th['id']} participant {p} is not a contact")
        if th["kind"] == "dm" and len(th["participants"]) > 1:
            E(f"{pid}: {th['id']} dm with {len(th['participants'])} participants")
        if not th["messages"]:
            E(f"{pid}: {th['id']} has no messages")
        prev = None
        for m in th["messages"]:
            check_time(m["id"], m["day"], m["time"], observed=True)
            key = (m["day"], m["time"])
            if prev and key < prev:
                E(f"{pid}: {m['id']} out of order")
            prev = key
            f = m["from"]
            if f != "me" and f not in th["participants"] and not PHONE_RE.fullmatch(f):
                E(f"{pid}: {m['id']} from {f!r} is not me, a participant or a phone handle")
            if not m["text"].strip():
                E(f"{pid}: {m['id']} empty text")
            if " | " in m["text"]:
                E(f"{pid}: {m['id']} text contains ' | ' (breaks the Messages wire format)")

    # ---- emails
    em_by_id = {e["id"]: e for e in profile["emails"]}
    for e in profile["emails"]:
        w = e["id"]
        check_time(w, e["day"], e["time"], observed=True)
        if e["account"] not in account_ids:
            E(f"{pid}: {w} account {e['account']} unknown")
        if e["mailbox"] not in MAILBOXES:
            E(f"{pid}: {w} mailbox {e['mailbox']}")
        if e["category"] not in CATEGORIES:
            E(f"{pid}: {w} category {e['category']}")
        if not e["subject"].strip() or not e["body"].strip():
            E(f"{pid}: {w} empty subject or body")
        if not e["from"].get("addr") or "@" not in e["from"]["addr"]:
            E(f"{pid}: {w} from.addr {e['from']}")
        rp = e.get("in_reply_to")
        if rp:
            parent = em_by_id.get(rp)
            if not parent:
                E(f"{pid}: {w} in_reply_to {rp} missing")
            elif (parent["day"], parent["time"]) > (e["day"], e["time"]):
                E(f"{pid}: {w} replies to a later email {rp}")
            elif parent["thread_id"] != e["thread_id"]:
                E(f"{pid}: {w} and its parent {rp} are in different threads")
        for att in e["attachments"]:
            if att.get("doc_id") and att["doc_id"] not in doc_ids:
                E(f"{pid}: {w} attachment doc {att['doc_id']} missing")
        if e["mailbox"] == "sent":
            mine = {x["address"].lower() for x in profile["accounts"]}
            if e["from"]["addr"].lower() not in mine:
                E(f"{pid}: {w} is in sent but not from one of the user's accounts")

    # ---- events
    for ev in profile["events"]:
        w = ev["id"]
        if ev["kind"] not in EVENT_KINDS:
            E(f"{pid}: {w} kind {ev['kind']!r}")
        if not (EVENT_WINDOW[0] <= ev["day"] <= EVENT_WINDOW[1]):
            E(f"{pid}: {w} day {ev['day']} outside {EVENT_WINDOW}")
        if ev["account"] and ev["account"] not in account_ids:
            E(f"{pid}: {w} account unknown")
        if ev["all_day"]:
            if ev["time"] or ev["end"]:
                E(f"{pid}: {w} all-day event with a time")
        else:
            check_time(w, ev["day"], ev["time"], observed=False)
            if ev["end"]:
                if not HHMM.match(ev["end"]):
                    E(f"{pid}: {w} bad end")
                elif ev.get("end_day", ev["day"]) == ev["day"] and _minutes(ev["end"]) < _minutes(ev["time"]):
                    E(f"{pid}: {w} ends before it starts")
            elif ev["kind"] not in ("assignment",):
                E(f"{pid}: {w} timed event without an end")
        if ev["status"] not in ("confirmed", "tentative"):
            E(f"{pid}: {w} status {ev['status']}")

    # ---- reminders
    for r in profile["reminders"]:
        w = r["id"]
        check_time(w, r["day"], r["time"], observed=False, allow_none=True)
        if r["created_day"] > 0:
            E(f"{pid}: {w} created in the future")
        if r["day"] is not None and r["day"] < r["created_day"] - 0 and not r["completed"]:
            pass  # a reminder may be created with a past due date (rare but real)
        if r["completed"]:
            cd = r.get("completed_day")
            if cd is None or cd > 0 or cd < r["created_day"]:
                E(f"{pid}: {w} completed_day {cd} inconsistent")
            elif cd == 0 and r.get("completed_time") and _minutes(r["completed_time"]) > now:
                E(f"{pid}: {w} completed after anchor.now")

    for coll in ("notes", "documents", "browser"):
        for r in profile[coll]:
            check_time(r["id"], r["day"], r["time"], observed=True)
    for n in profile["notes"]:
        if n["created_day"] > n["day"]:
            E(f"{pid}: {n['id']} created after it was modified")
    for d in profile["documents"]:
        if d["kind"] not in DOC_KINDS:
            E(f"{pid}: {d['id']} kind {d['kind']}")
        if not d["path"].startswith("~/"):
            E(f"{pid}: {d['id']} path must start with ~/")
        if not d["content"].strip():
            E(f"{pid}: {d['id']} empty content")
    for m in profile["memory"]:
        check_time(m["id"], m["day"], "00:00", observed=True)
        if m["category"] not in MEM_CATS:
            E(f"{pid}: {m['id']} category {m['category']}")
        if m.get("superseded_by") and m["superseded_by"] not in ids:
            E(f"{pid}: {m['id']} superseded_by missing")
    for b in profile["browser"]:
        if not _is_reserved(b["host"]):
            E(f"{pid}: {b['id']} host {b['host']} is not a reserved domain")

    # ---- English only (user decision 2026-11-18)
    for th in profile["threads"]:
        for m in th["messages"]:
            if m.get("lang", "en") != "en":
                E(f"{pid}: {m['id']} lang {m['lang']}: profiles are English only")
    for e in profile["emails"]:
        if e.get("lang", "en") != "en":
            E(f"{pid}: {e['id']} lang {e['lang']}: profiles are English only")

    # ---- tokens, domains, phones, brands (profile + ledger)
    for where, s in list(_strings(profile, pid)) + list(_strings(ledger, pid + ".ledger")):
        if ":es}}" in s:
            E(f"{where}: Spanish date token; profiles are English only")
        stripped = TOKEN_RE.sub("", s)
        if "{{" in stripped or "}}" in stripped:
            E(f"{where}: malformed date token in {s[:80]!r}")
        for m in EMAIL_RE.finditer(s):
            if not _is_reserved(m.group(1)):
                E(f"{where}: email domain {m.group(1)} is not reserved")
        for m in URL_RE.finditer(s):
            if not _is_reserved(m.group(1)):
                E(f"{where}: url host {m.group(1)} is not reserved")
        for m in BARE_HOST_RE.finditer(s):
            host = m.group(1)
            if not _is_reserved(host) and "@" not in s[max(0, m.start() - 1):m.start()]:
                E(f"{where}: host-like text {host} is not reserved")
        for m in PHONE_RE.finditer(s):
            if not (m.group(2) == "555" and 100 <= int(m.group(3)) <= 199):
                E(f"{where}: phone {m.group(0)} is not a 555-01xx number")
        for m in INTL_RE.finditer(s):
            E(f"{where}: international-looking number {m.group(0)}")
        for brand in BRANDS:
            if re.search(r"(?<![A-Za-z])" + re.escape(brand) + r"(?![A-Za-z])", s):
                E(f"{where}: names a real brand {brand!r}")

    # ---- ledger
    if ledger.get("profile_id") != pid:
        E(f"{pid}: ledger profile_id mismatch")
    seen_types = Counter()
    lids = set()
    for ent in ledger.get("entries", []):
        lid = ent.get("id")
        if lid in lids:
            E(f"{pid}: duplicate ledger id {lid}")
        lids.add(lid)
        t = ent.get("type")
        if t not in LEDGER_TYPES:
            E(f"{pid}: {lid} unknown type {t}")
        seen_types[t] += 1
        if not ent.get("surfaces") or any(s not in SURFACES for s in ent["surfaces"]):
            E(f"{pid}: {lid} surfaces {ent.get('surfaces')}")
        if not ent.get("truth") or not ent.get("why"):
            E(f"{pid}: {lid} needs truth and why")
        if not ent.get("evidence") and t not in ("unknowable", "time_window"):
            E(f"{pid}: {lid} has no evidence")
        for ref in ent.get("evidence", []) + ent.get("distractors", []):
            if ref not in ids:
                E(f"{pid}: {lid} references missing item {ref}")
        both = set(ent.get("evidence", [])) & set(ent.get("distractors", []))
        if both:
            E(f"{pid}: {lid} lists {sorted(both)} as both evidence and distractor")
        st = (ent.get("expected") or {}).get("status")
        if st is not None and st not in STATUSES:
            E(f"{pid}: {lid} status {st}")
        if t == "unknowable" and st != "unknown":
            E(f"{pid}: {lid} unknowable must have status unknown")
        if t in ("injection", "brief_noise") and st != "ignore":
            E(f"{pid}: {lid} {t} must have status ignore")
        if t == "today_agenda":
            agenda = (ent.get("expected") or {}).get("agenda") or []
            if not agenda:
                E(f"{pid}: {lid} today_agenda needs expected.agenda")
            last = -1
            for item in agenda:
                ref = item.get("ref")
                if ref not in ids:
                    E(f"{pid}: {lid} agenda ref {ref} missing")
                tm = item.get("time")
                if tm and HHMM.match(tm):
                    if _minutes(tm) < last:
                        E(f"{pid}: {lid} agenda not in time order at {ref}")
                    last = _minutes(tm)
    # ---- About you (Harbor's profile store limits)
    ay = profile["person"].get("about_you") or {}
    for k, lim in ABOUT_LIMITS.items():
        if len(ay.get(k) or "") > lim:
            E(f"{pid}: about_you.{k} longer than {lim} chars")
    if ay.get("tone") not in (None, "gentle", "warm", "direct"):
        E(f"{pid}: about_you.tone {ay.get('tone')!r}")
    if ay.get("detail") not in (None, "short", "thorough"):
        E(f"{pid}: about_you.detail {ay.get('detail')!r}")
    if len(ay.get("goals") or []) > 5 or len(ay.get("struggles") or []) > 6 or len(ay.get("people") or []) > 8:
        E(f"{pid}: about_you goals/struggles/people over Harbor's limits (5/6/8)")
    cnames = {c["name"] for c in profile["contacts"]}
    for pp in ay.get("people") or []:
        if pp.get("name") not in cnames:
            E(f"{pid}: about_you.people {pp.get('name')!r} is not a contact display name")

    # ---- preferences and privacy leaks
    prefs = [e for e in ledger.get("entries", []) if e.get("type") == "preference"]
    mem_text = {m["id"]: m["text"] for m in profile["memory"]}
    mid = 0
    pref_nums = Counter()
    for ent in prefs:
        x = ent.get("expected") or {}
        lid = ent["id"]
        if not isinstance(x.get("pref"), int) or not 1 <= x["pref"] <= 24:
            E(f"{pid}: {lid} expected.pref must be 1..24")
        pref_nums[x.get("pref")] += 1
        home = x.get("home")
        if home not in PREF_HOMES:
            E(f"{pid}: {lid} home {home!r}")
        chk = x.get("check") or {}
        if chk.get("on") not in CHECK_ON or not chk.get("rule"):
            E(f"{pid}: {lid} check needs on in {CHECK_ON} and a rule")
        if any(d not in DIMENSIONS for d in x.get("dimensions") or []) or not x.get("dimensions"):
            E(f"{pid}: {lid} dimensions must be a non-empty subset of {DIMENSIONS}")
        if not ent.get("distractors"):
            E(f"{pid}: {lid} needs at least one bait item in distractors")
        if home == "mid_conversation":
            mid += 1
            if not x.get("say"):
                E(f"{pid}: {lid} mid_conversation preference needs expected.say")
            continue
        ht = x.get("home_text") or ""
        if not ht:
            E(f"{pid}: {lid} needs home_text")
            continue
        if home == "memory":
            if not any(ht in mem_text.get(r, "") for r in ent.get("evidence", [])):
                E(f"{pid}: {lid} home_text not found in its evidence memory fact")
            if not any(r in mem_text and next(m for m in profile["memory"] if m["id"] == r)["category"]
                       in ("preference", "routine") for r in ent.get("evidence", [])):
                E(f"{pid}: {lid} memory home must be a preference or routine fact")
        elif home.startswith("about_you."):
            field = home.split(".", 1)[1]
            val = ay.get(field)
            blob = " ".join(f"{p.get('name')} {p.get('note')}" for p in val) if field == "people" else str(val or "")
            if ht not in blob:
                E(f"{pid}: {lid} home_text not in about_you.{field}")
        elif home.startswith("settings."):
            blob = str(profile["settings"].get(home.split(".", 1)[1]))
            if ht not in blob:
                E(f"{pid}: {lid} home_text not in {home}")
    if prefs and not (8 <= len(prefs) <= 10):
        E(f"{pid}: {len(prefs)} preferences, want 8-10")
    if prefs and not (2 <= mid <= 3):
        E(f"{pid}: {mid} mid-conversation preferences, want 2-3")
    for n, k in pref_nums.items():
        if k > 1:
            E(f"{pid}: preference {n} used {k} times")
    leaks = [e for e in ledger.get("entries", []) if e.get("type") == "privacy_leak"]
    kinds = Counter((e.get("expected") or {}).get("kind") for e in leaks)
    for ent in leaks:
        x = ent.get("expected") or {}
        lid = ent["id"]
        if x.get("kind") not in ("third_party_secret", "wrong_recipient"):
            E(f"{pid}: {lid} kind {x.get('kind')!r}")
        for k in ("may_receive", "must_not_receive"):
            if not x.get(k) and not (k == "may_receive" and x.get("kind") == "third_party_secret"):
                E(f"{pid}: {lid} needs {k}")
            for c in x.get(k) or []:
                if c not in contact_ids:
                    E(f"{pid}: {lid} {k} {c} is not a contact")
        if set(x.get("may_receive") or []) & set(x.get("must_not_receive") or []):
            E(f"{pid}: {lid} a contact is in both may_receive and must_not_receive")
        if x.get("kind") == "third_party_secret":
            if not x.get("secret") or x.get("source") not in ids or not x.get("must_not_include"):
                E(f"{pid}: {lid} third_party_secret needs secret, an existing source and must_not_include")
        chk = x.get("check") or {}
        if chk.get("on") not in CHECK_ON:
            E(f"{pid}: {lid} check.on must be one of {CHECK_ON}")
    if leaks and (kinds["third_party_secret"] < 2 or kinds["wrong_recipient"] < 1):
        E(f"{pid}: privacy_leak needs >=2 third_party_secret and >=1 wrong_recipient")

    # ---- schedule ops (the true schedule, windows.py)
    ev_ids = {e["id"] for e in profile["events"]}
    for op in ledger.get("schedule_ops") or []:
        oid = op.get("id", "?")
        if op.get("op") not in ("move", "relocate", "cancel", "add", "exclude"):
            E(f"{pid}: {oid} op {op.get('op')!r}")
        if op.get("op") in ("move", "relocate", "cancel", "exclude") and op.get("ref") not in ev_ids:
            E(f"{pid}: {oid} ref {op.get('ref')!r} is not a calendar event")
        if op.get("op") == "add" and not (op.get("title") and isinstance(op.get("day"), int)):
            E(f"{pid}: {oid} add needs title and day")
        if op.get("op") == "move" and op.get("day") is None and not op.get("time"):
            E(f"{pid}: {oid} move needs a new day or time")
        for t_ in ("time", "end"):
            if op.get(t_) and not HHMM.match(op[t_]):
                E(f"{pid}: {oid} bad {t_}")
        if not op.get("because"):
            E(f"{pid}: {oid} needs because")
        for r in op.get("because") or []:
            if r not in ids:
                E(f"{pid}: {oid} because {r} missing")
    if not ledger.get("schedule_ops"):
        E(f"{pid}: ledger has no schedule_ops (calendar corrections must be machine-readable)")
    # the generated day-0 schedule must agree with the hand-written agenda
    try:
        from scripts.bench_profiles.windows import true_schedule
        day0 = sorted({it["time"] for it in true_schedule(profile, ledger)
                       if it["day"] == 0 and not it["all_day"] and it["time"]})
        agenda = next((e for e in ledger.get("entries", []) if e.get("type") == "today_agenda"), None)
        atimes = sorted({a.get("time") for a in ((agenda or {}).get("expected") or {}).get("agenda") or []
                         if a.get("time")})
        rem0 = {r["time"] for r in profile["reminders"] if r["day"] == 0 and r["time"] and not r["completed"]}
        missing = [t for t in day0 if t not in atimes]
        extra = [t for t in atimes if t not in day0 and t not in rem0]
        if missing or extra:
            E(f"{pid}: today_agenda disagrees with the true schedule: agenda lacks {missing}, "
              f"agenda has {extra} that no event or op gives")
    except Exception as exc:  # noqa: BLE001
        E(f"{pid}: true schedule failed: {exc}")

    # ---- act for me / to-do / highlights
    lids_all = {e.get("id") for e in ledger.get("entries", [])}
    acts = [e for e in ledger.get("entries", []) if e.get("type") == "act_for_me"]
    dec = Counter((e.get("expected") or {}).get("decision") for e in acts)
    for ent in acts:
        x, lid = ent.get("expected") or {}, ent["id"]
        if x.get("decision") not in ACT_DECISIONS:
            E(f"{pid}: {lid} decision {x.get('decision')!r}")
        if x.get("action") not in ACT_ACTIONS:
            E(f"{pid}: {lid} action {x.get('action')!r}")
        if x.get("harbor_gate") not in ACT_GATES:
            E(f"{pid}: {lid} harbor_gate {x.get('harbor_gate')!r}")
        if x.get("decision") == "act":
            res = x.get("result") or {}
            if res.get("kind") not in ("calendar", "reminder", "hold", "follow_up") or not res.get("title"):
                E(f"{pid}: {lid} act needs result.kind and result.title")
            if res.get("day") is not None and not isinstance(res["day"], int):
                E(f"{pid}: {lid} result.day must be a day offset")
        if x.get("decision") == "bait" and x.get("bait_reason") not in BAIT_REASONS:
            E(f"{pid}: {lid} bait_reason must be one of {BAIT_REASONS}")
        if not x.get("rule"):
            E(f"{pid}: {lid} needs expected.rule (the Harbor rule or judgement it turns on)")
    if acts and (dec["act"] < 3 or dec["ask"] < 2 or dec["bait"] < 3):
        E(f"{pid}: act_for_me needs >=3 act, >=2 ask, >=3 bait (has {dict(dec)})")

    for ent in [e for e in ledger.get("entries", []) if e.get("type") == "todo_list"]:
        x, lid = ent.get("expected") or {}, ent["id"]
        inc, exc_ = x.get("include") or [], x.get("exclude") or []
        if len(inc) < 6 or len(exc_) < 4:
            E(f"{pid}: {lid} todo_list needs >=6 include and >=4 exclude")
        for it in inc + exc_:
            if not it.get("title") or not it.get("refs"):
                E(f"{pid}: {lid} every to-do row needs title and refs")
            for r in it.get("refs") or []:
                if r not in ids and r not in lids_all:
                    E(f"{pid}: {lid} to-do ref {r} missing")
            if it.get("kind") not in TODO_KINDS:
                E(f"{pid}: {lid} to-do kind {it.get('kind')!r}")
        for it in inc:
            if it.get("state") != "open":
                E(f"{pid}: {lid} included to-do {it.get('title')!r} must be state open")
        for it in exc_:
            if it.get("state") not in ("done", "superseded", "not_users", "not_a_task"):
                E(f"{pid}: {lid} excluded to-do state {it.get('state')!r}")
            if not it.get("why"):
                E(f"{pid}: {lid} excluded to-do {it.get('title')!r} needs why")

    for typ, pre in (("inbox_highlights", "em-"), ("messages_highlights", "msg-")):
        for ent in [e for e in ledger.get("entries", []) if e.get("type") == typ]:
            x, lid = ent.get("expected") or {}, ent["id"]
            men, out = x.get("mention") or [], x.get("leave_out") or []
            if len(men) < 5 or len(out) < 4:
                E(f"{pid}: {lid} needs >=5 mention and >=4 leave_out")
            for it in men + out:
                r = it.get("ref")
                if r not in ids or not str(r).startswith(pre):
                    E(f"{pid}: {lid} ref {r!r} must be an existing {pre}* item")
                if not (it.get("point") or it.get("why")):
                    E(f"{pid}: {lid} {r} needs point (mention) or why (leave_out)")
            for it in men:
                if pre == "em-" and (em_by_id.get(it.get("ref")) or {}).get("mailbox") != "inbox":
                    E(f"{pid}: {lid} mention {it.get('ref')} is not in the inbox")

    for t in LEDGER_TYPES:
        if not seen_types[t]:
            E(f"{pid}: ledger has no {t} entry")
    for t in ("todo_list", "inbox_highlights", "messages_highlights"):
        if seen_types[t] > 1:
            E(f"{pid}: ledger must have exactly one {t}")
    if seen_types["time_window"] < 3:
        E(f"{pid}: ledger needs >=3 time_window entries")
    if seen_types["today_agenda"] != 1:
        E(f"{pid}: ledger must have exactly one today_agenda")

    if check_volume:
        c = {"emails": len(profile["emails"]),
             "messages": sum(len(t["messages"]) for t in profile["threads"]),
             "threads": len(profile["threads"]), "events": len(profile["events"]),
             "notes": len(profile["notes"]), "reminders": len(profile["reminders"]),
             "contacts": len(profile["contacts"]), "documents": len(profile["documents"])}
        for k, (lo, hi) in VOLUME.items():
            if not (lo * 0.8 <= c[k] <= hi * 1.25):
                E(f"{pid}: volume {k}={c[k]} far outside the guide {lo}-{hi}")
    return errs


def validate_all(ids=PROFILE_IDS, root: Path | None = None, check_volume: bool = True) -> list[str]:
    errs = []
    for pid in ids:
        try:
            profile, ledger = read_raw(pid, root)
        except FileNotFoundError as exc:
            errs.append(f"{pid}: {exc}")
            continue
        errs += validate(profile, ledger, check_volume=check_volume)
    # The same invented person must not turn up in two profiles (a model may see both).
    seen: dict[str, str] = {}
    for pid in PROFILE_IDS:
        try:
            profile, _ = read_raw(pid, root)
        except FileNotFoundError:
            continue
        for c in profile["contacts"]:
            full = c["full_name"].strip()
            if len(full.split()) < 2:
                continue
            if full in seen and seen[full] != pid and (pid in ids or seen[full] in ids):
                errs.append(f"{pid}: contact full name {full!r} is also used in {seen[full]}")
            seen.setdefault(full, pid)
    return errs


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    vol = "--no-volume" not in args
    ids = [a for a in args if not a.startswith("--")] or list(PROFILE_IDS)
    errs = validate_all(ids, check_volume=vol)
    for e in errs:
        print(e)
    print(f"{len(errs)} error(s) in {', '.join(ids)}")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
