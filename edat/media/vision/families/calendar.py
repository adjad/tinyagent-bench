"""Calendar: day, week, month, clash, agenda and timeline views drawn from an invented event list.

Planted fields: weekday names, dates, event titles, start and end times, locations, month title.
Question types: event (what is at a time), lookup (start time, location, date), count,
free_slot (the one free hour, the one empty day), compare (which events clash), absence (and
the reading).
Clash views (clash_day, clash_week, agenda, timeline) hold exactly one pair of overlapping events
on the asked day: "which two events clash on Thursday?" is answered "A and B" ("B and A" is
accepted too), "which event clashes with A?" by the other title. Every view has a toolbar whose
buttons are recorded as elements: Day, Week, Month (and Agenda), Today, Previous and Next.

Seven templates (a count coprime with 10): every template is reachable in each theme, scale and
abstention slot.
"""
from __future__ import annotations

import calendar as _cal
import random
from datetime import date, timedelta

from edat.media.common import EVENT_WORDS, MONTHS, WEEKDAYS, Inventor, date_long, time_12h
from edat.media.vision.canvas import Canvas
from edat.media.vision.items import ABSTAIN_ANSWER, Question, Rendered

FAMILY = "calendar"
TEMPLATES = ("day", "week", "month", "clash_day", "clash_week", "agenda", "timeline")
LOGICAL_SIZES = {"day": (520, 800), "week": (1120, 640), "month": (940, 800),
                 "clash_day": (520, 800), "clash_week": (1120, 700), "agenda": (520, 780),
                 "timeline": (1120, 270)}


def render(rng: random.Random, *, template: str, theme: str, scale: float,
           abstain: bool) -> Rendered:
    fn = {"day": _day, "week": _week, "month": _month, "clash_day": _clash_day,
          "clash_week": _clash_week, "agenda": _agenda, "timeline": _timeline}.get(template)
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


def _toolbar(c: Canvas, xr: float, y: float, active: str | None,
             modes=("Day", "Week", "Month")) -> None:
    """View switch, Today and the previous/next arrows, each recorded as an element."""
    x = xr
    for m in reversed(modes):
        wd = max(58.0, c.text_width(m, 13) + 24)
        on = m == active
        c.rect(x - wd, y, x, y + 32, fill="accent" if on else None, outline=None if on else "border",
               radius=8, width=1.5)
        c.text_center(x - wd / 2, y + 16, m, size=13, color="accent_fg" if on else "fg", anchor="lm")
        c.element(m, x - wd, y, x, y + 32, role="button")
        x -= wd + 6
    x -= 8
    for name, sign in (("Next", 1), ("Previous", -1)):
        c.rect(x - 34, y, x, y + 32, outline="border", radius=8, width=1.5)
        cx = x - 17
        c.polyline([(cx - 3 * sign, y + 9), (cx + 4 * sign, y + 16), (cx - 3 * sign, y + 23)],
                   color="fg", width=2)
        c.element(name, x - 34, y, x, y + 32, role="button")
        x -= 40
    x -= 4
    c.rect(x - 70, y, x, y + 32, outline="accent", radius=8, width=1.5)
    c.text_center(x - 35, y + 16, "Today", size=13, color="accent", anchor="lm")
    c.element("Today", x - 70, y, x, y + 32, role="button")


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
    c.text(24, 14, date_long(day), size=22, bold=True, field="day_date", kind="date",
           value=day.isoformat())
    _toolbar(c, w - 24, 54, "Day")
    top, hh = 124, 64
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
    _toolbar(c, w - 24, 14, "Week")
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
    _toolbar(c, w - 28, 24, "Month")
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


# ---------------------------------------------------------------------------- clash helpers
def _overlap(a, b) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _with_clash(rng, lo, hi, n, breaks=(30, 60, 60, 90), min_total=3):
    """Events (start, end) sorted by start with exactly one overlapping pair; returns (events, (i, j))."""
    for _ in range(1000):
        ev = _sched(rng, lo, hi, n, breaks=breaks)
        if len(ev) < 2:
            continue
        cands = [k for k, (s, e) in enumerate(ev)
                 if (ev[k + 1][0] if k + 1 < len(ev) else hi) - e >= 30]
        if not cands:
            continue
        k = rng.choice(cands)
        s, e = ev[k]
        nxt = ev[k + 1][0] if k + 1 < len(ev) else hi
        s2 = e - 30
        e2 = s2 + rng.choice((60, 90))
        if e2 > nxt:
            e2 = s2 + 60
        new = sorted(ev + [(s2, e2)])
        if len(new) < min_total:
            continue
        a, b = new.index(ev[k]), new.index((s2, e2))
        pairs = [(i, j) for i in range(len(new)) for j in range(i + 1, len(new))
                 if _overlap(new[i], new[j])]
        if pairs == [tuple(sorted((a, b)))]:
            return new, tuple(sorted((a, b)))
    raise RuntimeError("could not build a schedule with one clash")


def _clash_questions(prefix, wd, titles, ev, pair, fields_of, *, day_fields=()):
    """'Which two events clash on <day>?' and 'Which event clashes with X?'."""
    a, b = pair
    both = [f for i in (a, b) for f in fields_of(i)] + list(day_fields)
    q_pair = Question(f"{prefix}_pair", "compare", f"Two events on {wd} overlap in time. Which "
                      "two events clash? Answer in the form 'A and B'.",
                      f"{titles[a]} and {titles[b]}", fields=both,
                      accept=[f"{titles[b]} and {titles[a]}"])
    x, other = (a, b) if rng_choice(prefix, a, b) else (b, a)
    q_with = Question(f"{prefix}_with", "compare", f'On {wd}, which event clashes with '
                      f'"{titles[x]}"?', titles[other], fields=both)
    return q_pair, q_with


def rng_choice(prefix, a, b) -> bool:      # a deterministic coin from the pair itself (no RNG use)
    return (a + 2 * b + len(prefix)) % 2 == 0


def _event_box(c, x0, x1, y0, y1, title, s, e, loc, i, *, size=13, lines=2, wide=True):
    c.rect(x0, y0, x1, y1, fill="surface", radius=6)
    c.rect(x0, y0, x0 + 4, y1, fill="accent")
    c.text(x0 + 16, y0 + 4, title, size=size, field=f"title_{i}")
    if wide:
        x = x0 + 16
        ts = time_12h(_hm(s))
        c.text(x, y0 + 23, ts, size=11, field=f"start_{i}", kind="time", value=_hm(s))
        x += c.text_width(ts, 11) + 6
        c.text(x, y0 + 23, "-", size=11, color="muted")
        c.text(x + 10, y0 + 23, time_12h(_hm(e)), size=11, field=f"end_{i}", kind="time",
               value=_hm(e))
        if loc is not None:
            c.text(x0 + 16, y0 + 41, loc, size=11, color="muted", field=f"loc_{i}")
    else:
        c.text(x0 + 16, y0 + 19, time_12h(_hm(s)), size=11, field=f"start_{i}", kind="time",
               value=_hm(s))
        c.text(x0 + 16, y0 + 33, "to " + time_12h(_hm(e)), size=11, color="muted",
               field=f"end_{i}", kind="time", value=_hm(e))


def _inside_hour(rng, ev, pair, hr_lo=8, hr_hi=18):
    """(event index, hour) with the hour inside exactly one event, never a clashing one."""
    options = [(i, x) for i, (s, e) in enumerate(ev) if i not in pair
               for x in range(hr_lo, hr_hi) if s <= x * 60 < e]
    return rng.choice(options)


# ---------------------------------------------------------------------------- clash day view
def _clash_day(rng, *, theme, scale, abstain):
    inv = Inventor(rng)
    w, h = LOGICAL_SIZES["clash_day"]
    c = Canvas(w, h, scale=scale, theme=theme)
    monday = date(2027, 1, 4) + timedelta(days=7 * rng.randint(0, 45))
    day = monday + timedelta(days=rng.randint(0, 4))
    ev, pair = _with_clash(rng, 9 * 60, 17 * 60, rng.randint(3, 4), min_total=4)
    n = len(ev)
    titles = _titles(rng, c, inv, n, 13, 168)
    locs = [_place(rng, inv) for _ in ev]
    c.text(24, 14, date_long(day), size=22, bold=True, field="day_date", kind="date",
           value=day.isoformat())
    _toolbar(c, w - 24, 54, "Day")
    top, hh = 124, 64
    for hr in range(8, 19):
        y = top + (hr - 8) * hh
        c.line(70, y, w - 16, y, color="grid")
        c.text(16, y - 7, time_12h(_hm(hr * 60)).replace(":00", ""), size=11, color="muted",
               field=f"hr_{hr}", kind="time", value=_hm(hr * 60))
    for i, (s_, e_) in enumerate(ev):
        y0 = top + (s_ - 480) * hh / 60 + 2
        y1 = top + (e_ - 480) * hh / 60 - 2
        if i in pair:
            x0 = 76 + pair.index(i) * 212
            x1 = x0 + 208
        else:
            x0, x1 = 76, w - 20
        _event_box(c, x0, x1, y0, y1, titles[i], s_, e_, locs[i], i)
    wd = WEEKDAYS[day.weekday()]
    fields_of = lambda i: [f"title_{i}", f"start_{i}", f"end_{i}"]  # noqa: E731
    q_pair, q_with = _clash_questions("q_clash", wd, titles, ev, pair, fields_of,
                                      day_fields=["day_date"])
    ia, hr = _inside_hour(rng, ev, pair)
    g = rng.choice([i for i in range(n) if i != ia])
    qs = [
        q_pair, q_with,
        Question("q_at", "event", f"What event is scheduled at {time_12h(_hm(hr * 60))} on {wd}?",
                 titles[ia], fields=[f"title_{ia}", f"start_{ia}", f"end_{ia}", f"hr_{hr}"]),
    ]
    _tail(c, rng, abstain, qs,
          Question("q_loc", "lookup", f'Where is the event "{titles[g]}" held?', locs[g],
                   fields=[f"title_{g}", f"loc_{g}"]),
          f"loc_{g}", ("q_host", f'Who is hosting the event "{titles[g]}"?', "host",
                       inv.person(), "text"))
    qs.append(Question("q_reading", "reading", "Read the screen.", "", kind="reading"))
    data = {"date": day.isoformat(), "clash": [titles[pair[0]], titles[pair[1]]], "events": [
        {"title": titles[i], "start": _hm(ev[i][0]), "end": _hm(ev[i][1]), "location": locs[i]}
        for i in range(n)]}
    return Rendered(c, "clash_day", qs, data, default_year=day.year)


# ---------------------------------------------------------------------------- clash week view
def _clash_week(rng, *, theme, scale, abstain):
    inv = Inventor(rng)
    w, h = LOGICAL_SIZES["clash_week"]
    c = Canvas(w, h, scale=scale, theme=theme)
    monday = date(2027, 1, 4) + timedelta(days=7 * rng.randint(0, 45))
    days = [monday + timedelta(days=i) for i in range(5)]
    x0, cw, top, hh = 72, 200, 112, 56
    label = (f"{days[0].strftime('%b')} {days[0].day} - {days[4].strftime('%b')} {days[4].day}, "
             f"{days[4].year}")
    c.text(72, 20, label, size=22, bold=True)
    _toolbar(c, w - 24, 14, "Week")
    cd = rng.randrange(5)                         # the day holding the clash
    events = []                                   # (day, start, end, title, col, ncols)
    used: set[str] = set()
    for d in range(5):
        if d == cd:
            ev, pair = _with_clash(rng, 8 * 60, 18 * 60, 2, breaks=(30, 60, 90))
            tl = _titles(rng, c, inv, len(ev), 12, 80, used)
            for i, ((s_, e_), t) in enumerate(zip(ev, tl)):
                events.append((d, s_, e_, t, pair.index(i) if i in pair else 0,
                               2 if i in pair else 1))
        else:
            sched = _sched(rng, 8 * 60, 18 * 60, rng.randint(1, 3), breaks=(30, 60, 90, 120))
            tl = _titles(rng, c, inv, len(sched), 12, cw - 36, used)
            events += [(d, s_, e_, t, 0, 1) for (s_, e_), t in zip(sched, tl)]
    for d in range(5):
        x = x0 + d * cw
        c.text(x + 8, 52, WEEKDAYS[d], size=13, field=f"wd_{d}")
        c.text(x + 8, 72, f"{days[d].strftime('%b')} {days[d].day}", size=12, color="muted",
               field=f"dt_{d}", kind="date", value=days[d].isoformat())
    for hr in range(8, 19):
        y = top + (hr - 8) * hh
        c.line(x0 - 6, y, w - 14, y, color="grid")
        if hr < 18:
            c.text(12, y + 2, time_12h(_hm(hr * 60)).replace(":00", ""), size=11, color="muted",
                   field=f"hr_{hr}", kind="time", value=_hm(hr * 60))
    for d in range(6):
        c.line(x0 + d * cw - 6, top - 8, x0 + d * cw - 6, top + 10 * hh, color="grid")
    for i, (d, s_, e_, t, col, ncol) in enumerate(events):
        xa = x0 + d * cw + col * (cw - 12) / ncol
        xb = xa + (cw - 12) / ncol - 2
        y0 = top + (s_ - 480) * hh / 60 + 2
        y1 = top + (e_ - 480) * hh / 60 - 2
        _event_box(c, xa, xb, y0, y1, t, s_, e_, None, i, size=12, wide=False)
    n = len(events)
    in_clash = [i for i, ev in enumerate(events) if ev[0] == cd]
    pair = [i for i in in_clash if events[i][5] == 2]
    titles = [ev[3] for ev in events]
    wd = WEEKDAYS[cd]
    ev_only = [(ev[1], ev[2]) for ev in events]
    fields_of = lambda i: [f"title_{i}", f"start_{i}", f"end_{i}"]  # noqa: E731
    q_pair, q_with = _clash_questions("q_clash", wd, titles, ev_only, tuple(pair), fields_of,
                                      day_fields=[f"wd_{cd}"])
    others = [i for i in range(n) if i not in pair]
    ia = rng.choice(others)
    d_a, s_a, e_a, t_a, *_ = events[ia]
    hr = rng.choice([x for x in range(8, 18) if s_a <= x * 60 < e_a])
    f = rng.choice([i for i in others if i != ia])
    qs = [
        q_pair, q_with,
        Question("q_at", "event", f"What event is scheduled on {WEEKDAYS[d_a]} at "
                 f"{time_12h(_hm(hr * 60))}?", t_a,
                 fields=[f"title_{ia}", f"wd_{d_a}", f"hr_{hr}", f"start_{ia}", f"end_{ia}"]),
    ]
    tf = events[f][3]
    _tail(c, rng, abstain, qs,
          Question("q_start", "lookup", f'At what time does "{tf}" start?', _hm(events[f][1]),
                   canon="time", fields=[f"title_{f}", f"start_{f}"]),
          f"start_{f}", ("q_loc", f'Where is the event "{tf}" held?', "location",
                         inv.merchant(), "text"))
    qs.append(Question("q_reading", "reading", "Read the screen.", "", kind="reading"))
    data = {"week_start": monday.isoformat(), "clash_day": wd,
            "clash": [titles[pair[0]], titles[pair[1]]], "events": [
                {"day": days[d].isoformat(), "start": _hm(s_), "end": _hm(e_), "title": t}
                for d, s_, e_, t, *_ in events]}
    return Rendered(c, "clash_week", qs, data, default_year=monday.year)


# ---------------------------------------------------------------------------- agenda list
def _agenda(rng, *, theme, scale, abstain):
    inv = Inventor(rng)
    w, h = LOGICAL_SIZES["agenda"]
    c = Canvas(w, h, scale=scale, theme=theme)
    monday = date(2027, 1, 4) + timedelta(days=7 * rng.randint(0, 45))
    first = rng.randint(0, 2)
    days = [monday + timedelta(days=first + k) for k in range(3)]
    cd = rng.randrange(3)
    c.text(24, 14, f"{days[0].strftime('%b')} {days[0].day} - {days[2].strftime('%b')} "
           f"{days[2].day}, {days[2].year}", size=22, bold=True)
    _toolbar(c, w - 24, 54, "Agenda", modes=("Day", "Week", "Month", "Agenda"))
    events = []                                   # (day k, start, end, title, loc)
    used: set[str] = set()
    pair = None
    for k in range(3):
        if k == cd:
            ev, pr = _with_clash(rng, 8 * 60, 18 * 60, 2, breaks=(30, 60, 90))
        else:
            ev, pr = _sched(rng, 8 * 60, 18 * 60, rng.randint(2, 3), breaks=(30, 60, 90)), None
        tl = _titles(rng, c, inv, len(ev), 14, 330, used)
        base = len(events)
        for (s_, e_), t in zip(ev, tl):
            events.append((k, s_, e_, t, _place(rng, inv)))
        if pr is not None:
            pair = (base + pr[0], base + pr[1])
    y = 108
    for k in range(3):
        c.text(24, y, date_long(days[k]), size=15, bold=True, color="accent", field=f"dh_{k}",
               kind="date", value=days[k].isoformat())
        y += 34
        c.line(24, y - 4, w - 24, y - 4, color="border")
        for i, (dk, s_, e_, t, loc) in enumerate(events):
            if dk != k:
                continue
            c.text(24, y + 6, time_12h(_hm(s_)), size=13, field=f"start_{i}", kind="time",
                   value=_hm(s_))
            c.text(24, y + 26, time_12h(_hm(e_)), size=12, color="muted", field=f"end_{i}",
                   kind="time", value=_hm(e_))
            c.rect(112, y + 4, 116, y + 46, fill="accent")
            c.text(130, y + 6, t, size=14, field=f"title_{i}")
            c.text(130, y + 28, loc, size=12, color="muted", field=f"loc_{i}")
            c.line(24, y + 54, w - 24, y + 54, color="grid")
            y += 56
        y += 10
    n = len(events)
    titles = [ev[3] for ev in events]
    wd = WEEKDAYS[days[cd].weekday()]
    fields_of = lambda i: [f"title_{i}", f"start_{i}", f"end_{i}"]  # noqa: E731
    q_pair, q_with = _clash_questions("q_clash", wd, titles, [(e[1], e[2]) for e in events], pair,
                                      fields_of, day_fields=[f"dh_{cd}"])
    kc = rng.randrange(3)
    in_day = [i for i, e in enumerate(events) if e[0] == kc]
    others = [i for i in range(n) if i not in pair]
    g = rng.choice(others)
    qs = [
        q_pair, q_with,
        Question("q_count", "count", f"How many events are listed on {WEEKDAYS[days[kc].weekday()]}?",
                 str(len(in_day)), canon="number", fields=[f"dh_{kc}"] + [f"title_{i}" for i in range(n)]),
    ]
    _tail(c, rng, abstain, qs,
          Question("q_loc", "lookup", f'Where is the event "{titles[g]}" held?', events[g][4],
                   fields=[f"title_{g}", f"loc_{g}"]),
          f"loc_{g}", ("q_host", f'Who is hosting the event "{titles[g]}"?', "host",
                       inv.person(), "text"))
    qs.append(Question("q_reading", "reading", "Read the screen.", "", kind="reading"))
    data = {"clash_day": wd, "clash": [titles[pair[0]], titles[pair[1]]], "events": [
        {"day": days[e[0]].isoformat(), "start": _hm(e[1]), "end": _hm(e[2]), "title": e[3],
         "location": e[4]} for e in events]}
    return Rendered(c, "agenda", qs, data, default_year=monday.year)


# ---------------------------------------------------------------------------- timeline
def _timeline(rng, *, theme, scale, abstain):
    inv = Inventor(rng)
    w, h = LOGICAL_SIZES["timeline"]
    c = Canvas(w, h, scale=scale, theme=theme)
    monday = date(2027, 1, 4) + timedelta(days=7 * rng.randint(0, 45))
    day = monday + timedelta(days=rng.randint(0, 4))
    ev, pair = _with_clash(rng, 9 * 60, 17 * 60, rng.randint(3, 4), min_total=4)
    n = len(ev)
    titles = _titles(rng, c, inv, n, 12, 84)
    c.text(24, 14, date_long(day), size=22, bold=True, field="day_date", kind="date",
           value=day.isoformat())
    _toolbar(c, w - 24, 14, "Day")
    x0, hp, top = 56, 102, 92
    for hr in range(8, 19):
        x = x0 + (hr - 8) * hp
        c.line(x, top - 8, x, 252, color="grid")
        if hr < 18:
            c.text(x + 4, 66, time_12h(_hm(hr * 60)).replace(":00", ""), size=11, color="muted",
                   field=f"hr_{hr}", kind="time", value=_hm(hr * 60))
    lanes = {pair[0]: rng.randrange(2)}
    lanes[pair[1]] = 1 - lanes[pair[0]]
    for i in range(n):
        lanes.setdefault(i, rng.randrange(2))
    for i, (s_, e_) in enumerate(ev):
        xa = x0 + (s_ - 480) * hp / 60 + 2
        xb = x0 + (e_ - 480) * hp / 60 - 2
        y0 = top + lanes[i] * 80
        _event_box(c, xa, xb, y0, y0 + 70, titles[i], s_, e_, None, i, size=12, wide=False)
    wd = WEEKDAYS[day.weekday()]
    fields_of = lambda i: [f"title_{i}", f"start_{i}", f"end_{i}"]  # noqa: E731
    q_pair, q_with = _clash_questions("q_clash", wd, titles, ev, pair, fields_of,
                                      day_fields=["day_date"])
    ia, hr = _inside_hour(rng, ev, pair)
    g = rng.choice([i for i in range(n) if i != ia and i not in pair])
    qs = [
        q_pair, q_with,
        Question("q_at", "event", f"What event is scheduled at {time_12h(_hm(hr * 60))} on {wd}?",
                 titles[ia], fields=[f"title_{ia}", f"start_{ia}", f"end_{ia}", f"hr_{hr}"]),
    ]
    _tail(c, rng, abstain, qs,
          Question("q_start", "lookup", f'At what time does "{titles[g]}" start?', _hm(ev[g][0]),
                   canon="time", fields=[f"title_{g}", f"start_{g}"]),
          f"start_{g}", ("q_loc", f'Where is the event "{titles[g]}" held?', "location",
                         inv.merchant(), "text"))
    qs.append(Question("q_reading", "reading", "Read the screen.", "", kind="reading"))
    data = {"date": day.isoformat(), "clash": [titles[pair[0]], titles[pair[1]]], "events": [
        {"title": titles[i], "start": _hm(ev[i][0]), "end": _hm(ev[i][1]), "lane": lanes[i]}
        for i in range(n)]}
    return Rendered(c, "timeline", qs, data, default_year=day.year)
