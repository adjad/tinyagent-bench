"""Calendar: Day and Week views, an event reader, and the New event / Edit sheet (spec 11 §3.5).

Data state is `calendar.events`, a list of

    {id, title, date: "YYYY-MM-DD", start: "HH:MM", end: "HH:MM", location, invitees: [...], all_day, notes}

(`start` and `end` are "" for an all-day event). The shown date, the Day/Week
choice and the sheet's unsaved fields are UI state under `ui:calendar.*`, so
opening events and moving between days never changes data. "Delete event" is
the only irreversible control; adding and editing are ordinary saves.
"""
from __future__ import annotations

import random
import re
from datetime import date, timedelta

from edat.screen.apps import App
from edat.screen.apps import pools
from edat.screen.world.elements import El, Screen, button, checkbox, group, heading, popup, text, textbox

TIMES = [f"{h:02d}:{m:02d}" for h in range(8, 21) for m in (0, 30) if (h, m) != (20, 30)]   # 08:00 .. 20:00
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
FORM = "calendar.form"
FORM_FIELDS = ("title", "date", "start", "end", "location", "invitees", "all_day", "error")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")

ONE_OFF = [("Dentist", "Larkspur Dental"), ("Vet appointment", "Oakridge Vets"), ("Book club", "Kelmouth Library"),
           ("Lunch with {first}", "Copper Row Café"), ("Call with {name}", ""), ("Plumber visit", "Home"),
           ("Parking permit renewal", "Fernvale Council"), ("Bake sale", "Fernvale School hall"),
           ("Piano recital", "Ashbourne Chapel"), ("Bike repair", "Cinder Path Cycles"), ("Carpool to school", ""),
           ("Allotment work day", "Heron Way allotments"), ("Haircut", "Mill Street Barbers"),
           ("Insurance review", "Brightwater Insurance"), ("Coffee with {first}", "Lantern Walk Café")]
RECURRING = [("Team stand-up", "Video call", "09:00", "09:15"), ("Gym", "Thornby Gym", "18:00", "19:00"),
             ("Choir practice", "Fernvale Hall", "19:00", "20:00")]
ALL_DAY = [("Fernvale street fair", "Town square"), ("School inset day", ""), ("Kelmouth half marathon", "Seafront")]
NOTES = ["Bring the referral letter.", "Parking is behind the building.", "Ask about the {topic}.",
         "Agenda: the {topic} and anything else.", "Pay at the front desk.", "Remember the reusable bags."]


# ---------------------------------------------------------------- small helpers

def _ui(world, path: str, default=None):
    return world.get("ui:" + path, default)


def _set_ui(world, path: str, value) -> None:
    world.set("ui:" + path, value)


def _clear_ui(world, path: str) -> None:
    """Drop one UI field and every field under it (UI fields are flat keys: "calendar.form.title")."""
    fields = world.ui["fields"]
    for k in [k for k in fields if k == path or k.startswith(path + ".")]:
        del fields[k]


def _clip(s: str, n: int = 150) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _chunks(s: str, n: int = 130) -> list[str]:
    words, out, cur = str(s).split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > n:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}" if cur else w
    if cur:
        out.append(cur)
    return out


def long_date(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]} {d.year}"


def short_date(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()][:3]} {d.day} {MONTHS[d.month - 1][:3]}"


def parse_date(raw: str) -> date | None:
    raw = str(raw or "").strip()
    if not DATE_RE.match(raw):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def sort_key(ev: dict):
    return (ev.get("date", ""), 0 if ev.get("all_day") else 1, ev.get("start", ""), str(ev.get("title", "")).casefold(),
            str(ev.get("id", "")))


def row_label(ev: dict) -> str:
    when = "All day" if ev.get("all_day") else (ev.get("start") or "")
    lab = f"{when} {ev.get('title', '')}".strip()
    if ev.get("location"):
        lab += f" — {ev['location']}"
    return _clip(lab)


def _form(world) -> dict:
    return {k: _ui(world, f"{FORM}.{k}") for k in FORM_FIELDS}


def parse_invitees(raw: str) -> list[str]:
    return [x.strip() for x in re.split(r"[,;]", str(raw or "")) if x.strip()]


def new_id(events: list[dict]) -> str:
    used = {str(e.get("id")) for e in events}
    k = len(events) + 1
    while f"e{k}" in used:
        k += 1
    return f"e{k}"


class Calendar(App):
    name = "calendar"
    title = "Calendar"
    home = "day"

    # ------------------------------------------------------------ state
    def seed(self, world, rng: random.Random, params: dict) -> dict:
        today = date.fromisoformat(world.clock[:10])
        now = world.clock[11:16] if len(world.clock) >= 16 else "00:00"
        events: list[dict] = []
        taken: set[tuple[str, str]] = set()

        def add(title, d: date, start="", end="", location="", invitees=None, all_day=False, notes=""):
            ev = {"id": f"e{len(events) + 1}", "title": title, "date": d.isoformat(), "start": start, "end": end,
                  "location": location, "invitees": list(invitees or []), "all_day": bool(all_day), "notes": notes}
            events.append(ev)
            if not all_day:
                taken.add((ev["date"], start))
            return ev

        def free_slot(d: date, lo: str = "09:00", hi: str = "18:30") -> str:
            opts = [t for t in TIMES if lo <= t <= hi and (d.isoformat(), t) not in taken]
            return rng.choice(opts) if opts else lo

        def end_for(start: str) -> str:
            i = TIMES.index(start) if start in TIMES else 0
            return TIMES[min(len(TIMES) - 1, i + rng.choice([1, 2, 3]))]

        def fill(s: str) -> str:
            p = pools.person(rng)
            return s.format(first=p["first"], name=p["name"], topic=rng.choice(pools.TOPICS))

        slots: dict = {}
        if not params.get("empty"):
            rec = RECURRING[rng.randrange(len(RECURRING))]
            rec_days = [today + timedelta(days=o) for o in (-1, 0, 1, 2)][: rng.choice([3, 4])]
            for d in rec_days:
                add(rec[0], d, rec[2], rec[3], rec[1])
            n_one = int(params.get("extra", rng.randint(5, 8)))
            temps = rng.sample(ONE_OFF, k=min(len(ONE_OFF), max(2, n_one)))
            later = [t for t in TIMES if now < t <= "17:00" and t >= "09:30"]
            for i, (t_title, t_loc) in enumerate(temps):
                if i == 0:      # one later today (or tomorrow when the clock is late)
                    d = today if later else today + timedelta(days=1)
                    start = rng.choice([t for t in later if (d.isoformat(), t) not in taken] or later) if later \
                        else free_slot(d)
                elif i == 1:    # one tomorrow
                    d = today + timedelta(days=1)
                    start = free_slot(d)
                else:
                    d = today + timedelta(days=rng.choice([-3, -2, -1, 2, 3, 4, 5, 6, 7]))
                    start = free_slot(d)
                guests = [p["email"] for p in pools.people(rng, rng.randint(1, 3))] if rng.random() < 0.4 else []
                notes = fill(rng.choice(NOTES)) if rng.random() < 0.4 else ""
                add(fill(t_title), d, start, end_for(start), t_loc, guests, False, notes)
            # a same-title distractor of tomorrow's event, a week or more later
            base = events[len(rec_days) + 1]
            dd = today + timedelta(days=rng.randint(8, 13))
            ds = free_slot(dd)
            dist = add(base["title"], dd, ds, end_for(ds), base["location"])
            slots.update({"distractor_title": dist["title"], "distractor_date": dist["date"],
                          "distractor_time": dist["start"], "distractor_id": dist["id"],
                          "tomorrow_event_id": base["id"], "tomorrow_event_title": base["title"],
                          "tomorrow_event_time": base["start"], "tomorrow_event_location": base["location"],
                          "recurring_title": rec[0], "recurring_time": rec[2]})
            if rng.random() < 0.5:
                ad = ALL_DAY[rng.randrange(len(ALL_DAY))]
                add(ad[0], today + timedelta(days=rng.randint(2, 6)), "", "", ad[1], all_day=True)
        world.state["calendar"] = {"events": events}

        ordered = sorted(events, key=sort_key)
        upcoming = [e for e in ordered if not e["all_day"] and (e["date"], e["start"]) > (today.isoformat(), now)]
        tomorrow = today + timedelta(days=1)
        tom = [e for e in ordered if e["date"] == tomorrow.isoformat()]
        tod = [e for e in ordered if e["date"] == today.isoformat()]
        guest = pools.person(rng)
        slots.update({
            "today": today.isoformat(), "tomorrow": tomorrow.isoformat(),
            "yesterday": (today - timedelta(days=1)).isoformat(),
            "today_weekday": WEEKDAYS[today.weekday()], "tomorrow_weekday": WEEKDAYS[tomorrow.weekday()],
            "week_start": (today - timedelta(days=today.weekday())).isoformat(),
            "event_titles": [e["title"] for e in ordered], "event_ids": [e["id"] for e in ordered],
            "events_today": [e["title"] for e in tod], "events_today_count": len(tod),
            "events_tomorrow_count": len(tom),
            "invitee_name": guest["name"], "invitee_email": guest["email"],
        })
        for i, wd in enumerate(WEEKDAYS):
            ahead = (i - today.weekday()) % 7 or 7
            slots[f"next_{wd.lower()}"] = (today + timedelta(days=ahead)).isoformat()
        if upcoming:
            n = upcoming[0]
            slots.update({"next_event_title": n["title"], "next_event_time": n["start"], "next_event_end": n["end"],
                          "next_event_date": n["date"], "next_event_location": n["location"],
                          "next_event_id": n["id"]})
        if tom:
            slots.update({"tomorrow_first_title": tom[0]["title"], "tomorrow_first_time": tom[0]["start"],
                          "tomorrow_first_id": tom[0]["id"]})
        return slots

    # ------------------------------------------------------------ helpers
    def _events(self, world) -> list[dict]:
        return world.state.setdefault("calendar", {}).setdefault("events", [])

    def _find(self, world, eid: str) -> dict | None:
        return next((e for e in self._events(world) if str(e.get("id")) == str(eid)), None)

    def _today(self, world) -> date:
        return date.fromisoformat(world.clock[:10])

    def _shown(self, world) -> date:
        return parse_date(_ui(world, "calendar.shown") or "") or self._today(world)

    def _move(self, days: int | None):
        def go(w):
            d = self._today(w) if days is None else self._shown(w) + timedelta(days=days)
            _set_ui(w, "calendar.shown", d.isoformat())
        return go

    def _tabs(self, world, current: str) -> El:
        def to(screen: str):
            def go(w):
                if w.frame("calendar")["screen"] != screen:
                    w.replace("calendar", screen)
            return go
        p = f"calendar.{current}.tab"
        return group(f"calendar.{current}.tabs", "View", [
            El(key=f"{p}.day", role="tab", name="Day", selected=current == "day", on_click=to("day")),
            El(key=f"{p}.week", role="tab", name="Week", selected=current == "week", on_click=to("week")),
        ])

    def _open_event(self, eid: str):
        def go(w):
            w.push("calendar", "event", id=eid)
        return go

    def _start_form(self, world, ev: dict | None) -> None:
        _clear_ui(world, FORM)
        if ev is None:
            vals = {"title": "", "date": self._shown(world).isoformat(), "start": "09:00", "end": "10:00",
                    "location": "", "invitees": "", "all_day": False}
        else:
            vals = {"title": ev.get("title", ""), "date": ev.get("date", ""),
                    "start": ev.get("start") or "09:00", "end": ev.get("end") or "10:00",
                    "location": ev.get("location", ""), "invitees": ", ".join(ev.get("invitees") or []),
                    "all_day": bool(ev.get("all_day"))}
        for k, v in vals.items():
            _set_ui(world, f"{FORM}.{k}", v)

    def _new(self, w) -> None:
        self._start_form(w, None)
        w.push("calendar", "sheet")

    def _edit(self, eid: str):
        def go(w):
            ev = self._find(w, eid)
            if ev is None:
                return None
            self._start_form(w, ev)
            w.push("calendar", "sheet", id=eid)
        return go

    def _close_sheet(self, w) -> None:
        _clear_ui(w, FORM)
        w.pop("calendar")

    def _save(self, eid: str | None):
        def go(w):
            f = _form(w)
            title = " ".join(str(f.get("title") or "").split())
            d = parse_date(f.get("date") or "")
            all_day = bool(f.get("all_day"))
            start, end = str(f.get("start") or ""), str(f.get("end") or "")
            guests = parse_invitees(f.get("invitees") or "")
            err = None
            if not title:
                err = "Enter a title."
            elif d is None:
                err = "Enter the date as YYYY-MM-DD."
            elif not all_day and not (re.match(r"^\d{2}:\d{2}$", start) and re.match(r"^\d{2}:\d{2}$", end)):
                err = "Choose a start time and an end time."
            elif not all_day and end <= start:
                err = "The end time must be after the start time."
            else:
                bad = [g for g in guests if not EMAIL_RE.match(g)]
                if bad:
                    err = f"{_clip(bad[0], 60)} is not an email address."
            if err:
                _set_ui(w, f"{FORM}.error", err)
                return None
            rec = {"title": title, "date": d.isoformat(), "start": "" if all_day else start,
                   "end": "" if all_day else end, "location": " ".join(str(f.get("location") or "").split()),
                   "invitees": guests, "all_day": all_day}
            events = self._events(w)
            if eid is None:
                rec = {"id": new_id(events), **rec, "notes": ""}
                events.append(rec)
                _clear_ui(w, FORM)
                _set_ui(w, "calendar.shown", rec["date"])
                w.reset_stack("calendar", "day")
                return "event added"
            ev = self._find(w, eid)
            if ev is None:
                _set_ui(w, f"{FORM}.error", "This event was deleted.")
                return None
            ev.update(rec)
            _clear_ui(w, FORM)
            w.pop("calendar")
            return "event saved"
        return go

    def _delete(self, eid: str):
        def go(w):
            cal = w.state.setdefault("calendar", {})
            cal["events"] = [e for e in cal.get("events", []) if str(e.get("id")) != str(eid)]
            w.pop("calendar")
            return "event deleted"
        return go

    def _nav_buttons(self, scr: str) -> list[El]:
        return [button(f"calendar.{scr}.prev", "Previous", on_click=self._move(-1 if scr == "day" else -7)),
                button(f"calendar.{scr}.today", "Today", on_click=self._move(None)),
                button(f"calendar.{scr}.next", "Next", on_click=self._move(1 if scr == "day" else 7)),
                button(f"calendar.{scr}.new", "New event", on_click=self._new)]

    # ------------------------------------------------------------ screens
    def screen_day(self, world) -> Screen:
        shown = self._shown(world)
        today = self._today(world)
        evs = sorted((e for e in self._events(world) if e.get("date") == shown.isoformat()), key=sort_key)
        title = long_date(shown) + (" · Today" if shown == today else "")
        els = [heading("calendar.day.title", title), self._tabs(world, "day"), *self._nav_buttons("day")]
        if evs:
            els.append(El(key="calendar.day.events", role="list", name="Events", children=[
                El(key=f"calendar.day.event.{e['id']}", role="row", name=row_label(e), on_click=self._open_event(e["id"]),
               cleared=True) for e in evs]))
        else:
            els.append(text("calendar.day.empty", "No events"))
        return Screen("Calendar", els)

    def screen_week(self, world) -> Screen:
        shown = self._shown(world)
        today = self._today(world)
        monday = shown - timedelta(days=shown.weekday())
        evs = sorted(self._events(world), key=sort_key)
        days = []
        for i in range(7):
            d = monday + timedelta(days=i)
            rows = [El(key=f"calendar.week.event.{e['id']}", role="row", name=row_label(e),
                       on_click=self._open_event(e["id"]), cleared=True) for e in evs if e.get("date") == d.isoformat()]
            if not rows:
                rows = [text(f"calendar.week.empty.{d.isoformat()}", "No events")]
            label = short_date(d) + (" · Today" if d == today else "")
            days.append(group(f"calendar.week.day.{d.isoformat()}", label, rows))
        return Screen("Calendar", [heading("calendar.week.title", f"Week of {long_date(monday)}"),
                                   self._tabs(world, "week"), *self._nav_buttons("week"), *days])

    def screen_event(self, world, id: str) -> Screen:
        ev = self._find(world, id)
        back = button("calendar.event.back", "Back", on_click=lambda w: w.pop("calendar"))
        if ev is None:
            return Screen("Event", [back, text("calendar.event.gone", "This event was deleted.")])
        d = parse_date(ev.get("date", ""))
        when = "All day" if ev.get("all_day") else f"{ev.get('start', '')}–{ev.get('end', '')}"
        els = [back, heading("calendar.event.title", _clip(ev.get("title", ""))),
               text("calendar.event.date", f"Date: {long_date(d)} ({d.isoformat()})" if d else f"Date: {ev.get('date')}"),
               text("calendar.event.time", f"Time: {when}"),
               text("calendar.event.location", f"Location: {_clip(ev.get('location') or 'none')}")]
        guests = list(ev.get("invitees") or [])
        if guests:
            els.append(group("calendar.event.invitees", "Invitees", [
                text(f"calendar.event.invitee.{i}", _clip(g)) for i, g in enumerate(guests)]))
        else:
            els.append(text("calendar.event.invitees", "Invitees: none"))
        notes = ev.get("notes") or ""
        paras = notes if isinstance(notes, list) else [notes]
        lines = [c for p in paras for c in _chunks(p)]
        if lines:
            els.append(group("calendar.event.notes", "Notes", [
                text(f"calendar.event.note.{i}", ln) for i, ln in enumerate(lines)]))
        els += [button("calendar.event.edit", "Edit", on_click=self._edit(ev["id"])),
                button("calendar.event.delete", "Delete event", on_click=self._delete(ev["id"]), irreversible="delete")]
        return Screen(_clip(ev.get("title", "Event"), 80), els)

    def screen_sheet(self, world, id: str | None = None) -> Screen:
        f = _form(world)
        all_day = bool(f.get("all_day"))
        save = self._save(id)
        els = [button("calendar.sheet.back", "Back", on_click=self._close_sheet),
               heading("calendar.sheet.heading", "Edit event" if id else "New event")]
        if f.get("error"):
            els.append(text("calendar.sheet.error", f"Error: {f['error']}"))
        els += [
            textbox("calendar.sheet.title", "Title", f"ui:{FORM}.title", on_submit=save),
            textbox("calendar.sheet.date", "Date (YYYY-MM-DD)", f"ui:{FORM}.date", on_submit=save),
            popup("calendar.sheet.start", "Start time", f"ui:{FORM}.start", TIMES, disabled=all_day),
            popup("calendar.sheet.end", "End time", f"ui:{FORM}.end", TIMES, disabled=all_day),
            textbox("calendar.sheet.location", "Location", f"ui:{FORM}.location", on_submit=save),
            textbox("calendar.sheet.invitees", "Invitees (comma-separated emails)", f"ui:{FORM}.invitees",
                    on_submit=save),
            checkbox("calendar.sheet.all_day", "All-day", f"ui:{FORM}.all_day"),
            button("calendar.sheet.save" if id else "calendar.sheet.add", "Save" if id else "Add", on_click=save),
            button("calendar.sheet.cancel", "Cancel", on_click=self._close_sheet),
        ]
        return Screen("Edit event" if id else "New event", els)


APP = Calendar()
