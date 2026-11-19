"""Documents: letters, memos, reports, policies, newsletters, slides and dense pages (spec 05 §7.1).

Planted fields: names, dates, clause numbers and clause content, figures (money, percentages,
counts, days), places and section headings. Question types: lookup (including "section
finding": which heading a stated fact appears under, answered with the heading text), and the
full reading. The three dense templates hold about 350, 600 and 900 words of 9-10 px body text;
planted values inside them stay at 11 px.

Body prose comes from a seeded phrase generator (invented civic and office prose); no real text
is copied. Each fact topic appears exactly once per document, so every section-finding question
has one answer.

Nine templates on purpose: the generator picks template = index % len(TEMPLATES), and a count
coprime with 10 makes every template meet every theme, scale and abstention slot.
"""
from __future__ import annotations

import random
from datetime import date

from edat.media.common import Inventor, MONTHS, canon_text, date_long
from edat.media.vision.canvas import Canvas, font
from edat.media.vision.items import ABSTAIN_ANSWER, Question, Rendered

FAMILY = "documents"
TEMPLATES = ("letter", "report", "policy", "columns", "slide", "dense_350", "dense_600",
             "dense_900", "memo")
LOGICAL_SIZES = {"letter": (612, 792), "report": (612, 792), "policy": (612, 792),
                 "columns": (816, 1056), "slide": (1280, 720), "dense_350": (612, 792),
                 "dense_600": (816, 1056), "dense_900": (816, 1056), "memo": (612, 792)}
BASE_DATE = date(2027, 1, 4)
FIELD_MIN = 11


def render(rng: random.Random, *, template: str, theme: str, scale: float,
           abstain: bool) -> Rendered:
    if template not in TEMPLATES:
        raise ValueError(f"unknown template {template!r}")
    if template == "slide":
        return _slide(rng, theme=theme, scale=scale, abstain=abstain)
    return _page(rng, template, theme=theme, scale=scale, abstain=abstain)


# ============================================================================ prose generator
_SUBJ = ("The committee", "The review panel", "Our facilities team", "The steering group",
         "Each department", "The working group", "The board", "The staff council",
         "The finance office", "The program lead", "The planning desk",
         "The volunteer coordinator", "The operations team", "Our partner network",
         "The records office")       # all singular, to agree with the verbs below
_VERB = ("will review", "has approved", "continues to monitor", "plans to expand",
         "has completed", "is preparing", "will publish", "recommends changes to",
         "intends to simplify", "has postponed", "will consult members on", "is updating",
         "has shared a draft of", "will report back on")
_OBJ = ("the shared parking schedule", "the volunteer training plan", "the maintenance budget",
        "the updated travel guidelines", "the records retention process",
        "the quarterly safety audit", "the new intake procedure", "the room booking system",
        "the equipment loan scheme", "the noise reduction measures", "the visitor guidance",
        "the cleaning rota", "the members handbook", "the storage arrangements",
        "the complaints procedure", "the meeting calendar", "the supplier contracts",
        "the accessibility checklist", "the newsletter format", "the waiting list rules")
_TAIL = ("before the spring session", "over the next two quarters",
         "in line with earlier recommendations", "after consultation with residents",
         "so that requests are handled more quickly", "pending a final vote",
         "with support from local partners", "as resources allow",
         "to reduce delays for visitors", "following feedback from members",
         "ahead of the annual meeting", "once the survey results are in",
         "at the next regular meeting", "without changing current opening hours")
_GROUP = ("residents", "members", "staff", "volunteers", "visitors", "local businesses",
          "parents", "tenants")
_ADJ = ("clear", "steady", "encouraging", "mixed", "positive", "useful", "consistent")


def _filler(r: random.Random) -> str:
    form = r.randrange(7)
    obj = r.choice(_OBJ)
    if form <= 2:
        return f"{r.choice(_SUBJ)} {r.choice(_VERB)} {obj} {r.choice(_TAIL)}."
    if form == 3:
        return f"Feedback from {r.choice(_GROUP)} suggests that {obj} is working well."
    if form == 4:
        return f"Further details on {obj} will be shared {r.choice(_TAIL)}."
    if form == 5:
        return f"Early results on {obj} are {r.choice(_ADJ)}, and work will continue {r.choice(_TAIL)}."
    return f"No changes to {obj} are expected {r.choice(_TAIL)}."


# ============================================================================ planted facts
_TOPICS = ("the riverside cleanup", "the evening shuttle pilot", "the youth mentoring scheme",
           "the seed library", "the tool lending program", "the winter coat drive",
           "the bike repair clinic", "the senior lunch club", "the job skills workshop",
           "the rooftop solar study", "the after-school reading hour", "the market stall permits",
           "the green waste collection", "the open studio weekend",
           "the language exchange evenings", "the community fridge", "the tree planting day",
           "the repair cafe", "the hall refurbishment", "the swim lesson subsidy")
# kind -> forms of (prefix, suffix, question); {t} is the topic, {T} the topic capitalised.
_FORMS = {
    "money": (("The budget for {t} is set at", "for the coming year.",
               "What budget does the document give for {t}?"),
              ("Spending on {t} reached", "by the end of the last quarter.",
               "How much was spent on {t}?")),
    "percent": (("Participation in {t} rose to", "this year.",
                 "What participation rate does the document report for {t}?"),
                ("Satisfaction with {t} stood at", "in the latest survey.",
                 "What satisfaction rate does the document give for {t}?")),
    "count": (("A total of", "households took part in {t}.",
               "How many households took part in {t}?"),
              ("{T} received", "applications in its first month.",
               "How many applications did {t} receive in its first month?")),
    "date": (("Registration for {t} closes on", ".",
              "On what date does registration for {t} close?"),
             ("The next phase of {t} begins on", ".",
              "On what date does the next phase of {t} begin?")),
    "person": (("", "will coordinate {t} from the main office.",
                "Who will coordinate {t}?"),
               ("Questions about {t} should go to", ", the program lead.",
                "Who should questions about {t} go to?")),
    "place": (("Sessions for {t} will be held at", ".",
               "Where will sessions for {t} be held?"),
              ("{T} will move to", "in the autumn.",
               "Where will {t} move to in the autumn?")),
    "days": (("Requests for {t} must be made at least", "days in advance.",
              "How many days in advance must requests for {t} be made?"),
             ("Bookings for {t} open", "days before each session.",
              "How many days before each session do bookings for {t} open?")),
}
_CANON = {"money": "money", "percent": "number", "count": "number", "date": "date",
          "person": "text", "place": "text", "days": "number"}
_PLACE_KIND = ("Hall", "Community Center", "Annex", "Pavilion", "Library", "Studio")

_POLICY_HEADINGS = ("Access", "Visitors", "Bookings", "Safety", "Conduct", "Shared Equipment",
                    "Reporting", "Deliveries", "Parking", "Events", "Cleaning")
_HEADINGS = ("Background", "Budget and Funding", "Schedule", "Staffing", "Next Steps",
             "Community Feedback", "Facilities", "Risks", "Outreach", "Partnerships",
             "Training", "Safety", "Timeline", "Contacts", "Program Updates", "Membership",
             "Transport", "Grants", "Events", "Volunteering")


def _seg(text: str) -> tuple:
    return (text, None)


def _make_fact(r: random.Random, inv: Inventor, kind: str, topic: str, name: str,
               used: set) -> dict:
    prefix, suffix, question = r.choice(_FORMS[kind])
    if kind == "money":
        amt = r.randrange(12, 980) * 100
        shown, value = f"${amt:,}", f"{amt:.2f}"
    elif kind == "percent":
        n = r.randint(12, 97)
        shown, value = f"{n}%", str(n)
    elif kind == "count":
        n = r.randint(40, 4800)
        shown, value = f"{n:,}", str(n)
    elif kind == "date":
        d = inv.day(BASE_DATE, 600)
        shown, value = f"{MONTHS[d.month - 1]} {d.day}, {d.year}", d.isoformat()
    elif kind == "person":
        shown = inv.person()
        while shown in used:
            shown = inv.person()
        value = shown
    elif kind == "place":
        shown = f"{inv.surname()} {r.choice(_PLACE_KIND)}"
        while shown in used:
            shown = f"{inv.surname()} {r.choice(_PLACE_KIND)}"
        value = shown
    else:
        n = r.choice((3, 5, 7, 10, 14, 21, 30))
        shown, value = str(n), str(n)
    used.add(shown)
    t, tc = topic, topic[0].upper() + topic[1:]
    prefix = prefix.format(t=t, T=tc)
    suffix = suffix.format(t=t, T=tc)
    field_text = shown
    if suffix and suffix[0] in ".,;":      # keep trailing punctuation with the value
        field_text += suffix[0]
        suffix = suffix[1:].strip()
    segs = []
    if prefix:
        segs.append(_seg(prefix))
    segs.append((field_text, {"field": name, "kind": _CANON[kind], "value": value}))
    if suffix:
        segs.append(_seg(suffix))
    return {"name": name, "kind": kind, "canon": _CANON[kind], "topic": topic, "shown": shown,
            "value": value, "question": question.format(t=t, T=tc), "segs": segs,
            "filler": False}


# ============================================================================ flow layout
class _Flow:
    """Places words in columns; with ``draw`` False it only measures (for fitting)."""

    def __init__(self, c: Canvas, cols: list[tuple[float, float]], top: float, bottom: float,
                 draw: bool, color="fg") -> None:
        self.c, self.cols, self.top, self.bottom, self.draw = c, cols, top, bottom, draw
        self.ci, self.y, self.ok, self.color = 0, top, True, color
        self.fields_drawn: list[str] = []

    @property
    def x(self) -> float:
        return self.cols[self.ci][0]

    @property
    def width(self) -> float:
        return self.cols[self.ci][1]

    def w(self, s: str, size: float, bold: bool = False) -> float:
        c = self.c
        extra = 2 * c._stroke(bold)
        return (font(c.font_px(size)).getlength(s) + extra) / c.scale

    def room(self, h: float) -> bool:
        if not self.ok:
            return False
        if self.y + h <= self.bottom:
            return True
        if self.ci + 1 < len(self.cols):
            self.ci += 1
            self.y = self.top
            return True
        self.ok = False
        return False

    def gap(self, h: float) -> None:
        if self.y > self.top:
            self.y += h

    def heading(self, s: str, size: float, *, field: str | None = None, bold: bool = True,
                color="fg", after: float = 6) -> None:
        if not self.room(size * 1.4 + 2 * size):   # keep a heading with its first lines
            return
        if self.draw:
            self.c.text(self.x, self.y, s, size=size, bold=bold, color=color, field=field)
            if field:
                self.fields_drawn.append(field)
        self.y += size * 1.3 + after

    def para(self, sents: list[dict], size: float, lh: float, *, indent: float = 0,
             lead: tuple | None = None) -> None:
        """Flow sentences. ``lead`` = (text, field, size): a clause number hung in the margin."""
        atoms: list[tuple[str, dict | None]] = []
        for s in sents:
            for text, fd in s["segs"]:
                if fd is None:
                    atoms.extend((wd, None) for wd in text.split())
                else:
                    atoms.append((text, fd))
        if not atoms:
            return
        fsize = max(size, FIELD_MIN)
        space = self.w(" ", size)
        lines: list[list[tuple[str, dict | None]]] = []
        cur: list = []
        cur_w = 0.0
        avail = self.width - indent
        for a in atoms:
            aw = self.w(a[0], fsize if a[1] else size)
            add = aw + (space if cur else 0)
            if cur and cur_w + add > avail:
                lines.append(cur)
                cur, cur_w = [a], aw
            else:
                cur.append(a)
                cur_w += add
        if cur:
            lines.append(cur)
        first = True
        for ln in lines:
            line_h = lh if not any(a[1] for a in ln) else max(lh, fsize * 1.32)
            if not self.room(line_h):
                return
            base = self.y + line_h * 0.78
            x = self.x + indent
            if first and lead is not None and self.draw:
                text, fname, lsize = lead
                self.c.text(self.x, base, text, size=lsize, bold=True, anchor="ls", field=fname,
                            color=self.color)
                if fname:
                    self.fields_drawn.append(fname)
            first = False
            run: list[str] = []

            def flush() -> None:
                nonlocal x, run
                if run:
                    s = " ".join(run)
                    if self.draw:
                        self.c.text(x, base, s, size=size, anchor="ls", color=self.color)
                    x += self.w(s, size) + space
                    run = []

            for text, fd in ln:
                if fd is None:
                    run.append(text)
                    continue
                flush()
                if self.draw:
                    self.c.text(x, base, text, size=fsize, anchor="ls", field=fd["field"],
                                kind=fd["kind"], value=fd["value"], color=self.color)
                    self.fields_drawn.append(fd["field"])
                x += self.w(text, fsize) + space
            flush()
            self.y += line_h


def _fit(c: Canvas, layout, model: dict) -> None:
    """Drop filler sentences until ``layout(c, model, draw=False)`` fits, then draw."""
    for _ in range(400):
        if layout(c, model, False):
            break
        best = None
        for sec in model["sections"]:
            for para in sec["paras"]:
                n = sum(1 for s in para["sents"] if s["filler"])
                if n and (best is None or n > best[0]):
                    best = (n, para)
        if best is None:
            break
        para = best[1]
        for i in range(len(para["sents"]) - 1, -1, -1):
            if para["sents"][i]["filler"]:
                del para["sents"][i]
                break
    layout(c, model, True)


def _word_count(model: dict) -> int:
    n = 0
    for sec in model["sections"]:
        n += len((sec.get("heading") or "").split())
        for para in sec["paras"]:
            for s in para["sents"]:
                n += sum(len(t.split()) for t, _ in s["segs"])
    return n


# ============================================================================ document model
def _org(inv: Inventor, r: random.Random) -> str:
    kind = r.choice(("Community Council", "Housing Cooperative", "Library Board", "Parks Trust",
                     "Residents Association", "Arts Collective", "Water Users Group",
                     "Transit Forum", "Garden Society"))
    return f"{inv.town()} {kind}"


def _build_sections(r: random.Random, inv: Inventor, *, n_sections: int, facts_per: list[int],
                    filler_per_para: tuple[int, int], paras_per: tuple[int, int],
                    numbered: bool, headings: bool, used: set, words: int | None = None,
                    clauses: bool = False) -> tuple[list[dict], list[dict], list[dict]]:
    heads = r.sample(_POLICY_HEADINGS if clauses else _HEADINGS, n_sections)
    if clauses:
        heads[0] = "Purpose"
    topics = r.sample(_TOPICS, sum(facts_per))
    kinds = list(_FORMS)
    sections, facts, clause_list = [], [], []
    ti = 0
    obligations = r.sample(_OBLIGATIONS, min(len(_OBLIGATIONS), n_sections * 3)) if clauses else []
    oi = 0
    for si in range(n_sections):
        sec = {"heading": None, "heading_field": None, "plain": heads[si], "paras": []}
        if headings:
            sec["heading"] = f"{si + 1}. {heads[si]}" if numbered else heads[si]
            sec["heading_field"] = f"heading_{si}"
        np_ = r.randint(*paras_per)
        paras = [{"sents": [], "clause": None} for _ in range(np_)]
        for p in paras:
            for _ in range(r.randint(*filler_per_para)):
                p["sents"].append({"segs": [_seg(_filler(r))], "filler": True})
        for k in range(facts_per[si]):
            kind = r.choice(kinds)
            f = _make_fact(r, inv, kind, topics[ti], f"fact_{ti}", used)
            f["section"] = si
            ti += 1
            p = r.choice(paras)
            p["sents"].insert(r.randint(0, len(p["sents"])), f)
            facts.append(f)
        if clauses and si > 0:
            for k, p in enumerate(paras):
                if oi < len(obligations):
                    who, what = obligations[oi]
                    oi += 1
                    num = f"{si + 1}.{k + 1}"
                    cname = f"clause_{si}_{k}"
                    p["clause"] = (num, cname + "_no")
                    p["sents"].insert(0, {"segs": [_seg(f"{who} must"),
                                                   (what + ".", {"field": cname + "_text",
                                                                 "kind": "text",
                                                                 "value": what})],
                                          "filler": False})
                    clause_list.append({"number": num, "who": who, "what": what, "section": si,
                                        "no_field": cname + "_no", "text_field": cname + "_text"})
        sec["paras"] = paras
        sections.append(sec)
    if words:                     # top up with filler towards the target word count
        model = {"sections": sections}
        guard = 0
        while _word_count(model) < words and guard < 400:
            sec = r.choice(sections)
            r.choice(sec["paras"])["sents"].append({"segs": [_seg(_filler(r))], "filler": True})
            guard += 1
    return sections, facts, clause_list


_OBLIGATIONS = (("Visitors", "sign in at the front desk"),
                ("Contractors", "wear an identification badge at all times"),
                ("Staff", "report spills to the facilities desk"),
                ("Members", "book meeting rooms at least two days ahead"),
                ("Volunteers", "complete safety training before their first shift"),
                ("Drivers", "park only in marked bays"),
                ("Tenants", "keep fire exits clear"),
                ("Event hosts", "return keys to reception the next morning"),
                ("Supervisors", "keep a log of first aid incidents"),
                ("Room users", "switch off lights in empty rooms"),
                ("Applicants", "provide two forms of contact details"),
                ("Pet owners", "keep dogs on a short lead indoors"),
                ("Cyclists", "store bicycles in the rear shed"),
                ("Caterers", "label every dish with its ingredients"),
                ("Instructors", "check the equipment before each class"),
                ("Group leaders", "carry a printed list of participants"))


# ============================================================================ page templates
def _page(rng: random.Random, template: str, *, theme: str, scale: float,
          abstain: bool) -> Rendered:
    inv = Inventor(rng)
    w, h = LOGICAL_SIZES[template]
    c = Canvas(w, h, scale=scale, theme=theme)
    used: set = set()
    org = _org(inv, rng)
    doc_date = inv.day(BASE_DATE, 600)
    meta: dict = {"org": org, "date": doc_date.isoformat()}
    clauses: list[dict] = []
    if template == "letter":
        sections, facts, _ = _build_sections(rng, inv, n_sections=1, facts_per=[rng.randint(2, 3)],
                                             filler_per_para=(2, 4), paras_per=(3, 5),
                                             numbered=False, headings=False, used=used)
        meta.update(sender=inv.person(), recipient=inv.person(),
                    ref=f"{inv.code(2, 0)}-{rng.randint(100, 999)}",
                    role=rng.choice(("Secretary", "Program Manager", "Chair", "Treasurer",
                                     "Office Coordinator")))
        body, lh, top = 12, 17, 270
    elif template == "memo":
        n = rng.randint(3, 4)
        sections, facts, _ = _build_sections(rng, inv, n_sections=n,
                                             facts_per=_spread(rng, n, rng.randint(3, 4)),
                                             filler_per_para=(1, 3), paras_per=(1, 2),
                                             numbered=False, headings=True, used=used)
        meta.update(sender=inv.person(), recipient=f"All {rng.choice(_GROUP)}",
                    subject=f"Update on {rng.choice(_OBJ)[4:]}")
        body, lh, top = 12, 17, 230
    elif template == "report":
        n = rng.randint(3, 4)
        sections, facts, _ = _build_sections(rng, inv, n_sections=n,
                                             facts_per=_spread(rng, n, rng.randint(3, 5)),
                                             filler_per_para=(1, 2), paras_per=(1, 2),
                                             numbered=True, headings=True, used=used)
        meta.update(author=inv.person(),
                    title=f"{rng.choice(('Annual', 'Quarterly', 'Interim', 'Progress'))} Report "
                          f"{doc_date.year}")
        body, lh, top = 11.5, 16, 170
    elif template == "policy":
        n = rng.randint(3, 4)
        sections, facts, clauses = _build_sections(rng, inv, n_sections=n,
                                                   facts_per=_spread(rng, n, rng.randint(1, 2)),
                                                   filler_per_para=(0, 2), paras_per=(1, 3),
                                                   numbered=True, headings=True, used=used,
                                                   clauses=True)
        sections[0]["paras"] = [{"sents": [{"segs": [_seg(
            f"This policy explains how {org} manages shared spaces and services. "
            f"It applies to everyone who uses its buildings.")], "filler": True}],
            "clause": None}] + sections[0]["paras"]
        meta.update(title=f"{rng.choice(('Visitor', 'Facilities', 'Shared Spaces', 'Building Use'))}"
                          f" Policy", code=f"POL-{rng.randint(100, 999)}",
                    owner=inv.person())
        body, lh, top = 11.5, 16, 170
    elif template == "columns":
        n = rng.choice((3, 4, 5))
        sections, facts, _ = _build_sections(rng, inv, n_sections=n,
                                             facts_per=_spread(rng, n, rng.randint(3, 5)),
                                             filler_per_para=(2, 4), paras_per=(1, 3),
                                             numbered=False, headings=True, used=used)
        meta.update(issue=rng.randint(3, 60), editor=inv.person(),
                    title=f"{inv.town()} {rng.choice(('Neighborhood News', 'Bulletin', 'Gazette', 'Monthly'))}")
        body, lh, top = 11, 15, 150
    else:                                  # dense pages
        words = {"dense_350": 350, "dense_600": 600, "dense_900": 900}[template]
        n = {"dense_350": 4, "dense_600": 5, "dense_900": 6}[template]
        sections, facts, _ = _build_sections(rng, inv, n_sections=n,
                                             facts_per=_spread(rng, n, rng.randint(4, 6)),
                                             filler_per_para=(1, 2), paras_per=(1, 2),
                                             numbered=rng.random() < 0.5, headings=True,
                                             used=used, words=words)
        meta.update(title=f"{rng.choice(('Briefing', 'Notice', 'Minutes', 'Summary'))}: "
                          f"{org}", ref=f"{inv.code(3, 0)}-{rng.randint(10, 99)}")
        body = {"dense_350": 10, "dense_600": 9.5, "dense_900": 9}[template]
        lh = {"dense_350": 13.5, "dense_600": 12.8, "dense_900": 12.2}[template]
        top = 120
    model = {"sections": sections}
    ncols = 1
    if template == "columns":
        ncols = rng.choice((2, 3))
    elif template in ("dense_600", "dense_900"):
        ncols = 2
    margin = 54 if w < 700 else 60
    gutter = 24
    colw = (w - 2 * margin - gutter * (ncols - 1)) / ncols
    cols = [(margin + i * (colw + gutter), colw) for i in range(ncols)]
    bottom = h - 112 if template == "letter" else h - 46
    head_size = 13 if template not in ("dense_600", "dense_900") else 11.5
    if template == "dense_350":
        head_size = 12

    # header, drawn first (reading order)
    _header(c, rng, inv, template, meta, w, margin)

    eff = [bottom]

    def layout(cv: Canvas, m: dict, draw: bool, cols_=None, bottom_=None) -> bool:
        fl = _Flow(cv, cols_ or cols, top, bottom_ or eff[0], draw)
        for sec in m["sections"]:
            if sec["heading"]:
                fl.gap(8)
                fl.heading(sec["heading"], head_size, field=sec["heading_field"],
                           color="accent" if template == "columns" else "fg")
            for para in sec["paras"]:
                lead = None
                indent = 0
                if para["clause"]:
                    lead = (para["clause"][0], para["clause"][1], max(body, FIELD_MIN))
                    indent = 30
                fl.para(para["sents"], body, lh, indent=indent, lead=lead)
                fl.y += lh * 0.45
        layout.drawn = fl.fields_drawn
        layout.end = fl.y
        return fl.ok

    layout.drawn = []
    if ncols > 1:                   # balance the columns: start short, grow until it fits
        layout(c, model, False, [cols[0]], 10 ** 6)
        eff[0] = min(bottom, top + (layout.end - top) / ncols + lh)
        while eff[0] < bottom and not layout(c, model, False):
            eff[0] = min(bottom, eff[0] + lh)
    _fit(c, layout, model)
    drawn = set(layout.drawn)
    if template == "letter":
        y = min(h - 102, max(top + 40, _last_y(c) + 16))
        c.text(margin, y, "Sincerely,", size=12)
        c.text(margin, y + 22, meta["sender"], size=13, bold=True, field="sender")
        c.text(margin, y + 40, f"{meta['role']}, {org}", size=11, color="muted")
    page_no = rng.randint(1, 4)
    c.text_center(w / 2, h - 28, f"Page {page_no}", size=10, color="muted")

    facts = [f for f in facts if f["name"] in drawn]
    clauses = [cl for cl in clauses if cl["no_field"] in drawn and cl["text_field"] in drawn]
    qs = _doc_questions(rng, c, template, meta, sections, facts, clauses, drawn)
    data = {"template": template, "meta": meta, "words": _word_count(model),
            "sections": [{"heading": s["heading"], "facts": [
                {"topic": f["topic"], "kind": f["kind"], "shown": f["shown"], "value": f["value"]}
                for f in facts if f["section"] == i]} for i, s in enumerate(sections)],
            "clauses": [{k: cl[k] for k in ("number", "who", "what")} for cl in clauses],
            "columns": ncols}
    return _finish(c, rng, abstain, qs, data, template, inv, doc_date.year)


def _spread(r: random.Random, n: int, total: int) -> list[int]:
    out = [0] * n
    for i in range(total):
        out[i % n if i < n else r.randrange(n)] += 1
    r.shuffle(out)
    return out


def _last_y(c: Canvas) -> float:
    return max((r.bbox[3] for r in c.runs), default=0) / c.scale


def _header(c: Canvas, r: random.Random, inv: Inventor, template: str, meta: dict, w: float,
            margin: float) -> None:
    org = meta["org"]
    d = date.fromisoformat(meta["date"])
    dshown = r.choice((date_long(d), f"{MONTHS[d.month - 1]} {d.day}, {d.year}",
                       f"{d.day} {MONTHS[d.month - 1]} {d.year}"))
    if template == "letter":
        if r.random() < 0.5:
            c.rect(0, 0, w, 8, fill="accent")
        c.text(margin, 40, org, size=18, bold=True, color="accent", field="org")
        c.text(margin, 64, f"{inv.street_address()}, {inv.town()}", size=11, color="muted")
        c.text(margin, 80, f"{inv.phone()}  |  {inv.email('office')}", size=11, color="muted")
        c.line(margin, 102, w - margin, 102)
        c.text(margin, 120, dshown, size=12, field="date", kind="date", value=meta["date"])
        c.text_right(w - margin, 120, f"Ref: {meta['ref']}", size=12, field="ref",
                     value=meta["ref"])
        c.text(margin, 150, meta["recipient"], size=12, field="recipient")
        c.text(margin, 166, inv.street_address(), size=11)
        c.text(margin, 182, inv.town(), size=11)
        c.text(margin, 220, f"Dear {meta['recipient'].split()[0]},", size=12)
        c.text(margin, 244, f"Re: {r.choice(_OBJ)[4:].capitalize()}", size=12, bold=True)
    elif template == "memo":
        c.text(margin, 40, "MEMORANDUM", size=22, bold=True, color="accent")
        c.text_right(w - margin, 48, org, size=11, color="muted", field="org")
        rows = (("To:", meta["recipient"], "recipient", "text", meta["recipient"]),
                ("From:", meta["sender"], "sender", "text", meta["sender"]),
                ("Date:", dshown, "date", "date", meta["date"]),
                ("Subject:", meta["subject"], "subject", "text", meta["subject"]))
        y = 86
        for label, val, name, kind, value in rows:
            c.text(margin, y, label, size=12, bold=True)
            c.text(margin + 70, y, val, size=12, field=name, kind=kind, value=value)
            y += 24
        c.line(margin, y + 6, w - margin, y + 6, width=2)
    elif template in ("report", "policy"):
        c.rect(0, 0, w, 6, fill="accent")
        c.text(margin, 34, org, size=11, color="muted", field="org")
        c.text(margin, 54, meta["title"], size=22, bold=True, field="title")
        if template == "report":
            c.text(margin, 92, f"Prepared by", size=11, color="muted")
            c.text(margin + 70, 92, meta["author"], size=12, field="author")
            c.text(margin, 112, "Date", size=11, color="muted")
            c.text(margin + 70, 112, dshown, size=12, field="date", kind="date",
                   value=meta["date"])
        else:
            c.text(margin, 92, "Policy number", size=11, color="muted")
            c.text(margin + 96, 92, meta["code"], size=12, field="code")
            c.text(margin, 112, "Effective", size=11, color="muted")
            c.text(margin + 96, 112, dshown, size=12, field="date", kind="date",
                   value=meta["date"])
            c.text(w - margin - 190, 92, "Policy owner", size=11, color="muted")
            c.text(w - margin - 190, 110, meta["owner"], size=12, field="owner")
        c.line(margin, 140, w - margin, 140)
    elif template == "columns":
        c.rect(0, 0, w, 96, fill="surface")
        c.text(60, 22, meta["title"], size=28, bold=True, field="title")
        c.text(60, 66, f"Issue {meta['issue']}", size=12, field="issue", kind="number",
               value=str(meta["issue"]))
        c.text(150, 66, dshown, size=12, field="date", kind="date", value=meta["date"])
        c.text_right(w - 60, 66, f"Editor: {meta['editor']}", size=12, field="editor",
                     value=meta["editor"])
        c.line(60, 120, w - 60, 120, width=2, color="fg")
    else:
        c.text(margin, 34, meta["title"], size=16, bold=True, field="title")
        c.text(margin, 62, dshown, size=11, field="date", kind="date", value=meta["date"])
        c.text_right(w - margin, 62, f"Ref {meta['ref']}", size=11, field="ref",
                     value=meta["ref"])
        c.line(margin, 90, w - margin, 90)


# ============================================================================ questions
def _doc_questions(r: random.Random, c: Canvas, template: str, meta: dict, sections: list,
                   facts: list, clauses: list, drawn: set) -> list[Question]:
    pool_meta: list[Question] = []
    if "sender" in meta and "sender" in c.fields:
        q = "Who signed the letter?" if template == "letter" else "Who is the memo from?"
        pool_meta.append(Question("q_sender", "lookup", q, meta["sender"], fields=["sender"]))
    if template == "memo":
        pool_meta.append(Question("q_subject", "lookup", "What is the subject of the memo?",
                                  meta["subject"], kind="anls", fields=["subject"]))
    if template == "letter":
        pool_meta.append(Question("q_recipient", "lookup", "To whom is the letter addressed?",
                                  meta["recipient"], fields=["recipient"]))
    if "date" in c.fields:
        q = {"letter": "What date is on the letter?", "memo": "What date is on the memo?",
             "policy": "On what date does the policy take effect?",
             "columns": "What date is this issue dated?"}.get(template,
                                                             "What date is on the document?")
        pool_meta.append(Question("q_date", "lookup", q, meta["date"], canon="date",
                                  fields=["date"]))
    if "ref" in c.fields:
        pool_meta.append(Question("q_ref", "lookup", "What is the reference number?",
                                  meta["ref"], fields=["ref"]))
    if "author" in c.fields:
        pool_meta.append(Question("q_author", "lookup", "Who prepared the report?",
                                  meta["author"], fields=["author"]))
    if "code" in c.fields:
        pool_meta.append(Question("q_code", "lookup", "What is the policy number?", meta["code"],
                                  fields=["code"]))
    if "editor" in c.fields:
        pool_meta.append(Question("q_editor", "lookup", "Who is the editor of this newsletter?",
                                  meta["editor"], fields=["editor"]))
    heads = [s for s in sections if s["heading_field"] and s["heading_field"] in drawn]
    if heads and template in ("report", "policy", "memo"):
        i = r.randrange(len(heads))
        nth = ("first", "second", "third", "fourth", "fifth", "sixth")[i]
        pool_meta.append(Question("q_heading", "lookup",
                                  f"What is the heading of the {nth} section?", heads[i]["heading"],
                                  fields=[s["heading_field"] for s in heads],
                                  accept=[heads[i]["plain"]]))

    fact_lookups = [Question(f"q_{f['name']}", "lookup", f["question"], f["value"],
                             canon=f["canon"], fields=[f["name"]]) for f in facts]
    sect_qs = []
    for f in facts:
        sec = sections[f["section"]]
        if sec["heading_field"] and sec["heading_field"] in drawn:
            sect_qs.append(Question(
                f"q_section_{f['name']}", "lookup",
                f"Under which heading does the document mention {f['topic']}? "
                f"Give the heading text.", sec["heading"],
                fields=[sec["heading_field"], f["name"]] +
                [s["heading_field"] for s in heads if s is not sec],
                accept=[sec["plain"]] if sec["plain"] != sec["heading"] else []))
    clause_qs = []
    for cl in clauses:
        clause_qs.append(Question(
            f"q_clause_{cl['number'].replace('.', '_')}", "lookup",
            f"According to clause {cl['number']}, what must {cl['who'].lower()} do?", cl["what"],
            kind="anls", fields=[cl["no_field"], cl["text_field"]]))
        clause_qs.append(Question(
            f"q_clauseno_{cl['number'].replace('.', '_')}", "lookup",
            f"Which clause number says that {cl['who'].lower()} must {cl['what']}?",
            cl["number"], fields=[cl["no_field"], cl["text_field"]],
            accept=[f"clause {cl['number']}", f"section {cl['number']}"]))

    chosen: list[Question] = []

    def take(pool: list[Question], k: int) -> None:
        pool = [q for q in pool if q.qid not in {x.qid for x in chosen}]
        for q in r.sample(pool, min(k, len(pool))):
            chosen.append(q)

    if clause_qs:
        cl_pick = r.sample(clause_qs, min(2, len(clause_qs)))
        chosen.extend(cl_pick)
    if sect_qs:
        take(sect_qs, 1)
    # a fact asked as a lookup should not also be the section question's fact
    asked = {f for q in chosen for f in q.fields}
    take([q for q in fact_lookups if q.fields[0] not in asked], 1 if sect_qs else 2)
    take(pool_meta, 4 - len(chosen))
    take(fact_lookups + sect_qs + pool_meta + clause_qs, 4 - len(chosen))
    chosen = chosen[:4]
    r.shuffle(chosen)
    return chosen


def _hidden_ok(c: Canvas, name: str, keep: list[str]) -> bool:
    """A field can be blurred only when no field that stays asked about is within the blur pad."""
    f = c.fields.get(name)
    if f is None or not f.visible or f.bbox is None:
        return False
    pad = c.px(3) + 2
    x0, y0, x1, y1 = f.bbox[0] - pad, f.bbox[1] - pad, f.bbox[2] + pad, f.bbox[3] + pad
    boxes = [c.fields[o].bbox for o in keep if o != name]
    boxes += [run.bbox for run in c.runs if run.field != name]   # no other text gets smeared
    for b in boxes:
        if b and not (b[2] <= x0 or b[0] >= x1 or b[3] <= y0 or b[1] >= y1):
            return False
    return True


_ABSENT = (("q_fax", "What fax number is given in the document?", "fax", "phone"),
           ("q_cc", "Who is copied on this document?", "cc", "person"),
           ("q_budget_code", "What is the budget code given in the document?", "budget_code",
            "code"))


def _finish(c: Canvas, r: random.Random, abstain: bool, qs: list[Question], data: dict,
            template: str, inv: Inventor, year: int) -> Rendered:
    if abstain:
        drop = qs.pop(r.randrange(len(qs)))
        keep = [f for q in qs for f in q.fields]
        blur_cands = [q for q in [drop] if q.kind != "anls" and len(q.fields) == 1
                      and q.fields[0] not in keep and _hidden_ok(c, q.fields[0], keep)]
        if blur_cands and r.random() < 0.5:
            q = blur_cands[0]
            c.blur_field(q.fields[0], radius=7)
            qs.append(Question(q.qid + "_abstain", "absence", q.question, ABSTAIN_ANSWER,
                               kind="abstain", fields=list(q.fields)))
            data["abstain"] = {"blurred": q.fields[0]}
        else:
            qid, text, name, how = r.choice(_ABSENT)
            drawn = [canon_text(run.text) for run in c.runs]
            for _ in range(50):
                value = {"phone": inv.phone, "person": inv.person,
                         "code": lambda: f"BC-{inv.code(1, 5)}"}[how]()
                if not any(canon_text(value) in t for t in drawn):
                    break
            c.absent(name, value, "phone" if how == "phone" else "text")
            qs.append(Question(qid, "absence", text, ABSTAIN_ANSWER, kind="abstain",
                               fields=[name]))
            data["abstain"] = {"absent": name}
    qs.append(Question("q_reading", "reading", "Read the document.", "", kind="reading"))
    return Rendered(c, template, qs, data, default_year=year)


# ============================================================================ slide
_SLIDE_METRIC = {"money": ("budget", "spending", "funding"),
                 "percent": ("participation", "satisfaction", "uptake"),
                 "count": ("sign-ups", "visitors", "applications"),
                 "date": ("start date", "launch date", "deadline")}


def _slide(rng: random.Random, *, theme: str, scale: float, abstain: bool) -> Rendered:
    inv = Inventor(rng)
    w, h = LOGICAL_SIZES["slide"]
    c = Canvas(w, h, scale=scale, theme=theme)
    used: set = set()
    org = _org(inv, rng)
    d = inv.day(BASE_DATE, 600)
    presenter = inv.person()
    title = f"{rng.choice(('Plans for', 'Review of', 'Update on', 'Results from'))} " \
            f"{rng.choice(_TOPICS)[4:]}".strip()
    title = title[0].upper() + title[1:]
    total, n_slide = rng.randint(8, 20), 0
    n_slide = rng.randint(2, total)
    style = rng.randrange(3)
    if style == 0:
        c.rect(0, 0, w, 120, fill="accent")
        tcol = "accent_fg"
    else:
        c.rect(0, 0, 14, h, fill="accent")
        tcol = "fg"
    c.text(64, 34, title, size=36, bold=True, color=tcol, field="title")
    sub_y = 84 if style == 0 else 92
    dshown = f"{MONTHS[d.month - 1]} {d.day}, {d.year}"
    c.text(64, sub_y, presenter, size=16, color=tcol, field="presenter")
    pw = c.text_width(presenter, 16)
    c.text(64 + pw + 14, sub_y, "|", size=16, color=tcol, reading=False)
    c.text(64 + pw + 30, sub_y, dshown, size=16, color=tcol, field="date", kind="date",
           value=d.isoformat())

    n_panels = rng.choice((2, 3))
    heads = rng.sample(_HEADINGS, n_panels)
    topics = rng.sample(_TOPICS, n_panels * 3)
    panel_w = (w - 128 - 32 * (n_panels - 1)) / n_panels
    y0 = 160
    bullets = []
    facts = []
    ti = 0
    for p in range(n_panels):
        x = 64 + p * (panel_w + 32)
        c.rect(x, y0, x + panel_w, y0 + 380, fill="surface", radius=12)
        c.text(x + 24, y0 + 22, heads[p], size=22, bold=True, color="accent",
               field=f"heading_{p}")
        y = y0 + 72
        for k in range(3):
            t = topics[ti]
            kind = rng.choice(("money", "percent", "count", "date"))
            label = t[4:].capitalize() + " " + rng.choice(_SLIDE_METRIC[kind])
            f = _make_fact(rng, inv, kind, t, f"fact_{ti}", used)
            c.ellipse(x + 24, y + 7, x + 32, y + 15, fill="accent")
            c.text(x + 42, y, label, size=17, field=f"bullet_{ti}", value=label)
            c.text(x + 42, y + 26, f["shown"], size=20, bold=True, field=f["name"],
                   kind=f["canon"], value=f["value"])
            bullets.append({"panel": p, "label": label, "name": f"bullet_{ti}"})
            f.update(panel=p, label=label)
            facts.append(f)
            y += 98
            ti += 1
    # navigation controls
    c.text(64, h - 46, f"Slide {n_slide} of {total}", size=13, color="muted",
           field="slide_no", kind="number", value=str(n_slide))
    c.text_center(w / 2, h - 46, org, size=13, color="muted")
    bx = w - 64
    for label in ("Next", "Back"):
        bw = 96
        fill, fg = ("accent", "accent_fg") if label == "Next" else ("surface", "fg")
        c.rect(bx - bw, h - 62, bx, h - 22, fill=fill, radius=8)
        c.text_center(bx - bw / 2, h - 51, label, size=15, color=fg)
        c.element(label, bx - bw, h - 62, bx, h - 22, role="button")
        bx -= bw + 16

    pool_meta = [Question("q_title", "lookup", "What is the title of the slide?", title,
                          fields=["title"]),
                 Question("q_presenter", "lookup", "Who is presenting this slide?", presenter,
                          fields=["presenter"]),
                 Question("q_date", "lookup", "What date is shown on the slide?", d.isoformat(),
                          canon="date", fields=["date"]),
                 Question("q_slide_no", "lookup", "Which slide number is this?", str(n_slide),
                          canon="number", fields=["slide_no"])]
    fa = rng.sample(facts, 2)
    qs = [Question(f"q_{fa[0]['name']}", "lookup",
                   f"What value does the slide show for {fa[0]['label'].lower()}?",
                   fa[0]["value"], canon=fa[0]["canon"],
                   fields=[f"bullet_{fa[0]['name'][5:]}", fa[0]["name"]]),
          Question(f"q_section_{fa[1]['name']}", "lookup",
                   f"Under which heading is {fa[1]['label'].lower()} listed?",
                   heads[fa[1]["panel"]],
                   fields=[f"heading_{p}" for p in range(n_panels)] +
                   [f"bullet_{fa[1]['name'][5:]}"])]
    qs += rng.sample(pool_meta, 2)
    rng.shuffle(qs)
    data = {"template": "slide", "title": title, "presenter": presenter, "date": d.isoformat(),
            "slide": n_slide, "of": total, "org": org,
            "panels": [{"heading": heads[p], "items": [
                {"label": f["label"], "kind": f["kind"], "shown": f["shown"], "value": f["value"]}
                for f in facts if f["panel"] == p]} for p in range(n_panels)]}
    return _finish(c, rng, abstain, qs, data, "slide", inv, d.year)
