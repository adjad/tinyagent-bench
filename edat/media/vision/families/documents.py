"""Documents and receipts: receipts in ten layouts, and invoices (spec 05 §7.1).

Planted fields: merchant, date, line items, tax, total, receipt or invoice number.
Question types: lookup, count, sum check, multi-field (the reading).
"""
from __future__ import annotations

import random
from datetime import date

from edat.media.common import Inventor, PRODUCTS, date_short, money_str, time_12h
from edat.media.vision.canvas import Canvas
from edat.media.vision.items import ABSTAIN_ANSWER, Question, Rendered

FAMILY = "documents"
TEMPLATES = ("receipt", "invoice")
LOGICAL_SIZES = {"receipt": (360, 640), "invoice": (612, 792)}
BASE_DATE = date(2027, 1, 4)


def render(rng: random.Random, *, template: str, theme: str, scale: float,
           abstain: bool) -> Rendered:
    if template == "receipt":
        return _receipt(rng, theme=theme, scale=scale, abstain=abstain)
    if template == "invoice":
        return _invoice(rng, theme=theme, scale=scale, abstain=abstain)
    raise ValueError(f"unknown template {template!r}")


def _lines(inv: Inventor, n: int) -> list[dict]:
    names = inv.r.sample(PRODUCTS, n)
    out = []
    for name in names:
        qty = inv.r.choice((1, 1, 1, 2, 3))
        unit = inv.money(0.8, 24.0)
        out.append({"name": name, "qty": qty, "unit": unit, "amount": round(qty * unit, 2)})
    return out


def _receipt(rng: random.Random, *, theme: str, scale: float, abstain: bool) -> Rendered:
    inv = Inventor(rng)
    layout = rng.randrange(10)
    centered, upper, dashed = layout & 1, (layout >> 1) & 1, (layout >> 2) & 1
    qty_first = layout in (8, 9)
    merchant = inv.merchant()
    address = f"{inv.street_address()}, {inv.town()}"
    day = inv.day(BASE_DATE, 300)
    clock = inv.clock(8, 21, 1)
    number = inv.code(1, 6)
    lines = _lines(inv, rng.randint(3, 7))
    subtotal = round(sum(l["amount"] for l in lines), 2)
    rate = rng.choice((0.05, 0.0625, 0.07, 0.08, 0.0875))
    tax = round(subtotal * rate, 2)
    total = round(subtotal + tax, 2)

    w, h = LOGICAL_SIZES["receipt"]
    c = Canvas(w, h, scale=scale, theme=theme)
    x0, x1, y = 24, w - 24, 24

    def head(s: str, **kw) -> None:
        nonlocal y
        s2 = s.upper() if upper else s
        if centered:
            c.text_center(w / 2, y, s2, **kw)
        else:
            c.text(x0, y, s2, **kw)
        y += kw.get("size", 13) + 8

    head(merchant, size=19, bold=True, field="merchant", value=merchant)
    head(address, size=11, color="muted")
    head(f"Receipt {number}", size=12, field="receipt_number", value=number)
    date_text = date_short(day) if layout % 3 else day.strftime("%m/%d/%Y")
    c.text(x0, y, date_text, size=12, field="date", kind="date", value=day.isoformat())
    c.text_right(x1, y, time_12h(clock), size=12, field="time", kind="time", value=clock)
    y += 24

    def sep() -> None:
        nonlocal y
        if dashed:
            c.text(x0, y - 4, "-" * 48, size=11, color="muted", reading=False)
        else:
            c.line(x0, y, x1, y)
        y += 12

    sep()
    for i, l in enumerate(lines):
        label = f"{l['qty']} x {l['name']}" if qty_first else l["name"]
        c.text(x0, y, label, size=12, field=f"item_{i}_name", value=l["name"])
        c.text_right(x1, y, money_str(l["amount"]), size=12, field=f"item_{i}_amount",
                     kind="money", value=f"{l['amount']:.2f}")
        y += 18
        if l["qty"] > 1 and not qty_first:
            c.text(x0 + 12, y, f"{l['qty']} @ {money_str(l['unit'])}", size=10, color="muted")
            y += 15
    sep()
    for label, amount, name in (("Subtotal", subtotal, "subtotal"),
                                (f"Tax {rate * 100:g}%", tax, "tax")):
        c.text(x0, y, label, size=12)
        c.text_right(x1, y, money_str(amount), size=12, field=name, kind="money",
                     value=f"{amount:.2f}")
        y += 18
    c.text(x0, y + 2, "TOTAL" if upper else "Total", size=15, bold=True)
    c.text_right(x1, y + 2, money_str(total), size=15, bold=True, field="total", kind="money",
                 value=f"{total:.2f}")
    y += 30
    c.text_center(w / 2, y, "Thank you for shopping with us", size=11, color="muted")

    questions = [
        Question("q_merchant", "lookup", "What is the name of the store on this receipt?",
                 merchant, canon="text", fields=["merchant"]),
        Question("q_date", "lookup", "On what date was this purchase made? Give the date.",
                 day.isoformat(), canon="date", fields=["date"]),
        Question("q_count", "count", "How many different products are listed on the receipt?",
                 str(len(lines)), canon="number", fields=[f"item_{i}_name" for i in range(len(lines))]),
        Question("q_sum", "sum", "What is the subtotal plus the tax?", f"{total:.2f}",
                 canon="money", fields=["subtotal", "tax"]),
        Question("q_reading", "reading", "Read the receipt.", "", kind="reading"),
    ]
    if abstain:
        pick = rng.choice(("blur_total", "absent_cashier"))
        if pick == "blur_total":
            c.blur_field("total", radius=7)
            questions.append(Question("q_total_abstain", "absence",
                                      "What is the total amount on the receipt?",
                                      ABSTAIN_ANSWER, kind="abstain", fields=["total"]))
        else:
            c.absent("cashier", inv.person())
            questions.append(Question("q_cashier", "absence",
                                      "What is the name of the cashier on this receipt?",
                                      ABSTAIN_ANSWER, kind="abstain", fields=["cashier"]))
    else:
        questions.append(Question("q_total", "lookup", "What is the total amount on the receipt?",
                                  f"{total:.2f}", canon="money", fields=["total"]))
    data = {"merchant": merchant, "address": address, "date": day.isoformat(), "time": clock,
            "receipt_number": number, "lines": lines, "subtotal": subtotal, "tax_rate": rate,
            "tax": tax, "total": total, "layout": layout}
    return Rendered(c, "receipt", questions, data, default_year=day.year)


def _invoice(rng: random.Random, *, theme: str, scale: float, abstain: bool) -> Rendered:
    inv = Inventor(rng)
    seller = inv.merchant()
    buyer = inv.person()
    number = f"INV-{rng.randint(1000, 9999)}"
    issued = inv.day(BASE_DATE, 300)
    due_days = rng.choice((14, 30))
    due = date.fromordinal(issued.toordinal() + due_days)
    lines = _lines(inv, rng.randint(2, 6))
    subtotal = round(sum(l["amount"] for l in lines), 2)
    tax = round(subtotal * 0.08, 2)
    total = round(subtotal + tax, 2)

    w, h = LOGICAL_SIZES["invoice"]
    c = Canvas(w, h, scale=scale, theme=theme)
    c.text(48, 48, seller, size=22, bold=True, field="seller", value=seller)
    c.text(48, 80, f"{inv.street_address()}, {inv.town()}", size=11, color="muted")
    c.text_right(w - 48, 48, "INVOICE", size=22, bold=True, color="accent")
    c.text_right(w - 48, 82, number, size=13, field="invoice_number", value=number)
    c.text(48, 124, "Bill to", size=11, color="muted")
    c.text(48, 142, buyer, size=13, field="buyer", value=buyer)
    c.text(360, 124, "Issued", size=11, color="muted")
    c.text(360, 142, date_short(issued), size=13, field="issued", kind="date",
           value=issued.isoformat())
    c.text(470, 124, "Due", size=11, color="muted")
    c.text(470, 142, date_short(due), size=13, field="due", kind="date", value=due.isoformat())
    y = 190
    c.rect(48, y, w - 48, y + 26, fill="surface")
    for x, label in ((56, "Description"), (330, "Qty"), (400, "Unit"), (490, "Amount")):
        c.text(x, y + 7, label, size=11, bold=True)
    y += 34
    for i, l in enumerate(lines):
        c.text(56, y, l["name"], size=12, field=f"item_{i}_name", value=l["name"])
        c.text(330, y, str(l["qty"]), size=12)
        c.text(400, y, money_str(l["unit"]), size=12)
        c.text(490, y, money_str(l["amount"]), size=12, field=f"item_{i}_amount", kind="money",
               value=f"{l['amount']:.2f}")
        y += 22
    c.line(48, y + 4, w - 48, y + 4)
    y += 16
    for label, amount, name, bold in (("Subtotal", subtotal, "subtotal", False),
                                      ("Tax 8%", tax, "tax", False),
                                      ("Total due", total, "total", True)):
        c.text(400, y, label, size=13 if bold else 12, bold=bold)
        c.text(490, y, money_str(amount), size=13 if bold else 12, bold=bold, field=name,
               kind="money", value=f"{amount:.2f}")
        y += 22

    big = max(lines, key=lambda l: l["amount"])
    questions = [
        Question("q_number", "lookup", "What is the invoice number?", number,
                 fields=["invoice_number"]),
        Question("q_due", "lookup", "When is payment due? Give the date.", due.isoformat(),
                 canon="date", fields=["due"]),
        Question("q_max", "max", "Which line item has the largest amount?", big["name"],
                 fields=[f"item_{i}_amount" for i in range(len(lines))]),
        Question("q_total", "lookup", "What is the total due?", f"{total:.2f}", canon="money",
                 fields=["total"]),
        Question("q_reading", "reading", "Read the invoice.", "", kind="reading"),
    ]
    if abstain:
        c.absent("po_number", inv.code(2, 5))
        questions.append(Question("q_po", "absence", "What is the purchase order number?",
                                  ABSTAIN_ANSWER, kind="abstain", fields=["po_number"]))
    else:
        questions.append(Question("q_buyer", "lookup", "Who is the invoice billed to?", buyer,
                                  fields=["buyer"]))
    data = {"seller": seller, "buyer": buyer, "number": number, "issued": issued.isoformat(),
            "due": due.isoformat(), "lines": lines, "subtotal": subtotal, "tax": tax,
            "total": total}
    return Rendered(c, "invoice", questions, data, default_year=issued.year)
