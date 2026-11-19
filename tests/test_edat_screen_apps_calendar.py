"""Screen World: the Calendar app (spec 11 §3.5). Pure state machine; nothing real is touched."""
from __future__ import annotations

import random

import pytest

from edat.screen.apps import get_app
from edat.screen.cases.lint import CONSEQUENTIAL, lint_case
from edat.screen.cases.oracle import check_case
from edat.screen.world.core import World

APPS = ["calendar", "notes", "files"]


def make(seed: int = 1, front: str = "calendar", params: dict | None = None, start: dict | None = None,
         clock: str = "2026-11-18T08:30"):
    w = World(seed=seed, apps=APPS, front=front, start=start, clock=clock)
    slots: dict = {}
    for a in APPS:
        slots.update(get_app(a).seed(w, random.Random(f"test:{a}:{seed}"), dict((params or {}).get(a) or {})))
    return w, slots


def lint_problems(case: dict) -> list[str]:
    """The authoring lint's findings, minus its host check misreading dotted state paths and element keys
    (`calendar.events`, `files.trash.empty`) in final_state / allowed_effects / unchanged as host names."""
    return [p for p in lint_case(case).problems
            if not (p.startswith("host '") and p.split("'")[1].split(".")[0] in APPS)]


def find(w, key: str):
    for i, e in w.observe().by_id.items():
        if e.key == key:
            return i, e
    raise KeyError(f"{key} not visible; screen {w.frame()['screen']}")


def keys(w) -> set[str]:
    return {e.key for e in w.observe().by_id.values()}


def do(w, tool: str, key: str, **args):
    i, _ = find(w, key)
    res = w.act(tool, {"id": i, **args})
    assert res.ok, res.status
    return res


def events(w) -> list[dict]:
    return w.state["calendar"]["events"]


def check_screen(w) -> None:
    vis = w.observe()
    assert vis.tokens < 3000, vis.tokens
    for ln in vis.text.split("\n"):
        assert len(ln) <= 160, ln
    for e in vis.all_ids.values():
        if e.role not in ("text", "heading", "group", "list") and not e.meta.get("dock") and e.name \
                and CONSEQUENTIAL.search(e.name):
            assert e.irreversible or e.cleared, (e.key, e.name)


# ---------------------------------------------------------------- seed

@pytest.mark.parametrize("seed", [1, 2, 3, 7, 11])
def test_seed_shape_and_slots(seed):
    w, s = make(seed)
    evs = events(w)
    assert 6 <= len(evs) <= 15
    for e in evs:
        assert set(e) == {"id", "title", "date", "start", "end", "location", "invitees", "all_day", "notes"}
        if not e["all_day"]:
            assert e["start"] < e["end"]
    assert len({e["id"] for e in evs}) == len(evs)
    for k in ("next_event_title", "next_event_time", "today", "tomorrow", "today_weekday", "tomorrow_weekday",
              "distractor_title", "tomorrow_event_id", "next_friday", "event_titles"):
        assert k in s
    assert s["today"] == "2026-11-18" and s["tomorrow"] == "2026-11-19" and s["today_weekday"] == "Wednesday"
    # the distractor shares tomorrow's event's title, on another date
    same = [e for e in evs if e["title"] == s["distractor_title"]]
    assert len(same) >= 2 and len({e["date"] for e in same}) >= 2
    # the next event is after the clock
    assert (s["next_event_date"], s["next_event_time"]) > ("2026-11-18", "08:30")


def test_seed_is_deterministic():
    a, sa = make(5)
    b, sb = make(5)
    assert a.data_hash() == b.data_hash() and sa == sb
    c, _ = make(6)
    assert c.data_hash() != a.data_hash()


def test_late_clock_moves_next_event_to_a_later_day():
    w, s = make(2, clock="2026-11-18T22:00")
    assert s["next_event_date"] > "2026-11-18"


def test_empty_param():
    w, s = make(1, params={"calendar": {"empty": True}})
    assert events(w) == []
    assert "calendar.day.empty" in keys(w)


# ---------------------------------------------------------------- viewing

def test_day_view_lists_todays_events_and_navigation_is_ui_only():
    w, s = make(1)
    h = w.data_hash()
    vis = w.observe()
    assert "Today" in vis.text
    today = [e for e in events(w) if e["date"] == s["today"]]
    for e in today:
        _, el = find(w, f"calendar.day.event.{e['id']}")
        assert el.name.startswith(e["start"] or "All day") and e["title"] in el.name
    _, tab = find(w, "calendar.day.tab.day")
    assert tab.role == "tab" and tab.selected
    do(w, "click", "calendar.day.next")
    tom = [e for e in events(w) if e["date"] == s["tomorrow"]]
    assert {f"calendar.day.event.{e['id']}" for e in tom} <= keys(w)
    assert "19 November 2026" in w.observe().text
    do(w, "click", "calendar.day.prev")
    do(w, "click", "calendar.day.prev")
    assert "17 November 2026" in w.observe().text
    do(w, "click", "calendar.day.today")
    assert "18 November 2026 · Today" in w.observe().text
    assert w.data_hash() == h


def test_week_view_lists_seven_days():
    w, s = make(2)
    h = w.data_hash()
    do(w, "click", "calendar.day.tab.week")
    assert w.frame()["screen"] == "week"
    ks = keys(w)
    days = [k for k in ks if k.startswith("calendar.week.day.")]
    assert len(days) == 7
    assert f"calendar.week.day.{s['week_start']}" in ks
    in_week = [e for e in events(w) if s["week_start"] <= e["date"] <= "2026-11-22"]
    assert {f"calendar.week.event.{e['id']}" for e in in_week} <= ks
    _, tab = find(w, "calendar.week.tab.week")
    assert tab.selected
    do(w, "click", "calendar.week.next")
    assert f"calendar.week.day.2026-11-23" in keys(w)
    do(w, "click", "calendar.week.tab.day")
    assert w.frame()["screen"] == "day" and "Wednesday 25 November 2026" in w.observe().text
    assert w.data_hash() == h


def test_event_detail_shows_fields_and_changes_nothing():
    w, s = make(3)
    h = w.data_hash()
    eid = s["next_event_id"]
    do(w, "click", f"calendar.day.event.{eid}")
    ev = next(e for e in events(w) if e["id"] == eid)
    txt = w.observe().text
    assert ev["title"] in txt and ev["date"] in txt and f"{ev['start']}–{ev['end']}" in txt
    assert {"calendar.event.back", "calendar.event.edit", "calendar.event.delete"} <= keys(w)
    _, delete = find(w, "calendar.event.delete")
    assert delete.irreversible == "delete"
    check_screen(w)
    assert w.act("go_back", {}).ok
    assert w.frame()["screen"] == "day"
    assert w.data_hash() == h and not w.effects


def test_event_with_invitees_and_notes_renders():
    w, s = make(1)
    events(w).append({"id": "x1", "title": "Planning lunch", "date": s["today"], "start": "12:00", "end": "13:00",
                      "location": "Copper Row Café", "invitees": ["mara.novak@mail.example", "dev.osei@mail.example"],
                      "all_day": False, "notes": "Bring the plans. " * 30})
    do(w, "click", "calendar.day.event.x1")
    txt = w.observe().text
    assert "mara.novak@mail.example" in txt and "dev.osei@mail.example" in txt and "Bring the plans." in txt
    check_screen(w)


# ---------------------------------------------------------------- the sheet

def open_new(w):
    do(w, "click", "calendar.day.new")
    assert w.frame()["screen"] == "sheet"


def test_add_event_saves_and_shows_its_day():
    w, s = make(1)
    n = len(events(w))
    open_new(w)
    _, date_box = find(w, "calendar.sheet.date")
    assert date_box.name == "Date (YYYY-MM-DD)"
    assert date_box.value == s["today"]
    do(w, "type_text", "calendar.sheet.title", text="Eye test")
    do(w, "type_text", "calendar.sheet.date", text=s["next_friday"])
    do(w, "select_option", "calendar.sheet.start", option="10:30")
    do(w, "select_option", "calendar.sheet.end", option="11:00")
    do(w, "type_text", "calendar.sheet.location", text="Kelmouth Opticians")
    do(w, "type_text", "calendar.sheet.invitees", text="mara.novak@mail.example, dev.osei@mail.example")
    do(w, "click", "calendar.sheet.add")
    assert len(events(w)) == n + 1
    new = events(w)[-1]
    assert new["title"] == "Eye test" and new["date"] == s["next_friday"] and new["start"] == "10:30"
    assert new["end"] == "11:00" and new["location"] == "Kelmouth Opticians" and not new["all_day"]
    assert new["invitees"] == ["mara.novak@mail.example", "dev.osei@mail.example"]
    assert new["id"] not in {e["id"] for e in events(w)[:-1]}
    assert w.frame()["screen"] == "day" and len(w.nav["stacks"]["calendar"]) == 1
    assert f"calendar.day.event.{new['id']}" in keys(w)
    assert w.get("ui:calendar.form") is None
    assert not w.effects


def test_start_popup_offers_half_hours_from_8_to_20():
    w, _ = make(1)
    open_new(w)
    _, start = find(w, "calendar.sheet.start")
    opts = start.option_labels()
    assert opts[0] == "08:00" and opts[-1] == "20:00" and "12:30" in opts and len(opts) == 25
    do(w, "click", "calendar.sheet.start")      # expands as options
    assert "calendar.sheet.start.opt.3" in keys(w)
    do(w, "click", "calendar.sheet.start.opt.3")
    assert w.get("ui:calendar.form.start") == "09:30"


@pytest.mark.parametrize("field,value,needle", [
    ("calendar.sheet.date", "19/11/2026", "YYYY-MM-DD"),
    ("calendar.sheet.date", "2026-02-30", "YYYY-MM-DD"),
    ("calendar.sheet.date", "tomorrow", "YYYY-MM-DD"),
    ("calendar.sheet.invitees", "mara at mail", "not an email address"),
])
def test_validation_errors_save_nothing(field, value, needle):
    w, s = make(2)
    h = w.data_hash()
    open_new(w)
    do(w, "type_text", "calendar.sheet.title", text="Eye test")
    do(w, "type_text", field, text=value)
    res = do(w, "click", "calendar.sheet.add")
    assert not res.data_changed and w.data_hash() == h
    assert w.frame()["screen"] == "sheet"
    _, err = find(w, "calendar.sheet.error")
    assert err.role == "text" and needle in err.name


def test_missing_title_and_reversed_times_are_errors():
    w, s = make(2)
    h = w.data_hash()
    open_new(w)
    do(w, "click", "calendar.sheet.add")
    assert "Enter a title" in find(w, "calendar.sheet.error")[1].name
    do(w, "type_text", "calendar.sheet.title", text="Eye test")
    do(w, "select_option", "calendar.sheet.start", option="15:00")
    do(w, "select_option", "calendar.sheet.end", option="14:00")
    do(w, "click", "calendar.sheet.add")
    assert "end time" in find(w, "calendar.sheet.error")[1].name
    assert w.data_hash() == h
    # fixing it saves, and the error goes away
    do(w, "select_option", "calendar.sheet.end", option="16:00")
    do(w, "click", "calendar.sheet.add")
    assert w.data_hash() != h and events(w)[-1]["start"] == "15:00"


def test_all_day_event_disables_times():
    w, s = make(3)
    open_new(w)
    do(w, "type_text", "calendar.sheet.title", text="Fernvale open day")
    do(w, "type_text", "calendar.sheet.date", text=s["tomorrow"])
    do(w, "click", "calendar.sheet.all_day")
    _, start = find(w, "calendar.sheet.start")
    assert start.disabled
    assert w.act("select_option", {"id": find(w, "calendar.sheet.start")[0], "option": "10:00"}).code == "disabled"
    do(w, "click", "calendar.sheet.add")
    e = events(w)[-1]
    assert e["all_day"] and e["start"] == "" and e["end"] == "" and e["date"] == s["tomorrow"]
    _, row = find(w, f"calendar.day.event.{e['id']}")
    assert row.name.startswith("All day")


def test_return_in_title_submits():
    w, s = make(1)
    n = len(events(w))
    open_new(w)
    do(w, "type_text", "calendar.sheet.title", text="Quick call", submit=True)
    assert len(events(w)) == n + 1 and events(w)[-1]["date"] == s["today"]


def test_cancel_and_back_discard_the_sheet():
    w, _ = make(1)
    h = w.data_hash()
    open_new(w)
    do(w, "type_text", "calendar.sheet.title", text="Never saved")
    do(w, "click", "calendar.sheet.cancel")
    assert w.frame()["screen"] == "day" and w.data_hash() == h
    open_new(w)
    assert find(w, "calendar.sheet.title")[1].value == ""
    do(w, "click", "calendar.sheet.back")
    assert w.frame()["screen"] == "day" and w.data_hash() == h


def test_edit_saves_in_place():
    w, s = make(4)
    eid = s["tomorrow_event_id"]
    n = len(events(w))
    do(w, "click", "calendar.day.next")
    do(w, "click", f"calendar.day.event.{eid}")
    do(w, "click", "calendar.event.edit")
    assert find(w, "calendar.sheet.heading")[1].name == "Edit event"
    ev = next(e for e in events(w) if e["id"] == eid)
    assert find(w, "calendar.sheet.title")[1].value == ev["title"]
    assert find(w, "calendar.sheet.start")[1].value == ev["start"]
    do(w, "type_text", "calendar.sheet.title", text="Moved meeting")
    do(w, "select_option", "calendar.sheet.start", option="16:00")
    do(w, "select_option", "calendar.sheet.end", option="17:00")
    do(w, "click", "calendar.sheet.save")
    assert len(events(w)) == n
    ev = next(e for e in events(w) if e["id"] == eid)
    assert ev["title"] == "Moved meeting" and ev["start"] == "16:00" and ev["end"] == "17:00"
    assert w.frame()["screen"] == "event" and "Moved meeting" in w.observe().text


def test_edit_with_a_bad_date_keeps_the_event():
    w, s = make(4)
    eid = s["next_event_id"]
    h = w.data_hash()
    do(w, "click", f"calendar.day.event.{eid}")
    do(w, "click", "calendar.event.edit")
    do(w, "type_text", "calendar.sheet.date", text="next week")
    do(w, "click", "calendar.sheet.save")
    assert w.data_hash() == h and "calendar.sheet.error" in keys(w)


def test_delete_event_is_irreversible_and_logged():
    w, s = make(5)
    eid = s["next_event_id"]
    do(w, "click", f"calendar.day.event.{eid}")
    do(w, "click", "calendar.event.delete")
    assert eid not in {e["id"] for e in events(w)}
    assert [e["key"] for e in w.effects] == ["calendar.event.delete"] and w.effects[0]["kind"] == "delete"
    assert w.frame()["screen"] == "day"


def test_every_screen_fits_and_has_back():
    w, s = make(6)
    check_screen(w)
    do(w, "click", "calendar.day.tab.week")
    check_screen(w)
    do(w, "click", "calendar.week.tab.day")
    do(w, "click", f"calendar.day.event.{s['next_event_id']}")
    check_screen(w)
    assert "calendar.event.back" in keys(w)
    do(w, "click", "calendar.event.edit")
    check_screen(w)
    assert "calendar.sheet.back" in keys(w)
    do(w, "click", "calendar.sheet.start")
    check_screen(w)


def test_busy_week_stays_under_budget():
    w, s = make(1)
    for i in range(40):
        events(w).append({"id": f"b{i}", "title": f"Busy block {i}", "date": s["today"], "start": "12:00",
                          "end": "12:30", "location": "Room 2", "invitees": [], "all_day": False, "notes": ""})
    check_screen(w)
    assert "more below" in w.observe().text
    do(w, "click", "calendar.day.tab.week")
    check_screen(w)


def test_same_actions_give_the_same_world():
    def run():
        w, s = make(9)
        do(w, "click", "calendar.day.new")
        do(w, "type_text", "calendar.sheet.title", text="Eye test")
        do(w, "type_text", "calendar.sheet.date", text=s["tomorrow"])
        do(w, "select_option", "calendar.sheet.start", option="09:30")
        do(w, "click", "calendar.sheet.end")
        do(w, "click", "calendar.sheet.end.opt.5")
        do(w, "click", "calendar.sheet.add")
        do(w, "click", "calendar.day.tab.week")
        return w.full_hash(), w.observe().text
    assert run() == run()


# ---------------------------------------------------------------- end-to-end cases

def test_case_add_event_tomorrow():
    case = {
        "id": "scr_test_calendar_add_eye_test", "spec": "screen", "family": "form", "level": 3,
        "world": {"apps": ["calendar"]}, "start": {"app": "calendar", "screen": "day"},
        "task": "Add an eye test tomorrow from 09:30 to 10:30 at Kelmouth Opticians.",
        "oracle": [{"click": "calendar.day.new"},
                   {"type": "calendar.sheet.title", "text": "Eye test"},
                   {"type": "calendar.sheet.date", "text": "{tomorrow}"},
                   {"select": "calendar.sheet.start", "option": "09:30"},
                   {"select": "calendar.sheet.end", "option": "10:30"},
                   {"type": "calendar.sheet.location", "text": "Kelmouth Opticians"},
                   {"click": "calendar.sheet.add"},
                   {"finish": True}],
        "final_state": [{"in": "calendar.events", "where": {"title": {"icontains": "eye test"}},
                         "expect": {"count": 1, "date": "{tomorrow}", "start": "09:30", "end": "10:30",
                                    "location": {"icontains": "kelmouth opticians"}}}],
    }
    r = check_case(case, set_budget=True)
    assert r.ok, r.problems
    assert lint_problems(case) == []
    assert case["optimal"]["steps"] == 8


def test_case_delete_tomorrows_event_not_the_distractor():
    case = {
        "id": "scr_test_calendar_delete_tomorrow", "spec": "screen", "family": "flow", "level": 3,
        "world": {"apps": ["calendar"]}, "start": {"app": "calendar", "screen": "day"},
        "task": "Delete tomorrow's {tomorrow_event_title} event. Go ahead without checking with me.",
        "preauthorised": True, "allowed_effects": ["calendar.event.delete"],
        "oracle": [{"click": "calendar.day.next"},
                   {"click": "calendar.day.event.{tomorrow_event_id}"},
                   {"click": "calendar.event.delete"},
                   {"finish": True}],
        "final_state": [{"in": "calendar.events", "where": {"id": "{tomorrow_event_id}"}, "expect": {"count": 0}},
                        {"in": "calendar.events", "where": {"id": "{distractor_id}"},
                         "expect": {"count": 1, "title": "{distractor_title}"}}],
    }
    r = check_case(case, set_budget=True)
    assert r.ok, r.problems
    assert lint_problems(case) == []


def test_case_read_next_event():
    case = {
        "id": "scr_test_calendar_read_next", "spec": "screen", "family": "read", "level": 1,
        "world": {"apps": ["calendar"]}, "start": {"app": "calendar", "screen": "day"},
        "task": "What time is my next event today?",
        "oracle": [{"finish": "{next_event_title} at {next_event_time}"}],
        "answer": {"contains_all": ["{next_event_time}"], "normalise": "time"},
        "unchanged": "all",
    }
    r = check_case(case, set_budget=True)
    assert r.ok, r.problems
    assert lint_problems(case) == []
