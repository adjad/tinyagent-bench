"""Dedicated structured-output cases (spec 03 s8): 60 per benchmark version.

Seven schema shapes, each over a short invented source:
flat_object, array_of_objects, enum_classification, optional_fields,
nested_object, numbers_vs_strings, date_formats.
"""
from __future__ import annotations

import datetime as dt

from edat.quality import world as W
from edat.quality.generate import CASE_SCHEMA, GENERATOR_VERSION, _finish
from edat.quality.render import fence  # noqa: F401  (kept for callers that fence schema sources)

SCHEMA_KINDS = ("flat_object", "array_of_objects", "enum_classification", "optional_fields", "nested_object",
                "numbers_vs_strings", "date_formats")


def _base(kind: str, seed: int, split: str, index: int, w: W.World) -> dict:
    return {"schema": CASE_SCHEMA, "case_id": f"q.schema.{kind}.S.{split}.{index:03d}", "family": "schema",
            "source_type": "schema", "schema_kind": kind, "tier": "S", "split": split, "seed": seed,
            "generator": GENERATOR_VERSION, "today": w.today.isoformat(), "case_year": w.year, "tz": "UTC+01:00",
            "persona": dict(W.PERSONA), "target_ref_tokens": 300, "items": [], "facts": [], "traps": [],
            "tasks": [], "renderings": ["primary"], "conditions": ["unfenced"]}


def _task(request: str, schema: dict, gold, key_fields, field_types, traps=()) -> dict:
    return {"kind": "extraction", "qtype": "format", "request": request, "schema": schema, "gold": gold,
            "key_fields": list(key_fields), "field_types": field_types, "traps": list(traps)}


def make_schema_case(kind: str, *, seed: int, split: str, index: int) -> dict:
    w = W.World.make(seed)
    rng = w.rng
    c = _base(kind, seed, split, index, w)
    if kind == "flat_object":
        d = w.date_after(3, 40)
        t1 = w.time(9, 16)
        t2 = f"{int(t1[:2]) + 1:02d}:{t1[3:]}"
        place = w.place()
        title = rng.choice(["Pottery taster", "Bike workshop", "Volunteer briefing", "Tasting evening"])
        text = (f"Hi {W.PERSONA['first']}, the {title.lower()} is on {W.long_date(d)}, starting at "
                f"{W.spoken_time(t1)} and finishing at {W.spoken_time(t2)}, at {place}. Bring comfy shoes!")
        schema = {"type": "object", "additionalProperties": False, "required": ["name", "date", "start", "end", "location"],
                  "properties": {"name": {"type": "string"}, "date": {"type": "string", "format": "date"},
                                 "start": {"type": "string", "pattern": "^[0-2][0-9]:[0-5][0-9]$"},
                                 "end": {"type": "string", "pattern": "^[0-2][0-9]:[0-5][0-9]$"},
                                 "location": {"type": "string"}}}
        gold = {"name": title, "date": d.isoformat(), "start": t1, "end": t2, "location": place}
        task = _task("Extract the event from this message as one JSON object matching this schema.", schema, gold, [],
                     {"name": "text_loose", "date": "date", "start": "time", "end": "time", "location": "text"})
    elif kind == "array_of_objects":
        people = rng.sample(w.contacts, rng.choice([3, 4, 5]))
        rows = []
        lines = []
        for p in people:
            ph = f"+44 20 7946 0{rng.randrange(100, 999)}"
            rows.append({"name": p["name"], "phone": ph.replace(" ", ""), "email": p["email"]})
            lines.append(f"{p['name']} can be reached on {ph} or at {p['email']}.")
        text = "Contact sheet for the street party volunteers:\n" + "\n".join(lines)
        schema = {"type": "array", "minItems": len(rows), "maxItems": len(rows),
                  "items": {"type": "object", "additionalProperties": False, "required": ["name", "phone", "email"],
                            "properties": {"name": {"type": "string"}, "phone": {"type": "string"},
                                           "email": {"type": "string", "format": "email"}}}}
        gold = rows
        task = _task("Return every volunteer as a JSON array matching this schema. Write phone numbers without spaces.",
                     schema, gold, ["email"], {"name": "text", "phone": "code", "email": "text"})
    elif kind == "enum_classification":
        labels = ["bill", "plan", "news", "spam"]
        msgs = []
        gold = []
        templates = {
            "bill": lambda: f"Your {rng.choice(['water', 'phone', 'energy'])} bill of {W.fmt_amount(w.amount(20, 200))} is ready.",
            "plan": lambda: f"Shall we meet at {w.place()} on {W.long_date(w.date_after(2, 30))}?",
            "news": lambda: f"{w.person()['first']} just started a new job in {rng.choice(W.CITIES)}.",
            "spam": lambda: "You have won a prize! Click to claim your reward now.",
        }
        n = 8
        for i in range(n):
            lab = labels[i % 4] if i < 4 else rng.choice(labels)
            msgs.append(f"{i + 1}. {templates[lab]()}")
            gold.append(lab)
        text = "Messages:\n" + "\n".join(msgs)
        schema = {"type": "array", "minItems": n, "maxItems": n, "items": {"enum": labels}}
        task = _task(f"Classify each of the {n} messages, in order, as one of bill, plan, news or spam. "
                     "Reply with a JSON array matching this schema.", schema, gold, [], {})
    elif kind == "optional_fields":
        d = w.date_after(3, 30)
        has_time = rng.random() < 0.5
        t = w.time(9, 18) if has_time else None
        has_place = not has_time or rng.random() < 0.5
        place = w.place() if has_place else None
        what = rng.choice(["Boiler service", "Window cleaning", "Meter reading", "Furniture delivery"])
        text = f"{what} booked for {W.long_date(d)}" + (f" at {t}" if t else ", time to be confirmed") + \
               (f", at {place}." if place else ".")
        schema = {"type": "object", "additionalProperties": False, "required": ["what", "date", "time", "place"],
                  "properties": {"what": {"type": "string"}, "date": {"type": "string", "format": "date"},
                                 "time": {"type": ["string", "null"]}, "place": {"type": ["string", "null"]}}}
        gold = {"what": what, "date": d.isoformat(), "time": t, "place": place}
        task = _task("Extract this booking as JSON matching this schema. Use null for anything not stated.", schema, gold,
                     [], {"what": "text_loose", "date": "date", "time": "time", "place": "text"})
    elif kind == "nested_object":
        code = w.code("ORD", 5)
        n_items = rng.choice([2, 3])
        items = []
        lines = []
        for _ in range(n_items):
            name = rng.choice(["notebook", "desk lamp", "kettle descaler", "bike light", "tea tin", "plant pot"])
            qty = rng.randrange(1, 5)
            price = w.amount(3, 60)
            items.append({"name": name, "qty": qty, "price": float(price)})
            lines.append(f"- {qty} x {name} at {W.fmt_amount(price)} each")
        text = f"Order {code} confirmed for {W.PERSONA['name']}.\n" + "\n".join(lines)
        schema = {"type": "object", "additionalProperties": False, "required": ["order", "items"],
                  "properties": {"order": {"type": "string"},
                                 "items": {"type": "array", "items": {
                                     "type": "object", "additionalProperties": False, "required": ["name", "qty", "price"],
                                     "properties": {"name": {"type": "string"}, "qty": {"type": "integer"},
                                                    "price": {"type": "number"}}}}}}
        gold = {"order": code, "items": items}
        task = _task("Return this order as JSON matching this schema.", schema, gold, [],
                     {"order": "code"})
    elif kind == "numbers_vs_strings":
        code = w.code(rng.choice(["RM", "LK"]), 4)
        qty = rng.randrange(2, 30)
        weight = round(rng.uniform(0.4, 12.0), 1)
        price = w.amount(5, 400)
        text = (f"Locker {code} holds {qty} boxes with a total weight of {weight} kg. Storage costs "
                f"{W.fmt_amount(price)} per month.")
        schema = {"type": "object", "additionalProperties": False, "required": ["locker", "boxes", "weight_kg", "monthly_eur"],
                  "properties": {"locker": {"type": "string"}, "boxes": {"type": "integer"},
                                 "weight_kg": {"type": "number"}, "monthly_eur": {"type": "number"}}}
        gold = {"locker": code, "boxes": qty, "weight_kg": weight, "monthly_eur": float(price)}
        task = _task("Extract the locker details as JSON matching this schema. Numbers must be JSON numbers.", schema, gold,
                     [], {"locker": "code", "boxes": "number", "weight_kg": "number", "monthly_eur": "amount"})
    elif kind == "date_formats":
        ds = [w.date_after(2, 60) for _ in range(3)]
        forms = [W.long_date(ds[0]), f"{ds[1].day}/{ds[1].month}/{ds[1].year}", f"{W.us_date(ds[2])}"]
        names = rng.sample(["passport renewal", "car tax", "library books", "gym contract"], 3)
        text = "Reminders:\n" + "\n".join(f"- {n} due {f}" for n, f in zip(names, forms))
        schema = {"type": "array", "minItems": 3, "maxItems": 3,
                  "items": {"type": "object", "additionalProperties": False, "required": ["item", "due"],
                            "properties": {"item": {"type": "string"}, "due": {"type": "string", "format": "date"}}}}
        gold = [{"item": n, "due": d.isoformat()} for n, d in zip(names, ds)]
        task = _task("List the reminders as JSON matching this schema, with dates as YYYY-MM-DD. "
                     "Numeric dates are day/month/year.", schema, gold, ["due"], {"item": "text_loose", "due": "date"})
    else:
        raise ValueError(kind)
    c["items"].append({"id": "src-01", "text": text})
    task["task_id"] = f"{c['case_id']}/extraction"
    c["tasks"].append(task)
    out = _finish(c, rng)
    out["renderings"] = ["primary"]
    return out
