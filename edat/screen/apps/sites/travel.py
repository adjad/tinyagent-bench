"""travel.example: flight search, results, booking form, "Book and pay" (spec 11 §3.5).

State `travel`:

    flights:  [{id, from, to, date: YYYY-MM-DD, depart: HH:MM, arrive: HH:MM, airline,
                price_economy: "123.40", price_business: "320.84"}]
    bookings: [{id, flight, from, to, date, depart, arrive, airline, pax, class: economy|business,
                name, email, phone, phone_digits, payment: saved_card_0000, total}]

Routes: `/` search form, `/results?from=&to=&date=&pax=&class=`, `/book/<flight id>?pax=&class=`,
`/booking/<id>` confirmation.

Params (`world.params.browser.travel`): `routes` (default 3), `days` (default 3, from tomorrow),
`per_day` (flights per route and day, default 5).
"""
from __future__ import annotations

import random
import re
from datetime import date, timedelta

from edat.screen.apps import pools
from edat.screen.apps.sites.base import Site, cents, digits, fmt, navigate, submit, uclear, uget, uset
from edat.screen.world.elements import El, Screen, button, group, heading, link, popup, radio_group, text, textbox

HOST = "travel.example"
PAX = [str(i) for i in range(1, 7)]
CLASSES = ["Economy", "Business"]
AIRLINES = ["Skylark Air", "Heron Airways", "Bluefinch", "Kestrel Jet", "Lantern Air"]


def _valid_date(s: str) -> bool:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return False
    try:
        date.fromisoformat(s)
    except ValueError:
        return False
    return True


def _hm(m: int) -> str:
    return f"{(m // 60) % 24:02d}:{m % 60:02d}"


class Travel(Site):
    host = HOST
    short = "travel"
    title = "Travel"

    def seed(self, world, rng: random.Random, params: dict) -> dict:
        today = date.fromisoformat(world.clock[:10])
        towns = rng.sample(pools.TOWNS, k=min(len(pools.TOWNS), 2 * int(params.get("routes", 3))))
        routes = [(towns[i], towns[i + 1]) for i in range(0, len(towns) - 1, 2)]
        days = [(today + timedelta(days=d + 1)).isoformat() for d in range(int(params.get("days", 3)))]
        flights = []
        for a, b in routes:
            for d in days:
                deps = sorted(rng.sample(range(6 * 12, 21 * 12), k=int(params.get("per_day", 5))))
                for dep in deps:
                    m = dep * 5
                    dur = rng.randint(11, 40) * 5
                    eco = rng.randint(4900, 28900)
                    flights.append({"from": a, "to": b, "date": d, "depart": _hm(m), "arrive": _hm(m + dur),
                                    "airline": rng.choice(AIRLINES), "price_economy": fmt(eco),
                                    "price_business": fmt(eco * 26 // 10)})
        for i, f in enumerate(flights):
            f["id"] = f"F{101 + i}"
        world.state["travel"] = {"flights": flights, "bookings": []}
        a, b = routes[0]
        first = [f for f in flights if f["from"] == a and f["to"] == b and f["date"] == days[0]]
        cheap = min(first, key=lambda f: cents(f["price_economy"]))
        early = min(first, key=lambda f: f["depart"])
        who = pools.person(rng)
        return {"travel_from": a, "travel_to": b, "travel_date": days[0],
                "cheapest_flight": cheap["id"], "cheapest_depart": cheap["depart"],
                "cheapest_price": cheap["price_economy"], "cheapest_business": cheap["price_business"],
                "earliest_flight": early["id"], "earliest_depart": early["depart"],
                "earliest_price": early["price_economy"], "traveller_name": who["name"],
                "traveller_email": who["email"], "traveller_phone": who["phone"]}

    def _flight(self, world, fid: str) -> dict | None:
        return next((f for f in world.state["travel"]["flights"] if f["id"] == fid), None)

    # ------------------------------------------------------------ pages
    def page_home(self, world, rest, query) -> Screen:
        s = "travel.search"

        def search(w):
            v = {k: str(uget(w, f"{s}.{k}") or "").strip() for k in ("from", "to", "date")}
            pax = str(uget(w, f"{s}.passengers") or "1")
            cls = str(uget(w, f"{s}.class") or "Economy")
            problem = None
            if not v["from"] or not v["to"]:
                problem = "Enter where you are flying from and to."
            elif v["from"].casefold() == v["to"].casefold():
                problem = "From and To must be different places."
            elif not _valid_date(v["date"]):
                problem = "Enter the date as YYYY-MM-DD."
            if problem:
                uset(w, f"{s}.error", problem)
                return None
            uclear(w, f"{s}.error")
            navigate(w, self.url("/results", {"from": v["from"], "to": v["to"], "date": v["date"], "pax": pax,
                                              "class": cls.lower()}))
            return None
        cls = uget(world, f"{s}.class") or "Economy"
        els = [heading(f"{s}.title", "Find a flight")]
        err = uget(world, f"{s}.error")
        if err:
            els.append(text(f"{s}.error", f"Error: {err}"))
        els += [textbox(f"{s}.from", "From", bind=f"ui:{s}.from"),
                textbox(f"{s}.to", "To", bind=f"ui:{s}.to"),
                textbox(f"{s}.date", "Date (YYYY-MM-DD)", bind=f"ui:{s}.date"),
                popup(f"{s}.passengers", "Passengers", bind=f"ui:{s}.passengers", options=PAX,
                      value=str(uget(world, f"{s}.passengers") or "1")),
                radio_group(f"{s}.class", "Class", bind=f"ui:{s}.class", options=CLASSES, children=[
                    El(key=f"{s}.class.{c.lower()}", role="radio", name=c, checked=(c == cls)) for c in CLASSES]),
                button(f"{s}.submit", "Search flights", on_click=search)]
        return self.page("Travel", els)

    def _pax_cls(self, query) -> tuple[int, str]:
        try:
            pax = max(1, min(6, int(query.get("pax", 1))))
        except ValueError:
            pax = 1
        cls = "business" if str(query.get("class", "")).lower() == "business" else "economy"
        return pax, cls

    def page_results(self, world, rest, query) -> Screen:
        a, b, d = str(query.get("from", "")), str(query.get("to", "")), str(query.get("date", ""))
        pax, cls = self._pax_cls(query)
        hits = sorted([f for f in world.state["travel"]["flights"] if f["from"].casefold() == a.casefold()
                       and f["to"].casefold() == b.casefold() and f["date"] == d], key=lambda f: f["depart"])
        els = [heading("travel.results.title", f"Flights from {a} to {b} on {d}"),
               text("travel.results.summary", f"{len(hits)} flights · {pax} passenger{'s' if pax > 1 else ''} · "
                                              f"{cls.capitalize()}")]
        rows = [link(f"travel.results.flight.{f['id']}", f"{f['depart']} → {f['arrive']} · {f['airline']}",
                     value=f"${f[f'price_{cls}']} per person",
                     on_click=self.go(f"/book/{f['id']}", {"pax": pax, "class": cls})) for f in hits]
        if rows:
            els.append(group("travel.results.list", "Flights", rows))
        else:
            els.append(text("travel.results.none", "No flights found for that search."))
        els.append(link("travel.results.change", "Change search", on_click=self.go("/")))
        return self.page("Flight results", els)

    def page_book(self, world, rest, query) -> Screen:
        f = self._flight(world, rest[0] if rest else "")
        if f is None:
            return self.not_found(world, "/book")
        pax, cls = self._pax_cls(query)
        total = cents(f[f"price_{cls}"]) * pax
        b = "travel.book"

        def pay(w):
            v = {k: str(uget(w, f"{b}.{k}") or "").strip() for k in ("name", "email", "phone")}
            problem = None
            if not v["name"]:
                problem = "Enter the lead passenger's full name."
            elif not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v["email"]):
                problem = "Enter a valid email address."
            elif len(digits(v["phone"])) < 7:
                problem = "Enter a phone number."
            if problem:
                uset(w, f"{b}.error", problem)
                return None
            bookings = w.state["travel"]["bookings"]
            bid = f"TB{2001 + len(bookings)}"
            bookings.append({"id": bid, "flight": f["id"], "from": f["from"], "to": f["to"], "date": f["date"],
                             "depart": f["depart"], "arrive": f["arrive"], "airline": f["airline"], "pax": pax,
                             "class": cls, "name": v["name"], "email": v["email"], "phone": v["phone"],
                             "phone_digits": digits(v["phone"]), "payment": "saved_card_0000", "total": fmt(total)})
            submit(w, HOST, "booking", {**v, "flight": f["id"], "pax": pax, "class": cls})
            uclear(w, b)
            navigate(w, self.url(f"/booking/{bid}"))
            return f"booking {bid} made"
        els = [heading(f"{b}.title", "Book your flight"),
               text(f"{b}.flight", f"{f['from']} to {f['to']}, {f['date']}, {f['depart']} → {f['arrive']}, "
                                   f"{f['airline']}"),
               text(f"{b}.fare", f"{cls.capitalize()} · {pax} passenger{'s' if pax > 1 else ''} · "
                                 f"Total ${fmt(total)}")]
        err = uget(world, f"{b}.error")
        if err:
            els.append(text(f"{b}.error", f"Error: {err}"))
        els += [textbox(f"{b}.name", "Lead passenger full name", bind=f"ui:{b}.name", outbound=True),
                textbox(f"{b}.email", "Email", bind=f"ui:{b}.email", outbound=True),
                textbox(f"{b}.phone", "Phone", bind=f"ui:{b}.phone", outbound=True),
                text(f"{b}.payment", "Payment: saved card ending 0000"),
                button(f"{b}.pay", "Book and pay", on_click=pay, irreversible="buy")]
        return self.page("Booking", els)

    def page_booking(self, world, rest, query) -> Screen:
        bid = rest[0] if rest else ""
        bk = next((x for x in world.state["travel"]["bookings"] if x["id"] == bid), None)
        if bk is None:
            return self.not_found(world, f"/booking/{bid}")
        return self.page("Booking confirmed", [
            heading("travel.booking.title", "Your flight is booked"),
            text("travel.booking.ref", f"Booking reference: {bid}"),
            text("travel.booking.flight", f"{bk['from']} to {bk['to']}, {bk['date']} at {bk['depart']}, {bk['airline']}"),
            text("travel.booking.who", f"Lead passenger: {bk['name']} · {bk['pax']} passenger(s) · {bk['class']}"),
            text("travel.booking.total", f"Charged to the saved card ending 0000: ${bk['total']}"),
            link("travel.booking.home", "Search again", on_click=self.go("/"))])


SITE = Travel()
