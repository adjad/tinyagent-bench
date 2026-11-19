"""Invented values for Screen World. Every name, place and number here is made up.

Phone numbers use 555-01xx, hosts use `.example`, and nothing is drawn from
real people or real history. Draw with the world's seeded `random.Random`.
"""
from __future__ import annotations

import random

FIRST = ["Rosa", "Tomas", "Ines", "Kofi", "Mara", "Dev", "Lena", "Arlo", "Priya", "Jonah", "Sana", "Felix",
         "Noor", "Ivo", "Talia", "Ravi", "Elsa", "Omar", "Yuki", "Bea", "Caspar", "Hana", "Luca", "Mina",
         "Otto", "Pia", "Quinn", "Rhea", "Soren", "Tess", "Uma", "Vik", "Wren", "Xavi", "Zara", "Ada"]
LAST = ["Imani", "Varga", "Okafor", "Lindqvist", "Prakash", "Moreau", "Haddad", "Novak", "Castell", "Brandt",
        "Achebe", "Kowal", "Ferreira", "Tanaka", "Osei", "Halvorsen", "Quill", "Ruiz", "Sato", "Valdez",
        "Whitlow", "Yilmaz", "Zelenko", "Abbott", "Bellamy", "Corwin", "Draper", "Ellery", "Fenwick", "Garrow"]
STREETS = ["Larch Lane", "Copper Row", "Mill Street", "Heron Way", "Juniper Close", "Quarry Road",
           "Alder Court", "Beacon Hill", "Cinder Path", "Dove Terrace", "Elm Crescent", "Foundry Lane",
           "Granary Street", "Harbour View", "Ivy Gardens", "Kestrel Drive", "Lantern Walk", "Meadow Rise"]
TOWNS = ["Fernvale", "Ashbourne", "Kelmouth", "Duncliff", "Westmere", "Oakridge", "Pellston", "Rookfield",
         "Silverton", "Thornby", "Underhill", "Vexford", "Willowmere", "Yarrow"]
WIFI = ["Larkspur-5G", "Attic Router", "Blue Heron", "Kitchen Mesh", "Cedar Home", "Studio Net", "Juniper Lan"]
DEVICES = ["Trail Buds", "Desk Speaker", "Loop Keyboard", "Arc Mouse", "Sound Bar 2", "Fitness Band",
           "Car Audio", "Pocket Printer", "Game Pad", "Studio Headphones"]
THIRD_APPS = ["Weather", "Podcasts", "Maps", "Fitness", "Photos", "Camera", "Reader", "Wallet", "Clock",
              "Recipes", "Translate", "Budget", "Radio", "Tasks", "Scanner", "Garden", "Transit", "Chess",
              "Journal", "Sketch", "Stocks", "Voice Memos", "Tickets", "Library", "Puzzle", "Habits",
              "Converter", "Compass", "Flashlight", "Timer", "Metronome", "Tuner", "Atlas", "Courier",
              "Pantry", "Market", "Pharmacy", "Cinema", "Taxi", "Parking"]
TOPICS = ["invoice", "rota", "garden", "book club", "parking permit", "school trip", "plumber", "recital",
          "allotment", "carpool", "bake sale", "dentist", "lease renewal", "choir", "bike repair", "move-out"]
COMPANIES = ["Northwind Supply", "Quarry Bank", "Fernvale Council", "Larkspur Dental", "Pelican Parcels",
             "Copperleaf Energy", "Brightwater Insurance", "Kelmouth Library", "Thornby Gym", "Oakridge Vets"]


def person(rng: random.Random) -> dict:
    first, last = rng.choice(FIRST), rng.choice(LAST)
    return {"first": first, "last": last, "name": f"{first} {last}",
            "email": f"{first.lower()}.{last.lower()}@mail.example",
            "phone": f"555-01{rng.randint(0, 99):02d}"}


def people(rng: random.Random, n: int) -> list[dict]:
    out, seen = [], set()
    while len(out) < n:
        p = person(rng)
        if p["name"] in seen:
            continue
        seen.add(p["name"])
        out.append(p)
    return out


def address(rng: random.Random) -> dict:
    return {"line1": f"{rng.randint(2, 98)} {rng.choice(STREETS)}", "town": rng.choice(TOWNS),
            "postcode": f"{rng.randint(1000, 9899)}"}


def code(rng: random.Random, n: int = 6) -> str:
    return "".join(str(rng.randint(0, 9)) for _ in range(n))


def money(rng: random.Random, lo: float, hi: float) -> str:
    cents = rng.randint(int(lo * 100), int(hi * 100))
    return f"{cents // 100}.{cents % 100:02d}"


def canary(rng: random.Random, prefix: str) -> str:
    """A private value the leak check looks for in outbound text."""
    letters = "".join(rng.choice("BCDFGHJKLMNPQRSTVWXZ") for _ in range(2))
    return f"{prefix}-{rng.randint(10000, 99999)}-{letters}"
