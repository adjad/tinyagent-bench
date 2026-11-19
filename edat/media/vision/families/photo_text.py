"""Photos with text: flyers, posters, signs, menus, product and parcel labels, whiteboards, event
tickets and a laptop screen, photographed at an angle (spec 05 §7.1).

The printed piece is drawn on a clean canvas (every field planted there, in clean
canvas coordinates), then composited onto a procedurally generated "photo"
background (seeded gradient, blobs and grain; no external images) with a small
perspective warp, then damaged by level:

0  composite and mild perspective only
1  + slight Gaussian blur
2  + glare (a bright translucent ellipse that covers no text) + JPEG quality 60
3  + glare + stronger blur + JPEG quality 35 (used by the V5 damage sweep)

``Rendered.damage["field_boxes"]`` maps each planted field's clean box through the
same placement and warp to its bounding box in the final image.
Planted fields: event title, date, start time, venue, ticket or dish prices, phone; product
name, price, best-before date and net weight on a product label; meeting title, time, room and
task owners on a whiteboard; recipient, tracking number, weight and ship date on a parcel label;
unread count, senders, subjects and times on a photographed screen; seat on a ticket.
Question types: event (title, date, time, venue), lookup (price, phone, weight, owner...),
absence, reading.

Nine templates (a count coprime with 10). The screen is photographed at a stronger angle.
"""
from __future__ import annotations

import io
import math
import random
from datetime import date

from PIL import Image, ImageDraw, ImageFilter

from edat.media.common import (MONTHS, WEEKDAYS, Inventor, canon_text, date_long, date_short,
                               money_str, time_12h)
from edat.media.vision.canvas import Canvas
from edat.media.vision.items import ABSTAIN_ANSWER, Question, Rendered

FAMILY = "photo_text"
TEMPLATES = ("flyer", "poster", "sign", "menu", "product_label", "whiteboard", "parcel",
             "screen", "ticket")
LEGACY = ("flyer", "poster", "sign", "menu")
LOGICAL_SIZES = {"flyer": (420, 594), "poster": (480, 640), "sign": (560, 360),
                 "menu": (420, 640), "product_label": (420, 560), "whiteboard": (560, 400),
                 "parcel": (420, 600), "screen": (560, 420), "ticket": (560, 240)}
TILT = {"screen": 1.8}              # photograph jitter factor: the screen is shot at a steeper angle
YEAR = 2027
BASE_DATE = date(2027, 1, 8)
PHOTO_MARGIN = 1.25                 # the photo is 1.25x the printed piece on each side
FINAL_FACTOR = PHOTO_MARGIN         # the generator sizes V5 re-renders by the final photo
BANDS = {"light": ("accent", "danger", "ok"), "dark": ("accent", "danger", "ok", "warn")}
ADJ = ("Spring", "Summer", "Autumn", "Winter", "Moonlit", "Riverside", "Open Air", "Midweek",
       "Late Night", "Sunday", "Lantern", "Harvest")
NOUNS = {
    "flyer": ("Craft Fair", "Jazz Night", "Book Swap", "Film Club", "Plant Sale", "Quiz Night"),
    "poster": ("Choir Concert", "Poetry Slam", "Dance Social", "Art Walk", "Chess Open",
               "Food Market"),
    "sign": ("Craft Fair", "Food Market", "Plant Sale", "Book Swap", "Art Walk", "Flea Market"),
    "menu": ("Supper Club", "Tasting Night", "Soup Social", "Brunch Club", "Pie Night"),
}
TAGLINES = ("Live music, food and friends", "Free for members", "Bring a friend",
            "Local makers and growers", "Rain or shine", "Everyone welcome")
NOTES = ("All ages welcome", "Cash and card accepted", "Step-free access", "Limited seats")
DISHES = ("Squash soup", "Herb flatbread", "Lentil stew", "Grilled peppers", "Mushroom tart",
          "Barley salad", "Roast carrots", "Bean chili", "Apple crumble", "Rice pudding",
          "Leek gratin", "Pear sorbet")
DESCS = ("house recipe", "made fresh daily", "served warm", "seasonal", "local produce",
         "small or large plate")


def render(rng: random.Random, *, template: str, theme: str, scale: float, abstain: bool,
           damage: int | None = None) -> Rendered:
    if template not in LOGICAL_SIZES:
        raise ValueError(f"unknown template {template!r}")
    auto = rng.randrange(3)             # always drawn, so the content never depends on damage
    level = auto if damage is None else int(damage)
    if not 0 <= level <= 3:
        raise ValueError(f"damage level {damage!r} is not 0-3")
    inv = Inventor(rng)
    if template not in LEGACY:
        c = Canvas(*LOGICAL_SIZES[template], scale=scale, theme=theme)
        questions, data = {"product_label": _product_label, "whiteboard": _whiteboard,
                           "parcel": _parcel, "screen": _screen,
                           "ticket": _ticket}[template](c, rng, inv, abstain)
        final, dmg = _photograph(c, rng, level, TILT.get(template, 1.0))
        return Rendered(c, template, questions, data, default_year=YEAR, clean_image=c.image,
                        damage=dmg, final_image=final)
    ev = _event(rng, inv, template)
    c = Canvas(*LOGICAL_SIZES[template], scale=scale, theme=theme)
    band = rng.choice(BANDS[theme])
    {"flyer": _flyer, "poster": _poster, "sign": _sign, "menu": _menu}[template](c, ev, band)

    questions = [
        Question("q_title", "event", "What is the name of the event?", ev["title"],
                 fields=["title"]),
        Question("q_date", "event", "On what date is the event? Give the date.", ev["date"],
                 canon="date", fields=["date"]),
        (Question("q_time", "event", "What time does the event start?", ev["start"],
                  canon="time", fields=["time"]) if rng.random() < 0.5 else
         Question("q_venue", "event", "Where is the event held? Give the venue name.",
                  ev["venue"], fields=["venue"])),
        Question("q_reading", "reading", "Read all the text on the " + template + ".", "",
                 kind="reading"),
    ]
    if template == "menu":
        i = rng.randrange(len(ev["dishes"]))
        d = ev["dishes"][i]
        last = Question("q_price", "lookup", f"How much does the {d['name']} cost?",
                        f"{d['price']:.2f}", canon="money", fields=[f"dish_{i}", f"price_{i}"])
    elif template == "sign":
        last = Question("q_phone", "lookup", "What phone number is given for information?",
                        ev["phone"], canon="phone", fields=["phone"])
    else:
        last = Question("q_price", "lookup", "How much is a ticket?", f"{ev['price']:.2f}",
                        canon="money", fields=["price"])
    if abstain:
        last = _abstain(c, rng, ev, template, last)
    questions.append(last)

    final, dmg = _photograph(c, rng, level)
    return Rendered(c, template, questions, ev, default_year=YEAR, clean_image=c.image,
                    damage=dmg, final_image=final)


# ------------------------------------------------------------------ invented event

def _event(rng: random.Random, inv: Inventor, template: str) -> dict:
    day = inv.day(BASE_DATE, 330)
    start = inv.clock(10, 20, 15)
    h, m = map(int, start.split(":"))
    end = f"{min(h + rng.choice((2, 3)), 23):02d}:{m:02d}"
    venue = rng.choice((f"{inv.surname()} Hall", f"The {inv.brand()} Room",
                        f"{inv.town()} Library", f"{inv.surname()} Park Pavilion",
                        f"{inv.brand()} Gallery"))
    fmt = rng.randrange(4)
    if fmt == 0:
        date_text = date_long(day)
    elif fmt == 1:
        date_text = date_short(day)
    elif fmt == 2:
        date_text = f"{WEEKDAYS[day.weekday()][:3]} {MONTHS[day.month - 1][:3]} {day.day}".upper()
    else:
        date_text = day.strftime("%m/%d/%Y")
    lead = rng.choice(("", "Doors ", "Starts ", "From "))
    time_text = f"{time_12h(start)} - {time_12h(end)}" if not lead else lead + time_12h(start)
    price = rng.choice((5, 8, 10, 12, 15, 18, 20, 25)) + rng.choice((0, 0, 0.5))
    price_str = money_str(price) if price % 1 else f"${int(price)}"
    dishes = []
    for name in rng.sample(DISHES, rng.randint(4, 6)):
        dishes.append({"name": name, "desc": rng.choice(DESCS),
                       "price": rng.randint(6, 28) + rng.choice((0, 0.5))})
    return {"title": f"{rng.choice(ADJ)} {rng.choice(NOUNS[template])}", "date": day.isoformat(),
            "date_text": date_text, "start": start, "end": end, "time_text": time_text,
            "venue": venue, "address": f"{inv.street_address()}, {inv.town()}",
            "price": price, "price_text": f"{rng.choice(('Tickets', 'Entry', 'Admission'))} "
                                          f"{price_str}",
            "phone": inv.phone(), "tagline": rng.choice(TAGLINES), "note": rng.choice(NOTES),
            "host": f"{inv.brand()} {rng.choice(('Society', 'Collective', 'Club', 'Guild'))}",
            "dishes": dishes if template == "menu" else []}


def _fit(c: Canvas, s: str, size: float, maxw: float, *, bold: bool = False,
         lo: float = 12) -> float:
    while size > lo and c.text_width(s, size, bold) > maxw:
        size -= 1
    return size


def _when(c: Canvas, ev: dict, x: float, y: float, size: float, gap: float, *, center: bool,
          maxw: float) -> None:
    put = c.text_center if center else c.text
    put(x, y, ev["date_text"], size=_fit(c, ev["date_text"], size, maxw, bold=True), bold=True,
        field="date", kind="date", value=ev["date"])
    put(x, y + gap, ev["time_text"], size=_fit(c, ev["time_text"], size - 2, maxw),
        field="time", kind="time", value=ev["start"])


# ------------------------------------------------------------------ the printed pieces

def _flyer(c: Canvas, ev: dict, band: str) -> None:
    w, h = c.logical
    c.rect(10, 10, w - 10, h - 10, outline=band, width=2)
    c.rect(22, 22, w - 22, 178, fill=band)
    for k in range(5):
        x = w / 2 - 110 + 48 * k
        c.ellipse(x - 14, 452, x + 14, 480, fill=band if k % 2 else "surface")
    c.text_center(w / 2, 38, f"{ev['host']} presents", size=12, color="accent_fg")
    ts = _fit(c, ev["title"], 30, w - 72, bold=True, lo=16)
    c.text_center(w / 2, 86 - ts / 2, ev["title"], size=ts, bold=True, color="accent_fg",
                  field="title")
    c.text_center(w / 2, 140, ev["tagline"], size=13, color="accent_fg")
    _when(c, ev, w / 2, 204, 20, 36, center=True, maxw=w - 64)
    c.text_center(w / 2, 284, ev["venue"], size=_fit(c, ev["venue"], 18, w - 64), field="venue")
    c.text_center(w / 2, 312, ev["address"], size=_fit(c, ev["address"], 12, w - 64, lo=10),
                  color="muted")
    c.text_center(w / 2, 356, ev["price_text"], size=22, bold=True, color=band, field="price",
                  kind="money", value=f"{ev['price']:.2f}")
    c.text_center(w / 2, 402, ev["note"], size=12, color="muted")
    c.text_center(w / 2, 528, "Please recycle this flyer", size=11, color="muted")


def _poster(c: Canvas, ev: dict, band: str) -> None:
    w, h = c.logical
    c.rect(24, 24, w - 24, 296, fill="surface")
    for k, r in enumerate((112, 84, 56, 28)):
        c.ellipse(w / 2 - r, 160 - r, w / 2 + r, 160 + r, fill=band if k % 2 == 0 else "surface")
    c.polygon([(40, 280), (130, 200), (220, 280)], fill="border")
    c.polygon([(w - 220, 280), (w - 130, 210), (w - 40, 280)], fill="border")
    ts = _fit(c, ev["title"], 32, w - 56, bold=True, lo=16)
    c.text_center(w / 2, 318, ev["title"], size=ts, bold=True, field="title")
    c.text_center(w / 2, 364, ev["tagline"], size=14, color="muted")
    c.line(40, 396, w - 40, 396, color=band, width=2)
    c.text(40, 412, "WHEN", size=11, color="muted")
    _when(c, ev, 40, 430, 16, 26, center=False, maxw=200)
    c.text(264, 412, "WHERE", size=11, color="muted")
    c.text(264, 430, ev["venue"], size=_fit(c, ev["venue"], 16, w - 300), field="venue")
    c.text(264, 456, ev["address"], size=_fit(c, ev["address"], 11, w - 300, lo=9), color="muted")
    c.text_center(w / 2, 510, ev["price_text"], size=24, bold=True, color=band, field="price",
                  kind="money", value=f"{ev['price']:.2f}")
    c.text_center(w / 2, 590, f"Presented by {ev['host']}", size=11, color="muted")


def _sign(c: Canvas, ev: dict, band: str) -> None:
    w, h = c.logical
    c.rect(8, 8, w - 8, h - 8, outline=band, width=6)
    c.polygon([(460, 238), (500, 238), (500, 226), (528, 252), (500, 278), (500, 266),
               (460, 266)], fill=band)
    ts = _fit(c, ev["title"], 34, w - 80, bold=True, lo=18)
    c.text_center(w / 2, 34, ev["title"], size=ts, bold=True, field="title")
    c.text_center(w / 2, 82, f"Hosted by {ev['host']}", size=12, color="muted")
    c.line(40, 106, w - 40, 106, color=band, width=2)
    _when(c, ev, w / 2, 122, 20, 34, center=True, maxw=w - 80)
    at = f"At {ev['venue']}"
    c.text_center(w / 2, 196, at, size=_fit(c, at, 18, w - 80), field="venue", value=ev["venue"])
    c.text(444 - c.text_width("Entrance this way", 14), 245, "Entrance this way", size=14,
           color="muted")
    c.text_center(w / 2, 298, f"Info {ev['phone']}", size=15, field="phone", kind="phone",
                  value=ev["phone"])


def _menu(c: Canvas, ev: dict, band: str) -> None:
    w, h = c.logical
    c.rect(22, 22, w - 22, 120, fill=band)
    c.text_center(w / 2, 34, f"{ev['host']} presents", size=12, color="accent_fg")
    ts = _fit(c, ev["title"], 28, w - 72, bold=True, lo=16)
    c.text_center(w / 2, 82 - ts / 2, ev["title"], size=ts, bold=True, color="accent_fg",
                  field="title")
    _when(c, ev, w / 2, 136, 16, 24, center=True, maxw=w - 64)
    c.text_center(w / 2, 186, ev["venue"], size=_fit(c, ev["venue"], 15, w - 64), field="venue")
    c.line(40, 216, w - 40, 216, color=band, width=2)
    c.text_center(w / 2, 228, "MENU", size=12, color="muted")
    y = 256
    for i, d in enumerate(ev["dishes"]):
        c.text(40, y, d["name"], size=14, field=f"dish_{i}", value=d["name"])
        c.text_right(w - 40, y, money_str(d["price"]), size=14, field=f"price_{i}", kind="money",
                     value=f"{d['price']:.2f}")
        c.text(40, y + 19, d["desc"], size=10, color="muted")
        y += 42
    c.text_center(w / 2, 590, "Reservations recommended", size=11, color="muted")


def _abstain(c: Canvas, rng: random.Random, ev: dict, template: str, last: Question) -> Question:
    """Replace the last scored question with one whose answer is not readable or not there."""
    drawn = [canon_text(r.text) for r in c.runs]
    if rng.random() < 0.5:
        name = last.fields[-1]
        c.blur_field(name, radius=7)
        return Question(last.qid + "_abstain", "absence", last.question, ABSTAIN_ANSWER,
                        kind="abstain", fields=[name])
    if template == "menu":
        for dish in DISHES:
            if not any(canon_text(dish) in t for t in drawn):
                c.absent("missing_dish", dish)
                return Question("q_missing_dish", "absence", f"How much does the {dish} cost?",
                                ABSTAIN_ANSWER, kind="abstain", fields=["missing_dish"])
    if template == "sign":
        c.absent("price", money_str(ev["price"]), kind="money")
        return Question("q_price_abstain", "absence", "How much is a ticket?", ABSTAIN_ANSWER,
                        kind="abstain", fields=["price"])
    c.absent("phone", ev["phone"], kind="phone")
    return Question("q_phone_abstain", "absence", "What phone number is given for information?",
                    ABSTAIN_ANSWER, kind="abstain", fields=["phone"])


# ------------------------------------------------------------------ the new printed pieces

def _tail(c: Canvas, rng: random.Random, abstain: bool, qs: list, normal: Question,
          blur: str, absent: tuple) -> None:
    """The last scored question: normal, or an abstention (blurred field or an absent field)."""
    if not abstain:
        qs.append(normal)
        return
    if rng.random() < 0.5:
        c.blur_field(blur, radius=7)
        qs.append(Question(normal.qid + "_abstain", "absence", normal.question, ABSTAIN_ANSWER,
                           kind="abstain", fields=[blur]))
        return
    qid, text, name, value, kind = absent
    c.absent(name, value, kind)
    qs.append(Question(qid, "absence", text, ABSTAIN_ANSWER, kind="abstain", fields=[name]))


def _reading(what: str) -> Question:
    return Question("q_reading", "reading", f"Read all the text on the {what}.", "", kind="reading")


def _barcode(c: Canvas, rng: random.Random, x0: float, x1: float, y0: float, y1: float,
             digits: str) -> None:
    x = x0
    while x < x1 - 4:
        bw = max(rng.choice((1, 1, 2, 3)), 1.5 / c.scale)
        if rng.random() < 0.6:
            c.rect(x, y0, x + bw, y1, fill="fg")
        x += bw + rng.choice((1, 2))
    c.text_center((x0 + x1) / 2, y1 + 8, digits, size=12, color="muted")


_PRODUCT_NOUNS = ("Oat Granola", "Olive Oil", "Green Tea", "Tomato Soup", "Wild Honey",
                  "Rye Crackers", "Lentil Chips", "Apple Butter")


def _product_label(c: Canvas, rng: random.Random, inv: Inventor, abstain: bool):
    w, h = c.logical
    band = rng.choice(BANDS[c.theme])
    brand = inv.brand()
    product = rng.choice(_PRODUCT_NOUNS)
    price = rng.choice((2, 3, 4, 5, 6, 8, 9, 12)) + rng.choice((0.29, 0.49, 0.99, 0.0))
    grams = rng.choice((150, 200, 250, 340, 400, 500, 750))
    day = inv.day(BASE_DATE, 330)
    lot = f"L-{rng.randint(10000, 99999)}"
    c.rect(10, 10, w - 10, h - 10, outline="border", width=2)
    c.rect(22, 22, w - 22, 128, fill=band)
    c.text_center(w / 2, 36, brand.upper(), size=14, color="accent_fg")
    ps = _fit(c, product, 30, w - 72, bold=True, lo=16)
    c.text_center(w / 2, 90 - ps / 2, product, size=ps, bold=True, color="accent_fg",
                  field="product")
    c.text_center(w / 2, 162, f"Net wt {grams} g", size=18, field="weight", kind="number",
                  value=str(grams))
    ptxt = money_str(price)
    c.text_center(w / 2, 212, ptxt, size=38, bold=True, color=band, field="price", kind="money",
                  value=f"{price:.2f}")
    c.text_center(w / 2, 280, f"Best before {date_short(day)}", size=16, field="best",
                  kind="date", value=day.isoformat())
    c.text_center(w / 2, 314, f"Lot {lot}", size=13, color="muted", field="lot", value=lot)
    c.text_center(w / 2, 348, "Keep cool and dry. Reseal after opening.", size=12, color="muted")
    digits = "".join(str(rng.randint(0, 9)) for _ in range(12))
    _barcode(c, rng, 70, w - 70, 394, 470, f"{digits[0]} {digits[1:7]} {digits[7:]}")
    qs = [
        Question("q_product", "lookup", "What is the name of the product on the label?", product,
                 fields=["product"]),
        Question("q_price", "lookup", "What price is printed on the label?", f"{price:.2f}",
                 canon="money", fields=["price"]),
        Question("q_best", "lookup", "What is the best-before date?", day.isoformat(),
                 canon="date", fields=["best"]),
    ]
    _tail(c, rng, abstain, qs,
          Question("q_weight", "lookup", "What is the net weight in grams? Give the number.",
                   str(grams), canon="number", fields=["weight"]),
          "weight", ("q_kcal", "How many calories per serving are printed on the label?",
                     "kcal", f"{rng.randint(90, 480)} kcal", "text"))
    qs.append(_reading("label"))
    return qs, {"brand": brand, "product": product, "price": price, "grams": grams,
                "best_before": day.isoformat(), "lot": lot}


_BOARD_TOPICS = ("Sprint plan", "Launch review", "Kitchen refit", "Event setup", "Budget talk",
                 "Roadmap sync")
_TASKS = ("Slides", "Venue", "Catering", "Invites", "Budget", "Signage", "Tickets", "Demo")


def _whiteboard(c: Canvas, rng: random.Random, inv: Inventor, abstain: bool):
    w, h = c.logical
    title = rng.choice(_BOARD_TOPICS)
    day = inv.day(BASE_DATE, 330)
    start = inv.clock(9, 17, 15)
    room = rng.choice((f"Studio {rng.randint(1, 9)}", f"Room {rng.choice('ABCD')}{rng.randint(1, 9)}",
                       f"Lab {rng.randint(1, 6)}"))
    n = 3
    tasks = rng.sample(_TASKS, n)
    owners = []
    while len(owners) < n:
        g = inv.given()
        if g not in owners:
            owners.append(g)
    date_text = f"{WEEKDAYS[day.weekday()][:3]} {MONTHS[day.month - 1][:3]} {day.day}"
    c.rect(0, 0, w, h, fill=(196, 198, 204))
    c.rect(12, 12, w - 12, h - 42, fill="bg", outline=(150, 152, 160), width=2)
    c.rect(70, h - 34, w - 70, h - 16, fill=(160, 162, 170), radius=3)
    c.text(40, 34, title, size=30, bold=False, field="title", color="accent")
    c.line(40, 78, 40 + c.text_width(title, 30) + 20, 78, color="accent", width=2)
    c.text(40, 96, f"Date: {date_text}", size=18, field="date", kind="date", value=day.isoformat())
    c.text(40, 128, f"Time: {time_12h(start)}", size=18, field="time", kind="time", value=start)
    c.text(40, 160, f"Room: {room}", size=18, field="room", value=room)
    c.text(40, 206, "To do", size=16, color="danger")
    for i in range(n):
        y = 238 + i * 34
        c.text(52, y, tasks[i], size=17, field=f"task_{i}", value=tasks[i])
        c.text(220, y, "-", size=17, color="muted")
        c.text(240, y, owners[i], size=17, color="ok", field=f"owner_{i}", value=owners[i])
    i = rng.randrange(n)
    qs = [
        Question("q_title", "lookup", "What is written as the heading of the whiteboard?", title,
                 fields=["title"]),
        Question("q_time", "event", "At what time is the meeting?", start, canon="time",
                 fields=["time"]),
        Question("q_room", "event", "In which room is the meeting?", room, fields=["room"]),
    ]
    _tail(c, rng, abstain, qs,
          Question("q_owner", "lookup", f'Who is responsible for "{tasks[i]}"?', owners[i],
                   fields=[f"task_{i}", f"owner_{i}"]),
          f"owner_{i}", ("q_budget", "What budget is written on the whiteboard?", "budget",
                         f"{rng.randint(2, 9)}00.00 USD", "money"))
    qs.append(_reading("whiteboard"))
    return qs, {"title": title, "date": day.isoformat(), "time": start, "room": room,
                "tasks": [{"task": t, "owner": o} for t, o in zip(tasks, owners)]}


def _parcel(c: Canvas, rng: random.Random, inv: Inventor, abstain: bool):
    w, h = c.logical
    to_name = inv.person()
    street = inv.street_address()
    town = inv.town()
    sender = inv.merchant()
    kg = f"{rng.randint(1, 19)}.{rng.randint(0, 9)}"
    day = inv.day(BASE_DATE, 330)
    track = inv.code(2, 8)
    service = rng.choice(("EXPRESS", "STANDARD", "PRIORITY"))
    c.rect(10, 10, w - 10, h - 10, outline="fg", width=3)
    c.rect(10, 10, w - 10, 70, fill="fg")
    c.text(26, 26, service, size=26, bold=True, color="bg", field="service", value=service)
    c.text_right(w - 26, 34, f"Ship date {date_short(day)}", size=13, color="bg", field="ship",
                 kind="date", value=day.isoformat())
    c.text(26, 86, "FROM", size=11, color="muted")
    c.text(26, 102, sender, size=15)
    c.line(10, 134, w - 10, 134, color="fg", width=2)
    c.text(26, 148, "TO", size=11, color="muted")
    c.text(26, 166, to_name, size=24, field="to_name", value=to_name)
    c.text(26, 202, street, size=16)
    c.text(26, 228, town, size=16)
    c.line(10, 270, w - 10, 270, color="fg", width=2)
    c.text(26, 284, "WEIGHT", size=11, color="muted")
    c.text(26, 302, f"{kg} kg", size=24, field="weight", kind="number", value=kg)
    c.rect(w - 150, 284, w - 26, 332, outline="danger", width=3, radius=4)
    c.text_center(w - 88, 298, "FRAGILE", size=18, color="danger")
    c.line(10, 350, w - 10, 350, color="fg", width=2)
    _barcode(c, rng, 40, w - 40, 368, 450, f"{track}")
    c.text_center(w / 2, 482, f"Tracking {track}", size=18, field="tracking", value=track)
    c.text_center(w / 2, 520, "Handle with care. Do not stack.", size=12, color="muted")
    qs = [
        Question("q_to", "lookup", "Who is the parcel addressed to?", to_name, fields=["to_name"]),
        Question("q_track", "lookup", "What is the tracking number on the label?", track,
                 fields=["tracking"]),
        Question("q_service", "lookup", "Which service level is printed at the top of the label?",
                 service, fields=["service"]),
    ]
    _tail(c, rng, abstain, qs,
          Question("q_weight", "lookup", "How many kilograms does the parcel weigh?", kg,
                   canon="number", fields=["weight"]),
          "weight", ("q_phone", "What phone number is printed for the recipient?", "to_phone",
                     inv.phone(), "phone"))
    qs.append(_reading("parcel label"))
    return qs, {"to": to_name, "street": street, "town": town, "sender": sender, "kg": kg,
                "ship_date": day.isoformat(), "tracking": track, "service": service}


def _screen(c: Canvas, rng: random.Random, inv: Inventor, abstain: bool):
    w, h = c.logical
    n = 3
    names = []
    while len(names) < n:
        p = inv.person()
        if p not in names:
            names.append(p)
    subjects = rng.sample(("Your invoice is ready", "Schedule for next week", "Photos from the trip",
                           "Question about the draft", "Updated agenda", "Shipping confirmation",
                           "Draft budget attached", "Venue options"), n)
    unread = [rng.random() < 0.5 for _ in range(n)]
    unread[rng.randrange(n)] = True
    cnt = sum(unread)
    minute = rng.randint(8 * 60, 19 * 60)
    times = []
    for _ in range(n):
        times.append(f"{minute // 60:02d}:{minute % 60:02d}")
        minute -= rng.randint(9, 80)
    c.rect(0, 0, w, h, fill=(22, 22, 26), radius=14)
    c.rect(22, 20, w - 22, h - 56, fill="bg")
    c.rect(0, h - 44, w, h, fill=(70, 70, 76))
    c.rect(w / 2 - 50, h - 44, w / 2 + 50, h - 34, fill=(48, 48, 52), radius=4)
    c.rect(22, 20, w - 22, 68, fill="surface")
    c.text(40, 34, f"{inv.brand()} Mail", size=16, color="accent")
    c.text_right(w - 96, 38, "Unread", size=12, color="muted")
    c.rect(w - 84, 31, w - 40, 59, fill="accent", radius=14)
    c.text_center(w - 62, 36, str(cnt), size=15, color="accent_fg", field="unread", kind="number",
                  value=str(cnt))
    y = 84
    for i in range(n):
        c.text(40, y, names[i], size=14, color="fg" if unread[i] else "muted", field=f"sender_{i}",
               value=names[i])
        c.text_right(w - 40, y + 1, time_12h(times[i]), size=12, color="muted",
                     field=f"time_{i}", kind="time", value=times[i])
        c.text(40, y + 24, subjects[i], size=13, field=f"subject_{i}", value=subjects[i])
        c.line(40, y + 56, w - 40, y + 56, color="grid")
        y += 70
    ia, ib, ic = rng.sample(range(n), 3)
    qs = [
        Question("q_unread", "count", "How many unread emails does the badge show?", str(cnt),
                 canon="number", fields=["unread"]),
        Question("q_from", "who_said", f'Who sent the email with the subject "{subjects[ia]}"?',
                 names[ia], fields=[f"subject_{ia}", f"sender_{ia}"]),
        Question("q_time", "lookup", f"At what time was the email from {names[ib]} received?",
                 times[ib], canon="time", fields=[f"sender_{ib}", f"time_{ib}"]),
    ]
    _tail(c, rng, abstain, qs,
          Question("q_subject", "lookup", f"What is the subject of the email from {names[ic]}?",
                   subjects[ic], fields=[f"sender_{ic}", f"subject_{ic}"]),
          f"subject_{ic}", ("q_attach", "What is the name of the attached file?", "attachment",
                            inv.code(3, 3) + ".pdf", "text"))
    qs.append(_reading("screen"))
    return qs, {"unread": cnt, "emails": [{"sender": names[i], "subject": subjects[i],
                                           "time": times[i], "unread": unread[i]}
                                          for i in range(n)]}


def _ticket(c: Canvas, rng: random.Random, inv: Inventor, abstain: bool):
    w, h = c.logical
    ev = _event(rng, inv, "flyer")
    band = rng.choice(BANDS[c.theme])
    row, seat = rng.choice("ABCDEFGH"), rng.randint(1, 40)
    num = inv.code(2, 7)
    c.rect(8, 8, w - 8, h - 8, fill="surface", outline=band, radius=14, width=3)
    c.rect(8, 8, 18, h - 8, fill=band)
    for k in range(10):                    # perforation between the ticket and its stub
        c.ellipse(398, 20 + k * 21, 404, 26 + k * 21, fill="bg")
    ts = _fit(c, ev["title"], 28, 340, bold=True, lo=16)
    c.text(40, 28, ev["title"], size=ts, bold=True, field="title")
    c.text(40, 76, ev["date_text"], size=_fit(c, ev["date_text"], 18, 340, bold=True),
           field="date", kind="date", value=ev["date"])
    c.text(40, 108, ev["time_text"], size=_fit(c, ev["time_text"], 16, 340), field="time",
           kind="time", value=ev["start"])
    c.text(40, 142, ev["venue"], size=_fit(c, ev["venue"], 16, 340), field="venue")
    c.text(40, 190, f"Ticket {num}", size=12, color="muted", field="number", value=num)
    c.text(420, 36, "ROW", size=11, color="muted")
    c.text(420, 52, row, size=30, bold=True, color=band, field="row", value=row)
    c.text(420, 108, "SEAT", size=11, color="muted")
    c.text(420, 124, str(seat), size=30, bold=True, color=band, field="seat", kind="number",
           value=str(seat))
    c.text(420, 184, money_str(ev["price"]), size=16, field="price", kind="money",
           value=f"{ev['price']:.2f}")
    qs = [
        Question("q_title", "event", "What is the name of the event on the ticket?", ev["title"],
                 fields=["title"]),
        Question("q_date", "event", "On what date is the event? Give the date.", ev["date"],
                 canon="date", fields=["date"]),
        Question("q_row", "lookup", "Which row letter is printed on the ticket?", row,
                 fields=["row"]),
    ]
    _tail(c, rng, abstain, qs,
          Question("q_seat", "lookup", "Which seat number is printed on the ticket?", str(seat),
                   canon="number", fields=["seat"]),
          "seat", ("q_gate", "Which gate is printed on the ticket?", "gate",
                   f"{rng.choice('ABCD')}{rng.randint(1, 12)}", "text"))
    qs.append(_reading("ticket"))
    return qs, {"title": ev["title"], "date": ev["date"], "time": ev["start"], "venue": ev["venue"],
                "row": row, "seat": seat, "number": num, "price": ev["price"]}


# ------------------------------------------------------------------ the photograph

def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    m = [row[:] + [v] for row, v in zip(a, b)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[piv] = m[piv], m[col]
        for r in range(n):
            if r != col and m[r][col]:
                f = m[r][col] / m[col][col]
                m[r] = [x - f * y for x, y in zip(m[r], m[col])]
    return [m[i][n] / m[i][i] for i in range(n)]


def _homography(src, dst) -> list[float]:
    """Coefficients (a..h) with dst = ((a x + b y + c) / (g x + h y + 1), (d x + e y + f) / ...)."""
    a, b = [], []
    for (x, y), (u, v) in zip(src, dst):
        a.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        b.append(u)
        a.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        b.append(v)
    return _solve(a, b)


def _apply(hm: list[float], x: float, y: float) -> tuple[float, float]:
    d = hm[6] * x + hm[7] * y + 1
    return (hm[0] * x + hm[1] * y + hm[2]) / d, (hm[3] * x + hm[4] * y + hm[5]) / d


def _map_box(hm: list[float], box, size) -> list[int]:
    x0, y0, x1, y1 = box
    pts = [_apply(hm, x, y) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return [max(0, math.floor(min(xs))), max(0, math.floor(min(ys))),
            min(size[0], math.ceil(max(xs))), min(size[1], math.ceil(max(ys)))]


def _background(rng: random.Random, size: tuple[int, int]) -> Image.Image:
    W, H = size
    sw = 48
    sh = max(8, round(sw * H / W))
    c1 = [rng.randint(60, 200) for _ in range(3)]
    c2 = [rng.randint(30, 170) for _ in range(3)]
    small = Image.new("RGB", (sw, sh))
    small.putdata([tuple(round(a + (b - a) * y / (sh - 1)) for a, b in zip(c1, c2))
                   for y in range(sh) for _ in range(sw)])
    d = ImageDraw.Draw(small)
    for _ in range(rng.randint(4, 8)):
        cx, cy, r = rng.uniform(0, sw), rng.uniform(0, sh), rng.uniform(3, sw / 3)
        d.ellipse([cx - r, cy - r * rng.uniform(0.5, 1.5), cx + r, cy + r],
                  fill=tuple(rng.randint(20, 230) for _ in range(3)))
    img = small.filter(ImageFilter.GaussianBlur(2.5)).resize(size, Image.Resampling.BICUBIC)
    nw, nh = max(1, W // 2), max(1, H // 2)
    grain = Image.frombytes("L", (nw, nh), rng.randbytes(nw * nh)).resize(
        size, Image.Resampling.BILINEAR)
    return Image.blend(img, Image.merge("RGB", (grain, grain, grain)), 0.07)


def _overlaps(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _glare(img: Image.Image, rng: random.Random, avoid: list) -> tuple[Image.Image, dict]:
    W, H = img.size
    for k in range(300):
        f = 1 - k / 360
        ew, eh = rng.uniform(0.16, 0.3) * W * f, rng.uniform(0.08, 0.2) * H * f
        cx, cy = rng.uniform(0.12, 0.88) * W, rng.uniform(0.12, 0.88) * H
        soft = 0.15 * min(ew, eh)
        ell = (cx - ew / 2, cy - eh / 2, cx + ew / 2, cy + eh / 2)
        reach = (ell[0] - 3 * soft, ell[1] - 3 * soft, ell[2] + 3 * soft, ell[3] + 3 * soft)
        if not any(_overlaps(reach, b) for b in avoid):
            break
    else:   # the photo's corner lies outside the printed piece for any warp used here
        soft = 0.004 * min(W, H)
        ell = (0.0, 0.0, 0.045 * W, 0.045 * H)
    alpha = rng.uniform(0.45, 0.7)
    layer = Image.new("L", (W, H), 0)
    ImageDraw.Draw(layer).ellipse(ell, fill=round(255 * alpha))
    layer = layer.filter(ImageFilter.GaussianBlur(max(0.5, soft)))
    out = Image.composite(Image.new("RGB", (W, H), (255, 255, 246)), img, layer)
    return out, {"op": "glare", "ellipse": [round(v, 2) for v in ell], "alpha": round(alpha, 3),
                 "soft": round(soft, 2)}


def _photograph(c: Canvas, rng: random.Random, level: int, tilt: float = 1.0) -> tuple[Image.Image, dict]:
    pw, ph = c.size
    size = (round(pw * PHOTO_MARGIN), round(ph * PHOTO_MARGIN))
    W, H = size
    bg = _background(rng, size)
    mx = (W - pw) / 2 + rng.uniform(-0.03, 0.03) * pw
    my = (H - ph) / 2 + rng.uniform(-0.03, 0.03) * ph
    j = 0.035 * min(pw, ph) * tilt
    src = [(0, 0), (pw, 0), (pw, ph), (0, ph)]
    dst = [(mx + x + rng.uniform(-j, j), my + y + rng.uniform(-j, j)) for x, y in src]
    shadow = Image.new("L", size, 0)
    off = (0.012 * pw, 0.018 * ph)
    ImageDraw.Draw(shadow).polygon([(x + off[0], y + off[1]) for x, y in dst], fill=255)
    shadow = shadow.filter(ImageFilter.GaussianBlur(0.015 * min(pw, ph)))
    bg = Image.composite(Image.new("RGB", size, (12, 12, 14)), bg,
                         shadow.point(lambda v: v * 45 // 100))
    inv = _homography(dst, src)         # PIL maps each output pixel back into the clean image
    warped = c.image.transform(size, Image.Transform.PERSPECTIVE, inv,
                               Image.Resampling.BICUBIC)
    mask = Image.new("L", (pw, ph), 255).transform(size, Image.Transform.PERSPECTIVE, inv,
                                                   Image.Resampling.BILINEAR)
    img = Image.composite(warped, bg, mask)
    fwd = _homography(src, dst)
    ops: list[dict] = [{"op": "composite", "size": [W, H], "offset": [round(mx, 2), round(my, 2)]},
                       {"op": "perspective", "corners": [[round(x, 2), round(y, 2)]
                                                         for x, y in dst]}]
    if level >= 2:
        avoid = [_map_box(fwd, r.bbox, size) for r in c.runs]
        avoid = [[b[0] - 2, b[1] - 2, b[2] + 2, b[3] + 2] for b in avoid]
        img, op = _glare(img, rng, avoid)
        ops.append(op)
    if level >= 1:
        radius = (0.6 if level < 3 else 1.5) * c.scale
        img = img.filter(ImageFilter.GaussianBlur(radius))
        ops.append({"op": "gaussian_blur", "radius": round(radius, 3)})
    if level >= 2:
        q = 60 if level == 2 else 35
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=q, optimize=False, subsampling=0, progressive=False)
        img = Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
        ops.append({"op": "jpeg", "quality": q, "subsampling": "4:4:4"})
    boxes = {name: _map_box(fwd, f.bbox, size) for name, f in c.fields.items() if f.bbox}
    return img, {"level": level, "ops": ops, "field_boxes": boxes}
