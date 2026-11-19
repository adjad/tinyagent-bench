"""Tables: 3 to 40 rows, merged group headers, totals rows, zebra striping (spec 05 §7.1).

Seven templates: plain, zebra, grouped (merged group headers), totals, wide, status and long
(24-40 rows). Every table has a label column, one category column, two to four numeric columns
and, in four templates, a status column. Every cell is a planted text field.
Questions (4 scored plus the reading): a cell lookup, a row count, a column sum and a filter
("how many rows have Status = Late", or "which item has the highest X among category Y").
On abstention items the cell lookup is replaced by a question about a blurred cell or a row
that is not in the table.
Question types: cell, count, sum, lookup, max, absence, reading.
"""
from __future__ import annotations

import random

from edat.media.common import Inventor, MONTHS, canon_text, money_str
from edat.media.vision.canvas import Canvas
from edat.media.vision.items import ABSTAIN_ANSWER, Question, Rendered

FAMILY = "tables"
TEMPLATES = ("plain", "zebra", "grouped", "totals", "wide", "status", "long")
YEAR = 2027
OPTS = {
    "plain": dict(size=(600, 440), rows=(3, 12), cols=("label", "cat", "n0", "n1"),
                  zebra=False, group=False, totals=False, vlines=False),
    "zebra": dict(size=(640, 600), rows=(8, 20), cols=("label", "cat", "n0", "n1", "status"),
                  zebra=True, group=False, totals=False, vlines=False),
    "grouped": dict(size=(680, 520), rows=(5, 14),
                    cols=("label", "cat", "n0", "n1", "status"),
                    zebra=True, group=True, totals=False, vlines=True),
    "totals": dict(size=(620, 480), rows=(4, 14), cols=("label", "cat", "n0", "n1"),
                   zebra=True, group=False, totals=True, vlines=False),
    "wide": dict(size=(980, 540), rows=(5, 16),
                 cols=("label", "cat", "n0", "n1", "n2", "n3", "status"),
                 zebra=True, group=True, totals=True, vlines=True),
    "status": dict(size=(640, 600), rows=(6, 20), cols=("label", "cat", "n0", "status"),
                   zebra=False, group=False, totals=False, vlines=False),
    "long": dict(size=(660, 1040), rows=(24, 40), cols=("label", "cat", "n0", "n1", "status"),
                 zebra=True, group=False, totals=False, vlines=False),
}
LOGICAL_SIZES = {t: OPTS[t]["size"] for t in TEMPLATES}
SCHEMAS = (
    dict(title="order", label="Order", maker="code", cat=("Region", ("East", "West", "North", "South")),
         status=("Status", ("On time", "Late", "Cancelled")),
         nums=(("Qty", "int", 1, 60), ("Total", "money", 20, 900), ("Items", "int", 1, 9),
               ("Fee", "money", 2, 40))),
    dict(title="stock", label="Item", maker="brand", cat=("Aisle", ("Dry", "Fresh", "Frozen", "Home")),
         status=("Level", ("In stock", "Low", "Out")),
         nums=(("Units", "int", 0, 500), ("Price", "money", 2, 120), ("Sold", "int", 0, 400),
               ("Cost", "money", 1, 90))),
    dict(title="staff", label="Name", maker="person", cat=("Team", ("Alpha", "Beta", "Gamma", "Delta")),
         status=("Status", ("Active", "On leave", "Remote")),
         nums=(("Hours", "int", 10, 40), ("Rate", "money", 12, 60), ("Tasks", "int", 0, 80),
               ("Bonus", "money", 0, 300))),
    dict(title="parcel", label="Parcel", maker="code", cat=("Depot", ("North", "South", "East", "West")),
         status=("State", ("Delivered", "Late", "Returned")),
         nums=(("Weight", "int", 1, 40), ("Fee", "money", 3, 80), ("Days", "int", 1, 9),
               ("Refund", "money", 0, 60))),
)


def render(rng: random.Random, *, template: str, theme: str, scale: float,
           abstain: bool) -> Rendered:
    if template not in OPTS:
        raise ValueError(f"unknown template {template!r}")
    return _table(rng, template, theme, scale, abstain)


def _unique_labels(maker, n: int, inv: Inventor) -> list[str]:
    out: list[str] = []
    while len(out) < n:
        s = {"code": lambda: inv.code(2, 4), "brand": inv.brand, "person": inv.person}[maker]()
        if all(s.lower() not in o.lower() and o.lower() not in s.lower() for o in out):
            out.append(s)
    return out


def _fmt(kind: str, v: int) -> tuple[str, str]:
    if kind == "money":                       # cents
        return money_str(v / 100), f"{v / 100:.2f}"
    return str(v), str(v)


def _table(rng: random.Random, template: str, theme: str, scale: float, abstain: bool) -> Rendered:
    inv = Inventor(rng)
    o = OPTS[template]
    sch = rng.choice(SCHEMAS)
    n = rng.randint(*o["rows"])
    labels = _unique_labels(sch["maker"], n, inv)
    cols: list[dict] = []
    for key in o["cols"]:
        if key == "label":
            cols.append(dict(key=key, head=sch["label"], kind="text", vals=labels))
        elif key == "cat":
            cols.append(dict(key=key, head=sch["cat"][0], kind="text",
                             vals=[rng.choice(sch["cat"][1]) for _ in range(n)]))
        elif key == "status":
            pool = sch["status"][1]
            cols.append(dict(key=key, head=sch["status"][0], kind="text",
                             vals=[rng.choice(pool) for _ in range(n)]))
        else:
            head, kind, lo, hi = sch["nums"][int(key[1])]
            while True:
                vals = [rng.randint(lo, hi) if kind == "int" else round(rng.uniform(lo, hi) * 100)
                        for _ in range(n)]
                if len(set(vals)) > min(n, 2):
                    break
            cols.append(dict(key=key, head=head, kind=kind, vals=vals))
    nc = len(cols)
    num_idx = [j for j, k in enumerate(cols) if k["kind"] != "text"]
    cat_idx = [j for j, k in enumerate(cols) if k["key"] in ("cat", "status")]

    w, h = o["size"]
    c = Canvas(w, h, scale=scale, theme=theme)
    month = rng.randrange(12)
    c.text(24, 18, f"{inv.brand()} {sch['title']} report", size=18, bold=True)
    c.text(24, 44, f"Figures for {MONTHS[month]} {YEAR}", size=11, color="muted")

    # --- layout
    total_row = o["totals"]
    top = 74
    nrows = n + 1 + (1 if o["group"] else 0) + (1 if total_row else 0)
    rh = max(22.0, min(28.0, (h - top - 20) / nrows))
    fs = 12 if rh >= 24 else 11
    cells = [[_fmt(k["kind"], k["vals"][r]) if k["kind"] != "text" else (k["vals"][r], k["vals"][r])
              for k in cols] for r in range(n)]
    tot = {j: sum(cols[j]["vals"]) for j in num_idx}
    minw = []
    for j, k in enumerate(cols):
        ws = [c.text_width(k["head"], fs)] + [c.text_width(cells[r][j][0], fs)
                                                    for r in range(n)]
        if total_row and j in tot:
            ws.append(c.text_width(_fmt(k["kind"], tot[j])[0], fs))
        minw.append(max(ws) + 24)
    avail = w - 48
    extra = max(0.0, (avail - sum(minw)) / nc)
    widths = [m + extra for m in minw]
    xs = [24.0]
    for wd in widths:
        xs.append(xs[-1] + wd)
    right = xs[-1]

    y = top
    groups: list[tuple[str, int, int]] = []
    if o["group"]:
        splits = _group_spans(cols)
        for gi, (name, j0, j1) in enumerate(splits):
            groups.append((name, j0, j1))
            c.rect(xs[j0] + 1, y, xs[j1] - 1, y + rh - 2, fill="accent")
            c.text_center((xs[j0] + xs[j1]) / 2, y + (rh - 2) / 2, name, size=fs, bold=False,
                          color="accent_fg", anchor="mm", field=f"grp_{gi}", value=name)
        y += rh
    c.rect(24, y, right, y + rh, fill="border")
    for j, k in enumerate(cols):
        if k["kind"] == "text":
            c.text(xs[j] + 10, y + rh / 2, k["head"], size=fs, anchor="lm")
        else:
            c.text(xs[j + 1] - 10, y + rh / 2, k["head"], size=fs, anchor="rm")
    y += rh
    body_top = y
    for r in range(n):
        yy = body_top + r * rh
        if o["zebra"] and r % 2:
            c.rect(24, yy, right, yy + rh, fill="surface")
        elif o["zebra"] is False and r > 0:
            c.line(24, yy, right, yy, color="grid")
        for j, k in enumerate(cols):
            s, v = cells[r][j]
            name = f"row_{r}" if j == 0 else f"cell_{r}_{j}"
            if k["kind"] == "text":
                c.text(xs[j] + 10, yy + rh / 2, s, size=fs, anchor="lm", field=name, value=v)
            else:
                c.text(xs[j + 1] - 10, yy + rh / 2, s, size=fs, anchor="rm", field=name,
                       kind=k["kind"] if k["kind"] == "money" else "number", value=v)
    y = body_top + n * rh
    c.line(24, y, right, y, color="muted", width=1.5)
    if o["vlines"]:
        for j in range(1, nc):
            c.line(xs[j], top + (rh if o["group"] else 0), xs[j], y, color="grid")
    if total_row:
        c.rect(24, y + 1, right, y + rh, fill="border")
        c.text(xs[0] + 10, y + 1 + (rh - 1) / 2, "Total", size=fs, anchor="lm")
        for j in num_idx:
            s, v = _fmt(cols[j]["kind"], tot[j])
            c.text(xs[j + 1] - 10, y + 1 + (rh - 1) / 2, s, size=fs, anchor="rm",
                   field=f"tot_{j}", kind="money" if cols[j]["kind"] == "money" else "number",
                   value=v)
        y += rh

    # --- questions
    col_fields = lambda j: [f"row_{r}" if j == 0 else f"cell_{r}_{j}" for r in range(n)]  # noqa: E731
    canon_of = lambda j: "money" if cols[j]["kind"] == "money" else "number"  # noqa: E731
    lbl = cols[0]["head"].lower()
    cell = lambda r, j: f"row_{r}" if j == 0 else f"cell_{r}_{j}"  # noqa: E731

    j_sum = rng.choice(num_idx)
    total = tot[j_sum]
    q_sum = Question("q_sum", "sum", f"What is the sum of the {cols[j_sum]['head']} column "
                     "(the data rows only)?", f"{total / 100:.2f}" if canon_of(j_sum) == "money"
                     else str(total), canon=canon_of(j_sum), fields=col_fields(j_sum))
    q_count = Question("q_count", "count", "How many data rows does the table have (not counting "
                       "the header rows or the Total row)?", str(n), canon="number",
                       fields=col_fields(0))
    q_filter, used_filter_cols = _filter_question(rng, cols, labels, n, cat_idx, num_idx, j_sum,
                                                  lbl, cell)
    # the cell lookup: any non-label column
    jc = rng.choice([j for j in range(1, nc)])
    rc = rng.randrange(n)
    q_cell = Question("q_cell", "cell", f"In the table, what is the {cols[jc]['head']} for "
                      f"{labels[rc]}?", cells[rc][jc][1],
                      canon=canon_of(jc) if cols[jc]["kind"] != "text" else "text",
                      fields=[f"row_{rc}", cell(rc, jc)])
    questions = [q_cell, q_count, q_sum, q_filter]
    if abstain:
        free = [j for j in num_idx if j != j_sum and j not in used_filter_cols]
        if free and rng.random() < 0.5:
            jb, rb = rng.choice(free), rng.randrange(n)
            c.blur_field(cell(rb, jb), radius=7)
            questions[0] = Question("q_cell_abstain", "absence", f"What is the "
                                    f"{cols[jb]['head']} for {labels[rb]}?", ABSTAIN_ANSWER,
                                    kind="abstain", fields=[cell(rb, jb)])
        else:
            questions[0] = _missing_row(c, inv, labels, sch["maker"], cols[num_idx[0]]["head"])
    questions.append(Question("q_reading", "reading", "Read the table.", "", kind="reading"))
    data = {"columns": [k["head"] for k in cols], "rows": labels,
            "values": [[cells[r][j][1] for j in range(nc)] for r in range(n)],
            "groups": [g[0] for g in groups], "totals": ({cols[j]["head"]: _fmt(cols[j]["kind"], tot[j])[1]
                                                          for j in num_idx} if total_row else None),
            "zebra": o["zebra"], "month": MONTHS[month], "year": YEAR}
    return Rendered(c, template, questions, data, default_year=YEAR)


def _group_spans(cols: list[dict]) -> list[tuple[str, int, int]]:
    """Merged header spans covering every column: (title, first column, one past the last)."""
    nc = len(cols)
    num = [j for j, k in enumerate(cols) if k["kind"] != "text"]
    first_num = num[0]
    spans = [("Details", 0, first_num), ("Figures", first_num, num[-1] + 1)]
    if num[-1] + 1 < nc:
        spans.append(("State", num[-1] + 1, nc))
    return [s for s in spans if s[2] > s[1]]


def _filter_question(rng, cols, labels, n, cat_idx, num_idx, j_sum, lbl, cell):
    """Count rows matching a category value, or the top row of one category (unique)."""
    want_max = rng.random() < 0.5
    cands = []
    if want_max:
        for jc in cat_idx:
            if cols[jc]["key"] != "cat":
                continue
            for v in sorted(set(cols[jc]["vals"])):
                rows = [r for r in range(n) if cols[jc]["vals"][r] == v]
                if len(rows) < 2:
                    continue
                for jn in num_idx:
                    vals = sorted((cols[jn]["vals"][r] for r in rows), reverse=True)
                    if vals[0] != vals[1]:
                        cands.append((jc, v, jn, rows))
    if cands:
        jc, v, jn, rows = rng.choice(cands)
        best = max(rows, key=lambda r: cols[jn]["vals"][r])
        q = Question("q_filter", "max", f"Among the rows where {cols[jc]['head']} is {v}, which "
                     f"{lbl} has the highest {cols[jn]['head']}?", cols[0]["vals"][best],
                     fields=[cell(r, 0) for r in range(n)] + [cell(r, jc) for r in range(n)] +
                     [cell(r, jn) for r in range(n)])
        return q, {jc, jn}
    jc = rng.choice(cat_idx)
    v = rng.choice(cols[jc]["vals"])
    cnt = sum(1 for x in cols[jc]["vals"] if x == v)
    q = Question("q_filter", "count", f"How many rows have {cols[jc]['head']} = {v}?", str(cnt),
                 canon="number", fields=[cell(r, jc) for r in range(n)])
    return q, {jc}


def _missing_row(c: Canvas, inv: Inventor, labels: list[str], maker: str, head: str) -> Question:
    drawn = [canon_text(r.text) for r in c.runs]
    while True:
        s = _unique_labels(maker, 1, inv)[0]
        if not any(canon_text(s) in t for t in drawn):
            break
    c.absent("missing", s)
    return Question("q_row_abstain", "absence", f"What is the {head} for {s}?", ABSTAIN_ANSWER,
                    kind="abstain", fields=["missing"])
