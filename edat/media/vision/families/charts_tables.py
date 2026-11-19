"""Charts and tables: a table and five chart types drawn from invented data (spec 05 §7.1).

Templates: table (3-30 rows), bar chart, line chart, pie chart, stacked bar chart, scatter plot.
Values printed on the image (table cells, bar data labels, pie percentages) are planted
text fields scored by exact match. Values read off the plot with no printed label are
recorded with ``Canvas.mark`` and scored relaxed (within 5 %); they sit on a gridline or
half-way between two, on an axis with ticks 10, 20 or 50 apart, and are never below one
tick step, so the 5 % rule is fair.
Question types: cell, max, min, difference, lookup, trend, compare, count, absence, reading.
"""
from __future__ import annotations

import math
import random

from edat.media.common import Inventor, MONTHS, canon_text, money_str
from edat.media.vision.canvas import Canvas
from edat.media.vision.items import ABSTAIN_ANSWER, Question, Rendered

FAMILY = "charts_tables"
TEMPLATES = ("table", "bar_chart", "line_chart", "pie_chart", "stacked_bar", "scatter")
LOGICAL_SIZES = {"table": (600, 780), "bar_chart": (640, 480), "line_chart": (640, 480),
                 "pie_chart": (640, 480), "stacked_bar": (640, 480), "scatter": (640, 520)}
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
TABLES = (
    ("Store", "store", (("Units", "int", 40, 900), ("Revenue", "money", 500, 9000),
                        ("Staff", "int", 3, 40))),
    ("Team", "team", (("Wins", "int", 0, 30), ("Losses", "int", 0, 30),
                      ("Points", "int", 0, 90))),
    ("Product", "product", (("Stock", "int", 0, 500), ("Price", "money", 2, 120),
                            ("Sold", "int", 0, 400))),
)


def render(rng: random.Random, *, template: str, theme: str, scale: float,
           abstain: bool) -> Rendered:
    fn = {"table": _table, "bar_chart": _bar, "line_chart": _line, "pie_chart": _pie,
          "stacked_bar": _stacked, "scatter": _scatter}.get(template)
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


def _legend(c: Canvas, names: list[str], colors, y: float, *, line: bool = False) -> None:
    x = PLOT[0]
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


def _cats(rng: random.Random, inv: Inventor, n: int) -> tuple[list[str], str, list[list[str]]]:
    """Category labels, the word for them, and accepted alternative answers."""
    if n <= 4 and rng.random() < 0.5:
        return [f"Q{i + 1}" for i in range(n)], "quarter", [[] for _ in range(n)]
    if rng.random() < 0.5:
        s = rng.randrange(0, 13 - n)
        return [MONTHS[i][:3] for i in range(s, s + n)], "month", \
            [[MONTHS[i]] for i in range(s, s + n)]
    return _unique(inv.brand, n), "store", [[] for _ in range(n)]


def _halves(rng: random.Random, half: int, lo: int, hi: int) -> int:
    return half * rng.randint(lo, hi)


# ------------------------------------------------------------------ table

def _table(rng: random.Random, *, theme: str, scale: float, abstain: bool) -> Rendered:
    inv = Inventor(rng)
    head, word, cols = rng.choice(TABLES)
    n = rng.randint(3, 30)
    rows = _unique(inv.brand, n)
    values: list[list[float]] = []
    for name, typ, lo, hi in cols:
        while True:
            col = [inv.money(lo, hi) if typ == "money" else rng.randint(lo, hi) for _ in rows]
            if _unique_extreme(col, 0.01):
                break
        values.append(col)
    month = rng.randrange(12)
    w, h = LOGICAL_SIZES["table"]
    c = Canvas(w, h, scale=scale, theme=theme)
    c.text(24, 20, f"{inv.brand()} {word} report", size=18, bold=True)
    c.text(24, 48, f"Figures for {MONTHS[month]} {YEAR}", size=11, color="muted")
    top, rh = 76, min(26.0, (h - 24 - 76) / (n + 1))
    fs = 12 if rh >= 20 else 11
    right = (330, 450, 568)
    c.rect(24, top, w - 24, top + rh, fill="surface")
    c.text(32, top + rh / 2, head, size=fs, color="muted", anchor="lm")
    for (name, *_), xr in zip(cols, right):
        c.text(xr, top + rh / 2, name, size=fs, color="muted", anchor="rm")

    def cell(ci: int, v: float) -> tuple[str, str]:
        if cols[ci][1] == "money":
            return money_str(v), f"{v:.2f}"
        return str(v), str(v)
    for r, label in enumerate(rows):
        y = top + rh * (r + 1)
        if r % 2:
            c.rect(24, y, w - 24, y + rh, fill="surface")
        c.text(32, y + rh / 2, label, size=fs, anchor="lm", field=f"row_{r}", value=label)
        for ci, xr in enumerate(right):
            s, v = cell(ci, values[ci][r])
            c.text(xr, y + rh / 2, s, size=fs, anchor="rm", field=f"cell_{r}_{ci}",
                   kind="money" if cols[ci][1] == "money" else "number", value=v)
    c.line(24, top + rh * (n + 1), w - 24, top + rh * (n + 1))

    c_look, c_max, c_min = rng.sample(range(3), 3)
    canon = lambda ci: "money" if cols[ci][1] == "money" else "number"  # noqa: E731
    r_look = rng.randrange(n)
    a, b = rng.sample(range(n), 2)
    while values[c_look][a] == values[c_look][b]:
        a, b = rng.sample(range(n), 2)
    if values[c_look][a] < values[c_look][b]:
        a, b = b, a
    diff = round(values[c_look][a] - values[c_look][b], 2)
    imax = max(range(n), key=lambda r: values[c_max][r])
    imin = min(range(n), key=lambda r: values[c_min][r])
    col_fields = lambda ci: [f"row_{r}" for r in range(n)] + [f"cell_{r}_{ci}" for r in range(n)]  # noqa: E731
    lname = lambda ci: cols[ci][0].lower()  # noqa: E731
    questions = [
        Question("q_cell", "cell", f"In the table, what is the {lname(c_look)} for {rows[r_look]}?",
                 cell(c_look, values[c_look][r_look])[1], canon=canon(c_look),
                 fields=[f"cell_{r_look}_{c_look}"]),
        Question("q_max", "max", f"Which {word} has the highest {lname(c_max)}?", rows[imax],
                 fields=col_fields(c_max)),
        Question("q_diff", "difference",
                 f"How much higher is the {lname(c_look)} for {rows[a]} than for {rows[b]}?",
                 f"{diff:.2f}" if canon(c_look) == "money" else str(int(diff)),
                 canon=canon(c_look), fields=[f"cell_{a}_{c_look}", f"cell_{b}_{c_look}"]),
        Question("q_reading", "reading", "Read the table.", "", kind="reading"),
    ]
    if abstain and rng.random() < 0.5:
        rb = rng.randrange(n)
        c.blur_field(f"cell_{rb}_{c_min}", radius=7)
        questions.append(Question("q_cell_abstain", "absence",
                                  f"What is the {lname(c_min)} for {rows[rb]}?", ABSTAIN_ANSWER,
                                  kind="abstain", fields=[f"cell_{rb}_{c_min}"]))
    elif abstain:
        missing = _missing_cat(inv, rows, "store")
        questions.append(_abstain(c, inv, "q_row_abstain", missing,
                                  f"What is the {lname(c_min)} for {missing}?"))
    else:
        questions.append(Question("q_min", "min", f"Which {word} has the lowest {lname(c_min)}?",
                                  rows[imin], fields=col_fields(c_min)))
    data = {"columns": [head] + [x[0] for x in cols], "rows": rows,
            "values": [[values[ci][r] for ci in range(3)] for r in range(n)],
            "month": MONTHS[month], "year": YEAR}
    return Rendered(c, "table", questions, data, default_year=YEAR)


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
    w, h = LOGICAL_SIZES["bar_chart"]
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
    for i, cat in enumerate(cats):
        c.text(PLOT[0] + slot * (i + 0.5), PLOT[3] + 8, cat, size=12, anchor="ma",
               field=f"cat_{i}", value=cat)
    for i, (box, v) in enumerate(zip(boxes, vals)):
        c.mark(f"bar_{i}", *box, value=str(v), color=color)

    vf = (lambda i: f"val_{i}") if labelled else (lambda i: f"bar_{i}")
    cat_f = [f"cat_{i}" for i in range(n)]
    imax, imin = vals.index(max(vals)), vals.index(min(vals))
    i1, i2 = rng.sample(range(n), 2)
    q_value = Question("q_value", "lookup", f"What value is shown for {cats[i1]}?", str(vals[i1]),
                       canon="number", fields=[vf(i1)]) if labelled else \
        Question("q_value", "lookup", f"Reading the bar chart, what is the value for {cats[i1]}?",
                 str(vals[i1]), kind="relaxed", canon="number", fields=[vf(i1)])
    questions = [
        q_value,
        Question("q_max", "max", f"Which {word} has the highest value in the bar chart?",
                 cats[imax], fields=cat_f + [vf(i) for i in range(n)], accept=accept[imax]),
        Question("q_min", "min", f"Which {word} has the lowest value in the bar chart?",
                 cats[imin], fields=cat_f + [vf(i) for i in range(n)], accept=accept[imin]),
        Question("q_reading", "reading", "Read the chart.", "", kind="reading"),
    ]
    if abstain:
        missing = _missing_cat(inv, cats, word)
        questions.append(_abstain(c, inv, "q_missing", missing,
                                  f"What value does the bar chart show for {missing}?"))
    elif labelled:
        a, b = (i1, i2) if vals[i1] > vals[i2] else (i2, i1)
        if vals[a] == vals[b]:
            a, b = imax, imin
        questions.append(Question("q_diff", "difference",
                                  f"How much higher is the value for {cats[a]} than for {cats[b]}?",
                                  str(vals[a] - vals[b]), canon="number",
                                  fields=[vf(a), vf(b)]))
    else:
        questions.append(Question("q_value2", "lookup",
                                  f"Reading the bar chart, what is the value for {cats[i2]}?",
                                  str(vals[i2]), kind="relaxed", canon="number",
                                  fields=[vf(i2)]))
    data = {"subject": subject, "categories": cats, "values": vals, "tick_step": step,
            "y_max": ymax, "data_labels": labelled}
    return Rendered(c, "bar_chart", questions, data, default_year=YEAR)


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


# ------------------------------------------------------------------ line chart

def _line(rng: random.Random, *, theme: str, scale: float, abstain: bool) -> Rendered:
    inv = Inventor(rng)
    n = rng.choice((4, 6))
    if n == 4:
        xs, word, accept = [f"Q{i + 1}" for i in range(4)], "quarter", [[] for _ in range(4)]
    else:
        s = rng.choice((0, 3, 6))
        xs, word = [MONTHS[i][:3] for i in range(s, s + 6)], "month"
        accept = [[MONTHS[i]] for i in range(s, s + 6)]
    ns = rng.choice((1, 2))
    names = _unique(inv.brand, ns)
    step, nt = rng.choice((10, 20, 50)), 6
    half = step // 2
    while True:
        series = [[_halves(rng, half, 2, 2 * nt) for _ in xs] for _ in names]
        s0 = series[0]
        ok = abs(s0[-1] - s0[0]) >= 2 * step and sorted(s0)[-1] - sorted(s0)[-2] >= step
        if ns == 2:
            ok = ok and all(abs(a - b) >= step for a, b in zip(*series))
        if ok:
            break
    colors = rng.sample(SERIES_COLORS[theme], ns)
    w, h = LOGICAL_SIZES["line_chart"]
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
    for i, x in enumerate(xs):
        c.text(px[i], PLOT[3] + 8, x, size=12, anchor="ma", field=f"x_{i}", value=x)
    for si, (vals, col) in enumerate(zip(series, colors)):
        for i, v in enumerate(vals):
            c.mark(f"pt_{si}_{i}", px[i] - 4.5, y_of(v) - 4.5, px[i] + 4.5, y_of(v) + 4.5,
                   value=str(v), color=col)

    s0 = series[0]
    i1, i2 = rng.sample(range(n), 2)
    peak = s0.index(max(s0))
    trend = "rise" if s0[-1] > s0[0] else "fall"
    acc = ["rose", "risen", "increase", "increased", "up"] if trend == "rise" else \
        ["fell", "fallen", "decrease", "decreased", "down"]
    questions = [
        Question("q_value", "lookup", f"Reading the line chart, what is the value of {names[0]} "
                 f"in {xs[i1]}?", str(s0[i1]), kind="relaxed", canon="number",
                 fields=["leg_0", f"pt_0_{i1}"]),
        Question("q_trend", "trend", f"Between {xs[0]} and {xs[-1]}, did {names[0]} rise or fall?",
                 trend, fields=["leg_0", "x_0", f"x_{n - 1}", "pt_0_0", f"pt_0_{n - 1}"],
                 accept=acc),
        Question("q_peak", "max", f"In which {word} is {names[0]} highest?", xs[peak],
                 fields=["leg_0"] + [f"x_{i}" for i in range(n)] +
                 [f"pt_0_{i}" for i in range(n)], accept=accept[peak]),
        Question("q_reading", "reading", "Read the chart.", "", kind="reading"),
    ]
    if abstain:
        while True:
            missing = inv.brand()
            if missing not in names:
                break
        questions.append(_abstain(c, inv, "q_missing", missing,
                                  f"What is the value of {missing} in {xs[i2]}?"))
    elif ns == 2:
        hi = 0 if series[0][i2] > series[1][i2] else 1
        questions.append(Question("q_compare", "compare",
                                  f"In {xs[i2]}, which series has the higher value?", names[hi],
                                  fields=["leg_0", "leg_1", f"x_{i2}", f"pt_0_{i2}",
                                          f"pt_1_{i2}"]))
    else:
        questions.append(Question("q_value2", "lookup", f"Reading the line chart, what is the "
                                  f"value of {names[0]} in {xs[i2]}?", str(s0[i2]),
                                  kind="relaxed", canon="number", fields=[f"pt_0_{i2}"]))
    data = {"subject": subject, "x": xs, "series": dict(zip(names, series)), "tick_step": step,
            "y_max": step * nt}
    return Rendered(c, "line_chart", questions, data, default_year=YEAR)


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
    w, h = LOGICAL_SIZES["pie_chart"]
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
        Question("q_reading", "reading", "Read the chart.", "", kind="reading"),
    ]
    if abstain:
        missing = _missing_cat(inv, cats, "store")
        questions.append(_abstain(c, inv, "q_missing", missing,
                                  f"What percentage share does {missing} have?"))
    else:
        questions.append(Question("q_count", "count", "How many slices does the pie chart have?",
                                  str(n), canon="number", fields=pcts))
    data = {"subject": subject, "categories": cats, "percent": pct}
    return Rendered(c, "pie_chart", questions, data, default_year=YEAR)


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
        Question("q_reading", "reading", "Read the chart.", "", kind="reading"),
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
    data = {"subject": subject, "categories": cats, "series": names,
            "values": segs, "totals": totals, "tick_step": step, "y_max": step * nt}
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
        Question("q_reading", "reading", "Read the chart.", "", kind="reading"),
    ]
    if abstain:
        missing = _missing_cat(inv, names, "store")
        questions.append(_abstain(c, inv, "q_missing", missing,
                                  f"What is the {yt.lower()} of point {missing}?"))
    else:
        questions.append(Question("q_count", "count", "How many points are plotted?", str(n),
                                  canon="number", fields=[f"pt_{i}_y" for i in range(n)]))
    data = {"x_axis": xt, "y_axis": yt, "x_step": sx, "y_step": sy,
            "points": [{"name": nm, "x": p[0], "y": p[1]} for nm, p in zip(names, pts)]}
    return Rendered(c, "scatter", questions, data, default_year=YEAR)
