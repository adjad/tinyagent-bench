"""V3 image to action (spec 05 §7.3).

``python -m edat.media.vision.actions generate --split dev --out DIR``

1,000 images of flyers, tickets, invitations, booking confirmations,
appointment cards, due notices and chat screenshots. 70 % show exactly one
dated event or to-do; 15 % show none and 15 % show several, where the right
response is no proposal at all.

The request is Harbor's own: its vision system prompt and message shape
(``service.vision._messages``) with the person's words asking for an action.
The model must end its reply with Harbor's proposal JSON; in the agentic
harness it makes the tool call instead (``add_calendar_event`` or
``add_reminder``, as ``service.vision.proposal_call`` would).

**Scoring** uses Harbor's validator (``service.vision.parse_proposal``):

* one-event images: ``kind``, ISO ``date``, ``start_time``, ``end_time`` and
  ``location`` must match exactly (location after case and space folding);
  the title passes when it contains one of the event's key phrases;
* zero- and several-event images: right only when no valid proposal is given
  (the no-proposal accuracy).

Every drawn date carries its year, so the gold date never depends on the day
the test runs.
"""
from __future__ import annotations

import argparse
import base64
import json
from datetime import date, timedelta
from pathlib import Path

from edat.media.common import (MONTHS, WEEKDAYS, Inventor, canon_text, rng_for,
                               sha256_bytes, time_12h)
from edat.media.vision import generate as gen
from edat.media.vision.canvas import Canvas, render_stamp
from edat.media.vision.reasoning import _field_ok

SCHEMA = "edat.media.vision-action/1"
TOTAL = 1000
TEMPLATES = ("flyer", "ticket", "invitation", "booking", "appointment", "due_notice", "chat")
LOGICAL = {"flyer": (520, 720), "ticket": (640, 300), "invitation": (520, 640),
           "booking": (600, 560), "appointment": (480, 300), "due_notice": (520, 360),
           "chat": (390, 700)}
FIELDS = ("kind", "date", "start_time", "end_time", "location")
REQUESTS = ("Add this to my calendar.", "Can you put this in my calendar?",
            "Save this event for me.", "Remind me about this.", "Add this please.")
BASE_DAY = date(2027, 3, 1)
EVENT_NAMES = ("Spring Pottery Workshop", "Community Book Swap", "Jazz on the Lawn",
               "Night Market", "Beginner Yoga Class", "Coding Club", "Charity 5K Fun Run",
               "Film Night", "Chess Tournament", "Garden Tour", "Choir Rehearsal",
               "Science Fair", "Plant Sale", "Poetry Reading", "Board Game Night",
               "Bake Sale", "Open Studio Day", "Photography Walk", "Quiz Night", "Salsa Lesson")
VENUES = ("Hall B", "Riverside Park", "Main Library", "Studio 4", "Town Square",
          "Room 210", "Corner Cafe", "Community Centre", "Old Mill Gallery", "Pier 3")
DOCTORS = ("Dental check-up", "Eye exam", "Physio session", "Annual physical", "Haircut",
           "Car service", "Vet visit", "Passport photo")
DUE = ("Library books due", "Rent due", "Permit renewal due", "Insurance payment due",
       "Return parcel by", "Parking permit expires")


def date_forms(d: date, rng) -> str:
    style = rng.randrange(4)
    if style == 0:
        return f"{MONTHS[d.month - 1]} {d.day}, {d.year}"
    if style == 1:
        return f"{WEEKDAYS[d.weekday()][:3]} {d.day} {MONTHS[d.month - 1][:3]} {d.year}"
    if style == 2:
        return f"{WEEKDAYS[d.weekday()]}, {MONTHS[d.month - 1]} {d.day}, {d.year}"
    return d.isoformat()


def time_form(hhmm: str, rng) -> str:
    return hhmm if rng.random() < 0.3 else time_12h(hhmm)


def _event(rng, inv, *, with_end=None, kind="event", title=None):
    d = BASE_DAY + timedelta(days=rng.randint(3, 200))
    start = f"{rng.randint(8, 20):02d}:{rng.choice((0, 15, 30, 45)):02d}"
    end = None
    if with_end if with_end is not None else rng.random() < 0.5:
        sh, sm = map(int, start.split(":"))
        mins = sh * 60 + sm + rng.choice((45, 60, 90, 120, 150))
        if mins < 24 * 60:
            end = f"{mins // 60:02d}:{mins % 60:02d}"
    return {"kind": kind, "title": title or rng.choice(EVENT_NAMES), "date": d.isoformat(),
            "start_time": start, "end_time": end,
            "location": f"{rng.choice(VENUES)}, {inv.street_address()}"}


def _when_line(ev, rng) -> str:
    t = time_form(ev["start_time"], rng)
    if ev["end_time"]:
        t += f" - {time_form(ev['end_time'], rng)}"
    return t


def _draw_event_block(c: Canvas, x, y, ev, rng, *, prefix="", title_size=24, tag=""):
    d = date.fromisoformat(ev["date"])
    c.text(x, y, ev["title"], size=title_size, bold=True, field=f"title{tag}")
    c.text(x, y + title_size + 18, prefix + date_forms(d, rng), size=16, field=f"date{tag}",
           kind="date", value=ev["date"])
    if ev["start_time"]:
        c.text(x, y + title_size + 46, _when_line(ev, rng), size=16, field=f"time{tag}")
    if ev["location"]:
        c.text(x, y + title_size + 74, ev["location"], size=14, color="muted",
               field=f"location{tag}")
    return y + title_size + 104


def case_for(index: int) -> str:
    """"one", "none" or "several" events for item ``index`` (3 in 20 none, 3 in 20 several)."""
    slot = (index // len(TEMPLATES)) % 20
    return "none" if slot in (2, 9, 16) else "several" if slot in (5, 12, 19) else "one"


def build_item(index: int, *, seed: str) -> dict:
    """Draw one item; returns {canvas, template, expect, events, title_keys, request}."""
    rng = rng_for(seed, "v3", index)
    inv = Inventor(rng)
    template = TEMPLATES[index % len(TEMPLATES)]
    k = index // len(TEMPLATES)
    theme, dpr = ("light", "dark")[k % 2], (1, 2)[(k // 2) % 2]
    case = case_for(index)
    W, H = LOGICAL[template]
    c = Canvas(W, H, scale=dpr, theme=theme)
    events: list[dict] = []
    keys: list[str] = []
    if template in ("flyer", "invitation"):
        c.rect(0, 0, W, 90, fill="accent")
        host = inv.person() if template == "invitation" else inv.merchant()
        c.text(28, 30, ("You're invited by " if template == "invitation" else "") + host,
               size=18, color="accent_fg")
        y = 130
        if case == "one":
            ev = _event(rng, inv, title=(f"{inv.given()}'s Birthday Party" if template ==
                                         "invitation" else None))
            _draw_event_block(c, 28, y, ev, rng)
            events.append(ev)
            keys = _keys(ev["title"])
            c.text(28, 520, rng.choice(("Free entry. All welcome.", "RSVP by text.",
                                        "Bring a friend!")), size=14, color="muted")
        elif case == "several":
            c.text(28, y, rng.choice(("Spring Season", "Upcoming Sessions", "This Month")),
                   size=24, bold=True)
            yy = y + 50
            for n in range(rng.randint(2, 3)):
                ev = _event(rng, inv, with_end=False)
                d = date.fromisoformat(ev["date"])
                c.text(28, yy, f"{ev['title']}", size=16, bold=True)
                c.text(28, yy + 24, f"{date_forms(d, rng)}, {time_form(ev['start_time'], rng)}",
                       size=14)
                events.append(ev)
                yy += 70
        else:
            c.text(28, y, rng.choice(("Now open!", "New menu", "Grand opening offer")), size=26,
                   bold=True)
            c.text(28, y + 50, f"{inv.street_address()}, {inv.town()}", size=15)
            c.text(28, y + 80, "Open every day 9 to 6. No booking needed.", size=14,
                   color="muted")
    elif template == "ticket":
        c.rect(0, 0, W, H, fill="surface")
        c.rect(W - 150, 0, W - 148, H, fill="border")
        if case == "one":
            ev = _event(rng, inv, with_end=False)
            ev["title"] = f"{rng.choice(('The Lanterns', 'Velvet Static', 'Orla Quartet', 'Paper Moons'))} Live"
            _draw_event_block(c, 24, 24, ev, rng, prefix="")
            c.text(W - 132, 30, "ADMIT ONE", size=14, bold=True)
            c.text(W - 132, 60, f"Seat {rng.randint(1, 30)}{rng.choice('ABCDEFG')}", size=14)
            events.append(ev)
            keys = _keys(ev["title"])
        elif case == "several":
            for n in range(2):
                ev = _event(rng, inv, with_end=False)
                c.text(24, 24 + n * 120, ev["title"], size=18, bold=True)
                c.text(24, 52 + n * 120, f"{date_forms(date.fromisoformat(ev['date']), rng)} "
                       f"{time_form(ev['start_time'], rng)}", size=14)
                events.append(ev)
            c.text(W - 132, 30, "2 EVENTS", size=14, bold=True)
        else:
            c.text(24, 24, "Gift voucher", size=22, bold=True)
            c.text(24, 64, f"Value {rng.choice(('$25', '$50', '$100'))} at {inv.merchant()}",
                   size=15)
            c.text(24, 94, f"Code {inv.code(3, 5)}", size=15)
    elif template == "booking":
        c.text(28, 24, f"{inv.brand()} {rng.choice(('Bistro', 'Adventures', 'Spa', 'Tours'))}", size=20,
               bold=True)
        c.text(28, 60, "Booking confirmation", size=15, color="muted")
        c.text(28, 84, f"Reference {inv.booking_ref()}", size=14)
        if case == "one":
            ev = _event(rng, inv, title=rng.choice(("Table for 4", "Escape room", "Spa treatment",
                                                    "Cooking class", "Bike tour")))
            _draw_event_block(c, 28, 130, ev, rng, title_size=20)
            events.append(ev)
            keys = _keys(ev["title"])
        elif case == "several":
            yy = 130
            for n in range(rng.randint(2, 3)):
                ev = _event(rng, inv, with_end=False, title=rng.choice(("Tour", "Lesson",
                                                                         "Session")) + f" {n + 1}")
                c.text(28, yy, f"{ev['title']}: {date_forms(date.fromisoformat(ev['date']), rng)} "
                       f"at {time_form(ev['start_time'], rng)}", size=15)
                events.append(ev)
                yy += 40
        else:
            c.text(28, 130, "Your booking has been cancelled.", size=17, bold=True)
            c.text(28, 164, "No charge was made. We hope to see you soon.", size=14)
    elif template == "appointment":
        c.rect(0, 0, W, H, fill="surface", outline="border", radius=12)
        c.text(24, 20, f"{inv.surname()} {rng.choice(('Clinic', 'Health', 'Studio', 'Garage'))}", size=16,
               color="muted")
        if case == "one":
            ev = _event(rng, inv, title=rng.choice(DOCTORS))
            _draw_event_block(c, 24, 52, ev, rng, title_size=20)
            events.append(ev)
            keys = _keys(ev["title"])
        elif case == "several":
            yy = 56
            for n in range(2):
                ev = _event(rng, inv, with_end=False, title=rng.choice(DOCTORS))
                c.text(24, yy, f"{ev['title']}: {date_forms(date.fromisoformat(ev['date']), rng)}, "
                       f"{time_form(ev['start_time'], rng)}", size=14)
                events.append(ev)
                yy += 40
        else:
            c.text(24, 60, "Please call us to book your next visit.", size=16)
            c.text(24, 92, inv.phone(), size=16)
    elif template == "due_notice":
        c.text(28, 24, inv.merchant(), size=18, bold=True)
        if case == "one":
            d = BASE_DAY + timedelta(days=rng.randint(3, 200))
            title = rng.choice(DUE)
            ev = {"kind": "reminder", "title": title, "date": d.isoformat(), "start_time": None,
                  "end_time": None, "location": None}
            c.text(28, 80, title, size=22, bold=True, field="title")
            c.text(28, 120, date_forms(d, rng), size=18, field="date", kind="date",
                   value=d.isoformat())
            c.text(28, 170, "Late fees apply after this date.", size=14, color="muted")
            events.append(ev)
            keys = _keys(title.replace(" by", ""))
        elif case == "several":
            yy = 80
            for n in range(2):
                d = BASE_DAY + timedelta(days=rng.randint(3, 200))
                title = rng.choice(DUE)
                c.text(28, yy, f"{title} {date_forms(d, rng)}", size=16)
                events.append({"kind": "reminder", "title": title, "date": d.isoformat(),
                               "start_time": None, "end_time": None, "location": None})
                yy += 40
        else:
            c.text(28, 80, "Thank you, your account is paid up.", size=18)
            c.text(28, 116, "Nothing is due.", size=15, color="muted")
    else:   # chat
        friend = inv.given()
        c.rect(0, 0, W, 64, fill="surface")
        c.text_center(W / 2, 22, friend, size=16, bold=True)
        msgs: list[tuple[str, bool]] = [(f"Hey! Are you free {rng.choice(('soon', 'next week'))}?", False)]
        if case == "one":
            ev = _event(rng, inv, with_end=False, title=rng.choice(("Dinner", "Coffee", "Movie",
                                                                    "Climbing", "Brunch")))
            d = date.fromisoformat(ev["date"])
            venue = ev["location"].split(",")[0]
            ev["location"] = venue
            msgs += [(f"{ev['title']} at {venue}?", False),
                     (f"{date_forms(d, rng)} at {time_form(ev['start_time'], rng)}", False),
                     ("Perfect, see you there!", True)]
            events.append(ev)
            keys = _keys(ev["title"])
        elif case == "several":
            for n in range(2):
                ev = _event(rng, inv, with_end=False, title=rng.choice(("Dinner", "Coffee",
                                                                        "Movie", "Brunch")))
                ev["location"] = ev["location"].split(",")[0]
                msgs.append((f"{ev['title']} at {ev['location']}, "
                             f"{date_forms(date.fromisoformat(ev['date']), rng)} "
                             f"{time_form(ev['start_time'], rng)}?", False))
                events.append(ev)
            msgs.append(("Both work for me!", True))
        else:
            msgs += [("Not really, busy month.", True), ("No worries, another time!", False)]
        y = 84
        for text, mine in msgs:
            w = min(W - 24, c.text_width(text, 14) + 24)
            x0 = W - w - 12 if mine else 12
            c.rect(x0, y, x0 + w, y + 34, fill="bubble_out" if mine else "bubble_in", radius=14)
            c.text(x0 + 12, y + 9, text, size=14, color="bubble_out_fg" if mine else "fg")
            y += 46
    expect = dict(events[0]) if case == "one" else None
    if expect is not None:
        expect.setdefault("notes", None)
    return {"canvas": c, "template": template, "case": case, "events": events, "expect": expect,
            "title_keys": keys, "request": rng.choice(REQUESTS), "theme": theme, "dpr": dpr}


def _keys(title: str) -> list[str]:
    """Key phrases a right title contains: the whole title, else its distinctive part."""
    t = canon_text(title)
    keys = [t]
    if "'s " in t:
        keys.append(t.split("'s ", 1)[1])
    words = [w for w in t.split() if len(w) > 3 and w not in ("live", "class", "session")]
    if len(words) >= 2:
        keys.append(" ".join(words[-2:]))
    return list(dict.fromkeys(keys))


# ---------------------------------------------------------------- scoring

def _vision():
    from service import vision      # Harbor's own parser and tool mapping
    return vision


def title_matches(pred: str | None, keys: list[str]) -> bool:
    p = canon_text(pred or "")
    return bool(p) and any(k and k in p for k in keys)


def score_reply(item: dict, output: str | None) -> dict:
    """Score a model's text reply with Harbor's ``parse_proposal``."""
    _, proposal = _vision().parse_proposal(output or "")
    return score_proposal(item, proposal)


def score_proposal(item: dict, proposal: dict | None) -> dict:
    gold = item["expect"]
    res = {"item_id": item["item_id"], "case": item["case"], "proposed": proposal is not None,
           "fields": {}, "passed": False}
    if gold is None:
        res["passed"] = proposal is None
        res["no_proposal_ok"] = res["passed"]
        return res
    if proposal is None:
        return res
    f = res["fields"]
    for name in FIELDS:
        a, b = proposal.get(name), gold.get(name)
        if name == "location":
            f[name] = canon_text(a or "") == canon_text(b or "")
        else:
            f[name] = a == b
    f["title"] = title_matches(proposal.get("title"), item["title_keys"])
    res["passed"] = all(f.values())
    return res


def score_tool_call(item: dict, name: str | None, args: dict | None) -> dict:
    """Score the agentic form: the tool call Harbor would make from the gold proposal."""
    gold = item["expect"]
    res = {"item_id": item["item_id"], "case": item["case"], "called": name is not None,
           "fields": {}, "passed": False}
    if gold is None:
        res["passed"] = name is None
        return res
    if name is None or not isinstance(args, dict):
        return res
    want_name, want = _vision().proposal_call(gold)
    f = res["fields"]
    f["tool"] = name == want_name
    for k, v in want.items():
        if k == "title":
            f[k] = title_matches(args.get(k), item["title_keys"])
        elif k == "location":
            f[k] = canon_text(args.get(k) or "") == canon_text(v)
        else:
            f[k] = args.get(k) == v
    res["passed"] = all(f.values())
    return res


def harbor_messages(item: dict, image_bytes: bytes, mime: str = "image/png") -> list[dict]:
    """Harbor's exact vision request for this item (system prompt + image + request)."""
    url = f"data:{mime};base64," + base64.b64encode(image_bytes).decode("ascii")
    return _vision()._messages(item["request"], [url], None, None)


# ---------------------------------------------------------------- generation

def build(index: int, *, split: str, seed: str | None = None) -> tuple[dict, bytes]:
    seed = seed if seed is not None else gen.seed_for(split)
    d = build_item(index, seed=seed)
    c = d["canvas"]
    for name in list(c.fields):
        if not _field_ok(c, name):
            raise RuntimeError(f"v3 {index}: field {name} fails its check")
    if d["expect"] is not None:
        vision = _vision()
        if vision._validate_proposal(d["expect"]) != {**d["expect"], "notes": None}:
            raise RuntimeError(f"v3 {index}: gold proposal does not pass Harbor's validator")
    data = gen.png_bytes(c.image)
    iid = f"v3-{d['template']}-{split}-{index:04d}"
    meta = {"schema": SCHEMA, "item_id": iid, "template": d["template"], "split": split,
            "seed": seed if split == "dev" else "private", "index": index, "case": d["case"],
            "variant": {"theme": d["theme"], "dpr": d["dpr"]},
            "image": {"file": f"{iid}.png", "sha256": sha256_bytes(data), "width": c.size[0],
                      "height": c.size[1]},
            "render": render_stamp(), "request": d["request"], "expect": d["expect"],
            "title_keys": d["title_keys"], "events": d["events"],
            "fields": {k: f.to_json() for k, f in c.fields.items()},
            "reading_text": c.reading_text()}
    if d["expect"] is not None:
        gold_reply = "Here it is.\n" + json.dumps({"proposal": d["expect"]})
        assert score_reply(meta, gold_reply)["passed"], iid
        assert score_tool_call(meta, *_vision().proposal_call(
            _vision()._validate_proposal(d["expect"])))["passed"], iid
    else:
        assert score_reply(meta, "This shows no single event.")["passed"], iid
    assert not score_reply(meta, "")["passed"] or d["expect"] is None
    return meta, data


def generate(out: Path, *, split: str, count: int = TOTAL, start: int = 0,
             indices=None) -> dict:
    """Write V3 items ``start`` .. ``start + count``, or exactly ``indices`` (a tier's list)."""
    if split == "heldout" and gen._inside_repo(out):
        raise RuntimeError("held-out items must not be written inside the repository")
    out.mkdir(parents=True, exist_ok=True)
    counts: dict = {}
    for i in (range(start, start + count) if indices is None else indices):
        meta, data = build(i, split=split)
        (out / meta["image"]["file"]).write_bytes(data)
        (out / f"{meta['item_id']}.json").write_text(json.dumps(meta, indent=1) + "\n")
        counts[meta["case"]] = counts.get(meta["case"], 0) + 1
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="tinyagent-bench V3 image-to-action items")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--split", default="dev", choices=("dev", "heldout"))
    g.add_argument("--count", type=int, default=TOTAL)
    g.add_argument("--start", type=int, default=0)
    g.add_argument("--tier", choices=("core", "extended", "full"),
                   help="write the tier's items instead of --start/--count")
    g.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    indices = None
    if args.tier:
        from edat.media import tiers
        indices = tiers.select_vision(tiers.load_manifest(args.tier))["v3"]
    print(json.dumps(generate(Path(args.out), split=args.split, count=args.count,
                              start=args.start, indices=indices)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
