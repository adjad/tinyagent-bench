"""Score a day plan against checkable rules and the ledger. Pure.

Two inputs produce a plan:

* an Ask answer to a planning request ("Plan my day"): free text, parsed into
  timed blocks by `parse_plan`. This is the model-written plan.
* the Day tab's own plan (GET /assistant/today: `blocks` and
  `workspace.proposed_plan.blocks`), built by deterministic code
  (service/assistant/today.py `build_plan`). It is the same for every model and
  is scored once per profile as Harbor's baseline (`blocks_from_today`).

Rules (each evaluation is one check; a violation fails it):

  R1 no_overlap       no two blocks overlap by more than 5 minutes
  R2 fixed_kept       each fixed item of the day is in the plan at its own time (±10 min)
  R3 change_honoured  a moved item sits at its new time, never at the old one
  R4 deadline_block   a deadline due within 3 days gets a work block, ending before it falls due
  R5 working_hours    work blocks the plan chose sit inside working hours
  R6 travel_kept      nothing is planned in the travel time before an away event
  R7 personal_kept    personal obligations (a pickup, a doctor) are in the plan at their time
  R8 not_in_past      nothing new is planned before "now"

Coverage: share of the day's ledger tasks (deadline, owed, task with a duration)
that the plan gives a block. When the key gives no such task the check cannot run: coverage
is reported as missing (rate None) and counts 0 in the score, never 1.0.

A block whose end the plan does not give takes the length of the fixed item that starts at
its time (±10 min), else it runs to the next block (at most an hour). "Overdue by 5 days" is
an amount, not a clock time (matcher.py).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from scripts.productbench import matcher
from scripts.productbench.profile import Profile

TOLERANCE_MIN = 10
OVERLAP_MIN = 5
HORIZON_DAYS = 3


@dataclass
class Block:
    start: int          # minutes after midnight, plan day
    end: int
    title: str
    line: str = ""

    def as_dict(self) -> dict:
        return {"start": _hm(self.start), "end": _hm(self.end), "title": self.title}


def _hm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def _pick(cands: list[int], work: tuple[int, int]) -> int:
    """Choose am or pm for a time written without one, the way people write plans:
    7 to 11 is the morning, 12 is noon, 1 to 6 is the afternoon."""
    if len(cands) == 1:
        return cands[0]
    morning = min(cands)
    return morning if 7 * 60 <= morning < 12 * 60 else max(cands)


_BULLET = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s*")
_TIME_TEXT = re.compile(
    r"\b\d{1,2}(?:[:.]\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?\s*(?:-|–|—|to|until|till)\s*"
    r"\d{1,2}(?:[:.]\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?|\b\d{1,2}[:.]\d{2}\s*(?:a\.?m\.?|p\.?m\.?)?|"
    r"\b\d{1,2}\s*(?:a\.?m\.?|p\.?m\.?)|\bnoon\b", re.I)


def parse_plan(text: str, profile: Profile) -> list[Block]:
    """Timed blocks from a plan written as text, one per line (or sentence) that has a time."""
    work = profile.working_window()
    pieces = []
    for line in text.splitlines():
        if len(matcher.time_mentions(line)) > 2 and ";" in line:
            pieces += line.split(";")
        else:
            pieces.append(line)
    raw = []
    for piece in pieces:
        mentions = matcher.time_mentions(piece)
        if not mentions:
            continue
        start = _pick(mentions[0], work)
        end = None
        rng = matcher._RANGE.search(piece)
        if rng and len(mentions) >= 2:
            end = _pick(mentions[1], work)
            if end <= start:
                end = None
        title = _TIME_TEXT.sub(" ", piece)
        title = _BULLET.sub("", title.replace("**", "").replace("__", ""))
        title = re.sub(r"^\W+|\W+$", "", re.sub(r"\s+", " ", title)).strip()
        title = re.sub(r"^(?:from|at|by)\s+", "", title, flags=re.I)
        raw.append([start, end, title, piece.strip()])
    raw.sort(key=lambda r: r[0])
    fixed = [(profile.minutes(it["start"]), profile.minutes(it["end"])) for it in profile.ledger
             if it.get("fixed") and it.get("start") and it.get("end") and profile.on_day(it["start"])
             and profile.on_day(it["end"])]
    blocks = []
    for i, (start, end, title, line) in enumerate(raw):
        if end is None:
            # "12:15 GIS meeting" runs as long as the meeting does, not until the next line.
            own = next((e - s for s, e in fixed if abs(s - start) <= TOLERANCE_MIN and e > s), None)
            if own:
                end = start + own
            else:
                nxt = next((r[0] for r in raw[i + 1:] if r[0] > start), None)
                end = min(nxt, start + 60) if nxt else start + 30
        blocks.append(Block(start, end, title, line))
    return blocks


def blocks_from_today(snapshot: dict, profile: Profile) -> list[Block]:
    """The Day tab's plan: authored blocks plus the proposed plan's suggestions."""
    rows = list(snapshot.get("blocks") or [])
    proposed = ((snapshot.get("workspace") or {}).get("proposed_plan") or {}).get("blocks") or []
    seen = {(r.get("title"), r.get("start")) for r in rows}
    rows += [r for r in proposed if (r.get("title"), r.get("start")) not in seen]
    out = []
    for r in rows:
        try:
            s = datetime.fromtimestamp(float(r["start"]), profile.tz)
            e = datetime.fromtimestamp(float(r["end"]), profile.tz)
        except (KeyError, TypeError, ValueError):
            continue
        if s.date().isoformat() != profile.today:
            continue
        out.append(Block(s.hour * 60 + s.minute, e.hour * 60 + e.minute,
                         str(r.get("title") or ""), str(r.get("kind") or "")))
    return sorted(out, key=lambda b: b.start)


def _now_minutes(profile: Profile, rules: dict) -> int:
    """'now' for the rules: the anchor, or the case's later `now` ("14:00")."""
    if rules.get("now"):
        h, m = (int(x) for x in str(rules["now"]).split(":"))
        return h * 60 + m
    return profile.minutes(profile.data["anchor"])


def _phrases(item: dict) -> list[str]:
    """Words that tie a plan block to a ledger item: its `plan_words` (shared.py: the key's
    distinctive phrases and its to-do line's words), else its plain anchors."""
    return list(item.get("plan_words") or [a for a in item.get("anchors", []) if not a.startswith(("@", "#"))])


def _maps(block: Block, item: dict) -> bool:
    return any(matcher.phrase_hit(a, block.title) for a in _phrases(item))


@dataclass
class DayScore:
    score: float = 0.0
    validity: float | None = None
    coverage: dict = field(default_factory=dict)
    violations: list[dict] = field(default_factory=list)
    checks: int = 0
    blocks: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def score_plan(blocks: list[Block], profile: Profile, rules: dict | None = None) -> DayScore:
    rules = rules or {}
    s = DayScore(blocks=[b.as_dict() for b in blocks])
    if not blocks:
        s.notes.append("no timed blocks found")
        return s
    day = rules.get("day") or profile.today
    now = _now_minutes(profile, rules) if day == profile.today else 0
    work_lo, work_hi = profile.working_window()
    ledger = profile.ledger
    superseded = {it["supersedes"] for it in ledger if it.get("supersedes")}

    def fail(rule, detail):
        s.violations.append({"rule": rule, "detail": detail})

    def check(rule, ok, detail):
        s.checks += 1
        if not ok:
            fail(rule, detail)

    # R1
    for i, a in enumerate(blocks):
        for b in blocks[i + 1:]:
            overlap = min(a.end, b.end) - max(a.start, b.start)
            if overlap > OVERLAP_MIN:
                fail("R1", f"{a.title!r} {_hm(a.start)} overlaps {b.title!r} {_hm(b.start)}")
    s.checks += max(1, len(blocks) - 1)

    timed = [it for it in ledger if it.get("start") and profile.on_day(it["start"], day)
             and it["id"] not in superseded and it.get("fixed")]
    fixed_blocks: set[int] = set()
    for it in timed:
        start = profile.minutes(it["start"])
        mapped = [i for i, b in enumerate(blocks) if _maps(b, it)
                  or (it.get("supersedes") and any(matcher.phrase_hit(w, b.title)
                                                   for w in _phrases(profile.item(it["supersedes"]))))]
        at_time = [i for i in mapped if abs(blocks[i].start - start) <= TOLERANCE_MIN]
        if not mapped:
            # A block at the item's time with no matching words still keeps it.
            at_time = [i for i, b in enumerate(blocks) if abs(b.start - start) <= TOLERANCE_MIN]
        fixed_blocks.update(at_time or mapped)
        rule = "R7" if it.get("personal") else "R2"
        check(rule, bool(at_time), f"{it['id']} not at {_hm(start)}"
              + (f" (planned at {_hm(blocks[mapped[0]].start)})" if mapped and not at_time else
                 "" if mapped else " (missing)"))
        if it["type"] == "change" and it.get("supersedes"):
            old = profile.item(it["supersedes"])
            old_start = profile.minutes(old["start"])
            stale = [i for i in mapped if abs(blocks[i].start - old_start) <= TOLERANCE_MIN]
            check("R3", not stale and bool(at_time), f"{it['id']} at old time {_hm(old_start)}"
                  if stale else f"{it['id']} not at new time {_hm(start)}")
        if it.get("travel_min"):
            lead = start - int(it["travel_min"])
            clash = [b for i, b in enumerate(blocks) if i not in (at_time or mapped)
                     and b.start < start and b.end > lead]
            check("R6", not clash, f"{it['id']}: {', '.join(repr(b.title) for b in clash)} "
                  f"inside {it['travel_min']} min travel before {_hm(start)}")

    # R3 for a slot that moved to another day or was cancelled: the old slot is not kept.
    task_words = {matcher.fold(w) for t in ledger if t.get("due") for w in _phrases(t)}
    for it in ledger:
        if not it.get("supersedes") or it["id"] in superseded:
            continue
        old = profile.item(it["supersedes"])
        if not (old.get("start") and profile.on_day(old["start"], day)):
            continue
        if it.get("start") and profile.on_day(it["start"], day):
            continue                          # moved within the day: checked with R2 above
        old_start = profile.minutes(old["start"])
        own = {matcher.fold(w) for w in _phrases(old)}
        other = task_words - own              # "Lab 4 report" at 2:00 is a work block, not the old lab
        kept = [b for b in blocks if abs(b.start - old_start) <= TOLERANCE_MIN
                and any(matcher.phrase_hit(w, b.title) for w in own)
                and not any(matcher.phrase_hit(w, b.title) for w in other)]
        check("R3", not kept, f"{it['id']}: kept at the old time {_hm(old_start)} "
              f"({'moved to another day' if it.get('start') else 'cancelled'})")

    tasks = rules.get("tasks") or [
        it["id"] for it in ledger
        if it["type"] in ("deadline", "owed", "task") and it.get("duration_min")
        and it["id"] not in superseded and it.get("due")
        and profile.parse(it["due"]).date() <= (profile.anchor + timedelta(days=HORIZON_DAYS)).date()]
    covered = []
    for lid in tasks:
        it = profile.item(lid)
        mapped = [b for b in blocks if _maps(b, it)]
        if mapped:
            covered.append(lid)
        due = profile.parse(it["due"])
        due_min = profile.minutes(it["due"]) if due.date().isoformat() == day else 24 * 60
        if it["type"] == "deadline":
            ok = any(b.end <= due_min for b in mapped)
            check("R4", ok, f"{lid}: no work block before it falls due" if not mapped
                  else f"{lid}: block ends after it falls due")
        for b in mapped:
            if not it.get("personal"):
                check("R5", work_lo <= b.start and b.end <= work_hi,
                      f"{lid}: {_hm(b.start)}-{_hm(b.end)} outside working hours")
    for i, b in enumerate(blocks):
        if i in fixed_blocks:
            continue
        check("R8", b.start >= now - TOLERANCE_MIN, f"{b.title!r} at {_hm(b.start)} is before now")
    s.coverage = {"tasks": tasks, "covered": covered,
                  "rate": round(len(covered) / len(tasks), 3) if tasks else None}
    s.validity = round(1 - len(s.violations) / max(1, s.checks), 3)
    cov = s.coverage["rate"]
    if cov is None:
        s.notes.append("coverage not checked: the key gives no task due within "
                       f"{HORIZON_DAYS} days; scored as missing (0), not 1.0")
        cov = 0.0
    s.score = round(max(0.0, 0.6 * s.validity + 0.4 * cov), 3)
    return s


def score_slot(text: str, profile: Profile, case: dict) -> DayScore:
    """A request to fit one task ("when can I do two hours on X today?"): the
    first proposed block must be free of fixed items and travel, inside working
    hours, in the future, long enough and before the task falls due."""
    blocks = parse_plan(text, profile)
    s = DayScore(blocks=[b.as_dict() for b in blocks])
    if not blocks:
        s.notes.append("no time proposed")
        return s
    b = blocks[0]
    if case.get("minutes") and b.end - b.start < case["minutes"] - TOLERANCE_MIN:
        if not matcher._RANGE.search(b.line):
            b = Block(b.start, b.start + case["minutes"], b.title, b.line)
    superseded = {it["supersedes"] for it in profile.ledger if it.get("supersedes")}
    busy = []
    for it in profile.ledger:
        if it.get("start") and profile.on_day(it["start"]) and it["id"] not in superseded and it.get("fixed"):
            st, en = profile.minutes(it["start"]), profile.minutes(it.get("end") or it["start"])
            busy.append((st - int(it.get("travel_min", 0)), en, it["id"]))
    work_lo, work_hi = profile.working_window()
    now = _now_minutes(profile, case)

    def check(rule, ok, detail):
        s.checks += 1
        if not ok:
            s.violations.append({"rule": rule, "detail": detail})

    clash = [lid for st, en, lid in busy if min(b.end, en) - max(b.start, st) > OVERLAP_MIN]
    check("R1", not clash, f"{_hm(b.start)}-{_hm(b.end)} clashes with {clash}")
    check("R5", work_lo <= b.start and b.end <= work_hi, f"{_hm(b.start)}-{_hm(b.end)} outside working hours")
    check("R8", b.start >= now - TOLERANCE_MIN, f"{_hm(b.start)} is before now")
    if case.get("minutes"):
        check("length", b.end - b.start >= case["minutes"] - TOLERANCE_MIN,
              f"{b.end - b.start} min offered, {case['minutes']} asked")
    s.validity = round(1 - len(s.violations) / max(1, s.checks), 3)
    s.score = 1.0 if not s.violations else 0.0
    return s
