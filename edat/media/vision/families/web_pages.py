"""Web pages: browser-like pages with a fake address bar (1280x800 logical).

Templates: product page, flight, hotel and train confirmations, article, search results,
sign-in error, order tracking, checkout, booking form and pricing page. Planted fields: price,
rating, stock, booking reference, dates, times, gate, platform, seat, headline, byline, error
message, result titles, totals, form values, plan prices.
Question types: lookup, compare, count, difference, absence (and the reading).
Every link, button and form field is recorded with ``Canvas.element`` under its visible label
(the site's navigation gives four unique names on every template; V7 "locating a named control").

Eleven templates keep every template reachable in every theme and abstention slot (the generator
picks template = index % len(TEMPLATES)).
"""
from __future__ import annotations

import random
from datetime import date, timedelta

from edat.media.common import GIVEN_NAMES, Inventor, date_long, date_short, money_str, time_12h
from edat.media.vision.canvas import Canvas
from edat.media.vision.items import ABSTAIN_ANSWER, Question, Rendered

FAMILY = "web_pages"
TEMPLATES = ("product", "flight", "hotel", "article", "search", "signin", "order", "train",
             "checkout", "booking", "pricing")
LOGICAL_SIZES = {t: (1280, 800) for t in TEMPLATES}
BASE = date(2027, 1, 4)
W, H = 1280, 800


def render(rng: random.Random, *, template: str, theme: str, scale: float,
           abstain: bool) -> Rendered:
    fn = globals().get("_" + template)
    if template not in TEMPLATES or fn is None:
        raise ValueError(f"unknown template {template!r}")
    inv = Inventor(rng)
    c = Canvas(W, H, scale=scale, theme=theme)
    brand = inv.brand()
    domain = f"{brand.lower()}.example"
    path = fn.__doc__.split()[0]
    _chrome(c, brand, domain, path)
    qs, data, year = fn(rng, inv, c, abstain)
    qs.append(Question("q_reading", "reading", "Read the screen.", "", kind="reading"))
    data = {"site": brand, "domain": domain, **data}
    return Rendered(c, template, qs, data, default_year=year or 2027)


def _chrome(c: Canvas, brand: str, domain: str, path: str) -> None:
    c.rect(0, 0, W, 76, fill="surface")
    for i, col in enumerate(("danger", "warn", "ok")):
        c.ellipse(20 + i * 20, 14, 32 + i * 20, 26, fill=col)
    c.rect(100, 8, 340, 36, fill="bg", radius=8)
    c.text(116, 15, brand, size=12, color="muted")
    c.rect(24, 42, W - 24, 68, fill="bg", outline="border", radius=13)
    c.text(44, 48, f"https://{domain}/{path}", size=13, field="url", value=domain)
    c.line(0, 120, W, 120, color="border")
    c.text(40, 80, brand, size=26, bold=True, color="accent")
    c.element(brand, 34, 76, 46 + c.text_width(brand, 26), 114, role="link")
    for i, s in enumerate(("Shop", "Deals", "Support", "Account")):
        x = W - 340 + i * 84
        c.text(x, 88, s, size=14, color="muted")
        c.element(s, x - 8, 82, x + c.text_width(s, 14) + 8, 112, role="link")


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


def _btn(c: Canvas, x0, y0, x1, y1, label: str, *, role: str = "button", fill=None, outline=None,
         color="fg", size: float = 16, radius: float = 8, field: str | None = None) -> None:
    """A labelled button, recorded as an element named by its visible label."""
    if fill is not None or outline is not None:
        c.rect(x0, y0, x1, y1, fill=fill, outline=outline, radius=radius, width=1.5)
    kw = {"field": field, "value": label} if field else {}
    c.text_center((x0 + x1) / 2, (y0 + y1) / 2, label, size=size, color=color, anchor="lm", **kw)
    c.element(label, x0, y0, x1, y1, role=role)


def _link(c: Canvas, x: float, y: float, label: str, *, size: float = 14, color="accent",
          right: bool = False) -> None:
    w = c.text_width(label, size)
    x0 = x - w if right else x
    c.text(x0, y, label, size=size, color=color)
    c.element(label, x0 - 4, y - 3, x0 + w + 4, y + size * 1.25 + 3, role="link")


def _input(c: Canvas, x0, y0, x1, y1, label: str, value: str | None = None, *, field=None,
           kind: str = "text", fval: str | None = None, size: float = 15) -> None:
    """A labelled form field: label above, box with an optional filled value; recorded as 'field'."""
    c.text(x0, y0 - 20, label, size=13, color="muted")
    c.rect(x0, y0, x1, y1, fill="bg", outline="border", radius=8)
    if value is not None:
        kw = {"field": field, "kind": kind, "value": value if fval is None else fval} if field else {}
        c.text(x0 + 14, (y0 + y1) / 2, value, size=size, anchor="lm", **kw)
    c.element(label, x0, y0, x1, y1, role="field")


def _placeholder(c: Canvas, x0, y0, x1, y1) -> None:
    c.rect(x0, y0, x1, y1, fill="surface", radius=10)
    c.line(x0 + 20, y0 + 20, x1 - 20, y1 - 20, color="border", width=2)
    c.line(x0 + 20, y1 - 20, x1 - 20, y0 + 20, color="border", width=2)


# ---------------------------------------------------------------------------- product
_NOUNS = ("Kettle", "Backpack", "Desk lamp", "Headset", "Notebook set", "Water bottle",
          "Speaker", "Toaster", "Keyboard", "Camera strap")


def _product(rng, inv, c, abstain):
    """product/p/4821"""
    name = f"{inv.brand()} {rng.choice(_NOUNS)}"
    price = inv.money(12, 180)
    listp = round(price * rng.uniform(1.12, 1.45), 2)
    rating = f"{rng.randint(32, 49) / 10:.1f}"
    reviews = rng.randint(21, 960)
    left = rng.randint(2, 40)
    _placeholder(c, 80, 160, 560, 640)
    c.text(620, 160, name, size=34, bold=True, field="product")
    c.text(620, 220, f"{rating} out of 5", size=18, color="warn", field="rating", kind="number",
           value=rating)
    c.text(800, 222, f"{reviews} reviews", size=15, color="muted", field="reviews",
           kind="number", value=str(reviews))
    c.text(620, 276, money_str(price), size=44, bold=True, field="price", kind="money",
           value=f"{price:.2f}")
    c.text(620, 340, f"List price {money_str(listp)}", size=15, color="muted", field="list_price",
           kind="money", value=f"{listp:.2f}")
    c.text(620, 390, f"{left} left in stock", size=17, color="ok", field="stock", kind="number",
           value=str(left))
    for i, col in enumerate(("accent", "fg", "danger", "ok")):
        c.ellipse(620 + i * 44, 450, 650 + i * 44, 480, fill=col)
    _btn(c, 620, 540, 900, 596, "Add to cart", fill="accent", color="accent_fg", size=20, radius=10)
    _btn(c, 920, 540, 1140, 596, "Add to wishlist", outline="border", size=16, radius=10)
    _link(c, 620, 630, "Shipping and returns")
    diff = round(listp - price, 2)
    qs = [
        Question("q_price", "lookup", "What is the current price of the product?",
                 f"{price:.2f}", canon="money", fields=["price"]),
        Question("q_rating", "lookup", "What is the customer rating out of 5?", rating,
                 canon="number", fields=["rating"]),
        Question("q_save", "difference", "How much cheaper is the current price than the "
                 "list price?", f"{diff:.2f}", canon="money", fields=["price", "list_price"]),
    ]
    _tail(c, rng, abstain, qs,
          Question("q_stock", "lookup", "How many units are left in stock?", str(left),
                   canon="number", fields=["stock"]),
          "stock", ("q_ship", "What is the shipping fee?", "shipping",
                    f"{rng.randint(3, 9)}.{rng.randint(10, 99)} USD", "money"))
    return qs, {"product": name, "price": price, "list_price": listp, "rating": rating,
                "reviews": reviews, "stock": left}, None


# ---------------------------------------------------------------------------- flight
def _flight(rng, inv, c, abstain):
    """trips/confirmation"""
    ref = inv.booking_ref()
    towns = []
    while len(towns) < 2:
        t = inv.town()
        if t not in towns:
            towns.append(t)
    c.text(40, 144, "Booking confirmed", size=30, bold=True)
    c.text(40, 196, "Booking reference", size=14, color="muted")
    c.text(190, 188, ref, size=32, bold=True, color="accent", field="ref")
    d0 = inv.day(BASE, 200)
    d1 = d0 + timedelta(days=rng.randint(2, 9))
    mins = rng.sample(range(5 * 60, 22 * 60, 5), 2)
    flights = []
    for k, (label, a, b, day, dep) in enumerate(
            (("Outbound", towns[0], towns[1], d0, mins[0]),
             ("Return", towns[1], towns[0], d1, mins[1]))):
        x = 40 + k * 620
        c.rect(x, 250, x + 580, 700, fill="surface", radius=14)
        num = f"{rng.choice('QVKTRN')}{rng.choice('XWPLM')} {rng.randint(100, 9999)}"
        dep_s = f"{dep // 60:02d}:{dep % 60:02d}"
        arr = (dep + rng.randint(70, 330)) % (24 * 60)
        arr_s = f"{arr // 60:02d}:{arr % 60:02d}"
        gate = f"{rng.choice('ABCDE')}{rng.randint(1, 29)}"
        seat = f"{rng.randint(3, 32)}{rng.choice('ABCDEF')}"
        c.text(x + 28, 272, label, size=14, color="muted")
        c.text_right(x + 552, 270, f"Flight {num}", size=18, field=f"flight_{k}", value=num)
        c.text(x + 28, 316, f"{a} to {b}", size=22, bold=True)
        c.text(x + 28, 360, date_long(day), size=16, field=f"date_{k}", kind="date",
               value=day.isoformat())
        c.text(x + 28, 410, "Departs", size=13, color="muted")
        c.text(x + 300, 410, "Arrives", size=13, color="muted")
        c.text(x + 28, 436, time_12h(dep_s), size=32, bold=True, field=f"dep_{k}", kind="time",
               value=dep_s)
        c.text(x + 300, 436, time_12h(arr_s), size=32, field=f"arr_{k}", kind="time", value=arr_s)
        c.line(x + 28, 510, x + 552, 510)
        c.text(x + 28, 540, f"Gate {gate}", size=20, field=f"gate_{k}", value=gate)
        c.text(x + 300, 540, f"Seat {seat}", size=20, field=f"seat_{k}", value=seat)
        c.text(x + 28, 600, f"Passenger {inv.person()}", size=14, color="muted")
        flights.append({"label": label, "number": num, "date": day.isoformat(), "dep": dep_s,
                        "arr": arr_s, "gate": gate, "seat": seat})
    _btn(c, 40, 724, 240, 768, "Check in online", fill="accent", color="accent_fg", size=16)
    _btn(c, 260, 724, 460, 768, "Manage booking", outline="border", size=16)
    _link(c, 500, 736, "Download receipt", size=16)
    k = rng.randrange(2)
    first = 0 if mins[0] < mins[1] else 1
    qs = [
        Question("q_ref", "lookup", "What is the booking reference?", ref, fields=["ref"]),
        Question("q_earlier", "compare", f"Which flight departs earlier in the day, "
                 f"{flights[0]['number']} or {flights[1]['number']}? Answer with the flight "
                 "number.", flights[first]["number"],
                 fields=["flight_0", "flight_1", "dep_0", "dep_1"]),
        Question("q_date", "lookup", f"On what date does flight {flights[k]['number']} depart?",
                 flights[k]["date"], canon="date", fields=[f"flight_{k}", f"date_{k}"]),
    ]
    j = 1 - k
    _tail(c, rng, abstain, qs,
          Question("q_gate", "lookup", f"What is the gate for flight {flights[j]['number']}?",
                   flights[j]["gate"], fields=[f"flight_{j}", f"gate_{j}"]),
          f"gate_{j}", ("q_bag", "How many kilograms of baggage are included?", "baggage",
                        f"{rng.randint(15, 32)} kg", "text"))
    return qs, {"ref": ref, "flights": flights}, d0.year


# ---------------------------------------------------------------------------- hotel
def _hotel(rng, inv, c, abstain):
    """stays/booking"""
    name = f"{inv.brand()} {rng.choice(('Inn', 'Suites', 'Lodge', 'Hotel'))}"
    conf = f"HX-{rng.randint(10000, 99999)}"
    d0 = inv.day(BASE, 300)
    nights = rng.randint(2, 7)
    d1 = d0 + timedelta(days=nights)
    rate = inv.money(79, 289)
    total = round(rate * nights, 2)
    c.text(40, 144, "Your stay is booked", size=30, bold=True)
    c.text(40, 200, name, size=26, color="accent", field="hotel")
    c.text(40, 244, f"Confirmation {conf}", size=17, field="conf", value=conf)
    c.rect(40, 300, 620, 520, fill="surface", radius=14)
    c.rect(660, 300, 1240, 520, fill="surface", radius=14)
    c.text(68, 322, "Check-in", size=14, color="muted")
    c.text(68, 360, date_long(d0), size=22, field="checkin", kind="date", value=d0.isoformat())
    c.text(68, 410, "from 3:00 PM", size=15, color="muted")
    c.text(688, 322, "Check-out", size=14, color="muted")
    c.text(688, 360, date_long(d1), size=22, field="checkout", kind="date", value=d1.isoformat())
    c.text(688, 410, "until 11:00 AM", size=15, color="muted")
    c.text(40, 560, f"Room: {rng.choice(('Queen room', 'Garden suite', 'Twin room'))}", size=16)
    c.text(40, 596, f"{rng.randint(1, 4)} guests", size=16)
    c.text(40, 640, f"Rate {money_str(rate)} per night", size=16, color="muted")
    c.text(40, 684, "Total", size=22)
    c.text(140, 678, money_str(total), size=34, bold=True, field="total", kind="money",
           value=f"{total:.2f}")
    _btn(c, 660, 560, 880, 604, "Get directions", fill="accent", color="accent_fg", size=16)
    _btn(c, 900, 560, 1100, 604, "Modify booking", outline="border", size=16)
    _link(c, 660, 636, "Cancel booking", size=16, color="danger")
    qs = [
        Question("q_in", "lookup", "What is the check-in date?", d0.isoformat(), canon="date",
                 fields=["checkin"]),
        Question("q_nights", "difference", "How many nights does the stay last, from check-in "
                 "to check-out?", str(nights), canon="number", fields=["checkin", "checkout"]),
        Question("q_total", "lookup", "What is the total price of the stay?", f"{total:.2f}",
                 canon="money", fields=["total"]),
    ]
    _tail(c, rng, abstain, qs,
          Question("q_conf", "lookup", "What is the confirmation number?", conf, fields=["conf"]),
          "conf", ("q_wifi", "What is the Wi-Fi password for the room?", "wifi",
                   inv.code(3, 3).lower(), "text"))
    return qs, {"hotel": name, "conf": conf, "checkin": d0.isoformat(),
                "checkout": d1.isoformat(), "nights": nights, "rate": rate, "total": total}, d0.year


# ---------------------------------------------------------------------------- article
_HEADS = ("{t} opens a new library wing", "{t} votes to extend the tram line",
          "Harvest festival returns to {t}", "{t} school wins regional robotics cup",
          "Bridge repairs in {t} to finish early", "{t} bakery marks fifty years")
_SENT = ("The decision followed a long public meeting on Tuesday.",
         "Residents said the change was welcome and overdue.",
         "Officials expect the work to be finished within weeks.",
         "Local volunteers have offered to help with the first phase.",
         "A spokesperson declined to give further details yesterday.",
         "The project is paid for by a small community fund.",
         "Several neighbours praised the clear plans on display.",
         "More information will be posted on the town noticeboard.",
         "Opening hours will be announced later this month.",
         "Traffic is expected to be lighter once the work ends.")


def _article(rng, inv, c, abstain):
    """news/story"""
    town = inv.town()
    head = rng.choice(_HEADS).format(t=town)
    author = inv.person()
    day = inv.day(BASE, 330)
    mins = rng.randint(3, 12)
    c.text(40, 144, "LOCAL NEWS", size=12, color="accent")
    c.text(40, 172, head, size=38, bold=True, field="headline")
    c.text(40, 236, f"By {author}", size=16, field="author", value=author)
    c.text(300, 236, date_short(day), size=16, color="muted", field="date", kind="date",
           value=day.isoformat())
    c.text(460, 236, f"{mins} min read", size=16, color="muted", field="readtime", kind="number",
           value=str(mins))
    _btn(c, 990, 226, 1060, 262, "Share", outline="border", size=14, radius=18)
    _btn(c, 1070, 226, 1140, 262, "Save", outline="border", size=14, radius=18)
    _btn(c, 1150, 226, 1240, 262, "Subscribe", fill="accent", color="accent_fg", size=14, radius=18)
    _placeholder(c, 40, 290, 1000, 470)
    y = 500
    for s in rng.sample(_SENT, 8):
        c.text(40, y, s, size=16)
        y += 34
    c.rect(1040, 290, 1240, 700, fill="surface", radius=12)
    c.text(1060, 310, "Related", size=14, color="muted")
    for i in range(4):
        c.rect(1060, 350 + i * 80, 1220, 360 + i * 80, fill="border", radius=4)
        c.rect(1060, 372 + i * 80, 1180, 382 + i * 80, fill="border", radius=4)
    qs = [
        Question("q_head", "lookup", "What is the headline of the article?", head,
                 fields=["headline"]),
        Question("q_author", "lookup", "Who wrote the article?", author, fields=["author"]),
        Question("q_date", "lookup", "On what date was the article published?",
                 day.isoformat(), canon="date", fields=["date"]),
    ]
    _tail(c, rng, abstain, qs,
          Question("q_read", "lookup", "How many minutes does the article take to read?",
                   str(mins), canon="number", fields=["readtime"]),
          "readtime", ("q_editor", "Who edited the article?", "editor", inv.person(), "text"))
    return qs, {"headline": head, "author": author, "date": day.isoformat(), "minutes": mins}, day.year


# ---------------------------------------------------------------------------- search
_ADJ = ("Cozy", "Modern", "Budget", "Historic", "Quiet", "Family", "Seaside", "Hidden")
_PLACE = ("cafes", "guesthouses", "bookshops", "bike tours", "gardens", "workshops", "bakeries")


def _search(rng, inv, c, abstain):
    """search"""
    query = f"{rng.choice(_PLACE)} near {inv.town()}"
    total = rng.randint(120, 98000)
    c.rect(40, 150, 840, 196, fill="bg", outline="border", radius=23, width=2)
    c.text(64, 162, query, size=17, field="query")
    _btn(c, 740, 156, 830, 190, "Search", fill="accent", color="accent_fg", size=15, radius=17)
    for k, tab in enumerate(("All", "Images", "Maps")):
        x = 880 + k * 70
        c.text(x, 162, tab, size=15, color="accent" if k == 0 else "muted")
        c.element(tab, x - 8, 152, x + c.text_width(tab, 15) + 8, 190, role="tab")
    c.text(40, 214, f"About {total:,} results", size=14, color="muted", field="total",
           kind="number", value=str(total))
    n = rng.randint(4, 5)
    titles, domains = [], []
    while len(titles) < n:
        t = f"{rng.choice(_ADJ)} {rng.choice(_PLACE)} in {inv.town()}"
        if t not in titles:
            titles.append(t)
    while len(domains) < n:
        d = f"{inv.brand().lower()}.example"
        if d not in domains:
            domains.append(d)
    y = 262
    for i in range(n):
        c.text(40, y, f"https://{domains[i]}/page{i + 1}", size=13, color="ok",
               field=f"domain_{i}", value=domains[i])
        c.text(40, y + 26, titles[i], size=21, color="accent", field=f"title_{i}")
        c.text(40, y + 62, "Find hours, prices and photos from visitors and local guides.",
               size=14, color="muted")
        y += 106
    _link(c, 1000, 740, "Previous", size=15)
    _link(c, 1110, 740, "Next page", size=15)
    k = rng.randrange(n)
    j = rng.choice([i for i in range(n) if i != k])
    ordinal = ("first", "second", "third", "fourth", "fifth", "sixth")
    qs = [
        Question("q_total", "lookup", "How many total results does the page say were found?",
                 str(total), canon="number", fields=["total"]),
        Question("q_count", "count", "How many results are listed on this page?", str(n),
                 canon="number", fields=[f"title_{i}" for i in range(n)]),
        Question("q_nth", "lookup", f"What is the title of the {ordinal[k]} result?", titles[k],
                 fields=[f"title_{k}"]),
    ]
    _tail(c, rng, abstain, qs,
          Question("q_domain", "lookup", f'Which website domain is listed for the result titled '
                   f'"{titles[j]}"?', domains[j], fields=[f"title_{j}", f"domain_{j}"]),
          f"domain_{j}", ("q_ad", "What is the name of the sponsored result?", "sponsor",
                          inv.merchant(), "text"))
    return qs, {"query": query, "total": total, "results": [
        {"title": titles[i], "domain": domains[i]} for i in range(n)]}, None


# ---------------------------------------------------------------------------- sign-in error
def _signin(rng, inv, c, abstain):
    """account/signin"""
    email = inv.email()
    locked = rng.random() < 0.5
    c.rect(400, 160, 880, 700, fill="surface", radius=16)
    c.text_center(640, 190, "Sign in", size=32, bold=True)
    c.rect(432, 252, 848, 350, fill="bg", outline="danger", radius=8, width=2)
    if locked:
        msg, num = "Account temporarily locked", rng.choice((5, 10, 15, 30))
        line2 = f"Try again in {num} minutes"
    else:
        msg, num = "Incorrect password", rng.randint(1, 4)
        line2 = f"{num} attempts remaining"
    c.text(452, 268, msg, size=18, color="danger", field="err_msg")
    c.text(452, 304, line2, size=15, field="err_num", kind="number", value=str(num))
    c.text(432, 376, "Email", size=13, color="muted")
    c.rect(432, 398, 848, 444, fill="bg", outline="border", radius=8)
    c.text(448, 410, email, size=16, field="email")
    c.element("Email", 432, 398, 848, 444, role="field")
    c.text(432, 466, "Password", size=13, color="muted")
    c.rect(432, 488, 848, 534, fill="bg", outline="border", radius=8)
    c.text(448, 500, "**********", size=16)
    c.element("Password", 432, 488, 848, 534, role="field")
    _btn(c, 432, 560, 848, 608, "Sign in", fill="accent", color="accent_fg", size=18)
    _link(c, 432, 614, "Forgot password?", size=13)
    _link(c, 848, 614, "Create account", size=13, right=True)
    ref = f"AU-{rng.randint(10000, 99999)}"
    c.text_center(640, 640, f"Reference {ref}", size=13, color="muted", field="ref", value=ref)
    what = "minutes until it can be tried again" if locked else "attempts remain"
    qs = [
        Question("q_msg", "lookup", "What error message is shown on the page?", msg,
                 fields=["err_msg"]),
        Question("q_email", "lookup", "Which email address was typed into the form?", email,
                 fields=["email"]),
        Question("q_num", "lookup", f"How many {what}?", str(num), canon="number",
                 fields=["err_num"]),
    ]
    _tail(c, rng, abstain, qs,
          Question("q_ref", "lookup", "What reference code is shown at the bottom of the card?",
                   ref, fields=["ref"]),
          "ref", ("q_help", "What is the support phone number shown on the page?", "support",
                  inv.phone(), "phone"))
    return qs, {"error": msg, "number": num, "email": email, "ref": ref}, None


# ---------------------------------------------------------------------------- order tracking
_STATUS = ("Order placed", "Packed", "Shipped", "Delivered")
_STATUS_TEXT = ("Preparing your order", "Packed and ready", "Out for delivery", "Delivered")


def _order(rng, inv, c, abstain):
    """orders/track"""
    num = inv.code(2, 6)
    carrier = f"{inv.brand()} Express"
    placed = inv.day(BASE, 300)
    done = rng.randint(1, 3)              # number of completed steps beyond the first
    dates = [placed + timedelta(days=i * rng.randint(1, 2)) for i in range(4)]
    dates = [placed]
    for _ in range(3):
        dates.append(dates[-1] + timedelta(days=rng.randint(1, 3)))
    eta = dates[3]
    c.text(40, 144, "Track your order", size=30, bold=True)
    c.text(40, 200, f"Order {num}", size=20, field="order", value=num)
    c.text(40, 240, f"Carrier {carrier}", size=16, color="muted", field="carrier", value=carrier)
    c.text(40, 290, _STATUS_TEXT[done], size=34, bold=True, color="accent", field="status")
    c.text(40, 346, f"Estimated delivery {date_long(eta)}", size=17, field="eta", kind="date",
           value=eta.isoformat())
    c.line(160, 520, 1120, 520, color="border", width=4)
    c.line(160, 520, 160 + done * 320, 520, color="accent", width=4)
    for i in range(4):
        x = 160 + i * 320
        c.ellipse(x - 14, 506, x + 14, 534, fill="accent" if i <= done else "border")
        c.text_center(x, 556, _STATUS[i], size=16, field=f"step_{i}", value=_STATUS[i])
        c.text_center(x, 590, date_short(dates[i]), size=15, color="muted",
                      field=f"step_date_{i}", kind="date", value=dates[i].isoformat())
    _btn(c, 40, 660, 240, 704, "Contact carrier", fill="accent", color="accent_fg", size=16)
    _btn(c, 260, 660, 460, 704, "Change address", outline="border", size=16)
    _link(c, 500, 672, "Report a problem", size=16)
    ia = rng.randrange(4)
    qs = [
        Question("q_carrier", "lookup", "Which carrier is delivering the order?", carrier,
                 fields=["carrier"]),
        Question("q_eta", "lookup", "What is the estimated delivery date?", eta.isoformat(),
                 canon="date", fields=["eta"]),
        Question("q_step", "lookup", f'What date is shown under the step "{_STATUS[ia]}"?',
                 dates[ia].isoformat(), canon="date", fields=[f"step_{ia}", f"step_date_{ia}"]),
    ]
    _tail(c, rng, abstain, qs,
          Question("q_order", "lookup", "What is the order number?", num, fields=["order"]),
          "order", ("q_track", "What is the tracking number?", "tracking", inv.code(3, 8), "text"))
    return qs, {"order": num, "carrier": carrier, "status": _STATUS_TEXT[done],
                "dates": [d.isoformat() for d in dates]}, placed.year


# ---------------------------------------------------------------------------- train confirmation
def _train(rng, inv, c, abstain):
    """rail/confirmation"""
    ref = inv.booking_ref()
    towns = []
    while len(towns) < 2:
        t = inv.town()
        if t not in towns:
            towns.append(t)
    day = inv.day(BASE, 250)
    dep = rng.randrange(5 * 60, 21 * 60, 5)
    arr = dep + rng.randint(45, 260)
    dep_s, arr_s = f"{dep // 60:02d}:{dep % 60:02d}", f"{arr // 60 % 24:02d}:{arr % 60:02d}"
    plat = f"{rng.randint(1, 16)}{rng.choice(('', '', 'A', 'B'))}"
    coach = rng.choice("ABCDEFGH")
    seat = rng.randint(1, 80)
    fare = inv.money(14, 190)
    train_no = f"{rng.choice(('IC', 'RE', 'EX'))} {rng.randint(100, 999)}"
    c.text(40, 144, "Your train ticket is confirmed", size=30, bold=True)
    c.text(40, 196, "Booking reference", size=14, color="muted")
    c.text(190, 188, ref, size=32, bold=True, color="accent", field="ref")
    c.rect(40, 250, 840, 700, fill="surface", radius=14)
    c.text(68, 272, f"Train {train_no}", size=16, color="muted", field="train", value=train_no)
    c.text(68, 312, f"{towns[0]} to {towns[1]}", size=24, bold=True)
    c.text(68, 360, date_long(day), size=17, field="date", kind="date", value=day.isoformat())
    c.text(68, 420, "Departs", size=13, color="muted")
    c.text(340, 420, "Arrives", size=13, color="muted")
    c.text(68, 446, time_12h(dep_s), size=32, bold=True, field="dep", kind="time", value=dep_s)
    c.text(340, 446, time_12h(arr_s), size=32, field="arr", kind="time", value=arr_s)
    c.line(68, 520, 812, 520)
    c.text(68, 548, f"Platform {plat}", size=20, field="platform", value=plat)
    c.text(340, 548, f"Carriage {coach}", size=20, field="coach", value=coach)
    c.text(600, 548, f"Seat {seat}", size=20, field="seat", kind="number", value=str(seat))
    c.text(68, 620, f"Passenger {inv.person()}", size=14, color="muted")
    c.text(68, 652, f"Fare {money_str(fare)}", size=16, field="fare", kind="money",
           value=f"{fare:.2f}")
    _btn(c, 880, 250, 1240, 302, "Download ticket", fill="accent", color="accent_fg", size=17)
    _btn(c, 880, 318, 1240, 370, "Add to calendar", outline="border", size=17)
    _link(c, 880, 396, "Change journey", size=16)
    _link(c, 880, 430, "Cancel booking", size=16, color="danger")
    qs = [
        Question("q_ref", "lookup", "What is the booking reference?", ref, fields=["ref"]),
        Question("q_dep", "lookup", "At what time does the train depart?", dep_s, canon="time",
                 fields=["dep"]),
        Question("q_seat", "lookup", "Which seat number is reserved?", str(seat), canon="number",
                 fields=["seat"]),
    ]
    _tail(c, rng, abstain, qs,
          Question("q_platform", "lookup", "From which platform does the train leave?", plat,
                   fields=["platform"]),
          "platform", ("q_station", "What is the phone number of the departure station?",
                       "station_phone", inv.phone(), "phone"))
    return qs, {"ref": ref, "train": train_no, "date": day.isoformat(), "dep": dep_s,
                "arr": arr_s, "platform": plat, "coach": coach, "seat": seat,
                "fare": fare}, day.year


# ---------------------------------------------------------------------------- checkout
_ITEMS = ("Kettle", "Backpack", "Desk lamp", "Headset", "Notebook set", "Water bottle",
          "Speaker", "Toaster", "Keyboard", "Camera strap")


def _checkout(rng, inv, c, abstain):
    """cart/checkout"""
    n = rng.randint(2, 4)
    names = []
    while len(names) < n:
        s = f"{inv.brand()} {rng.choice(_ITEMS)}"
        if s not in names:
            names.append(s)
    prices = [round(inv.money(8, 120)) + rng.choice((0.0, 0.5, 0.99)) for _ in names]
    sub = round(sum(prices), 2)
    ship = rng.choice((0.0, 4.5, 6.0, 9.99))
    tax = round(sub * 0.08, 2)
    total = round(sub + ship + tax, 2)
    email = inv.email()
    person = inv.person()
    street = f"{inv.street_address()}, {inv.town()}"
    last4 = f"{rng.randint(1000, 9999)}"
    c.text(40, 138, "Checkout", size=30, bold=True)
    # left column: delivery then payment
    c.text(40, 192, "1  Delivery details", size=18, bold=True)
    _input(c, 40, 246, 400, 290, "Full name", person, field="name")
    _input(c, 420, 246, 780, 290, "Email", email, field="email")
    _input(c, 40, 336, 780, 380, "Address", street, field="address")
    _btn(c, 40, 410, 240, 454, "Continue", fill="accent", color="accent_fg", size=17)
    _link(c, 270, 422, "Back to cart", size=16)
    c.line(40, 490, 780, 490, color="border")
    c.text(40, 512, "2  Payment", size=18, bold=True)
    c.text(40, 556, f"Card ending {last4}", size=16, field="last4", kind="number", value=last4)
    c.element("Card number", 40, 548, 400, 586, role="field")
    _btn(c, 40, 620, 340, 676, "Pay now", fill="accent", color="accent_fg", size=20, radius=10)
    # right column: summary
    c.rect(840, 160, 1240, 700, fill="surface", radius=14)
    c.text(868, 182, "Order summary", size=18, bold=True)
    y = 232
    for i, (nm, pr) in enumerate(zip(names, prices)):
        c.text(868, y, nm, size=15, field=f"item_{i}", value=nm)
        c.text_right(1212, y, money_str(pr), size=15, field=f"price_{i}", kind="money",
                     value=f"{pr:.2f}")
        y += 40
    c.line(868, y + 4, 1212, y + 4, color="border")
    y += 24
    for label, val, fname in (("Subtotal", sub, "subtotal"), ("Shipping", ship, "shipping"),
                              ("Tax", tax, "tax")):
        c.text(868, y, label, size=15, color="muted")
        c.text_right(1212, y, "Free" if (fname == "shipping" and val == 0) else money_str(val),
                     size=15, field=fname, kind="money", value=f"{val:.2f}")
        y += 34
    c.text(868, y + 14, "Total", size=20, bold=True)
    c.text_right(1212, y + 10, money_str(total), size=26, bold=True, field="total", kind="money",
                 value=f"{total:.2f}")
    i1 = rng.randrange(n)
    qs = [
        Question("q_total", "lookup", "What is the order total?", f"{total:.2f}", canon="money",
                 fields=["total"]),
        Question("q_item", "lookup", f"What is the price of the {names[i1]}?", f"{prices[i1]:.2f}",
                 canon="money", fields=[f"item_{i1}", f"price_{i1}"]),
        Question("q_count", "count", "How many different items are in the order summary?", str(n),
                 canon="number", fields=[f"item_{i}" for i in range(n)]),
    ]
    _tail(c, rng, abstain, qs,
          Question("q_email", "lookup", "Which email address is entered in the delivery form?",
                   email, fields=["email"]),
          "email", ("q_promo", "What promo code was applied to the order?", "promo",
                    inv.code(4, 2), "text"))
    return qs, {"items": [{"name": a, "price": b} for a, b in zip(names, prices)],
                "subtotal": sub, "shipping": ship, "tax": tax, "total": total, "email": email,
                "card_last4": last4}, None


# ---------------------------------------------------------------------------- booking form
_SERVICES = ("Dental check-up", "Haircut", "Bike service", "Piano lesson", "Eye test",
             "Table for dinner")


def _booking(rng, inv, c, abstain):
    """book/appointment"""
    service = rng.choice(_SERVICES)
    name = inv.person()
    email = inv.email(name)
    phone = inv.phone()
    day = inv.day(BASE, 200)
    start = rng.randrange(8 * 60, 18 * 60, 15)
    start_s = f"{start // 60:02d}:{start % 60:02d}"
    guests = rng.randint(1, 8)
    c.text(40, 138, "Book an appointment", size=30, bold=True)
    c.text(40, 184, service, size=18, color="accent", field="service", value=service)
    _input(c, 40, 258, 600, 304, "Full name", name, field="name")
    _input(c, 660, 258, 1220, 304, "Email", email, field="email")
    _input(c, 40, 372, 600, 418, "Phone", phone, field="phone", kind="phone")
    _input(c, 660, 372, 1220, 418, "Date", date_long(day), field="date", kind="date",
           fval=day.isoformat())
    _input(c, 40, 486, 600, 532, "Time", time_12h(start_s), field="time", kind="time",
           fval=start_s)
    _input(c, 660, 486, 1220, 532, "Guests", str(guests), field="guests", kind="number")
    c.rect(40, 574, 62, 596, outline="fg", radius=4, width=1.5)
    c.text(74, 575, "Send me a reminder", size=15)
    c.element("Send me a reminder", 36, 570, 300, 600, role="toggle")
    _btn(c, 960, 640, 1220, 696, "Continue", fill="accent", color="accent_fg", size=20, radius=10)
    _btn(c, 40, 640, 220, 696, "Back", outline="border", size=18, radius=10)
    _link(c, 760, 656, "Cancel", size=18, color="danger")
    qs = [
        Question("q_name", "lookup", "Whose name is entered in the booking form?", name,
                 fields=["name"]),
        Question("q_date", "lookup", "Which date is entered in the Date field?", day.isoformat(),
                 canon="date", fields=["date"]),
        Question("q_time", "lookup", "Which time is entered in the Time field?", start_s,
                 canon="time", fields=["time"]),
    ]
    _tail(c, rng, abstain, qs,
          Question("q_guests", "lookup", "How many guests are entered in the form?", str(guests),
                   canon="number", fields=["guests"]),
          "guests", ("q_notes", "What special request is typed in the form?", "request",
                     rng.choice(("Window seat please", "Ground floor room", "Left-handed chair")),
                     "text"))
    return qs, {"service": service, "name": name, "email": email, "phone": phone,
                "date": day.isoformat(), "time": start_s, "guests": guests}, day.year


# ---------------------------------------------------------------------------- pricing
_PLANS = ("Starter", "Basic", "Team", "Business", "Plus", "Pro", "Studio", "Scale")


def _pricing(rng, inv, c, abstain):
    """plans/pricing"""
    n = 3
    plans = rng.sample(_PLANS, n)
    prices = sorted(rng.sample(range(5, 120), n))
    seats = [rng.randint(1, 5) * (k + 1) for k in range(n)]
    pop = rng.randrange(n)
    c.text_center(640, 140, "Choose your plan", size=34, bold=True)
    c.text_center(640, 192, "Prices are per month. Cancel at any time.", size=16, color="muted")
    for k in range(n):
        x0 = 120 + k * 360
        hi = k == pop
        c.rect(x0, 250, x0 + 320, 660, fill="surface", outline="accent" if hi else "border",
               radius=16, width=3 if hi else 1)
        if hi:
            c.text_center(x0 + 160, 266, "Most popular", size=13, color="accent")
        c.text_center(x0 + 160, 296, plans[k], size=24, bold=True, field=f"plan_{k}", value=plans[k])
        c.text_center(x0 + 160, 356, f"${prices[k]} per month", size=22, field=f"price_{k}",
                      kind="money", value=f"{prices[k]:.2f}")
        c.text_center(x0 + 160, 420, f"{seats[k]} users included", size=15, color="muted",
                      field=f"seats_{k}", kind="number", value=str(seats[k]))
        c.text_center(x0 + 160, 456, "Email support", size=15, color="muted")
        _btn(c, x0 + 40, 560, x0 + 280, 612, "Choose plan", fill="accent" if hi else None,
             outline=None if hi else "accent", color="accent_fg" if hi else "accent", size=17)
    _link(c, 120, 700, "Compare all features", size=16)
    _btn(c, 960, 690, 1160, 738, "Contact sales", outline="border", size=16)
    ia, ib = rng.sample(range(n), 2)
    k = rng.randrange(n)
    qs = [
        Question("q_price", "lookup", f"What is the monthly price of the {plans[ia]} plan?",
                 f"{prices[ia]:.2f}", canon="money", fields=[f"plan_{ia}", f"price_{ia}"]),
        Question("q_pop", "lookup", "Which plan is marked Most popular?", plans[pop],
                 fields=[f"plan_{i}" for i in range(n)]),
        Question("q_diff", "difference", f"How much more per month is the {plans[max(ia, ib)]} "
                 f"plan than the {plans[min(ia, ib)]} plan?", f"{prices[max(ia, ib)] - prices[min(ia, ib)]:.2f}",
                 canon="money", fields=[f"plan_{ia}", f"plan_{ib}", f"price_{ia}", f"price_{ib}"]),
    ]
    _tail(c, rng, abstain, qs,
          Question("q_seats", "lookup", f"How many users does the {plans[k]} plan include?",
                   str(seats[k]), canon="number", fields=[f"plan_{k}", f"seats_{k}"]),
          f"seats_{k}", ("q_trial", "How many days does the free trial last?", "trial",
                         f"{rng.choice((7, 14, 30))} days", "text"))
    return qs, {"plans": [{"name": plans[i], "price": prices[i], "seats": seats[i]}
                          for i in range(n)], "most_popular": plans[pop]}, None
