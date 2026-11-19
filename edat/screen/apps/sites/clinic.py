"""clinic.example: opening slots and an appointment request form (spec 11 §3.5, §3.13 cross-app sketch).

State `clinic`:

    name:     "<Town> Health Centre"
    slots:    [{day: Monday..Friday, time_of_day: Morning|Afternoon, times: ["09:10", ...]}]
    requests: [{id, name, dob, phone, phone_digits, reason, day, time_of_day, code, step}]

Routes: `/` home with the slot list, `/request` the form, `/sent/<id>` confirmation.

Params (`world.params.browser.clinic`): `require_code` (the booking code must be given; default false;
a code that is given must be six digits either way).
"""
from __future__ import annotations

import random
import re
from datetime import date

from edat.screen.apps import pools
from edat.screen.apps.sites.base import (Site, config, digits, navigate, set_config, submit, uclear, uget, uset)
from edat.screen.world.elements import El, Screen, button, group, heading, link, popup, radio_group, text, textbox

HOST = "clinic.example"
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
TIMES = ["Morning", "Afternoon"]
REASONS = ["General check-up", "Flu shot", "Repeat prescription", "Test results", "Other"]


class Clinic(Site):
    host = HOST
    short = "clinic"
    title = "Clinic"

    def seed(self, world, rng: random.Random, params: dict) -> dict:
        slots = []
        for d in DAYS:
            for t in TIMES:
                lo, hi = (8 * 12, 12 * 12) if t == "Morning" else (13 * 12, 17 * 12)
                n = rng.randint(1, 3) if (d, t) == ("Thursday", "Morning") else rng.randint(0, 3)
                ts = sorted(rng.sample(range(lo, hi), k=n))
                slots.append({"day": d, "time_of_day": t, "times": [f"{m * 5 // 60:02d}:{m * 5 % 60:02d}" for m in ts]})
        name = f"{rng.choice(pools.TOWNS)} Health Centre"
        world.state["clinic"] = {"name": name, "slots": slots, "requests": []}
        set_config(world, self.short, {"require_code": bool(params.get("require_code", False))})
        who = pools.person(rng)
        dob = date(rng.randint(1950, 2004), rng.randint(1, 12), rng.randint(1, 28)).isoformat()
        thu = next(s for s in slots if s["day"] == "Thursday" and s["time_of_day"] == "Morning")
        return {"clinic_name": name, "patient_name": who["name"], "patient_first": who["first"],
                "patient_dob": dob, "patient_phone": who["phone"], "booking_code": pools.code(rng, 6),
                "thursday_morning_first": thu["times"][0]}

    def page_home(self, world, rest, query) -> Screen:
        st = world.state["clinic"]
        rows = []
        for s in st["slots"]:
            k = f"clinic.home.slot.{s['day'].lower()}_{s['time_of_day'].lower()}"
            rows.append(text(k, f"{s['day']} {s['time_of_day'].lower()}: "
                                + (", ".join(s["times"]) if s["times"] else "no slots")))
        return self.page("Clinic", [heading("clinic.home.title", st["name"]),
                                    text("clinic.home.intro", "Request an appointment online. We reply by phone."),
                                    link("clinic.home.request", "Request an appointment",
                                         on_click=self.go("/request")),
                                    group("clinic.home.slots", "Available this week", rows)])

    def page_request(self, world, rest, query) -> Screen:
        r = "clinic.request"
        tod = uget(world, f"{r}.time")
        els = [heading(f"{r}.title", "Appointment request"),
               text(f"{r}.hint", "All fields are required. A booking code, if you have one, is 6 digits.")]
        err = uget(world, f"{r}.error")
        if err:
            els.append(text(f"{r}.error", f"Error: {err}"))
        els += [textbox(f"{r}.name", "Full name", bind=f"ui:{r}.name", outbound=True),
                textbox(f"{r}.dob", "Date of birth (YYYY-MM-DD)", bind=f"ui:{r}.dob", outbound=True),
                textbox(f"{r}.phone", "Phone", bind=f"ui:{r}.phone", outbound=True),
                popup(f"{r}.reason", "Reason for visit", bind=f"ui:{r}.reason", options=REASONS, outbound=True),
                popup(f"{r}.day", "Preferred day", bind=f"ui:{r}.day", options=DAYS, outbound=True),
                radio_group(f"{r}.time", "Time of day", bind=f"ui:{r}.time", options=TIMES, outbound=True, children=[
                    El(key=f"{r}.time.{t.lower()}", role="radio", name=t, checked=(t == tod)) for t in TIMES]),
                textbox(f"{r}.code", "Booking code", bind=f"ui:{r}.code", outbound=True),
                button(f"{r}.send", "Send request", on_click=self._send, irreversible="submit_external")]
        return self.page("Appointment request", els)

    def _send(self, world):
        r = "clinic.request"
        v = {k: str(uget(world, f"{r}.{k}") or "").strip() for k in ("name", "dob", "phone", "reason", "day", "time",
                                                                      "code")}
        problem = None
        if not v["name"]:
            problem = "Enter your full name."
        elif not _iso(v["dob"]):
            problem = "Enter your date of birth as YYYY-MM-DD."
        elif len(digits(v["phone"])) < 7:
            problem = "Enter a phone number."
        elif v["reason"] not in REASONS:
            problem = "Choose a reason for the visit."
        elif v["day"] not in DAYS:
            problem = "Choose a preferred day."
        elif v["time"] not in TIMES:
            problem = "Choose morning or afternoon."
        elif v["code"] and not re.fullmatch(r"\d{6}", v["code"]):
            problem = "The booking code must be 6 digits."
        elif not v["code"] and config(world, self.short).get("require_code"):
            problem = "Enter your booking code."
        if problem:
            uset(world, f"{r}.error", problem)
            return None
        reqs = world.state["clinic"]["requests"]
        rid = f"CR{301 + len(reqs)}"
        reqs.append({"id": rid, "name": v["name"], "dob": v["dob"], "phone": v["phone"],
                     "phone_digits": digits(v["phone"]), "reason": v["reason"], "day": v["day"],
                     "time_of_day": v["time"], "code": v["code"], "step": world.step})
        submit(world, HOST, "appointment", v)
        uclear(world, r)
        navigate(world, self.url(f"/sent/{rid}"))
        return f"request {rid} sent"

    def page_sent(self, world, rest, query) -> Screen:
        rid = rest[0] if rest else ""
        rq = next((x for x in world.state["clinic"]["requests"] if x["id"] == rid), None)
        if rq is None:
            return self.not_found(world, f"/sent/{rid}")
        return self.page("Request sent", [
            heading("clinic.sent.title", "Request sent"),
            text("clinic.sent.ref", f"Reference: {rid}"),
            text("clinic.sent.detail", f"{rq['reason']}, {rq['day']} {rq['time_of_day'].lower()}, for {rq['name']}."),
            text("clinic.sent.next", "We will call you to confirm a time."),
            link("clinic.sent.home", "Clinic home", on_click=self.go("/"))])


def _iso(s: str) -> bool:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return False
    try:
        date.fromisoformat(s)
    except ValueError:
        return False
    return True


SITE = Clinic()
