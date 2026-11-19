"""V7 image reasoning an assistant needs (spec 05 §7.7).

``python -m edat.media.vision.reasoning generate --skill counting --split dev --out DIR``

Five skills, each generated from a seed with the answer known by construction
and re-derived from the recorded pixel boxes before an item is written:

=================  =====  ==========================================================
skill              cases  design
=================  =====  ==========================================================
``counting``       1,000  0–30 shapes of one kind, alone or among clutter; to-do
                          lists (how many ticked); app grids (how many badges).
                          Exact count; error recorded by size of count
``spatial``        1,000  left/right, above/below and nearest on shape scenes;
                          order in a row; which box contains a shape; toolbar and
                          form layouts ("which button is right of Share")
``multi_image``    1,000  2–4 images per request: higher receipt total, earliest
                          flight, which image mentions a word, what changed
                          between two screens (about 2,750 images)
``chart_trend``      500  line charts: rising or falling, first crossing month,
                          peak month, change between two labelled months
``locate``         2,000  "where is the Save button?" on V1 screens with recorded
                          element boxes: a point inside the box, or a box with
                          IoU ≥ 0.5 (one in four asks for a box)
=================  =====  ==========================================================

Answers to point and box questions are pixel coordinates in the image exactly
as sent (``x,y`` or ``x0,y0,x1,y1``). A model that answers in another space
(0–1 or 0–1000) is converted by the runner from the model's declared
coordinate space, never guessed from the numbers.

Items use the schema ``edat.media.vision-reason/1``: a list of images, one
question, and the truth the answer was derived from.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from edat.media.common import (EVENT_WORDS, MONTHS, PRODUCTS, Inventor, canon_number, canon_text,
                               money_str, rng_for, sha256_bytes, time_12h)
from edat.media.vision import generate as gen
from edat.media.vision.canvas import Canvas, render_stamp
from edat.media.vision.score import extract_answer, is_abstention, score_answer
from edat.media.vision.validate import ink_stats

SCHEMA = "edat.media.vision-reason/1"
SKILL_COUNTS = {"counting": 1000, "spatial": 1000, "multi_image": 1000, "chart_trend": 500,
                "locate": 2000}
COUNT_BUCKETS = ((0, 0), (1, 5), (6, 10), (11, 20), (21, 30))
IOU_PASS = 0.5
LOCATE_FAMILIES = ("app_screens", "web_pages", "calendar", "spreadsheet")
LOCATE_ROLES = ("button", "toggle", "tab", "link", "field", "icon", "menu_item", "checkbox")
POINT_INSTRUCTION = ("Reply with one point as x,y in pixels of the image as given "
                     "(0,0 is the top-left corner), and nothing else.")
BOX_INSTRUCTION = ("Reply with one box as x0,y0,x1,y1 in pixels of the image as given "
                   "(0,0 is the top-left corner), and nothing else.")
ANSWER_ONLY = "Reply with the answer only, no explanation."
MULTI_PREAMBLE = "The images are numbered 1 to {n} in the order given."

COLOURS = {"red": (214, 40, 40), "blue": (36, 96, 224), "green": (24, 150, 64),
           "orange": (238, 130, 10), "purple": (136, 56, 196)}
SHAPES = ("circle", "square", "triangle", "star", "diamond")
ORDINALS = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth")
NUMBER_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
                "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
                "seventeen", "eighteen", "nineteen", "twenty")
BUTTONS = ("Save", "Share", "Print", "Undo", "Redo", "Export", "Delete", "Copy", "Paste",
           "Rename", "Archive", "Send")
FORM_FIELDS = ("Name", "Email", "Phone", "City", "Company", "Notes", "Country", "Title")
SETTINGS_ROWS = ("Wi-Fi", "Bluetooth", "Notifications", "Dark mode", "Location", "Auto-lock",
                 "Do not disturb", "Low power", "Hotspot", "Siri suggestions")
SERIES_NAMES = ("North", "South", "Online", "Store", "Visits", "Orders")


@dataclass
class ReasonItem:
    item_id: str
    skill: str
    template: str
    images: list                # Pillow images, in request order
    question: dict
    truth: dict
    default_year: int | None = None


# ---------------------------------------------------------------- scoring

_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def parse_numbers(text: str, n: int) -> list[float] | None:
    vals = [float(v) for v in _NUM.findall(text or "")]
    return vals[:n] if len(vals) >= n else None


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def count_bucket(n: int) -> str:
    for lo, hi in COUNT_BUCKETS:
        if lo <= n <= hi:
            return f"{lo}" if lo == hi else f"{lo}-{hi}"
    return ">30"


_WORD_NUM = {w: i for i, w in enumerate(NUMBER_WORDS)}


def _count_value(text: str) -> int | None:
    num = canon_number(text)
    if num is not None:
        try:
            return int(float(num))
        except ValueError:
            return None
    return _WORD_NUM.get(canon_text(text).split(" ")[0]) if text else None


def score_question(q: dict, output: str | None, *, default_year: int | None = None) -> dict:
    """Score one V7 answer. Returns ``{score, passed, extracted, ...}``; blank scores 0."""
    kind = q["kind"]
    extracted = extract_answer(output)
    res = {"qid": q["qid"], "kind": kind, "extracted": extracted, "score": 0.0, "passed": False}
    if not extracted or is_abstention(extracted):
        res["abstained"] = bool(extracted)
        return res
    if kind == "count":
        got, want = _count_value(extracted), int(q["answer"])
        res.update(pred=got, gold=want, bucket=count_bucket(want),
                   abs_error=None if got is None else abs(got - want))
        res["passed"] = got == want
    elif kind == "point":
        pt = parse_numbers(extracted, 2)
        b = q["target_box"]
        res["pred"] = pt
        res["passed"] = bool(pt) and b[0] <= pt[0] < b[2] and b[1] <= pt[1] < b[3]
    elif kind == "box":
        bx = parse_numbers(extracted, 4)
        v = iou(bx, q["target_box"]) if bx and bx[2] > bx[0] and bx[3] > bx[1] else 0.0
        res.update(pred=bx, iou=round(v, 4))
        res["passed"] = v >= IOU_PASS
    else:
        if q.get("canon") == "number":            # "the second image", "two" -> 2
            words = canon_text(extracted).replace("image", " ").split()
            for w in words:
                if w in ORDINALS or w in _WORD_NUM:
                    output = str(ORDINALS.index(w) + 1 if w in ORDINALS else _WORD_NUM[w])
                    break
        s = score_answer(q, output, default_year=default_year)
        res.update(pred=s.pred, gold=s.gold)
        res["passed"] = s.passed
    res["score"] = 1.0 if res["passed"] else 0.0
    return res


def prompt_for(q: dict, n_images: int = 1) -> str:
    head = MULTI_PREAMBLE.format(n=n_images) + " " if n_images > 1 else ""
    tail = {"point": POINT_INSTRUCTION, "box": BOX_INSTRUCTION}.get(q["kind"], ANSWER_ONLY)
    return f"{head}{q['question']}\n{tail}"


def _q(qid, qtype, question, answer, *, kind="field", canon="text", accept=(), **extra) -> dict:
    return {"qid": qid, "qtype": qtype, "question": question, "answer": str(answer), "kind": kind,
            "canon": canon, "fields": [], "accept": list(accept), **extra}


# ---------------------------------------------------------------- drawing helpers

def _variant(index: int, n_templates: int) -> tuple[str, int]:
    k = index // n_templates
    return ("light", "dark")[k % 2], (1, 2)[(k // 2) % 2]


def _star(cx, cy, r):
    pts = []
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.45
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return pts


def draw_shape(c: Canvas, shape: str, colour: str, cx: float, cy: float, size: float) -> list[int]:
    r = size / 2
    rgb = COLOURS[colour]
    if shape == "circle":
        c.ellipse(cx - r, cy - r, cx + r, cy + r, fill=rgb)
    elif shape == "square":
        c.rect(cx - r * 0.85, cy - r * 0.85, cx + r * 0.85, cy + r * 0.85, fill=rgb)
    elif shape == "triangle":
        c.polygon([(cx, cy - r), (cx + r, cy + r * 0.8), (cx - r, cy + r * 0.8)], fill=rgb)
    elif shape == "star":
        c.polygon(_star(cx, cy, r), fill=rgb)
    elif shape == "diamond":
        c.polygon([(cx, cy - r), (cx + r * 0.8, cy), (cx, cy + r), (cx - r * 0.8, cy)], fill=rgb)
    else:
        raise ValueError(shape)
    return [c.px(cx - r), c.px(cy - r), c.px(cx + r), c.px(cy + r)]


def _plural(shape: str) -> str:
    return shape + "s"


def _label(obj) -> str:
    return f"{obj['colour']} {obj['shape']}"


def _accept_label(lbl: str) -> list[str]:
    return [f"the {lbl}", f"a {lbl}"]


def _field_ok(c: Canvas, name: str) -> bool:
    f = c.fields[name]
    if not f.bbox or f.bbox[2] > c.size[0] or f.bbox[3] > c.size[1]:
        return False
    _, _, ratio = ink_stats(c.image, f.bbox, f.fg)
    return ratio >= 3.0 and c.crop_sha256(f.bbox) == f.crop_sha256


# ---------------------------------------------------------------- counting

COUNT_TEMPLATES = ("shapes", "shapes_clutter", "list", "badges")


def _cells(rng, cols, rows, n):
    cells = [(i, j) for j in range(rows) for i in range(cols)]
    return rng.sample(cells, n)


def build_counting(index: int, *, seed: str) -> ReasonItem:
    rng = rng_for(seed, "v7", "counting", index)
    template = COUNT_TEMPLATES[index % len(COUNT_TEMPLATES)]
    theme, dpr = _variant(index, len(COUNT_TEMPLATES))
    inv = Inventor(rng)
    if template.startswith("shapes"):
        cols, rows, cell = 10, 6, 60
        c = Canvas(cols * cell + 40, rows * cell + 80, scale=dpr, theme=theme)
        target_colour, target_shape = rng.choice(list(COLOURS)), rng.choice(SHAPES)
        n = rng.randint(0, 30)
        clutter = rng.randint(6, min(28, cols * rows - n)) if template == "shapes_clutter" else 0
        cells = _cells(rng, cols, rows, n + clutter)
        objs = []
        for k, (i, j) in enumerate(cells):
            if k < n:
                colour, shape = target_colour, target_shape
            else:   # distractors share the colour or the shape, never both
                if rng.random() < 0.5:
                    colour = target_colour
                    shape = rng.choice([s for s in SHAPES if s != target_shape])
                else:
                    shape = target_shape
                    colour = rng.choice([x for x in COLOURS if x != target_colour])
            cx = 20 + i * cell + cell / 2 + rng.uniform(-6, 6)
            cy = 60 + j * cell + cell / 2 + rng.uniform(-6, 6)
            box = draw_shape(c, shape, colour, cx, cy, rng.uniform(26, 38))
            objs.append({"colour": colour, "shape": shape, "box": box})
        c.text(20, 18, "Shapes", size=16, bold=True)
        got = sum(1 for o in objs if (o["colour"], o["shape"]) == (target_colour, target_shape))
        assert got == n
        q = _q("q1", "count", f"How many {target_colour} {_plural(target_shape)} are there?", n,
               kind="count", canon="number")
        truth = {"target": f"{target_colour} {target_shape}", "count": n, "clutter": clutter,
                 "objects": objs}
    elif template == "list":
        k = rng.randint(3, 20)
        c = Canvas(420, 90 + k * 26, scale=dpr, theme=theme)
        c.text(20, 20, rng.choice(("Shopping", "To do", "Errands", "Packing")), size=18, bold=True)
        items = rng.sample(list(PRODUCTS) + [f"{w} prep" for w in EVENT_WORDS], k)
        ticked = rng.sample(range(k), rng.randint(0, k))
        ask_ticked = rng.random() < 0.6
        rows = []
        for r, label in enumerate(items):
            y = 64 + r * 26
            done = r in ticked
            c.rect(20, y, 36, y + 16, outline="muted", radius=3, width=1.5)
            if done:
                c.polyline([(23, y + 8), (27, y + 13), (34, y + 3)], color="ok", width=2)
            c.text(46, y, label, size=14, color="muted" if done else "fg")
            rows.append({"label": label, "ticked": done})
        n = len(ticked) if ask_ticked else k
        question = ("How many items on the list are ticked?" if ask_ticked
                    else "How many items are on the list?")
        q = _q("q1", "count", question, n, kind="count", canon="number")
        truth = {"rows": rows, "count": n, "asked": "ticked" if ask_ticked else "items"}
    else:   # badges
        cols, rows_n = 4, 5
        c = Canvas(cols * 90 + 40, rows_n * 100 + 60, scale=dpr, theme=theme)
        apps = []
        nb = rng.randint(0, cols * rows_n)
        with_badge = set(rng.sample(range(cols * rows_n), nb))
        names = set()
        for a in range(cols * rows_n):
            i, j = a % cols, a // cols
            x, y = 30 + i * 90, 40 + j * 100
            while True:
                name = inv.brand()
                if name not in names:
                    names.add(name)
                    break
            icon = rng.choice([v for k, v in COLOURS.items() if k not in ("red", "orange")])
            c.rect(x, y, x + 56, y + 56, fill=icon, radius=12)
            c.text_center(x + 28, y + 62, name, size=11)
            badge = None
            if a in with_badge:
                badge = rng.randint(1, 99)
                c.ellipse(x + 37, y - 11, x + 67, y + 19, fill="bg")      # ring keeps it apart
                c.ellipse(x + 40, y - 8, x + 64, y + 16, fill=(220, 30, 30))
                c.text_center(x + 52, y - 4, str(badge), size=11, color=(255, 255, 255))
            apps.append({"name": name, "badge": badge})
        q = _q("q1", "count", "How many app icons show a red badge?", nb, kind="count",
               canon="number")
        truth = {"apps": apps, "count": nb}
    q["bucket"] = count_bucket(int(q["answer"]))
    return ReasonItem(f"v7-counting-{{split}}-{index:04d}", "counting", template, [c.image], q,
                      truth)


# ---------------------------------------------------------------- spatial

SPATIAL_TEMPLATES = ("scene", "row", "inside", "layout")


def _centre(b):
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def _unique_objects(rng, n):
    combos = [(col, sh) for col in COLOURS for sh in SHAPES]
    return [{"colour": a, "shape": b} for a, b in rng.sample(combos, n)]


def build_spatial(index: int, *, seed: str) -> ReasonItem:
    rng = rng_for(seed, "v7", "spatial", index)
    template = SPATIAL_TEMPLATES[index % len(SPATIAL_TEMPLATES)]
    theme, dpr = _variant(index, len(SPATIAL_TEMPLATES))
    for attempt in range(50):
        item = _spatial_try(rng, template, theme, dpr, index)
        if item is not None:
            return item
    raise RuntimeError(f"spatial {index}: no unambiguous question in 50 tries")


def _spatial_try(rng, template, theme, dpr, index):
    iid = f"v7-spatial-{{split}}-{index:04d}"
    if template == "scene":
        cols, rows, cell = 5, 3, 110
        c = Canvas(cols * cell + 40, rows * cell + 40, scale=dpr, theme=theme)
        n = rng.randint(7, 11)
        objs = _unique_objects(rng, n)
        for o, (i, j) in zip(objs, _cells(rng, cols, rows, n)):
            o["cell"] = [i, j]
            o["box"] = draw_shape(c, o["shape"], o["colour"], 20 + i * cell + cell / 2,
                                  20 + j * cell + cell / 2, 46)
        kind = rng.choice(("left_of", "right_of", "above_below", "nearest"))
        x = rng.choice(objs)
        if kind in ("left_of", "right_of"):
            sign = -1 if kind == "left_of" else 1
            same = [o for o in objs if o["cell"][1] == x["cell"][1] and
                    (o["cell"][0] - x["cell"][0]) * sign > 0]
            if not same:
                return None
            ans = min(same, key=lambda o: abs(o["cell"][0] - x["cell"][0]))
            word = "left" if sign < 0 else "right"
            q = _q("q1", "lookup", f"Which shape is the nearest one to the {word} of the "
                   f"{_label(x)}, in the same row?", _label(ans), accept=_accept_label(_label(ans)))
        elif kind == "above_below":
            others = [o for o in objs if o["cell"][1] != x["cell"][1]]
            if not others:
                return None
            y = rng.choice(others)
            ans = "above" if _centre(x["box"])[1] < _centre(y["box"])[1] else "below"
            q = _q("q1", "lookup", f"Is the {_label(x)} above or below the {_label(y)}? "
                   "Answer above or below.", ans)
        else:
            d = sorted((math.dist(_centre(o["box"]), _centre(x["box"])), _label(o))
                       for o in objs if o is not x)
            if len(d) < 2 or d[1][0] < 1.25 * d[0][0]:
                return None
            q = _q("q1", "lookup", f"Which shape is nearest to the {_label(x)}?", d[0][1],
                   accept=_accept_label(d[0][1]))
        truth = {"objects": objs, "relation": kind, "subject": _label(x)}
    elif template == "row":
        n = rng.randint(5, 8)
        c = Canvas(n * 80 + 40, 160, scale=dpr, theme=theme)
        objs = _unique_objects(rng, n)
        for k, o in enumerate(objs):
            o["box"] = draw_shape(c, o["shape"], o["colour"], 60 + k * 80, 80, 48)
        k = rng.randrange(n)
        side = rng.choice(("left", "right"))
        pos = k if side == "left" else n - 1 - k
        if pos >= len(ORDINALS):
            return None
        q = _q("q1", "lookup", f"In the row, which shape is {ORDINALS[pos]} from the {side}?",
               _label(objs[k]), accept=_accept_label(_label(objs[k])))
        truth = {"objects": objs, "position": pos + 1, "from": side}
    elif template == "inside":
        nb = 3
        c = Canvas(nb * 200 + 40, 300, scale=dpr, theme=theme)
        objs = _unique_objects(rng, 9)
        letters = rng.sample("ABCDEFGHJKLMNPQRSTUVWXYZ", nb)
        boxes, k = [], 0
        placed = []
        for b in range(nb):
            x0 = 20 + b * 200
            c.rect(x0, 60, x0 + 180, 230, outline="fg", width=2, radius=6)
            c.text(x0 + 8, 36, f"Box {letters[b]}", size=15, bold=True)
            m = rng.randint(1, 2)
            for s in range(m):
                o = objs[k]
                k += 1
                o["box"] = draw_shape(c, o["shape"], o["colour"], x0 + 50 + s * 80, 145, 44)
                o["container"] = letters[b]
                placed.append(o)
            boxes.append({"letter": letters[b], "box": [c.px(x0), c.px(60), c.px(x0 + 180),
                                                        c.px(230)]})
        for s in range(2):              # outside every box
            o = objs[k]
            k += 1
            o["box"] = draw_shape(c, o["shape"], o["colour"], 60 + s * 400, 270, 28)
            o["container"] = None
            placed.append(o)
        x = rng.choice([o for o in placed if o["container"]])
        q = _q("q1", "lookup", f"Which box contains the {_label(x)}? Answer with the box letter.",
               x["container"], accept=[f"box {x['container']}"])
        truth = {"objects": placed, "boxes": boxes, "subject": _label(x)}
    else:   # layout: a toolbar row and a stacked form
        nbtn = rng.randint(4, 6)
        nfld = rng.randint(3, 5)
        c = Canvas(560, 110 + nfld * 64, scale=dpr, theme=theme)
        btns = rng.sample(BUTTONS, nbtn)
        flds = rng.sample(FORM_FIELDS, nfld)
        x = 20
        bx = []
        for label in btns:
            w = c.text_width(label, 13) + 24
            c.rect(x, 20, x + w, 52, fill="surface", outline="border", radius=6)
            c.text(x + 12, 28, label, size=13)
            bx.append({"label": label, "box": [c.px(x), c.px(20), c.px(x + w), c.px(52)]})
            x += w + 10
        if x > 540:
            return None
        fx = []
        for r, label in enumerate(flds):
            y = 80 + r * 64
            c.text(20, y, label, size=12, color="muted")
            c.rect(20, y + 18, 400, y + 48, outline="border", radius=4)
            fx.append({"label": label, "box": [c.px(20), c.px(y), c.px(400), c.px(y + 48)]})
        if rng.random() < 0.5:
            k = rng.randrange(nbtn - 1)
            if rng.random() < 0.5:
                q = _q("q1", "lookup", f"Which button is directly to the right of {btns[k]}?",
                       btns[k + 1], accept=[f"the {btns[k + 1]} button", f"{btns[k + 1]} button"])
            else:
                q = _q("q1", "lookup", f"Which button is directly to the left of {btns[k + 1]}?",
                       btns[k], accept=[f"the {btns[k]} button", f"{btns[k]} button"])
        else:
            k = rng.randrange(nfld - 1)
            q = _q("q1", "lookup", f"In the form, which field is directly below {flds[k]}?",
                   flds[k + 1], accept=[f"the {flds[k + 1]} field", f"{flds[k + 1]} field"])
        truth = {"buttons": bx, "fields": fx}
    return ReasonItem(iid, "spatial", template, [c.image], q, truth)


# ---------------------------------------------------------------- multi-image

MULTI_TEMPLATES = ("higher_total", "earliest_flight", "find_word", "what_changed")


def _receipt_card(rng, inv, total, theme, dpr):
    c = Canvas(300, 260, scale=dpr, theme=theme)
    c.rect(0, 0, 300, 260, fill="surface")
    c.text(20, 16, inv.merchant(), size=15, bold=True)
    y = 50
    lines = rng.sample(PRODUCTS, 3)
    rest = total
    for k, item in enumerate(lines):
        amt = round(total * rng.uniform(0.15, 0.3), 2) if k < 2 else round(rest, 2)
        rest -= amt
        c.text(20, y, item, size=13)
        c.text_right(280, y, money_str(amt), size=13)
        y += 26
    c.line(20, y + 4, 280, y + 4)
    c.text(20, y + 14, "TOTAL", size=14, bold=True)
    c.text_right(280, y + 14, money_str(total), size=14, bold=True, field="total", kind="money")
    return c


def _flight_card(rng, inv, hhmm, day_label, theme, dpr):
    c = Canvas(340, 200, scale=dpr, theme=theme)
    c.rect(0, 0, 340, 200, fill="surface")
    code = inv.code(2, 3)
    c.text(20, 16, f"Flight {code}", size=16, bold=True)
    c.text(20, 50, f"{inv.town()} to {inv.town()}", size=13, color="muted")
    c.text(20, 84, "Departs", size=12, color="muted")
    c.text(20, 102, f"{day_label} {time_12h(hhmm)}", size=16, field="departs", kind="text")
    c.text(20, 140, f"Gate {rng.choice('ABCDE')}{rng.randint(1, 40)}   Seat "
           f"{rng.randint(1, 38)}{rng.choice('ABCDEF')}", size=13)
    return c, code


def _note_card(rng, words, theme, dpr):
    c = Canvas(320, 220, scale=dpr, theme=theme)
    c.rect(0, 0, 320, 220, fill=(255, 244, 170) if theme == "light" else (70, 64, 30))
    for k, line in enumerate(words):
        c.text(20, 20 + k * 30, line, size=15, color=(40, 40, 40) if theme == "light"
               else (240, 236, 210))
    return c


def _settings_screen(rows, states, theme, dpr):
    c = Canvas(360, 60 + len(rows) * 44, scale=dpr, theme=theme)
    c.text(20, 18, "Settings", size=18, bold=True)
    for r, (label, on) in enumerate(zip(rows, states)):
        y = 60 + r * 44
        c.text(20, y + 8, label, size=14)
        fill = "ok" if on else "border"
        c.rect(290, y + 4, 336, y + 30, fill=fill, radius=13)
        knob = 312 if on else 292
        c.ellipse(knob + 2, y + 6, knob + 24, y + 28, fill=(255, 255, 255))
        c.text(240, y + 8, "On" if on else "Off", size=12, color="muted")
    return c


def build_multi(index: int, *, seed: str) -> ReasonItem:
    rng = rng_for(seed, "v7", "multi", index)
    inv = Inventor(rng)
    template = MULTI_TEMPLATES[index % len(MULTI_TEMPLATES)]
    theme, dpr = _variant(index, len(MULTI_TEMPLATES))
    iid = f"v7-multi-{{split}}-{index:04d}"
    n = 2 if template == "what_changed" else rng.randint(2, 4)
    if template == "higher_total":
        totals = sorted({round(rng.uniform(8, 240), 2) for _ in range(n * 3)})
        totals = rng.sample(totals, n)
        if max(totals) - sorted(totals)[-2] < 1.0:
            totals[totals.index(max(totals))] += 5.0
        cards = [_receipt_card(rng, inv, t, theme, dpr) for t in totals]
        hi = totals.index(max(totals)) + 1
        ask_low = rng.random() < 0.3
        lo = totals.index(min(totals)) + 1
        ans = lo if ask_low else hi
        q = _q("q1", "compare", f"Which image shows the receipt with the "
               f"{'lowest' if ask_low else 'highest'} total? Answer with the image number.",
               ans, canon="number", accept=[f"image {ans}", ORDINALS[ans - 1]])
        truth = {"totals": totals}
        names = ["total"]
    elif template == "earliest_flight":
        slots = rng.sample(range(6 * 4, 22 * 4), n)
        times = [f"{s // 4:02d}:{(s % 4) * 15:02d}" for s in slots]
        day = rng.choice(("Mon", "Tue", "Wed", "Thu", "Fri"))
        cards, codes = [], []
        for t in times:
            cc, code = _flight_card(rng, inv, t, day, theme, dpr)
            cards.append(cc)
            codes.append(code)
        first = min(range(n), key=lambda k: times[k]) + 1
        q = _q("q1", "compare", "All the flights leave on the same day. Which image shows the "
               "flight that leaves earliest? Answer with the image number.", first,
               canon="number", accept=[f"image {first}", ORDINALS[first - 1]])
        truth = {"departures": times, "codes": codes}
        names = ["departs"]
    elif template == "find_word":
        pool = list(PRODUCTS) + list(EVENT_WORDS)
        target = rng.choice(pool)
        others = [w for w in pool if w != target and target.lower() not in w.lower()
                  and w.lower() not in target.lower()]
        where = rng.randrange(n)
        cards, notes = [], []
        for k in range(n):
            words = rng.sample(others, 4)
            if k == where:
                words[rng.randrange(4)] = target
            notes.append(words)
            cards.append(_note_card(rng, words, theme, dpr))
        q = _q("q1", "lookup", f"Which image mentions {target}? Answer with the image number.",
               where + 1, canon="number", accept=[f"image {where + 1}", ORDINALS[where]])
        truth = {"notes": notes, "target": target}
        names = []
    else:
        rows = rng.sample(SETTINGS_ROWS, rng.randint(5, 7))
        before = [rng.random() < 0.5 for _ in rows]
        k = rng.randrange(len(rows))
        after = list(before)
        after[k] = not after[k]
        cards = [_settings_screen(rows, before, theme, dpr), _settings_screen(rows, after, theme, dpr)]
        q = _q("q1", "compare", "These are two screenshots of the same settings screen, before "
               "and after a change. Which setting changed? Answer with its name.", rows[k],
               accept=[f"the {rows[k]} setting", f"{rows[k]} setting"])
        truth = {"rows": rows, "before": before, "after": after, "changed": rows[k]}
        names = []
    for cc in cards:
        for nm in names:
            if not _field_ok(cc, nm):
                raise RuntimeError(f"{iid}: field {nm} fails its check")
    return ReasonItem(iid, "multi_image", template, [cc.image for cc in cards], q, truth)


# ---------------------------------------------------------------- chart trend

TREND_QTYPES = ("trend", "crossing", "peak", "change")
MONTH_ABBR = [m[:3] for m in MONTHS]


def build_chart_trend(index: int, *, seed: str) -> ReasonItem:
    rng = rng_for(seed, "v7", "chart", index)
    qtype = TREND_QTYPES[index % len(TREND_QTYPES)]
    theme, dpr = _variant(index, len(TREND_QTYPES))
    for _ in range(200):
        out = _chart_try(rng, qtype, theme, dpr, index)
        if out is not None:
            return out
    raise RuntimeError(f"chart_trend {index}: no clear question in 200 tries")


def _chart_try(rng, qtype, theme, dpr, index):
    n = rng.randint(6, 12)
    start = rng.randrange(0, 12 - n + 1)
    months = MONTH_ABBR[start:start + n]
    names = rng.sample(SERIES_NAMES, 2)
    lo, hi = 0, rng.choice((50, 100, 200, 500))
    series = {}
    for nm in names:
        v = rng.uniform(0.15, 0.85) * hi
        vals = []
        for _ in range(n):
            v = min(hi * 0.95, max(hi * 0.05, v + rng.uniform(-0.18, 0.18) * hi))
            vals.append(round(v))
        series[nm] = vals
    a, b = names
    rng_span = hi - lo
    labelled = qtype == "change"
    if qtype == "trend":
        i, j = sorted(rng.sample(range(n), 2))
        d = series[a][j] - series[a][i]
        if abs(d) < 0.2 * rng_span:
            return None
        ans = "rising" if d > 0 else "falling"
        q = _q("q1", "trend", f"Between {months[i]} and {months[j]}, did {a} rise or fall overall? "
               "Answer rising or falling.", ans,
               accept=["rise", "rose", "up", "increased"] if d > 0 else
               ["fall", "fell", "down", "decreased"])
    elif qtype == "crossing":
        diff = [series[a][k] - series[b][k] for k in range(n)]
        if diff[0] >= 0:
            return None
        cross = [k for k in range(1, n) if diff[k] > 0 and diff[k - 1] <= 0]
        if len(cross) != 1 or any(abs(x) < 0.04 * rng_span for x in diff):
            return None
        k = cross[0]
        q = _q("q1", "trend", f"In which month does {a} first go above {b}?", months[k],
               accept=[MONTHS[start + k]])
    elif qtype == "peak":
        vals = series[a]
        top = max(vals)
        if sorted(vals)[-2] > top - 0.08 * rng_span:
            return None
        k = vals.index(top)
        q = _q("q1", "max", f"In which month was {a} highest?", months[k],
               accept=[MONTHS[start + k]])
    else:
        i, j = sorted(rng.sample(range(n), 2))
        d = series[a][j] - series[a][i]
        if d == 0:
            return None
        q = _q("q1", "difference", f"By how much did {a} change from {months[i]} to {months[j]}? "
               "Give the change as a signed number (negative for a fall).", d, canon="number")
    W, H = 640, 400
    c = Canvas(W, H, scale=dpr, theme=theme)
    x0, x1, y0, y1 = 60, W - 30, 60, H - 50
    c.text(x0, 16, "Monthly figures", size=15, bold=True)
    for t in range(5):
        v = lo + rng_span * t / 4
        y = y1 - (y1 - y0) * t / 4
        c.line(x0, y, x1, y, color="grid")
        c.text_right(x0 - 8, y - 7, f"{v:g}", size=11, color="muted")
    def px(k, v):
        return (x0 + (x1 - x0) * k / (n - 1), y1 - (y1 - y0) * (v - lo) / rng_span)
    colours = ("accent", "danger")
    for (nm, vals), col in zip(series.items(), colours):
        pts = [px(k, v) for k, v in enumerate(vals)]
        c.polyline(pts, color=col, width=2.5)
        for k, (x, y) in enumerate(pts):
            c.ellipse(x - 3, y - 3, x + 3, y + 3, fill=col)
            if labelled and nm == a:
                c.text_center(x, y - 20, str(vals[k]), size=11, color=col)
    for k, m in enumerate(months):
        x, _ = px(k, lo)
        c.text_center(x, y1 + 10, m, size=11, color="muted")
    lx = x1 - 200
    for k, (nm, col) in enumerate(zip(names, colours)):
        c.rect(lx + k * 100, 22, lx + k * 100 + 14, 34, fill=col)
        c.text(lx + k * 100 + 20, 20, nm, size=12)
    truth = {"months": months, "series": series, "axis": [lo, hi], "labelled": labelled}
    return ReasonItem(f"v7-chart-{{split}}-{index:04d}", "chart_trend", qtype, [c.image], q, truth)


# ---------------------------------------------------------------- locate

def locate_candidates(meta: dict) -> list[dict]:
    return [e for e in meta.get("elements", []) if e.get("unique") and e["role"] in LOCATE_ROLES
            and e["bbox"][2] - e["bbox"][0] >= 4 and e["bbox"][3] - e["bbox"][1] >= 4]


def _role_word(role: str) -> str:
    return {"menu_item": "menu item", "field": "field", "icon": "icon"}.get(role, role)


def build_locate(index: int, *, split: str, seed: str):
    """The ``index``-th locate item: (ReasonItem, png bytes) built on a V1 screen."""
    rng = rng_for(seed, "v7", "locate", index)
    fam = LOCATE_FAMILIES[index % len(LOCATE_FAMILIES)]
    for attempt in range(60):
        src = 7000 + (index // len(LOCATE_FAMILIES)) * 60 + attempt
        meta, data, rendered = gen.build(fam, src, split=split, seed=seed)
        cands = locate_candidates(meta)
        if cands:
            break
    else:
        return None
    el = rng.choice(cands)
    ask_box = (index // len(LOCATE_FAMILIES)) % 4 == 3      # one in four, in every family
    name, role = el["name"], _role_word(el["role"])
    question = (f"Where is the {name} {role}? Give its bounding box." if ask_box
                else f"Where is the {name} {role}? Give the point to click.")
    q = _q("q1", "lookup", question, ",".join(map(str, el["bbox"])),
           kind="box" if ask_box else "point", canon="none", target_box=el["bbox"])
    truth = {"source_item": meta["item_id"], "element": el,
             "image_size": [meta["image"]["width"], meta["image"]["height"]]}
    item = ReasonItem(f"v7-locate-{{split}}-{index:04d}", "locate", meta["template"],
                      [rendered.image], q, truth, meta.get("default_year"))
    return item, data


# ---------------------------------------------------------------- generation

BUILDERS = {"counting": build_counting, "spatial": build_spatial, "multi_image": build_multi,
            "chart_trend": build_chart_trend}


def build(skill: str, index: int, *, split: str, seed: str | None = None):
    """(meta, [image bytes]) for one V7 item; None when a locate item finds no element."""
    seed = seed if seed is not None else gen.seed_for(split)
    if skill == "locate":
        got = build_locate(index, split=split, seed=seed)
        if got is None:
            return None
        item, data = got
        blobs = [data]
    else:
        item = BUILDERS[skill](index, seed=seed)
        blobs = [gen.png_bytes(im) for im in item.images]
    iid = item.item_id.format(split=split)
    images = []
    for k, (im, blob) in enumerate(zip(item.images, blobs)):
        images.append({"file": f"{iid}-{k + 1}.png", "sha256": sha256_bytes(blob),
                       "width": im.size[0], "height": im.size[1]})
    q = dict(item.question, qid=f"{iid}-q1")
    meta = {"schema": SCHEMA, "item_id": iid, "skill": skill, "template": item.template,
            "split": split, "seed": seed if split == "dev" else "private", "index": index,
            "render": render_stamp(), "default_year": item.default_year, "images": images,
            "prompt": prompt_for(q, len(images)), "question": q, "truth": item.truth}
    check = score_question(q, q["answer"] if q["kind"] not in ("point", "box") else
                           _gold_coords(q), default_year=item.default_year)
    if not check["passed"] or score_question(q, "", default_year=item.default_year)["passed"]:
        raise RuntimeError(f"{iid}: gold does not pass or blank passes")
    return meta, blobs


def _gold_coords(q: dict) -> str:
    b = q["target_box"]
    if q["kind"] == "point":
        return f"{(b[0] + b[2]) // 2},{(b[1] + b[3]) // 2}"
    return ",".join(map(str, b))


def generate(skill: str, out: Path, *, split: str, count: int | None = None, start: int = 0,
             indices=None) -> int:
    """Write ``skill`` items ``start`` .. ``start + count``, or exactly ``indices`` (a tier's)."""
    if split == "heldout" and gen._inside_repo(out):
        raise RuntimeError("held-out items must not be written inside the repository")
    count = SKILL_COUNTS[skill] if count is None else count
    out.mkdir(parents=True, exist_ok=True)
    written = 0
    for index in (range(start, start + count) if indices is None else indices):
        got = build(skill, index, split=split)
        if got is None:
            continue
        meta, blobs = got
        for spec, blob in zip(meta["images"], blobs):
            (out / spec["file"]).write_bytes(blob)
        (out / f"{meta['item_id']}.json").write_text(json.dumps(meta, indent=1) + "\n")
        written += 1
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="tinyagent-bench V7 image reasoning items")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--skill", action="append", choices=sorted(SKILL_COUNTS))
    g.add_argument("--split", default="dev", choices=("dev", "heldout"))
    g.add_argument("--count", type=int)
    g.add_argument("--start", type=int, default=0)
    g.add_argument("--tier", choices=("core", "extended", "full"),
                   help="write the tier's items instead of --start/--count")
    g.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    picked = None
    if args.tier:
        from edat.media import tiers
        picked = tiers.select_vision(tiers.load_manifest(args.tier))["v7"]
    for skill in args.skill or list(SKILL_COUNTS):
        if picked is not None and not picked.get(skill):
            continue
        n = generate(skill, Path(args.out) / skill, split=args.split, count=args.count,
                     start=args.start, indices=None if picked is None else picked[skill])
        print(f"{skill}: {n} items")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
