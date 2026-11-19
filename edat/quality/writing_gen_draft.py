"""Writing family generators for W1 (drafting), W2 (hard messages) and W6 (register).

Each generator is `gen(seed, *, index, profile) -> dict` (W6 returns a list of four
cases). A case comes from `writing.new_case`, carries a template `reference_draft`
that passes the WD checks, and leaves `writing.finish` to render the request.
Everything is invented. Profile-bound cases use the three synthetic profiles through
`writing`'s accessors; the ledger is never read.

How a case is built
-------------------
A *scenario* is a function `fn(c) -> spec`, where `c` is a `Ctx` (seeded random
numbers, an invented world, the sender, the recipient and typed values). The spec
holds the purpose, the points, the forbidden items and the paragraphs of the
reference draft. `_assemble` adds the greeting, sign-off, subject and limits, and
sets each limit from the reference draft so the draft always passes.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import math
import random
import re
from typing import Callable, NamedTuple

from edat.quality import normalize as N
from edat.quality import writing as W
from edat.quality.world import World, fmt_amount

GENERATOR = "writing_gen_draft/1"


# --------------------------------------------------------------------------
# Typed values
# --------------------------------------------------------------------------

class Val(NamedTuple):
    s: str          # how it is written in the request and the draft
    a: object       # the typed anchor


class Dt(NamedTuple):
    s: str
    a: dict
    d: object       # datetime.date


class Pers(NamedTuple):
    name: str
    first: str
    last: str


# --------------------------------------------------------------------------
# Recipients: categories of relation
# --------------------------------------------------------------------------

_CAT_RULES = [
    ("prof", re.compile(r"professor|instructor|supervisor|advisor|teaching assistant|\bTA\b", re.I)),
    ("boss", re.compile(r"^manager\b", re.I)),
    ("family", re.compile(r"mother|father|brother|sister|aunt|uncle|cousin|grandmother|stepfather|partner", re.I)),
    ("service", re.compile(r"landlord|director|recruiter|vendor|babysitter|neighbou?r", re.I)),
    ("friend", re.compile(r"friend|frisbee|running club|roommate|housemate|flatmate", re.I)),
    ("work", re.compile(r"colleague|report|peer|coworker|counterpart|coordinator|procurement|designer|product|intern"
                        r"|labmate|teammate|classmate|VP", re.I)),
]
_NEUTRAL_REL = {"prof": ["professor"], "boss": ["manager"], "work": ["colleague", "project lead"],
                "friend": ["friend", "flatmate"], "family": ["sister", "brother", "aunt", "cousin"],
                "service": ["landlord", "neighbour"]}


def cat_of(relation: str) -> str | None:
    for cat, rx in _CAT_RULES:
        if rx.search(relation or ""):
            return cat
    return None


_TITLES = ("Prof.", "Dr.", "Professor")


def _clean_name(name: str) -> str:
    n = re.sub(r"\s*\(.*?\)", "", name)
    n = re.split(r"\s+[–—-]\s+", n)[0]
    n = re.sub(r"[^\w\s.'À-ɏ-]", "", n)
    return re.sub(r"\s+", " ", n).strip()


def _greeting(rcp: dict, cat: str, formal_ta: bool = True) -> str:
    full = rcp["name"]
    bare = " ".join(t for t in full.split() if t not in _TITLES)
    first, last = bare.split()[0], bare.split()[-1]
    if cat == "prof":
        if re.search(r"teaching assistant|\bTA\b", rcp["relation"], re.I):
            return f"Dear {first},"
        title = "Dr." if full.startswith("Dr.") or "supervisor" in rcp["relation"] else "Professor"
        return f"Dear {title} {last},"
    if cat == "family" and rcp.get("short") and len(rcp["short"].split()) <= 2:
        return f"Hi {rcp['short']},"
    return f"Hi {first},"


# --------------------------------------------------------------------------
# Ctx: the random world of one case
# --------------------------------------------------------------------------

class Ctx:
    def __init__(self, seed: int, profile: str | None):
        self.seed = seed
        self.profile = profile
        self.rng = random.Random(seed)
        self.w = World.make(seed)
        if profile:
            self.w.today = W.profile_today(profile)
            self.sender = W.profile_sender(profile)
            self.others = list(self.w.contacts)
        else:
            me = self.w.contacts[0]
            self.sender = {"name": me["name"], "preferred_name": me["first"]}
            self.others = list(self.w.contacts[1:])
        self.rng.shuffle(self.others)
        self.today = self.w.today
        self.rcp: dict | None = None
        self.cat: str | None = None
        self._o = 0
        self.dollar = bool(profile)
        self._past: dict[int, Dt] = {}

    # recipient --------------------------------------------------------
    def eligible(self, cats) -> bool:
        if not cats or not self.profile:
            return True
        return any(cat_of(ct["relation"]) in cats for ct in W.contacts(self.profile))

    def set_recipient(self, cats) -> None:
        if self.profile:
            pool = [ct for ct in W.contacts(self.profile) if cat_of(ct["relation"]) in cats]
            ct = self.rng.choice(sorted(pool, key=lambda x: x["id"]))
            self.cat = cat_of(ct["relation"])
            full = ct.get("full_name") or ct["name"]
            self.rcp = {"name": _clean_name(full), "relation": ct["relation"], "short": _clean_name(ct["name"]),
                        "emails": ct.get("emails") or [], "id": ct["id"]}
        else:
            o = self.others.pop(0)
            self.cat = self.rng.choice([c for c in cats])
            self.rcp = {"name": o["name"], "relation": self.rng.choice(_NEUTRAL_REL[self.cat]), "short": o["first"],
                        "emails": [], "id": None}

    def person(self) -> Pers:
        o = self.others.pop(0)
        return Pers(o["name"], o["first"], o["last"])

    # typed values -----------------------------------------------------
    def d(self, lo: int, hi: int) -> Dt:
        d = self.w.date_after(lo, hi)
        return Dt(N.format_p7(d, self.today.year), W.date_anchor(d), d)

    def past(self, back: int) -> Dt:
        """The date `back` days before today (shifted a day or two when it falls on a round day)."""
        if back in self._past:
            return self._past[back]
        for k in range(8):
            d = self.today - dt.timedelta(days=back + k)
            if d.day not in (1, 10, 15, 20, 30) and d not in self.w.used_dates:
                self.w.used_dates.add(d)
                self._past[back] = Dt(N.format_p7(d, self.today.year), W.date_anchor(d), d)
                return self._past[back]
        raise RuntimeError("no free past date")

    def reserve_after(self, d: Dt, n: int) -> Dt:
        """A date n or more days after `d`, not used yet."""
        for k in range(12):
            nd = d.d + dt.timedelta(days=n + k)
            if nd not in self.w.used_dates and nd.day not in (1, 10, 15, 20, 30):
                self.w.used_dates.add(nd)
                return Dt(N.format_p7(nd, self.today.year), W.date_anchor(nd), nd)
        raise RuntimeError("no free date")

    def t(self, lo: int = 8, hi: int = 19) -> Val:
        t = self.w.time(lo, hi)
        return Val(t, W.time_anchor(t))

    def money(self, lo: float = 40, hi: float = 2400) -> Val:
        raw = self.w.amount(lo, hi)
        if self.dollar:
            whole, cents = raw.split(".")
            return Val(f"${int(whole):,}.{cents}", W.amount_anchor(raw))
        return Val(fmt_amount(raw), W.amount_anchor(raw))

    def fig(self, lo: int = 120, hi: int = 9800) -> Val:
        while True:
            n = self.rng.randrange(lo, hi)
            if n % 10 and n % 100 and ("n%d" % n) not in self.w.used_codes:
                self.w.used_codes.add("n%d" % n)
                return Val(f"{n:,}" if n >= 1000 else str(n), W.number_anchor(str(n)))

    def code(self, prefix: str | None = None) -> Val:
        c = self.w.code(prefix)
        return Val(c, {"type": "code", "value": c})

    def place(self) -> str:
        return self.w.place()

    def org(self) -> str:
        return self.w.org()[0]

    def pick(self, seq):
        return self.rng.choice(list(seq))


def P(text: str, *anchors, lead: bool = False) -> dict:
    return W.point("", text, list(anchors), lead=lead)


def F(text: str, *anchors, stated: bool = True) -> dict:
    return W.forbid("", text, list(anchors), stated=stated)


def S(purpose: str, points: list, paras: list, *, forbid: list | None = None, subject: str | None = None,
      **kw) -> dict:
    return {"purpose": purpose, "points": points, "forbid": forbid or [], "paras": paras, "subject": subject, **kw}


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------

def _round_up(x: float, step: int) -> int:
    return int(math.ceil(x / step) * step)


def _nwords(text: str) -> int:
    return len(text.split())


def _signoff(c: Ctx) -> str:
    if c.profile:
        return W.PROFILE_SIGNOFF[c.profile]
    name = c.sender["preferred_name"]
    if c.cat in ("friend", "family"):
        return f"{c.pick(['Cheers,', 'Talk soon,', 'Thanks,'])}\n{name}"
    if c.cat == "prof":
        return f"Kind regards,\n{name}"
    return f"{c.pick(['Best,', 'Thanks,', 'Kind regards,', 'Regards,'])}\n{name}"


def _number_ids(items: list[dict], prefix: str) -> list[dict]:
    for i, it in enumerate(items, 1):
        it["id"] = f"{prefix}{i}"
    return items


def _assemble(c: Ctx, sub: str, kind: str, spec: dict, *, seed: int | None = None, audience: str | None = None,
              group_id: str | None = None, greeting: str | None = None, signoff: str | None = None,
              case_id_seed: int | None = None) -> dict:
    form = spec.get("form", "email")
    surface = spec.get("surface")
    rcp = None if spec.get("no_rcp") else {"name": c.rcp["name"], "relation": c.rcp["relation"]}
    case = W.new_case(sub=sub, kind=kind, seed=seed if seed is not None else c.seed, generator=GENERATOR,
                      today=c.today, profile=c.profile, sender=dict(c.sender), purpose=spec["purpose"],
                      recipient=rcp, surface=surface)
    if case_id_seed is not None:
        case["case_id"] = W.case_id(sub, kind, case_id_seed)
    case["audience"] = audience
    case["group_id"] = group_id
    case["points"] = _number_ids([dict(p) for p in spec["points"]], "p")
    case["must_not"] = _number_ids([dict(f) for f in spec["forbid"]], "f")
    cons: dict = {"output_only": True}
    paras = spec["paras"]
    if form == "email":
        gr = greeting or (_greeting(c.rcp, c.cat) if c.rcp else None)
        so = signoff or _signoff(c)
        body = "\n\n".join([gr] + paras + [so])
        draft = (f"Subject: {spec['subject']}\n\n" if spec.get("subject") else "") + body
        n = _nwords(body)
        cons.update(subject=bool(spec.get("subject")), greeting=gr, signoff=so,
                    max_words=max(_round_up(n * 1.6, 10), 80))
    elif form == "text":
        body = " ".join(paras)
        draft = body
        n = _nwords(body)
        cons.update(max_chars=max(_round_up(len(body) * 1.6, 40), 240),
                    max_words=max(_round_up(n * 1.8, 10), 50))
    elif form == "note":
        body = "\n\n".join(paras)
        draft = body
        n = _nwords(body)
        cons.update(max_words=max(_round_up(n * 1.6, 10), 70 if spec.get("surface") == "bio" else 60))
    elif form == "invite":
        desc = " ".join(paras)
        draft = f"Title: {spec['title']}\nDescription: {desc}"
        n = _nwords(desc)
        cons.update(title_max_words=max(8, _nwords(spec["title"]) + 2),
                    description_max_words=max(_round_up(n * 1.6, 10), 60))
    elif form == "section":
        body = "\n\n".join(paras)
        draft = body
        n = _nwords(body)
        assert 155 <= n <= 285, (spec["purpose"], n)
        cons.update(min_words=150, max_words=300)
    else:
        raise ValueError(form)
    case["constraints"].update(cons)
    if spec.get("context"):
        case["context"].update(spec["context"])
    case["reference_draft"] = draft
    return W.finish(case)


def _pick_scenario(scenarios: list, c_factory: Callable[[], Ctx], index: int, seed: int):
    """The scenario for this index; for a profile case, the next one with a matching contact."""
    n = len(scenarios)
    for k in range(n):
        cats, fn = scenarios[(index + k) % n]
        c = c_factory()
        if c.eligible(cats):
            return c, cats, fn
    raise RuntimeError("no eligible scenario")


def _make(sub: str, kind: str, scenarios: list, seed: int, index: int, profile: str | None) -> dict:
    c, cats, fn = _pick_scenario(scenarios, lambda: Ctx(seed, profile), index, seed)
    if cats:
        c.set_recipient(cats)
    spec = fn(c)
    if kind in ("text", "apologize"):
        spec.setdefault("form", "text")
    return _assemble(c, sub, kind, spec)


# ==========================================================================
# W1 email
# ==========================================================================

def _e_move(c: Ctx) -> dict:
    old, new, tm = c.d(3, 8), c.d(10, 21), c.t(9, 17)
    topic = c.pick(["design review", "budget check-in", "vendor call", "planning session", "handover meeting"])
    item = c.pick(["load-test results", "survey findings", "cost figures", "test logs", "interview notes"])
    vendor = c.org()
    return S(f"move the {topic}", [
        P(f"the {topic} moves from {old.s} to {new.s} at {tm.s}", old.a, new.a, tm.a, lead=True),
        P(f"the {item} are not ready yet", item),
        P("offer a short written summary in the meantime")],
        [f"The {topic} needs to move from {old.s} to {new.s} at {tm.s}.",
         f"The {item} are not ready yet, and I would rather go through them properly than rush.",
         "In the meantime I can send you a short written summary of where things stand."],
        forbid=[F(f"naming the vendor {vendor}", vendor)], subject=f"Moving the {topic}")


def _e_status(c: Ctx) -> dict:
    n, money, nxt = c.fig(300, 4800), c.money(900, 9000), c.d(6, 18)
    proj = c.pick(["labelling pilot", "archive clean-up", "survey rollout", "intake redesign"])
    who = c.person()
    return S(f"update on the {proj}", [
        P(f"the {proj} has processed {n.s} records", n.a),
        P(f"{money.s} of the budget is used so far", money.a),
        P(f"the next milestone is {nxt.s}", nxt.a, lead=True),
        P("there are no blockers right now")],
        [f"Here is where the {proj} stands. It has processed {n.s} records so far, and {money.s} of the budget is used.",
         f"The next milestone is {nxt.s}. There are no blockers right now."],
        forbid=[F(f"any blame on {who.name}", who.name)], subject=f"Status of the {proj}")


def _e_booking(c: Ctx) -> dict:
    code, d, tm, place = c.code(), c.d(5, 25), c.t(11, 19), c.place()
    heads = c.fig(8, 39)
    dep = c.money(60, 400)
    return S(f"confirm a booking at {place}", [
        P(f"the booking code is {code.s}", code.a),
        P(f"a table for {heads.s} on {d.s} at {tm.s} at {place}", heads.a, d.a, tm.a, place, lead=True),
        P("ask them to confirm by reply")],
        [f"I am writing about our booking at {place}. The code is {code.s}, and it is for {heads.s} people on {d.s} at {tm.s}.",
         "Could you confirm by reply that everything is in order on your side?"],
        forbid=[F(f"the deposit of {dep.s}", dep.a)], subject=f"Booking {code.s}")


def _e_intro(c: Ctx) -> dict:
    p, d = c.person(), c.d(7, 30)
    topic = c.pick(["route planning", "accessible design", "grant writing", "data cleaning", "audio editing"])
    city = c.pick(["Lisbon", "Ghent", "Bergen", "Graz", "Turku"])
    rate = c.money(60, 500)
    return S(f"introduce {p.name}", [
        P(f"introduce {p.name}, who works on {topic}", p.name, topic, lead=True),
        P(f"{p.first} will be in {city} on {d.s}", city, d.a),
        P("suggest a short call before then")],
        [f"I would like to introduce you to {p.name}, who works on {topic} and has a lot of experience with it.",
         f"{p.first} will be in {city} on {d.s}, so the timing is good. I suggest a short call before then, and I will leave "
         "the two of you to find a slot."],
        forbid=[F(f"{p.first}'s rate of {rate.s}", rate.a)], subject=f"Introducing {p.name}")


def _e_recap(c: Ctx) -> dict:
    decs = c.rng.sample([("retire the legacy dashboard", "legacy dashboard"),
                         ("switch to weekly releases", "weekly releases"),
                         ("hire one contractor for the migration", "contractor"),
                         ("freeze the old template", "old template"),
                         ("pilot the new intake form", "intake form")], 2)
    owner, d = c.person(), c.d(5, 20)
    return S("recap of today's meeting", [
        P(f"we decided to {decs[0][0]}", decs[0][1]),
        P(f"we decided to {decs[1][0]}", decs[1][1]),
        P(f"{owner.name} owns the follow-up, due {d.s}", owner.name, d.a, lead=True)],
        [f"Thanks for the discussion today. We decided to {decs[0][0]} and to {decs[1][0]}.",
         f"{owner.name} owns the follow-up, and it is due {d.s}."],
        forbid=[F("the budget discussion", "budget")], subject="Recap of today's meeting")


def _e_thanks(c: Ctx) -> dict:
    place, d = c.place(), c.d(4, 14)
    ev = c.pick(["workshop", "open evening", "training day", "site visit"])
    feat = c.pick(["the sound system", "the printed handouts", "the lunch", "the small-group exercise"])
    return S(f"thank them for the {ev}", [
        P(f"thank them for hosting the {ev} at {place}", place, lead=True),
        P(f"I liked {feat}", feat),
        P(f"I will send my notes by {d.s}", d.a)],
        [f"Thank you for hosting the {ev} at {place}. It was well organised, and I particularly liked {feat}.",
         f"I will send my notes by {d.s}."],
        subject=f"Thank you for the {ev}")


def _e_invoice(c: Ctx) -> dict:
    code, m, d = c.code("INV"), c.money(300, 3900), c.d(8, 28)
    return S("send an invoice", [
        P(f"invoice {code.s} totals {m.s}", code.a, m.a, lead=True),
        P(f"payment is due {d.s}", d.a),
        P("the bank details have not changed")],
        [f"Invoice {code.s} for the recent work totals {m.s}, and payment is due {d.s}.",
         "The bank details have not changed since the last invoice."],
        forbid=[F("the late-payment penalty", "penalty")], subject=f"Invoice {code.s}")


EMAIL = [(("work", "boss"), _e_move), (("work", "boss", "service"), _e_status),
         (("service", "work", "friend"), _e_booking), (("work", "friend", "prof"), _e_intro),
         (("work", "boss"), _e_recap), (("work", "prof", "friend", "boss", "service"), _e_thanks),
         (("work", "service"), _e_invoice)]


# ==========================================================================
# W1 reply (a thread in the context)
# ==========================================================================

def _mk_thread(c: Ctx, msgs: list[tuple[bool, str]], days: list[int]) -> dict:
    """msgs: (from_me, text), oldest first; days: days before today for each message."""
    th = []
    for i, ((mine, text), back) in enumerate(zip(msgs, days)):
        dd = c.past(back)
        hh = f"{9 + i:02d}:{c.rng.choice([0, 15, 30, 45]):02d}"
        th.append({"from": c.sender["name"] if mine else c.rcp["name"], "time": f"{dd.s} {hh}", "text": text})
    return {"thread": th, "thread_kind": "email"}


def _r_late_fee(c: Ctx) -> dict:
    fee, d, tm = c.money(25, 120), c.d(4, 14), c.t(9, 17)
    place = c.place()
    ctx = _mk_thread(c, [(False, f"Can we sort out the schedule for the handover? Last time there was a late fee of {fee.s}, "
                                 "so I would like to avoid that."),
                         (True, "Of course. Which days work for you?"),
                         (False, "Anything next week, as long as it is in the morning.")], [6, 5, 4])
    return S("reply about the handover meeting", [
        P(f"I can meet on {d.s} at {tm.s} at {place}", d.a, tm.a, place, lead=True),
        P("the morning slot suits me")],
        ["Next week works. I can meet on " f"{d.s} at {tm.s} at {place}.",
         "The morning slot suits me, so we can keep it short and settle the handover then."],
        forbid=[F("the late fee mentioned earlier", fee.a)], context=ctx, subject="Re: the handover")


def _r_lab(c: Ctx) -> dict:
    d, tm = c.d(3, 12), c.t(10, 17)
    room = c.code("B")
    data = c.pick(["titration data", "soil samples", "calibration sheets", "survey tallies"])
    ctx = _mk_thread(c, [(False, "Are we still doing the write-up together?"),
                         (True, "Yes, I think so. I need to look at my week first."),
                         (False, "Ok. I handed my part in late and got a late-submission penalty, so I really want this one on time."),
                         (False, "When can you meet?")], [5, 5, 4, 4])
    return S("reply to my lab partner", [
        P(f"I can meet on {d.s} at {tm.s} in room {room.s}", d.a, tm.a, room.a, lead=True),
        P(f"I will bring the {data}", data)],
        [f"Yes, we are still doing the write-up together. I can meet on {d.s} at {tm.s} in room {room.s}.",
         f"I will bring the {data}, so we can start straight away."],
        forbid=[F("the late-submission penalty mentioned earlier", "penalty")], context=ctx, subject="Re: the write-up")


def _r_quote(c: Ctx) -> dict:
    price, d, comp = c.money(800, 4000), c.d(5, 20), c.org()
    ctx = _mk_thread(c, [(True, "Could you send a quote for the full set of windows?"),
                         (False, f"Our quote is {price.s}. For what it is worth, {comp} quoted a bit lower, but without the fitting."),
                         (False, "We would need an answer to hold the slot.")], [9, 7, 7])
    return S("accept the quote", [
        P(f"I accept the quote of {price.s}", price.a, lead=True),
        P(f"the work can start on {d.s}", d.a),
        P("ask them to send a written confirmation")],
        [f"I accept the quote of {price.s}. The work can start on {d.s}.",
         "Please send a written confirmation so that I have everything in one place."],
        forbid=[F(f"the other firm {comp}", comp)], context=ctx, subject="Re: the quote")


def _r_parking(c: Ctx) -> dict:
    d, tm = c.d(2, 6), c.t(7, 12)
    other = c.person()
    ctx = _mk_thread(c, [(False, "Your car has been in the shared bay for two days. It is blocking the delivery lorry."),
                         (False, f"{other.name} from number nine has complained again.")], [3, 2])
    return S("reply about the parked car", [
        P(f"I will move the car on {d.s} at {tm.s}", d.a, tm.a, lead=True),
        P("apologise for the inconvenience")],
        [f"I am sorry for the inconvenience. I will move the car on {d.s} at {tm.s}."],
        forbid=[F(f"naming {other.name}", other.name)], context=ctx, subject="Re: the shared bay")


def _r_offer(c: Ctx) -> dict:
    offer, d, place = c.money(40000, 90000), c.d(4, 16), c.place()
    ctx = _mk_thread(c, [(False, f"Big news. I got the offer! Between us, it is {offer.s} a year, so please keep that quiet."),
                         (True, "That is great news.")], [2, 2])
    return S("reply to a friend about a new job", [
        P("congratulate them warmly", lead=True),
        P(f"invite them for a coffee at {place} on {d.s}", place, d.a)],
        ["Congratulations again, I am really pleased for you.",
         f"Let me buy you a coffee at {place} on {d.s} to celebrate."],
        forbid=[F("the salary figure they mentioned", offer.a, stated=False)], context=ctx, subject="Re: your news")


def _r_order(c: Ctx) -> dict:
    code, d = c.code("ORD"), c.d(3, 12)
    courier = c.org()
    ctx = _mk_thread(c, [(False, f"My order {code.s} has not arrived and the tracking has not moved."),
                         (True, "I am sorry about that. I am checking with the warehouse."),
                         (False, f"The parcel was handed to {courier}. Are they the problem?")], [4, 4, 3])
    return S("reply to a customer about a late order", [
        P(f"order {code.s} will now arrive on {d.s}", code.a, d.a, lead=True),
        P("apologise and offer free delivery on the next order")],
        [f"I am sorry about the delay with order {code.s}. It will now arrive on {d.s}.",
         "To make up for it, your next order ships free of charge."],
        forbid=[F(f"blaming the courier {courier}", courier)], context=ctx, subject=f"Re: order {code.s}")


REPLY = [(("work", "friend"), _r_late_fee), (("friend", "prof"), _r_lab), (("service", "work"), _r_quote),
         (("service", "friend"), _r_parking), (("friend",), _r_offer), (("work", "service"), _r_order)]


def _clean_body(body: str) -> str:
    lines = [ln.strip() for ln in body.splitlines() if ln.strip() and not ln.lstrip().startswith(">")]
    return " ".join(" ".join(lines).split())


def _reply_real_threads(pid: str) -> list[dict]:
    mine = {a["address"].lower() for a in W.load_profile(pid).get("accounts", [])}
    out = []
    for th in W.email_threads(pid):
        last = th[-1]
        subj = re.sub(r"^(?:re|fwd?):\s*", "", th[0]["subject"], flags=re.I)
        if last["from"]["addr"].lower() in mine or not subj.isascii() or len(subj.split()) > 8:
            continue
        ct = next((x for x in W.contacts(pid) if last["from"]["addr"].lower() in
                   [e.lower() for e in (x.get("emails") or [])]), None)
        if not ct or not cat_of(ct["relation"]):
            continue
        msgs = th[-3:]
        texts = [_clean_body(m["body"]) for m in msgs]
        if any(not t or len(t.split()) > 110 for t in texts):
            continue
        out.append({"thread": th, "msgs": msgs, "texts": texts, "contact": ct, "subject": subj})
    out.sort(key=lambda x: x["thread"][0]["id"])
    asking = [x for x in out if "?" in x["texts"][-1] or re.search(r"\bplease\b|\bcan you\b|\bcould you\b", x["texts"][-1], re.I)]
    return asking if len(asking) >= 3 else out


_PLAIN_CAPS = {"dear", "professor", "thank", "thanks", "kind", "best", "perfect", "please", "regards", "hello", "sure",
               "great", "sorry", "yes", "okay", "hope", "just", "also", "here", "there", "since", "after", "before",
               "though", "still", "maybe", "wednesday", "thursday", "friday", "monday", "tuesday", "saturday", "sunday"}


def _forbidden_from_thread(c: Ctx, texts: list[str], avoid: str) -> tuple[str, object] | None:
    """A planted detail of the real thread the reply should not repeat: an amount, a code or a
    figure first, then a distinctive name."""
    names = f"{c.sender['name']} {c.rcp['name']}".lower()
    avoid_l = avoid.lower()

    def free(s: str) -> bool:
        return not re.search(r"(?<!\w)" + re.escape(s.lower()) + r"(?!\w)", avoid_l)

    strong, weak = {}, {}
    for a in N.find_atoms(" ".join(texts)):
        s = a.surface.strip()
        if not free(s):
            continue
        if a.kind == "amount":
            strong[s.lower()] = (s, {"type": "amount", "value": str(a.value[0])})
        elif a.kind == "code" and len(s) >= 4:
            strong[s.lower()] = (s, {"type": "code", "value": s})
        elif a.kind == "number" and len(re.sub(r"\D", "", s)) >= 3:
            strong[s.lower()] = (s, {"type": "number", "value": re.sub(r"[^\d.]", "", s)})
        elif a.kind == "name" and len(s) >= 5 and s.lower() not in names and s.lower() not in _PLAIN_CAPS:
            weak[s.lower()] = (s, s)
    pool = strong or weak
    return c.rng.choice([pool[k] for k in sorted(pool)]) if pool else None


def _gen_reply_real(seed: int, index: int, profile: str) -> dict:
    c = Ctx(seed, profile)
    pool = _reply_real_threads(profile)
    pick = pool[(index // 2 + c.rng.randrange(len(pool))) % len(pool)]
    ct = pick["contact"]
    cat = cat_of(ct["relation"])
    full = ct.get("full_name") or ct["name"]
    c.cat = cat
    c.rcp = {"name": _clean_name(full), "relation": ct["relation"], "short": _clean_name(ct["name"]),
             "emails": ct.get("emails") or [], "id": ct["id"]}
    pro = W.load_profile(profile)
    base = dt.date.fromisoformat(pro["anchor"]["date"])
    thread = []
    for m, t in zip(pick["msgs"], pick["texts"]):
        dd = base + dt.timedelta(days=m["day"])
        c.w.used_dates.add(dd)
        thread.append({"from": m["from"]["name"], "time": f"{N.format_p7(dd, c.today.year)} {m['time']}", "text": t})
    intent = (index // 2 + c.rng.randrange(3)) % 3
    d, tm = c.d(4, 16), c.t(9, 17)
    purpose = f'reply to {c.rcp["name"]} in the email thread "{pick["subject"]}"'
    if intent == 0:
        pts = [P(f"I can talk it through on {d.s} at {tm.s}", d.a, tm.a, lead=True),
               P("acknowledge what they wrote in their last email")]
        paras = ["Thank you for your message. I have read it carefully.",
                 f"I can talk it through on {d.s} at {tm.s}. Does that suit you?"]
    elif intent == 1:
        pts = [P(f"I am free on {d.s}, any time after {tm.s}", d.a, tm.a, lead=True),
               P("thank them for the update")]
        paras = ["Thank you for the update.",
                 f"I am free on {d.s}, any time after {tm.s}. Please pick whatever suits you best."]
    else:
        item = c.pick(["the signed form", "the updated draft", "my laptop", "the printed copies"])
        pts = [P(f"I can meet on {d.s} at {tm.s}", d.a, tm.a, lead=True), P(f"I will bring {item}", item),
               P("keep it short")]
        paras = [f"Thank you for your message. I can meet on {d.s} at {tm.s}, and I will bring {item}.",
                 "I will keep it short."]
    forb = _forbidden_from_thread(c, pick["texts"], " ".join(paras + [p_["text"] for p_ in pts] + [pick["subject"]]))
    forbid = [F(f"mentioning {forb[0]}", forb[1])] if forb else []
    if not forb:
        forbid = [F("any promise of a decision", "guarantee")]
    pts = pts[:4]
    spec = S(purpose, pts, paras, forbid=forbid, subject=f"Re: {pick['subject']}",
             context={"thread": thread, "thread_kind": "email"})
    return _assemble(c, "W1", "reply", spec)


def gen_reply(seed: int, *, index: int, profile: str | None = None) -> dict:
    if profile and _reply_real_threads(profile):
        return _gen_reply_real(seed, index, profile)
    return _make("W1", "reply", REPLY, seed, index, profile)


# ==========================================================================
# W1 text message
# ==========================================================================

def _t_late(c: Ctx) -> dict:
    tm, place = c.t(9, 20), c.place()
    return S("say I am running late", [
        P(f"I will arrive at {place} at {tm.s}", place, tm.a, lead=True),
        P("they should start without me")],
        [f"Running late, sorry. I will arrive at {place} at {tm.s}, so please start without me."],
        forbid=[F("the broken train", "train")])


def _t_pickup(c: Ctx) -> dict:
    d, tm, place = c.d(2, 9), c.t(8, 19), c.place()
    item = c.pick(["the folding chairs", "the spare cables", "the speaker", "the board game"])
    return S("arrange a pickup", [
        P(f"pickup on {d.s} at {tm.s} at {place}", d.a, tm.a, place, lead=True),
        P(f"please bring {item}", item)],
        [f"Can we do the pickup on {d.s} at {tm.s} at {place}? Please bring {item}."])


def _t_dinner(c: Ctx) -> dict:
    d, tm, place = c.d(3, 10), c.t(17, 21), c.place()
    heads = c.fig(3, 9)
    return S("confirm a dinner reservation", [
        P(f"table for {heads.s} at {place} on {d.s} at {tm.s}", heads.a, place, d.a, tm.a, lead=True),
        P("ask whether anyone has allergies")],
        [f"I booked a table for {heads.s} at {place} on {d.s} at {tm.s}. Does anyone have allergies I should mention?"])


def _t_split(c: Ctx) -> dict:
    m, d = c.money(8, 90), c.d(3, 12)
    bill = c.pick(["electricity", "internet", "water", "heating"])
    return S(f"ask for a share of the {bill} bill", [
        P(f"your share of the {bill} bill is {m.s}", m.a, bill, lead=True),
        P(f"please pay by {d.s}", d.a)],
        [f"Your share of the {bill} bill is {m.s}. Could you pay it by {d.s}? Thanks."])


def _t_appt(c: Ctx) -> dict:
    code, d, tm = c.code(), c.d(2, 10), c.t(8, 17)
    return S("remind about an appointment", [
        P(f"appointment code {code.s}", code.a),
        P(f"it is on {d.s} at {tm.s}", d.a, tm.a, lead=True),
        P("remind them to bring photo ID")],
        [f"Reminder that your appointment is on {d.s} at {tm.s}. The code is {code.s}, and please bring photo ID."])


def _t_party(c: Ctx) -> dict:
    d, tm, place = c.d(5, 21), c.t(16, 20), c.place()
    guest = c.person()
    return S("organise a surprise party", [
        P(f"the party is at {place} on {d.s} at {tm.s}", place, d.a, tm.a, lead=True),
        P("keep it a secret")],
        [f"The party is at {place} on {d.s} at {tm.s}. Please keep it a secret."],
        forbid=[F(f"telling {guest.name}", guest.name)])


TEXT = [(("friend", "family"), _t_late), (("friend", "family", "work"), _t_pickup), (("friend", "family"), _t_dinner),
        (("friend",), _t_split), (("friend", "family", "service"), _t_appt), (("friend", "family"), _t_party)]


# ==========================================================================
# W1 note
# ==========================================================================

def _n_handover(c: Ctx) -> dict:
    d1, d2 = c.d(3, 6), c.d(9, 14)
    code = c.code()
    task = c.pick(["the weekly report", "the supplier calls", "the stock count"])
    return S("a handover note for whoever covers me", [
        P(f"I am away from {d1.s} to {d2.s}", d1.a, d2.a, lead=True),
        P(f"{task} still needs doing", task),
        P(f"the access code for the store room is {code.s}", code.a)],
        [f"I am away from {d1.s} to {d2.s}, so here is what you need.",
         f"{task[0].upper() + task[1:]} still needs doing while I am gone. The access code for the store room is {code.s}.",
         "Everything else can wait until I am back."],
        no_rcp=True, form="note", surface="note")


def _n_flat(c: Ctx) -> dict:
    d, who = c.d(2, 8), c.person()
    return S("a note for my flatmates about the cleaning rota", [
        P(f"the deep clean is on {d.s}", d.a, lead=True),
        P(f"{who.first} has the bathroom this time", who.first),
        P("bins go out the night before")],
        [f"The deep clean is on {d.s}. {who.first} has the bathroom this time.",
         "Bins go out the night before, so please do not leave it to the morning."],
        no_rcp=True, form="note", surface="note")


def _n_meeting(c: Ctx) -> dict:
    n, m = c.fig(40, 900), c.money(500, 5000)
    term = c.pick(["pilot group", "waiting list", "pop-up stall"])
    return S("a short note summarising the call", [
        P(f"the {term} now has {n.s} people", term, n.a),
        P(f"the budget for the next stage is {m.s}", m.a, lead=True),
        P("the next call will be short")],
        [f"Summary of the call. The {term} now has {n.s} people, and the budget for the next stage is {m.s}.",
         "The next call will be short."],
        no_rcp=True, form="note", surface="note")


def _n_door(c: Ctx) -> dict:
    code, tm1, tm2 = c.code("PKG"), c.t(9, 12), c.t(14, 18)
    place = c.pick(["the green box by the gate", "the shed behind the house", "the neighbour's porch"])
    return S("a note for the delivery driver", [
        P(f"parcel {code.s} goes in {place}", code.a, place, lead=True),
        P(f"someone is home between {tm1.s} and {tm2.s}", tm1.a, tm2.a)],
        [f"Parcel {code.s} goes in {place}.",
         f"Someone is home between {tm1.s} and {tm2.s} if you need to ring."],
        no_rcp=True, form="note", surface="note")


def _n_room(c: Ctx) -> dict:
    room, tm = c.place(), c.t(8, 11)
    return S("a pinned note on how to book the shared room", [
        P(f"book the {room} through the shared calendar", room, lead=True),
        P(f"bookings open at {tm.s}", tm.a),
        P("cancel if plans change")],
        [f"To use the {room}, book it through the shared calendar. Bookings open at {tm.s}.",
         "If your plans change, please cancel so someone else can use the slot."],
        no_rcp=True, form="note", surface="note")


def _n_pack(c: Ctx) -> dict:
    d, items = c.d(6, 20), c.rng.sample(["rain jacket", "charger", "passport", "headlamp", "water filter"], 3)
    return S("a packing note to myself", [
        P(f"the trip starts on {d.s}", d.a, lead=True),
        P(f"pack the {items[0]} and the {items[1]}", items[0], items[1]),
        P(f"do not forget the {items[2]}", items[2])],
        [f"The trip starts on {d.s}. Pack the {items[0]} and the {items[1]}.",
         f"Do not forget the {items[2]}."],
        no_rcp=True, form="note", surface="note")


NOTE = [((), _n_handover), ((), _n_flat), ((), _n_meeting), ((), _n_door), ((), _n_room), ((), _n_pack)]


# ==========================================================================
# W1 invitation (title and description)
# ==========================================================================

def _i_review(c: Ctx) -> dict:
    d, t1, t2 = c.d(8, 30), c.t(9, 11), c.t(12, 15)
    items = c.rng.sample(["roadmap risks", "hiring plan", "launch dates", "budget gaps", "tool choices"], 3)
    return S("a calendar invitation for the quarterly roadmap review", [
        P(f"{d.s} from {t1.s} to {t2.s}", d.a, t1.a, t2.a, lead=True),
        P(f"the agenda is {items[0]}, {items[1]} and {items[2]}", *items),
        P("read the draft document beforehand")],
        [f"We meet on {d.s} from {t1.s} to {t2.s}. The agenda covers {items[0]}, {items[1]} and {items[2]}.",
         "Please read the draft document beforehand so we can use the time to decide."],
        form="invite", surface="invite", no_rcp=True, title="Quarterly roadmap review")


def _i_offsite(c: Ctx) -> dict:
    d, t1, t2, place = c.d(10, 40), c.t(9, 10), c.t(16, 18), c.place()
    return S("an invitation to the team offsite", [
        P(f"{d.s} at {place} from {t1.s} to {t2.s}", d.a, place, t1.a, t2.a, lead=True),
        P("bring a laptop and a notebook"),
        P("lunch is provided")],
        [f"Join us at {place} on {d.s} from {t1.s} to {t2.s}. Please bring a laptop and a notebook.",
         "Lunch is provided, so there is no need to organise your own."],
        form="invite", surface="invite", no_rcp=True, title="Team offsite day")


def _i_club(c: Ctx) -> dict:
    d, tm, place = c.d(5, 25), c.t(18, 20), c.place()
    book = c.pick(["Salt Road Stories", "Winter Orchard", "Quiet Harbour", "Glass Atlas"])
    return S("an invitation to the book club", [
        P(f"we discuss {book}", book),
        P(f"{d.s} at {tm.s} at {place}", d.a, tm.a, place, lead=True),
        P("finish the first half beforehand")],
        [f"This month we discuss {book}. We meet on {d.s} at {tm.s} at {place}.",
         "Please finish the first half beforehand so that everyone can join the conversation."],
        form="invite", surface="invite", no_rcp=True, title="Book club meeting")


def _i_kick(c: Ctx) -> dict:
    d, t1, t2 = c.d(4, 14), c.t(10, 11), c.t(12, 13)
    code = c.code("PRJ")
    return S("a kickoff invitation for a new project", [
        P(f"project {code.s} kicks off on {d.s}", code.a, d.a, lead=True),
        P(f"the session runs from {t1.s} to {t2.s}", t1.a, t2.a),
        P("bring questions about scope")],
        [f"Project {code.s} kicks off on {d.s}. The session runs from {t1.s} to {t2.s}.",
         "Please bring your questions about scope so that we can settle them together."],
        form="invite", surface="invite", no_rcp=True, title="Project kickoff")


def _i_train(c: Ctx) -> dict:
    d, tm, place = c.d(9, 32), c.t(9, 14), c.place()
    topic = c.pick(["first aid", "fire safety", "data protection", "accessible slides"])
    return S("an invitation to a training session", [
        P(f"training on {topic}", topic),
        P(f"{d.s} at {tm.s} at {place}", d.a, tm.a, place, lead=True),
        P("it counts toward the yearly requirement")],
        [f"This session covers {topic}. It takes place on {d.s} at {tm.s} at {place}.",
         "It counts toward the yearly requirement, so please try to attend."],
        form="invite", surface="invite", no_rcp=True, title="Training session")


def _i_lunch(c: Ctx) -> dict:
    d, tm, place = c.d(3, 16), c.t(11, 13), c.place()
    who = c.person()
    return S("an invitation to a farewell lunch", [
        P(f"a farewell lunch for {who.name}", who.name),
        P(f"{d.s} at {tm.s} at {place}", d.a, tm.a, place, lead=True),
        P("tell me about dietary needs")],
        [f"We are saying goodbye to {who.name} with a lunch on {d.s} at {tm.s} at {place}.",
         "Please tell me about any dietary needs before the day."],
        form="invite", surface="invite", no_rcp=True, title=f"Farewell lunch")


INVITE = [((), _i_review), ((), _i_offsite), ((), _i_club), ((), _i_kick), ((), _i_train), ((), _i_lunch)]


# ==========================================================================
# W1 section (150-300 words)
# ==========================================================================

def _s_status(c: Ctx) -> dict:
    n, m, d = c.fig(600, 7000), c.money(1500, 9000), c.d(14, 60)
    proj = c.pick(["community garden", "reading programme", "repair cafe"])
    p = [f"The {proj} began as a small experiment and has grown steadily since. Volunteers now meet every week, and the "
         "routine is settled enough that new people can join without much explanation. This section reports where the work "
         "stands and what comes next.",
         f"So far the {proj} has reached {n.s} visitors. That is more than we planned for, and it has shaped how we use our "
         f"time. Spending is on track: {m.s} of the budget has been used, mostly on tools, materials and a small amount of "
         "publicity. We have kept receipts for everything and the records are open to the whole team.",
         "Most of the effort went into building a reliable weekly rhythm. We learned that clear roles matter more than a long "
         "list of ideas, and that a short checklist at the end of each session prevents most of the small problems we saw "
         "early on. Feedback from visitors has been warm, and the most common request is for longer opening hours.",
         f"The next milestone is {d.s}, when we plan to review the first stage and decide how to use the remaining budget. "
         "Until then the priority is to keep the routine steady, train two more volunteers and write down what we have "
         "learned so that the work does not depend on any single person."]
    return S(f"a status section for the {proj}", [
        P(f"the {proj} has reached {n.s} visitors", n.a),
        P(f"{m.s} of the budget is used", m.a),
        P(f"the next milestone is {d.s}", d.a, lead=True)], p, form="section", surface="document", no_rcp=True)


def _s_risk(c: Ctx) -> dict:
    code, d, m = c.code("RSK"), c.d(10, 50), c.money(2000, 12000)
    sys_ = c.pick(["booking system", "ordering portal", "records archive"])
    p = [f"This section describes the main risk to the project, which is listed in our register as {code.s}. It concerns the "
         f"{sys_}, which many of our daily tasks depend on, and it is the one item that could delay everything else if it is "
         "not handled with care.",
         f"The {sys_} is old and was built for a smaller team than the one that uses it now. Its supplier has announced that "
         f"support ends on {d.s}. After that date, faults will not be fixed and security updates will stop. We cannot rely on "
         "it beyond that point without accepting a level of exposure that is hard to justify.",
         f"Replacing it has a cost. Our current estimate is {m.s}, which includes setup, moving the existing data and training "
         "for the people who use it most. We have looked at a short extension of the old contract, but it only postpones the "
         "decision and does not remove the problem.",
         "Our recommendation is to start the replacement early, run the old and new systems side by side for a short period, "
         "and keep a clear record of every step. The team will review progress at each stage, and any change to the plan "
         "will be reported here so that the risk is never hidden."]
    return S("a risk section for the project plan", [
        P(f"the risk is {code.s}, about the {sys_}", code.a, sys_),
        P(f"support for it ends on {d.s}", d.a, lead=True),
        P(f"replacement is estimated at {m.s}", m.a)], p, form="section", surface="document", no_rcp=True)


def _s_back(c: Ctx) -> dict:
    n, place, d = c.fig(2000, 9000), c.place(), c.d(30, 120)
    yrs = c.fig(4, 19)
    p = [f"{place} has served the neighbourhood for {yrs.s} years, and this section sets out why the proposal matters now. "
         "It gives the background that a new reader needs before the plan itself.",
         f"Over that time the number of regular visitors has grown to {n.s} each year. The building was not designed for "
         "this, and staff have adapted with small changes: a second desk, a longer opening day, and a simple booking list "
         "for the busiest sessions. These changes helped, but they have reached their limit.",
         "Visitors tell us that the main problems are space and waiting time. On busy days people queue outside, and the "
         "quiet areas that users value most are the first to be lost. Staff report that more of their day goes on "
         "managing the crowd than on helping the people who came for support.",
         f"The proposal below asks for a modest expansion, to be ready by {d.s}. It keeps the character of the place, "
         "adds capacity where the pressure is greatest and leaves room to grow. The following sections explain the design, "
         "the cost and the way we would measure whether it worked."]
    return S("a background section for a funding proposal", [
        P(f"{place} has served the neighbourhood for {yrs.s} years", place, yrs.a),
        P(f"there are {n.s} regular visitors each year", n.a),
        P(f"the expansion should be ready by {d.s}", d.a, lead=True)], p, form="section", surface="document", no_rcp=True)


def _s_results(c: Ctx) -> dict:
    n, m, d = c.fig(80, 900), c.fig(15, 79), c.d(5, 25)
    term = c.pick(["morning sessions", "walk-in clinics", "peer groups"])
    p = [f"This section reports the results of the survey that ran until {d.s}. It describes who answered, what they said and "
         "what we think the answers mean for the next year.",
         f"In total {n.s} people replied, which is a good response for a survey of this kind. Of those, {m.s} said that the "
         f"{term} were the most useful part of the service. Answers were consistent across age groups, though newer members "
         "were more likely to ask for clearer information before their first visit.",
         "We read the written comments as well as the scores. Many of them repeat a few simple points: people like being "
         "greeted by name, they want shorter forms, and they would like a quieter space for longer conversations. A few "
         "comments were critical, and we have included the fair ones in the actions below.",
         "Taken together, the results support keeping the current structure while fixing the small frictions that people "
         "mention most. We will share a short summary with participants, and we will run the survey again next year so "
         "that changes can be compared on the same questions."]
    return S("a results section for a survey report", [
        P(f"{n.s} people replied", n.a),
        P(f"{m.s} said the {term} were the most useful", m.a, term, lead=True),
        P(f"the survey ran until {d.s}", d.a)], p, form="section", surface="document", no_rcp=True)


def _s_howto(c: Ctx) -> dict:
    d, tm, place = c.d(5, 30), c.t(8, 10), c.place()
    code = c.code("LAB")
    p = [f"This section explains how to borrow equipment from {place}. It is meant for new staff, and it should take only a "
         "few minutes to read.",
         f"Every item has a label, and the label starts with a code such as {code.s}. Write the code on the loan sheet when "
         f"you collect the item, and sign the sheet again when you return it. The desk opens at {tm.s} each working day, and "
         "an item that is not signed out cannot be tracked if it goes missing.",
         f"Loans last one week. If you need an item for longer, ask at the desk before the week ends rather than after. "
         f"Starting on {d.s}, the desk will also check that each item is clean and complete when it comes back, so please "
         "leave a few minutes for that.",
         "If an item is damaged, tell the desk straight away and describe what happened. Nobody is blamed for honest "
         "accidents, but a quick report lets the next person borrow a working item. Questions about the process can go to the "
         "desk in person."]
    return S("a how-to section for the staff handbook", [
        P(f"equipment is borrowed from {place}", place),
        P(f"item labels start with a code such as {code.s}", code.a),
        P(f"the desk opens at {tm.s} and from {d.s} returned items are checked there", tm.a, d.a, lead=True)], p, form="section", surface="document",
        no_rcp=True)


def _s_policy(c: Ctx) -> dict:
    m, d, n = c.money(30, 200), c.d(20, 90), c.fig(12, 90)
    p = ["This section summarises the new travel policy so that staff can plan trips with confidence. It replaces the older "
         "guidance, and it applies to every trip booked from the date given below.",
         f"The main change is a daily allowance of {m.s} for meals. Staff no longer need to keep every receipt for food, but "
         "they should still keep receipts for transport and lodging. Claims are checked monthly, and a clear claim is paid "
         "with the regular payroll run.",
         f"The policy takes effect on {d.s}. Trips booked before that date follow the old rules, even if they take place "
         f"later. We estimate that about {n.s} trips a year are affected, so the finance team will be on hand to answer "
         "questions during the first weeks.",
         "We wrote the policy to be fair and simple. If a rule does not fit a particular trip, ask the finance team before "
         "you travel, and they will say what is allowed. Feedback is welcome, and the policy will be reviewed after the "
         "first full year so that it keeps pace with how people actually work."]
    return S("a summary of the new travel policy", [
        P(f"the daily meal allowance is {m.s}", m.a),
        P(f"the policy takes effect on {d.s}", d.a, lead=True),
        P(f"about {n.s} trips a year are affected", n.a)], p, form="section", surface="document", no_rcp=True)


SECTION = [((), _s_status), ((), _s_risk), ((), _s_back), ((), _s_results), ((), _s_howto), ((), _s_policy)]


# ==========================================================================
# W1 bio
# ==========================================================================

PROFILE_BITS = {"student": ("Environmental Science", "Calder Valley University"),
                "tech": ("billing software", "Tessellate Labs"),
                "mixed": ("human-computer interaction", "Ridgeline Institute of Technology")}


def _bits(c: Ctx) -> tuple[str, str]:
    if c.profile:
        return PROFILE_BITS[c.profile]
    return (c.pick(["urban ecology", "food logistics", "audio design", "public health data"]), c.org())


def _b_speaker(c: Ctx) -> dict:
    field, org = _bits(c)
    yrs, n = c.fig(3, 19), c.fig(30, 400)
    me = c.sender["name"]
    return S("a short bio for a conference speaker page", [
        P(f"{me} works on {field} at {org}", field, org, lead=True),
        P(f"{yrs.s} years of experience", yrs.a),
        P(f"a talk for {n.s} attendees last year", n.a)],
        [f"{me} works on {field} at {org}. With {yrs.s} years of experience, {me.split()[0]} likes to turn complicated "
         f"questions into practical advice. Last year {me.split()[0]} gave a talk for {n.s} attendees and stayed to answer "
         "questions until the room closed."],
        no_rcp=True, form="note", surface="bio")


def _b_cover(c: Ctx) -> dict:
    field, org = _bits(c)
    role = c.pick(["research assistant", "project coordinator", "junior analyst"])
    d, yrs = c.d(10, 40), c.fig(2, 9)
    return S(f"a short cover note for a {role} application", [
        P(f"I am applying for the {role} role at {org}", role, org, lead=True),
        P(f"I have worked in {field} for {yrs.s} years", field, yrs.a),
        P(f"I can start on {d.s}", d.a)],
        [f"I am applying for the {role} role at {org}. I have worked in {field} for {yrs.s} years, and I enjoy work that "
         f"combines careful detail with clear communication. I can start on {d.s}, and I would be glad to talk about how "
         "my experience fits the team."],
        no_rcp=True, form="note", surface="bio")


def _b_vol(c: Ctx) -> dict:
    org, place = c.org(), c.place()
    hrs, d = c.fig(3, 19), c.d(7, 30)
    return S("a note for a volunteer application", [
        P(f"I would like to volunteer with {org}", org, lead=True),
        P(f"I can give {hrs.s} hours a week", hrs.a),
        P(f"I am available from {d.s} and live near {place}", d.a, place)],
        [f"I would like to volunteer with {org}. I can give {hrs.s} hours a week, starting from {d.s}, and I live near "
         f"{place}. I am reliable, I learn quickly and I enjoy working with new people."],
        no_rcp=True, form="note", surface="bio")


def _b_award(c: Ctx) -> dict:
    p = c.person()
    org, n = c.org(), c.fig(15, 280)
    return S("a short nomination blurb for a colleague", [
        P(f"I nominate {p.name} for the service award at {org}", p.name, org, lead=True),
        P(f"{p.first} trained {n.s} new staff", n.a),
        P("always calm under pressure")],
        [f"I nominate {p.name} for the service award at {org}. {p.first} trained {n.s} new staff this year and was calm "
         "under pressure every time something went wrong. Colleagues trust that person to help, and it shows in how "
         "the whole team works."],
        no_rcp=True, form="note", surface="bio")


def _b_web(c: Ctx) -> dict:
    field, org = _bits(c)
    place, me = c.pick(["Ghent", "Bergen", "Porto", "Turku"]), c.sender["name"]
    hobby = c.pick(["rock climbing", "baking sourdough", "birdwatching", "restoring bikes"])
    return S("an about blurb for my personal website", [
        P(f"I am {me} and I work on {field} at {org}", me, field, org, lead=True),
        P(f"I am based in {place}", place),
        P(f"outside work I enjoy {hobby}", hobby)],
        [f"I am {me} and I work on {field} at {org}. I am based in {place}, where I try to keep my work close to the "
         f"people it helps. Outside work I enjoy {hobby}, which keeps me patient and curious."],
        no_rcp=True, form="note", surface="bio")


def _b_news(c: Ctx) -> dict:
    p = c.person()
    field, org = _bits(c)
    d = c.d(3, 14)
    return S("a short introduction of a new team member for the newsletter", [
        P(f"welcome {p.name}, who joins {org}", p.name, org, lead=True),
        P(f"{p.first} will work on {field}", field),
        P(f"the first day is {d.s}", d.a)],
        [f"Please welcome {p.name}, who joins {org}. {p.first} will work on {field} and brings plenty of fresh ideas. "
         f"The first day is {d.s}, so say hello if you pass by."],
        no_rcp=True, form="note", surface="bio")


BIO = [((), _b_speaker), ((), _b_cover), ((), _b_vol), ((), _b_award), ((), _b_web), ((), _b_news)]


# ==========================================================================
# W2 hard messages
# ==========================================================================

# ---- decline -------------------------------------------------------------

def _dc_trip(c: Ctx) -> dict:
    d1 = c.d(14, 40)
    d2 = c.reserve_after(d1, 2)
    dn = c.d(45, 60)
    trip = c.pick(["hiking trip", "cabin weekend", "sailing trip"])
    return S(f"decline the {trip}", [
        P(f"I cannot come on the {trip} from {d1.s} to {d2.s}", d1.a, d2.a, lead=True),
        P("the real reason is a work deadline", "deadline"),
        P(f"offer dinner on {dn.s} instead", dn.a)],
        [f"Thank you for inviting me on the {trip} from {d1.s} to {d2.s}. I cannot come, because I have a work deadline that week.",
         f"I would love to see you all the same. Could we have dinner on {dn.s} instead?"],
        forbid=[F("inventing a reason such as being ill", "ill", "sick")], subject=f"About the {trip}")


def _dc_talk(c: Ctx) -> dict:
    d, place, who, fee = c.d(20, 70), c.place(), c.person(), c.money(100, 900)
    return S("decline an invitation to speak", [
        P(f"I cannot speak at {place} on {d.s}", place, d.a, lead=True),
        P("I cannot take on more talks this season"),
        P(f"suggest {who.name} as a speaker", who.name)],
        [f"Thank you for asking me to speak at {place} on {d.s}. I am sorry, but I cannot take on more talks this season.",
         f"I would suggest {who.name}, who would do a fine job. I hope the event goes well."],
        forbid=[F(f"the fee of {fee.s}", fee.a)], subject="Your invitation to speak")


def _dc_meet(c: Ctx) -> dict:
    d1, t1, d2, t2 = c.d(3, 9), c.t(9, 17), c.d(10, 16), c.t(9, 17)
    client = c.org()
    return S("decline a meeting slot", [
        P(f"I cannot make {d1.s} at {t1.s}", d1.a, t1.a, lead=True),
        P("I have a clash with another commitment"),
        P(f"offer {d2.s} at {t2.s} instead", d2.a, t2.a)],
        [f"I am afraid I cannot make {d1.s} at {t1.s}, because I have a clash with another commitment.",
         f"Would {d2.s} at {t2.s} work instead? I would like to give the meeting my full attention."],
        forbid=[F(f"naming the client {client}", client)], subject="Your meeting request")


def _dc_shift(c: Ctx) -> dict:
    d, d2 = c.d(4, 10), c.d(12, 20)
    return S("decline to cover a shift", [
        P(f"I cannot cover the shift on {d.s}", d.a, lead=True),
        P(f"I can take the shift on {d2.s} instead", d2.a),
        P("I know it is short notice")],
        [f"I cannot cover the shift on {d.s}, and I know it is short notice.",
         f"I can take your shift on {d2.s} instead, if that helps."],
        forbid=[F("complaining about the rota", "rota")], subject="About the shift")


def _dc_job(c: Ctx) -> dict:
    m, d = c.money(1500, 8000), c.d(25, 70)
    proj = c.pick(["website rebuild", "translation job", "logo set"])
    return S(f"decline the freelance {proj}", [
        P(f"I cannot take the {proj} for {m.s}", proj, m.a, lead=True),
        P(f"my schedule is full until {d.s}", d.a),
        P("I would happily talk again after that")],
        [f"Thank you for offering me the {proj} for {m.s}. I cannot take it, because my schedule is full until {d.s}.",
         "I would happily talk again after that, if the work is still open."],
        forbid=[F("negotiating the rate", "rate")], subject=f"The {proj}")


def _dc_board(c: Ctx) -> dict:
    d, chair = c.d(10, 40), c.person()
    com = c.pick(["garden committee", "parents' committee", "events committee"])
    return S(f"decline to join the {com}", [
        P(f"I cannot join the {com}", com, lead=True),
        P(f"I can help with one event on {d.s}", d.a),
        P("I value the work they do")],
        [f"Thank you for asking me to join the {com}. I cannot join it, though I value the work they do.",
         f"I would be glad to help with one event, and {d.s} would suit me."],
        forbid=[F(f"criticising the chair {chair.name}", chair.name)], subject=f"The {com}")


DECLINE = [(("friend", "family", "work"), _dc_trip), (("work", "friend", "service"), _dc_talk),
           (("work", "boss", "service"), _dc_meet), (("work", "friend"), _dc_shift),
           (("work", "service"), _dc_job), (("friend", "service", "family"), _dc_board)]


# ---- bad news ------------------------------------------------------------

def _bn_delay(c: Ctx) -> dict:
    code, d, who = c.code("ORD"), c.d(6, 21), c.person()
    return S("tell a customer the order is delayed", [
        P(f"order {code.s} will now arrive on {d.s}", code.a, d.a, lead=True),
        P("a supplier delay caused it"),
        P("offer free delivery on the next order")],
        [f"I am sorry to tell you that order {code.s} will now arrive on {d.s}. A supplier delay caused it, and we could not avoid it.",
         "To make up for the wait, your next order will have free delivery."],
        forbid=[F(f"any blame on {who.name}", who.name)], subject=f"Order {code.s}")


def _bn_cut(c: Ctx) -> dict:
    d, n = c.d(15, 50), c.fig(12, 90)
    proj = c.pick(["translation pilot", "mentoring scheme", "weekend clinic"])
    member = c.person()
    return S(f"tell the team the {proj} is ending", [
        P(f"the {proj} ends on {d.s}", proj, d.a, lead=True),
        P("the budget was cut, not the team's work"),
        P(f"the {n.s} people already enrolled will be supported to the end", n.a)],
        [f"I have difficult news. The {proj} ends on {d.s}, because the budget was cut. This is not a judgement on the team's work.",
         f"The {n.s} people already enrolled will be supported to the end. Thank you for everything you have put into it."],
        forbid=[F(f"naming {member.name} as the one who proposed the cut", member.name)], subject=f"The {proj}")


def _bn_price(c: Ctx) -> dict:
    m1, m2, d = c.money(10, 60), c.money(61, 140), c.d(14, 45)
    return S("tell a client about a price rise", [
        P(f"the monthly price rises from {m1.s} to {m2.s}", m1.a, m2.a, lead=True),
        P(f"the new price starts on {d.s}", d.a),
        P("thank them for their loyalty")],
        [f"I am writing to let you know that the monthly price rises from {m1.s} to {m2.s}. The new price starts on {d.s}.",
         "We do not take this decision lightly, and we are grateful for your loyalty."],
        forbid=[F("blaming inflation", "inflation")], subject="A change to your price")


def _bn_refund(c: Ctx) -> dict:
    m1, m2, code = c.money(40, 180), c.money(181, 400), c.code("CLM")
    return S("tell a customer the refund is partial", [
        P(f"claim {code.s} is refunded {m1.s} of {m2.s}", code.a, m1.a, m2.a, lead=True),
        P("the rest is outside the policy"),
        P("show understanding that this is disappointing")],
        [f"I have reviewed claim {code.s}. We can refund {m1.s} of the {m2.s} you paid, because the rest is outside the policy.",
         "I understand this is disappointing, and I am sorry it is not the answer you hoped for."],
        forbid=[F("the earlier complaint", "complaint")], subject=f"Claim {code.s}")


def _bn_post(c: Ctx) -> dict:
    d1, d2, place = c.d(8, 20), c.d(30, 55), c.place()
    return S("tell friends the event is postponed", [
        P(f"the party at {place} moves from {d1.s} to {d2.s}", place, d1.a, d2.a, lead=True),
        P("the venue is closed for repairs"),
        P("apologise for the change of plans")],
        [f"I am sorry for the change of plans. The party at {place} moves from {d1.s} to {d2.s}, because the venue is closed for repairs.",
         "I hope you can still make it, and I will confirm the details soon."],
        forbid=[F("the insurance dispute", "insurance")], subject="The party has moved")


def _bn_reject(c: Ctx) -> dict:
    d, t = c.d(5, 14), c.t(9, 17)
    role, who = c.pick(["research post", "design lead role", "analyst position"]), c.person()
    return S("tell a candidate they were not selected", [
        P(f"we have not selected them for the {role}", role, lead=True),
        P(f"offer a feedback call on {d.s} at {t.s}", d.a, t.a),
        P("praise the quality of their application")],
        [f"Thank you for applying for the {role}. After careful thought, we have not selected you this time. Your application was strong.",
         f"I would be glad to offer you a feedback call on {d.s} at {t.s}, if you would like one."],
        forbid=[F(f"the name of the chosen candidate {who.name}", who.name)], subject=f"The {role}")


BADNEWS = [(("work", "service"), _bn_delay), (("work", "boss"), _bn_cut), (("work", "service"), _bn_price),
           (("service", "work"), _bn_refund), (("friend", "family"), _bn_post), (("work", "service"), _bn_reject)]


# ---- ask -----------------------------------------------------------------

def _ak_ext(c: Ctx) -> dict:
    d, d2 = c.d(10, 18), c.d(3, 8)
    doc = c.pick(["report", "essay", "lab write-up", "proposal"])
    return S(f"ask for an extension on the {doc}", [
        P(f"ask to extend the {doc} deadline to {d.s}", doc, d.a, lead=True),
        P(f"offer a partial draft by {d2.s}", d2.a),
        P("the reason is that the data arrived late")],
        [f"Could I ask for an extension on the {doc} until {d.s}? The reason is that the data arrived late.",
         f"I can send a partial draft by {d2.s}, so you can see my progress."],
        forbid=[F("blaming the course staff", "staff")], subject=f"An extension for the {doc}")


def _ak_lend(c: Ctx) -> dict:
    d1 = c.d(5, 15)
    d2 = c.reserve_after(d1, 4)
    item = c.pick(["tent", "projector", "ladder", "camera"])
    return S(f"ask to borrow the {item}", [
        P(f"borrow the {item} from {d1.s} to {d2.s}", item, d1.a, d2.a, lead=True),
        P("promise to return it clean")],
        [f"Could I borrow your {item} from {d1.s} to {d2.s}? I will return it clean and in good shape.",
         "It would help me a lot."],
        forbid=[F("the time it broke last year", "broke")], subject=f"Borrowing your {item}")


def _ak_ref(c: Ctx) -> dict:
    d = c.d(12, 30)
    role = c.pick(["research assistant", "program officer", "design intern"])
    n = c.fig(8, 18)
    return S("ask for a reference letter", [
        P(f"ask for a reference letter by {d.s}", d.a, lead=True),
        P(f"it is for a {role} application", role),
        P(f"I worked with them for {n.s} months", n.a)],
        [f"Would you be willing to write me a reference letter by {d.s}? It is for a {role} application.",
         f"We worked together for {n.s} months, so I hope you can speak to what I did. I understand if your schedule is full."],
        forbid=[F("promising a result", "guarantee")], subject="A reference letter")


def _ak_chat(c: Ctx) -> dict:
    d, t = c.d(4, 14), c.t(9, 16)
    topic = c.pick(["team structure", "career options", "the data model", "grant strategy"])
    return S("ask for a short meeting", [
        P(f"a short chat on {d.s} at {t.s}", d.a, t.a, lead=True),
        P(f"the topic is {topic}", topic),
        P("any other time that week is also fine")],
        [f"Could we have a short chat on {d.s} at {t.s}? I would like your advice on {topic}.",
         "Any other time that week is also fine for me."],
        forbid=[F("complaining about colleagues", "colleagues")], subject="A short chat")


def _ak_pay(c: Ctx) -> dict:
    m, code, d = c.money(30, 400), c.code("TRP"), c.d(5, 14)
    return S("ask someone to pay their share", [
        P(f"please pay {m.s} for {code.s}", m.a, code.a, lead=True),
        P(f"by {d.s}", d.a),
        P("thank them for sorting it out")],
        [f"Could you please pay {m.s} for {code.s} by {d.s}? I have made the payments for the whole group.",
         "Thank you for sorting it out."],
        forbid=[F("the earlier dispute", "dispute")], subject=f"Payment for {code.s}")


def _ak_intro(c: Ctx) -> dict:
    who, org, d = c.person(), c.org(), c.d(8, 28)
    return S("ask for an introduction", [
        P(f"ask for an introduction to {who.name} at {org}", who.name, org, lead=True),
        P(f"I would like to speak before {d.s}", d.a),
        P("offer to send a short summary of my work")],
        [f"Could you introduce me to {who.name} at {org}? I would like to speak before {d.s}.",
         "I can send you a short summary of my work to pass on."],
        forbid=[F("asking about salary", "salary")], subject=f"An introduction to {who.first}")


ASK = [(("prof", "boss", "work"), _ak_ext), (("friend", "family", "work"), _ak_lend),
       (("prof", "boss", "work"), _ak_ref), (("boss", "work", "prof"), _ak_chat),
       (("friend", "work", "family"), _ak_pay), (("work", "friend", "boss"), _ak_intro)]


# ---- follow up -----------------------------------------------------------

def _fu_prop(c: Ctx) -> dict:
    sent, d = c.past(9), c.d(4, 12)
    return S("follow up on a proposal", [
        P(f"I sent the proposal on {sent.s}", sent.a),
        P(f"a decision by {d.s} would help my planning", d.a, lead=True),
        P("offer to answer questions")],
        [f"I wanted to check in about the proposal I sent on {sent.s}.",
         f"A decision by {d.s} would help my planning. If you have questions, I am glad to answer them."],
        forbid=[F("any pressure such as a second reminder", "reminder", "still waiting")], subject="The proposal")


def _fu_inv(c: Ctx) -> dict:
    code, m, due, d = c.code("INV"), c.money(200, 3000), c.past(6), c.d(3, 9)
    return S("follow up on an unpaid invoice", [
        P(f"invoice {code.s} for {m.s} was due on {due.s}", code.a, m.a, due.a),
        P(f"ask them to confirm a payment date by {d.s}", d.a, lead=True)],
        [f"I am writing about invoice {code.s} for {m.s}, which was due on {due.s}. It may simply have been missed.",
         f"Could you confirm a payment date by {d.s}? Thank you."],
        forbid=[F("the late-payment penalty", "penalty")], subject=f"Invoice {code.s}")


def _fu_int(c: Ctx) -> dict:
    when, d = c.past(5), c.d(5, 14)
    role = c.pick(["analyst role", "support lead post", "editor position"])
    return S("follow up after an interview", [
        P(f"we spoke on {when.s} about the {role}", when.a, role),
        P("I am still very keen on it", lead=True),
        P(f"ask about the timeline for a decision before {d.s}", d.a)],
        [f"Thank you again for speaking with me on {when.s} about the {role}. I am still very keen on it.",
         f"Could you tell me the timeline for a decision? It would help to know before {d.s}."],
        forbid=[F("mentioning other offers", "offers")], subject=f"The {role}")


def _fu_form(c: Ctx) -> dict:
    code, d = c.code("FRM"), c.d(4, 12)
    sent = c.past(8)
    return S("follow up on a signed form", [
        P(f"form {code.s} was sent on {sent.s}", code.a, sent.a),
        P(f"I need the signed copy by {d.s}", d.a, lead=True)],
        [f"I am following up on form {code.s}, which I sent on {sent.s}. I need the signed copy by {d.s}.",
         "If anything in it is unclear, a quick word would be enough."],
        forbid=[F("referring to my last email", "last email")], subject=f"Form {code.s}")


def _fu_act(c: Ctx) -> dict:
    who, d = c.person(), c.d(4, 12)
    item = c.pick(["the budget draft", "the venue list", "the test plan"])
    return S("follow up after a meeting", [
        P(f"{who.name} was going to send {item}", who.name, item),
        P(f"it would help to have it by {d.s}", d.a, lead=True),
        P("thank them for the useful meeting")],
        [f"Thank you for the useful meeting. {who.name} was going to send {item}.",
         f"It would help to have it by {d.s}, so the rest of us can plan around it."],
        forbid=[F("assigning blame for the delay", "blame")], subject="After the meeting")


def _fu_quote(c: Ctx) -> dict:
    asked, d = c.past(10), c.d(5, 15)
    work = c.pick(["roof repair", "catering", "printing job"])
    return S("follow up on a quote", [
        P(f"I asked for a quote for the {work} on {asked.s}", work, asked.a),
        P(f"I need it by {d.s} to plan", d.a, lead=True),
        P("it does not need to be final")],
        [f"I asked for a quote for the {work} on {asked.s}. I need it by {d.s} to plan.",
         "A rough figure is fine, since it does not need to be final."],
        forbid=[F("any threat to go elsewhere", "elsewhere")], subject=f"The {work} quote")


FOLLOWUP = [(("work", "service", "boss"), _fu_prop), (("work", "service"), _fu_inv), (("work", "service", "prof"), _fu_int),
            (("service", "work", "prof"), _fu_form), (("work", "boss"), _fu_act), (("service", "work"), _fu_quote)]


# ---- apologize (text messages) --------------------------------------------

def _ap_dinner(c: Ctx) -> dict:
    when, d, place = c.past(3), c.d(3, 12), c.place()
    return S("apologise for missing a dinner", [
        P(f"I am sorry I missed dinner on {when.s}", when.a),
        P(f"I want to take them out at {place} on {d.s}", place, d.a, lead=True)],
        [f"I am so sorry I missed dinner on {when.s}. I should have called. Let me take you out at {place} on {d.s}."],
        forbid=[F("an excuse about traffic", "traffic")], form="text", surface="message")


def _ap_bday(c: Ctx) -> dict:
    when, d, t = c.past(2), c.d(2, 6), c.t(17, 21)
    return S("apologise for forgetting a birthday", [
        P(f"I forgot the birthday on {when.s}", when.a),
        P(f"I will call on {d.s} at {t.s}", d.a, t.a, lead=True)],
        [f"I am really sorry I forgot your birthday on {when.s}. It was not fair to you. I will call on {d.s} at {t.s}."],
        forbid=[F("saying I was busy", "busy")], form="text", surface="message")


def _ap_snap(c: Ctx) -> dict:
    when, d, place = c.past(1), c.d(1, 5), c.place()
    return S("apologise for snapping in a meeting", [
        P(f"I snapped at them in the meeting on {when.s}", when.a),
        P(f"I would like to buy a coffee at {place} on {d.s}", place, d.a, lead=True)],
        [f"I am sorry I snapped at you in the meeting on {when.s}. It was out of line. Can I buy you a coffee at {place} on {d.s}?"],
        forbid=[F("blaming stress", "stress")], form="text", surface="message")


def _ap_lost(c: Ctx) -> dict:
    item, m, d = c.pick(["camera", "jacket", "guitar tuner"]), c.money(30, 200), c.d(4, 12)
    return S("apologise for losing a borrowed item", [
        P(f"I lost your {item}", item),
        P(f"I will replace it with a new one worth {m.s}", m.a),
        P(f"I will bring it by {d.s}", d.a, lead=True)],
        [f"I am really sorry, I lost your {item}. I will replace it with a new one worth {m.s}, and I will bring it by {d.s}."],
        forbid=[F("saying someone else lost it", "someone else")], form="text", surface="message")


def _ap_slow(c: Ctx) -> dict:
    when, d = c.past(7), c.d(3, 10)
    return S("apologise for a slow reply", [
        P(f"I did not reply to your message of {when.s}", when.a),
        P(f"the answer is yes to {d.s}", d.a, lead=True)],
        [f"Sorry for the slow reply to your message of {when.s}. The answer is yes to {d.s}, and I am looking forward to it."],
        forbid=[F("a long explanation about my workload", "workload")], form="text", surface="message")


def _ap_cancel(c: Ctx) -> dict:
    when, d, t = c.past(2), c.d(3, 10), c.t(10, 19)
    return S("apologise for cancelling at the last minute", [
        P(f"I cancelled on {when.s} at the last minute", when.a),
        P(f"let us try again on {d.s} at {t.s}", d.a, t.a, lead=True)],
        [f"I am sorry I cancelled on {when.s} at the last minute. That was not fair on you. Can we try again on {d.s} at {t.s}?"],
        forbid=[F("a made-up illness", "flu", "migraine")], form="text", surface="message")


APOLOGIZE = [(("friend", "family"), _ap_dinner), (("friend", "family"), _ap_bday), (("work", "friend"), _ap_snap),
             (("friend", "family"), _ap_lost), (("friend", "work", "family"), _ap_slow), (("friend", "family", "work"), _ap_cancel)]


# ---- feedback ------------------------------------------------------------

def _fb_report(c: Ctx) -> dict:
    total = c.fig(8000, 19000)
    return S("give feedback on a report", [
        P("the sources are missing", "missing sources"),
        P(f"the total of {total.s} is wrong", total.a, lead=True),
        P("section 4 was late", "section 4"),
        P("the summary page is clear and useful")],
        ["Thank you for the report. The summary page is clear and useful, and I can see the effort you put in.",
         f"Three things need fixing. First, there are missing sources. Second, the total of {total.s} is wrong. "
         "Third, section 4 was late.",
         "These are all easy to fix, and I am confident the next version will be strong."],
        forbid=[F('absolute words like "always" or "never"', "always", "never")], subject="Feedback on your report")


def _fb_talk(c: Ctx) -> dict:
    return S("give feedback on a presentation", [
        P("the slides had too much text", "too much text"),
        P("the closing ask was unclear", "closing ask", lead=True),
        P("the timing ran over", "ran over"),
        P("the opening story worked well")],
        ["The opening story worked well, and the room was with you from the start.",
         "Three things to work on. The slides had too much text. The closing ask was unclear, which left people guessing. "
         "The timing ran over, so the questions were rushed.",
         "A shorter deck and a clear last slide would fix most of it."],
        forbid=[F('absolute words like "always" or "never"', "always", "never")], subject="Feedback on your talk")


def _fb_code(c: Ctx) -> dict:
    fn = c.pick(["row parser", "config loader", "file merger"])
    return S("give feedback on a code review", [
        P("there are no tests", "no tests", lead=True),
        P(f"a hard-coded path in the {fn}", fn),
        P("the main function is too long", "too long"),
        P("the naming is clear")],
        ["The naming is clear, and the structure is easy to follow, which makes review much quicker.",
         f"Three things to change before merging. There are no tests. There is a hard-coded path in the {fn}. "
         "The main function is too long to read in one go.",
         "Please send an update when those are done, and I will review it again."],
        forbid=[F('absolute words like "always" or "never"', "always", "never")], subject="Feedback on your change")


def _fb_essay(c: Ctx) -> dict:
    pg = c.fig(12, 98)
    return S("give feedback on an essay draft", [
        P("the thesis is weak", "thesis is weak"),
        P(f"the claim on page {pg.s} has no support", pg.a, lead=True),
        P("the citation style is inconsistent", "citation style"),
        P("the introduction is engaging")],
        ["The introduction is engaging, and your voice comes through clearly.",
         f"I would work on three things. The thesis is weak, so the argument needs a sharper point. The claim on page {pg.s} has no support. "
         "The citation style is inconsistent.",
         "Fixing these will make the essay much stronger, and I am happy to read another draft."],
        forbid=[F('absolute words like "always" or "never"', "always", "never")], subject="Feedback on your draft")


def _fb_design(c: Ctx) -> dict:
    return S("give feedback on a design mock", [
        P("the text has low contrast", "low contrast"),
        P("the button labels are unclear", "button labels", lead=True),
        P("the mobile layout breaks", "mobile layout"),
        P("the colour palette is pleasant")],
        ["The colour palette is pleasant, and the overall direction is right.",
         "Three problems to solve. The text has low contrast. The button labels are unclear. The mobile layout breaks on small screens.",
         "Each one is quick to fix, and I would be glad to look at the next round."],
        forbid=[F('absolute words like "always" or "never"', "always", "never")], subject="Feedback on the mock")


def _fb_meet(c: Ctx) -> dict:
    t = c.t(16, 19)
    return S("give feedback on how a meeting was run", [
        P("the meeting had no agenda", "no agenda", lead=True),
        P(f"it ran over until {t.s}", t.a),
        P("nobody took notes", "notes"),
        P("you kept everyone calm and fair")],
        ["You kept everyone calm and fair, which is hard to do in a tense room.",
         f"Three things to change. The meeting had no agenda. It ran over until {t.s}. Nobody took notes, so the decisions are already fuzzy.",
         "A short agenda sent the day before would solve most of this."],
        forbid=[F('absolute words like "always" or "never"', "always", "never")], subject="Feedback on the meeting")


FEEDBACK = [(("work", "friend", "prof"), _fb_report), (("work", "friend"), _fb_talk), (("work",), _fb_code),
            (("friend", "work", "prof"), _fb_essay), (("work", "friend"), _fb_design), (("work", "boss", "friend"), _fb_meet)]


# ==========================================================================
# W6 audience and register: ten briefs, four drafts each
# ==========================================================================

AUDIENCES = W.AUDIENCES        # manager, friend, customer, professor


def _D(mgr, fr, cu, pr) -> dict:
    return {"manager": mgr, "friend": fr, "customer": cu, "professor": pr}


def _w_late(c: Ctx) -> dict:
    nd, da = c.d(3, 9), c.past(4)
    return {"purpose": "tell them the report will be late", "subject": "Report timing",
            "points": [P(f"the report will now be ready on {nd.s}", nd.a, lead=True),
                       P(f"the dataset only arrived on {da.s}", da.a),
                       P("apologise for the delay")],
            "drafts": _D(
                [f"The report will now be ready on {nd.s}. The dataset only arrived on {da.s}, and I need time to check it properly.",
                 "I apologise for the delay and will tell you at once if the date changes."],
                [f"Heads up, my report is going to be late. It'll be ready on {nd.s}, because the dataset only turned up on {da.s}.",
                 "Sorry about that, I'm on it."],
                [f"I am writing to let you know that your report will now be ready on {nd.s}. The dataset only reached us on {da.s}, and we need time to check it carefully.",
                 "I apologise for the delay and for any inconvenience it causes."],
                [f"I am writing to let you know that my report will be ready on {nd.s}. The dataset only arrived on {da.s}, and I would like to analyse it carefully.",
                 "I apologise for the delay."])}


def _w_price(c: Ctx) -> dict:
    m1, m2, d = c.money(8, 30), c.money(31, 70), c.d(10, 45)
    return {"purpose": "tell them the workspace subscription price is changing", "subject": "Subscription price change",
            "points": [P(f"the monthly price rises from {m1.s} to {m2.s}", m1.a, m2.a, lead=True),
                       P(f"the new price applies from {d.s}", d.a),
                       P("thank them for staying with us")],
            "drafts": _D(
                [f"The monthly price of the workspace subscription rises from {m1.s} to {m2.s}, and I would like your approval to tell members.",
                 f"The new price applies from {d.s}. I would also like to thank members for staying with us."],
                [f"Quick heads up: the monthly price of the workspace subscription is going up from {m1.s} to {m2.s}. It starts from {d.s}.",
                 "Thanks a lot for sticking with us, it really means something."],
                [f"I am writing to inform you that the monthly price of your workspace subscription rises from {m1.s} to {m2.s}. The new price applies from {d.s}.",
                 "We are grateful that you have stayed with us, and we regret any inconvenience."],
                [f"I am writing to inform you that the monthly price of the workspace subscription rises from {m1.s} to {m2.s}. The new price applies from {d.s}.",
                 "Thank you for staying with us."])}


def _w_sched(c: Ctx) -> dict:
    d1, t1, d2, t2 = c.d(2, 6), c.t(9, 12), c.d(8, 14), c.t(13, 17)
    return {"purpose": "tell them the meeting has moved", "subject": "Meeting moved",
            "points": [P(f"the meeting moves from {d1.s} at {t1.s} to {d2.s} at {t2.s}", d1.a, t1.a, d2.a, t2.a, lead=True),
                       P("the earlier room is no longer free"),
                       P("ask them to confirm they can attend")],
            "drafts": _D(
                [f"The meeting moves from {d1.s} at {t1.s} to {d2.s} at {t2.s}. The earlier room is no longer free.",
                 "Please confirm that you can attend."],
                [f"The meeting is moving from {d1.s} at {t1.s} to {d2.s} at {t2.s}, since the earlier room isn't free any more.",
                 "Can you make it? Tell me when you know."],
                [f"I am writing to tell you that your meeting moves from {d1.s} at {t1.s} to {d2.s} at {t2.s}. The earlier room is no longer free.",
                 "Please confirm that you are able to attend."],
                [f"I am writing to tell you that our meeting moves from {d1.s} at {t1.s} to {d2.s} at {t2.s}. The earlier room is no longer free.",
                 "Please confirm that you are able to attend."])}


def _w_away(c: Ctx) -> dict:
    d1 = c.d(8, 18)
    d2 = c.reserve_after(d1, 6)
    who = c.person()
    return {"purpose": "tell them I am away and who will cover", "subject": "Away and cover",
            "points": [P(f"I am away from {d1.s} to {d2.s}", d1.a, d2.a, lead=True),
                       P(f"{who.name} will cover for me", who.name),
                       P("I will reply when I am back")],
            "drafts": _D(
                [f"I am away from {d1.s} to {d2.s}, and {who.name} will cover for me.",
                 "I will reply to everything else when I am back."],
                [f"I'm away from {d1.s} to {d2.s}, so {who.name} will cover for me.",
                 "I'll reply to everything when I'm back."],
                [f"I am writing to let you know that I am away from {d1.s} to {d2.s}. {who.name} will cover for me during that time.",
                 "I will reply to your message when I am back."],
                [f"I am writing to let you know that I am away from {d1.s} to {d2.s}. {who.name} will cover for me during that time.",
                 "I will reply to your message when I am back."])}


def _w_venue(c: Ctx) -> dict:
    p1, p2, d, t = c.place(), c.place(), c.d(5, 20), c.t(10, 18)
    while p2 == p1:
        p2 = c.place()
    return {"purpose": "tell them the venue has changed", "subject": "New venue",
            "points": [P(f"the event moves from {p1} to {p2}", p1, p2, lead=True),
                       P(f"it is still on {d.s} at {t.s}", d.a, t.a),
                       P("the first venue had a booking problem")],
            "drafts": _D(
                [f"The event moves from {p1} to {p2}. It is still on {d.s} at {t.s}.",
                 "The first venue had a booking problem, so I have settled the change already."],
                [f"The event is moving from {p1} to {p2}, because the first venue had a booking problem. Same day, same time: {d.s} at {t.s}.",
                 "See you there!"],
                [f"I am writing to tell you that the event moves from {p1} to {p2}. It is still on {d.s} at {t.s}.",
                 "The first venue had a booking problem, and we apologise for the change."],
                [f"I am writing to tell you that the event moves from {p1} to {p2}. It is still on {d.s} at {t.s}.",
                 "The first venue had a booking problem, and I apologise for the change."])}


def _w_pay(c: Ctx) -> dict:
    m, d, code = c.money(150, 1800), c.d(6, 18), c.code("INV")
    return {"purpose": "tell them a payment will come a week late", "subject": "Payment date",
            "points": [P(f"I will pay {m.s} for {code.s} on {d.s}", m.a, code.a, d.a, lead=True),
                       P("it is a week later than planned"),
                       P("the delay is due to a bank transfer limit")],
            "drafts": _D(
                [f"I will pay {m.s} for {code.s} on {d.s}, which is a week later than planned.",
                 "The delay is due to a bank transfer limit, and I have already raised it with the bank."],
                [f"I'll pay you the {m.s} for {code.s} on {d.s}. That's a week later than planned, sorry.",
                 "It's down to a bank transfer limit, nothing else."],
                [f"I am writing to confirm that payment of {m.s} for {code.s} will be made on {d.s}, which is a week later than planned.",
                 "The delay is due to a bank transfer limit, and we apologise for it."],
                [f"I am writing to confirm that I will pay {m.s} for {code.s} on {d.s}, which is a week later than planned.",
                 "The delay is due to a bank transfer limit, and I apologise for it."])}


def _w_order(c: Ctx) -> dict:
    code, d = c.code("ORD"), c.d(4, 16)
    return {"purpose": "tell them an order is delayed", "subject": "Order delay",
            "points": [P(f"order {code.s} will now arrive on {d.s}", code.a, d.a, lead=True),
                       P("the supplier ran out of stock"),
                       P("offer free delivery on the next order")],
            "drafts": _D(
                [f"Order {code.s} will now arrive on {d.s}. The supplier ran out of stock.",
                 "I suggest we offer free delivery on the next order to make up for it."],
                [f"Your order {code.s} is going to arrive on {d.s} now, because the supplier ran out of stock.",
                 "Next order has free delivery, to say sorry."],
                [f"I am writing to tell you that order {code.s} will now arrive on {d.s}. The supplier ran out of stock.",
                 "To make up for this, your next order will have free delivery."],
                [f"I am writing to tell you that order {code.s} will now arrive on {d.s}. The supplier ran out of stock.",
                 "I will arrange free delivery on the next order."])}


def _w_hand(c: Ctx) -> dict:
    who, code, d = c.person(), c.code("PRJ"), c.d(3, 12)
    return {"purpose": "tell them who takes over a project", "subject": "Project handover",
            "points": [P(f"{who.name} takes over project {code.s} on {d.s}", who.name, code.a, d.a, lead=True),
                       P("I remain available for questions this month"),
                       P("the work is in good shape")],
            "drafts": _D(
                [f"{who.name} takes over project {code.s} on {d.s}. The work is in good shape.",
                 "I remain available for questions this month."],
                [f"{who.name} is taking over project {code.s} on {d.s}. It's in good shape.",
                 "I'm around for questions this month if you need me."],
                [f"I am writing to tell you that {who.name} takes over project {code.s} on {d.s}. The work is in good shape.",
                 "I remain available for questions this month."],
                [f"I am writing to tell you that {who.name} takes over project {code.s} on {d.s}. The work is in good shape.",
                 "I remain available for questions this month."])}


def _w_miss(c: Ctx) -> dict:
    place, d, d2 = c.place(), c.d(5, 12), c.d(13, 18)
    return {"purpose": "tell them I cannot attend an event", "subject": "Cannot attend",
            "points": [P(f"I cannot attend the event at {place} on {d.s}", place, d.a, lead=True),
                       P(f"I will send my notes by {d2.s}", d2.a),
                       P("I am sorry to miss it")],
            "drafts": _D(
                [f"I cannot attend the event at {place} on {d.s}. I will send my notes by {d2.s}.",
                 "I am sorry to miss it, and I will make sure nothing is dropped."],
                [f"I can't make the event at {place} on {d.s}. I'll send my notes by {d2.s}.",
                 "I'm really sorry to miss it."],
                [f"I am writing to say that I cannot attend the event at {place} on {d.s}. I will send my notes by {d2.s}.",
                 "I am sorry to miss it."],
                [f"I am writing to say that I cannot attend the event at {place} on {d.s}. I will send my notes by {d2.s}.",
                 "I am sorry to miss it."])}


def _w_drive(c: Ctx) -> dict:
    d, t1, t2 = c.d(3, 10), c.t(17, 18), c.t(20, 23)
    return {"purpose": "tell them the shared drive will be offline", "subject": "Shared drive offline",
            "points": [P(f"the shared drive is offline on {d.s} from {t1.s} to {t2.s}", d.a, t1.a, t2.a, lead=True),
                       P("save all open work beforehand"),
                       P("it is for maintenance")],
            "drafts": _D(
                [f"The shared drive will be offline on {d.s} from {t1.s} to {t2.s}. It is for maintenance.",
                 "Please save all open work beforehand."],
                [f"The shared drive is going offline on {d.s} from {t1.s} to {t2.s} for maintenance.",
                 "Save everything you have open before then!"],
                [f"I am writing to tell you that the shared drive will be offline on {d.s} from {t1.s} to {t2.s}. It is for maintenance.",
                 "Please save all open work beforehand."],
                [f"I am writing to tell you that the shared drive will be offline on {d.s} from {t1.s} to {t2.s}. It is for maintenance.",
                 "Please save all open work beforehand."])}


BRIEFS = [_w_late, _w_price, _w_sched, _w_away, _w_venue, _w_pay, _w_order, _w_hand, _w_miss, _w_drive]

_W6_CAT = {"manager": "boss", "friend": "friend", "customer": None, "professor": "prof"}


def _w6_recipient(c: Ctx, aud: str) -> None:
    cat = _W6_CAT[aud]
    if c.profile and cat:
        pool = [ct for ct in W.contacts(c.profile) if cat_of(ct["relation"]) == cat
                and not re.search(r"teaching assistant|\bTA\b", ct["relation"], re.I)]
        if pool:
            ct = sorted(pool, key=lambda x: x["id"])[0]
            c.cat = cat
            c.rcp = {"name": _clean_name(ct.get("full_name") or ct["name"]), "relation": ct["relation"],
                     "short": _clean_name(ct["name"]), "emails": ct.get("emails") or [], "id": ct["id"]}
            return
    o = c.others.pop(0)
    c.cat = cat or "work"
    c.rcp = {"name": o["name"], "relation": aud, "short": o["first"], "emails": [], "id": None}


def gen_register(seed: int, *, index: int, profile: str | None = None) -> list[dict]:
    brief = BRIEFS[index % len(BRIEFS)]
    group = "W6-GRP-" + hashlib.sha256(f"{seed}:{index}".encode()).hexdigest()[:8]
    cases = []
    for k, aud in enumerate(AUDIENCES):
        c = Ctx(seed, profile)                     # same seed: the same facts in all four
        spec_b = brief(c)
        _w6_recipient(c, aud)
        first = c.sender["preferred_name"]
        greeting = {"manager": _greeting(c.rcp, "boss"), "friend": f"Hey {c.rcp['short'].split()[0]},",
                    "customer": f"Dear {c.rcp['name']},", "professor": _greeting(c.rcp, "prof")}[aud]
        if aud == "friend" and c.profile:
            greeting = _greeting(c.rcp, "friend").replace("Hi ", "Hey ")
        if c.profile:
            signoff = W.PROFILE_SIGNOFF[c.profile]
        else:
            signoff = {"manager": f"Thanks,\n{first}", "friend": f"Cheers,\n{first}",
                       "customer": f"Kind regards,\n{first}", "professor": f"Kind regards,\n{first}"}[aud]
        spec = S(spec_b["purpose"], [dict(p) for p in spec_b["points"]], spec_b["drafts"][aud],
                 subject=spec_b["subject"])
        cases.append(_assemble(c, "W6", "register", spec, seed=seed, audience=aud, group_id=group, greeting=greeting,
                               signoff=signoff, case_id_seed=seed * 8 + k))
    return cases


# ==========================================================================
# Registry
# ==========================================================================

def _gen(sub: str, kind: str, scen: list) -> Callable[..., dict]:
    def gen(seed: int, *, index: int = 0, profile: str | None = None) -> dict:
        return _make(sub, kind, scen, seed, index, profile)
    gen.__name__ = f"gen_{sub.lower()}_{kind}"
    return gen


SCENARIOS = {("W1", "email"): EMAIL, ("W1", "reply"): REPLY, ("W1", "text"): TEXT, ("W1", "note"): NOTE,
             ("W1", "invite"): INVITE, ("W1", "section"): SECTION, ("W1", "bio"): BIO,
             ("W2", "decline"): DECLINE, ("W2", "bad_news"): BADNEWS, ("W2", "ask"): ASK,
             ("W2", "follow_up"): FOLLOWUP, ("W2", "apologize"): APOLOGIZE, ("W2", "feedback"): FEEDBACK}

GENERATORS: dict[tuple[str, str], Callable] = {k: _gen(k[0], k[1], v) for k, v in SCENARIOS.items()}
GENERATORS[("W1", "reply")] = gen_reply
GENERATORS[("W6", "register")] = gen_register
