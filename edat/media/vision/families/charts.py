"""Charts and plots drawn from invented data (spec 05 §7.1, and the V7 trend questions of §7.7).

Eleven templates: bar, hbar (horizontal bar), grouped_bar, stacked_bar, line, area, pie, scatter,
histogram, box_plot and dual_axis. Values printed on the image (bar data labels, pie percentages)
are planted text fields scored by exact match. Values read off the plot with no printed label are
recorded with ``Canvas.mark`` and scored relaxed (within 5 %); they sit on a gridline or
half-way between two, on an axis with ticks 10, 20 or 50 apart (the dual axis' second scale uses
other steps), and are never below one tick step, so the 5 % rule is fair. Differences and
growth the question asks for are at least one or two tick steps for the same reason.
Question types: lookup, max, min, difference, trend, compare, count, absence, reading.

Naming convention for category charts (line, area, grouped_bar, bar, hbar, dual_axis): the
x labels are the fields ``x_<i>``, the legend entries ``leg_<s>``, the plotted mark of series
``s`` at x ``i`` is ``pt_<s>_<i>``; ``Rendered.data`` holds ``x`` (the labels), ``series``
(name -> the full list of values), ``word`` and ``kind``.

V7 chart-trend questions (500 of them) are generated from an item's ``data`` alone: call
:func:`trend_questions` with a rendered item (it needs ``data`` and the canvas's field names; a
saved item JSON holds both as ``data`` and ``fields``). It returns up to three questions: the
trend between the first and last point (rise, fall or flat), the crossing point of two series
("in which month does A first exceed B?") and the growth between the first and last point.
"""
from __future__ import annotations

import math
import random

from edat.media.common import Inventor, MONTHS, canon_text
from edat.media.vision.canvas import Canvas
from edat.media.vision.items import ABSTAIN_ANSWER, Question, Rendered

FAMILY = "charts"
TEMPLATES = ("bar", "hbar", "grouped_bar", "stacked_bar", "line", "area", "pie", "scatter",
             "histogram", "box_plot", "dual_axis")
LOGICAL_SIZES = {t: (640, 480) for t in TEMPLATES}
LOGICAL_SIZES["scatter"] = (640, 520)
LOGICAL_SIZES["dual_axis"] = (680, 480)
YEAR = 2027
SERIES_COLORS = {   # every colour is at least 5:1 against its theme's background
    "light": ((0, 92, 197), (192, 28, 40), (22, 120, 60), (153, 92, 0), (118, 56, 170),
              (0, 112, 122)),
    "dark": ((100, 170, 255), (255, 105, 97), (90, 210, 120), (255, 190, 80), (190, 150, 255),
             (80, 200, 210)),
}
PLOT = (72, 100, 616, 420)          # x0, y0, x1, y1 of the plot area (logical px)
SUBJECTS = ("Units sold", "Visitors", "Orders", "Sign-ups", "Deliveries", "Repairs")
CHANNELS = ("Online", "In store", "Wholesale", "Phone", "Kiosk", "Mail order")
AXES = (("Hours per week", "Score"), ("Price", "Rating"), ("Distance", "Minutes"),
        ("Batch size", "Defects"), ("Rooms", "Bookings"))


def render(rng: random.Random, *, template: str, theme: str, scale: float,
           abstain: bool) -> Rendered:
    fn = {"bar": _bar, "hbar": _hbar, "grouped_bar": _grouped, "stacked_bar": _stacked,
          "line": _line, "area": _area, "pie": _pie, "scatter": _scatter,
          "histogram": _histogram, "box_plot": _box, "dual_axis": _dual}.get(template)
    if fn is None:
        raise ValueError(f"unknown template {template!r}")
    return fn(rng, theme=theme, scale=scale, abstain=abstain)


# ------------------------------------------------------------------ helpers

def _unique(make, n: int) -> list[str]:
    out: list[str] = []
    while len(out) < n:
        s = make()
        if all(s.lower() not in o.lower() and o.lower() not in s.lower() for o in out):
            out.append(s)
    return out


def _unique_extreme(vals: list[float], margin: float) -> bool:
    s = sorted(vals)
    return len(s) < 2 or (s[-1] - s[-2] >= margin and s[1] - s[0] >= margin)


def _abstain(c: Canvas, inv: Inventor, qid: str, missing: str, question: str) -> Question:
    """An abstention question on a value that is not on the image.

    ``question`` names ``missing`` (a category, series or row that is not drawn); if
    that name happens to occur in drawn text, ask for an uncredited data source instead.
    """
    drawn = [canon_text(r.text) for r in c.runs]
    if not any(canon_text(missing) in t for t in drawn):
        c.absent("missing", missing)
        return Question(qid, "absence", question, ABSTAIN_ANSWER, kind="abstain",
                        fields=["missing"])
    while True:
        source = inv.merchant()
        if not any(canon_text(source) in t for t in drawn):
            break
    c.absent("source", source)
    return Question(qid, "absence", "Which organisation is credited as the source of this data?",
                    ABSTAIN_ANSWER, kind="abstain", fields=["source"])


def _missing_cat(inv: Inventor, cats: list[str], word: str) -> str:
    if word == "quarter":
        return "Q4" if "Q4" not in cats else "Q5"
    if word == "month":
        rest = [m[:3] for m in MONTHS if m[:3] not in cats]
        return inv.r.choice(rest)
    while True:
        s = inv.brand()
        if s not in cats:
            return s


def _grid(c: Canvas, step: int, nt: int, plot=PLOT):
    x0, y0, x1, y1 = plot

    def y_of(v: float) -> float:
        return y1 - (y1 - y0) * v / (step * nt)
    for k in range(1, nt + 1):
        c.line(x0, y_of(k * step), x1, y_of(k * step), color="grid")
    c.line(x0, y1, x1, y1, color="muted")
    return y_of


def _y_ticks(c: Canvas, step: int, nt: int, y_of, plot=PLOT) -> None:
    for k in range(nt, -1, -1):
        c.text(plot[0] - 8, y_of(k * step), str(k * step), size=11, color="muted", anchor="rm")


def _legend(c: Canvas, names: list[str], colors, y: float, *, line: bool = False,
            x: float = PLOT[0]) -> None:
    for i, (name, col) in enumerate(zip(names, colors)):
        if line:
            c.line(x, y + 7, x + 18, y + 7, color=col, width=2)
        else:
            c.rect(x + 2, y + 1, x + 15, y + 14, fill=col)
        c.text(x + 24, y, name, size=12, field=f"leg_{i}", value=name)
        x += 24 + c.text_width(name, 12) + 22


def _heads(c: Canvas, title: str, subtitle: str) -> None:
    c.text(24, 18, title, size=18, bold=True)
    c.text(24, 44, subtitle, size=11, color="muted")


def _tint(c: Canvas, color, alpha: float) -> tuple[int, int, int]:
    fg, bg = c.color(color), c.pal["bg"]
    return tuple(round(alpha * f + (1 - alpha) * b) for f, b in zip(fg, bg))  # type: ignore[return-value]


def _cats(rng: random.Random, inv: Inventor, n: int) -> tuple[list[str], str, list[list[str]]]:
    """Category labels, the word for them, and accepted alternative answers."""
    if n <= 4 and rng.random() < 0.5:
        return [f"Q{i + 1}" for i in range(n)], "quarter", [[] for _ in range(n)]
    if rng.random() < 0.5:
        s = rng.randrange(0, 13 - n)
        return [MONTHS[i][:3] for i in range(s, s + n)], "month", \
            [[MONTHS[i]] for i in range(s, s + n)]
    return _unique(inv.brand, n), "store", [[] for _ in range(n)]


def _time_cats(rng: random.Random, n: int) -> tuple[list[str], str, list[list[str]]]:
    if n == 4:
        return [f"Q{i + 1}" for i in range(4)], "quarter", [[] for _ in range(4)]
    s = rng.choice((0, 3, 6))
    return [MONTHS[i][:3] for i in range(s, s + n)], "month", [[MONTHS[i]] for i in range(s, s + n)]


def _halves(rng: random.Random, half: int, lo: int, hi: int) -> int:
    return half * rng.randint(lo, hi)


def _x_labels(c: Canvas, xs: list[str], px: list[float], y: float) -> None:
    for i, x in enumerate(xs):
        c.text(px[i], y, x, size=12, anchor="ma", field=f"x_{i}", value=x)


def _accept_for(x: str) -> list[str]:
    return [m for m in MONTHS if m[:3] == x and len(x) == 3 and m != x]


# ------------------------------------------------------------------ question builders (category charts)

def _q_value(qid: str, name: str, xs, series, si: int, i: int, *, many: bool, word: str,
             noun: str = "chart") -> Question:
    who = f" of {name}" if many else ""
    return Question(qid, "lookup", f"Reading the {noun}, what is the value{who} in {xs[i]}?",
                    str(series[si][i]), kind="relaxed", canon="number",
                    fields=([f"leg_{si}"] if many else []) + [f"x_{i}", f"pt_{si}_{i}"])


def _trend_answer(first: float, last: float) -> tuple[str, list[str]]:
    if last > first:
        return "rise", ["rose", "risen", "increase", "increased", "up", "grew"]
    if last < first:
        return "fall", ["fell", "fallen", "decrease", "decreased", "down", "dropped"]
    return "flat", ["flat", "stayed flat", "unchanged", "no change", "stayed the same", "stay flat"]


def _q_trend(qid: str, name: str, xs, series, si: int, *, many: bool) -> Question:
    n = len(xs)
    ans, acc = _trend_answer(series[si][0], series[si][-1])
    return Question(qid, "trend", f"Between {xs[0]} and {xs[-1]}, did {name} rise, fall or stay "
                    "flat? Answer rise, fall or flat.", ans,
                    fields=([f"leg_{si}"] if many else []) + ["x_0", f"x_{n - 1}", f"pt_{si}_0",
                                                               f"pt_{si}_{n - 1}"], accept=acc)


def _q_cross(qid: str, names, xs, series, word: str) -> Question | None:
    a, b = series[0], series[1]
    if not a[0] < b[0]:
        return None
    k = next((i for i in range(len(xs)) if a[i] > b[i]), None)
    if k is None:
        return None
    n = len(xs)
    return Question(qid, "compare", f"In which {word} does {names[0]} first exceed {names[1]}?",
                    xs[k], fields=["leg_0", "leg_1"] + [f"x_{i}" for i in range(n)] +
                    [f"pt_0_{i}" for i in range(n)] + [f"pt_1_{i}" for i in range(n)],
                    accept=_accept_for(xs[k]))


def _q_rate(qid: str, name: str, xs, series, si: int, *, many: bool, rng: random.Random) -> Question | None:
    n = len(xs)
    first, last = series[si][0], series[si][-1]
    if first == last:
        return None
    grew = last > first
    verb = "grow" if grew else "fall"
    d = abs(last - first)
    fields = ([f"leg_{si}"] if many else []) + ["x_0", f"x_{n - 1}", f"pt_{si}_0", f"pt_{si}_{n - 1}"]
    if first and (d * 100) % first == 0 and rng.random() < 0.4:
        return Question(qid, "difference", f"By what percentage did {name} {verb} from {xs[0]} "
                        f"to {xs[-1]}? Give the number of per cent.", str(d * 100 // first),
                        kind="relaxed", canon="number", fields=fields)
    return Question(qid, "difference", f"By how much did {name} {verb} from {xs[0]} to "
                    f"{xs[-1]}? Give the size of the change.", str(d), kind="relaxed",
                    canon="number", fields=fields)


def trend_questions(rng: random.Random, rendered, *, prefix: str = "v7") -> list[Question]:
    """V7 chart questions (trend, crossing point, growth) built from an item's ``data`` alone.

    ``rendered`` is a :class:`Rendered` or a saved item JSON (a dict with ``data`` and
    ``fields``). Only charts whose data hold ``x`` and ``series`` give questions, and a
    question is returned only when every field it names exists on the image.
    """
    data = rendered["data"] if isinstance(rendered, dict) else rendered.data
    fields = rendered["fields"] if isinstance(rendered, dict) else rendered.canvas.fields
    xs, series_map = data.get("x"), data.get("series")
    if not xs or not series_map or data.get("kind") in ("stacked_bar", "scatter", "pie"):
        return []
    names = list(series_map)
    series = [series_map[k] for k in names]
    many = len(names) > 1 or data.get("legend", False)
    word = data.get("word", "period")
    si = rng.randrange(len(names))
    n = len(xs)
    step = data.get("tick_step", 1)
    out: list[Question | None] = []
    if data.get("dual_axis") and si == 1:
        step = data.get("right_tick_step", step)
    first, last = series[si][0], series[si][-1]
    if first == last or abs(last - first) >= step:
        out.append(_q_trend(f"{prefix}_trend", names[si], xs, series, si, many=many))
    if len(names) >= 2 and not data.get("dual_axis"):
        out.append(_q_cross(f"{prefix}_cross", names, xs, series, word))
    if abs(last - first) >= 2 * step:
        out.append(_q_rate(f"{prefix}_rate", names[si], xs, series, si, many=many, rng=rng))
    return [q for q in out if q is not None and all(f in fields for f in q.fields)]


# ------------------------------------------------------------------ bar chart

def _bar(rng: random.Random, *, theme: str, scale: float, abstain: bool) -> Rendered:
    inv = Inventor(rng)
    n = rng.randint(4, 7)
    cats, word, accept = _cats(rng, inv, n)
    subject = rng.choice(SUBJECTS)
    step, nt = rng.choice((10, 20, 50)), 5
    half, ymax = step // 2, step * nt
    labelled = rng.random() < 0.5
    while True:
        if labelled:
            vals = [rng.randint(step, ymax - half) for _ in cats]
        else:
            vals = [_halves(rng, half, 2, 2 * nt) for _ in cats]
        if _unique_extreme(vals, step if not labelled else half):
            break
    color = rng.choice(SERIES_COLORS[theme])
    w, h = LOGICAL_SIZES["bar"]
    c = Canvas(w, h, scale=scale, theme=theme)
    y_of = _grid(c, step, nt)
    slot = (PLOT[2] - PLOT[0]) / n
    boxes = []
    for i, v in enumerate(vals):
        cx = PLOT[0] + slot * (i + 0.5)
        bw = slot * 0.56
        box = (cx - bw / 2, y_of(v), cx + bw / 2, PLOT[3])
        c.rect(*box, fill=color)
        boxes.append(box)
    _heads(c, f"{subject} by {word}, {YEAR}", f"{subject} per {word}")
    _y_ticks(c, step, nt, y_of)
    if labelled:
        for i, v in enumerate(vals):
            cx = (boxes[i][0] + boxes[i][2]) / 2
            c.text(cx, boxes[i][1] - 5, str(v), size=12, anchor="ms", field=f"val_{i}",
                   kind="number", value=str(v))
    _x_labels(c, cats, [PLOT[0] + slot * (i + 0.5) for i in range(n)], PLOT[3] + 8)
    for i, (box, v) in enumerate(zip(boxes, vals)):
        c.mark(f"pt_0_{i}", *box, value=str(v), color=color)

    vf = (lambda i: f"val_{i}") if labelled else (lambda i: f"pt_0_{i}")
    x_f = [f"x_{i}" for i in range(n)]
    imax, imin = vals.index(max(vals)), vals.index(min(vals))
    pairs = [(i, j) for i in range(n) for j in range(n)
             if vals[i] - vals[j] >= (half if labelled else step)]
    i1 = rng.randrange(n)
    a, b = rng.choice(pairs) if pairs else (imax, imin)
    mk = (lambda i: Question("q_value", "lookup", f"What value is shown for {cats[i]}?",
                             str(vals[i]), canon="number", fields=[vf(i)])) if labelled else \
        (lambda i: Question("q_value", "lookup", f"Reading the bar chart, what is the value for "
                            f"{cats[i]}?", str(vals[i]), kind="relaxed", canon="number",
                            fields=[f"x_{i}", vf(i)]))
    questions = [
        mk(i1),
        Question("q_max", "max", f"Which {word} has the highest value in the bar chart?",
                 cats[imax], fields=x_f + [vf(i) for i in range(n)], accept=accept[imax]),
        Question("q_min", "min", f"Which {word} has the lowest value in the bar chart?",
                 cats[imin], fields=x_f + [vf(i) for i in range(n)], accept=accept[imin]),
    ]
    if abstain:
        missing = _missing_cat(inv, cats, word)
        questions.append(_abstain(c, inv, "q_missing", missing,
                                  f"What value does the bar chart show for {missing}?"))
    else:
        questions.append(Question(
            "q_diff", "difference", f"How much higher is the value for {cats[a]} than for "
            f"{cats[b]}?", str(vals[a] - vals[b]), kind="field" if labelled else "relaxed",
            canon="number", fields=[f"x_{a}", f"x_{b}", vf(a), vf(b)]))
    questions.append(Question("q_reading", "reading", "Read the chart.", "", kind="reading"))
    data = {"kind": "bar", "subject": subject, "word": word, "x": cats,
            "series": {subject: vals}, "tick_step": step, "y_max": ymax, "data_labels": labelled}
    return Rendered(c, "bar", questions, data, default_year=YEAR)


# ------------------------------------------------------------------ horizontal bar chart

def _hbar(rng: random.Random, *, theme: str, scale: float, abstain: bool) -> Rendered:
    inv = Inventor(rng)
    n = rng.randint(4, 7)
    cats, word, accept = _cats(rng, inv, n)
    subject = rng.choice(SUBJECTS)
    step, nt = rng.choice((10, 20, 50)), 5
    half, vmax = step // 2, step * nt
    labelled = rng.random() < 0.5
    while True:
        vals = ([rng.randint(step, vmax - step) for _ in cats] if labelled
                else [_halves(rng, half, 2, 2 * nt) for _ in cats])
        if _unique_extreme(vals, step if not labelled else half):
            break
    color = rng.choice(SERIES_COLORS[theme])
    w, h = LOGICAL_SIZES["hbar"]
    c = Canvas(w, h, scale=scale, theme=theme)
    x0, y0, x1, y1 = 130, 84, 604, 416
    X = lambda v: x0 + (x1 - x0) * v / vmax  # noqa: E731
    for k in range(1, nt + 1):
        c.line(X(k * step), y0, X(k * step), y1, color="grid")
    c.line(x0, y0, x0, y1, color="muted")
    slot = (y1 - y0) / n
    boxes = []
    for i, v in enumerate(vals):
        cy, bh = y0 + slot * (i + 0.5), slot * 0.56
        box = (x0, cy - bh / 2, X(v), cy + bh / 2)
        c.rect(*box, fill=color)
        boxes.append(box)
    _heads(c, f"{subject} by {word}, {YEAR}", f"{subject} per {word}")
    for k in range(nt + 1):
        c.text(X(k * step), y1 + 8, str(k * step), size=11, color="muted", anchor="ma")
    for i, cat in enumerate(cats):
        c.text(x0 - 10, y0 + slot * (i + 0.5), cat, size=12, anchor="rm", field=f"x_{i}", value=cat)
    if labelled:
        for i, v in enumerate(vals):
            c.text(X(v) + 6, y0 + slot * (i + 0.5), str(v), size=12, anchor="lm",
                   field=f"val_{i}", kind="number", value=str(v))
    for i, (box, v) in enumerate(zip(boxes, vals)):
        c.mark(f"pt_0_{i}", *box, value=str(v), color=color)

    vf = (lambda i: f"val_{i}") if labelled else (lambda i: f"pt_0_{i}")
    x_f = [f"x_{i}" for i in range(n)]
    imax, imin = vals.index(max(vals)), vals.index(min(vals))
    i1 = rng.randrange(n)
    pairs = [(i, j) for i in range(n) for j in range(n)
             if vals[i] - vals[j] >= (half if labelled else step)]
    a, b = rng.choice(pairs) if pairs else (imax, imin)
    if labelled:
        q_value = Question("q_value", "lookup", f"What value is shown for {cats[i1]}?",
                           str(vals[i1]), canon="number", fields=[vf(i1)])
    else:
        q_value = Question("q_value", "lookup", f"Reading the horizontal bar chart, what is the "
                           f"value for {cats[i1]}?", str(vals[i1]), kind="relaxed",
                           canon="number", fields=[f"x_{i1}", vf(i1)])
    questions = [
        q_value,
        Question("q_max", "max", f"Which {word} has the longest bar?", cats[imax],
                 fields=x_f + [vf(i) for i in range(n)], accept=accept[imax]),
        Question("q_min", "min", f"Which {word} has the shortest bar?", cats[imin],
                 fields=x_f + [vf(i) for i in range(n)], accept=accept[imin]),
    ]
    if abstain:
        missing = _missing_cat(inv, cats, word)
        questions.append(_abstain(c, inv, "q_missing", missing,
                                  f"What value does the chart show for {missing}?"))
    else:
        questions.append(Question(
            "q_diff", "difference", f"How much longer is the bar for {cats[a]} than the bar "
            f"for {cats[b]}, in value?", str(vals[a] - vals[b]),
            kind="field" if labelled else "relaxed", canon="number",
            fields=[f"x_{a}", f"x_{b}", vf(a), vf(b)]))
    questions.append(Question("q_reading", "reading", "Read the chart.", "", kind="reading"))
    data = {"kind": "hbar", "subject": subject, "word": word, "x": cats,
            "series": {subject: vals}, "tick_step": step, "y_max": vmax, "data_labels": labelled}
    return Rendered(c, "hbar", questions, data, default_year=YEAR)


# ------------------------------------------------------------------ grouped bar chart

def _grouped(rng: random.Random, *, theme: str, scale: float, abstain: bool) -> Rendered:
    inv = Inventor(rng)
    n = rng.choice((4, 6)) if rng.random() < 0.4 else 4
    xs, word, accept = _time_cats(rng, n)
    ns = rng.choice((2, 3))
    names = _unique(inv.brand, ns)
    step, nt = rng.choice((10, 20, 50)), 6
    half = step // 2
    for _ in range(4000):
        series = [[_halves(rng, half, 2, 2 * nt) for _ in xs] for _ in names]
        s0 = series[0]
        if abs(s0[-1] - s0[0]) >= 2 * step and _unique_extreme(s0, step):
            break
    colors = rng.sample(SERIES_COLORS[theme], ns)
    w, h = LOGICAL_SIZES["grouped_bar"]
    c = Canvas(w, h, scale=scale, theme=theme)
    y_of = _grid(c, step, nt)
    slot = (PLOT[2] - PLOT[0]) / n
    bw = slot * 0.74 / ns
    boxes = {}
    for i in range(n):
        left = PLOT[0] + slot * i + slot * 0.13
        for s in range(ns):
            box = (left + bw * s, y_of(series[s][i]), left + bw * (s + 1) - 2, PLOT[3])
            c.rect(*box, fill=colors[s])
            boxes[(s, i)] = box
    subject = rng.choice(SUBJECTS)
    _heads(c, f"{subject} per {word} and site, {YEAR}", "One bar per site in each group")
    _legend(c, names, colors, 68)
    _y_ticks(c, step, nt, y_of)
    _x_labels(c, xs, [PLOT[0] + slot * (i + 0.5) for i in range(n)], PLOT[3] + 8)
    for (s, i), box in boxes.items():
        c.mark(f"pt_{s}_{i}", *box, value=str(series[s][i]), color=colors[s])

    s_q = rng.randrange(ns)
    i1, i2 = rng.sample(range(n), 2)
    peak = series[s_q].index(max(series[s_q]))
    s_a, s_b = rng.sample(range(ns), 2)
    gaps = [i for i in range(n) if abs(series[s_a][i] - series[s_b][i]) >= step]
    if not gaps:
        gaps = [max(range(n), key=lambda i: abs(series[s_a][i] - series[s_b][i]))]
    ig = rng.choice(gaps)
    if series[s_a][ig] < series[s_b][ig]:
        s_a, s_b = s_b, s_a
    d = series[s_a][ig] - series[s_b][ig]
    questions = [
        _q_value("q_value", names[s_q], xs, series, s_q, i1, many=True, word=word,
                 noun="grouped bar chart"),
        Question("q_peak", "max", f"In which {word} is the {names[s_q]} bar the highest?",
                 xs[peak], fields=[f"leg_{s_q}"] + [f"x_{i}" for i in range(n)] +
                 [f"pt_{s_q}_{i}" for i in range(n)], accept=accept[peak]),
        Question("q_diff", "difference", f"In {xs[ig]}, how much higher is the {names[s_a]} bar "
                 f"than the {names[s_b]} bar?", str(d), kind="relaxed", canon="number",
                 fields=["leg_0", "leg_1", f"x_{ig}", f"pt_{s_a}_{ig}", f"pt_{s_b}_{ig}"]),
    ]
    if abstain:
        while True:
            missing = inv.brand()
            if missing not in names:
                break
        questions.append(_abstain(c, inv, "q_missing", missing,
                                  f"What is the value of {missing} in {xs[i2]}?"))
    else:
        rate = _q_rate("q_rate", names[0], xs, series, 0, many=True, rng=rng)
        if rate is None:               # a flat series has no growth: ask a second value instead
            rate = _q_value("q_value2", names[0], xs, series, 0, i2, many=True, word=word,
                            noun="grouped bar chart")
        questions.append(rate)
    questions.append(Question("q_reading", "reading", "Read the chart.", "", kind="reading"))
    data = {"kind": "grouped_bar", "subject": subject, "word": word, "x": xs,
            "series": dict(zip(names, series)), "tick_step": step, "y_max": step * nt,
            "legend": True}
    return Rendered(c, "grouped_bar", questions, data, default_year=YEAR)


# ------------------------------------------------------------------ line chart

def _gen_line(rng: random.Random, n: int, ns: int, half: int, nt: int, step: int, *,
              allow_flat: bool) -> list[list[int]]:
    for _ in range(6000):
        series = [[_halves(rng, half, 2, 2 * nt) for _ in range(n)] for _ in range(ns)]
        s0 = series[0]
        if ns == 1 and allow_flat and rng.random() < 0.2:
            s0[-1] = s0[0]
            srt = sorted(s0)
            if not (srt[-1] - srt[-2] >= step and srt[-1] - s0[0] >= step):
                continue
            ok = True
        else:
            ok = abs(s0[-1] - s0[0]) >= 2 * step and _unique_extreme(s0, step)
        if ns == 2:
            ok = ok and all(abs(a - b) >= step for a, b in zip(*series)) and \
                series[0][0] < series[1][0] and any(a > b for a, b in zip(*series))
        if ok:
            return series
    raise RuntimeError("could not draw a line series")


def _line(rng: random.Random, *, theme: str, scale: float, abstain: bool) -> Rendered:
    inv = Inventor(rng)
    n = rng.choice((4, 6))
    xs, word, accept = _time_cats(rng, n)
    ns = rng.choice((1, 2))
    names = _unique(inv.brand, ns)
    step, nt = rng.choice((10, 20, 50)), 6
    half = step // 2
    series = _gen_line(rng, n, ns, half, nt, step, allow_flat=True)
    colors = rng.sample(SERIES_COLORS[theme], ns)
    w, h = LOGICAL_SIZES["line"]
    c = Canvas(w, h, scale=scale, theme=theme)
    y_of = _grid(c, step, nt)
    slot = (PLOT[2] - PLOT[0]) / n
    px = [PLOT[0] + slot * (i + 0.5) for i in range(n)]
    for vals, col in zip(series, colors):
        c.polyline([(x, y_of(v)) for x, v in zip(px, vals)], color=col, width=2)
    for vals, col in zip(series, colors):
        for x, v in zip(px, vals):
            c.ellipse(x - 4.5, y_of(v) - 4.5, x + 4.5, y_of(v) + 4.5, fill=col)
    subject = rng.choice(SUBJECTS)
    _heads(c, f"{subject} per {word}, {YEAR}", f"{subject} by {'store' if ns > 1 else 'site'}")
    _legend(c, names, colors, 68, line=True)
    _y_ticks(c, step, nt, y_of)
    _x_labels(c, xs, px, PLOT[3] + 8)
    for si, (vals, col) in enumerate(zip(series, colors)):
        for i, v in enumerate(vals):
            c.mark(f"pt_{si}_{i}", px[i] - 4.5, y_of(v) - 4.5, px[i] + 4.5, y_of(v) + 4.5,
                   value=str(v), color=col)

    s0 = series[0]
    i1, i2 = rng.sample(range(n), 2)
    peak = s0.index(max(s0))
    many = True
    questions = [
        _q_value("q_value", names[0], xs, series, 0, i1, many=many, word=word, noun="line chart"),
        _q_trend("q_trend", names[0], xs, series, 0, many=many),
    ]
    cross = _q_cross("q_cross", names, xs, series, word) if ns == 2 else None
    questions.append(cross or Question(
        "q_peak", "max", f"In which {word} is {names[0]} highest?", xs[peak],
        fields=["leg_0"] + [f"x_{i}" for i in range(n)] + [f"pt_0_{i}" for i in range(n)],
        accept=accept[peak]))
    if abstain:
        while True:
            missing = inv.brand()
            if missing not in names:
                break
        questions.append(_abstain(c, inv, "q_missing", missing,
                                  f"What is the value of {missing} in {xs[i2]}?"))
    else:
        rate = _q_rate("q_rate", names[0], xs, series, 0, many=many, rng=rng)
        if rate is None:               # a flat series has no growth: ask a second value instead
            rate = _q_value("q_value2", names[0], xs, series, 0, i2, many=many, word=word,
                            noun="line chart")
        questions.append(rate)
    questions.append(Question("q_reading", "reading", "Read the chart.", "", kind="reading"))
    data = {"kind": "line", "subject": subject, "word": word, "x": xs,
            "series": dict(zip(names, series)), "tick_step": step, "y_max": step * nt,
            "legend": True}
    return Rendered(c, "line", questions, data, default_year=YEAR)


# ------------------------------------------------------------------ area chart

def _area(rng: random.Random, *, theme: str, scale: float, abstain: bool) -> Rendered:
    inv = Inventor(rng)
    n = rng.choice((4, 6))
    xs, word, accept = _time_cats(rng, n)
    ns = rng.choice((1, 2))
    names = _unique(inv.brand, ns)
    step, nt = rng.choice((10, 20, 50)), 6
    half = step // 2
    for _ in range(6000):
        series = [[_halves(rng, half, 2, 2 * nt) for _ in range(n)] for _ in range(ns)]
        s0 = series[0]
        ok = abs(s0[-1] - s0[0]) >= 2 * step and _unique_extreme(s0, step)
        if ns == 2:
            ok = ok and all(a - b >= step for a, b in zip(*series)) and min(series[1]) >= step
        if ok:
            break
    colors = rng.sample(SERIES_COLORS[theme], ns)
    w, h = LOGICAL_SIZES["area"]
    c = Canvas(w, h, scale=scale, theme=theme)
    y_of = _grid(c, step, nt)
    slot = (PLOT[2] - PLOT[0]) / n
    px = [PLOT[0] + slot * (i + 0.5) for i in range(n)]
    for si, (vals, col) in enumerate(zip(series, colors)):
        top = [(x, y_of(v)) for x, v in zip(px, vals)]
        c.polygon(top + [(px[-1], PLOT[3]), (px[0], PLOT[3])],
                  fill=_tint(c, col, 0.16 if si == 0 else 0.34))
        c.polyline(top, color=col, width=2)
    for vals, col in zip(series, colors):
        for x, v in zip(px, vals):
            # a background halo keeps each point readable over the other series' shading
            c.ellipse(x - 7.5, y_of(v) - 7.5, x + 7.5, y_of(v) + 7.5, fill="bg")
            c.ellipse(x - 4.5, y_of(v) - 4.5, x + 4.5, y_of(v) + 4.5, fill=col)
    subject = rng.choice(SUBJECTS)
    _heads(c, f"{subject} per {word}, {YEAR}", "Shaded area chart; the lower series sits in front"
           if ns == 2 else "Shaded area chart")
    _legend(c, names, colors, 68)
    _y_ticks(c, step, nt, y_of)
    _x_labels(c, xs, px, PLOT[3] + 8)
    for si, (vals, col) in enumerate(zip(series, colors)):
        for i, v in enumerate(vals):
            c.mark(f"pt_{si}_{i}", px[i] - 4.5, y_of(v) - 4.5, px[i] + 4.5, y_of(v) + 4.5,
                   value=str(v), color=col)
    s0 = series[0]
    i1, i2 = rng.sample(range(n), 2)
    peak = s0.index(max(s0))
    questions = [
        _q_value("q_value", names[0], xs, series, 0, i1, many=True, word=word, noun="area chart"),
        _q_trend("q_trend", names[0], xs, series, 0, many=True),
        Question("q_peak", "max", f"In which {word} is {names[0]} highest?", xs[peak],
                 fields=["leg_0"] + [f"x_{i}" for i in range(n)] + [f"pt_0_{i}" for i in range(n)],
                 accept=accept[peak]),
    ]
    if abstain:
        while True:
            missing = inv.brand()
            if missing not in names:
                break
        questions.append(_abstain(c, inv, "q_missing", missing,
                                  f"What is the value of {missing} in {xs[i2]}?"))
    else:
        rate = _q_rate("q_rate", names[0], xs, series, 0, many=True, rng=rng)
        if rate is None:               # a flat series has no growth: ask a second value instead
            rate = _q_value("q_value2", names[0], xs, series, 0, i2, many=True, word=word,
                            noun="area chart")
        questions.append(rate)
    questions.append(Question("q_reading", "reading", "Read the chart.", "", kind="reading"))
    data = {"kind": "area", "subject": subject, "word": word, "x": xs,
            "series": dict(zip(names, series)), "tick_step": step, "y_max": step * nt,
            "legend": True}
    return Rendered(c, "area", questions, data, default_year=YEAR)


# ------------------------------------------------------------------ dual-axis chart

def _dual(rng: random.Random, *, theme: str, scale: float, abstain: bool) -> Rendered:
    inv = Inventor(rng)
    n = rng.choice((4, 6))
    xs, word, accept = _time_cats(rng, n)
    names = _unique(inv.brand, 2)
    stepA = rng.choice((10, 20, 50))
    stepB = rng.choice([s for s in (4, 8, 40, 100, 200) if s != stepA])
    nt = 5
    halfA, halfB = stepA // 2, stepB // 2
    for _ in range(6000):
        A = [_halves(rng, halfA, 2, 2 * nt) for _ in xs]
        B = [_halves(rng, halfB, 2, 2 * nt) for _ in xs]
        if _unique_extreme(A, stepA) and _unique_extreme(B, stepB) and \
                abs(A[-1] - A[0]) >= 2 * stepA and abs(B[-1] - B[0]) >= 2 * stepB:
            break
    colors = rng.sample(SERIES_COLORS[theme], 2)
    w, h = LOGICAL_SIZES["dual_axis"]
    c = Canvas(w, h, scale=scale, theme=theme)
    plot = (72, 100, 590, 420)
    yA = lambda v: plot[3] - (plot[3] - plot[1]) * v / (stepA * nt)  # noqa: E731
    yB = lambda v: plot[3] - (plot[3] - plot[1]) * v / (stepB * nt)  # noqa: E731
    for k in range(1, nt + 1):
        c.line(plot[0], yA(k * stepA), plot[2], yA(k * stepA), color="grid")
    c.line(plot[0], plot[3], plot[2], plot[3], color="muted")
    slot = (plot[2] - plot[0]) / n
    bw = slot * 0.34
    boxes = []
    for i, v in enumerate(A):
        left = plot[0] + slot * i + slot * 0.14
        box = (left, yA(v), left + bw, plot[3])
        c.rect(*box, fill=colors[0])
        boxes.append(box)
    px = [plot[0] + slot * (i + 0.72) for i in range(n)]
    c.polyline([(x, yB(v)) for x, v in zip(px, B)], color=colors[1], width=2)
    for x, v in zip(px, B):
        c.ellipse(x - 4.5, yB(v) - 4.5, x + 4.5, yB(v) + 4.5, fill=colors[1])
    subject = rng.choice(SUBJECTS)
    _heads(c, f"{subject} and revenue per {word}, {YEAR}",
           f"Bars use the left axis; the line uses the right axis")
    _legend(c, names, colors, 68)
    for k in range(nt, -1, -1):
        c.text(plot[0] - 8, yA(k * stepA), str(k * stepA), size=11, color=colors[0], anchor="rm")
        c.text(plot[2] + 8, yB(k * stepB), str(k * stepB), size=11, color=colors[1], anchor="lm")
    _x_labels(c, xs, [plot[0] + slot * (i + 0.5) for i in range(n)], plot[3] + 8)
    for i in range(n):
        c.mark(f"pt_0_{i}", *boxes[i], value=str(A[i]), color=colors[0])
        c.mark(f"pt_1_{i}", px[i] - 4.5, yB(B[i]) - 4.5, px[i] + 4.5, yB(B[i]) + 4.5,
               value=str(B[i]), color=colors[1])
    series = [A, B]
    i1, i2 = rng.sample(range(n), 2)
    peak = B.index(max(B))
    questions = [
        Question("q_bar", "lookup", f"Reading the left axis, what is the {names[0]} bar value "
                 f"in {xs[i1]}?", str(A[i1]), kind="relaxed", canon="number",
                 fields=["leg_0", f"x_{i1}", f"pt_0_{i1}"]),
        Question("q_line", "lookup", f"Reading the right axis, what is the {names[1]} line "
                 f"value in {xs[i2]}?", str(B[i2]), kind="relaxed", canon="number",
                 fields=["leg_1", f"x_{i2}", f"pt_1_{i2}"]),
        Question("q_peak", "max", f"In which {word} is the {names[1]} line highest?", xs[peak],
                 fields=["leg_1"] + [f"x_{i}" for i in range(n)] + [f"pt_1_{i}" for i in range(n)],
                 accept=accept[peak]),
    ]
    if abstain:
        while True:
            missing = inv.brand()
            if missing not in names:
                break
        questions.append(_abstain(c, inv, "q_missing", missing,
                                  f"What is the value of {missing} in {xs[i2]}?"))
    else:
        questions.append(_q_trend("q_trend", names[0], xs, series, 0, many=True))
    questions.append(Question("q_reading", "reading", "Read the chart.", "", kind="reading"))
    data = {"kind": "dual_axis", "subject": subject, "word": word, "x": xs,
            "series": dict(zip(names, series)), "tick_step": stepA, "y_max": stepA * nt,
            "right_tick_step": stepB, "right_y_max": stepB * nt, "dual_axis": True,
            "legend": True}
    return Rendered(c, "dual_axis", questions, data, default_year=YEAR)


# ------------------------------------------------------------------ histogram

def _histogram(rng: random.Random, *, theme: str, scale: float, abstain: bool) -> Rendered:
    inv = Inventor(rng)
    n = rng.randint(5, 8)
    width = rng.choice((5, 10, 20))
    lo0 = rng.choice((0, 10, 20)) if width <= 10 else 0
    labels = [f"{lo0 + i * width}-{lo0 + (i + 1) * width - 1}" for i in range(n)]
    accept = [[f"{lo0 + i * width} to {lo0 + (i + 1) * width - 1}",
               f"{lo0 + i * width} - {lo0 + (i + 1) * width - 1}"] for i in range(n)]
    step, nt = rng.choice((10, 20, 50)), 5
    half, ymax = step // 2, step * nt
    labelled = rng.random() < 0.5
    while True:
        vals = ([rng.randint(step, ymax - half) for _ in labels] if labelled
                else [_halves(rng, half, 2, 2 * nt) for _ in labels])
        if _unique_extreme(vals, step if not labelled else half):
            break
    color = rng.choice(SERIES_COLORS[theme])
    w, h = LOGICAL_SIZES["histogram"]
    c = Canvas(w, h, scale=scale, theme=theme)
    y_of = _grid(c, step, nt)
    slot = (PLOT[2] - PLOT[0]) / n
    boxes = []
    for i, v in enumerate(vals):
        box = (PLOT[0] + slot * i + 1, y_of(v), PLOT[0] + slot * (i + 1) - 1, PLOT[3])
        c.rect(*box, fill=color)
        boxes.append(box)
    subject = rng.choice(("Ages", "Scores", "Wait times", "Ride lengths", "Basket sizes"))
    _heads(c, f"{subject}, number of people per band", f"Each bar counts the people in one band "
           f"of {width} values")
    _y_ticks(c, step, nt, y_of)
    for i, lab in enumerate(labels):
        c.text(PLOT[0] + slot * (i + 0.5), PLOT[3] + 8, lab, size=11, anchor="ma",
               field=f"x_{i}", value=lab)
    if labelled:
        for i, v in enumerate(vals):
            c.text(PLOT[0] + slot * (i + 0.5), boxes[i][1] - 5, str(v), size=12, anchor="ms",
                   field=f"val_{i}", kind="number", value=str(v))
    for i, (box, v) in enumerate(zip(boxes, vals)):
        c.mark(f"pt_0_{i}", *box, value=str(v), color=color)
    vf = (lambda i: f"val_{i}") if labelled else (lambda i: f"pt_0_{i}")
    x_f = [f"x_{i}" for i in range(n)]
    imax, imin = vals.index(max(vals)), vals.index(min(vals))
    pairs = [(i, j) for i in range(n) for j in range(n)
             if vals[i] - vals[j] >= (half if labelled else step)]
    a, b = rng.choice(pairs) if pairs else (imax, imin)
    i1 = rng.randrange(n)
    if labelled:
        q_value = Question("q_value", "lookup", f"How many people are in the band {labels[i1]}?",
                           str(vals[i1]), canon="number", fields=[vf(i1)], accept=[])
    else:
        q_value = Question("q_value", "lookup", f"Reading the histogram, how many people are in "
                           f"the band {labels[i1]}?", str(vals[i1]), kind="relaxed",
                           canon="number", fields=[f"x_{i1}", vf(i1)])
    questions = [
        q_value,
        Question("q_max", "max", "Which band has the most people?", labels[imax],
                 fields=x_f + [vf(i) for i in range(n)], accept=accept[imax]),
        Question("q_diff", "difference", f"How many more people are in the band {labels[a]} than "
                 f"in the band {labels[b]}?", str(vals[a] - vals[b]),
                 kind="field" if labelled else "relaxed", canon="number",
                 fields=[f"x_{a}", f"x_{b}", vf(a), vf(b)]),
    ]
    if abstain:
        miss = f"{lo0 + n * width}-{lo0 + (n + 1) * width - 1}"
        questions.append(_abstain(c, inv, "q_missing", miss,
                                  f"How many people are in the band {miss}?"))
    else:
        questions.append(Question("q_count", "count", "How many bands does the histogram show?",
                                  str(n), canon="number", fields=x_f))
    questions.append(Question("q_reading", "reading", "Read the chart.", "", kind="reading"))
    data = {"kind": "histogram", "subject": subject, "word": "band", "x": labels,
            "series": {"People": vals}, "tick_step": step, "y_max": ymax, "data_labels": labelled,
            "bin_width": width}
    return Rendered(c, "histogram", questions, data, default_year=YEAR)


# ------------------------------------------------------------------ box plot

def _box(rng: random.Random, *, theme: str, scale: float, abstain: bool) -> Rendered:
    inv = Inventor(rng)
    n = rng.randint(3, 5)
    groups = _unique(inv.brand, n)
    step, nt = rng.choice((10, 20, 50)), 5
    half = step // 2
    while True:
        stats = [sorted(rng.sample(range(1, 2 * nt + 1), 5)) for _ in groups]
        stats = [[half * v for v in s] for s in stats]
        meds = [s[2] for s in stats]
        if _unique_extreme(meds, step) and _unique_extreme([s[4] for s in stats], step):
            break
    color = rng.choice(SERIES_COLORS[theme])
    w, h = LOGICAL_SIZES["box_plot"]
    c = Canvas(w, h, scale=scale, theme=theme)
    y_of = _grid(c, step, nt)
    slot = (PLOT[2] - PLOT[0]) / n
    bw = slot * 0.4
    centers = [PLOT[0] + slot * (i + 0.5) for i in range(n)]
    for cx, (mn, q1, md, q3, mx) in zip(centers, stats):
        c.line(cx, y_of(mx), cx, y_of(mn), color=color, width=2)
        for v in (mn, mx):
            c.line(cx - bw / 4, y_of(v), cx + bw / 4, y_of(v), color=color, width=2)
        c.rect(cx - bw / 2, y_of(q3), cx + bw / 2, y_of(q1), fill="surface", outline=color, width=2)
        c.line(cx - bw / 2, y_of(md), cx + bw / 2, y_of(md), color=color, width=3)
    subject = rng.choice(("Delivery time (min)", "Test score", "Weekly spend", "Wait time (min)"))
    _heads(c, f"{subject} by store", "Box: middle half of values; thick line: median; "
           "whiskers: lowest and highest")
    _y_ticks(c, step, nt, y_of)
    for i, g in enumerate(groups):
        c.text(centers[i], PLOT[3] + 8, g, size=12, anchor="ma", field=f"x_{i}", value=g)
    for i, (cx, (mn, q1, md, q3, mx)) in enumerate(zip(centers, stats)):
        c.mark(f"med_{i}", cx - bw / 2, y_of(md) - 2.5, cx + bw / 2, y_of(md) + 2.5,
               value=str(md), color=color)
        c.mark(f"max_{i}", cx - bw / 4, y_of(mx) - 2.5, cx + bw / 4, y_of(mx) + 2.5,
               value=str(mx), color=color)
    i1, i2 = rng.sample(range(n), 2)
    imax = meds.index(max(meds))
    pairs = [(i, j) for i in range(n) for j in range(n) if meds[i] - meds[j] >= step]
    a, b = rng.choice(pairs)
    x_f = [f"x_{i}" for i in range(n)]
    questions = [
        Question("q_median", "lookup", f"Reading the box plot, what is the median for "
                 f"{groups[i1]}?", str(meds[i1]), kind="relaxed", canon="number",
                 fields=[f"x_{i1}", f"med_{i1}"]),
        Question("q_max", "max", "Which store has the highest median?", groups[imax],
                 fields=x_f + [f"med_{i}" for i in range(n)]),
        Question("q_top", "lookup", f"Reading the box plot, what is the highest value (top of "
                 f"the whisker) for {groups[i2]}?", str(stats[i2][4]), kind="relaxed",
                 canon="number", fields=[f"x_{i2}", f"max_{i2}"]),
    ]
    if abstain:
        missing = _missing_cat(inv, groups, "store")
        questions.append(_abstain(c, inv, "q_missing", missing,
                                  f"What is the median for {missing}?"))
    else:
        questions.append(Question("q_diff", "difference", f"How much higher is the median for "
                                  f"{groups[a]} than for {groups[b]}?", str(meds[a] - meds[b]),
                                  kind="relaxed", canon="number",
                                  fields=[f"x_{a}", f"x_{b}", f"med_{a}", f"med_{b}"]))
    questions.append(Question("q_reading", "reading", "Read the chart.", "", kind="reading"))
    data = {"kind": "box_plot", "subject": subject, "groups": groups,
            "stats": [dict(zip(("min", "q1", "median", "q3", "max"), s)) for s in stats],
            "tick_step": step, "y_max": step * nt}
    return Rendered(c, "box_plot", questions, data, default_year=YEAR)


# ------------------------------------------------------------------ pie chart

def _pie(rng: random.Random, *, theme: str, scale: float, abstain: bool) -> Rendered:
    inv = Inventor(rng)
    n = rng.randint(3, 6)
    cats = _unique(inv.brand, n)
    while True:
        cuts = sorted(rng.sample(range(1, 100), n - 1))
        pct = [b - a for a, b in zip([0] + cuts, cuts + [100])]
        s = sorted(pct)
        if s[0] >= 6 and s[-1] - s[-2] >= 3:
            break
    colors = rng.sample(SERIES_COLORS[theme], n)
    w, h = LOGICAL_SIZES["pie"]
    c = Canvas(w, h, scale=scale, theme=theme)
    cx, cy, r = 220, 276, 134
    start = -90.0
    mids = []
    for p, col in zip(pct, colors):
        end = start + 360 * p / 100
        c.pieslice(cx - r, cy - r, cx + r, cy + r, start, end, fill=col)
        mids.append((start + end) / 2)
        start = end
    subject = rng.choice(SUBJECTS)
    _heads(c, f"Share of {subject.lower()} by store", f"Per cent of all {subject.lower()}, "
           f"{MONTHS[rng.randrange(12)]} {YEAR}")
    for i, (name, col) in enumerate(zip(cats, colors)):
        y = 124 + 32 * i
        c.rect(430, y + 1, 444, y + 15, fill=col)
        c.text(454, y, name, size=13, field=f"leg_{i}", value=name)
    for i, (p, a) in enumerate(zip(pct, mids)):
        rad = math.radians(a)
        c.text(cx + (r + 24) * math.cos(rad), cy + (r + 24) * math.sin(rad), f"{p}%", size=12,
               anchor="mm", field=f"pct_{i}", kind="number", value=str(p))

    i1, i2 = rng.sample(range(n), 2)
    a, b = (i1, i2) if pct[i1] >= pct[i2] else (i2, i1)
    imax = pct.index(max(pct))
    leg = [f"leg_{i}" for i in range(n)]
    pcts = [f"pct_{i}" for i in range(n)]
    questions = [
        Question("q_share", "lookup", f"What percentage share does {cats[i1]} have?",
                 str(pct[i1]), canon="number", fields=[f"leg_{i1}", f"pct_{i1}"]),
        Question("q_max", "max", "Which store has the largest share?", cats[imax],
                 fields=leg + pcts),
        Question("q_diff", "difference", f"By how many percentage points is the share of "
                 f"{cats[a]} larger than the share of {cats[b]}?", str(pct[a] - pct[b]),
                 canon="number", fields=[f"leg_{a}", f"leg_{b}", f"pct_{a}", f"pct_{b}"]),
    ]
    if abstain:
        missing = _missing_cat(inv, cats, "store")
        questions.append(_abstain(c, inv, "q_missing", missing,
                                  f"What percentage share does {missing} have?"))
    else:
        questions.append(Question("q_count", "count", "How many slices does the pie chart have?",
                                  str(n), canon="number", fields=pcts))
    questions.append(Question("q_reading", "reading", "Read the chart.", "", kind="reading"))
    data = {"kind": "pie", "subject": subject, "categories": cats, "percent": pct}
    return Rendered(c, "pie", questions, data, default_year=YEAR)


# ------------------------------------------------------------------ stacked bar chart

def _stacked(rng: random.Random, *, theme: str, scale: float, abstain: bool) -> Rendered:
    inv = Inventor(rng)
    n = rng.randint(3, 6)
    cats, word, accept = _cats(rng, inv, n)
    ns = rng.choice((2, 3))
    names = rng.sample(CHANNELS, ns)
    step, nt = rng.choice((10, 20, 50)), 6
    half = step // 2
    while True:
        segs = [[_halves(rng, half, 2, 4) for _ in names] for _ in cats]
        totals = [sum(s) for s in segs]
        if max(totals) <= step * nt and _unique_extreme(totals, step):
            break
    colors = rng.sample(SERIES_COLORS[theme], ns)
    w, h = LOGICAL_SIZES["stacked_bar"]
    c = Canvas(w, h, scale=scale, theme=theme)
    y_of = _grid(c, step, nt)
    slot = (PLOT[2] - PLOT[0]) / n
    marks = []
    for i, seg in enumerate(segs):
        cx, bw = PLOT[0] + slot * (i + 0.5), slot * 0.5
        cum = 0
        for si, v in enumerate(seg):
            top = y_of(cum + v) + (1 if si < ns - 1 else 0)
            bottom = PLOT[3] if si == 0 else y_of(cum) - 1
            c.rect(cx - bw / 2, top, cx + bw / 2, bottom, fill=colors[si])
            if si == 0:
                marks.append((f"seg_{i}_0", (cx - bw / 2, top, cx + bw / 2, bottom), v, colors[0]))
            if si == ns - 1:
                marks.append((f"tot_{i}", (cx - bw / 2, top, cx + bw / 2, bottom), cum + v,
                              colors[si]))
            cum += v
    subject = rng.choice(SUBJECTS)
    _heads(c, f"{subject} by {word} and channel, {YEAR}", "Stacked totals; each colour is a channel")
    _legend(c, names, colors, 68)
    _y_ticks(c, step, nt, y_of)
    for i, cat in enumerate(cats):
        c.text(PLOT[0] + slot * (i + 0.5), PLOT[3] + 8, cat, size=12, anchor="ma",
               field=f"cat_{i}", value=cat)
    for name, box, v, col in marks:
        c.mark(name, *box, value=str(v), color=col)

    i1, i2 = rng.sample(range(n), 2)
    imax = totals.index(max(totals))
    questions = [
        Question("q_total", "lookup", f"Reading the stacked bar chart, what is the total for "
                 f"{cats[i1]} (all channels together)?", str(totals[i1]), kind="relaxed",
                 canon="number", fields=[f"cat_{i1}", f"tot_{i1}"]),
        Question("q_max", "max", f"Which {word} has the largest total?", cats[imax],
                 fields=[f"cat_{i}" for i in range(n)] + [f"tot_{i}" for i in range(n)],
                 accept=accept[imax]),
        Question("q_bottom_series", "lookup",
                 "Which channel is the bottom segment of every bar?", names[0], fields=["leg_0"]),
    ]
    if abstain:
        missing = _missing_cat(inv, cats, word)
        questions.append(_abstain(c, inv, "q_missing", missing,
                                  f"What is the total for {missing} in the stacked bar chart?"))
    else:
        questions.append(Question("q_bottom", "lookup", f"Reading the stacked bar chart, what is "
                                  f"the {names[0]} value for {cats[i2]}?", str(segs[i2][0]),
                                  kind="relaxed", canon="number",
                                  fields=["leg_0", f"cat_{i2}", f"seg_{i2}_0"]))
    questions.append(Question("q_reading", "reading", "Read the chart.", "", kind="reading"))
    data = {"kind": "stacked_bar", "subject": subject, "word": word, "categories": cats,
            "series": names, "values": segs, "totals": totals, "tick_step": step,
            "y_max": step * nt}
    return Rendered(c, "stacked_bar", questions, data, default_year=YEAR)


# ------------------------------------------------------------------ scatter plot

def _overlap(a, b, m: float = 3) -> bool:
    return a[0] < b[2] + m and b[0] < a[2] + m and a[1] < b[3] + m and b[1] < a[3] + m


def _scatter(rng: random.Random, *, theme: str, scale: float, abstain: bool) -> Rendered:
    inv = Inventor(rng)
    xt, yt = rng.choice(AXES)
    sx, sy, nt = rng.choice((10, 20, 50)), rng.choice((10, 20, 50)), 5
    plot = (72, 92, 600, 430)
    w, h = LOGICAL_SIZES["scatter"]
    c = Canvas(w, h, scale=scale, theme=theme)
    X = lambda v: plot[0] + (plot[2] - plot[0]) * v / (sx * nt)  # noqa: E731
    Y = lambda v: plot[3] - (plot[3] - plot[1]) * v / (sy * nt)  # noqa: E731
    while True:
        n = rng.randint(6, 9)
        names = _unique(inv.brand, n)
        pts, boxes = [], []
        for name in names:
            for _ in range(400):
                x, y = _halves(rng, sx // 2, 2, 2 * nt - 1), _halves(rng, sy // 2, 2, 2 * nt - 1)
                tw = c.text_width(name, 11)
                pb = (X(x) - 5, Y(y) - 5, X(x) + 5, Y(y) + 5)
                lx = X(x) + 9 if X(x) + 9 + tw < plot[2] - 4 else X(x) - 9 - tw
                lb = (lx, Y(y) - 8, lx + tw, Y(y) + 8)
                if not any(_overlap(pb, b) or _overlap(lb, b) for b in boxes):
                    pts.append((x, y, lx))
                    boxes += [pb, lb]
                    break
        ys = [p[1] for p in pts]
        if len(pts) == n and sorted(ys)[-1] - sorted(ys)[-2] >= sy:
            break
    color = rng.choice(SERIES_COLORS[theme])
    for k in range(1, nt + 1):
        c.line(plot[0], Y(k * sy), plot[2], Y(k * sy), color="grid")
        c.line(X(k * sx), plot[1], X(k * sx), plot[3], color="grid")
    c.line(plot[0], plot[3], plot[2], plot[3], color="muted")
    c.line(plot[0], plot[1], plot[0], plot[3], color="muted")
    for x, y, _ in pts:
        c.ellipse(X(x) - 4.5, Y(y) - 4.5, X(x) + 4.5, Y(y) + 4.5, fill=color)
    c.text(24, 18, f"{yt} against {xt.lower()}", size=18, bold=True)
    c.text(24, 44, f"Each point is one store; x: {xt.lower()}, y: {yt.lower()}", size=11,
           color="muted")
    c.text(24, 66, yt, size=12)
    for k in range(nt, -1, -1):
        c.text(plot[0] - 8, Y(k * sy), str(k * sy), size=11, color="muted", anchor="rm")
    for i, ((x, y, lx), name) in enumerate(zip(pts, names)):
        c.text(lx, Y(y), name, size=11, anchor="lm", field=f"lab_{i}", value=name)
    for k in range(0, nt + 1):
        c.text(X(k * sx), plot[3] + 8, str(k * sx), size=11, color="muted", anchor="ma")
    c.text((plot[0] + plot[2]) / 2, plot[3] + 30, xt, size=12, anchor="ma")
    for i, (x, y, _) in enumerate(pts):
        box = (X(x) - 4.5, Y(y) - 4.5, X(x) + 4.5, Y(y) + 4.5)
        c.mark(f"pt_{i}_y", *box, value=str(y), color=color)
        c.mark(f"pt_{i}_x", *box, value=str(x), color=color)

    i1, i2 = rng.sample(range(n), 2)
    imax = ys.index(max(ys))
    questions = [
        Question("q_y", "lookup", f"In the scatter plot, what is the {yt.lower()} (y value) of "
                 f"point {names[i1]}?", str(pts[i1][1]), kind="relaxed", canon="number",
                 fields=[f"lab_{i1}", f"pt_{i1}_y"]),
        Question("q_x", "lookup", f"In the scatter plot, what is the {xt.lower()} (x value) of "
                 f"point {names[i2]}?", str(pts[i2][0]), kind="relaxed", canon="number",
                 fields=[f"lab_{i2}", f"pt_{i2}_x"]),
        Question("q_max", "max", f"Which point has the highest {yt.lower()}?", names[imax],
                 fields=[f"lab_{i}" for i in range(n)] + [f"pt_{i}_y" for i in range(n)]),
    ]
    if abstain:
        missing = _missing_cat(inv, names, "store")
        questions.append(_abstain(c, inv, "q_missing", missing,
                                  f"What is the {yt.lower()} of point {missing}?"))
    else:
        questions.append(Question("q_count", "count", "How many points are plotted?", str(n),
                                  canon="number", fields=[f"pt_{i}_y" for i in range(n)]))
    questions.append(Question("q_reading", "reading", "Read the chart.", "", kind="reading"))
    data = {"kind": "scatter", "x_axis": xt, "y_axis": yt, "x_step": sx, "y_step": sy,
            "points": [{"name": nm, "x": p[0], "y": p[1]} for nm, p in zip(names, pts)]}
    return Rendered(c, "scatter", questions, data, default_year=YEAR)
