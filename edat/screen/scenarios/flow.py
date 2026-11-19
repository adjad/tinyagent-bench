"""Scenarios for family M (Flow), and the setup helpers families D and X reuse (spec 11 §3.7).

A case names its scenario in `world.generator` and lists setup helpers in
`world.params.setup`; each helper reads the apps' seeded state, makes it fit
the task (an app that has camera access, a list with enough open items, a
free time for a moved event) and returns the slots the task, the oracle and
the predicates use. Everything is drawn from the bind rng, so a case is the
same on the same world seed and differs between seeds.

    world:
      generator: flow_setup
      params: {setup: [settings_pick, notes_pick], settings: {extra_rows: 12}}

Helpers: `settings_pick`, `display_target`, `notes_pick`, `notes_long`, `notes_groceries_short`,
`notes_extra_list`, `files_pick`, `files_download`, `calendar_pick`, `new_event`, `time_regex`,
`invite_end`, `mail_address`, `clinic_long_dob`, `clinic_align`, `clinic_prefill`, `travel_long_date`,
`browser_open`.
"""
from __future__ import annotations

import random
from datetime import date, timedelta

from edat.screen.apps import pools
from edat.screen.cases.bind import fill, scenario

SETUP: dict = {}
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
NEW_ITEMS = ["Paper towels", "Birthday card", "Light bulbs", "Plasters", "Toothpaste", "Bird seed", "Candles",
             "Envelopes", "Shoe polish", "Dishwasher tablets", "Matches", "Tissues"]
PRINT_ITEMS = ["Seating plan", "Tax form", "Recipe cards", "Map of the walk"]
RENAME_SUFFIX = [" - signed", " (final)", " - for the accountant", " - checked", " - copy for Pia", " (old)"]
LOCATIONS = ["Room 4, Kelmouth Library", "Copper Row Café", "Fernvale Hall, side entrance", "Video call",
             "Mill Street community room", "Heron Way surgery"]
TIMES = [f"{h:02d}:{m:02d}" for h in range(8, 21) for m in (0, 30) if (h, m) != (20, 30)]


def setup(name: str):
    def deco(fn):
        SETUP[name] = fn
        return fn
    return deco


def slug(s: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(s).lower()).strip("_")


def long_date(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}"


def decode(spec: dict) -> dict:
    """A surprise spec from `world.params.disrupt`: element keys there are written with "/" instead of "."
    (the authoring lint scans params for host names), so turn `key` and `when.on_key` back into dotted keys."""
    out = dict(spec)
    if isinstance(out.get("key"), str):
        out["key"] = out["key"].replace("/", ".")
    when = dict(out.get("when") or {})
    if isinstance(when.get("on_key"), str):
        when["on_key"] = when["on_key"].replace("/", ".")
    if when:
        out["when"] = when
    return out


def run_setup(world, rng: random.Random, params: dict) -> dict:
    """Run the helpers named in `params.setup` in order; each sees the slots so far."""
    slots: dict = {}
    for name in params.get("setup") or []:
        out = SETUP[name](world, rng, params, {**world.slots, **slots}) or {}
        slots.update(out)
        world.slots.update(out)
    return slots


# ---------------------------------------------------------------- Settings

@setup("settings_pick")
def _settings_pick(world, rng, params, slots):
    st = world.state["settings"]
    order = list(st["notifications"]["order"])
    a1, a2 = rng.sample(order, k=2)
    for a in (a1, a2):
        st["notifications"]["apps"][a]["allow"] = True
        st["notifications"]["apps"][a]["sounds"] = True
        st["notifications"]["apps"][a]["badges"] = True
        st["notifications"]["apps"][a]["banner"] = "Temporary"
    out = {"notif_app": a1, "notif_app_key": slug(a1), "notif_app2": a2, "notif_app2_key": slug(a2)}
    acc = st["privacy"]["access"]
    cam = rng.choice(sorted(acc["camera"]))
    mic_pool = sorted(a for a in acc["microphone"] if a != cam) or sorted(acc["microphone"])
    mic = rng.choice(mic_pool)
    acc["camera"][cam] = True
    acc["microphone"][mic] = True
    out.update({"cam_app": cam, "cam_app_key": slug(cam), "mic_app": mic, "mic_app_key": slug(mic)})
    st["privacy"]["analytics"] = True
    st["privacy"]["personalised_ads"] = True
    st["battery"]["low_power"] = False
    st["display"]["night_shift"] = False
    st["display"]["auto_brightness"] = True
    devs = st["bluetooth"]["devices"]
    out.update({"bt_on": devs[0]["name"], "bt_on_key": slug(devs[0]["name"]),
                "bt_off": devs[1]["name"], "bt_off_key": slug(devs[1]["name"])})
    if st["battery"]["display_off"] == "Never":
        st["battery"]["display_off"] = "5 minutes"
    return out


@setup("display_target")
def _display_target(world, rng, params, slots):
    """Make the seeded appearance differ from the one the task asks for."""
    want = params.get("appearance", "Dark")
    d = world.state["settings"]["display"]
    if d["appearance"] == want:
        d["appearance"] = rng.choice([a for a in ("Light", "Dark", "Auto") if a != want])
    d["text_size"] = "Default"
    return {}


# ---------------------------------------------------------------- Notes

def _ensure_open(lst: dict, rng, n: int) -> None:
    items = lst["items"]
    open_ = [i for i in items if not i["done"]]
    for it in rng.sample([i for i in items if i["done"]], k=max(0, min(n - len(open_), sum(i["done"] for i in items)))):
        it["done"] = False


@setup("notes_pick")
def _notes_pick(world, rng, params, slots):
    lists = world.state["notes"]["lists"]
    g = lists[0]
    _ensure_open(g, rng, 4)
    near = [i for i in g["items"][:12] if not i["done"]]
    if len(near) < 4:
        near = [i for i in g["items"] if not i["done"]]
    picks = rng.sample(near, k=min(4, len(near)))
    out = {"l2_name": lists[1]["name"], "l2_id": lists[1]["id"]}
    for j, it in enumerate(picks, 1):
        out[f"g_open{j}"] = it["text"]
        out[f"g_open{j}_id"] = it["id"]
    have = {i["text"].casefold() for l in lists for i in l["items"]}
    fresh = [x for x in NEW_ITEMS if x.casefold() not in have]
    a, b = rng.sample(fresh, k=2)
    out.update({"new1": a, "new2": b, "renamed": f"{picks[-1]['text']} (large)"})
    return out


@setup("notes_groceries_short")
def _notes_short(world, rng, params, slots):
    """Groceries trimmed to three open items and two ticked off (a list to copy into an email)."""
    from edat.screen.apps.notes import GROCERIES
    g = world.state["notes"]["lists"][0]
    have = {i["text"] for i in g["items"]}
    used = {str(i["id"]) for l in world.state["notes"]["lists"] for i in l["items"]}
    for t in [x for x in GROCERIES if x not in have][: max(0, 5 - len(g["items"]))]:
        k = len(used) + 1
        while f"i{k}" in used:
            k += 1
        used.add(f"i{k}")
        g["items"].append({"id": f"i{k}", "text": t, "done": False})
    keep = rng.sample(g["items"], k=5)
    for i, it in enumerate(keep):
        it["done"] = i >= 3
    g["items"] = keep
    out = {}
    for j, it in enumerate(keep[:3], 1):
        out[f"g_open{j}"] = it["text"]
    for j, it in enumerate(keep[3:], 1):
        out[f"g_done{j}"] = it["text"]
    return out


@setup("notes_extra_list")
def _notes_extra(world, rng, params, slots):
    lists = world.state["notes"]["lists"]
    name = params.get("extra_list", "To print")
    used = {str(i["id"]) for l in lists for i in l["items"]}
    items = []
    for t in rng.sample(PRINT_ITEMS, k=2):
        k = len(used) + 1
        while f"i{k}" in used:
            k += 1
        used.add(f"i{k}")
        items.append({"id": f"i{k}", "text": t, "done": False})
    lists.append({"id": "lx", "name": name, "items": items})
    return {"x_list": name, "x_list_idx": len(lists) - 1}


# ---------------------------------------------------------------- Files

@setup("files_pick")
def _files_pick(world, rng, params, slots):
    items = world.state["files"]["items"]
    docs = [f for f in items if f["folder"] == "Documents" and not f["name"].startswith("Invoice")]
    doc = rng.choice(docs)
    stem, _, ext = doc["name"].rpartition(".")
    new = f"{stem}{rng.choice(RENAME_SUFFIX)}.{ext}"
    inv = [f for f in items if f["name"].startswith("Invoice")]
    inv_pick = inv[0]
    return {"doc_name": doc["name"], "doc_id": doc["id"], "doc_new_name": new,
            "inv_name": inv_pick["name"], "inv_id": inv_pick["id"]}


@setup("files_download")
def _files_download(world, rng, params, slots):
    kind = params.get("download_kind") or rng.choice(["Booking confirmation", "Boarding pass", "Receipt", "E-ticket"])
    name = f"{kind} {pools.code(rng, 5)}.pdf"
    items = world.state["files"]["items"]
    fid = f"f{len(items) + len(world.state['files']['trash']) + 1}"
    items.append({"id": fid, "name": name, "folder": "Downloads", "kind": "PDF document",
                  "size_kb": rng.randint(60, 900), "modified": f"{world.clock[:10]}T{rng.randint(7, 8):02d}:{rng.randint(10, 59):02d}"})
    return {"dl_name": name, "dl_id": fid, "dl_kind": kind.lower()}


# ---------------------------------------------------------------- Calendar

@setup("calendar_pick")
def _calendar_pick(world, rng, params, slots):
    evs = world.state["calendar"]["events"]
    ev = next(e for e in evs if e["id"] == slots["tomorrow_event_id"])
    i0, i1 = TIMES.index(ev["start"]), TIMES.index(ev["end"])
    dur = max(1, i1 - i0)
    taken = {e["start"] for e in evs if e["date"] == ev["date"] and e["id"] != ev["id"]}
    opts = [t for t in TIMES if "09:00" <= t <= "16:00" and t != ev["start"] and t not in taken
            and TIMES.index(t) + dur < len(TIMES)]
    new = rng.choice(opts)
    loc = rng.choice([x for x in LOCATIONS if x != ev["location"]])
    return {"ev_id": ev["id"], "ev_title": ev["title"], "ev_start": ev["start"], "ev_new_start": new,
            "ev_new_end": TIMES[TIMES.index(new) + dur], "ev_new_location": loc,
            "ev_new_title": f"{ev['title']} (moved)"}


@setup("new_event")
def _new_event(world, rng, params, slots):
    today = date.fromisoformat(world.clock[:10])
    d = today + timedelta(days=rng.randint(3, 12))
    start = rng.choice([t for t in TIMES if "09:00" <= t <= "17:00"])
    end = TIMES[TIMES.index(start) + rng.choice([1, 2])]
    return {"nev_date": d.isoformat(), "nev_date_long": long_date(d), "nev_start": start, "nev_end": end}


def time_pattern(hm: str) -> str:
    """A regex for a clock time as people write it: 14:30, 14.30, 2:30 pm, 2.30pm; 09:00, 9am, 9:00."""
    h, m = int(hm[:2]), hm[3:5]
    h12 = h % 12 or 12
    ampm = "(am|a\\.m\\.)" if h < 12 else "(pm|p\\.m\\.)"
    alts = [f"\\b0?{h}[:.]{m}\\b" if h < 10 else f"\\b{h}[:.]{m}\\b", f"\\b{h12}[:.]{m}\\s*{ampm}"]
    if m == "00":
        alts.append(f"\\b{h12}\\s*{ampm}")
    return "(" + "|".join(alts) + ")"


@setup("time_regex")
def _time_regex(world, rng, params, slots):
    return {"tomorrow_event_time_rx": time_pattern(slots["tomorrow_event_time"])}


@setup("invite_end")
def _invite_end(world, rng, params, slots):
    """The end of an invited event `params.hours` after its start; a start too late for the calendar's last
    time (20:00) is moved earlier in the email itself, so the email and the slots agree."""
    hours = int(params.get("hours", 1))
    t = slots["invite_time"]
    latest = TIMES[len(TIMES) - 1 - 2 * hours]
    out = {}
    if t > latest:
        msg = next(m for m in world.state["mail"]["inbox"] if m["id"] == slots["invite_id"])
        msg["body"] = [p.replace(f" at {t},", f" at {latest},") for p in msg["body"]]
        out["invite_time"] = t = latest
    out["invite_end"] = TIMES[TIMES.index(t) + 2 * hours]
    return out


# ---------------------------------------------------------------- Mail

@setup("mail_address")
def _mail_address(world, rng, params, slots):
    """A friend's email with a new postal address, newest in the inbox."""
    p = pools.person(rng)
    while p["name"] == slots.get("my_name"):
        p = pools.person(rng)
    addr = pools.address(rng)
    t = world.clock[11:16]
    hh, mm = int(t[:2]), int(t[3:5])
    stamp = f"{max(0, hh - (1 if mm < 3 else 0)):02d}:{(mm - 3) % 60:02d}"
    me = slots.get("my_name", "there").split()[0]
    msg = {"id": "m90", "from_name": p["name"], "from_email": p["email"], "to": slots.get("my_email", ""),
           "subject": "We've moved!", "date": world.clock[:10], "time": stamp, "unread": True,
           "body": [f"Hi {me},", f"We finally moved last week. The new address is {addr['line1']}, {addr['town']} "
                    f"{addr['postcode']}.", "Come and visit soon!", p["first"]]}
    world.state["mail"]["inbox"].append(msg)
    return {"friend_name": p["name"], "friend_first": p["first"], "friend_line1": addr["line1"],
            "friend_town": addr["town"], "friend_postcode": addr["postcode"], "friend_msg_id": "m90"}


# ---------------------------------------------------------------- Browser sites

@setup("clinic_long_dob")
def _clinic_long_dob(world, rng, params, slots):
    d = date.fromisoformat(slots["patient_dob"])
    return {"patient_dob_long": f"{d.day} {MONTHS[d.month - 1]} {d.year}"}


@setup("clinic_align")
def _clinic_align(world, rng, params, slots):
    """The booking code's text comes from the clinic the site is for."""
    name = world.state["clinic"]["name"]
    for th in world.state["messages"]["threads"]:
        if th["id"] == slots.get("code_thread_id"):
            th["contact"] = name
    return {"code_sender": name}


@setup("clinic_prefill")
def _clinic_prefill(world, rng, params, slots):
    """The user already filled in the form (unsubmitted UI state); only the code is missing."""
    vals = {"name": slots["patient_name"], "dob": slots["patient_dob"], "phone": slots["patient_phone"],
            "reason": params.get("reason", "General check-up"), "day": params.get("day", "Thursday"),
            "time": params.get("time_of_day", "Morning")}
    for k, v in vals.items():
        world.set_field(f"clinic.request.{k}", v)
    return {}


@setup("travel_long_date")
def _travel_long_date(world, rng, params, slots):
    return {"travel_date_long": long_date(date.fromisoformat(slots["travel_date"]))}


@setup("browser_open")
def _browser_open(world, rng, params, slots):
    """The Browser already shows a page (behind the start app)."""
    url = fill(params["browser_url"], slots)
    world.nav["stacks"]["browser"] = [{"screen": "page", "params": {"url": url}, "offset": 0}]
    return {}


@scenario("flow_setup")
def flow_setup(world, rng: random.Random, params: dict, case: dict) -> dict:
    return run_setup(world, rng, params)
