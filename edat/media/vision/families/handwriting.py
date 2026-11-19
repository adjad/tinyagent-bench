"""Handwriting: notes, shopping lists, to-do lists, sticky notes, phone messages, recipe cards
and day plans, written in a handwriting style (spec 05 §7.1).

No handwriting font ships offline, so the default is a deterministic *handwriting proxy*
(:func:`hand_text`): every character is drawn with the embedded font into a small glyph mask,
sheared to a slant, given a seeded rotation of at most 6 degrees, a baseline wobble and a size
jitter, and pasted in a pen-like ink colour on lined or plain paper. The jitter is kept mild so
the text reads clearly at 1x. If ``EDAT_HANDWRITING_FONT_DIR`` names a folder of ``.ttf``
handwriting fonts, one of them (picked by the item's seed) is used instead; ``data["hand"]``
records "proxy" or the font file name.

Planted fields: words, numbers, times, quantities, money, phone numbers and names.
Question types: lookup, count (items on a list, ticked tasks, notes), and the full reading.

Seven templates on purpose: a count coprime with 10 makes every template meet every theme,
scale and abstention slot.
"""
from __future__ import annotations

import functools
import os
import random
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from edat.media.common import GIVEN_NAMES, Inventor, MONTHS, canon_text
from edat.media.vision.canvas import Canvas, Field, TextRun, font
from edat.media.vision.items import ABSTAIN_ANSWER, Question, Rendered

FAMILY = "handwriting"
TEMPLATES = ("note", "shopping_list", "todo_list", "sticky_notes", "phone_message",
             "recipe_card", "day_plan")
LOGICAL_SIZES = {"note": (600, 760), "shopping_list": (480, 720), "todo_list": (520, 720),
                 "sticky_notes": (900, 600), "phone_message": (560, 660),
                 "recipe_card": (720, 480), "day_plan": (520, 780)}
YEAR = 2027
FONT_DIR_ENV = "EDAT_HANDWRITING_FONT_DIR"

# Paper, ruling and ink per theme. Every ink keeps well over 3:1 against its paper.
_PAPER = {
    "light": {"desk": (222, 214, 200), "paper": (253, 251, 244), "rule": (176, 198, 228),
              "margin": (226, 150, 150), "print": (60, 60, 66), "printmuted": (96, 96, 104),
              "inks": ((28, 52, 140), (34, 34, 40), (20, 84, 48), (84, 36, 120))},
    "dark": {"desk": (20, 20, 22), "paper": (42, 43, 47), "rule": (74, 80, 94),
             "margin": (120, 70, 74), "print": (210, 210, 216), "printmuted": (170, 170, 178),
             "inks": ((236, 236, 242), (246, 226, 150), (160, 205, 255), (180, 236, 190))},
}
_STICKY = {"light": ((255, 236, 130), (255, 204, 214), (204, 240, 194), (194, 226, 255)),
           "dark": ((92, 80, 28), (96, 44, 60), (40, 84, 48), (34, 62, 100))}
_STICKY_INK = {"light": ((30, 30, 36), (24, 40, 120)), "dark": ((250, 246, 228), (255, 255, 255))}


# ============================================================================ handwriting proxy
@dataclass
class Hand:
    """One writer's style for a page: ink, slant and how much each glyph wanders."""

    ink: tuple
    slant: float = 0.12          # horizontal shear (positive leans right)
    rot: float = 4.0             # max per-character rotation in degrees (<= 6)
    wobble: float = 0.04         # baseline wobble, as a fraction of the font size
    jitter: float = 0.05         # size jitter fraction
    spacing: float = 1.0         # advance multiplier
    marker: bool = False         # thicker stroke (sticky notes)
    font_path: str | None = None


@functools.lru_cache(maxsize=64)
def _ttf(path: str, px: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, px)


def _hand_font(hand: Hand, px: int):
    return _ttf(hand.font_path, px) if hand.font_path else font(px)


def pick_hand(rng: random.Random, ink: tuple, *, marker: bool = False) -> tuple[Hand, str]:
    """A seeded style; the font choice draws from ``rng`` whether or not fonts are present."""
    pick = rng.random()
    slant = rng.uniform(0.1, 0.3) if rng.random() < 0.8 else rng.uniform(-0.12, 0.0)
    hand = Hand(ink=ink, slant=slant, rot=rng.uniform(3.5, 6.0),
                wobble=rng.uniform(0.04, 0.08), jitter=rng.uniform(0.05, 0.1),
                spacing=rng.uniform(1.0, 1.08), marker=marker)
    folder = os.environ.get(FONT_DIR_ENV)
    if folder:
        fonts = sorted(Path(folder).glob("*.ttf"))
        if fonts:
            path = fonts[min(len(fonts) - 1, int(pick * len(fonts)))]
            hand.font_path = str(path)
            hand.rot = min(hand.rot, 2.5)          # real handwriting needs less help
            return hand, path.name
    return hand, "proxy"


def hand_width(c: Canvas, s: str, size: float, hand: Hand) -> float:
    """Approximate logical width of ``s`` written with ``hand`` (for layout)."""
    f = _hand_font(hand, c.font_px(size))
    return f.getlength(s) * hand.spacing * 1.02 / c.scale + size * 0.15


def hand_text(c: Canvas, x: float, y: float, s: str, size: float, rng: random.Random, *,
              field: str | None = None, kind: str = "text", value: str | None = None,
              hand: Hand, reading: bool = True) -> list[int]:
    """Write ``s`` by hand with its baseline at logical (x, y); return the pixel box.

    The whole string is recorded as one text run (reading ground truth) and, with ``field``
    set, one planted field, exactly as :meth:`Canvas.text` records them: union box of the
    glyphs, font size in output pixels, ink colour, and the box's sha256 right after drawing.
    """
    fpx = c.font_px(size)
    ink = c.color(hand.ink)
    stroke = max(1, round(fpx / 22)) if hand.marker else 0
    cx = float(c.px(x))
    base = float(c.px(y))
    drift = rng.uniform(-0.012, 0.012)            # the line slopes a little
    start = cx
    W, H = c.size
    ux0 = uy0 = 10 ** 9
    ux1 = uy1 = -10 ** 9
    f = _hand_font(hand, fpx)
    for ch in s:
        adv = f.getlength(ch)
        if ch.isspace():
            cx += adv * (hand.spacing + rng.uniform(0.0, 0.25))
            continue
        gpx = max(1, round(fpx * (1 + rng.uniform(-hand.jitter, hand.jitter))))
        gf = _hand_font(hand, gpx)
        l, t, r, b = gf.getbbox(ch, anchor="ls", stroke_width=stroke)
        pad = 2 + gpx // 4
        gw, gh = max(1, r - l) + 2 * pad, max(1, b - t) + 2 * pad
        g = Image.new("L", (gw, gh), 0)
        ImageDraw.Draw(g).text((pad - l, pad - t), ch, font=gf, fill=255, anchor="ls",
                               stroke_width=stroke, stroke_fill=255)
        sx = 1 + rng.uniform(-hand.jitter, hand.jitter) * 1.5      # letters vary in width
        if abs(sx - 1) > 0.01:
            g = g.resize((max(1, round(gw * sx)), gh), Image.Resampling.BICUBIC)
            gw = g.width
        if hand.slant:
            k = hand.slant
            g = g.transform(g.size, Image.Transform.AFFINE, (1, k, -k * gh / 2, 0, 1, 0),
                            resample=Image.Resampling.BICUBIC)
        g = g.rotate(rng.uniform(-hand.rot, hand.rot), resample=Image.Resampling.BICUBIC,
                     expand=True)
        wob = rng.uniform(-1, 1) * hand.wobble * fpx + drift * (cx - start)
        ccx = cx + (l + r) / 2
        ccy = base + wob + (t + b) / 2
        px0 = round(ccx - g.width / 2)
        py0 = round(ccy - g.height / 2)
        c.image.paste(Image.new("RGB", g.size, ink), (px0, py0), g)
        bb = g.getbbox()
        if bb:
            ux0, uy0 = min(ux0, px0 + bb[0]), min(uy0, py0 + bb[1])
            ux1, uy1 = max(ux1, px0 + bb[2]), max(uy1, py0 + bb[3])
        cx += adv * hand.spacing + rng.uniform(-0.02, 0.05) * fpx
    if ux1 < ux0:                                  # nothing visible (blank string)
        ux0, uy0, ux1, uy1 = c.px(x), c.px(y) - fpx, c.px(x) + 1, c.px(y)
    raw = [ux0, uy0, ux1, uy1]
    box = [max(0, ux0), max(0, uy0), min(W, ux1), min(H, uy1)]
    c.runs.append(TextRun(s, box, fpx, field, reading))
    if field:
        clipped = box != raw
        c.fields[field] = Field(
            name=field, kind=kind, value=s if value is None else value, text=s, bbox=box,
            size_px=fpx, visible=not clipped, obscured="crop" if clipped else None,
            crop_sha256=c.crop_sha256(box), fg=list(ink))
    return box


class _Writer:
    """Writes a line from pieces, advancing x; each piece is its own run."""

    def __init__(self, c: Canvas, rng: random.Random, hand: Hand, size: float) -> None:
        self.c, self.rng, self.hand, self.size = c, rng, hand, size

    def line(self, x: float, y: float, pieces: list[tuple[str, dict | None]]) -> float:
        for text, fd in pieces:
            fd = fd or {}
            box = hand_text(self.c, x, y, text, self.size, self.rng, hand=self.hand, **fd)
            x = box[2] / self.c.scale + self.size * 0.45
        return x


# ============================================================================ helpers
def render(rng: random.Random, *, template: str, theme: str, scale: float,
           abstain: bool) -> Rendered:
    if template not in TEMPLATES:
        raise ValueError(f"unknown template {template!r}")
    return globals()["_" + template](rng, theme=theme, scale=scale, abstain=abstain)


def _paper(c: Canvas, theme: str, x0: float, y0: float, x1: float, y1: float, *,
           ruled: float | None, first: float = 0, margin: float | None = None,
           holes: bool = False) -> None:
    p = _PAPER[theme]
    c.rect(x0 + 4, y0 + 5, x1 + 4, y1 + 5, fill=tuple(max(0, v - 18) for v in p["desk"]))
    c.rect(x0, y0, x1, y1, fill=p["paper"])
    if ruled:
        y = y0 + first
        while y < y1 - 8:
            c.line(x0, y, x1, y, color=p["rule"], width=1)
            y += ruled
    if margin is not None:
        c.line(x0 + margin, y0, x0 + margin, y1, color=p["margin"], width=1)
    if holes:
        for k in range(3):
            cy = y0 + (y1 - y0) * (0.2 + 0.3 * k)
            c.ellipse(x0 + 12, cy - 7, x0 + 26, cy + 7, fill=p["desk"])


def _blur_safe(c: Canvas, name: str, keep: list[str]) -> bool:
    f = c.fields.get(name)
    if f is None or not f.visible or f.bbox is None:
        return False
    pad = c.px(3) + 2
    x0, y0, x1, y1 = f.bbox[0] - pad, f.bbox[1] - pad, f.bbox[2] + pad, f.bbox[3] + pad
    boxes = [c.fields[o].bbox for o in keep if o != name and o in c.fields]
    boxes += [r.bbox for r in c.runs if r.field != name]
    return not any(b and not (b[2] <= x0 or b[0] >= x1 or b[3] <= y0 or b[1] >= y1)
                   for b in boxes)


def _finish(c: Canvas, rng: random.Random, abstain: bool, qs: list[Question], data: dict,
            template: str, absent: tuple, what: str) -> Rendered:
    qs = qs[:4]
    if abstain:
        cands = [q for q in qs if q.qtype == "lookup" and len(q.fields) == 1]
        rng.shuffle(cands)
        done = False
        if rng.random() < 0.6:
            for q in cands:
                keep = [f for o in qs if o is not q for f in o.fields]
                if q.fields[0] not in keep and _blur_safe(c, q.fields[0], keep):
                    qs.remove(q)
                    c.blur_field(q.fields[0], radius=7)
                    qs.append(Question(q.qid + "_abstain", "absence", q.question,
                                       ABSTAIN_ANSWER, kind="abstain", fields=list(q.fields)))
                    data["abstain"] = {"blurred": q.fields[0]}
                    done = True
                    break
        if not done:
            qs.pop(rng.randrange(len(qs)))
            qid, text, name, make = absent
            drawn = [canon_text(r.text) for r in c.runs]
            v = make()
            for _ in range(50):
                if not any(canon_text(v) in t for t in drawn):
                    break
                v = make()
            c.absent(name, v)
            qs.append(Question(qid, "absence", text, ABSTAIN_ANSWER, kind="abstain",
                               fields=[name]))
            data["abstain"] = {"absent": name}
    qs.append(Question("q_reading", "reading", f"Read the handwritten {what}.", "",
                       kind="reading"))
    return Rendered(c, template, qs, data, default_year=YEAR)


def _time_shown(r: random.Random, hhmm: str) -> str:
    h, m = map(int, hhmm.split(":"))
    style = r.randrange(3)
    if style == 0:
        return hhmm
    h12 = h % 12 or 12
    suf = "am" if h < 12 else "pm"
    if style == 1:
        return f"{h12}:{m:02d}{suf}"
    return f"{h12}:{m:02d} {suf.upper()}"


_GROCERIES = ("Apples", "Bread", "Eggs", "Milk", "Rice", "Butter", "Lemons", "Onions", "Cheese",
              "Coffee", "Tomatoes", "Pasta", "Yogurt", "Carrots", "Oats", "Bananas", "Soap",
              "Tea", "Honey", "Beans", "Spinach", "Flour", "Garlic", "Peppers", "Limes",
              "Cucumber", "Tortillas", "Mushrooms")
_TASKS = ("Return library books", "Email the landlord", "Water the plants", "Book a haircut",
          "Pay the gas bill", "Pick up dry cleaning", "Call the plumber", "Renew bus pass",
          "Buy a birthday card", "Back up laptop", "Fix the bike light", "Send the invoice",
          "Clean the oven", "Post the parcel", "Change the bedsheets", "Order printer ink")


# ============================================================================ note
def _note(rng, *, theme, scale, abstain):
    inv = Inventor(rng)
    w, h = LOGICAL_SIZES["note"]
    c = Canvas(w, h, scale=scale, theme=theme, bg=_PAPER[theme]["desk"])
    hand, hand_name = pick_hand(rng, rng.choice(_PAPER[theme]["inks"]))
    rule = 38
    _paper(c, theme, 40, 30, w - 40, h - 30, ruled=rule, first=80, margin=60, holes=True)
    size = rng.choice((19, 20, 21))
    wr = _Writer(c, rng, hand, size)
    d = date(YEAR, rng.randint(1, 12), rng.randint(1, 28))
    title = rng.choice(("Notes", "Reminders", "Don't forget", "This week"))
    wr.line(112, 30 + 80 - 6, [(title, {"field": "title"})])
    names = rng.sample(GIVEN_NAMES, 3)
    forms = rng.sample(range(8), rng.randint(5, 7))
    qs: list[Question] = []
    lines_data = []
    y = 30 + 80 + rule - 6
    for k, form in enumerate(forms):
        fname = f"v_{k}"
        if form == 0:
            t = inv.clock(9, 18, 15)
            pieces = [(f"Call {names[0]} at", None),
                      (_time_shown(rng, t), {"field": fname, "kind": "time", "value": t})]
            q = Question("q_call", "lookup", f"At what time should {names[0]} be called?", t,
                         canon="time", fields=[fname])
        elif form == 1:
            code = str(rng.randint(1000, 9999))
            pieces = [("Door code is", None), (code, {"field": fname})]
            q = Question("q_code", "lookup", "What door code is written in the note?", code,
                         fields=[fname])
        elif form == 2:
            util = rng.choice(("water", "phone", "internet", "electric"))
            amt = round(rng.uniform(18, 140), 2)
            pieces = [(f"Pay the {util} bill", None),
                      (f"${amt:.2f}", {"field": fname, "kind": "money", "value": f"{amt:.2f}"})]
            q = Question("q_bill", "lookup", f"How much is the {util} bill?", f"{amt:.2f}",
                         canon="money", fields=[fname])
        elif form == 3:
            n = rng.randint(3, 14)
            pieces = [("Bring", None), (str(n), {"field": fname, "kind": "number"}),
                      ("chairs to the hall", None)]
            q = Question("q_chairs", "lookup", "How many chairs should be brought to the hall?",
                         str(n), canon="number", fields=[fname])
        elif form == 4:
            place = f"{inv.surname()} {rng.choice(('Cafe', 'Park', 'Library', 'Station'))}"
            pieces = [(f"Meet {names[1]} at", None), (place, {"field": fname})]
            q = Question("q_meet", "lookup", f"Where is the meeting with {names[1]}?", place,
                         fields=[fname])
        elif form == 5:
            n = rng.randint(2, 6)
            item = rng.choice(("lemons", "stamps", "batteries", "candles", "folders"))
            pieces = [("Pick up", None), (str(n), {"field": fname, "kind": "number"}),
                      (item, None)]
            q = Question("q_pickup", "lookup", f"How many {item} should be picked up?", str(n),
                         canon="number", fields=[fname])
        elif form == 6:
            t = inv.clock(6, 21, 5)
            pieces = [("Train leaves at", None),
                      (_time_shown(rng, t), {"field": fname, "kind": "time", "value": t})]
            q = Question("q_train", "lookup", "What time does the train leave?", t,
                         canon="time", fields=[fname])
        else:
            word = rng.choice(("blue folder", "spare key", "red umbrella", "green bag"))
            pieces = [(f"Give {names[2]} the", None), (word, {"field": fname})]
            q = Question("q_give", "lookup", f"What should {names[2]} be given?", word,
                         fields=[fname])
        if y > h - 60:
            break
        wr.line(84 + rng.uniform(0, 8), y, pieces)
        lines_data.append(" ".join(p for p, _ in pieces))
        qs.append(q)
        y += rule
    sig = rng.choice(names[:2] + [inv.given()])
    wr.line(w - 200, min(y + rule, h - 52), [(f"- {sig}", None)])
    rng.shuffle(qs)
    data = {"template": "note", "hand": hand_name, "title": title, "lines": lines_data}
    return _finish(c, rng, abstain, qs, data, "note",
                   ("q_ref", "What reference number is written on the note?", "ref",
                    lambda: inv.code(2, 4)), "note")


# ============================================================================ shopping list
def _shopping_list(rng, *, theme, scale, abstain):
    inv = Inventor(rng)
    w, h = LOGICAL_SIZES["shopping_list"]
    c = Canvas(w, h, scale=scale, theme=theme, bg=_PAPER[theme]["desk"])
    hand, hand_name = pick_hand(rng, rng.choice(_PAPER[theme]["inks"]))
    ruled = rng.random() < 0.6
    rule = 40
    _paper(c, theme, 50, 24, w - 50, h - 24, ruled=rule if ruled else None, first=86)
    size = rng.choice((19, 20, 22))
    wr = _Writer(c, rng, hand, size)
    title = rng.choice(("Shopping", "Groceries", "To buy", "Market list"))
    wr.line(80, 24 + 86 - 8, [(title, {"field": "title"})])
    n = rng.randint(5, 11)
    items = rng.sample(_GROCERIES, n)
    qtys = [rng.choice((None, None, 2, 3, 4, 6, 12)) for _ in items]
    if all(q is None for q in qtys):
        qtys[rng.randrange(n)] = rng.choice((2, 3, 4))
    bullet = rng.choice(("-", "*", "o", ""))
    y = 24 + 86 + rule - 8
    for i, (it, q) in enumerate(zip(items, qtys)):
        pieces = []
        if bullet:
            pieces.append((bullet, None))
        if q is not None:
            pieces.append((f"{q} x" if rng.random() < 0.5 else str(q),
                           {"field": f"qty_{i}", "kind": "number", "value": str(q)}))
        pieces.append((it, {"field": f"item_{i}"}))
        wr.line(80, y, pieces)
        y += rule
    with_q = [i for i in range(n) if qtys[i] is not None]
    iq = rng.choice(with_q)
    ia = rng.randrange(n - 1)
    qs = [Question("q_count", "count", "How many items are on the shopping list?", str(n),
                   canon="number", fields=[f"item_{i}" for i in range(n)]),
          Question("q_qty", "lookup", f"How many {items[iq].lower()} are on the list?",
                   str(qtys[iq]), canon="number", fields=[f"qty_{iq}", f"item_{iq}"]),
          Question("q_after", "lookup", f"Which item is written right after {items[ia]}?",
                   items[ia + 1], fields=[f"item_{ia}", f"item_{ia + 1}"]),
          rng.choice((Question("q_last", "lookup", "What is the last item on the list?",
                               items[-1], fields=[f"item_{i}" for i in range(n)]),
                      Question("q_title", "lookup", "What is the title of the list?", title,
                               fields=["title"])))]
    rng.shuffle(qs)
    data = {"template": "shopping_list", "hand": hand_name, "title": title,
            "items": [{"item": it, "qty": q} for it, q in zip(items, qtys)]}
    return _finish(c, rng, abstain, qs, data, "shopping_list",
                   ("q_store", "Which store is named on the shopping list?", "store",
                    inv.merchant), "list")


# ============================================================================ to-do list
def _todo_list(rng, *, theme, scale, abstain):
    inv = Inventor(rng)
    w, h = LOGICAL_SIZES["todo_list"]
    c = Canvas(w, h, scale=scale, theme=theme, bg=_PAPER[theme]["desk"])
    p = _PAPER[theme]
    ink = rng.choice(p["inks"])
    hand, hand_name = pick_hand(rng, ink)
    rule = 46
    _paper(c, theme, 40, 24, w - 40, h - 24, ruled=rule if rng.random() < 0.5 else None,
           first=96)
    size = rng.choice((19, 20))
    wr = _Writer(c, rng, hand, size)
    day = rng.choice(("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"))
    wr.line(70, 24 + 96 - 12, [("To do", {"field": "title"}), (day, {"field": "day"})])
    n = rng.randint(5, 10)
    tasks = rng.sample(_TASKS, n)
    done = [rng.random() < 0.4 for _ in tasks]
    timed = {i: inv.clock(8, 19, 15) for i in rng.sample(range(n), rng.randint(1, 3))}
    y = 24 + 96 + rule - 12
    for i, t in enumerate(tasks):
        bx, by = 70, y - 17
        c.line(bx, by, bx + 16, by + 1, color=ink, width=1.5)
        c.line(bx + 16, by + 1, bx + 15, by + 17, color=ink, width=1.5)
        c.line(bx + 15, by + 17, bx - 1, by + 16, color=ink, width=1.5)
        c.line(bx - 1, by + 16, bx, by, color=ink, width=1.5)
        if done[i]:
            c.polyline([(bx + 2, by + 8), (bx + 7, by + 15), (bx + 20, by - 6)], color=ink,
                       width=2.2)
        pieces = []
        if i in timed:
            pieces.append((_time_shown(rng, timed[i]),
                           {"field": f"time_{i}", "kind": "time", "value": timed[i]}))
        pieces.append((t, {"field": f"task_{i}"}))
        wr.line(102, y, pieces)
        y += rule
    ti = rng.choice(sorted(timed))
    qs = [Question("q_count", "count", "How many tasks are on the to-do list?", str(n),
                   canon="number", fields=[f"task_{i}" for i in range(n)]),
          Question("q_done", "count", "How many tasks are ticked off?", str(sum(done)),
                   canon="number", fields=[f"task_{i}" for i in range(n)]),
          Question("q_time", "lookup", f"At what time is '{tasks[ti]}' planned?", timed[ti],
                   canon="time", fields=[f"task_{ti}", f"time_{ti}"]),
          rng.choice((Question("q_first", "lookup", "What is the first task on the list?",
                               tasks[0], fields=[f"task_{i}" for i in range(n)]),
                      Question("q_day", "lookup", "Which day is the list for?", day,
                               fields=["day"])))]
    rng.shuffle(qs)
    data = {"template": "todo_list", "hand": hand_name, "day": day,
            "tasks": [{"task": t, "done": done[i], "time": timed.get(i)}
                      for i, t in enumerate(tasks)]}
    return _finish(c, rng, abstain, qs, data, "todo_list",
                   ("q_priority", "Which task is marked as top priority?", "priority",
                    lambda: rng.choice(("Repaint the gate", "Sort the garage", "Tune the piano"))),
                   "list")


# ============================================================================ sticky notes
def _sticky_notes(rng, *, theme, scale, abstain):
    inv = Inventor(rng)
    w, h = LOGICAL_SIZES["sticky_notes"]
    desk = (196, 200, 206) if theme == "light" else (16, 16, 18)
    c = Canvas(w, h, scale=scale, theme=theme, bg=desk)
    n = rng.randint(3, 4)
    colors = rng.sample(_STICKY[theme], n)
    ink = rng.choice(_STICKY_INK[theme])
    hand, hand_name = pick_hand(rng, ink, marker=True)
    kinds = rng.sample(range(6), n)
    slots = [(40, 50), (480, 50), (40, 320), (480, 320)]
    rng.shuffle(slots)
    names = rng.sample(GIVEN_NAMES, 2)
    qs: list[Question] = []
    notes = []
    size = rng.choice((26, 28))
    for k in range(n):
        x0, y0 = slots[k]
        x0 += rng.randint(0, 40)
        y0 += rng.randint(0, 20)
        nw, nh = 340, 230
        c.rect(x0 + 5, y0 + 6, x0 + nw + 5, y0 + nh + 6,
               fill=tuple(max(0, v - 30) for v in desk))
        c.rect(x0, y0, x0 + nw, y0 + nh, fill=colors[k])
        c.rect(x0, y0, x0 + nw, y0 + 26, fill=tuple(max(0, v - 14) for v in colors[k]))
        wr = _Writer(c, rng, hand, size)
        lx, ly = x0 + 24, y0 + 80
        fn = f"note_{k}"
        kind = kinds[k]
        if kind == 0:
            t = inv.clock(6, 20, 15)
            wr.line(lx, ly, [("Gym", {"field": fn + "_a"})])
            wr.line(lx, ly + 52, [(_time_shown(rng, t),
                                   {"field": fn + "_b", "kind": "time", "value": t})])
            qs.append(Question("q_gym", "lookup", "What time is written on the gym note?", t,
                               canon="time", fields=[fn + "_a", fn + "_b"]))
            notes.append(["Gym", t])
        elif kind == 1:
            ph = inv.phone()
            wr.line(lx, ly, [(f"Call {names[0]}", {"field": fn + "_a"})])
            wr.line(lx, ly + 52, [(ph, {"field": fn + "_b", "kind": "phone"})])
            qs.append(Question("q_phone", "lookup", f"What phone number is written for {names[0]}?",
                               ph, canon="phone", fields=[fn + "_a", fn + "_b"]))
            notes.append([f"Call {names[0]}", ph])
        elif kind == 2:
            code = inv.booking_ref()
            wr.line(lx, ly, [("Wi-Fi", {"field": fn + "_a"})])
            wr.line(lx, ly + 52, [(code, {"field": fn + "_b"})])
            qs.append(Question("q_wifi", "lookup", "What Wi-Fi password is written on the note?",
                               code, fields=[fn + "_a", fn + "_b"]))
            notes.append(["Wi-Fi", code])
        elif kind == 3:
            n_st = rng.randint(4, 20)
            wr.line(lx, ly, [("Buy", None), (str(n_st), {"field": fn + "_b", "kind": "number"}),
                             ("stamps", {"field": fn + "_a"})])
            qs.append(Question("q_stamps", "lookup", "How many stamps should be bought?",
                               str(n_st), canon="number", fields=[fn + "_a", fn + "_b"]))
            notes.append([f"Buy {n_st} stamps"])
        elif kind == 4:
            d = date(YEAR, rng.randint(1, 12), rng.randint(1, 28))
            shown = f"{MONTHS[d.month - 1][:3]} {d.day}"
            wr.line(lx, ly, [(f"{names[1]}'s birthday", {"field": fn + "_a"})])
            wr.line(lx, ly + 52, [(shown, {"field": fn + "_b", "kind": "date",
                                           "value": d.isoformat()})])
            qs.append(Question("q_bday", "lookup", f"When is {names[1]}'s birthday?",
                               d.isoformat(), canon="date", fields=[fn + "_a", fn + "_b"]))
            notes.append([f"{names[1]}'s birthday", shown])
        else:
            room = f"Room {rng.randint(101, 420)}"
            wr.line(lx, ly, [("Meeting moved to", {"field": fn + "_a"})])
            wr.line(lx, ly + 52, [(room, {"field": fn + "_b"})])
            qs.append(Question("q_room", "lookup", "Which room was the meeting moved to?", room,
                               fields=[fn + "_a", fn + "_b"]))
            notes.append(["Meeting moved to", room])
    qs = qs[:3]
    qs.append(Question("q_notes", "count", "How many sticky notes are in the picture?", str(n),
                       canon="number", fields=[f"note_{k}_b" for k in range(n)]))
    rng.shuffle(qs)
    data = {"template": "sticky_notes", "hand": hand_name, "notes": notes}
    return _finish(c, rng, abstain, qs, data, "sticky_notes",
                   ("q_pin", "What PIN is written on the notes?", "pin",
                    lambda: str(rng.randint(10000, 99999))), "notes")


# ============================================================================ phone message
def _phone_message(rng, *, theme, scale, abstain):
    inv = Inventor(rng)
    w, h = LOGICAL_SIZES["phone_message"]
    p = _PAPER[theme]
    c = Canvas(w, h, scale=scale, theme=theme, bg=p["desk"])
    hand, hand_name = pick_hand(rng, rng.choice(p["inks"]))
    _paper(c, theme, 40, 30, w - 40, h - 30, ruled=None)
    pc = p["print"]
    pm = p["printmuted"]
    c.rect(40, 30, w - 40, 80, fill=p["rule"] if theme == "light" else (60, 66, 80))
    c.text_center(w / 2, 44, "WHILE YOU WERE OUT", size=20, bold=True, color=pc)
    to = inv.given()
    caller = inv.person()
    org = inv.merchant()
    ph = inv.phone()
    d = date(YEAR, rng.randint(1, 12), rng.randint(1, 28))
    t = inv.clock(8, 18, 5)
    size = rng.choice((18, 19, 20))
    wr = _Writer(c, rng, hand, size)
    rows = [("For", to, "for", "text", to), ("Date", f"{d.month}/{d.day}", "date", "date",
                                                 d.isoformat()),
            ("Time", _time_shown(rng, t), "time", "time", t), ("From", caller, "from", "text",
                                                               caller),
            ("Of", org, "of", "text", org), ("Phone", ph, "phone", "phone", ph)]
    y = 112
    for label, shown, name, kind, value in rows:
        c.text(64, y - 14, label, size=13, bold=True, color=pc)
        c.line(130, y + 6, w - 64, y + 6, color=pm)
        wr.line(140, y + 2, [(shown, {"field": name, "kind": kind, "value": value})])
        y += 52
    opts = ("Telephoned", "Please call", "Will call again", "Came to see you", "Returned your call",
            "Urgent")
    ticked = set(rng.sample(range(len(opts)), rng.randint(1, 2)))
    for k, o in enumerate(opts):
        x = 64 + (k % 2) * 220
        yy = y + (k // 2) * 30
        c.rect(x, yy, x + 16, yy + 16, outline=pc, width=1.2)
        c.text(x + 26, yy, o, size=12, color=pc)
        if k in ticked:
            c.line(x + 3, yy + 3, x + 13, yy + 13, color=hand.ink, width=2)
            c.line(x + 13, yy + 3, x + 3, yy + 13, color=hand.ink, width=2)
    y += 3 * 30 + 16
    c.text(64, y, "Message", size=13, bold=True, color=pc)
    msg_a = rng.choice(("Wants to move the", "Asked about the", "Can confirm the",
                        "Needs a reply on the"))
    thing = rng.choice(("site visit", "delivery", "quote", "booking", "contract"))
    wr.line(64, y + 44, [(msg_a, None), (thing, {"field": "thing"})])
    wr.line(64, y + 82, [("before", None),
                         (rng.choice(("Friday", "noon", "the 15th", "next week")), None)])
    qs = [Question("q_from", "lookup", "Who called?", caller, fields=["from"]),
          Question("q_phone", "lookup", "What number should be called back?", ph,
                   canon="phone", fields=["phone"]),
          Question("q_time", "lookup", "At what time did the call come in?", t, canon="time",
                   fields=["time"]),
          rng.choice((Question("q_for", "lookup", "Who is the message for?", to, fields=["for"]),
                      Question("q_of", "lookup", "Which company is the caller from?", org,
                               fields=["of"]),
                      Question("q_thing", "lookup", "What is the message about?", thing,
                               fields=["thing"])))]
    rng.shuffle(qs)
    data = {"template": "phone_message", "hand": hand_name, "for": to, "from": caller,
            "of": org, "phone": ph, "date": d.isoformat(), "time": t,
            "ticked": [opts[k] for k in sorted(ticked)], "about": thing}
    return _finish(c, rng, abstain, qs, data, "phone_message",
                   ("q_ext", "What extension number did the caller give?", "ext",
                    lambda: f"ext {rng.randint(200, 899)}"), "message")


# ============================================================================ recipe card
_RECIPES = (("Lemon Bars", (("cups flour", "flour"), ("eggs", "eggs"), ("cups sugar", "sugar"),
                            ("lemons", "lemons"), ("g butter", "butter"), ("tsp salt", "salt"))),
            ("Veggie Chili", (("cans beans", "beans"), ("onions", "onions"),
                              ("peppers", "peppers"), ("cloves garlic", "garlic"),
                              ("cups stock", "stock"), ("tsp cumin", "cumin"))),
            ("Oat Cookies", (("cups oats", "oats"), ("cups flour", "flour"), ("eggs", "eggs"),
                             ("g butter", "butter"), ("tbsp honey", "honey"),
                             ("tsp cinnamon", "cinnamon"))),
            ("Banana Bread", (("bananas", "bananas"), ("cups flour", "flour"), ("eggs", "eggs"),
                              ("g butter", "butter"), ("tsp baking soda", "baking soda"),
                              ("cup walnuts", "walnuts"))))


def _recipe_card(rng, *, theme, scale, abstain):
    inv = Inventor(rng)
    w, h = LOGICAL_SIZES["recipe_card"]
    p = _PAPER[theme]
    c = Canvas(w, h, scale=scale, theme=theme, bg=p["desk"])
    hand, hand_name = pick_hand(rng, rng.choice(p["inks"]))
    rule = 34
    _paper(c, theme, 30, 30, w - 30, h - 30, ruled=rule, first=90)
    c.line(30, 30 + 90 - rule + 4, w - 30, 30 + 90 - rule + 4, color=p["margin"], width=2)
    name, ingr = rng.choice(_RECIPES)
    k = rng.randint(4, 6)
    chosen = rng.sample(ingr, k)
    size = rng.choice((18, 19))
    wr = _Writer(c, rng, hand, size)
    wr.line(60, 30 + 90 - rule - 4, [(name, {"field": "title"})])
    serves = rng.choice((4, 6, 8, 10, 12))
    wr.line(w - 200, 30 + 90 - rule - 4, [("Serves", None),
                                          (str(serves), {"field": "serves", "kind": "number"})])
    qtys = []
    y = 30 + 90 - 4
    col_x = (60, 380)
    for i, (unit, ing) in enumerate(chosen):
        q = rng.choice((1, 2, 3, 4)) if not unit.startswith("g ") else rng.choice((50, 100, 150, 200, 250))
        qtys.append(q)
        x = col_x[i // 3]
        yy = y + (i % 3) * rule
        shown_unit = unit
        if q == 1 and unit.split()[0].endswith("s"):          # "1 cup oats", "1 egg"
            first, _, rest = unit.partition(" ")
            shown_unit = (first[:-1] + " " + rest).strip()
        wr.line(x, yy, [(str(q), {"field": f"qty_{i}", "kind": "number"}),
                        (shown_unit, {"field": f"ing_{i}"})])
    temp = rng.choice((325, 350, 375, 400))
    mins = rng.choice((20, 25, 30, 35, 40, 45))
    y2 = y + 3 * rule
    wr.line(60, y2, [("Bake at", None), (f"{temp} F", {"field": "temp", "kind": "number",
                                                       "value": str(temp)}),
                     ("for", None), (f"{mins} min", {"field": "mins", "kind": "number",
                                                     "value": str(mins)})])
    wr.line(60, y2 + rule, [(rng.choice(("Cool before cutting", "Serve warm",
                                         "Keeps for 3 days", "Grandma's recipe")), None)])
    ii = rng.randrange(k)
    unit, ing = chosen[ii]
    measure = unit[: -len(ing)].strip() if unit.endswith(ing) and unit != ing else ""
    qq = (f"How many {measure} of {ing} does the recipe call for?" if measure else
          f"How many {ing} does the recipe call for?")
    qs = [Question("q_qty", "lookup", qq, str(qtys[ii]), canon="number",
                   fields=[f"qty_{ii}", f"ing_{ii}"]),
          Question("q_count", "count", "How many ingredients are listed?", str(k),
                   canon="number", fields=[f"ing_{i}" for i in range(k)]),
          rng.choice((Question("q_temp", "lookup", "At what temperature is it baked?",
                               str(temp), canon="number", fields=["temp"]),
                      Question("q_mins", "lookup", "For how many minutes is it baked?",
                               str(mins), canon="number", fields=["mins"]))),
          rng.choice((Question("q_serves", "lookup", "How many does the recipe serve?",
                               str(serves), canon="number", fields=["serves"]),
                      Question("q_title", "lookup", "What is the recipe called?", name,
                               fields=["title"])))]
    rng.shuffle(qs)
    data = {"template": "recipe_card", "hand": hand_name, "name": name, "serves": serves,
            "ingredients": [{"qty": q, "unit": u, "item": g} for q, (u, g) in zip(qtys, chosen)],
            "temp_f": temp, "minutes": mins}
    return _finish(c, rng, abstain, qs, data, "recipe_card",
                   ("q_source", "Who is the recipe credited to?", "source", inv.person),
                   "recipe card")


# ============================================================================ day plan
_PLANS = ("Dentist", "Team call", "Lunch with Mira", "Gym", "Pick up kids", "Grocery run",
          "Haircut", "Write report", "Coffee with Jun", "Piano lesson", "Library", "Yoga",
          "Car service", "Bank", "Doctor", "Call mum")


def _day_plan(rng, *, theme, scale, abstain):
    w, h = LOGICAL_SIZES["day_plan"]
    p = _PAPER[theme]
    c = Canvas(w, h, scale=scale, theme=theme, bg=p["desk"])
    hand, hand_name = pick_hand(rng, rng.choice(p["inks"]))
    _paper(c, theme, 30, 24, w - 30, h - 24, ruled=None)
    pc, pm = p["print"], p["printmuted"]
    d = date(YEAR, rng.randint(1, 12), rng.randint(1, 28))
    from edat.media.common import WEEKDAYS
    c.text(60, 46, WEEKDAYS[d.weekday()], size=24, bold=True, color=pc, field="weekday")
    c.text(60, 80, f"{MONTHS[d.month - 1]} {d.day}, {d.year}", size=14, color=pm,
           field="date", kind="date", value=d.isoformat())
    start = rng.choice((7, 8))
    hours = list(range(start, start + 12))
    slot = 52
    y0 = 120
    plans = rng.sample(_PLANS, rng.randint(4, 7))
    at = sorted(rng.sample(range(len(hours)), len(plans)))
    size = rng.choice((18, 19, 20))
    wr = _Writer(c, rng, hand, size)
    for k, hr in enumerate(hours):
        y = y0 + k * slot
        c.line(60, y, w - 60, y, color=p["rule"])
        lab = f"{hr % 12 or 12} {'AM' if hr < 12 else 'PM'}"
        c.text(64, y + 8, lab, size=12, color=pm, field=f"hour_{k}", kind="time",
               value=f"{hr:02d}:00")
    for j, k in enumerate(at):
        y = y0 + k * slot
        wr.line(140, y + 34, [(plans[j], {"field": f"plan_{j}"})])
    j1 = rng.randrange(len(plans))
    j2 = rng.choice([j for j in range(len(plans)) if j != j1])
    qs = [Question("q_what", "lookup",
                   f"What is planned at {hours[at[j1]] % 12 or 12} {'AM' if hours[at[j1]] < 12 else 'PM'}?",
                   plans[j1], fields=[f"hour_{at[j1]}", f"plan_{j1}"]),
          Question("q_when", "lookup", f"At what time is '{plans[j2]}' planned?",
                   f"{hours[at[j2]]:02d}:00", canon="time",
                   fields=[f"hour_{at[j2]}", f"plan_{j2}"]),
          Question("q_count", "count", "How many things are written on the plan?",
                   str(len(plans)), canon="number",
                   fields=[f"plan_{j}" for j in range(len(plans))]),
          Question("q_last", "lookup", "What is the last thing planned for the day?", plans[-1],
                   fields=[f"plan_{j}" for j in range(len(plans))])]
    rng.shuffle(qs)
    data = {"template": "day_plan", "hand": hand_name, "date": d.isoformat(),
            "plans": [{"hour": f"{hours[k]:02d}:00", "what": plans[j]} for j, k in enumerate(at)]}
    return _finish(c, rng, abstain, qs, data, "day_plan",
                   ("q_place", "Where is the dentist appointment?", "where",
                    lambda: f"{Inventor(rng).surname()} Clinic"), "day plan")
