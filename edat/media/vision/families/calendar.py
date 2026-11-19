"""Calendar: day, week and month views drawn from an invented event list.

Planted fields: weekday names, dates, event titles, start and end times, locations, month title.
Question types: event (what is at a time), lookup (start time, location, date), count,
free_slot (the one free hour, the one empty day), absence (and the reading).

Three templates: every template is reachable in each theme, scale and abstention slot.
"""
from __future__ import annotations

import calendar as _cal
import random
from datetime import date, timedelta

from edat.media.common import EVENT_WORDS, MONTHS, WEEKDAYS, Inventor, date_long, time_12h
from edat.media.vision.canvas import Canvas
from edat.media.vision.items import ABSTAIN_ANSWER, Question, Rendered

FAMILY = "calendar"
TEMPLATES = ("day", "week", "month")
LOGICAL_SIZES = {"day": (520, 740), "week": (1120, 640), "month": (940, 800)}


def render(rng: random.Random, *, template: str, theme: str, scale: float,
           abstain: bool) -> Rendered:
    fn = {"day": _day, "week": _week, "month": _month}.get(template)
    if fn is None:
        raise ValueError(f"unknown template {template!r}")
    return fn(rng, theme=theme, scale=scale, abstain=abstain)


def _hm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def _tail(c, rng, abstain, qs, normal, blur, absent):
    if not abstain:
        qs.append(normal)
        return
    if absent is not None and rng.random() < 0.5:
        qid, text, name, value, kind = absent
        c.absent(name, value, kind)
        qs.append(Question(qid, "absence", text, ABSTAIN_ANSWER, kind="abstain", fields=[name]))
    else:
        c.blur_field(blur, radius=7)
        qs.append(Question(normal.qid, "absence", normal.question, ABSTAIN_ANSWER,
                           kind="abstain", fields=[blur]))


def _sched(rng, lo, hi, n, breaks=(0, 30, 30, 60, 90)):
    """Up to ``n`` events (start, end minutes) between lo and hi, in order, not overlapping."""
    ev, t = [], lo + 30 * rng.randint(0, 2)
    while len(ev) < n:
        d = rng.choice((60, 60, 90, 120))
        if t + d > hi:
            break
        ev.append((t, t + d))
        t += d + rng.choice(breaks)
    return ev


def _titles(rng, c, inv, n, size, max_w, used=None):
    """n unique event titles that fit max_w logical px at ``size``."""
    used = used if used is not None else set()
    out = []
    while len(out) < n:
        word = rng.choice(EVENT_WORDS)
        t = word if rng.random() < 0.5 else f"{word} with {inv.given()}"
        if c.text_width(t, size) > max_w or t in used:
            continue
        used.add(t)
        out.append(t)
    return out


def _place(rng, inv):
    return rng.choice((f"Room {rng.choice('ABCDE')}{rng.randint(1, 9)}", "Online",
                       inv.merchant(), f"Studio {rng.randint(1, 9)}"))


def _free_hour_day(rng):
    """A day's events with exactly one free gap of exactly 60 minutes between 9:00 and 17:00."""
    lo, hi = 9 * 60, 17 * 60
    for _ in range(400):
        ev = _sched(rng, lo, hi, rng.randint(4, 6), breaks=(0, 0, 30, 60))
        gaps, t = [], lo
        for s, e in ev:
            gaps.append((t, s))
            t = e
        gaps.append((t, hi))
        free = [(a, b) for a, b in gaps if b - a >= 60]
        if len(ev) >= 3 and len(free) == 1 and free[0][1] - free[0][0] == 60:
            return ev, free[0][0]
    return [(540, 630), (630, 720), (780, 840), (840, 960), (960, 1020)], 720


# ---------------------------------------------------------------------------- day view
def _day(rng, *, theme, scale, abstain):
    inv = Inventor(rng)
    w, h = LOGICAL_SIZES["day"]
    c = Canvas(w, h, scale=scale, theme=theme)
    monday = date(2027, 1, 4) + timedelta(days=7 * rng.randint(0, 45))
    day = monday + timedelta(days=rng.randint(0, 4))
    ev, gap_start = _free_hour_day(rng)
    titles = _titles(rng, c, inv, len(ev), 13, 330)
    locs = [_place(rng, inv) for _ in ev]
    c.text(24, 22, date_long(day), size=22, bold=True, field="day_date", kind="date",
           value=day.isoformat())
    top, hh = 80, 64
    for hr in range(8, 19):
        y = top + (hr - 8) * hh
        c.line(70, y, w - 16, y, color="grid")
        c.text(16, y - 7, time_12h(_hm(hr * 60)).replace(":00", ""), size=11, color="muted")
    for i, (s, e) in enumerate(ev):
        y0 = top + (s - 480) * hh / 60 + 2
        y1 = top + (e - 480) * hh / 60 - 2
        c.rect(76, y0, w - 20, y1, fill="surface", radius=6)
        c.rect(76, y0, 80, y1, fill="accent")
        c.text(92, y0 + 4, titles[i], size=13, field=f"title_{i}")
        x = 92
        ts = time_12h(_hm(s))
        c.text(x, y0 + 23, ts, size=11, field=f"start_{i}", kind="time", value=_hm(s))
        x += c.text_width(ts, 11) + 6
        c.text(x, y0 + 23, "-", size=11, color="muted")
        c.text(x + 10, y0 + 23, time_12h(_hm(e)), size=11, field=f"end_{i}", kind="time",
               value=_hm(e))
        c.text(92, y0 + 41, locs[i], size=11, color="muted", field=f"loc_{i}")
    n = len(ev)
    wd = WEEKDAYS[day.weekday()]
    ia = rng.randrange(n)
    hours = [x for x in range(8, 18) if ev[ia][0] <= x * 60 < ev[ia][1]]
    hr = rng.choice(hours)
    rest = [i for i in range(n) if i not in (ia,)]
    f = rng.choice(rest)
    g = rng.choice([i for i in rest if i != f])
    sname = time_12h(_hm(hr * 60))
    qs = [
        Question("q_slot", "free_slot", f"Between 9 AM and 5 PM on {wd}, there is exactly one "
                 "free hour with no events. At what time does it start?", _hm(gap_start),
                 canon="time", fields=[f"start_{i}" for i in range(n)] +
                 [f"end_{i}" for i in range(n)]),
        Question("q_at", "event", f"What event is scheduled at {sname} on {wd}?", titles[ia],
                 fields=[f"title_{ia}", f"start_{ia}", f"end_{ia}"]),
        Question("q_start", "lookup", f'At what time does "{titles[f]}" start?', _hm(ev[f][0]),
                 canon="time", fields=[f"title_{f}", f"start_{f}"]),
    ]
    _tail(c, rng, abstain, qs,
          Question("q_loc", "lookup", f'Where is the event "{titles[g]}" held?', locs[g],
                   fields=[f"title_{g}", f"loc_{g}"]),
          f"loc_{g}", ("q_host", f'Who is hosting the event "{titles[g]}"?', "host",
                       inv.person(), "text"))
    qs.append(Question("q_reading", "reading", "Read the screen.", "", kind="reading"))
    data = {"date": day.isoformat(), "free_hour_start": _hm(gap_start), "events": [
        {"title": titles[i], "start": _hm(ev[i][0]), "end": _hm(ev[i][1]), "location": locs[i]}
        for i in range(n)]}
    return Rendered(c, "day", qs, data, default_year=day.year)


# ---------------------------------------------------------------------------- week view
def _week(rng, *, theme, scale, abstain):
    inv = Inventor(rng)
    w, h = LOGICAL_SIZES["week"]
    c = Canvas(w, h, scale=scale, theme=theme)
    monday = date(2027, 1, 4) + timedelta(days=7 * rng.randint(0, 45))
    days = [monday + timedelta(days=i) for i in range(7)]
    empty = rng.randrange(7)
    x0, cw, top, hh = 72, 146, 112, 48
    sunday = days[6]
    label = f"{days[0].strftime('%b')} {days[0].day} - {sunday.strftime('%b')} {sunday.day}, {sunday.year}"
    c.text(72, 20, label, size=22, bold=True)
    events = []                          # (day index, start, end, title)
    used: set[str] = set()
    for d in range(7):
        if d == empty:
            continue
        sched = _sched(rng, 8 * 60, 18 * 60, rng.randint(1, 2), breaks=(30, 60, 90, 120))
        tl = _titles(rng, c, inv, len(sched), 12, cw - 28, used)
        events += [(d, s, e, t) for (s, e), t in zip(sched, tl)]
    for d in range(7):
        x = x0 + d * cw
        c.text(x + 8, 52, WEEKDAYS[d], size=13, field=f"wd_{d}")
        c.text(x + 8, 72, f"{days[d].strftime('%b')} {days[d].day}", size=12, color="muted",
               field=f"dt_{d}", kind="date", value=days[d].isoformat())
    for hr in range(8, 19):
        y = top + (hr - 8) * hh
        c.line(x0 - 6, y, w - 6, y, color="grid")
        if hr < 18:
            c.text(12, y + 2, time_12h(_hm(hr * 60)).replace(":00", ""), size=11, color="muted",
                   field=f"hr_{hr}", kind="time", value=_hm(hr * 60))
    for d in range(8):
        c.line(x0 + d * cw - 6, top - 8, x0 + d * cw - 6, top + 10 * hh, color="grid")
    for i, (d, s, e, t) in enumerate(events):
        x = x0 + d * cw
        y0 = top + (s - 480) * hh / 60 + 2
        y1 = top + (e - 480) * hh / 60 - 2
        c.rect(x, y0, x + cw - 12, y1, fill="surface", radius=5)
        c.rect(x, y0, x + 3, y1, fill="accent")
        c.text(x + 10, y0 + 4, t, size=12, field=f"title_{i}")
        c.text(x + 10, y0 + 24, time_12h(_hm(s)), size=11, color="muted", field=f"start_{i}",
               kind="time", value=_hm(s))
    n = len(events)
    ia = rng.randrange(n)
    d_a, s_a, e_a, t_a = events[ia]
    hr = rng.choice([x for x in range(8, 18) if s_a <= x * 60 < e_a])
    by_day = {}
    for i, ev in enumerate(events):
        by_day.setdefault(ev[0], []).append(i)
    dc = rng.choice(sorted(by_day))
    rest = [i for i in range(n) if i != ia]
    f = rng.choice(rest)
    qs = [
        Question("q_at", "event", f"What event is scheduled on {WEEKDAYS[d_a]} at "
                 f"{time_12h(_hm(hr * 60))}?", t_a,
                 fields=[f"title_{ia}", f"wd_{d_a}", f"hr_{hr}", f"start_{ia}"]),
        Question("q_free", "free_slot", "Which day of this week has no events at all?",
                 WEEKDAYS[empty], fields=[f"wd_{empty}"] + [f"title_{i}" for i in range(n)]),
        Question("q_count", "count", f"How many events are scheduled on {WEEKDAYS[dc]}?",
                 str(len(by_day[dc])), canon="number",
                 fields=[f"wd_{dc}"] + [f"title_{i}" for i in by_day[dc]]),
    ]
    tf = events[f][3]
    _tail(c, rng, abstain, qs,
          Question("q_start", "lookup", f'At what time does "{tf}" start?', _hm(events[f][1]),
                   canon="time", fields=[f"title_{f}", f"start_{f}"]),
          f"start_{f}", ("q_loc", f'Where is the event "{tf}" held?', "location",
                         inv.merchant(), "text"))
    qs.append(Question("q_reading", "reading", "Read the screen.", "", kind="reading"))
    data = {"week_start": monday.isoformat(), "empty_day": WEEKDAYS[empty], "events": [
        {"day": days[d].isoformat(), "start": _hm(s), "end": _hm(e), "title": t}
        for d, s, e, t in events]}
    return Rendered(c, "week", qs, data, default_year=monday.year)


# ---------------------------------------------------------------------------- month view
def _month(rng, *, theme, scale, abstain):
    inv = Inventor(rng)
    w, h = LOGICAL_SIZES["month"]
    c = Canvas(w, h, scale=scale, theme=theme)
    month = rng.randint(1, 12)
    year = 2027
    ndays = _cal.monthrange(year, month)[1]
    offset = date(year, month, 1).weekday()
    title = f"{MONTHS[month - 1]} {year}"
    x0, cw, top, ch = 28, 126, 108, 110
    c.text(28, 24, title, size=28, bold=True, field="month_title")
    for i, name in enumerate(WEEKDAYS):
        c.text(x0 + i * cw + 8, 80, name[:3], size=13, color="muted")
    n = rng.randint(6, 9)
    days = sorted(rng.sample(range(1, ndays + 1), n))
    titles = _titles(rng, c, inv, n, 11, cw - 40)
    starts = [rng.choice(range(8 * 60, 19 * 60, 30)) for _ in range(n)]
    ev_at = dict(zip(days, range(n)))
    for dnum in range(1, ndays + 1):
        pos = offset + dnum - 1
        x, y = x0 + (pos % 7) * cw, top + (pos // 7) * ch
        c.rect(x, y, x + cw, y + ch, outline="grid")
        c.text(x + 8, y + 6, str(dnum), size=13, field=f"day_{dnum}", kind="number",
               value=str(dnum))
        if dnum in ev_at:
            i = ev_at[dnum]
            c.rect(x + 6, y + 28, x + cw - 6, y + 68, fill="surface", radius=4)
            c.rect(x + 6, y + 28, x + 9, y + 68, fill="accent")
            c.text(x + 14, y + 32, titles[i], size=11, field=f"title_{i}")
            c.text(x + 14, y + 49, time_12h(_hm(starts[i])), size=11, color="muted",
                   field=f"start_{i}", kind="time", value=_hm(starts[i]))
    ia, ib, ic = rng.sample(range(n), 3)
    iso = lambda i: date(year, month, days[i]).isoformat()
    qs = [
        Question("q_date", "lookup", f'On what date is the event "{titles[ia]}"? Give the full '
                 "date.", iso(ia), canon="date",
                 fields=[f"title_{ia}", f"day_{days[ia]}", "month_title"]),
        Question("q_on", "event", f"What event is on {MONTHS[month - 1]} {days[ib]}?",
                 titles[ib], fields=[f"day_{days[ib]}", f"title_{ib}", "month_title"]),
        Question("q_count", "count", "How many events are shown in this month?", str(n),
                 canon="number", fields=[f"title_{i}" for i in range(n)]),
    ]
    _tail(c, rng, abstain, qs,
          Question("q_start", "lookup", f'At what time does "{titles[ic]}" start?',
                   _hm(starts[ic]), canon="time", fields=[f"title_{ic}", f"start_{ic}"]),
          f"start_{ic}", ("q_loc", f'Where is the event "{titles[ic]}" held?', "location",
                          inv.merchant(), "text"))
    qs.append(Question("q_reading", "reading", "Read the screen.", "", kind="reading"))
    data = {"month": title, "events": [{"date": iso(i), "start": _hm(starts[i]),
                                        "title": titles[i]} for i in range(n)]}
    return Rendered(c, "month", qs, data, default_year=year)
