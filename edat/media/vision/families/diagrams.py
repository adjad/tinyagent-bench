"""Diagrams: flowcharts, org charts, timelines, floor plans, network and sequence diagrams, and
labelled street maps, drawn from invented graph descriptions (spec 05 §7.1).

Every node, room, event, participant, place and street label is a planted field. Structural
questions ("what comes after X", "whom does X report to", "which room shares a wall with Z",
"which event is after X", "which device connects X to Y", "who sends the first message",
"which place is east of X") have answers that are checked unique in code, and list every label
field the answer depends on. Question types: lookup (structure and labels), count, and the full
reading.

Seven templates on purpose: a count coprime with 10 makes every template meet every theme,
scale and abstention slot.
"""
from __future__ import annotations

import math
import random
from datetime import date

from edat.media.common import GIVEN_NAMES, Inventor, canon_text, date_short
from edat.media.vision.canvas import Canvas
from edat.media.vision.items import ABSTAIN_ANSWER, Question, Rendered

FAMILY = "diagrams"
TEMPLATES = ("flowchart", "org_chart", "timeline", "floor_plan", "network", "sequence", "map")
LOGICAL_SIZES = {"flowchart": (760, 900), "org_chart": (1040, 640), "timeline": (1100, 460),
                 "floor_plan": (900, 640), "network": (1000, 640), "sequence": (920, 720),
                 "map": (900, 700)}
YEAR = 2027


def render(rng: random.Random, *, template: str, theme: str, scale: float,
           abstain: bool) -> Rendered:
    if template not in TEMPLATES:
        raise ValueError(f"unknown template {template!r}")
    return globals()["_" + template](rng, theme=theme, scale=scale, abstain=abstain)


# ============================================================================ helpers
def _arrow(c: Canvas, pts: list[tuple[float, float]], *, color="fg", width: float = 1.6,
           head: float = 9) -> None:
    for a, b in zip(pts, pts[1:]):
        c.line(a[0], a[1], b[0], b[1], color=color, width=width)
    (x0, y0), (x1, y1) = pts[-2], pts[-1]
    ang = math.atan2(y1 - y0, x1 - x0)
    ca, sa = math.cos(ang), math.sin(ang)
    left = (x1 - head * ca + head * 0.5 * sa, y1 - head * sa - head * 0.5 * ca)
    right = (x1 - head * ca - head * 0.5 * sa, y1 - head * sa + head * 0.5 * ca)
    c.polygon([(x1, y1), left, right], fill=color)


def _dashed(c: Canvas, x: float, y0: float, y1: float, *, color="border", dash: float = 7,
            width: float = 1.2) -> None:
    y = y0
    while y < y1:
        c.line(x, y, x, min(y1, y + dash), color=color, width=width)
        y += dash * 2


def _label(c: Canvas, cx: float, cy: float, s: str, size: float, field: str, *,
           bold: bool = False, color="fg", kind: str = "text", value: str | None = None) -> None:
    """Text centred on (cx, cy)."""
    c.text_center(cx, cy - size * 0.62, s, size=size, bold=bold, color=color, field=field,
                  kind=kind, value=value)


def _box_w(c: Canvas, s: str, size: float, pad: float = 18, minimum: float = 0) -> float:
    return max(minimum, c.text_width(s, size) + 2 * pad)


def _blur_safe(c: Canvas, name: str, keep: list[str]) -> bool:
    f = c.fields.get(name)
    if f is None or not f.visible or f.bbox is None or name in keep:
        return False
    pad = c.px(3) + 2
    x0, y0, x1, y1 = f.bbox[0] - pad, f.bbox[1] - pad, f.bbox[2] + pad, f.bbox[3] + pad
    boxes = [c.fields[o].bbox for o in keep if o in c.fields]
    boxes += [r.bbox for r in c.runs if r.field != name]
    return not any(b and not (b[2] <= x0 or b[0] >= x1 or b[3] <= y0 or b[1] >= y1)
                   for b in boxes)


def _finish(c: Canvas, rng: random.Random, abstain: bool, qs: list[Question], answer_field: dict,
            data: dict, template: str, absent: tuple, what: str) -> Rendered:
    """``answer_field`` maps qid -> the field holding that question's answer (for blurring)."""
    assert len(qs) == 4, template
    if abstain:
        done = False
        if rng.random() < 0.5:
            order = list(qs)
            rng.shuffle(order)
            for q in order:
                name = answer_field.get(q.qid)
                if not name:
                    continue
                keep = [f for o in qs if o is not q for f in o.fields]
                if _blur_safe(c, name, keep):
                    qs.remove(q)
                    c.blur_field(name, radius=7)
                    qs.append(Question(q.qid + "_abstain", "absence", q.question, ABSTAIN_ANSWER,
                                       kind="abstain", fields=[name]))
                    data["abstain"] = {"blurred": name}
                    done = True
                    break
        if not done:
            qs.pop(rng.randrange(len(qs)))
            qid, text, name, make = absent
            drawn = [canon_text(r.text) for r in c.runs]
            v = make()
            for _ in range(50):
                if not any(canon_text(v) in t for t in drawn):
                    break
                v = make()
            c.absent(name, v)
            qs.append(Question(qid, "absence", text, ABSTAIN_ANSWER, kind="abstain",
                               fields=[name]))
            data["abstain"] = {"absent": name}
    qs.append(Question("q_reading", "reading", f"Read all the labels in the {what}.", "",
                       kind="reading"))
    return Rendered(c, template, qs, data, default_year=YEAR)


def _title(c: Canvas, x: float, y: float, s: str, size: float = 20) -> None:
    c.text(x, y, s, size=size, bold=True, field="diagram_title")


# ============================================================================ flowchart
_STEPS = ("Receive request", "Log the request", "Check details", "Pack the order",
          "Print shipping label", "Ship parcel", "Send invoice", "Notify customer",
          "Review form", "Schedule visit", "Archive file", "Assign volunteer", "Book room",
          "Collect payment", "Send reminder", "Update records", "Prepare quote", "Sign contract")
_DECISIONS = (("Item in stock?", "Order from supplier"), ("Form complete?", "Return to applicant"),
              ("Budget approved?", "Revise the plan"), ("Payment received?", "Send a reminder"),
              ("Room available?", "Offer another date"), ("Details correct?", "Ask for changes"))


def _flowchart(rng, *, theme, scale, abstain):
    inv = Inventor(rng)
    w, h = LOGICAL_SIZES["flowchart"]
    c = Canvas(w, h, scale=scale, theme=theme)
    n = rng.randint(4, 6)
    steps = rng.sample(_STEPS, n)
    dq, side = rng.choice(_DECISIONS)
    k = rng.randint(0, n - 2)                    # the decision follows steps[k]
    loop_back = rng.random() < 0.5               # the "No" branch returns to steps[k] or ends
    side_right = rng.random() < 0.5
    rounded = rng.random() < 0.5
    title = f"{rng.choice(('Order', 'Request', 'Booking', 'Intake', 'Repair'))} process"
    _title(c, 40, 28, title)
    fs = 14
    nodes = [("Start", "term")] + [(s, "step") for s in steps[:k + 1]] + [(dq, "dec")] + \
        [(s, "step") for s in steps[k + 1:]] + [("End", "term")]
    mx = 300 if side_right else w - 300
    sx = mx + 270 if side_right else mx - 270
    gap = 30
    y = 80
    geo = []
    for label, kind in nodes:
        hh = 70 if kind == "dec" else 42
        ww = _box_w(c, label, fs, 22, 150) if kind != "dec" else _box_w(c, label, fs, 44, 190)
        geo.append({"label": label, "kind": kind, "cx": mx, "y0": y, "y1": y + hh, "w": ww})
        y += hh + gap
    di = k + 2                                   # index of the decision in ``nodes``
    d = geo[di]
    sw = _box_w(c, side, fs, 22, 150)
    side_g = {"cx": sx, "y0": (d["y0"] + d["y1"]) / 2 - 21, "y1": (d["y0"] + d["y1"]) / 2 + 21,
              "w": sw}
    # edges first
    for a, b in zip(geo, geo[1:]):
        _arrow(c, [(mx, a["y1"]), (mx, b["y0"])])
    dmid = (d["y0"] + d["y1"]) / 2
    dx = d["w"] / 2 * (1 if side_right else -1)
    sx_edge = sx - sw / 2 if side_right else sx + sw / 2
    _arrow(c, [(mx + dx, dmid), (sx_edge, dmid)])
    c.text((mx + dx + sx_edge) / 2 - 8, dmid - 22, "No", size=12, color="muted")
    c.text(mx + 10, d["y1"] + 6, "Yes", size=12, color="muted")
    if loop_back:
        tgt = geo[di - 1]
        tmid = (tgt["y0"] + tgt["y1"]) / 2
        tx = mx + (tgt["w"] / 2 if side_right else -tgt["w"] / 2)
        _arrow(c, [(sx, side_g["y0"]), (sx, tmid), (tx, tmid)])
        side_next = tgt["label"]
    else:
        end = geo[-1]
        emid = (end["y0"] + end["y1"]) / 2
        ex = mx + (end["w"] / 2 if side_right else -end["w"] / 2)
        _arrow(c, [(sx, side_g["y1"]), (sx, emid), (ex, emid)])
        side_next = "End"
    # nodes, top to bottom; the side branch is read with its decision
    fill = "surface"
    for i, g in enumerate(geo):
        x0, x1 = g["cx"] - g["w"] / 2, g["cx"] + g["w"] / 2
        if g["kind"] == "dec":
            cy = (g["y0"] + g["y1"]) / 2
            c.polygon([(g["cx"], g["y0"]), (x1, cy), (g["cx"], g["y1"]), (x0, cy)],
                      fill="muted")
            c.polygon([(g["cx"], g["y0"] + 2), (x1 - 3, cy), (g["cx"], g["y1"] - 2),
                       (x0 + 3, cy)], fill=fill)
        elif g["kind"] == "term":
            c.rect(x0, g["y0"], x1, g["y1"], fill="accent", radius=21)
        else:
            c.rect(x0, g["y0"], x1, g["y1"], fill=fill, outline="border",
                   radius=8 if rounded else 0, width=1.2)
        _label(c, g["cx"], (g["y0"] + g["y1"]) / 2, g["label"], fs, f"node_{i}",
               bold=g["kind"] == "term", color="accent_fg" if g["kind"] == "term" else "fg")
        if i == di:
            s0, s1 = side_g["cx"] - sw / 2, side_g["cx"] + sw / 2
            c.rect(s0, side_g["y0"], s1, side_g["y1"], fill=fill, outline="border",
                   radius=8 if rounded else 0, width=1.2)
            _label(c, side_g["cx"], (side_g["y0"] + side_g["y1"]) / 2, side, fs, "node_side")
    names = [f"node_{i}" for i in range(len(geo))]
    ask = rng.choice([i for i in range(1, len(geo) - 1) if geo[i]["kind"] == "step"])
    nxt = ask + 1
    qs = [Question("q_after", "lookup",
                   f"In the flowchart, what comes right after '{geo[ask]['label']}'?",
                   geo[nxt]["label"], fields=[names[ask], names[nxt]]),
          Question("q_no", "lookup", f"What happens if the answer to '{dq}' is No?", side,
                   fields=[names[di], "node_side"]),
          rng.choice((Question("q_first", "lookup",
                               "What is the first step after Start?", geo[1]["label"],
                               fields=[names[0], names[1]]),
                      Question("q_side_next", "lookup", f"Where does the flow go after '{side}'?",
                               side_next, fields=["node_side", names[di - 1] if loop_back else
                                                  names[-1]]))),
          Question("q_count", "count",
                   "How many rectangular step boxes are in the flowchart, not counting Start "
                   "and End?", str(n + 1), canon="number", fields=names[1:-1] + ["node_side"])]
    ans = {"q_after": names[nxt], "q_no": "node_side", "q_first": names[1]}
    rng.shuffle(qs)
    data = {"template": "flowchart", "title": title, "nodes": [g["label"] for g in geo],
            "decision": dq, "no_branch": side, "no_next": side_next}
    return _finish(c, rng, abstain, qs, ans, data, "flowchart",
                   ("q_owner", "Who is named as the owner of this process?", "owner",
                    inv.person), "flowchart")


# ============================================================================ org chart
_TITLES_TOP = ("Director", "Executive Director", "Head of Operations", "General Manager")
_TITLES_MID = ("Facilities Lead", "Finance Manager", "Program Manager", "Outreach Lead",
               "Volunteer Coordinator", "Events Manager", "IT Lead")
_TITLES_LOW = ("Analyst", "Coordinator", "Assistant", "Technician", "Officer", "Designer",
               "Clerk", "Associate")


def _unique_people(inv: Inventor, n: int) -> list[str]:
    out: list[str] = []
    firsts: set = set()
    while len(out) < n:
        p = inv.person()
        if p.split()[0] not in firsts:
            out.append(p)
            firsts.add(p.split()[0])
    return out


def _org_chart(rng, *, theme, scale, abstain):
    inv = Inventor(rng)
    w, h = LOGICAL_SIZES["org_chart"]
    c = Canvas(w, h, scale=scale, theme=theme)
    m = rng.randint(2, 4)
    counts = [rng.randint(0, 3) for _ in range(m)]
    a = rng.randrange(m)
    counts[a] = 1                                  # someone with exactly one report
    if not any(x >= 2 for x in counts):
        counts[rng.choice([i for i in range(m) if i != a])] = rng.randint(2, 3)
    people = _unique_people(inv, 1 + m + sum(counts))
    root = {"name": people[0], "title": rng.choice(_TITLES_TOP)}
    mids, pi = [], 1
    for i in range(m):
        mids.append({"name": people[pi], "title": rng.choice(_TITLES_MID), "reports": []})
        pi += 1
    for i in range(m):
        for _ in range(counts[i]):
            mids[i]["reports"].append({"name": people[pi], "title": rng.choice(_TITLES_LOW)})
            pi += 1
    org = f"{inv.town()} {rng.choice(('Community Trust', 'Library Board', 'Arts Center'))}"
    _title(c, 40, 24, f"{org} - team structure", 18)
    bw, bh = 196, 52
    colw = (w - 80) / m
    root_cx = w / 2
    ry = 70
    my = 180
    rows_y = [290 + j * 76 for j in range(3)]
    # edges
    c.line(root_cx, ry + bh, root_cx, my - 24, color="border", width=1.5)
    xs = [40 + colw * (i + 0.5) for i in range(m)]
    c.line(xs[0], my - 24, xs[-1], my - 24, color="border", width=1.5)
    for i, x in enumerate(xs):
        c.line(x, my - 24, x, my, color="border", width=1.5)
        if mids[i]["reports"]:
            spine = x - bw / 2 + 14
            last = rows_y[len(mids[i]["reports"]) - 1] + bh / 2
            c.line(spine, my + bh, spine, last, color="border", width=1.5)
            for j in range(len(mids[i]["reports"])):
                yy = rows_y[j] + bh / 2
                c.line(spine, yy, x - bw / 2 + 34, yy, color="border", width=1.5)

    def node(cx: float, y: float, person: dict, fname: str, width: float, top: bool) -> None:
        x0 = cx - width / 2
        c.rect(x0, y, x0 + width, y + bh, fill="accent" if top else "surface",
               outline=None if top else "border", radius=8)
        col = "accent_fg" if top else "fg"
        c.text_center(cx, y + 9, person["name"], size=14, bold=True, color=col, field=fname)
        c.text_center(cx, y + 30, person["title"], size=11, color=col, field=fname + "_title",
                      value=person["title"])

    node(root_cx, ry, root, "root", bw + 20, True)
    for i, x in enumerate(xs):
        node(x, my, mids[i], f"mgr_{i}", bw, False)
    for i, x in enumerate(xs):
        for j, rep in enumerate(mids[i]["reports"]):
            node(x + 10, rows_y[j], rep, f"rep_{i}_{j}", bw - 20, False)
    mgr_f = [f"mgr_{i}" for i in range(m)]
    with_reports = [(i, j) for i in range(m) for j in range(len(mids[i]["reports"]))]
    i1, j1 = rng.choice(with_reports)
    one = rng.choice([i for i in range(m) if counts[i] == 1])
    many = rng.choice([i for i in range(m) if counts[i] >= 2])
    q_whom = Question("q_whom", "lookup",
                      f"Whom does {mids[i1]['reports'][j1]['name']} report to?",
                      mids[i1]["name"], fields=[f"rep_{i1}_{j1}"] + mgr_f)
    q_who = Question("q_who", "lookup", f"Who reports directly to {mids[one]['name']}?",
                     mids[one]["reports"][0]["name"],
                     fields=[f"mgr_{one}", f"rep_{one}_0"] +
                     [f"rep_{i}_{j}" for i, j in with_reports])
    q_count = rng.choice((
        Question("q_count", "count", f"How many people report directly to {root['name']}?",
                 str(m), canon="number", fields=["root"] + mgr_f),
        Question("q_count", "count", f"How many people report directly to {mids[many]['name']}?",
                 str(counts[many]), canon="number",
                 fields=[f"mgr_{many}"] + [f"rep_{many}_{j}" for j in range(counts[many])])))
    im = rng.randrange(m)
    q_title = rng.choice((
        Question("q_title", "lookup", f"What is {mids[im]['name']}'s job title?",
                 mids[im]["title"], fields=[f"mgr_{im}", f"mgr_{im}_title"]),
        Question("q_top", "lookup", "Who is at the top of the chart?", root["name"],
                 fields=["root"])))
    qs = [q_whom, q_who, q_count, q_title]
    rng.shuffle(qs)
    ans = {"q_whom": f"mgr_{i1}", "q_who": f"rep_{one}_0", "q_title": f"mgr_{im}_title"}
    data = {"template": "org_chart", "org": org, "root": root, "managers": mids}
    return _finish(c, rng, abstain, qs, ans, data, "org_chart",
                   ("q_hr", "Who is listed as the HR contact on the chart?", "hr_contact",
                    inv.person), "org chart")


# ============================================================================ timeline
_EVENTS = ("Kickoff", "Site survey", "Permits filed", "Design review", "Groundbreaking",
           "Fundraiser", "Volunteer day", "Soft opening", "Grand opening", "Board vote",
           "Pilot launch", "Mid-term review", "Final report", "Ribbon cutting", "Public meeting",
           "Contract signed", "Training week", "First delivery")


def _timeline(rng, *, theme, scale, abstain):
    inv = Inventor(rng)
    w, h = LOGICAL_SIZES["timeline"]
    c = Canvas(w, h, scale=scale, theme=theme)
    n = rng.randint(5, 7)
    events = rng.sample(_EVENTS, n)
    start = date(rng.choice((2027, 2028)), rng.randint(1, 3), 1)
    ords = sorted(rng.sample(range(start.toordinal(), start.toordinal() + 300), n))
    days = [date.fromordinal(o) for o in ords]
    project = f"{inv.surname()} {rng.choice(('Library', 'Park', 'Clinic', 'Market', 'Bridge'))} project"
    _title(c, 40, 28, f"{project} timeline")
    ly = 240
    x0, x1 = 90, w - 90
    c.line(x0 - 30, ly, x1 + 30, ly, color="border", width=3)
    _arrow(c, [(x1 + 20, ly), (x1 + 44, ly)], color="border", width=3, head=12)
    xs = [x0 + (x1 - x0) * i / (n - 1) for i in range(n)]
    up_first = rng.random() < 0.5
    for i, x in enumerate(xs):
        up = (i % 2 == 0) == up_first
        c.line(x, ly, x, ly - 50 if up else ly + 50, color="border", width=1.5)
    for i, x in enumerate(xs):
        up = (i % 2 == 0) == up_first
        c.ellipse(x - 8, ly - 8, x + 8, ly + 8, fill="accent")
        if up:
            c.text_center(x, ly - 96, events[i], size=14, bold=True, field=f"event_{i}")
            c.text_center(x, ly - 74, date_short(days[i]), size=12, color="muted",
                          field=f"date_{i}", kind="date", value=days[i].isoformat())
        else:
            c.text_center(x, ly + 58, date_short(days[i]), size=12, color="muted",
                          field=f"date_{i}", kind="date", value=days[i].isoformat())
            c.text_center(x, ly + 78, events[i], size=14, bold=True, field=f"event_{i}")
    ev = [f"event_{i}" for i in range(n)]
    a = rng.randrange(n - 1)
    b = rng.randrange(n)
    qs = [Question("q_after", "lookup", f"Which event comes right after '{events[a]}' on the "
                   "timeline?", events[a + 1], fields=ev + [f"date_{a}", f"date_{a + 1}"]),
          Question("q_date", "lookup", f"When is '{events[b]}'?", days[b].isoformat(),
                   canon="date", fields=[ev[b], f"date_{b}"]),
          rng.choice((Question("q_first", "lookup", "Which event is first on the timeline?",
                               events[0], fields=ev),
                      Question("q_last", "lookup", "Which event is last on the timeline?",
                               events[-1], fields=ev))),
          Question("q_count", "count", "How many events are on the timeline?", str(n),
                   canon="number", fields=ev)]
    rng.shuffle(qs)
    ans = {"q_date": f"date_{b}"}
    data = {"template": "timeline", "project": project,
            "events": [{"event": e, "date": d.isoformat()} for e, d in zip(events, days)]}
    return _finish(c, rng, abstain, qs, ans, data, "timeline",
                   ("q_budget", "What is the project budget shown on the timeline?", "budget",
                    lambda: f"${rng.randint(20, 900)},000"), "timeline")


# ============================================================================ floor plan
_ROOMS = ("Kitchen", "Office", "Storage", "Lounge", "Bathroom", "Bedroom", "Laundry", "Studio",
          "Library", "Meeting Room", "Server Room", "Pantry", "Workshop", "Reception", "Nursery",
          "Gym", "Darkroom", "First Aid")


def _cuts(rng: random.Random, x0: float, x1: float, k: int, minw: float) -> list[float]:
    for _ in range(200):
        pts = sorted(rng.uniform(x0 + minw, x1 - minw) for _ in range(k - 1))
        edges = [x0] + pts + [x1]
        if all(b - a >= minw for a, b in zip(edges, edges[1:])):
            return [round(e) for e in edges]
    step = (x1 - x0) / k
    return [round(x0 + step * i) for i in range(k + 1)]


def _floor_plan(rng, *, theme, scale, abstain):
    inv = Inventor(rng)
    w, h = LOGICAL_SIZES["floor_plan"]
    c = Canvas(w, h, scale=scale, theme=theme)
    bx0, bx1, by0, by1 = 60, w - 60, 90, h - 70
    cy0, cy1 = 300, 360
    for _ in range(300):
        kt, kb = rng.randint(3, 4), rng.randint(3, 4)
        top = _cuts(rng, bx0, bx1, kt, 150)
        bot = _cuts(rng, bx0, bx1, kb, 150)
        # "directly across" candidates: a room whose span lies inside one opposite room
        across = []
        for row, edges, other in (("top", top, bot), ("bottom", bot, top)):
            for i in range(len(edges) - 1):
                inside = [j for j in range(len(other) - 1)
                          if other[j] <= edges[i] and edges[i + 1] <= other[j + 1]]
                if len(inside) == 1:
                    across.append((row, i, inside[0]))
        if across and all(abs(a - b) > 12 for a in top[1:-1] for b in bot[1:-1]):
            break
    names = rng.sample(_ROOMS, kt + kb)
    top_names, bot_names = names[:kt], names[kt:]
    title = f"{inv.surname()} House - ground floor" if rng.random() < 0.5 else \
        f"{inv.town()} Annex - level {rng.randint(1, 3)}"
    _title(c, 60, 30, title, 18)
    wall = "fg"
    c.rect(bx0, by0, bx1, by1, fill="surface")
    c.rect(bx0, cy0, bx1, cy1, fill="bg")
    c.rect(bx0, by0, bx1, by1, outline=wall, width=4)
    for edges, (ya, yb) in ((top, (by0, cy0)), (bot, (cy1, by1))):
        for e in edges[1:-1]:
            c.line(e, ya, e, yb, color=wall, width=3)
    c.line(bx0, cy0, bx1, cy0, color=wall, width=3)
    c.line(bx0, cy1, bx1, cy1, color=wall, width=3)
    for edges, yw in ((top, cy0), (bot, cy1)):            # door gaps onto the hallway
        for i in range(len(edges) - 1):
            mid = (edges[i] + edges[i + 1]) / 2 + rng.uniform(-30, 30)
            c.rect(mid - 16, yw - 3, mid + 16, yw + 3, fill="bg")
    rooms = []
    for row, edges, labels, (ya, yb) in (("top", top, top_names, (by0, cy0)),
                                         ("bottom", bot, bot_names, (cy1, by1))):
        for i, lab in enumerate(labels):
            cx = (edges[i] + edges[i + 1]) / 2
            fname = f"room_{row}_{i}"
            _label(c, cx, (ya + yb) / 2, lab, 15, fname, bold=True)
            rooms.append({"row": row, "i": i, "name": lab, "x0": edges[i], "x1": edges[i + 1]})
    c.text_center((bx0 + bx1) / 2, (cy0 + cy1) / 2 - 8, "Hallway", size=13, color="muted",
                  field="hallway")
    c.text_right(bx1, h - 44, "Not to scale", size=11, color="muted")
    # questions
    row = rng.choice(("top", "bottom"))
    labels = top_names if row == "top" else bot_names
    end = rng.choice((0, len(labels) - 1))
    nb = 1 if end == 0 else len(labels) - 2
    row_f = [f"room_{row}_{i}" for i in range(len(labels))]
    q_next = Question("q_next_to", "lookup",
                      f"Not counting the hallway, which room shares a wall with the {labels[end]}?",
                      labels[nb], fields=row_f)
    q_across = None
    if across:
        arow, ai, aj = rng.choice(across)
        src = top_names if arow == "top" else bot_names
        dst = bot_names if arow == "top" else top_names
        drow = "bottom" if arow == "top" else "top"
        q_across = Question("q_across", "lookup",
                            f"Which room is directly across the hallway from the {src[ai]}?",
                            dst[aj], fields=[f"room_{arow}_{ai}"] +
                            [f"room_{drow}_{j}" for j in range(len(dst))] + ["hallway"])
    corners = rng.sample((("top-left", top_names[0]), ("top-right", top_names[-1]),
                          ("bottom-left", bot_names[0]), ("bottom-right", bot_names[-1])), 2)
    all_f = [f"room_top_{i}" for i in range(kt)] + [f"room_bottom_{i}" for i in range(kb)]
    q_corner, q_corner2 = [Question(f"q_corner{i}", "lookup",
                                    f"Which room is in the {cn[0]} corner of the plan?", cn[1],
                                    fields=all_f) for i, cn in enumerate(corners)]
    q_count = Question("q_count", "count", "How many rooms are on the plan, not counting the "
                       "hallway?", str(kt + kb), canon="number", fields=all_f)
    qs = [q_next, q_corner, q_count] + ([q_across] if q_across else [
        q_corner2])
    rng.shuffle(qs)
    data = {"template": "floor_plan", "title": title,
            "top": [{"name": n_, "x0": a, "x1": b} for n_, a, b in zip(top_names, top, top[1:])],
            "bottom": [{"name": n_, "x0": a, "x1": b} for n_, a, b in zip(bot_names, bot, bot[1:])]}
    return _finish(c, rng, abstain, qs, {}, data, "floor_plan",
                   ("q_area", "What is the floor area of the kitchen in square meters?", "area",
                    lambda: f"{rng.randint(9, 40)}.{rng.randint(1, 9)} m2"), "floor plan")


# ============================================================================ network
def _network(rng, *, theme, scale, abstain):
    inv = Inventor(rng)
    w, h = LOGICAL_SIZES["network"]
    c = Canvas(w, h, scale=scale, theme=theme)
    k = rng.randint(2, 3)
    mids = rng.sample(("Switch A", "Switch B", "Access Point 1", "Access Point 2", "Mesh Node",
                       "Office Switch"), k)
    givens = rng.sample(GIVEN_NAMES, 8)
    leaf_pool = [f"Laptop-{givens[0]}", f"Phone-{givens[1]}", f"Tablet-{givens[2]}",
                 f"Printer-{rng.randint(1, 3)}F", f"NAS-0{rng.randint(1, 9)}",
                 f"Camera-{rng.choice(('Door', 'Garage', 'Yard'))}",
                 f"TV-{rng.choice(('Lounge', 'Den'))}", f"Console-{givens[3]}",
                 f"Desktop-{givens[4]}"]
    rng.shuffle(leaf_pool)
    counts = [rng.randint(1, 3) for _ in range(k)]
    if not any(x >= 2 for x in counts):
        counts[0] = 2
    leaves, li = [], 0
    for i in range(k):
        leaves.append(leaf_pool[li:li + counts[i]])
        li += counts[i]
    title = f"{rng.choice(('Home', 'Office', 'Studio', 'Library'))} network"
    _title(c, 40, 24, title)
    fs = 13
    cx = w / 2
    top = [("Internet", 70), ("Modem", 150), ("Router", 230)]
    xs = [60 + (w - 120) * (i + 0.5) / k for i in range(k)]
    my = 330
    rows = [430 + 64 * j for j in range(3)]
    bh = 40
    # edges
    for (_, y) in top[1:]:
        c.line(cx, y - 80 + bh, cx, y, color="accent", width=2)
    c.line(cx, 230 + bh, cx, my - 30, color="accent", width=2)
    c.line(xs[0], my - 30, xs[-1], my - 30, color="accent", width=2)
    for i, x in enumerate(xs):
        c.line(x, my - 30, x, my, color="accent", width=2)
        spine = x - 60
        c.line(x - 60, my + bh, spine, rows[len(leaves[i]) - 1] + bh / 2, color="accent",
               width=2)
        for j in range(len(leaves[i])):
            c.line(spine, rows[j] + bh / 2, spine + 24, rows[j] + bh / 2, color="accent",
                   width=2)

    def node(x: float, y: float, label: str, fname: str, cloud: bool = False) -> None:
        bw = _box_w(c, label, fs, 16, 120)
        if cloud:
            c.ellipse(x - bw / 2 - 10, y - 4, x + bw / 2 + 10, y + bh + 4, fill="surface",
                      outline="border")
        else:
            c.rect(x - bw / 2, y, x + bw / 2, y + bh, fill="surface", outline="border", radius=6)
        _label(c, x, y + bh / 2, label, fs, fname, bold=not cloud)

    node(cx, 70, "Internet", "n_internet", cloud=True)
    node(cx, 150, "Modem", "n_modem")
    node(cx, 230, "Router", "n_router")
    for i, x in enumerate(xs):
        node(x, my, mids[i], f"n_mid_{i}")
    for i, x in enumerate(xs):
        for j, lf in enumerate(leaves[i]):
            bw = _box_w(c, lf, fs, 16, 120)
            node(x - 36 + bw / 2, rows[j], lf, f"n_leaf_{i}_{j}")
    mid_f = [f"n_mid_{i}" for i in range(k)]
    i1 = rng.randrange(k)
    j1 = rng.randrange(len(leaves[i1]))
    many = rng.choice([i for i in range(k) if counts[i] >= 2])
    ja, jb = rng.sample(range(counts[many]), 2)
    qs = [Question("q_connects", "lookup",
                   f"Which device connects {leaves[i1][j1]} to the Router?", mids[i1],
                   fields=[f"n_leaf_{i1}_{j1}", "n_router"] + mid_f),
          Question("q_count", "count", f"How many devices connect directly to {mids[many]}?",
                   str(counts[many]), canon="number",
                   fields=[f"n_mid_{many}"] + [f"n_leaf_{many}_{j}" for j in range(counts[many])]),
          rng.choice((Question("q_modem", "lookup",
                               "Which device connects the Router to the Internet?", "Modem",
                               fields=["n_router", "n_modem", "n_internet"]),
                      Question("q_pair", "lookup",
                               f"Which device connects {leaves[many][ja]} and "
                               f"{leaves[many][jb]}?", mids[many],
                               fields=[f"n_leaf_{many}_{ja}", f"n_leaf_{many}_{jb}"] + mid_f))),
          Question("q_title", "lookup", "What is the title of the diagram?", title,
                   fields=["diagram_title"])]
    rng.shuffle(qs)
    ans = {"q_title": "diagram_title"}
    data = {"template": "network", "title": title, "switches": mids,
            "devices": {mids[i]: leaves[i] for i in range(k)}}
    return _finish(c, rng, abstain, qs, ans, data, "network",
                   ("q_ip", "What IP address is shown for the router?", "router_ip",
                    lambda: f"10.{rng.randint(1, 250)}.{rng.randint(1, 250)}.1"), "network diagram")


# ============================================================================ sequence diagram
_PARTS = (  # (title, participants, messages in a natural order)
    ("Order flow", ("Customer", "Shop App", "Payments", "Warehouse"),
     ("Send order", "Check stock", "Stock confirmed", "Charge card", "Payment approved",
      "Confirm order", "Book pickup", "Pickup booked", "Send receipt", "Update status")),
    ("Appointment flow", ("Patient", "Front Desk", "Scheduler", "Clinic"),
     ("Ask for slot", "Check calendar", "Offer slot", "Accept slot", "Hold room",
      "Room held", "Confirm visit", "Send reminder", "Check in", "Notify doctor")),
    ("Booking flow", ("Member", "Booking App", "Calendar", "Mail Service"),
     ("Request booking", "Find free time", "Time found", "Show options", "Pick option",
      "Save booking", "Booking saved", "Send confirmation", "Email sent", "Request change")),
    ("Repair request flow", ("Tenant", "Portal", "Repairs Team", "Contractor"),
     ("Report issue", "Create ticket", "Assign job", "Book visit", "Visit booked",
      "Share visit time", "Job done", "Close ticket", "Ask for rating", "Send rating")),
)


def _sequence(rng, *, theme, scale, abstain):
    inv = Inventor(rng)
    w, h = LOGICAL_SIZES["sequence"]
    c = Canvas(w, h, scale=scale, theme=theme)
    title, parts, pool = rng.choice(_PARTS)
    parts = list(parts)[: rng.randint(3, 4)]
    p = len(parts)
    n = rng.randint(5, 8)
    labels = [pool[i] for i in sorted(rng.sample(range(len(pool)), n))]
    msgs = []
    prev_to = rng.randrange(p)
    for i in range(n):
        frm = prev_to if rng.random() < 0.7 else rng.randrange(p)
        to = rng.choice([j for j in range(p) if j != frm])
        msgs.append((frm, to, labels[i]))
        prev_to = to
    _title(c, 40, 22, title)
    xs = [60 + (w - 120) * (i + 0.5) / p for i in range(p)]
    top = 64
    bh = 40
    y_msgs = [top + bh + 50 + i * 62 for i in range(n)]
    for x in xs:
        _dashed(c, x, top + bh, y_msgs[-1] + 40)
    for i, x in enumerate(xs):
        bw = _box_w(c, parts[i], 14, 16, 120)
        c.rect(x - bw / 2, top, x + bw / 2, top + bh, fill="surface", outline="border", radius=6)
        _label(c, x, top + bh / 2, parts[i], 14, f"part_{i}", bold=True)
    for i, (frm, to, lab) in enumerate(msgs):
        y = y_msgs[i]
        _arrow(c, [(xs[frm], y), (xs[to], y)], color="accent", width=1.8)
        mid = (xs[frm] + xs[to]) / 2
        c.text_center(mid, y - 22, lab, size=13, field=f"msg_{i}")
    pf = [f"part_{i}" for i in range(p)]
    mf = [f"msg_{i}" for i in range(n)]
    a = rng.randrange(1, n)
    b = rng.randrange(n - 1)
    who = rng.randrange(p)
    sent = sum(1 for m in msgs if m[0] == who)
    qs = [Question("q_first", "lookup", "Who sends the first message?", parts[msgs[0][0]],
                   fields=pf + [mf[0]]),
          Question("q_receiver", "lookup", f"Who receives the message '{msgs[a][2]}'?",
                   parts[msgs[a][1]], fields=pf + [mf[a]]),
          Question("q_after", "lookup", f"Which message comes right after '{msgs[b][2]}'?",
                   msgs[b + 1][2], fields=mf),
          Question("q_count", "count", f"How many messages does {parts[who]} send?", str(sent),
                   canon="number", fields=pf + mf)]
    rng.shuffle(qs)
    data = {"template": "sequence", "title": title, "participants": parts,
            "messages": [{"from": parts[f], "to": parts[t], "label": l} for f, t, l in msgs]}
    return _finish(c, rng, abstain, qs, {}, data, "sequence",
                   ("q_timeout", "What timeout value is given for the payment step?", "timeout",
                    lambda: f"{rng.randint(5, 90)} seconds"), "sequence diagram")


# ============================================================================ map
_PLACES = ("Library", "Post Office", "Fire Station", "Bakery", "Town Hall", "Clinic", "Park",
           "School", "Museum", "Market", "Pharmacy", "Pool", "Bus Depot", "Cinema", "Garden")


def _map(rng, *, theme, scale, abstain):
    inv = Inventor(rng)
    w, h = LOGICAL_SIZES["map"]
    c = Canvas(w, h, scale=scale, theme=theme)
    vx = [240, 480, 720]
    hy = [190, 370, 550]
    sw = 36
    cols = [(0, vx[0] - sw / 2), (vx[0] + sw / 2, vx[1] - sw / 2), (vx[1] + sw / 2, vx[2] - sw / 2),
            (vx[2] + sw / 2, w)]
    rows = [(60, hy[0] - sw / 2), (hy[0] + sw / 2, hy[1] - sw / 2),
            (hy[1] + sw / 2, hy[2] - sw / 2), (hy[2] + sw / 2, h)]
    vnames, hnames = [], []
    while len(vnames) < 3:
        s = f"{inv.surname()} Ave"
        if s not in vnames:
            vnames.append(s)
    while len(hnames) < 3:
        s = f"{inv.surname()} St"
        if s not in hnames and s.split()[0] not in [v.split()[0] for v in vnames]:
            hnames.append(s)
    # place 6-9 labelled places in distinct blocks, with at least one east pair and north pair
    blocks = [(r, cc) for r in range(4) for cc in range(4)]
    for _ in range(200):
        chosen = rng.sample(blocks, rng.randint(6, 9))
        cs = set(chosen)
        east = [(b, (b[0], b[1] + 1)) for b in chosen if (b[0], b[1] + 1) in cs]
        north = [(b, (b[0] - 1, b[1])) for b in chosen if (b[0] - 1, b[1]) in cs]
        if east and north:
            break
    names = rng.sample(_PLACES, len(chosen))
    place = dict(zip(chosen, names))
    land = (225, 236, 220) if theme == "light" else (36, 44, 38)
    block = (246, 244, 236) if theme == "light" else (52, 54, 58)
    road = (200, 200, 206) if theme == "light" else (84, 86, 92)
    c.rect(0, 0, w, h, fill=land)
    _title(c, 24, 18, f"{inv.town()} - town center", 18)
    for (r, cc) in blocks:
        x0, x1 = cols[cc]
        y0, y1 = rows[r]
        c.rect(x0 + 8, y0 + 8, x1 - 8, y1 - 8, fill=block, radius=6)
    for x in vx:
        c.rect(x - sw / 2, 46, x + sw / 2, h, fill=road)
    for y in hy:
        c.rect(0, y - sw / 2, w, y + sw / 2, fill=road)
    # compass
    c.polygon([(w - 40, 14), (w - 48, 38), (w - 32, 38)], fill="fg")
    c.text_center(w - 40, 40, "N", size=12, bold=True)
    # street names: avenues on a sign at the top of the road, streets inside the band
    for i, x in enumerate(vx):
        tw = c.text_width(vnames[i], 12, True)
        c.rect(x - tw / 2 - 8, 42, x + tw / 2 + 8, 62, fill=road, radius=5)
        c.text_center(x, 46, vnames[i], size=12, bold=True, field=f"ave_{i}")
    for i, y in enumerate(hy):
        c.text(16, y - 8, hnames[i], size=12, bold=True, field=f"st_{i}")
    for (r, cc) in sorted(chosen):
        x0, x1 = cols[cc]
        y0, y1 = rows[r]
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        if r == 0:
            my += 10
        c.ellipse(mx - 6, my - 26, mx + 6, my - 14, fill="accent")
        _label(c, mx, my + 4, place[(r, cc)], 14, f"place_{r}_{cc}", bold=True)
    pf = [f"place_{r}_{cc}" for (r, cc) in sorted(chosen)]
    (ea, eb) = rng.choice(east)
    (na, nb) = rng.choice(north)
    q_east = Question("q_east", "lookup",
                      f"Which place is on the block directly east of the {place[ea]}?",
                      place[eb], fields=pf)
    q_north = Question("q_north", "lookup",
                       f"Which place is on the block directly north of the {place[na]}?",
                       place[nb], fields=pf)
    q_between = Question("q_between", "lookup",
                         f"Which avenue runs between the {place[ea]} and the {place[eb]}?",
                         vnames[ea[1]], fields=[f"place_{ea[0]}_{ea[1]}",
                                                f"place_{eb[0]}_{eb[1]}"] +
                         [f"ave_{i}" for i in range(3)])
    q_count = Question("q_count", "count", "How many places are labelled on the map?",
                       str(len(chosen)), canon="number", fields=pf)
    sa, sb = (na, nb)
    q_street = Question("q_street", "lookup",
                        f"Which street runs between the {place[sb]} and the {place[sa]}?",
                        hnames[sb[0]], fields=[f"place_{sa[0]}_{sa[1]}",
                                               f"place_{sb[0]}_{sb[1]}"] +
                        [f"st_{i}" for i in range(3)])
    qs = [q_east, q_between] + rng.sample([q_north, q_count, q_street], 2)
    rng.shuffle(qs)
    data = {"template": "map", "avenues": vnames, "streets": hnames,
            "places": [{"name": place[b], "row": b[0], "col": b[1]} for b in sorted(chosen)]}
    return _finish(c, rng, abstain, qs, {}, data, "map",
                   ("q_bus", "Which bus route stops at the Town Hall?", "bus_route",
                    lambda: f"Route {rng.randint(10, 99)}{rng.choice('ABC')}"), "map")
