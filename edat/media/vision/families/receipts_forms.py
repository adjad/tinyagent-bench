"""Receipts and forms: 12 receipt layouts, invoices, tax-style forms, application forms and
ID-like cards (spec 05 §7.1).

Planted fields: merchant, date, line items, discount, tax, tip, total, payment method, invoice
number, form boxes 1-12, filled form fields, checkbox states and card fields.
Question types: lookup, sum check, multi-field extraction ("multi", ANLS over a comma list),
empty-field detection (one blank field per such form; asked as "lookup", answer = its label)
and checkbox state ("state"); plus the full reading.

The 12 receipt layouts are split over three receipt templates (shop, cafe, wide), four each.
Every form and card is marked SAMPLE and uses invented names, numbers and form titles.

Seven templates on purpose: a count coprime with 10 makes every template meet every theme,
scale and abstention slot.
"""
from __future__ import annotations

import random
from datetime import date, timedelta

from edat.media.common import Inventor, PRODUCTS, MONTHS, canon_text, date_short, money_str, \
    time_12h
from edat.media.vision.canvas import Canvas, Field
from edat.media.vision.items import ABSTAIN_ANSWER, Question, Rendered

FAMILY = "receipts_forms"
TEMPLATES = ("receipt_shop", "invoice", "receipt_cafe", "tax_form", "receipt_wide",
             "application", "id_card")
LOGICAL_SIZES = {"receipt_shop": (360, 680), "receipt_cafe": (360, 680),
                 "receipt_wide": (560, 640), "invoice": (612, 792), "tax_form": (720, 900),
                 "application": (640, 860), "id_card": (680, 440)}
BASE_DATE = date(2027, 1, 4)

_CAFE_ITEMS = ("Flat white", "Oat latte", "Bagel", "Soup of the day", "Club sandwich",
               "Green salad", "Lemon tart", "Iced tea", "Pancakes", "Veggie wrap", "Fries",
               "Hot chocolate", "Fruit bowl", "Chai latte", "Toastie", "Muffin")
_PAY_METHODS = ("Debit card", "Credit card", "Cash", "Mobile wallet", "Gift card",
                "Contactless card")


def render(rng: random.Random, *, template: str, theme: str, scale: float,
           abstain: bool) -> Rendered:
    if template not in TEMPLATES:
        raise ValueError(f"unknown template {template!r}")
    if template.startswith("receipt_"):
        return _receipt(rng, template, theme=theme, scale=scale, abstain=abstain)
    return globals()["_" + template](rng, theme=theme, scale=scale, abstain=abstain)


def _reading(what: str) -> Question:
    return Question("q_reading", "reading", f"Read the {what}.", "", kind="reading")


def _absent_value(c: Canvas, make) -> str:
    drawn = [canon_text(r.text) for r in c.runs]
    v = make()
    for _ in range(50):
        if not any(canon_text(v) in t for t in drawn):
            break
        v = make()
    return v


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


def _abstain(c: Canvas, rng: random.Random, qs: list[Question], blur_q: Question | None,
             absent: tuple) -> dict:
    """Replace one scored question with an abstention item; return a note for ``data``."""
    keep = [f for q in qs if q is not blur_q for f in q.fields]
    if blur_q is not None and blur_q in qs and rng.random() < 0.5 and \
            blur_q.fields[-1] not in keep and _blur_safe(c, blur_q.fields[-1], keep):
        name = blur_q.fields[-1]
        qs.remove(blur_q)
        c.blur_field(name, radius=7)
        qs.append(Question(blur_q.qid + "_abstain", "absence", blur_q.question, ABSTAIN_ANSWER,
                           kind="abstain", fields=[name]))
        return {"blurred": name}
    qs.pop(rng.randrange(len(qs)))
    qid, text, name, make, kind = absent
    c.absent(name, _absent_value(c, make), kind)
    qs.append(Question(qid, "absence", text, ABSTAIN_ANSWER, kind="abstain", fields=[name]))
    return {"absent": name}


# ============================================================================ receipts
# 12 layouts, 4 per receipt template. Each is a set of style switches.
_LAYOUTS = {
    "receipt_shop": (
        dict(align="center", upper=True, sep="dash", qty="below", tax="Sales tax", disc=False),
        dict(align="left", upper=False, sep="line", qty="prefix", tax="Tax", disc=True),
        dict(align="center", upper=False, sep="eq", qty="below", tax="TAX", disc=True),
        dict(align="left", upper=True, sep="star", qty="prefix", tax="State tax", disc=False)),
    "receipt_cafe": (
        dict(align="center", upper=False, sep="line", qty="prefix", tax="Tax", disc=False),
        dict(align="center", upper=True, sep="dash", qty="prefix", tax="Sales tax", disc=True),
        dict(align="left", upper=False, sep="eq", qty="below", tax="Tax", disc=False),
        dict(align="left", upper=True, sep="line", qty="prefix", tax="TAX", disc=True)),
    "receipt_wide": (
        dict(align="left", upper=False, sep="line", cols=("qty", "item", "unit", "amount"),
             tax="Sales tax", disc=False),
        dict(align="center", upper=True, sep="dash", cols=("item", "qty", "unit", "amount"),
             tax="Tax", disc=True),
        dict(align="left", upper=True, sep="eq", cols=("item", "unit", "qty", "amount"),
             tax="TAX", disc=False),
        dict(align="center", upper=False, sep="line", cols=("qty", "item", "amount"),
             tax="Sales tax", disc=True)),
}


def _receipt_lines(rng: random.Random, inv: Inventor, n: int, cafe: bool) -> list[dict]:
    names = rng.sample(_CAFE_ITEMS if cafe else PRODUCTS, n)
    out = []
    for name in names:
        qty = rng.choice((1, 1, 1, 2, 3))
        unit = inv.money(1.5, 14.0) if cafe else inv.money(0.8, 24.0)
        out.append({"name": name, "qty": qty, "unit": unit, "amount": round(qty * unit, 2)})
    return out


def _receipt(rng: random.Random, template: str, *, theme: str, scale: float,
             abstain: bool) -> Rendered:
    inv = Inventor(rng)
    li = rng.randrange(4)
    L = _LAYOUTS[template][li]
    layout_id = TEMPLATES_RECEIPT_OFFSET[template] + li
    cafe = template == "receipt_cafe"
    wide = template == "receipt_wide"
    merchant = inv.merchant() if not cafe else f"{inv.brand()} {rng.choice(('Cafe', 'Kitchen', 'Diner', 'Bistro'))}"
    address = f"{inv.street_address()}, {inv.town()}"
    phone = inv.phone()
    day = inv.day(BASE_DATE, 600)
    clock = inv.clock(7, 21, 1)
    number = inv.code(1, 6) if rng.random() < 0.5 else str(rng.randint(100000, 999999))
    lines = _receipt_lines(rng, inv, rng.randint(3, 7), cafe)
    subtotal = round(sum(l["amount"] for l in lines), 2)
    discount = round(subtotal * rng.choice((0.05, 0.1, 0.15)), 2) if L["disc"] else 0.0
    rate = rng.choice((0.05, 0.0625, 0.07, 0.08, 0.0875))
    tax = round((subtotal - discount) * rate, 2)
    tip = round((subtotal - discount) * rng.choice((0.1, 0.15, 0.18, 0.2)), 2) if cafe else 0.0
    total = round(subtotal - discount + tax + tip, 2)
    pay = rng.choice(_PAY_METHODS)
    server = inv.given() if cafe else None
    table = rng.randint(1, 30) if cafe else None

    w, h = LOGICAL_SIZES[template]
    c = Canvas(w, h, scale=scale, theme=theme)
    paper_inset = 0
    if rng.random() < 0.5:            # a paper slip on a surface
        c.rect(0, 0, w, h, fill="surface")
        c.rect(10, 8, w - 10, h - 8, fill="bg", outline="border")
        paper_inset = 10
    x0, x1 = 26 + paper_inset, w - 26 - paper_inset
    y = 28
    up = (lambda s: s.upper()) if L["upper"] else (lambda s: s)

    def head(s: str, size: float, **kw) -> None:
        nonlocal y
        if L["align"] == "center":
            c.text_center(w / 2, y, s, size=size, **kw)
        else:
            c.text(x0, y, s, size=size, **kw)
        y += size + 8

    head(up(merchant), 19, bold=True, field="merchant", value=merchant)
    head(address, 11, color="muted")
    head(phone, 11, color="muted")
    y += 4
    rlabel = rng.choice(("Receipt", "Receipt #", "Trans", "Order"))
    c.text(x0, y, f"{rlabel} {number}", size=12, field="receipt_number", value=number)
    if cafe:
        c.text_right(x1, y, f"Table {table}", size=12, field="table", kind="number",
                     value=str(table))
    y += 20
    date_text = rng.choice((date_short(day), day.strftime("%m/%d/%Y"),
                            f"{MONTHS[day.month - 1]} {day.day}, {day.year}"))
    c.text(x0, y, date_text, size=12, field="date", kind="date", value=day.isoformat())
    c.text_right(x1, y, time_12h(clock) if rng.random() < 0.6 else clock, size=12,
                 field="time", kind="time", value=clock)
    y += 20
    if cafe:
        c.text(x0, y, f"Server: {server}", size=12, field="server", value=server)
        y += 20

    def sep() -> None:
        nonlocal y
        if L["sep"] == "line":
            c.line(x0, y + 2, x1, y + 2)
        else:
            ch = {"dash": "-", "eq": "=", "star": "*"}[L["sep"]]
            n = int((x1 - x0) / max(1.0, c.text_width(ch * 10, 11) / 10))
            c.text(x0, y - 5, ch * max(4, n - 1), size=11, color="muted", reading=False)
        y += 12

    sep()
    if wide:
        cols = L["cols"]
        pos = _wide_cols(cols, x0, x1)
        for name, (cx, align) in zip(cols, pos):
            label = {"qty": "Qty", "item": "Item", "unit": "Price", "amount": "Amount"}[name]
            (c.text_right if align == "r" else c.text)(cx, y, up(label), size=11, bold=True)
        y += 18
        for i, l in enumerate(lines):
            for name, (cx, align) in zip(cols, pos):
                put = c.text_right if align == "r" else c.text
                if name == "item":
                    put(cx, y, l["name"], size=12, field=f"item_{i}_name", value=l["name"])
                elif name == "qty":
                    put(cx, y, str(l["qty"]), size=12, field=f"item_{i}_qty", kind="number",
                        value=str(l["qty"]))
                elif name == "unit":
                    put(cx, y, money_str(l["unit"]), size=12)
                else:
                    put(cx, y, money_str(l["amount"]), size=12, field=f"item_{i}_amount",
                        kind="money", value=f"{l['amount']:.2f}")
            y += 20
    else:
        for i, l in enumerate(lines):
            label = f"{l['qty']} x {l['name']}" if (L["qty"] == "prefix" and l["qty"] > 1) or \
                (L["qty"] == "prefix" and cafe) else l["name"]
            c.text(x0, y, up(label) if L["upper"] and not cafe else label, size=12,
                   field=f"item_{i}_name", value=l["name"])
            c.text_right(x1, y, money_str(l["amount"]), size=12, field=f"item_{i}_amount",
                         kind="money", value=f"{l['amount']:.2f}")
            y += 18
            if l["qty"] > 1 and L["qty"] == "below":
                c.text(x0 + 12, y - 1, f"{l['qty']} @ {money_str(l['unit'])}", size=10,
                       color="muted")
                y += 15
    sep()
    xl = x0 if not wide else (x0 + x1) / 2
    rows = [("Subtotal", subtotal, "subtotal")]
    if discount:
        rows.append((rng.choice(("Member discount", "Discount", "Coupon")), -discount,
                     "discount"))
    rows.append((f"{L['tax']} {rate * 100:g}%", tax, "tax"))
    if cafe:
        rows.append(("Tip", tip, "tip"))
    for label, amount, name in rows:
        c.text(xl, y, up(label), size=12)
        shown = money_str(abs(amount)) if amount >= 0 else f"-{money_str(-amount)}"
        c.text_right(x1, y, shown, size=12, field=name, kind="money", value=f"{abs(amount):.2f}")
        y += 18
    y += 4
    c.text(xl, y, "TOTAL" if L["upper"] else "Total", size=15, bold=True)
    c.text_right(x1, y, money_str(total), size=15, bold=True, field="total", kind="money",
                 value=f"{total:.2f}")
    y += 30
    c.text(xl, y, up("Paid by") + ":", size=12)
    c.text_right(x1, y, pay, size=12, field="payment", value=pay)
    y += 18
    if pay == "Cash":
        tendered = float(int(total / 10) * 10 + 10)
        c.text(xl, y, up("Change"), size=11, color="muted")
        c.text_right(x1, y, money_str(round(tendered - total, 2)), size=11, color="muted")
        y += 18
    y += 14
    thanks = rng.choice(("Thank you for shopping with us", "Thanks, come again",
                         "Keep this receipt for returns", "Have a great day"))
    if cafe:
        thanks = rng.choice(("Thanks for dining with us", "See you soon", "Enjoy your day"))
    c.text_center(w / 2, y, up(thanks) if L["upper"] else thanks, size=11, color="muted")

    n = len(lines)
    items = [f"item_{i}_name" for i in range(n)]
    amounts = [f"item_{i}_amount" for i in range(n)]
    q_merchant = Question("q_merchant", "lookup", "What is the name of the business on this receipt?",
                          merchant, fields=["merchant"])
    q_date = Question("q_date", "lookup", "On what date was this purchase made?",
                      day.isoformat(), canon="date", fields=["date"])
    q_total = Question("q_total", "lookup", "What is the total amount on the receipt?",
                       f"{total:.2f}", canon="money", fields=["total"])
    q_pay = Question("q_payment", "lookup", "How was this receipt paid?", pay,
                     fields=["payment"])
    q_count = Question("q_count", "count", "How many different items are listed on the receipt?",
                       str(n), canon="number", fields=items)
    q_items_sum = Question("q_items_sum", "sum", "What do the line item amounts add up to?",
                           f"{subtotal:.2f}", canon="money", fields=amounts)
    q_sub_tax = Question("q_sum", "sum", "What is the subtotal plus the tax?",
                         f"{round(subtotal + tax, 2):.2f}", canon="money",
                         fields=["subtotal", "tax"])
    q_multi = Question("q_multi", "multi",
                       "Give the business name and the receipt number, separated by a comma.",
                       f"{merchant}, {number}", kind="anls", fields=["merchant", "receipt_number"],
                       accept=[f"{merchant}; {number}", f"{merchant} {number}"])
    i_big = max(range(n), key=lambda i: lines[i]["amount"])
    q_item = Question("q_item", "lookup", f"How much was charged for {lines[i_big]['name']}?",
                      f"{lines[i_big]['amount']:.2f}", canon="money",
                      fields=[f"item_{i_big}_name", f"item_{i_big}_amount"])
    pool = [q_date, q_pay, q_item]
    if cafe:
        pool.append(Question("q_tip", "lookup", "How much was the tip?", f"{tip:.2f}",
                             canon="money", fields=["tip"]))
        pool.append(Question("q_server", "lookup", "What is the name of the server?", server,
                             fields=["server"]))
    if discount:
        pool.append(Question("q_discount", "lookup", "How much was the discount?",
                             f"{discount:.2f}", canon="money", fields=["discount"]))
    qs = [rng.choice((q_sub_tax, q_items_sum)), rng.choice((q_count, q_multi)),
          rng.choice((q_merchant, q_total))] + rng.sample(pool, 1)
    rng.shuffle(qs)
    data = {"template": template, "layout": layout_id, "style": L if not wide else
            {k: (list(v) if isinstance(v, tuple) else v) for k, v in L.items()},
            "merchant": merchant, "address": address, "phone": phone, "date": day.isoformat(),
            "time": clock, "receipt_number": number, "lines": lines, "subtotal": subtotal,
            "discount": discount, "tax_rate": rate, "tax": tax, "tip": tip, "total": total,
            "payment": pay, "server": server, "table": table}
    if abstain:
        blur_q = q_total if q_total in qs else None
        data["abstain"] = _abstain(c, rng, qs, blur_q,
                                   ("q_cashier", "What is the name of the cashier on this receipt?",
                                    "cashier", inv.person, "text") if not cafe else
                                   ("q_card", "What are the last four digits of the card used?",
                                    "card_last4", lambda: str(rng.randint(1000, 9999)), "text"))
    qs.append(_reading("receipt"))
    return Rendered(c, template, qs, data, default_year=day.year)


TEMPLATES_RECEIPT_OFFSET = {"receipt_shop": 0, "receipt_cafe": 4, "receipt_wide": 8}


def _wide_cols(cols: tuple, x0: float, x1: float) -> list[tuple[float, str]]:
    width = x1 - x0
    out = []
    x = x0
    share = {"qty": 0.12, "item": 0.46, "unit": 0.2, "amount": 0.22}
    tot = sum(share[c] for c in cols)
    for name in cols:
        wcol = width * share[name] / tot
        if name in ("unit", "amount"):
            out.append((x + wcol, "r"))
        elif name == "qty":
            out.append((x + 4, "l"))
        else:
            out.append((x + 6, "l"))
        x += wcol
    return out


# ============================================================================ invoice
def _invoice(rng: random.Random, *, theme: str, scale: float, abstain: bool) -> Rendered:
    inv = Inventor(rng)
    seller = inv.merchant()
    buyer = inv.person()
    number = f"INV-{rng.randint(1000, 9999)}"
    issued = inv.day(BASE_DATE, 560)
    due_days = rng.choice((14, 30))
    due = issued + timedelta(days=due_days)
    lines = _receipt_lines(rng, inv, rng.randint(2, 6), False)
    for l in lines:                    # invoices bill bigger quantities
        l["qty"] = rng.choice((1, 2, 4, 5, 10, 12))
        l["amount"] = round(l["qty"] * l["unit"], 2)
    subtotal = round(sum(l["amount"] for l in lines), 2)
    rate = rng.choice((0.05, 0.07, 0.08))
    tax = round(subtotal * rate, 2)
    total = round(subtotal + tax, 2)
    flip = rng.random() < 0.5            # seller block left or right

    w, h = LOGICAL_SIZES["invoice"]
    c = Canvas(w, h, scale=scale, theme=theme)
    if rng.random() < 0.5:
        c.rect(0, 0, w, 10, fill="accent")
    lx, rx = 48, w - 48
    if not flip:
        c.text(lx, 48, seller, size=22, bold=True, field="seller", value=seller)
        c.text(lx, 80, f"{inv.street_address()}, {inv.town()}", size=11, color="muted")
        c.text(lx, 96, inv.email("billing"), size=11, color="muted")
        c.text_right(rx, 48, "INVOICE", size=22, bold=True, color="accent")
        c.text_right(rx, 82, number, size=13, field="invoice_number", value=number)
    else:
        c.text(lx, 48, "INVOICE", size=22, bold=True, color="accent")
        c.text(lx, 82, number, size=13, field="invoice_number", value=number)
        c.text_right(rx, 48, seller, size=22, bold=True, field="seller", value=seller)
        c.text_right(rx, 80, f"{inv.street_address()}, {inv.town()}", size=11, color="muted")
        c.text_right(rx, 96, inv.email("billing"), size=11, color="muted")
    c.text(lx, 132, "Bill to", size=11, color="muted")
    c.text(lx, 150, buyer, size=13, field="buyer", value=buyer)
    c.text(lx, 168, f"{inv.street_address()}, {inv.town()}", size=11)
    c.text(360, 132, "Issued", size=11, color="muted")
    c.text(360, 150, date_short(issued), size=13, field="issued", kind="date",
           value=issued.isoformat())
    c.text(470, 132, "Due", size=11, color="muted")
    c.text(470, 150, date_short(due), size=13, field="due", kind="date", value=due.isoformat())
    y = 206
    c.rect(48, y, w - 48, y + 26, fill="surface")
    for x, label in ((56, "Description"), (330, "Qty"), (400, "Unit"), (490, "Amount")):
        c.text(x, y + 7, label, size=11, bold=True)
    y += 34
    for i, l in enumerate(lines):
        c.text(56, y, l["name"], size=12, field=f"item_{i}_name", value=l["name"])
        c.text(330, y, str(l["qty"]), size=12, field=f"item_{i}_qty", kind="number",
               value=str(l["qty"]))
        c.text(400, y, money_str(l["unit"]), size=12)
        c.text(490, y, money_str(l["amount"]), size=12, field=f"item_{i}_amount", kind="money",
               value=f"{l['amount']:.2f}")
        y += 22
    c.line(48, y + 4, w - 48, y + 4)
    y += 16
    for label, amount, name, bold in (("Subtotal", subtotal, "subtotal", False),
                                      (f"Tax {rate * 100:g}%", tax, "tax", False),
                                      ("Total due", total, "total", True)):
        c.text(400, y, label, size=13 if bold else 12, bold=bold)
        c.text(490, y, money_str(amount), size=13 if bold else 12, bold=bold, field=name,
               kind="money", value=f"{amount:.2f}")
        y += 22
    c.text(48, h - 90, f"Payment terms: net {due_days} days", size=11, color="muted")
    c.text(48, h - 72, "Please quote the invoice number with your payment.", size=11,
           color="muted")

    big = max(lines, key=lambda l: l["amount"])
    q_number = Question("q_number", "lookup", "What is the invoice number?", number,
                        fields=["invoice_number"])
    q_total = Question("q_total", "lookup", "What is the total due?", f"{total:.2f}",
                       canon="money", fields=["total"])
    pool = [Question("q_due", "lookup", "When is payment due?", due.isoformat(), canon="date",
                     fields=["due"]),
            Question("q_buyer", "lookup", "Who is the invoice billed to?", buyer,
                     fields=["buyer"]),
            Question("q_seller", "lookup", "Which business issued this invoice?", seller,
                     fields=["seller"]),
            Question("q_item_qty", "lookup", f"What quantity of {big['name']} is billed?",
                     str(big["qty"]), canon="number",
                     fields=[f"item_{lines.index(big)}_name", f"item_{lines.index(big)}_qty"])]
    q_sum = rng.choice((
        Question("q_sum", "sum", "What is the subtotal plus the tax?", f"{total:.2f}",
                 canon="money", fields=["subtotal", "tax"]),
        Question("q_items_sum", "sum", "What do the line item amounts add up to?",
                 f"{subtotal:.2f}", canon="money",
                 fields=[f"item_{i}_amount" for i in range(len(lines))])))
    q_multi = Question("q_multi", "multi",
                       "Give the invoice number and the name it is billed to, separated by a comma.",
                       f"{number}, {buyer}", kind="anls", fields=["invoice_number", "buyer"],
                       accept=[f"{number}; {buyer}"])
    qs = [q_sum, q_multi, rng.choice((q_number, q_total))] + rng.sample(
        [q for q in pool if not (q.qid == "q_buyer")], 1)
    rng.shuffle(qs)
    data = {"template": "invoice", "seller": seller, "buyer": buyer, "number": number,
            "issued": issued.isoformat(), "due": due.isoformat(), "lines": lines,
            "subtotal": subtotal, "tax_rate": rate, "tax": tax, "total": total, "flip": flip}
    if abstain:
        data["abstain"] = _abstain(c, rng, qs, q_total if q_total in qs else None,
                                   ("q_po", "What is the purchase order number?", "po_number",
                                    lambda: f"PO-{inv.code(2, 5)}", "text"))
    qs.append(_reading("invoice"))
    return Rendered(c, "invoice", qs, data, default_year=issued.year)


# ============================================================================ tax-style form
_BOX_LABELS = ("Wages and salary", "Interest received", "Shared savings payouts",
               "Other income", "Total income (add boxes 1 to 4)", "Household allowance",
               "Taxable amount (box 5 minus box 6)", "Tax on the taxable amount",
               "Amount already withheld", "Advance payments made",
               "Total paid (add boxes 9 and 10)", "Balance due (box 8 minus box 11)")


def _tax_form(rng: random.Random, *, theme: str, scale: float, abstain: bool) -> Rendered:
    inv = Inventor(rng)
    name = inv.person()
    year = rng.choice((2027, 2028))
    form_code = rng.choice(("EX-27", "EX-31", "HX-12", "EX-40"))
    sample_id = f"SMP-{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}"
    blank_box = rng.choice((3, 4, 10))       # exactly one box left blank (read as zero)
    b = [0.0] * 13
    b[1] = float(rng.randrange(18000, 92000, 50))
    b[2] = round(rng.uniform(10, 900), 2)
    b[3] = round(rng.uniform(5, 1500), 2) if blank_box != 3 else 0.0
    b[4] = round(rng.uniform(5, 4000), 2) if blank_box != 4 else 0.0
    b[5] = round(b[1] + b[2] + b[3] + b[4], 2)
    b[6] = float(rng.choice((8000, 9500, 12000, 14500)))
    b[7] = round(max(0.0, b[5] - b[6]), 2)
    b[8] = round(b[7] * rng.choice((0.1, 0.12, 0.15)), 2)
    b[9] = round(b[8] * rng.uniform(0.6, 0.95), 2)
    b[10] = round(b[8] * rng.uniform(0.02, 0.2), 2) if blank_box != 10 else 0.0
    b[11] = round(b[9] + b[10], 2)
    b[12] = round(b[8] - b[11], 2)

    w, h = LOGICAL_SIZES["tax_form"]
    c = Canvas(w, h, scale=scale, theme=theme)
    c.rect(24, 24, w - 24, h - 24, outline="fg", width=2)
    c.text(44, 40, f"Form {form_code}", size=24, bold=True, field="form_code",
           value=f"Form {form_code}")
    c.text(44, 74, "Household Return (SAMPLE)", size=16, bold=True)
    c.text_right(w - 44, 44, f"Tax year {year}", size=16, bold=True, field="year",
                 kind="number", value=str(year))
    c.text_right(w - 44, 72, "SAMPLE - NOT AN OFFICIAL FORM", size=11, color="danger")
    c.line(24, 104, w - 24, 104, color="fg", width=2)
    c.text(44, 116, "Name", size=11, color="muted")
    c.text(44, 134, name, size=14, field="name")
    c.text(380, 116, "Sample ID number", size=11, color="muted")
    c.text(380, 134, sample_id, size=14, field="sample_id")
    c.text(44, 160, "Address", size=11, color="muted")
    c.text(44, 176, f"{inv.street_address()}, {inv.town()}", size=12)
    c.line(24, 204, w - 24, 204)
    two_col = rng.random() < 0.5
    y0 = 222
    for k in range(1, 13):
        if two_col:
            col, row = (k - 1) // 6, (k - 1) % 6
            bx0 = 44 + col * 330
            by = y0 + row * 100
            bx1 = bx0 + 300
            c.rect(bx0, by, bx1, by + 84, outline="border")
            c.rect(bx0, by, bx0 + 30, by + 26, fill="surface", outline="border")
            c.text_center(bx0 + 15, by + 6, str(k), size=12, bold=True)
            c.text(bx0 + 40, by + 7, _BOX_LABELS[k - 1], size=11, field=f"label_{k}")
            c.rect(bx0 + 120, by + 40, bx1 - 10, by + 74, fill="surface")
            if k != blank_box:
                c.text_right(bx1 - 18, by + 48, f"{b[k]:,.2f}", size=14, field=f"box_{k}",
                             kind="money", value=f"{b[k]:.2f}")
        else:
            by = y0 + (k - 1) * 52
            c.line(44, by + 46, w - 44, by + 46, color="grid")
            c.rect(44, by + 8, 74, by + 34, fill="surface", outline="border")
            c.text_center(59, by + 13, str(k), size=12, bold=True)
            c.text(86, by + 14, _BOX_LABELS[k - 1], size=12, field=f"label_{k}")
            c.rect(w - 230, by + 6, w - 44, by + 38, outline="border")
            if k != blank_box:
                c.text_right(w - 54, by + 14, f"{b[k]:,.2f}", size=14, field=f"box_{k}",
                             kind="money", value=f"{b[k]:.2f}")
    c.text(44, h - 56, "Signature", size=11, color="muted")
    c.line(110, h - 44, 340, h - 44, color="fg")
    c.text(380, h - 56, "Date", size=11, color="muted")
    sign = date(year + 1 if year == 2027 else 2028, rng.randint(1, 4), rng.randint(1, 28))
    c.text(420, h - 58, date_short(sign), size=12, field="signed", kind="date",
           value=sign.isoformat())

    filled = [k for k in range(1, 13) if k != blank_box]
    k1 = rng.choice([k for k in filled if k not in (5, 11, 12)])
    q_lookup = Question("q_box", "lookup", f"What amount is entered in box {k1}?",
                        f"{b[k1]:.2f}", canon="money", fields=[f"label_{k1}", f"box_{k1}"])
    sk = [k for k in (1, 2, 3, 4) if k != blank_box]
    q_sum = rng.choice((
        Question("q_sum", "sum", "What is the sum of the amounts in boxes 1 to 4?",
                 f"{round(sum(b[k] for k in sk), 2):.2f}", canon="money",
                 fields=[f"box_{k}" for k in sk]),
        Question("q_sum", "sum", "What is the sum of the amounts in boxes 9 and 10?",
                 f"{b[11]:.2f}", canon="money",
                 fields=[f"box_{k}" for k in (9, 10) if k != blank_box])))
    q_multi = Question("q_multi", "multi",
                       "Give the name on the form and the tax year, separated by a comma.",
                       f"{name}, {year}", kind="anls", fields=["name", "year"],
                       accept=[f"{name}; {year}"])
    lbl = _BOX_LABELS[blank_box - 1]
    q_blank = Question("q_blank", "lookup",
                       "Which numbered box on the form was left blank? Give the box number.",
                       str(blank_box), canon="number",
                       fields=[f"label_{blank_box}"] + [f"box_{k}" for k in filled],
                       accept=[f"box {blank_box}"])
    pool = [Question("q_id", "lookup", "What is the sample ID number on the form?", sample_id,
                     fields=["sample_id"]),
            Question("q_balance", "lookup", "What balance is shown in box 12?", f"{b[12]:.2f}",
                     canon="money", fields=["label_12", "box_12"]),
            Question("q_signed", "lookup", "On what date was the form signed?",
                     sign.isoformat(), canon="date", fields=["signed"])]
    qs = [q_lookup, q_sum, q_blank, rng.choice([q_multi] + pool)]
    rng.shuffle(qs)
    data = {"template": "tax_form", "form": f"Form {form_code}", "name": name, "year": year,
            "sample_id": sample_id, "boxes": {str(k): b[k] for k in range(1, 13)},
            "blank_box": blank_box, "blank_label": lbl, "two_columns": two_col,
            "signed": sign.isoformat()}
    if abstain:
        data["abstain"] = _abstain(c, rng, qs, None,
                                   ("q_preparer", "What is the name of the paid preparer?",
                                    "preparer", inv.person, "text"))
    qs.append(_reading("form"))
    return Rendered(c, "tax_form", qs, data, default_year=year)


# ============================================================================ application form
_APP_KINDS = ("Library Card Application", "Community Pool Membership Application",
              "Parking Permit Application", "Volunteer Registration Form",
              "Allotment Plot Application", "Shuttle Pass Application")
_CHECKS = ("Paperless statements", "Email newsletter", "Text reminders", "Accessible parking",
           "Large print letters", "Share my name with partners", "Weekend sessions",
           "Join the waiting list")


def _checkbox(c: Canvas, x: float, y: float, size: float, checked: bool, name: str,
              label: str, field_name: str) -> None:
    """A checkbox with an X when checked, recorded as a state field and a UI element."""
    c.rect(x, y, x + size, y + size, fill="bg", outline="fg", width=1.5)
    if checked:
        m = size * 0.22
        c.line(x + m, y + m, x + size - m, y + size - m, color="fg", width=2)
        c.line(x + size - m, y + m, x + m, y + size - m, color="fg", width=2)
    box = [max(0, c.px(x)), max(0, c.px(y)), min(c.size[0], c.px(x + size)),
           min(c.size[1], c.px(y + size))]
    c.fields[field_name] = Field(name=field_name, kind="state", value="on" if checked else "off",
                                 text="", bbox=box, size_px=box[3] - box[1], visible=True,
                                 crop_sha256=c.crop_sha256(box), fg=list(c.color("fg")))
    c.element(label, x, y, x + size, y + size, role="checkbox")


def _application(rng: random.Random, *, theme: str, scale: float, abstain: bool) -> Rendered:
    inv = Inventor(rng)
    title = rng.choice(_APP_KINDS)
    org = f"{inv.town()} {rng.choice(('Council', 'Library', 'Leisure Trust', 'Transit'))}"
    person = inv.person()
    start = inv.day(BASE_DATE, 560)
    fields_spec = [
        ("Full name", person, "text"),
        ("Email", inv.email(person), "text"),
        ("Phone", inv.phone(), "phone"),
        ("Street address", inv.street_address(), "text"),
        ("Town", inv.town(), "text"),
        ("Postal code", f"{rng.randint(10000, 99999)}", "text"),
        ("Preferred start date", start, "date"),
        ("Emergency contact", inv.person(), "text"),
    ]
    blank_candidates = [i for i in range(1, len(fields_spec)) if fields_spec[i][0] != "Town"]
    blank = rng.choice(blank_candidates)
    checks = rng.sample(_CHECKS, 4)
    if "Paperless statements" not in checks and rng.random() < 0.6:
        checks[rng.randrange(4)] = "Paperless statements"
    states = [rng.random() < 0.5 for _ in checks]
    states[rng.randrange(4)] = True
    tiers = rng.choice((("Single", "Family", "Senior"), ("Standard", "Student", "Concession"),
                        ("Weekday", "Weekend", "Any day")))
    tier = rng.randrange(3)

    w, h = LOGICAL_SIZES["application"]
    c = Canvas(w, h, scale=scale, theme=theme)
    c.rect(0, 0, w, 70, fill="accent")
    c.text(32, 16, title, size=20, bold=True, color="accent_fg", field="title")
    c.text(32, 44, org, size=12, color="accent_fg")
    c.text_right(w - 32, 44, "SAMPLE FORM", size=12, bold=True, color="accent_fg")
    c.text(32, 86, "Please complete all fields in block capitals or type.", size=11,
           color="muted")
    layout_two = rng.random() < 0.5
    y = 112
    colw = (w - 64 - 24) / 2
    values: dict[str, str] = {}
    for i, (label, val, kind) in enumerate(fields_spec):
        if layout_two:
            col = i % 2
            x = 32 + col * (colw + 24)
            fw = colw
            yy = y + (i // 2) * 62
        else:
            x, fw, yy = 32, w - 64, y + i * 50
        c.text(x, yy, label, size=11, bold=True, field=f"label_{i}", value=label)
        c.rect(x, yy + 18, x + fw, yy + 44, fill="surface", outline="border", radius=4)
        c.element(label, x, yy + 18, x + fw, yy + 44, role="field")
        if i == blank:
            continue
        shown = val if kind != "date" else rng.choice(
            (val.strftime("%m/%d/%Y"), date_short(val)))
        value = val.isoformat() if kind == "date" else val
        c.text(x + 8, yy + 24, shown, size=13, field=f"value_{i}", kind=kind, value=value)
        values[label] = value
    y = y + (4 * 62 if layout_two else 8 * 50) + 10
    c.text(32, y, "Membership type", size=12, bold=True)
    y += 24
    x = 32
    for k, t in enumerate(tiers):
        _checkbox(c, x, y, 16, k == tier, t, t, f"tier_{k}")
        c.text(x + 24, y + 1, t, size=12, field=f"tier_label_{k}", value=t)
        x += 24 + c.text_width(t, 12) + 32
    y += 40
    c.text(32, y, "Preferences", size=12, bold=True)
    y += 24
    for k, label in enumerate(checks):
        cx = 32 + (k % 2) * ((w - 64) / 2)
        cy = y + (k // 2) * 30
        _checkbox(c, cx, cy, 16, states[k], label, label, f"check_{k}")
        c.text(cx + 24, cy + 1, label, size=12, field=f"check_label_{k}", value=label)
    y += 2 * 30 + 16
    c.text(32, y, "Signature", size=11, color="muted")
    c.line(100, y + 14, 300, y + 14, color="fg")
    by = h - 70
    c.rect(w - 180, by, w - 32, by + 40, fill="accent", radius=8)
    c.text_center(w - 106, by + 11, "Submit", size=15, bold=True, color="accent_fg")
    c.element("Submit", w - 180, by, w - 32, by + 40, role="button")
    c.rect(w - 320, by, w - 196, by + 40, fill="surface", outline="border", radius=8)
    c.text_center(w - 258, by + 11, "Clear form", size=14)
    c.element("Clear form", w - 320, by, w - 196, by + 40, role="button")

    k_ask = checks.index("Paperless statements") if "Paperless statements" in checks and \
        rng.random() < 0.7 else rng.randrange(4)
    word = {True: "on", False: "off"}
    q_check = Question("q_check", "state",
                       f"Is the '{checks[k_ask]}' box checked? Answer yes or no.",
                       word[states[k_ask]], canon="state",
                       fields=[f"check_label_{k_ask}", f"check_{k_ask}"])
    blank_label = fields_spec[blank][0]
    filled = [i for i in range(len(fields_spec)) if i != blank]
    q_blank = Question("q_blank", "lookup", "Which field on the form was left blank?",
                       blank_label, fields=[f"label_{blank}"] + [f"value_{i}" for i in filled])
    q_tier = Question("q_tier", "lookup", "Which membership type is selected?", tiers[tier],
                      fields=[f"tier_label_{k}" for k in range(3)] + [f"tier_{k}" for k in range(3)])
    look = [i for i in filled if i != 0]
    li = rng.choice(look)
    lab, val, kind = fields_spec[li]
    q_lookup = Question(f"q_value_{li}", "lookup", f"What is written in the '{lab}' field?",
                        val.isoformat() if kind == "date" else val,
                        canon=kind if kind in ("date", "phone") else "text",
                        fields=[f"label_{li}", f"value_{li}"])
    q_multi = Question("q_multi", "multi",
                       "Give the applicant's full name and town, separated by a comma.",
                       f"{person}, {fields_spec[4][1]}", kind="anls",
                       fields=["value_0", "value_4"], accept=[f"{person}; {fields_spec[4][1]}"])
    q_count = Question("q_count_checked", "count", "How many of the preference boxes are checked?",
                       str(sum(states)), canon="number",
                       fields=[f"check_{k}" for k in range(4)])
    qs = [q_check, q_blank, rng.choice((q_lookup, q_tier)), rng.choice((q_multi, q_count))]
    rng.shuffle(qs)
    data = {"template": "application", "title": title, "org": org,
            "fields": [{"label": l, "value": (v.isoformat() if k == "date" else v),
                        "blank": i == blank} for i, (l, v, k) in enumerate(fields_spec)],
            "blank": blank_label, "tiers": list(tiers), "tier": tiers[tier],
            "checks": dict(zip(checks, states)), "two_columns": layout_two}
    if abstain:
        data["abstain"] = _abstain(c, rng, qs, None,
                                   ("q_member_no", "What membership number was assigned?",
                                    "member_no", lambda: f"MB-{inv.code(1, 6)}", "text"))
    qs.append(_reading("form"))
    return Rendered(c, "application", qs, data, default_year=start.year)


# ============================================================================ ID-like card
_CARD_KINDS = (("Transit Pass", "Zone"), ("Library Card", "Branch"), ("Member Card", "Plan"),
               ("Staff Badge", "Department"), ("Visitor Badge", "Host"))


def _id_card(rng: random.Random, *, theme: str, scale: float, abstain: bool) -> Rendered:
    inv = Inventor(rng)
    kind, extra_label = rng.choice(_CARD_KINDS)
    org = f"{inv.town()} {rng.choice(('Transit', 'Libraries', 'Leisure', 'Works', 'Arts Center'))}"
    name = inv.person()
    number = f"SMP-{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}"
    issued = inv.day(BASE_DATE, 300)
    expires = date(issued.year + 1, issued.month, min(issued.day, 28))
    if expires.year > 2028:
        expires = date(2028, 12, 31)
    extra = {"Zone": rng.choice(("Zones 1-2", "Zones 1-3", "Zone A", "All zones")),
             "Branch": f"{inv.surname()} Branch",
             "Plan": rng.choice(("Off-peak", "Full access", "Family", "Weekday")),
             "Department": rng.choice(("Facilities", "Records", "Outreach", "Grounds",
                                       "Front Desk")),
             "Host": inv.person()}[extra_label]
    cls = rng.choice(("A", "B", "C", "S"))

    w, h = LOGICAL_SIZES["id_card"]
    c = Canvas(w, h, scale=scale, theme=theme)
    c.rect(0, 0, w, h, fill="surface")
    cx0, cy0, cx1, cy1 = 40, 30, w - 40, h - 30
    c.rect(cx0, cy0, cx1, cy1, fill="bg", outline="border", radius=18, width=2)
    band = rng.choice(("accent", "ok", "warn"))
    c.rect(cx0, cy0, cx1, cy0 + 64, fill=band, radius=18)
    c.rect(cx0, cy0 + 40, cx1, cy0 + 64, fill=band)
    band_fg = "accent_fg" if band == "accent" else "bg"
    c.text(cx0 + 24, cy0 + 12, org, size=18, bold=True, color=band_fg, field="org")
    c.text(cx0 + 24, cy0 + 38, kind, size=13, color=band_fg, field="card_kind")
    c.text_right(cx1 - 24, cy0 + 24, "SAMPLE - NOT A REAL ID", size=13, bold=True,
                 color=band_fg)
    # photo placeholder
    px0, py0 = cx0 + 24, cy0 + 86
    c.rect(px0, py0, px0 + 130, py0 + 160, fill="surface", outline="border")
    c.ellipse(px0 + 40, py0 + 24, px0 + 90, py0 + 74, fill="border")
    c.pieslice(px0 + 18, py0 + 84, px0 + 112, py0 + 200, 180, 360, fill="border")
    c.line(px0, py0 + 160, px0 + 130, py0 + 160, color="border", width=2)
    x = px0 + 160
    rows = [("Name", name, "name", "text", name),
            ("Card number", number, "card_number", "text", number),
            (extra_label, extra, "extra", "text", extra),
            ("Issued", rng.choice((date_short(issued), issued.strftime("%m/%d/%Y"))), "issued",
             "date", issued.isoformat()),
            ("Expires", rng.choice((date_short(expires), expires.strftime("%m/%d/%Y"))),
             "expires", "date", expires.isoformat())]
    y = py0 - 4
    for label, shown, fname, kind, value in rows[:3]:
        c.text(x, y, label.upper(), size=10, color="muted")
        c.text(x, y + 14, shown, size=16 if fname == "name" else 14,
               bold=fname == "name", field=fname, kind=kind, value=value)
        y += 46
    for j, (label, shown, fname, kind, value) in enumerate(rows[3:]):
        xx = x + j * 170
        c.text(xx, y, label.upper(), size=10, color="muted")
        c.text(xx, y + 14, shown, size=14, field=fname, kind=kind, value=value)
    c.text(x, y + 50, "CLASS", size=10, color="muted")
    c.text(x + 46, y + 48, cls, size=14, bold=True, field="class")
    # a fake barcode strip (drawn, not read)
    bx = cx0 + 24
    by = cy1 - 38
    for k in range(60):
        if rng.random() < 0.6:
            c.line(bx + k * 4 + 1, by, bx + k * 4 + 1, by + 22, color="fg",
                   width=rng.choice((1, 2, 3)))
    c.text_right(cx1 - 24, cy1 - 30, "For testing only", size=10, color="muted")

    q_num = Question("q_card_number", "lookup", "What is the card number?", number,
                     fields=["card_number"])
    q_exp = Question("q_expires", "lookup", "When does the card expire?", expires.isoformat(),
                     canon="date", fields=["expires"])
    q_name = Question("q_name", "lookup", "Whose name is on the card?", name, fields=["name"])
    q_multi = Question("q_multi", "multi",
                       "Give the cardholder's name and the card number, separated by a comma.",
                       f"{name}, {number}", kind="anls", fields=["name", "card_number"],
                       accept=[f"{name}; {number}"])
    pool = [Question("q_issued", "lookup", "When was the card issued?", issued.isoformat(),
                     canon="date", fields=["issued"]),
            Question("q_extra", "lookup", f"What {extra_label.lower()} is shown on the card?",
                     extra, fields=["extra"]),
            Question("q_org", "lookup", "Which organisation issued the card?", org,
                     fields=["org"]),
            Question("q_class", "lookup", "What class is printed on the card?", cls,
                     fields=["class"])]
    qs = [q_num, q_exp, rng.choice((q_name, q_multi))] + rng.sample(pool, 1)
    rng.shuffle(qs)
    data = {"template": "id_card", "kind": kind, "org": org, "name": name, "number": number,
            "issued": issued.isoformat(), "expires": expires.isoformat(),
            extra_label.lower(): extra, "class": cls}
    if abstain:
        data["abstain"] = _abstain(c, rng, qs, q_num,
                                   ("q_dob", "What is the cardholder's member PIN?", "pin",
                                    lambda: f"PIN-{rng.randint(1000, 9999)}", "text"))
    qs.append(_reading("card"))
    return Rendered(c, "id_card", qs, data, default_year=issued.year)
