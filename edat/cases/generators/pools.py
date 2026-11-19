"""Invented value pools for case generators. Everything here is fictional.

Phones are +1 555 01xx (reserved for fiction), addresses and URLs use
reserved domains (example.com, example.org, .test, .invalid). Names avoid the
assistant lane's canonical world (Toolbench's "Test User" world already holds
Mom, Dad, Sam Rivera, Alex Kim, Priya Shah and jules; Dr. Patel; Lakeside
Auto; Chase; Nopa; Oakland), so a seeded contact never collides with one of
those unless a case means it to.

Generators draw from these with their own `random.Random`, so the dev split
(public seed) and the held-out split (private seed) get different values from
the same templates.
"""
from __future__ import annotations

import random

FIRST = ["Ines", "Tomas", "Wren", "Kofi", "Mei", "Rafael", "Leila", "Oskar", "Nadia", "Jonah", "Aiko", "Bruno",
         "Selin", "Mateo", "Hana", "Idris", "Clara", "Dmitri", "Farah", "Gideon", "Ingrid", "Joaquin", "Kalani",
         "Lior", "Marisol", "Niko", "Odette", "Pavel", "Quinn", "Rosa", "Soren", "Talia", "Umar", "Vera",
         "Wendell", "Ximena", "Yusuf", "Zara", "Anouk", "Bastian", "Cyrus", "Delphine", "Emeka", "Fiona",
         "Gustavo", "Hollis", "Ilse", "Jasper", "Kirra", "Lucan", "Maeve", "Nuno", "Orla", "Priyanka", "Ronan",
         "Saoirse", "Teodor", "Uma", "Viggo", "Willa"]
LAST = ["Duarte", "Okafor", "Lindqvist", "Mensah", "Tanaka", "Ferreira", "Haddad", "Novak", "Petrov", "Achebe",
        "Moreau", "Castillo", "Byrne", "Kowalski", "Nakamura", "Oyelaran", "Varga", "Whitlock", "Yilmaz",
        "Zeller", "Abernathy", "Brightwater", "Calloway", "Dunmore", "Ellery", "Fairbanks", "Galloway",
        "Hartwell", "Ishikawa", "Jessup", "Kerrigan", "Lacroix", "Marchetti", "Northcott", "Ostrander",
        "Pemberton", "Quintero", "Rasmussen", "Sorensen", "Thackeray", "Underhill", "Valdivia", "Wexford"]
CITIES = ["Porto", "Lisbon", "Oslo", "Nairobi", "Madrid", "Lyon", "Kraków", "Montreal", "Hobart", "Valparaíso",
          "Tallinn", "Accra", "Kyoto", "Bergen", "Cork", "Graz", "Tbilisi", "Ljubljana", "Halifax", "Dunedin",
          "Cusco", "Malmö", "Utrecht", "Zadar", "Antwerp", "Seville", "Turku", "Galway", "Bologna", "Leipzig"]
PLACES = ["Lantern Bistro", "Copperleaf Café", "Harbourside Gym", "Northgate Library", "Willow Street Clinic",
          "Brightline Supplies", "Oak Street Lettings", "Hartwell Joinery", "Skyline Air", "Tidewater Dental",
          "Juniper Hall", "Riverside Climbing Wall", "Maple Row Garage", "Quayside Market", "Fernhill School"]
COMPANIES = ["Brightline Supplies", "Oak Street Lettings", "Hartwell Joinery", "Skyline Air", "Fernhill Energy",
             "Bluebarrow Insurance", "Kestrel Telecom", "Moorfield Water", "Arden Print Co", "Thistle Bank"]
TOPICS = ["the boiler service", "the school trip", "the quarterly report", "the bike repair", "the lease renewal",
          "the garden fence", "the book club", "the move to Thursday", "the conference badge", "the parking permit"]
ERRANDS = ["call the garage", "pay the gas bill", "pick up the dry cleaning", "renew the parking permit",
           "book the vet", "return the library books", "buy printer ink", "water the tomatoes", "post the parcel",
           "order new contact lenses", "collect the prescription", "book a haircut", "fix the bike light",
           "send the rent receipt", "check the smoke alarm", "defrost the freezer", "repot the fern"]


def phone(rng: random.Random, used: set | None = None) -> str:
    """A fictional +1 555 01xx number, unique within `used`."""
    used = used if used is not None else set()
    while True:
        tail = f"01{rng.randint(0, 99):02d}"
        if tail not in used:
            used.add(tail)
            return f"+1 555 {tail}"


def person(rng: random.Random, used_names: set | None = None) -> tuple[str, str]:
    used_names = used_names if used_names is not None else set()
    while True:
        first, last = rng.choice(FIRST), rng.choice(LAST)
        if first not in used_names:
            used_names.add(first)
            return first, last


def email(first: str, last: str, domain: str = "example.org") -> str:
    return f"{first.lower()}.{last.lower()}@{domain}".replace("ã", "a").replace("ö", "o").replace("ó", "o")


def contact(rng: random.Random, cid: str, used_names: set, used_phones: set, **extra) -> dict:
    first, last = person(rng, used_names)
    return {"id": cid, "name": f"{first} {last}", "phone": phone(rng, used_phones),
            "email": email(first, last), **extra}
