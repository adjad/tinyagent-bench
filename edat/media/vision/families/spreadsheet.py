"""Spreadsheet: a grid with column letters, row numbers, a header row and 5-20 invented rows.

Seven templates (expenses, inventory, roster, orders, grades, timesheet, sales) share one
drawing engine and differ in columns; formulas are shown as their values and an optional totals
row is drawn. Every cell is a planted field named like its address (``C7``). Some sheets have
frozen headers (the header row and the label column are set off by a heavy rule), and every
sheet highlights one data row or one cell in yellow ("which row is highlighted?" is answered
with its row number, "which cell is highlighted?" with its reference). A toolbar of labelled
buttons (Bold, Italic, Sort, Filter, Sum, Chart) and the Sheet1 tab are recorded as elements.
Question types: cell, sum, max or min, count, lookup (highlight) (and the reading).

Seven templates keep every template reachable in each theme, scale and abstention slot (the
generator picks template = index % len(TEMPLATES)).
"""
from __future__ import annotations

import random
from datetime import date

from edat.media.common import PRODUCTS, Inventor, money_str
from edat.media.vision.canvas import Canvas
from edat.media.vision.items import ABSTAIN_ANSWER, Question, Rendered

FAMILY = "spreadsheet"
BASE = date(2027, 1, 4)
SPECS = {
    "expenses": dict(label=1, cols=[("Date", "date"), ("Vendor", "merchant"),
                                    ("Category", "cat", ("Travel", "Meals", "Office", "Software")),
                                    ("Paid by", "given"), ("Amount", "money", (4, 900))]),
    "inventory": dict(label=1, cols=[("SKU", "code"), ("Product", "item"), ("Stock", "int", (0, 120)),
                                     ("Unit price", "money", (1, 60)), ("Value", "mul", 2, 3),
                                     ("Reorder", "cat", ("Yes", "No"))]),
    "roster": dict(label=0, cols=[("Name", "name"), ("Team", "cat", ("Alpha", "Beta", "Gamma")),
                                  ("Start", "date"), ("Hours", "int", (10, 40)),
                                  ("Rate", "money", (12, 60)), ("Pay", "mul", 3, 4)]),
    "orders": dict(label=1, cols=[("Order", "code"), ("Customer", "name"), ("Qty", "int", (1, 12)),
                                  ("Total", "money", (10, 900)), ("Shipped", "date"),
                                  ("Region", "cat", ("East", "West", "North", "South"))]),
    "grades": dict(label=0, cols=[("Student", "name"), ("Math", "int", (40, 100)),
                                  ("Science", "int", (40, 100)), ("Art", "int", (40, 100)),
                                  ("Total", "sum", (1, 2, 3))]),
    "timesheet": dict(label=0, cols=[("Employee", "name")] + [(d, "int", (0, 9)) for d in
                                                              ("Mon", "Tue", "Wed", "Thu", "Fri")]
                      + [("Total", "sum", (1, 2, 3, 4, 5))]),
    "sales": dict(label=0, cols=[("Rep", "name"), ("Region", "town"), ("Units", "int", (5, 400)),
                                 ("Revenue", "money", (200, 9000)),
                                 ("Quarter", "cat", ("Q1", "Q2", "Q3", "Q4"))]),
}
TEMPLATES = tuple(SPECS)
LOGICAL_SIZES = {t: (1000, 780) for t in TEMPLATES}
ROW_H, TOP = 28, 104
HIGHLIGHT = {"light": (255, 232, 140), "dark": (96, 78, 10)}


def render(rng: random.Random, *, template: str, theme: str, scale: float,
           abstain: bool) -> Rendered:
    if template not in SPECS:
        raise ValueError(f"unknown template {template!r}")
    return _sheet(rng, template, theme, scale, abstain)


def _tail(c, rng, abstain, qs, normal, blur, absent):
    if not abstain:
        qs.append(normal)
        return
    if rng.random() < 0.5:
        qid, text, name, value, kind = absent
        c.absent(name, value, kind)
        qs.append(Question(qid, "absence", text, ABSTAIN_ANSWER, kind="abstain", fields=[name]))
    else:
        c.blur_field(blur, radius=7)
        qs.append(Question(normal.qid, "absence", normal.question, ABSTAIN_ANSWER,
                           kind="abstain", fields=[blur]))


def _gen(kind, arg, rng, inv, n):
    """n raw values for a base column: money in cents, dates as date, others as str or int."""
    if kind == "int":
        return [rng.randint(*arg) for _ in range(n)]
    if kind == "money":
        return [round(rng.uniform(*arg) * 100) for _ in range(n)]
    if kind == "date":
        return [inv.day(BASE, 330) for _ in range(n)]
    if kind == "cat":
        pool = list(arg)
        return [rng.choice(pool) for _ in range(n)]
    out = []
    while len(out) < n:                   # unique text so a row can be named by its label
        v = {"name": inv.person, "merchant": inv.merchant, "item": lambda: rng.choice(PRODUCTS),
             "code": lambda: inv.code(2, 4), "given": inv.given, "town": inv.town}[kind]()
        if kind in ("given", "town") or v not in out:
            out.append(v)
    return out


def _fmt(kind, v) -> tuple[str, str]:
    """(drawn text, planted value) for one cell."""
    if kind == "money":
        return money_str(v / 100), f"{v / 100:.2f}"
    if kind == "date":
        return v.strftime("%m/%d/%Y"), v.isoformat()
    if kind == "int":
        return str(v), str(v)
    return v, v


def _sheet(rng, template, theme, scale, abstain):
    inv = Inventor(rng)
    spec = SPECS[template]
    n = rng.randint(5, 20)
    cols = []                                   # dicts: head, kind (display kind), vals
    for col in spec["cols"]:
        head, kind = col[0], col[1]
        arg = col[2] if len(col) > 2 else None
        cols.append({"head": head, "kind": kind, "arg": col, "base": kind not in ("mul", "sum")})
        if cols[-1]["base"]:
            cols[-1]["vals"] = _gen(kind, arg, rng, inv, n)
    label = spec["label"]
    # one numeric base column gets a strictly highest (or lowest) row, so the answer is unique
    base_num = [i for i, k in enumerate(cols) if k["kind"] in ("int", "money")]
    met = rng.choice(base_num)
    vals = cols[met]["vals"]
    big = cols[met]["kind"] == "money"
    lowest = min(vals)
    mode = "min" if rng.random() < 0.4 and lowest >= (300 if big else 3) else "max"
    win = rng.randrange(n)
    if mode == "max":
        vals[win] = max(vals) + (rng.randint(100, 900) if big else rng.randint(1, 5))
    else:
        vals[win] = lowest - (rng.randint(50, 250) if big else rng.randint(1, 2))
    for k in cols:                               # formulas last: they read the final base values
        if not k["base"]:
            a = k["arg"]
            if k["kind"] == "mul":
                x, y = cols[a[2]], cols[a[3]]
                k["vals"] = [p * q for p, q in zip(x["vals"], y["vals"])]
                k["kind"] = "money"
            else:
                k["vals"] = [sum(cols[j]["vals"][r] for j in a[2]) for r in range(n)]
                k["kind"] = "int"
    totals = rng.random() < 0.5
    nc = len(cols)
    letters = [chr(65 + i) for i in range(nc)]
    cells = [[_fmt(k["kind"], k["vals"][r]) for k in cols] for r in range(n)]

    w, h = LOGICAL_SIZES[template]
    c = Canvas(w, h, scale=scale, theme=theme)
    widths = []
    for j, k in enumerate(cols):
        wmax = max([c.text_width(k["head"], 13)] + [c.text_width(cells[r][j][0], 13)
                                                    for r in range(n)])
        if totals and k["kind"] in ("int", "money"):
            tot = sum(k["vals"])
            wmax = max(wmax, c.text_width(_fmt(k["kind"], tot)[0], 13))
        widths.append(max(wmax + 40, 96))
    xs = [44]
    for wd in widths:
        xs.append(xs[-1] + wd)
    ncell_rows = n + 1 + (1 if totals else 0)

    c.rect(0, 0, w, 40, fill="surface")
    bx = 12
    for tb_label in ("Bold", "Italic", "Sort", "Filter", "Sum", "Chart"):
        bw = max(52.0, c.text_width(tb_label, 12) + 24)
        c.rect(bx, 7, bx + bw, 33, fill="bg", outline="border", radius=6)
        c.text_center(bx + bw / 2, 20, tb_label, size=12, anchor="lm")
        c.element(tb_label, bx, 7, bx + bw, 33, role="button")
        bx += bw + 8
    c.rect(0, 40, w, 80, fill="bg")
    c.line(0, 79, w, 79, color="grid")
    c.rect(10, 47, 70, 73, fill="bg", outline="border", radius=4)
    c.text(22, 53, "A1", size=13, color="muted")
    c.text(88, 53, "fx", size=13, color="muted")
    c.rect(0, 80, w, TOP, fill="surface")
    for j in range(nc):
        c.text_center((xs[j] + xs[j + 1]) / 2, 85, letters[j], size=12, color="muted",
                      field=f"col_{j}", value=letters[j])

    def row_chrome(r, y, band):
        if band:
            c.rect(44, y, xs[-1], y + ROW_H, fill="surface")
        c.rect(0, y, 44, y + ROW_H, fill="surface")
        c.line(0, y + ROW_H, xs[-1], y + ROW_H, color="grid")
        for x in xs:
            c.line(x, y, x, y + ROW_H, color="grid")
        c.text_center(22, y + 8, str(r), size=11, color="muted", field=f"rn_{r}", kind="number",
                      value=str(r))

    def put(j, y, text, dk, **kw):
        if dk in ("int", "money"):
            return c.text_right(xs[j + 1] - 14, y + 7, text, size=13, **kw)
        return c.text(xs[j] + 14, y + 7, text, size=13, **kw)

    frozen = rng.random() < 0.6
    hl_cell = rng.random() < 0.4
    hr_row = rng.randrange(n)
    hc_col = rng.randrange(nc)
    hl = HIGHLIGHT[theme]
    y = TOP
    row_chrome(1, y, True)
    for j, k in enumerate(cols):
        put(j, y, k["head"], k["kind"], color="accent")
    for r in range(n):
        y = TOP + (r + 1) * ROW_H
        row_chrome(r + 2, y, r % 2 == 1)
        if r == hr_row:
            if hl_cell:
                c.rect(xs[hc_col] + 1, y + 1, xs[hc_col + 1], y + ROW_H, fill=hl)
                c.rect(xs[hc_col] + 1, y + 1, xs[hc_col + 1], y + ROW_H, outline="accent", width=2)
            else:
                c.rect(45, y + 1, xs[-1], y + ROW_H, fill=hl)
        for j, k in enumerate(cols):
            txt, val = cells[r][j]
            put(j, y, txt, k["kind"], field=f"{letters[j]}{r + 2}",
                kind="text" if k["kind"] in ("code", "name", "merchant", "item", "given", "town",
                                             "cat") else k["kind"], value=val)
    if totals:
        y = TOP + (n + 1) * ROW_H
        row_chrome(n + 2, y, False)
        c.text(xs[0] + 14, y + 7, "Total", size=13, color="accent")
        for j, k in enumerate(cols):
            if k["kind"] in ("int", "money"):
                txt, val = _fmt(k["kind"], sum(k["vals"]))
                put(j, y, txt, k["kind"], field=f"tot_{letters[j]}", kind=k["kind"], value=val)
    if frozen:
        c.line(0, TOP + ROW_H, xs[-1], TOP + ROW_H, color="muted", width=2)
        c.line(xs[1], TOP, xs[1], TOP + ncell_rows * ROW_H, color="muted", width=2)
    c.text(16, h - 28, "Sheet1", size=12, color="muted")
    c.element("Sheet1", 8, h - 34, 84, h - 6, role="tab")

    # ---- questions
    def cell_name(j, r):
        return f"{letters[j]}{r + 2}"

    nums = [j for j, k in enumerate(cols) if k["kind"] in ("int", "money")]
    cats = [j for j, k in enumerate(cols) if k["kind"] == "cat"]
    j_sum = rng.choice(nums)
    total = sum(cols[j_sum]["vals"])
    sum_money = cols[j_sum]["kind"] == "money"
    j_cnt = rng.choice(cats) if cats else rng.choice(nums)
    if cats:
        v = rng.choice(cols[j_cnt]["vals"])
        cnt = sum(1 for x in cols[j_cnt]["vals"] if x == v)
        cq = f'How many rows have {cols[j_cnt]["head"]} equal to "{v}"?'
    else:
        vs = sorted(cols[j_cnt]["vals"])
        thr = vs[len(vs) // 2]
        cnt = sum(1 for x in cols[j_cnt]["vals"] if x > thr)
        shown = _fmt(cols[j_cnt]["kind"], thr)[0]
        cq = f'How many rows have {cols[j_cnt]["head"]} greater than {shown}?'
    metric_h = cols[met]["head"]
    mq = (f"Which {cols[label]['head']} has the {'highest' if mode == 'max' else 'lowest'} "
          f"{metric_h}?")
    use_extreme = rng.random() < 0.5
    q_extreme = Question("q_extreme", mode, mq, cols[label]["vals"][win],
                         fields=[cell_name(met, r) for r in range(n)] + [cell_name(label, win)])
    q_count = Question("q_count", "count", cq, str(cnt), canon="number",
                       fields=[cell_name(j_cnt, r) for r in range(n)])
    rows_f = [f"rn_{r}" for r in range(n + 2)]
    if hl_cell:
        ref = f"{letters[hc_col]}{hr_row + 2}"
        q_hl = Question("q_highlight", "lookup", "Which cell is highlighted in yellow? Give its "
                        "cell reference, such as B4.", ref,
                        fields=[f"col_{j}" for j in range(nc)] + [f"rn_{hr_row + 2}"])
    else:
        q_hl = Question("q_highlight", "lookup", "Which row is highlighted in yellow? Give its "
                        "row number.", str(hr_row + 2), canon="number", fields=[f"rn_{hr_row + 2}"])
    qs = [
        Question("q_sum", "sum", f"What is the total of column {letters[j_sum]}?",
                 f"{total / 100:.2f}" if sum_money else str(total),
                 canon="money" if sum_money else "number",
                 fields=[cell_name(j_sum, r) for r in range(n)]),
        q_extreme if use_extreme else q_count,
        q_hl,
    ]
    busy = {j_sum} | ({met} if use_extreme else {j_cnt})
    free = [(j, r) for j in range(nc) for r in range(n)
            if j not in busy and not (use_extreme and j == label and r == win)]
    if not free:
        free = [(j, r) for j in range(nc) for r in range(n) if j == label and r != win]
    jt, rt = rng.choice(free)
    kt = cols[jt]["kind"]
    tcanon = {"int": "number", "money": "money", "date": "date"}.get(kt, "text")
    tname = cell_name(jt, rt)
    _tail(c, rng, abstain, qs,
          Question("q_cell", "cell", f"What is in cell {tname}?", cells[rt][jt][1], canon=tcanon,
                   fields=[tname]),
          tname, (f"q_cell_x", f"What is in cell {chr(65 + nc)}2?", f"cell_{chr(65 + nc)}2",
                  inv.code(3, 3), "text"))
    qs.append(Question("q_reading", "reading", "Read the screen.", "", kind="reading"))
    data = {"template": template, "headers": [k["head"] for k in cols],
            "rows": [[cells[r][j][1] for j in range(nc)] for r in range(n)],
            "totals_row": totals, "highest_or_lowest": mode}
    return Rendered(c, template, qs, data, default_year=BASE.year)
