"""The small fictional world every quality case is drawn from (spec 03 s7.1).

Everything here is invented. The persona is always Test User
(test.user@example.com); organisations use `.example` domains and phone
numbers use the 555 range. Surnames are constructed so they are unlikely to
name a real person. No text is copied from Harbor's benches (tilt T4).
"""
from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass, field

PERSONA = {"name": "Test User", "email": "test.user@example.com", "first": "Test"}

FIRST_NAMES = [
    "Ines", "Tomas", "Aiko", "Rafael", "Mirela", "Oskar", "Leni", "Darius", "Saskia", "Joran",
    "Pilar", "Emeka", "Ylva", "Matteo", "Noor", "Kasimir", "Halina", "Bertil", "Odile", "Faro",
    "Greta", "Ilya", "Corin", "Maren", "Tobin", "Anouk", "Severin", "Liesel", "Quentin", "Rhea",
    "Benedek", "Zofia", "Arvid", "Celeste", "Dorian", "Elodie", "Florin", "Hedda", "Ivo", "Juno",
]
SURNAMES = [
    "Quillmore", "Varnstead", "Okonkwi", "Brellan", "Tasmund", "Corvell", "Pennick", "Dorrance",
    "Halvey", "Strunden", "Melcombe", "Avenzo", "Rothery", "Kilbane", "Fennimore", "Ashgrove",
    "Belmondi", "Carrowe", "Dunleavy", "Esterby", "Falkwright", "Garlund", "Hestermann", "Ivanek",
    "Jorvane", "Kesterton", "Lammergeir", "Morrowind", "Nettlefold", "Ovington", "Pellegrue",
    "Quarrender", "Rusheby", "Selvanne", "Thorncastle", "Umberley", "Vellacott", "Wexmoor",
]
RELATIONS = ["sister", "brother", "cousin", "neighbour", "colleague", "flatmate", "aunt", "friend"]
CITIES = ["Lisbon", "Tallinn", "Ghent", "Bergen", "Lyon", "Graz", "Porto", "Turku", "Utrecht", "Ljubljana",
          "Aarhus", "Bilbao", "Kraków", "Bologna", "Galway", "Malmö"]
PLACES = [
    "Juniper Lane Clinic", "Northgate Library", "Cobalt Café", "Riverside Hall", "Pinewood Dental",
    "Saltmarsh Pharmacy", "Hollow Oak Pub", "Larkspur Studio", "Ferngate Station", "Bramble Market",
    "Kestrel Gym", "Old Mill Bakery", "Tidewater Pier", "Copperleaf Hotel", "Wrenfield School",
    "Lantern Square", "Mossbank Garage", "Quayside Theatre", "Thistle Community Centre", "Marigold Bistro",
]
ORGS = [
    ("Brightwell Energy", "brightwell-energy.example"), ("Tessaro Water", "tessaro-water.example"),
    ("Kitewing Broadband", "kitewing.example"), ("Hollis & Grey Lettings", "hollisgrey.example"),
    ("Orrin Insurance", "orrin-insurance.example"), ("Petrel Ferries", "petrel-ferries.example"),
    ("Calder Dental", "calder-dental.example"), ("Nimbus Mobile", "nimbus-mobile.example"),
    ("Greenhollow Council", "greenhollow.example"), ("Sablewood Gym", "sablewood.example"),
    ("Arden Rail", "arden-rail.example"), ("Lumen Books", "lumen-books.example"),
    ("Marlow Veterinary", "marlow-vet.example"), ("Quarry Lane Garage", "quarrylane.example"),
]
NEWSLETTERS = [
    ("Weekend Picks", "news@weekendpicks.example"), ("Garden Notes", "hello@gardennotes.example"),
    ("City Deals", "deals@citydeals.example"), ("Book Club Digest", "digest@bookclub.example"),
    ("Running Weekly", "team@runningweekly.example"),
]
CATEGORIES = ["Groceries", "Energy", "Transport", "Eating out", "Household", "Health", "Phone", "Water",
              "Insurance", "Gifts", "Hobbies", "Rent"]
ITEMS_TO_BRING = ["lemon tart", "folding chairs", "fruit salad", "spare cables", "the board game",
                  "a picnic blanket", "the speaker", "paper lanterns", "two baguettes", "the cake stand"]
EVENT_TITLES = ["Dentist", "Choir practice", "Team sync", "Book club",
                "Parent evening", "Haircut", "Physio", "Running club", "Design critique", "Car service",
                "Language class", "Lunch with {p}", "Coffee with {p}", "Call with {p}", "Pottery class",
                "Volunteer shift", "Piano lesson", "1:1 with {p}"]


@dataclass
class World:
    """One case's world: a few contacts, organisations, places and a date window."""

    rng: random.Random
    year: int
    today: dt.date
    contacts: list[dict] = field(default_factory=list)
    used_codes: set = field(default_factory=set)
    used_amounts: set = field(default_factory=set)
    used_names: set = field(default_factory=set)
    used_dates: set = field(default_factory=set)

    @classmethod
    def make(cls, seed: int, *, year: int = 2027, n_contacts: int = 10) -> "World":
        rng = random.Random(seed)
        start = dt.date(year, 1, 1) + dt.timedelta(days=rng.randrange(20, 320))
        w = cls(rng=rng, year=year, today=start)
        firsts = rng.sample(FIRST_NAMES, n_contacts)
        lasts = rng.sample(SURNAMES, n_contacts)
        for f, l in zip(firsts, lasts):
            w.contacts.append({"first": f, "last": l, "name": f"{f} {l}",
                               "email": f"{f.lower()}.{l.lower()}@mail.example",
                               "relation": rng.choice(RELATIONS)})
        return w

    # unique invented atoms -------------------------------------------------
    def code(self, prefix: str | None = None, digits: int | None = None) -> str:
        while True:
            p = prefix or "".join(self.rng.choice("ABCDEFGHJKLMNPQRSTUVWXZ") for _ in range(self.rng.choice([2, 3])))
            d = digits or self.rng.choice([4, 5])
            num = self.rng.randrange(10 ** (d - 1) + 1, 10 ** d - 1)
            c = f"{p}-{num}"
            if c not in self.used_codes and not str(num).endswith("00"):
                self.used_codes.add(c)
                return c

    def amount(self, lo: float = 12.0, hi: float = 2400.0) -> str:
        while True:
            cents = self.rng.choice([5, 15, 20, 35, 40, 45, 55, 60, 65, 70, 75, 80, 85, 95, 10, 30])
            whole = self.rng.randrange(int(lo), int(hi))
            if whole % 10 == 0:
                whole += self.rng.choice([1, 3, 7])
            s = f"{whole}.{cents:02d}"
            if s not in self.used_amounts:
                self.used_amounts.add(s)
                return s

    def person(self, *, exclude: list[str] = ()) -> dict:
        pool = [c for c in self.contacts if c["name"] not in exclude]
        return self.rng.choice(pool)

    def place(self) -> str:
        return self.rng.choice(PLACES)

    def org(self) -> tuple[str, str]:
        return self.rng.choice(ORGS)

    def date_after(self, lo: int, hi: int, *, avoid_round: bool = True) -> dt.date:
        """A date lo..hi days after today, never one already used in this case (so trap
        anchors cannot collide with fact anchors)."""
        for attempt in range(400):
            d = self.today + dt.timedelta(days=self.rng.randrange(lo, hi + attempt // 40))
            if (not avoid_round or d.day not in (1, 10, 15, 20, 30)) and d not in self.used_dates:
                self.used_dates.add(d)
                return d
        raise RuntimeError("no free date")

    def reserve(self, d: dt.date) -> dt.date:
        self.used_dates.add(d)
        return d

    def time(self, lo_h: int = 8, hi_h: int = 20, *, quarter: bool = True) -> str:
        h = self.rng.randrange(lo_h, hi_h)
        m = self.rng.choice([0, 15, 30, 45] if quarter else [5, 10, 20, 25, 35, 40, 50, 55])
        return f"{h:02d}:{m:02d}"


def fmt_amount(a: str, currency: str = "EUR") -> str:
    whole, cents = a.split(".")
    return f"{int(whole):,}.{cents} {currency}"


def long_date(d: dt.date) -> str:
    return f"{d.strftime('%A')} {d.day} {d.strftime('%B')}"


def short_date(d: dt.date) -> str:
    return f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"


def us_date(d: dt.date) -> str:
    return f"{d.strftime('%B')} {d.day}"


def spoken_time(t: str) -> str:
    """'16:30' -> '4:30 pm', '09:00' -> '9 am'."""
    h, m = int(t[:2]), int(t[3:])
    suffix = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    return f"{h12}{':' + format(m, '02d') if m else ''} {suffix}"
