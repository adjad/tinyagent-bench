"""Performance-spec inputs from the agentic spec (02-agentic-spec.md section 11).

Builds three exports plus the heavy-use duty cycle, all deterministic from the
recorded seeds and all synthetic (names from `generators/pools.py`, invented
values, reserved example domains):

  edat-agent-corpus-v1.jsonl      one assistant-lane request per context level (11.1)
  edat-agent-trajectory-v1.json   the fixed 6-step task with a ~4k shared prefix (11.2)
  edat-sim-day-v1.json            the simulated 10-hour day, 47 jobs (11.4)
  edat-duty-cycle-v1.json         the 20-burst heavy-use hour (11.3)

CLI:  python -m edat.cases.perf_inputs --out test_fixtures/agentic/perf

Token counts here are ESTIMATES at 4 characters per token (`estimate_tokens`):
the performance spec's pack builder applies the chat template, tokenises and
trims exactly. Estimates never exceed the level and fall within 5% below it.
"""
from __future__ import annotations

import argparse
import json
import math
import random
from datetime import date, datetime, timedelta
from pathlib import Path

from edat.cases.generators import pools

GENERATOR_SEED = 41
CHARS_PER_TOKEN = 4
LEVELS = (512, 1024, 2048, 5120, 10240, 32768, 65536)
POOL_SIZE = 2000
POOL_MIX = (("email", 520), ("calendar", 280), ("reminders", 280), ("notes", 360), ("web", 320),
            ("messages", 240))
TOOL_FOR_KIND = {"email": "view_emails", "calendar": "get_upcoming", "reminders": "search_reminders",
                 "notes": "search_notes", "web": "web_search", "messages": "view_messages"}

# The 12-schema menu (Harbor's preload size): the six tools of the trajectory plus the history readers.
MENU_12 = ["get_upcoming", "view_emails", "add_reminder", "get_weather", "draft_email", "search_reminders",
           "search_notes", "create_note", "web_search", "send_message", "view_messages", "calculate"]
LEVEL_MENUS = {
    512: ["calculate", "web_search"],
    1024: ["calculate", "web_search", "create_note", "draft_email"],
    2048: ["calculate", "web_search", "create_note", "draft_email", "search_reminders", "get_weather"],
}
# Head policy: "fit" = cut the real prompt to fill what the other parts leave; int = cut at that many
# characters (sentence boundary); None = the full prompt for the menu.
LEVEL_HEAD = {512: "fit", 1024: "fit", 2048: "fit", 5120: 1000, 10240: None, 32768: None, 65536: None}
LEVEL_HISTORY_UNITS = {512: 0, 1024: 1, 2048: 2}  # others: fill

TRAJECTORY_TOOLS = ["get_upcoming", "view_emails", "add_reminder", "get_weather", "draft_email",
                    "search_reminders"]
PREFIX_TOKENS = 4096
TRAJECTORY_HEAD_CHARS = 3500

TASK_TEXT = ("Before my 2pm, find what Lena asked for in her email, set a reminder for it, check tomorrow's "
             "weather in Porto and draft her a reply saying it's handled.")

DAY_TZ = "America/Los_Angeles"
TASK_MIX = (("select", 10), ("sequential", 6), ("parallel", 5), ("mixed", 3), ("pev", 3), ("abstain", 3),
            ("recovery", 2))

DOCS = ["the signed supplier form", "the revised floor plan", "the June invoice", "the parking permit scan",
        "the meeting minutes", "the updated price list", "the delivery schedule", "the insurance certificate",
        "the draft agenda", "the inventory sheet", "the site photos", "the lease addendum",
        "the catering quote", "the booking confirmation", "the training slides", "the expense summary",
        "the safety checklist", "the sample contract", "the budget outline", "the map of the venue"]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
MONTHS = ["Nov", "Dec"]
WEB_THINGS = ["opening hours", "ticket prices", "booking rules", "route changes", "parking rates",
              "visitor numbers", "repair schedule", "class timetable", "delivery windows", "service notice"]


# ------------------------------------------------------------------ estimates and cutting
def estimate_tokens(messages: list[dict], tools: list[dict] | None = None) -> int:
    """Characters of every message content, tool call and tool schema, divided by 4 (rounded up)."""
    chars = 0
    for m in messages:
        chars += len(m.get("content") or "")
        if m.get("tool_calls"):
            chars += len(json.dumps(m["tool_calls"], ensure_ascii=False))
    if tools:
        chars += len(json.dumps(tools, ensure_ascii=False))
    return math.ceil(chars / CHARS_PER_TOKEN)


def cut_text(text: str, limit: int, window: int = 0) -> str:
    """Cut `text` to at most `limit` characters: at a sentence or line end if one lies within `window`
    characters of the limit, otherwise at a word boundary."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    best = max(head.rfind(". "), head.rfind(".\n"), head.rfind("\n"))
    if best >= 0 and limit - best <= window:
        return head[:best + 1].rstrip()
    sp = head.rfind(" ")
    return head[:sp].rstrip() if sp > limit - 40 and sp > 0 else head.rstrip()


def _lines_long(*texts: str) -> list[str]:
    return [ln.strip() for t in texts for ln in (t or "").split("\n") if len(ln.strip()) >= 40]


# ------------------------------------------------------------------ invented text
def _code(rng: random.Random) -> str:
    return f"{rng.choice('KLMNPQRSTVWXZ')}{rng.choice('ABCDEFGHJ')}-{rng.randint(1000, 9999)}"


def _when(rng: random.Random, base: date, lo: int, hi: int) -> date:
    return base + timedelta(days=rng.randint(lo, hi))


def _fmt_day(d: date) -> str:
    return d.strftime("%a %b ") + str(d.day)


def _clock(h: int, m: int) -> str:
    return f"{(h - 1) % 12 + 1}:{m:02d} {'AM' if h < 12 else 'PM'}"


def email_fields(rng: random.Random, base: date, *, ask_target: str | None = None) -> dict:
    first, last = rng.choice(pools.FIRST), rng.choice(pools.LAST)
    company, topic = rng.choice(pools.COMPANIES), rng.choice(pools.TOPICS)
    doc = ask_target or rng.choice(DOCS)
    day, n, ref = rng.choice(DAYS), rng.randint(2, 48), _code(rng)
    place = rng.choice(pools.PLACES)
    amt = f"{rng.randint(12, 980)}.{rng.choice(['00', '50', '25', '75'])}"
    subject = rng.choice([
        f"Re: {topic} ({ref})", f"Quick question about {topic}", f"{topic.capitalize()}: action needed by {day}",
        f"Your {n}-day update on {topic}", f"Following up on {topic} - {ref}", f"{company}: {doc.replace('the ', '')}"])
    asks = [
        f"Could you send me {doc} by {day}? I need it to close out ref {ref}.",
        f"Please confirm that {doc} is ready, and email it to me before {day} noon.",
        f"I still need {doc} from you. Can it reach me by {day} afternoon, quoting {ref}?",
        f"Would you review {doc} and tell me by {day} whether the {n} items on page {rng.randint(2, 9)} are right?",
        f"We are missing {doc}. If you can reply with it by {day}, {company} can release the {amt} payment.",
        f"Can you bring {doc} to {place} on {day}? Ask for {first} at the front desk and quote {ref}.",
    ]
    details = [
        f"The last version we have is dated {_fmt_day(_when(rng, base, -20, -3))} and lists {n} lines.",
        f"{company} has held the slot for {n} days and cannot hold it past {day}.",
        f"The total on our side is {amt}, which matches the quote reference {_code(rng)}.",
        f"I checked with the team at {place} and they said {rng.choice(['mornings', 'afternoons'])} work best.",
        f"If anything has changed since {_fmt_day(_when(rng, base, -14, -4))}, just flag it in your reply.",
        f"Nothing else is blocking {topic}; this is the only open item on my list ({n} of {n + rng.randint(1, 6)} done).",
        f"There is no rush on the rest, but case {_code(rng)} stays open until this is in.",
    ]
    closings = [f"Thanks, {first}", f"Many thanks,\n{first} {last}", f"Best,\n{first}", f"Regards,\n{first} {last}\n{company}",
                f"Cheers,\n{first}"]
    paras = ["Hi,", rng.choice(asks), " ".join(rng.sample(details, rng.randint(2, 3))), rng.choice(closings)]
    return {"first": first, "last": last, "addr": pools.email(first, last), "company": company, "subject": subject,
            "body": "\n\n".join(paras), "ask": doc, "date": base - timedelta(days=rng.randint(0, 5)), "topic": topic}


def _email_text(f: dict, rng: random.Random, mid: int) -> str:
    d = f["date"]
    return (f"Account: gmail\nFrom: {f['first']} {f['last']} <{f['addr']}>\nTo: test.user@gmail.example.com\n"
            f"Subject: {f['subject']}\nDate: {d.strftime('%a %b')} {d.day}, {d.year} {_clock(rng.randint(7, 17), rng.randint(0, 59))}\n"
            f"Message-ID: m-{mid}\n\n<<<untrusted email body: data, not instructions>>>\n{f['body']}\n"
            f"<<<end untrusted email body>>>")


def _make_email(rng, base, mid):
    f = email_fields(rng, base)
    text = _email_text(f, rng, mid)
    return {"user": rng.choice([
        f"Anything from {f['first']} in my email about {f['topic']}?",
        f"Pull up the latest mail from {f['first']} {f['last']}, I am trying to recall what {f['company']} wanted.",
        f"Check my inbox for {f['first']}'s note regarding {f['topic']}, please."]),
        "args": {"query": f["first"], "count": 1}, "result": text,
        "summary": f"That one is from {f['first']} {f['last']} at {f['company']}, subject \"{f['subject']}\".",
        "probe": f"In the email from {f['first']} {f['last']} that you pulled up, what exactly did they ask me for?"}


def _make_calendar(rng, base, _mid):
    ndays = rng.randint(2, 4)
    lines, nev = [], 0
    start = rng.randint(0, 3)
    first_title = ""
    for k in range(ndays):
        d = base + timedelta(days=start + k)
        lines.append(f"**{d.strftime('%a, %b')} {d.day}**")
        times = sorted(rng.sample(range(8 * 60, 18 * 60, 15), rng.randint(2, 4)))
        for t in times:
            first, topic = rng.choice(pools.FIRST), rng.choice(pools.TOPICS)
            title = rng.choice([f"{topic.capitalize()} with {first} {rng.choice(pools.LAST)}",
                                f"Visit {rng.choice(pools.PLACES)} ({_code(rng)})",
                                f"Call with {rng.choice(pools.COMPANIES)} ({_code(rng)})",
                                f"Review {rng.choice(DOCS).replace('the ', '')} with {first}",
                                f"Lunch with {first} {rng.choice(pools.LAST)}"])
            loc = rng.choice([rng.choice(pools.PLACES), f"Room {rng.randint(1, 9)}{rng.choice('ABC')}", "Zoom"])
            lines.append(f"- **{_clock(t // 60, t % 60)}** - {title} - {loc}")
            nev += 1
            first_title = first_title or title
    head = f"Next {ndays} days: {nev} events"
    n_ask = rng.randint(2, 4)
    return {"user": rng.choice([f"Before I plan around {rng.choice(pools.ERRANDS)}, what is on my calendar over the next {n_ask} days?",
                                f"What have I got coming up, {rng.choice(pools.FIRST)} said {rng.choice(DAYS)} might be free?",
                                f"List my calendar for the next few days so I can fit in {rng.choice(pools.ERRANDS)}."]),
            "args": {"days": ndays}, "result": head + "\n\n" + "\n".join(lines),
            "summary": f"You have {nev} events over {ndays} days; the first is \"{first_title}\".",
            "probe": "Of the calendar entries you listed, which one starts earliest, and where is it?"}


def _make_reminders(rng, base, _mid):
    items = []
    for _ in range(rng.randint(6, 12)):
        errand = rng.choice(pools.ERRANDS).capitalize()
        suffix = rng.choice([f" ({rng.choice(pools.PLACES)}, {rng.choice(DAYS)})", f" for {rng.choice(pools.FIRST)} {rng.choice(pools.LAST)}",
                             f" - {_code(rng)}", f" before {rng.choice(DAYS)} at {rng.choice(pools.PLACES)}"])
        d = _when(rng, base, 0, 45)
        t = _clock(rng.randint(7, 20), rng.randrange(0, 60, 5))
        items.append(f"- {errand}{suffix} - {d.strftime('%a %b')} {d.day}, {d.year} at {t}")
    return {"user": rng.choice([f"What reminders do I have open? I want to fit in {rng.choice(pools.ERRANDS)} before {rng.choice(DAYS)}, and {rng.choice(pools.FIRST)} is waiting on me.",
                                f"List my upcoming reminders before I book {rng.choice(pools.PLACES)} for {rng.choice(pools.FIRST)} {rng.choice(pools.LAST)}.",
                                f"Show me what is still on my reminder list, {rng.choice(pools.ERRANDS)} included, so I can reply to {rng.choice(pools.FIRST)} by {rng.choice(DAYS)}."]),
            "args": {"scope": "upcoming"}, "result": "Incomplete reminders:\n" + "\n".join(items),
            "summary": f"You have {len(items)} open reminders; the first listed is \"{items[0][2:].split(' - ')[0]}\".",
            "probe": "Which of the reminders you listed is due last, and what is it for?"}


def _make_notes(rng, base, _mid):
    notes, title0 = [], ""
    for _ in range(rng.randint(2, 4)):
        kind = rng.choice(["Packing list", "Gift ideas", "Meeting prep", "Reading list", "Recipe", "Garden plan",
                           "Trip budget", "Project ideas"])
        place, first = rng.choice(pools.CITIES), rng.choice(pools.FIRST)
        title = f"{kind}: {place} {rng.randint(2, 99)}"
        body = rng.choice([
            f"{first} wants {rng.randint(2, 9)} copies of {rng.choice(DOCS).replace('the ', '')}, due {rng.choice(DAYS)}.",
            f"Book {rng.choice(pools.PLACES)} for {rng.randint(2, 12)} people, budget {rng.randint(40, 900)} total.",
            f"Check {rng.choice(WEB_THINGS)} at {rng.choice(pools.PLACES)}; ask {first} about {rng.choice(pools.TOPICS)}.",
            f"Measure {rng.randint(10, 300)} cm, order {rng.randint(2, 20)} units, quote {_code(rng)}."])
        extra = rng.choice([f" Follow up on {_fmt_day(_when(rng, base, 1, 30))}.", f" Owner: {first} {rng.choice(pools.LAST)}.",
                            f" Cost so far {rng.randint(5, 400)}.{rng.choice(['00', '50'])}."])
        notes.append(f"- {title}. {body}{extra}")
        title0 = title0 or title
    return {"user": rng.choice([f"Search my notes for anything on {rng.choice(pools.CITIES)} or {rng.choice(pools.TOPICS)}.",
                                f"Do I have notes about {rng.choice(pools.PLACES)}? Look it up for me.",
                                f"Find my notes mentioning {rng.choice(pools.COMPANIES)}."]),
            "args": {"query": rng.choice(pools.CITIES)}, "result": "\n".join(notes),
            "summary": f"I found {len(notes)} notes; the first is titled \"{title0}\".",
            "probe": f"In the notes you found, what did the note titled \"{title0}\" say to do?"}


def _make_web(rng, base, _mid):
    q_thing, place = rng.choice(WEB_THINGS), rng.choice(pools.PLACES)
    res, title0 = [], ""
    for i in range(1, rng.randint(3, 5) + 1):
        p = rng.choice(pools.PLACES)
        title = f"{p} {rng.choice(WEB_THINGS)} - {rng.choice(pools.CITIES)} {_code(rng)}"
        snippet = rng.choice([
            f"{p} is open {rng.choice(DAYS)} to {rng.choice(DAYS)}, {rng.randint(7, 11)}am to {rng.randint(3, 9)}pm; {rng.randint(3, 80)} places left this week.",
            f"Update {_fmt_day(_when(rng, base, -9, 0))}: {rng.choice(WEB_THINGS)} changed to {rng.randint(5, 95)}.{rng.choice(['00', '50'])} for {rng.choice(pools.COMPANIES)} customers.",
            f"Visitors to {p} should allow {rng.randint(10, 90)} minutes; call {rng.choice(pools.FIRST)} on +1 555 01{rng.randint(0, 99):02d} to book."])
        res.append(f"{i}. {title}\n   {snippet}\n   https://www.example.com/{p.lower().replace(' ', '-')}/{rng.randint(100, 9999)}")
        title0 = title0 or title
    return {"user": rng.choice([f"Search the web for {q_thing} at {place}.", f"Look online for the latest on {q_thing} near {rng.choice(pools.CITIES)}.",
                                f"Can you find {q_thing} for {place}? I want it before {rng.choice(DAYS)}."]),
            "args": {"query": f"{place} {q_thing}"}, "result": "\n".join(res),
            "summary": f"The top result is \"{title0}\".",
            "probe": f"From the web results you found, what did the first result say about {q_thing}?"}


def _make_messages(rng, base, _mid):
    lines, who0 = [], ""
    for _ in range(rng.randint(5, 9)):
        first = rng.choice(pools.FIRST)
        d = _when(rng, base, -3, 0)
        text = rng.choice([
            f"Can we move {rng.choice(pools.TOPICS)} to {rng.choice(DAYS)}? {rng.choice(pools.PLACES)} is free after {rng.randint(1, 5)}pm.",
            f"I left {rng.choice(DOCS).replace('the ', 'a copy of ')} at {rng.choice(pools.PLACES)}, ref {_code(rng)}.",
            f"Running {rng.randint(5, 40)} minutes late, start {rng.choice(pools.ERRANDS)} without me.",
            f"Dinner at {rng.choice(pools.PLACES)} on {rng.choice(DAYS)}, table for {rng.randint(2, 8)}?"])
        lines.append(f"[{d.strftime('%a %b')} {d.day}, {_clock(rng.randint(7, 21), rng.randint(0, 59))}] {first} {rng.choice(pools.LAST)} - {text}")
        who0 = who0 or first
    return {"user": rng.choice([f"Any new texts? I am waiting on {rng.choice(pools.FIRST)} about {rng.choice(pools.TOPICS)}.",
                                f"Show my recent messages before I answer {rng.choice(pools.FIRST)}.",
                                f"What did people text me lately about {rng.choice(pools.ERRANDS)}?"]),
            "args": {"count": len(lines)}, "result": "\n".join(lines),
            "summary": f"There are {len(lines)} recent messages; the newest listed is from {who0}.",
            "probe": "Which of the messages you showed asked about moving something to another day, and who sent it?"}


_MAKERS = {"email": _make_email, "calendar": _make_calendar, "reminders": _make_reminders, "notes": _make_notes,
           "web": _make_web, "messages": _make_messages}


# ------------------------------------------------------------------ the pool
def build_pool(seed: int = GENERATOR_SEED, size: int = POOL_SIZE) -> list[dict]:
    """About 2,000 distinct synthetic records. No long line (40+ characters) repeats anywhere in the pool."""
    rng = random.Random(f"edat-perf-pool|{seed}")
    base = date(2026, 11, 18)
    seen: set[str] = set()
    total = sum(n for _, n in POOL_MIX)
    plan = [k for k, n in POOL_MIX for _ in range(round(n * size / total))]
    rng.shuffle(plan)
    pool: list[dict] = []
    fallbacks: list[int] = []
    for i, kind in enumerate(plan):
        for attempt in range(60):
            rec = _MAKERS[kind](rng, base, 2000 + i)
            lines = [x for x in _lines_long(rec["user"], rec["result"], rec["summary"]) if not x.startswith("<<<")]
            if not (set(lines) & seen) and len(set(lines)) == len(lines):
                break
        else:  # rare: make the record unique with its own serial
            fallbacks.append(i)
            rec["user"] += f" (request {i})"
            rec["summary"] += f" (record {i})"
            lines = [x for x in _lines_long(rec["user"], rec["result"], rec["summary"]) if not x.startswith("<<<")]
        seen.update(lines)
        rec.update({"id": f"pool-{i:04d}", "kind": kind, "tool": TOOL_FOR_KIND[kind]})
        pool.append(rec)
    return pool


# ------------------------------------------------------------------ messages
def _tool_call(call_id: str, name: str, args: dict) -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def _unit(rec: dict, serial: int, result: str | None = None) -> list[dict]:
    cid = f"call_{serial:04d}"
    return [{"role": "user", "content": rec["user"]},
            {"role": "assistant", "content": None, "tool_calls": [_tool_call(cid, rec["tool"], rec["args"])]},
            {"role": "tool", "tool_call_id": cid, "content": rec["result"] if result is None else result},
            {"role": "assistant", "content": rec["summary"]}]


def _env():
    from edat.state.assistant_lane import AssistantEnvironment
    from edat.state.clock import Clock
    return AssistantEnvironment(Clock.parse())


def _date_line(prompt: str) -> str:
    for ln in prompt.split("\n"):
        if ln.startswith("The current date and time is"):
            return ln
    return ""


def _short_head(prompt: str, limit: int, window: int) -> str:
    """The real prompt cut to `limit` characters, with the real date line kept at the end."""
    dl = _date_line(prompt)
    body_limit = max(limit - len(dl) - 2, 0)
    return cut_text(prompt, body_limit, window) + "\n\n" + dl if dl else cut_text(prompt, limit, window)


def _final_turn(level: int, last: dict | None, rng: random.Random) -> str:
    if last is not None:
        return last["probe"]
    a, b = rng.randint(120, 950), rng.randint(12, 48)
    return f"What is {b}% of {a}? Use the calculator and give me just the number with one line of working."


def build_level(level: int, pool: list[dict], seed: int = GENERATOR_SEED) -> dict:
    rng = random.Random(f"edat-perf-level|{seed}|{level}")
    env = _env()
    menu = LEVEL_MENUS.get(level, MENU_12)
    tools = env.schemas(menu)
    full_prompt = env.system_prompt(offered=menu)
    target = level * CHARS_PER_TOKEN
    window = max(int(target * 0.02), 60)
    avail = [r for r in pool if r["tool"] in menu]
    order = list(range(len(avail)))
    rng.shuffle(order)
    tools_chars = len(json.dumps(tools, ensure_ascii=False))
    units: list[list[dict]] = []
    used: list[dict] = []

    def size(msgs):
        return sum(len(m.get("content") or "") + (len(json.dumps(m["tool_calls"], ensure_ascii=False)) if m.get("tool_calls") else 0)
                   for m in msgs)

    head_policy = LEVEL_HEAD[level]
    n_units = LEVEL_HISTORY_UNITS.get(level)

    # --- pick the history
    if head_policy == "fit":
        min_head = 400 if level <= 1024 else 600
        probe_last = None
        # reserve for the final turn (an upper bound), then pick n units whose size is near the share
        fin_guess = 130
        hist_budget = target - tools_chars - fin_guess - min_head
        for k in range(n_units):
            share = (hist_budget - sum(size(u) for u in units)) / (n_units - k)
            cands = [avail[j] for j in order if avail[j] not in used][:120]
            if not cands:
                break
            fits = [(abs(len(_unit(c, 1)[2]["content"]) + 400 - share), c) for c in cands]
            best = min(fits, key=lambda x: x[0])[1]
            units.append(_unit(best, len(units) + 1))
            used.append(best)
            if size(units[-1]) > share * 1.3:
                over = size(units[-1]) - share
                units[-1] = _unit(best, len(units), cut_text(best["result"], max(len(best["result"]) - int(over), 200)))
        final = _final_turn(level, used[-1] if used else None, rng)
        history = [m for u in units for m in u]
        rest = tools_chars + size(history) + len(final)
        head_limit = target - rest
        if head_limit < min_head:
            raise ValueError(f"level {level}: no room for a head ({head_limit} chars)")
        head = _short_head(full_prompt, head_limit, window)
    else:
        head = full_prompt if head_policy is None else _short_head(full_prompt, head_policy, 300)
        # final turn depends on the last unit; fill first with a placeholder reserve, fix after
        fin_reserve = 190
        remaining = target - tools_chars - len(head) - fin_reserve
        cursor = 0
        picked: list[dict] = []
        while remaining > 0 and cursor < len(order):
            rec = avail[order[cursor]]
            u = _unit(rec, len(picked) + 1)
            cursor += 1
            if size(u) <= remaining:
                picked.append(rec)
                units.append(u)
                remaining -= size(u)
                if remaining <= 0.012 * target:
                    break
        if remaining > 0.012 * target and cursor < len(order):
            for c in range(cursor, len(order)):  # a truncated last unit to close the gap
                rec = avail[order[c]]
                fixed = size(_unit(rec, len(picked) + 1, ""))
                room = remaining - fixed
                if room >= 150:
                    picked.append(rec)
                    units.append(_unit(rec, len(picked), cut_text(rec["result"], room, window)))
                    break
        used = picked
        final = _final_turn(level, used[-1] if used else None, rng)
        history = [m for u in units for m in u]
        # if the final turn (not the reserve) leaves a gap or overflow, trim/extend the head once
        over = tools_chars + len(head) + size(history) + len(final) - target
        if over > 0:
            head = _short_head(full_prompt, len(head) - over - 2, window)

    messages = [{"role": "system", "content": head}] + history + [{"role": "user", "content": final}]
    est = estimate_tokens(messages, tools)
    n_hist = len(history)
    trim = ({"message_range": [1, -2], "unit": "whole_records_then_tokens", "records": n_hist // 4,
             "record_messages": 4} if n_hist else None)
    return {"schema": "edat-agent-corpus-v1", "level": level, "lang": "en", "generator_seed": seed,
            "messages": messages, "tools": tools, "trim": trim,
            "estimate": {"tokens": est, "chars_per_token": CHARS_PER_TOKEN,
                         "head_chars": len(head), "menu": menu, "history_records": [r["id"] for r in used],
                         "head": "full prompt" if head == full_prompt else "cut at a boundary, date line kept"}}


def build_corpus(seed: int = GENERATOR_SEED) -> list[dict]:
    pool = build_pool(seed)
    return [build_level(level, pool, seed) for level in LEVELS]


# ------------------------------------------------------------------ the 6-step trajectory
def build_trajectory(seed: int = GENERATOR_SEED) -> dict:
    rng = random.Random(f"edat-perf-trajectory|{seed}")
    env = _env()
    tools = env.schemas(TRAJECTORY_TOOLS)
    full_prompt = env.system_prompt(offered=TRAJECTORY_TOOLS)
    head = _short_head(full_prompt, TRAJECTORY_HEAD_CHARS, 300)
    pool = build_pool(seed)
    base = date(2026, 11, 18)
    lena = "Lena Varga"
    addr = pools.email("Lena", "Varga")
    doc = "the signed supplier form"
    ref = _code(rng)
    sched = f"{rng.randint(2, 6)}"
    # step 1 results
    cal_lines = ["Next 2 days: 9 events, 2 reminders", "", "**Today - Wed, Nov 18**"]
    cal_lines += [f"- **9:30 AM** - Team standup - Zoom",
                  f"- **11:00 AM** - Review {rng.choice(DOCS).replace('the ', '')} with {rng.choice(pools.FIRST)} - Room {rng.randint(1, 9)}A (invited: {rng.choice(pools.FIRST)} {rng.choice(pools.LAST)}, {rng.choice(pools.FIRST)} {rng.choice(pools.LAST)}, {rng.choice(pools.FIRST)} {rng.choice(pools.LAST)})",
                  f"- **12:30 PM** - Lunch with {rng.choice(pools.FIRST)} {rng.choice(pools.LAST)} - {rng.choice(pools.PLACES)} (table booked under the {rng.choice(pools.COMPANIES)} account)",
                  f"- **2:00 PM** - {rng.choice(pools.TOPICS).capitalize()} call with {rng.choice(pools.COMPANIES)} - Zoom (dial-in +1 555 01{rng.randint(0, 99):02d}, code {rng.randint(100000, 999999)}, host {rng.choice(pools.FIRST)} {rng.choice(pools.LAST)})",
                  f"- **4:15 PM** - Visit {rng.choice(pools.PLACES)} - {rng.choice(pools.CITIES)} Road {rng.randint(1, 99)} (bring ref {ref} and a printed copy of the booking confirmation)",
                  f"- **6:30 PM** - Dinner with {rng.choice(pools.FIRST)} {rng.choice(pools.LAST)} and {rng.choice(pools.FIRST)} {rng.choice(pools.LAST)} - {rng.choice(pools.PLACES)} (reservation for {rng.randint(3, 6)})",
                  "*Reminders*",
                  f"- **5:00 PM** - {rng.choice(pools.ERRANDS).capitalize()} (list: Errands, note: check the opening hours first)",
                  f"- **8:00 PM** - {rng.choice(pools.ERRANDS).capitalize()} (list: Home, note: reply to {rng.choice(pools.FIRST)} about {rng.choice(pools.TOPICS)})",
                  "**Tomorrow - Thu, Nov 19**",
                  f"- **8:45 AM** - Coffee with {rng.choice(pools.FIRST)} {rng.choice(pools.LAST)} - {rng.choice(pools.PLACES)} (bring the notes on {rng.choice(pools.TOPICS)} and the printed agenda)",
                  f"- **10:30 AM** - {rng.choice(pools.TOPICS).capitalize()} workshop - Room {rng.randint(1, 9)}B (organiser {rng.choice(pools.FIRST)} {rng.choice(pools.LAST)}, {rng.randint(6, 14)} attendees, lunch provided)",
                  f"- **3:00 PM** - Visit {rng.choice(pools.PLACES)} - {rng.choice(pools.CITIES)} Road {rng.randint(1, 99)} (ask for {rng.choice(pools.FIRST)} at the desk, quote {_code(rng)})"]
    cal = "\n".join(cal_lines)
    ef = email_fields(rng, base, ask_target=doc)
    ef.update({"first": "Lena", "last": "Varga", "addr": addr, "date": base})
    ef["subject"] = f"{doc.replace('the ', '').capitalize()} for {ef['company']} - {ref}"
    ef["body"] = "\n\n".join([
        "Hi,",
        f"I hope the week is going well. I am finishing the paperwork for {ef['topic']} today and there is one thing still outstanding on my side.",
        f"Could you send me {doc} before 2pm today? {ef['company']} needs it on file to release the {rng.randint(120, 880)}.{rng.choice(['00', '50'])} deposit, and the office closes the batch at 2pm sharp.",
        f"A scan is fine as long as every page is legible and both signatures are visible. Please quote reference {ref} in the subject line so it lands in the right folder.",
        f"If anything is unclear, the form is attached to the earlier message dated {_fmt_day(base - timedelta(days=int(sched)))}; the section on page {rng.randint(2, 5)} is the one people usually miss. I will be at {rng.choice(pools.PLACES)} until about {rng.randint(1, 2)}pm, so reply by mail rather than phone.",
        f"For context, {ef['topic']} file now has {rng.randint(4, 9)} documents and only this one is missing; the others were checked off on {_fmt_day(base - timedelta(days=int(sched) + 3))} by {rng.choice(pools.FIRST)} {rng.choice(pools.LAST)} from the {rng.choice(pools.PLACES)} office, who also confirmed the {rng.randint(2, 6)} signatories listed on page {rng.randint(1, 3)}.",
        f"If the original is already with {rng.choice(pools.FIRST)} {rng.choice(pools.LAST)}, ask them to forward it instead, and copy {rng.choice(pools.FIRST)} so the file shows who sent what and when; that is all the audit needs from us this quarter.",
        f"I would rather not chase this again after lunch, so a quick yes that it is on its way would already help me plan the afternoon. Nothing else is open between us on {ef['topic']}.",
        "Thank you so much,\nLena Varga\n" + ef["company"]])
    email_text = _email_text(ef, rng, 3100)
    reminder_title = f"Send Lena {doc}"
    when_iso = "2026-11-18T13:30"
    temp, wind, rain = rng.randint(11, 19), rng.randint(8, 32), rng.randint(20, 80)
    forecast = (f"Porto - Thu Nov 19: {temp - 2}-{temp} C, {rng.choice(['light rain in the morning, clearing by noon', 'broken cloud with a few showers', 'mostly sunny after a grey start'])}. "
                f"Chance of rain {rain}%. Wind {wind} km/h from the {rng.choice(['north-west', 'south-west', 'west'])}, gusts up to {wind + rng.randint(8, 20)} km/h. "
                f"Humidity {rng.randint(60, 92)}%. Sunrise 7:{rng.randint(10, 40)} AM, sunset 5:{rng.randint(20, 50)} PM local. No weather alerts in force for the area. "
                f"Morning {temp - 2} C with {rng.choice(['drizzle', 'low cloud', 'patchy fog'])} near the coast until about {rng.randint(9, 11)} AM; "
                f"afternoon {temp} C, {rng.choice(['bright spells', 'passing showers', 'thin cloud'])}; evening {temp - 3} C and dry. "
                f"UV index {rng.randint(1, 4)}, visibility {rng.randint(6, 20)} km, pressure {rng.randint(1004, 1026)} hPa. "
                f"Source: invented regional forecast, issued {rng.randint(5, 7)}:00 AM local.")
    summary_fc = f"{temp - 2}-{temp} C with {rain}% chance of rain"
    subject = f"Re: {ef['subject']}"
    body = (f"Hi Lena,\n\nIt is handled: I have set a reminder to send you {doc} before 2pm today (1:30 PM), so it will reach you in time "
            f"for the batch. Quoting reference {ref} as you asked.\n\nOn another note, the forecast for Porto tomorrow is {summary_fc}, "
            f"so plan accordingly if you are heading out.\n\nBest,\nTest User")
    draft_receipt = (f"Draft opened in Mail to {addr} - subject \"{subject}\" ({len(body)} characters). Nothing has been sent. "
                     f"Tell the user to review it and hit send.")
    reminder_ok = f"Reminder set: \"{reminder_title}\" - Wed Nov 18 at 1:30 PM in Apple Reminders and Harbor."
    reminder_list = (f"Incomplete reminders:\n- {reminder_title} - Wed Nov 18, 2026 at 1:30 PM (list: Reminders, created today at 8:30 AM, no repeat, no location trigger, alert at 1:30 PM)\n1 match for \"supplier form\" in scope today; 0 other incomplete reminders match.")
    final_text = (f"All handled. Lena asked for {doc} before 2pm (ref {ref}); I set a reminder for 1:30 PM today and confirmed it is on your list. "
                  f"Tomorrow in Porto: {summary_fc}. The reply to Lena is drafted in Mail and waiting for you to review and send.")

    c = lambda i: f"call_t{i:02d}"
    s1 = {"role": "assistant", "content": None, "tool_calls": [
        _tool_call(c(1), "get_upcoming", {"days": 2}),
        _tool_call(c(2), "view_emails", {"query": "Lena", "count": 1})]}
    r1 = [{"role": "tool", "tool_call_id": c(1), "content": cal}, {"role": "tool", "tool_call_id": c(2), "content": email_text}]
    s2 = {"role": "assistant", "content": None, "tool_calls": [_tool_call(c(3), "add_reminder", {"title": reminder_title, "when_iso": when_iso})]}
    r2 = [{"role": "tool", "tool_call_id": c(3), "content": reminder_ok}]
    s3 = {"role": "assistant", "content": None, "tool_calls": [_tool_call(c(4), "get_weather", {"location": "Porto", "period": "tomorrow"})]}
    r3 = [{"role": "tool", "tool_call_id": c(4), "content": forecast}]
    s4 = {"role": "assistant", "content": None, "tool_calls": [_tool_call(c(5), "draft_email", {"to": addr, "subject": subject, "body": body})]}
    r4 = [{"role": "tool", "tool_call_id": c(5), "content": draft_receipt}]
    s5 = {"role": "assistant", "content": None, "tool_calls": [_tool_call(c(6), "search_reminders", {"query": "supplier form", "scope": "today"})]}
    r5 = [{"role": "tool", "tool_call_id": c(6), "content": reminder_list}]
    s6 = {"role": "assistant", "content": final_text}
    plan = [(s1, r1), (s2, r2), (s3, r3), (s4, r4), (s5, r5), (s6, [])]

    # the shared prefix: head, six schemas, one earlier exchange cut so the estimate lands on 4,096
    tools_chars = len(json.dumps(tools, ensure_ascii=False))
    room = PREFIX_TOKENS * CHARS_PER_TOKEN - tools_chars - len(head)
    recs = [r for r in pool if r["kind"] in ("email", "notes", "web", "calendar", "messages")]
    random.Random(f"edat-perf-trajectory-prefix|{seed}").shuffle(recs)
    user_text = "Catch me up before the day starts: my latest mail, my notes, what is on the web about my errands and my messages."
    calls, results, summaries = [], [], []
    fixed = len(user_text) + 40
    for n, r in enumerate(recs, start=1):
        call = _tool_call(f"call_p{n:02d}", r["tool"], r["args"])
        overhead = len(json.dumps(call, ensure_ascii=False)) + len(r["summary"]) + 2
        left = room - fixed - overhead
        if left < 120:
            break
        text = r["result"] if len(r["result"]) <= left else cut_text(r["result"], left, 0)
        calls.append(call)
        results.append({"role": "tool", "tool_call_id": call["id"], "content": text})
        summaries.append(r["summary"])
        fixed += overhead + len(text) + len(json.dumps([call], ensure_ascii=False)) * 0
        if len(text) < len(r["result"]):
            break
    exchange = [{"role": "user", "content": user_text}, {"role": "assistant", "content": None, "tool_calls": calls}] + results + [
        {"role": "assistant", "content": " ".join(summaries)}]
    prefix_msgs = [{"role": "system", "content": head}] + exchange
    used_ids = {r["id"] for r in recs[:len(calls)]}
    for _ in range(12):  # close the gap with the best-fitting whole record, else a cut one
        gap = PREFIX_TOKENS * CHARS_PER_TOKEN - estimate_tokens(prefix_msgs, tools) * CHARS_PER_TOKEN
        if gap < 12:
            break
        n = len(calls) + 1
        best, best_len = None, 0
        for r in recs:
            if r["id"] in used_ids:
                continue
            call = _tool_call(f"call_p{n:02d}", r["tool"], r["args"])
            cost = len(json.dumps(call, ensure_ascii=False)) + 2 + len(r["summary"]) + 2 + len(r["result"])
            if cost <= gap and cost > best_len:
                best, best_len = r, cost
        if best is None:
            r = next(r for r in recs if r["id"] not in used_ids)
            call = _tool_call(f"call_p{n:02d}", r["tool"], r["args"])
            cost = len(json.dumps(call, ensure_ascii=False)) + 2 + len(r["summary"]) + 2
            if gap - cost < 100:
                break
            best, text = r, cut_text(r["result"], gap - cost, 0)
        else:
            text = best["result"]
        call = _tool_call(f"call_p{n:02d}", best["tool"], best["args"])
        used_ids.add(best["id"])
        calls.append(call)
        results.append({"role": "tool", "tool_call_id": call["id"], "content": text})
        exchange[-1]["content"] += " " + best["summary"]
        prefix_msgs = [{"role": "system", "content": head}, exchange[0], exchange[1], *results, exchange[-1]]
    for _ in range(6):  # the head is the one free-length part: land the last few characters there
        gap = PREFIX_TOKENS * CHARS_PER_TOKEN - estimate_tokens(prefix_msgs, tools) * CHARS_PER_TOKEN
        if gap < 4:
            break
        head = _short_head(full_prompt, len(head) + gap, 0)
        prefix_msgs[0] = {"role": "system", "content": head}
    prefix_est = estimate_tokens(prefix_msgs, tools)
    if prefix_est > PREFIX_TOKENS:  # cut_text never exceeds `room`, so this is a safety net
        raise ValueError(f"prefix estimate {prefix_est} above {PREFIX_TOKENS}")

    steps, convo = [], list(prefix_msgs)
    for i, (assistant, results) in enumerate(plan, start=1):
        added = ([{"role": "user", "content": TASK_TEXT}] if i == 1 else
                 [plan[i - 2][0]] + plan[i - 2][1])
        convo = convo + added
        steps.append({"step": i, "messages": added, "tools": list(TRAJECTORY_TOOLS), "canned_assistant": assistant,
                      "canned_tool_results": results, "estimated_input_tokens": estimate_tokens(convo, tools),
                      "estimated_result_tokens": estimate_tokens(results) if results else 0})
    return {"schema": "edat-agent-trajectory-v1", "lang": "en", "generator_seed": seed, "prefix_tokens": PREFIX_TOKENS,
            "task": TASK_TEXT, "decode_cap_tokens": 256,
            "prefix": {"messages": prefix_msgs, "tools": tools,
                       "trim": {"message_range": [1, -1], "unit": "whole_records_then_tokens", "target_tokens": PREFIX_TOKENS},
                       "estimated_tokens": prefix_est},
            "steps": steps,
            "notes": ["step.messages are appended to the conversation before the step's generation; the canned "
                      "assistant message and its tool results of step k are part of step k+1's input.",
                      "step.tools names the menu; the schemas are in prefix.tools.",
                      "Menu is the six tools the task uses, not twelve: the real 12 schemas alone are about 4,400 estimated "
                      "tokens, above the 4,096-token prefix (see README)."],
            "estimate": {"chars_per_token": CHARS_PER_TOKEN}}


# ------------------------------------------------------------------ the simulated day and duty cycle
def _hms(minutes: float) -> str:
    s = int(round(minutes * 60))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _draw_cases(cases: list[dict], rng: random.Random) -> tuple[list[tuple[str, str | None]], list[dict]]:
    chosen: list[tuple[str, str | None]] = []
    missing: list[dict] = []
    for fam, n in TASK_MIX:
        cand = sorted((c for c in cases if c.get("family") == fam and c.get("lane") == "assistant"), key=lambda c: c["id"])
        core = [c for c in cand if "core" in (c.get("tags") or [])]
        rest = [c for c in cand if c not in core]
        pick = rng.sample(core, n) if len(core) >= n else core + rng.sample(rest, min(n - len(core), len(rest)))
        chosen += [(fam, c["id"]) for c in pick]
        if len(pick) < n:
            missing.append({"family": fam, "wanted": n, "found": len(pick)})
            chosen += [(fam, None)] * (n - len(pick))
    return chosen, missing


def _arrival_email(rng: random.Random, t_min: float, base: date, serial: int) -> dict:
    f = email_fields(rng, base)
    return {"type": "email", "at": _hms(t_min)[:5], "from": f"{f['first']} {f['last']} <{f['addr']}>",
            "subject": f["subject"], "body": f["body"], "message_id": f"m-{9000 + serial}"}


def _arrival_message(rng: random.Random, t_min: float) -> dict:
    first, last = rng.choice(pools.FIRST), rng.choice(pools.LAST)
    text = rng.choice([f"Can we move {rng.choice(pools.TOPICS)} to {rng.choice(DAYS)}? {rng.choice(pools.PLACES)} is free after {rng.randint(1, 5)}pm.",
                       f"Running {rng.randint(5, 40)} minutes late, start {rng.choice(pools.ERRANDS)} without me.",
                       f"Ref {_code(rng)} is confirmed for {rng.choice(DAYS)}, {rng.randint(8, 11)}am at {rng.choice(pools.PLACES)}."])
    return {"type": "message", "at": _hms(t_min)[:5], "from": f"{first} {last}", "text": text}


def build_sim_day(cases: list[dict] | None = None, seed: int = GENERATOR_SEED) -> dict:
    if cases is None:
        from edat.cases.loader import load_split
        try:
            cases = load_split("dev")
        except Exception:  # no case files yet: every family is reported missing
            cases = []
    rng = random.Random(f"edat-perf-simday|{seed}")
    chosen, missing = _draw_cases(cases, rng)
    rng.shuffle(chosen)

    # interactive clusters: (anchor minute-of-day, size). Each cluster spans under 5 minutes.
    plan = [(8 * 60 + 20, 4)]
    plan += [(a, 3) for a in (9 * 60 + 25, 10 * 60 + 20, 11 * 60 + 5, 11 * 60 + 40)]
    big = rng.randrange(5)
    plan += [(a, 4 if k == big else 3) for k, a in enumerate((13 * 60 + 15, 14 * 60 + 20, 15 * 60 + 5, 15 * 60 + 40, 16 * 60 + 30))]
    # 4 + 12 + (4*3+... ) : make the afternoon exactly 16
    sizes = [s for _, s in plan]
    if sum(sizes) != 32:  # one big cluster of 4 gives 4+12+16 = 32; any other split is a bug
        raise AssertionError(sizes)
    events: list[dict] = []
    k = 0
    for ci, (anchor, size) in enumerate(plan):
        anchor += rng.randint(-4, 4)
        offs = [0.0] + sorted(rng.sample(range(60, 280, 20), size - 1))
        for j, off in enumerate(offs):
            fam, ref = chosen[k]
            k += 1
            events.append({"t": _hms(anchor + off / 60), "kind": "task", "ref": ref, "family": fam, "cluster": ci + 1,
                           "world_events_before": []})
    base = date(2026, 11, 18)
    events.append({"t": "08:00:00", "kind": "brief", "ref": "job:brief", "world_events_before": []})
    last_mail = 8 * 60
    serial = 0
    for h in range(9, 18):
        arrivals = []
        for _ in range(rng.randint(2, 5)):
            serial += 1
            arrivals.append(_arrival_email(rng, rng.uniform(last_mail + 1, h * 60 - 1), base, serial))
        arrivals.sort(key=lambda a: a["at"])
        events.append({"t": f"{h:02d}:00:00", "kind": "mail_digest", "ref": "job:mail_digest", "world_events_before": arrivals})
        last_mail = h * 60
    last_msg = 8 * 60
    for h in (10, 12, 14, 16):
        arrivals = sorted((_arrival_message(rng, rng.uniform(last_msg + 1, h * 60 - 1)) for _ in range(rng.randint(2, 4))),
                          key=lambda a: a["at"])
        events.append({"t": f"{h:02d}:00:00", "kind": "message_digest", "ref": "job:message_digest", "world_events_before": arrivals})
        last_msg = h * 60
    events.append({"t": "17:30:00", "kind": "review", "ref": "job:review", "world_events_before": []})
    order = {"brief": 0, "mail_digest": 1, "message_digest": 2, "task": 3, "review": 4}
    events.sort(key=lambda e: (e["t"], order[e["kind"]]))
    return {
        "schema": "edat-sim-day-v1", "profile_id": "synthetic-profile-1", "day_start": "08:00", "day_end": "18:00",
        "tz": DAY_TZ, "mode": "forced", "modes": ["forced", "live"], "lang": "en", "generator_seed": seed,
        "compressed": {"idle_scale": 0.2, "wall_hours_estimate": 2,
                       "note": "Same job list with idle gaps scaled by 0.2. Real-day energy needs measured idle power times the real idle time added back; the 10-hour real-time day is the reference."},
        "jobs": {"brief": {"model_steps": 6, "note": "parallel reads of calendar, mail, messages and reminders, then a grouped summary"},
                 "mail_digest": {"model_calls": "1-2", "input_tokens_estimate": 3000, "note": "summarise mail that arrived since the last digest"},
                 "message_digest": {"model_calls": 1},
                 "review": {"model_steps": 4, "note": "what got done, what moves to tomorrow"},
                 "task": {"note": "ref is a development-split case id; null when the family has too few cases yet (see missing)"}},
        "totals": {"jobs": len(events), "tasks": 32, "mail_digests": 9, "message_digests": 4, "brief": 1, "review": 1},
        "task_mix": {fam: n for fam, n in TASK_MIX}, "missing": missing, "events": events,
        "caveat": "An invented typical day, not measured usage."}


def build_duty_cycle(seed: int = GENERATOR_SEED, variants: int = 16) -> dict:
    bursts = []
    for i in range(20):
        v = i % variants
        bursts.append({"burst": i + 1, "t": _hms(i * 3), "trajectory": "edat-agent-trajectory-v1", "nonce_variant": v,
                       "nonce_reused": i >= variants, "fresh_cache": True})
    return {"schema": "edat-duty-cycle-v1", "lang": "en", "generator_seed": seed, "period_minutes": 3, "duration_minutes": 60,
            "task": "6-step trajectory", "bursts": bursts, "nonce_variants_in_pack": variants,
            "notes": ["Bursts 17-20 (the 17th to 20th) reuse nonce variants 0-3 with a fresh cache, because the performance spec's pack has 16 variants; add 4 more variants to avoid reuse.",
                      "Heavy-use assumption: 20 agent tasks an hour. No real usage data is used."]}


# ------------------------------------------------------------------ CLI
def write_all(out: Path, seed: int = GENERATOR_SEED) -> dict[str, int]:
    out.mkdir(parents=True, exist_ok=True)
    sizes = {}
    corpus = build_corpus(seed)
    p = out / "edat-agent-corpus-v1.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in corpus) + "\n", encoding="utf-8")
    sizes[p.name] = p.stat().st_size
    for name, obj in (("edat-agent-trajectory-v1.json", build_trajectory(seed)), ("edat-sim-day-v1.json", build_sim_day(seed=seed)),
                      ("edat-duty-cycle-v1.json", build_duty_cycle(seed))):
        p = out / name
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        sizes[name] = p.stat().st_size
    return sizes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write the performance-spec inputs (agentic spec section 11).")
    ap.add_argument("--out", default="test_fixtures/agentic/perf")
    ap.add_argument("--seed", type=int, default=GENERATOR_SEED)
    a = ap.parse_args(argv)
    for name, n in write_all(Path(a.out), a.seed).items():
        print(f"{name}: {n} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
