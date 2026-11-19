"""Score a day plan against checkable rules and the ledger. Pure.

Two inputs produce a plan:

* an Ask answer to a planning request ("Plan my day"): free text, parsed into
  timed blocks by `parse_plan`. This is the model-written plan.
* the Day tab's own plan (GET /assistant/today: `blocks` and
  `workspace.proposed_plan.blocks`), built by deterministic code
  (service/assistant/today.py `build_plan`). It is the same for every model and
  is scored once per profile as Harbor's baseline (`blocks_from_today`).

Rules (each evaluation is one check; a violation fails it):

  R1 no_overlap       no two blocks overlap by more than 5 minutes, among the overlaps the plan
                      creates: two blocks that keep two fixed items already overlapping on the
                      true schedule are an existing conflict (`existing_conflicts`, "existing
                      conflict not resolved"), reported and not scored
  R2 fixed_kept       each fixed item of the day is in the plan at its own time (±10 min)
  R3 change_honoured  a moved or cancelled item is never kept at its old slot

One mistake counts once: a kept old slot is one R3 violation, and the other rules the same
block trips (its item not at the new time, R2; overlaps of the old-slot block, R1) are listed in
that violation's `also`, not scored.
  R4 deadline_block   a deadline due within 3 days gets a work block, ending before it falls due
  R5 working_hours    work blocks the plan chose sit inside working hours
  R6 travel_kept      nothing is planned in the travel time before an away event
  R7 personal_kept    personal obligations (a pickup, a doctor) are in the plan at their time
  R8 not_in_past      nothing new is planned before "now"

Coverage: share of the day's ledger tasks (deadline, owed, task with a duration)
that the plan gives a block. When the key gives no such task the check cannot run: coverage
is reported as missing (rate None) and counts 0 in the score, never 1.0.

A plan written as text is read one line at a time, and a line that gives several start times in
prose is read clause by clause. A time with another day's word right beside it ("tomorrow night at
7 PM", "Saturday at 8 pm") is not a block of the plan day, and the same item given twice at one time
is one block (edat-iae-0.3.3).

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


# A day named next to a time: "tomorrow night at 7 PM", "Saturday at 8 pm", "9:00 on Thursday".
_WEEKDAY = r"(?:mon|tues?|wed(?:nes)?|thu(?:rs?)?|fri|satur|sun)day|mon|tues?|wed|thu(?:rs?)?|fri"
_DAY_WORD = (rf"tomorrow|yesterday|today|tonight|{_WEEKDAY}|(?:{matcher._MONTH_RE})\s+\d{{1,2}}(?:st|nd|rd|th)?"
             r"|\d{1,2}/\d{1,2}")
_DAY_BEFORE = re.compile(rf"\b({_DAY_WORD})(?:'s)?(?:\s+(?:morning|afternoon|evening|night))?\s*,?\s*"
                         r"(?:(?:starting\s+)?(?:at|from|by|around|@)\s*)?(?:\*\*|__)?\s*$", re.I)
_DAY_AFTER = re.compile(rf"^\s*(?:\*\*|__)?\s*,?\s*(?:(?:on|this|next)\s+)?({_DAY_WORD})\b", re.I)
# A time that only bounds something else ("before the 5:00 deadline") starts no block of its own.
_BOUND_BEFORE = re.compile(r"\b(?:by|before|until|till|due(?:\s+at)?|no later than|deadline(?:\s+(?:is|of))?)"
                           r"\s+(?:the\s+)?(?:\*\*|__)?\s*$", re.I)
_SENTENCE = re.compile(r"(?<![ap]\.m\.)(?<!\b[DMS][rst]\.)(?<!\bMrs\.)(?<=[.!?])\s+(?=[\"'*_(\[]*[A-Z])")
_CLAUSE = re.compile(r"\s*[;,]\s+|\s+(?:and then|then|followed by|and)\s+", re.I)
_STOP = {"with", "your", "have", "about", "from", "that", "this", "then", "after", "before", "today",
         "tonight", "starting", "followed", "remember", "reminder", "scheduled", "don't", "forget"}


def _time_spans(text: str) -> list[tuple[int, int]]:
    """Where the clock times in `text` are, a range counted once: matcher.time_mentions' order."""
    spans: list[tuple[int, int]] = []
    for mt in _TIME_TEXT.finditer(text):
        spans.append(mt.span())
    for mt in matcher._AT_HOUR.finditer(text):
        a, b = mt.span(1)
        if not any(x <= a < y for x, y in spans):
            spans.append((a, b))
    return sorted(spans)


def _other_day(piece: str, span: tuple[int, int], today) -> bool:
    """Whether the time at `span` is said to be on another day ("tomorrow at 9:00 PM", "Saturday
    at 8 pm", "7 PM on Thursday"). Only a day word right beside the time counts: "9:15 am: reply
    to Katrin about Thursday's check" is a block today."""
    for m in (_DAY_BEFORE.search(piece[:span[0]]), _DAY_AFTER.match(piece[span[1]:])):
        if m:
            days = matcher.dates(m.group(1), today)
            return bool(days) and today not in days
    return False


def _starts(piece: str) -> list[tuple[int, int]]:
    """The spans of times in `piece` that start something: not the end of a range and not a bound."""
    return [s for s in _time_spans(piece) if not _BOUND_BEFORE.search(piece[:s[0]])]


def _split(line: str) -> list[str]:
    """One piece per planned item. A line that gives several start times in prose ("starting with
    the shift at 7:00, followed by the standup at 9:00") is cut into sentences, then clauses. A
    clause with no start time of its own stays in its sentence: it joins the clause after it ("Your
    focus then shifts | to the standup at 9:00"), or, last in the sentence, the one before."""
    if len(_starts(line)) < 2:
        return [line]
    out: list[str] = []
    for sentence in _SENTENCE.split(line):
        if len(_starts(sentence)) < 2:
            out.append(sentence)
            continue
        cut = [c for c in _CLAUSE.split(sentence) if c and c.strip()]
        mine: list[str] = []
        carry = ""
        for c in cut:
            if not _starts(c):
                carry = f"{carry} {c}".strip()
                continue
            mine.append(f"{carry} {c}".strip())
            carry = ""
        if carry:
            if mine:
                mine[-1] += ", " + carry
            else:
                mine.append(carry)
        out += mine
    return out


def _words(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z'-]{3,}", matcher.fold(title)) if w not in _STOP}


def parse_plan(text: str, profile: Profile) -> list[Block]:
    """Timed blocks from a plan written as text, one per line, sentence or clause that has a time.

    A time said to be on another day ("tomorrow night at 7 PM") is not a block of the plan day.
    The same item given twice at one time (a summary sentence, then the list) is one block."""
    work = profile.working_window()
    today = profile.anchor.date()
    pieces = []
    for line in text.splitlines():
        pieces += _split(line)
    raw = []
    for piece in pieces:
        mentions = matcher.time_mentions(piece)
        if not mentions:
            continue
        starts = _starts(piece)
        if starts and _other_day(piece, starts[0], today):
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
        title = re.sub(r"^(?:(?:and then|then|followed by|and|starting with|from|at|by)\s+)+", "", title, flags=re.I)
        title = re.sub(r"\s+(?:at|by|from|around)$", "", title, flags=re.I)
        same = next((r for r in raw if r[0] == start and _words(r[2]) & _words(title)), None)
        if same is not None:
            # The same item again at the same time: one block, which takes both titles' words.
            same[1] = same[1] or end
            same[2] = f"{same[2]} / {title}"
            continue
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
    """A key item ties to a block by any one of its phrases. A generated schedule item's words
    are only its title, so it ties by most of them (more than half): "Reminder for Grandma's 80th
    birthday" at 08:00 is not the 21:00 "Grandma's 80th birthday video call (from Lima)"."""
    phrases = _phrases(item)
    hits = sum(1 for a in phrases if matcher.phrase_hit(a, block.title))
    if item.get("generated") == "schedule" and not item.get("plan_words"):
        return hits * 2 > len(phrases)
    return hits > 0


@dataclass
class DayScore:
    score: float = 0.0
    validity: float | None = None
    coverage: dict = field(default_factory=dict)
    violations: list[dict] = field(default_factory=list)
    existing_conflicts: list[str] = field(default_factory=list)
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

    timed = [it for it in ledger if it.get("start") and profile.on_day(it["start"], day)
             and it["id"] not in superseded and it.get("fixed")]
    fixed_blocks: set[int] = set()
    is_item: dict[int, str] = {}          # block -> the fixed item it keeps at that item's time
    stale_of: dict[int, dict] = {}        # block -> the R3 violation of the old slot it keeps
    deferred: list[tuple[str, str, str]] = []   # (rule, detail, item id): decided once R3 is known
    for it in timed:
        start = profile.minutes(it["start"])
        # The old slot's words are a generated title, so they tie a block by most of them: a
        # "DLQ replay" block is not the "Design review: ... (DLQ v2)" that moved.
        mapped = [i for i, b in enumerate(blocks) if _maps(b, it)
                  or (it.get("supersedes") and _maps(b, profile.item(it["supersedes"])))]
        at_time = [i for i in mapped if abs(blocks[i].start - start) <= TOLERANCE_MIN]
        if not mapped:
            # A block at the item's time with no matching words still keeps it.
            at_time = [i for i, b in enumerate(blocks) if abs(b.start - start) <= TOLERANCE_MIN]
        fixed_blocks.update(at_time or mapped)
        for i in at_time:
            is_item.setdefault(i, it["id"])
        rule = "R7" if it.get("personal") else "R2"
        s.checks += 1
        if not at_time:
            deferred.append((rule, f"{it['id']} not at {_hm(start)}"
                             + (f" (planned at {_hm(blocks[mapped[0]].start)})" if mapped else " (missing)"), it["id"]))
        if it["type"] == "change" and it.get("supersedes"):
            old = profile.item(it["supersedes"])
            old_start = profile.minutes(old["start"])
            stale = [i for i in mapped if abs(blocks[i].start - old_start) <= TOLERANCE_MIN]
            if not mapped and not at_time and not _phrases(it) and not _phrases(old):
                # The key gives no words for it at all (times only): a block at its old time keeps
                # the old slot. With words, only a block that shares them does (above).
                stale = [i for i, b in enumerate(blocks) if abs(b.start - old_start) <= TOLERANCE_MIN]
            s.checks += 1
            if stale:
                v = {"rule": "R3", "detail": f"{it['id']}: kept at the old time {_hm(old_start)} "
                                             f"(moved to {_hm(start)} the same day)", "item": it["id"], "also": []}
                s.violations.append(v)
                for i in stale:
                    stale_of.setdefault(i, v)     # the first old slot a block keeps owns its knock-ons
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
            continue                          # moved within the day: checked above
        old_start = profile.minutes(old["start"])
        own = {matcher.fold(w) for w in _phrases(old)}
        other = task_words - own              # "Lab 4 report" at 2:00 is a work block, not the old lab
        kept = [i for i, b in enumerate(blocks) if abs(b.start - old_start) <= TOLERANCE_MIN
                and any(matcher.phrase_hit(w, b.title) for w in own)
                and not any(matcher.phrase_hit(w, b.title) for w in other)]
        s.checks += 1
        if kept:
            v = {"rule": "R3", "detail": f"{it['id']}: kept at the old time {_hm(old_start)} "
                                         f"({'moved to another day' if it.get('start') else 'cancelled'})",
                 "item": it["id"], "also": []}
            s.violations.append(v)
            for i in kept:
                stale_of.setdefault(i, v)

    # One mistake counts once: a kept old slot is scored under R3 (the most specific rule), and
    # the other rules it also trips (its item not at the new time, overlaps of the old-slot block)
    # are listed beside it, not scored.
    stale_items = {v["item"] for v in stale_of.values()}
    for rule, detail, lid in deferred:
        owner = next((v for v in stale_of.values() if v["item"] == lid), None)
        if owner is not None and lid in stale_items:
            owner["also"].append(f"{rule}: {detail}")
        else:
            fail(rule, detail)

    # R1: only overlaps the plan creates. Two blocks that keep two fixed items which already
    # overlap on the true schedule are an existing conflict, reported apart and not scored.
    true_slot = {it["id"]: (profile.minutes(it["start"]), profile.minutes(it["end"]))
                 for it in timed if it.get("end") and profile.on_day(it["end"], day)}
    for i, a in enumerate(blocks):
        for j in range(i + 1, len(blocks)):
            b = blocks[j]
            overlap = min(a.end, b.end) - max(a.start, b.start)
            if overlap <= OVERLAP_MIN:
                continue
            detail = f"{a.title!r} {_hm(a.start)} overlaps {b.title!r} {_hm(b.start)}"
            owner = stale_of.get(i) or stale_of.get(j)
            x, y = is_item.get(i), is_item.get(j)
            if owner is not None:
                owner["also"].append(f"R1: {detail}")
            elif x and y and x != y and x in true_slot and y in true_slot and \
                    min(true_slot[x][1], true_slot[y][1]) - max(true_slot[x][0], true_slot[y][0]) > OVERLAP_MIN:
                s.existing_conflicts.append(f"existing conflict not resolved: {detail}")
            else:
                fail("R1", detail)
    s.checks += max(1, len(blocks) - 1)

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
