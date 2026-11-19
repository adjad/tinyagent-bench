"""App screens: chat, inbox, settings, error dialog, notifications, maps card, contact card,
music player, file browser, system alert and alarms (spec 05 §7.1, and V7 control location).

Planted fields: sender, message, time, error code, toggle states, unread count, place, phone,
track, artist, selected file, default button, next alarm.
Question types: lookup, who_said, state, count (and the reading).
Every visible button, toggle, tab, link and labelled icon is recorded with ``Canvas.element``
under its visible label (at least four unique names on every template).

Eleven templates on purpose: the generator picks template = index % len(TEMPLATES), and a count
coprime with 10 keeps every template reachable in light and dark, at both scales, and in
abstention items.
"""
from __future__ import annotations

import random

from datetime import date

from edat.media.common import GIVEN_NAMES, Inventor, date_short, time_12h
from edat.media.vision.canvas import Canvas
from edat.media.vision.items import ABSTAIN_ANSWER, Question, Rendered

FAMILY = "app_screens"
TEMPLATES = ("chat", "inbox", "settings", "error", "notifications", "maps", "contact",
             "music", "files", "alert", "alarms")
LOGICAL_SIZES = {"chat": (390, 844), "inbox": (390, 844), "settings": (1024, 640),
                 "error": (1024, 640), "notifications": (390, 844), "maps": (390, 844),
                 "contact": (390, 844), "music": (390, 844), "files": (1024, 640),
                 "alert": (1024, 640), "alarms": (390, 844)}
YEAR = 2027


def render(rng: random.Random, *, template: str, theme: str, scale: float,
           abstain: bool) -> Rendered:
    fn = globals().get("_" + template)
    if template not in TEMPLATES or fn is None:
        raise ValueError(f"unknown template {template!r}")
    return fn(rng, theme=theme, scale=scale, abstain=abstain)


def _hm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _tail(c: Canvas, rng: random.Random, abstain: bool, questions: list, normal: Question,
          blur: str, absent: tuple | None) -> None:
    """The last question: normal, or an abstention (blurred field or absent field)."""
    if not abstain:
        questions.append(normal)
        return
    if absent is not None and rng.random() < 0.5:
        qid, text, name, value, kind = absent
        c.absent(name, value, kind)
        questions.append(Question(qid, "absence", text, ABSTAIN_ANSWER, kind="abstain",
                                  fields=[name]))
    else:
        c.blur_field(blur, radius=7)
        questions.append(Question(normal.qid, "absence", normal.question, ABSTAIN_ANSWER,
                                  kind="abstain", fields=[blur]))


def _reading() -> Question:
    return Question("q_reading", "reading", "Read the screen.", "", kind="reading")


def _btn(c: Canvas, x0, y0, x1, y1, label: str, *, role: str = "button", fill=None, outline=None,
         color="fg", size: float = 13, radius: float = 8, field: str | None = None) -> None:
    """A labelled button (drawn and recorded as an element named by its visible label)."""
    if fill is not None or outline is not None:
        c.rect(x0, y0, x1, y1, fill=fill, outline=outline, radius=radius, width=1.5)
    kw = {"field": field, "value": label} if field else {}
    c.text_center((x0 + x1) / 2, (y0 + y1) / 2, label, size=size, color=color, anchor="lm", **kw)
    c.element(label, x0, y0, x1, y1, role=role)


def _link(c: Canvas, x: float, y: float, label: str, *, size: float = 13, color="accent",
          role: str = "link", right: bool = False) -> None:
    """A text link at logical (x, y), or ending at x when ``right``; recorded as an element."""
    w = c.text_width(label, size)
    x0 = x - w if right else x
    c.text(x0, y, label, size=size, color=color)
    c.element(label, x0 - 4, y - 3, x0 + w + 4, y + size * 1.25 + 3, role=role)


def _switch(c: Canvas, x: float, y: float, on: bool, w: float = 42, h: float = 22) -> None:
    c.rect(x, y, x + w, y + h, fill="ok" if on else "border", radius=h / 2)
    kx = x + w - h + 2 if on else x + 2
    c.ellipse(kx, y + 2, kx + h - 4, y + h - 2, fill="bg")


def _unique_people(inv: Inventor, n: int) -> list[str]:
    out: list[str] = []
    while len(out) < n:
        s = inv.person()
        if s not in out:
            out.append(s)
    return out


# ---------------------------------------------------------------------------- chat
_PHRASES = ("Running ten minutes late", "Can someone bring the projector?", "I booked the room",
            "Dinner is on me tonight", "Did anyone see the draft?", "Sending the photos now",
            "The train was cancelled", "Let's meet by the fountain", "I will bring extra chairs",
            "Count me in for Saturday", "Who has the spare key?", "Great work on the demo",
            "Parking is full downtown", "The recital starts at seven", "Thanks for the ride",
            "I left my umbrella there", "New schedule is posted", "Can we move it to Friday?")


def _chat(rng, *, theme, scale, abstain):
    inv = Inventor(rng)
    w, h = LOGICAL_SIZES["chat"]
    c = Canvas(w, h, scale=scale, theme=theme)
    title = f"{inv.town()} {rng.choice(('Crew', 'Group', 'Club', 'Friends'))}"
    people = rng.sample(GIVEN_NAMES, 3)
    n = rng.randint(6, 8)
    phrases = rng.sample(_PHRASES, n)
    senders = [rng.choice(people + people + ["me"]) for _ in range(n)]
    for p in people:                       # everyone speaks at least once
        if p not in senders:
            senders[rng.choice([i for i, s in enumerate(senders) if s == "me" or
                                senders.count(s) > 1] or [0])] = p
    minute = rng.randint(8 * 60, 18 * 60)
    times = []
    for _ in range(n):
        minute += rng.randint(1, 14)
        times.append(_hm(minute))
    c.rect(0, 0, w, 64, fill="surface")
    _link(c, 14, 24, "Back", size=14)
    c.text(72, 22, title, size=17, bold=True, field="chat_title", value=title)
    _link(c, w - 14, 24, "Call", size=14, right=True, role="button")
    y = 80
    for i in range(n):
        s, tw = phrases[i], c.text_width(phrases[i], 14)
        if senders[i] == "me":
            x1 = w - 16
            c.rect(x1 - tw - 24, y, x1, y + 32, fill="bubble_out", radius=14)
            c.text(x1 - tw - 12, y + 8, s, size=14, color="bubble_out_fg", field=f"msg_{i}")
            c.text_right(x1 - 4, y + 38, time_12h(times[i]), size=11, color="muted",
                         field=f"time_{i}", kind="time", value=times[i])
            y += 38 + 14 + 14
        else:
            c.text(16, y, senders[i], size=12, color="accent", field=f"sender_{i}")
            y += 17
            c.rect(16, y, 16 + tw + 24, y + 32, fill="bubble_in", radius=14)
            c.text(28, y + 8, s, size=14, field=f"msg_{i}")
            c.text(20, y + 38, time_12h(times[i]), size=11, color="muted",
                   field=f"time_{i}", kind="time", value=times[i])
            y += 38 + 14 + 14
    c.rect(12, h - 60, w - 12, h - 20, fill="surface", radius=18)
    c.text(28, h - 49, "Message", size=14, color="muted")
    c.element("Message", 12, h - 60, w - 96, h - 20, role="field")
    _btn(c, w - 88, h - 56, w - 18, h - 24, "Send", fill="accent", color="accent_fg", size=13,
         radius=16)

    inc = [i for i in range(n) if senders[i] != "me"]
    a = rng.choice(inc)
    b = rng.choice([i for i in range(n) if i != a])
    p = senders[rng.choice(inc)]
    cnt = sum(1 for i in inc if senders[i] == p)
    qs = [
        Question("q_who", "who_said", f'Who sent the message "{phrases[a]}"?', senders[a],
                 fields=[f"msg_{a}", f"sender_{a}"]),
        Question("q_time", "lookup", f'At what time was the message "{phrases[b]}" sent?',
                 times[b], canon="time", fields=[f"msg_{b}", f"time_{b}"]),
        Question("q_count", "count", f"How many messages did {p} send in this chat?",
                 str(cnt), canon="number", fields=[f"sender_{i}" for i in inc]),
    ]
    _tail(c, rng, abstain, qs,
          Question("q_title", "lookup", "What is the name of this group chat?", title,
                   fields=["chat_title"]),
          "chat_title", ("q_invite", "What is the group's invite code?", "invite_code",
                         inv.code(3, 3), "text"))
    qs.append(_reading())
    data = {"title": title, "messages": [{"sender": senders[i], "text": phrases[i],
                                          "time": times[i]} for i in range(n)]}
    return Rendered(c, "chat", qs, data, default_year=YEAR)


# ---------------------------------------------------------------------------- inbox
_SUBJECTS = ("Your invoice is ready", "Schedule for next week", "Welcome to the team",
             "Photos from the trip", "Question about the draft", "Reminder: renewal due",
             "Lunch on Friday?", "Updated agenda", "Shipping confirmation", "Notes from today",
             "Draft budget attached", "Venue options", "Please review", "Thanks again")
_PREVIEWS = ("Hi there, following up on our last conversation about", "Just a quick note to say",
             "Please find the details below and let me know", "We wanted to share an update on",
             "Looking forward to hearing your thoughts about")


def _inbox(rng, *, theme, scale, abstain):
    inv = Inventor(rng)
    w, h = LOGICAL_SIZES["inbox"]
    c = Canvas(w, h, scale=scale, theme=theme)
    n = rng.randint(6, 8)
    names = []
    while len(names) < n:
        s = inv.person()
        if s not in names:
            names.append(s)
    subjects = rng.sample(_SUBJECTS, n)
    unread = [rng.random() < 0.5 for _ in range(n)]
    if not any(unread):
        unread[rng.randrange(n)] = True
    minute = rng.randint(15 * 60, 21 * 60)
    times = []
    for _ in range(n):
        times.append(_hm(minute))
        minute -= rng.randint(7, 70)
    times = [t for t in times]
    c.text(16, 22, "Inbox", size=26, bold=True)
    _btn(c, 118, 24, 210, 54, "Compose", outline="accent", color="accent", size=13, radius=15)
    c.text(w - 104, 34, "Unread", size=12, color="muted")
    cnt = sum(unread)
    c.rect(w - 54, 28, w - 16, 54, fill="accent", radius=13)
    c.text_center(w - 35, 33, str(cnt), size=14, color="accent_fg",
                  field="unread_count", kind="number", value=str(cnt))
    y = 80
    for i in range(n):
        if unread[i]:
            c.ellipse(14, y + 14, 24, y + 24, fill="accent")
        c.text(34, y + 10, names[i], size=14, color="fg" if unread[i] else "muted", field=f"sender_{i}")
        c.text_right(w - 16, y + 11, time_12h(times[i]), size=12, color="muted",
                     field=f"time_{i}", kind="time", value=times[i])
        c.text(34, y + 34, subjects[i], size=13, field=f"subject_{i}")
        c.text(34, y + 56, rng.choice(_PREVIEWS) + " ...", size=11, color="muted")
        c.line(34, y + 82, w, y + 82)
        y += 84
    c.rect(0, 780, w, h, fill="surface")
    for k, tab in enumerate(("Inbox", "Sent", "Drafts", "Search")):
        cx = 49 + k * 97.5
        c.text_center(cx, 800, tab, size=12, color="accent" if k == 0 else "muted",
                      bold=False, anchor="lm")
        c.element(tab, cx - 44, 784, cx + 44, 836, role="tab")
    ia, ib, ic = rng.sample(range(n), 3)
    qs = [
        Question("q_unread", "count", "How many unread emails does the badge show?", str(cnt),
                 canon="number", fields=["unread_count"]),
        Question("q_who", "who_said", f'Who sent the email with the subject "{subjects[ia]}"?',
                 names[ia], fields=[f"subject_{ia}", f"sender_{ia}"]),
        Question("q_time", "lookup", f"At what time was the email from {names[ib]} received?",
                 times[ib], canon="time", fields=[f"sender_{ib}", f"time_{ib}"]),
    ]
    _tail(c, rng, abstain, qs,
          Question("q_subject", "lookup", f"What is the subject of the email from {names[ic]}?",
                   subjects[ic], fields=[f"sender_{ic}", f"subject_{ic}"]),
          f"subject_{ic}", ("q_attach", "What is the name of the attached file?", "attachment",
                            inv.code(3, 3) + ".pdf", "text"))
    qs.append(_reading())
    data = {"unread": cnt, "emails": [{"sender": names[i], "subject": subjects[i],
                                       "time": times[i], "unread": unread[i]} for i in range(n)]}
    return Rendered(c, "inbox", qs, data, default_year=YEAR)


# ---------------------------------------------------------------------------- settings
_TOGGLES = ("Sound effects", "Banner previews", "Auto-sync", "Location hints", "Offline mode",
            "Haptics", "Usage reports", "Beta features", "Quiet mode", "Auto-update",
            "Badge counts", "Smart replies", "Lock screen widgets", "Background refresh")
_SECTIONS = ("General", "Account", "Privacy", "Storage", "Devices", "About")


def _settings(rng, *, theme, scale, abstain):
    inv = Inventor(rng)
    w, h = LOGICAL_SIZES["settings"]
    c = Canvas(w, h, scale=scale, theme=theme)
    c.rect(0, 0, 220, h, fill="surface")
    c.text(24, 28, inv.brand(), size=16, bold=True, color="accent")
    active = rng.randrange(len(_SECTIONS))
    for i, s in enumerate(_SECTIONS):
        yy = 80 + i * 40
        if i == active:
            c.rect(12, yy - 8, 208, yy + 26, fill="bg", radius=8)
        c.text(28, yy, s, size=14, color="fg" if i == active else "muted")
        c.element(s, 12, yy - 8, 208, yy + 26, role="menu_item")
    _btn(c, 24, h - 64, 196, h - 24, "Sign out", outline="border", size=13)
    c.text(260, 34, _SECTIONS[active], size=24, bold=True)
    n = rng.randint(7, 9)
    labels = rng.sample(_TOGGLES, n)
    states = [rng.random() < 0.5 for _ in range(n)]
    states[0], states[1] = True, False
    rng.shuffle(states)
    y = 96
    for i in range(n):
        c.text(260, y + 14, labels[i], size=14, field=f"label_{i}")
        on = states[i]
        c.text_right(780, y + 14, "On" if on else "Off", size=13,
                     field=f"state_{i}", kind="state", value="on" if on else "off")
        _switch(c, 800, y + 11, on)
        c.element(labels[i], 798, y + 9, 844, y + 35, role="toggle")
        c.line(260, y + 45, 960, y + 45, color="grid")
        y += 46
    minutes = rng.choice((1, 2, 5, 10, 15, 30))
    c.text(260, y + 14, "Auto-lock after", size=14)
    c.text_right(960, y + 14, f"{minutes} min", size=14, field="autolock",
                 kind="number", value=str(minutes))
    ia, ib = rng.sample(range(n), 2)
    word = {True: "on", False: "off"}
    qs = [
        Question("q_state_a", "state", f"Is {labels[ia]} turned on or off?", word[states[ia]],
                 canon="state", fields=[f"label_{ia}", f"state_{ia}"]),
        Question("q_state_b", "state", f"Is {labels[ib]} turned on or off?", word[states[ib]],
                 canon="state", fields=[f"label_{ib}", f"state_{ib}"]),
        Question("q_on", "count", "How many of the toggle settings are turned on?",
                 str(sum(states)), canon="number", fields=[f"state_{i}" for i in range(n)]),
    ]
    _tail(c, rng, abstain, qs,
          Question("q_lock", "lookup", "After how many minutes does the screen auto-lock?",
                   str(minutes), canon="number", fields=["autolock"]),
          "autolock", ("q_quota", "How much storage is used according to this page?",
                       "storage_used", f"{rng.randint(61, 97)} GB", "text"))
    qs.append(_reading())
    data = {"section": _SECTIONS[active], "toggles": dict(zip(labels, states)),
            "autolock_min": minutes}
    return Rendered(c, "settings", qs, data, default_year=YEAR)


# ---------------------------------------------------------------------------- error dialog
_ERRORS = (("Sync failed", "We could not reach the server."),
           ("Upload interrupted", "The connection was lost during upload."),
           ("Payment not processed", "The request timed out before it finished."),
           ("Session expired", "Your session ended. Please try again."),
           ("Export failed", "The file could not be written to disk."))


def _error(rng, *, theme, scale, abstain):
    inv = Inventor(rng)
    w, h = LOGICAL_SIZES["error"]
    c = Canvas(w, h, scale=scale, theme=theme)
    app = f"{inv.brand()} {rng.choice(('Sync', 'Studio', 'Desk', 'Notes'))}"
    c.rect(0, 0, w, 40, fill="surface")
    for i, col in enumerate(("danger", "warn", "ok")):
        c.ellipse(16 + i * 20, 14, 28 + i * 20, 26, fill=col)
    c.text(100, 12, app, size=13, field="app_name")
    for i in range(7):                               # a blurred-out page behind the dialog
        c.rect(40, 70 + i * 74, 40 + rng.randint(300, 900), 100 + i * 74, fill="surface",
               radius=6)
    c.rect(212, 120, 812, 470, fill="bg", outline="border", radius=12, width=2)
    title, msg = rng.choice(_ERRORS)
    code = f"E-{rng.randint(1000, 9999)}"
    host = f"{inv.brand().lower()}-{rng.randint(1, 9)}.example.org"
    secs = rng.choice((10, 15, 20, 30, 45, 60))
    c.ellipse(244, 152, 284, 192, fill="danger")
    c.text_center(264, 160, "!", size=24, bold=True, color="accent_fg")
    c.text(304, 156, title, size=22, bold=True, field="err_title")
    c.text(304, 196, msg, size=15)
    c.text(304, 226, f"Server: {host}", size=14, color="muted", field="server", value=host)
    c.text(304, 260, f"Error code: {code}", size=16, bold=True, color="danger",
           field="err_code", value=code)
    c.text(304, 300, f"Retrying in {secs} seconds", size=14, field="retry", kind="number",
           value=str(secs))
    _link(c, 304, 336, "View details")
    _link(c, 304, 360, "Report a problem")
    _btn(c, 560, 400, 660, 440, "Retry", fill="accent", color="accent_fg", size=15)
    _btn(c, 676, 400, 776, 440, "Cancel", outline="border", size=15)
    qs = [
        Question("q_code", "lookup", "What error code is shown in the dialog?", code,
                 fields=["err_code"]),
        Question("q_title", "lookup", "What is the title of the error dialog?", title,
                 fields=["err_title"]),
        Question("q_retry", "lookup", "In how many seconds will it retry?", str(secs),
                 canon="number", fields=["retry"]),
    ]
    _tail(c, rng, abstain, qs,
          Question("q_server", "lookup", "Which server could not be reached? Give its host name.",
                   host, fields=["server"]),
          "server", ("q_trace", "What is the trace ID shown in the dialog?", "trace_id",
                     inv.code(4, 4), "text"))
    qs.append(_reading())
    return Rendered(c, "error", qs, {"app": app, "title": title, "code": code, "host": host,
                                     "retry_seconds": secs}, default_year=YEAR)


# ---------------------------------------------------------------------------- notifications
_NOTES = (("Package delivered", "Left at the front door"), ("Meeting starts soon", "Room 4 in 10 minutes"),
          ("Backup complete", "All files are up to date"), ("Storage almost full", "Free up some space"),
          ("New follower", "Someone started following you"), ("Payment received", "A deposit was posted"),
          ("Weekly summary", "Your week at a glance"), ("Battery low", "Charge your device soon"))


def _notifications(rng, *, theme, scale, abstain):
    inv = Inventor(rng)
    w, h = LOGICAL_SIZES["notifications"]
    c = Canvas(w, h, scale=scale, theme=theme)
    n = rng.randint(4, 6)
    apps = []
    while len(apps) < n:
        b = inv.brand()
        if b not in apps:
            apps.append(b)
    notes = rng.sample(_NOTES, n)
    minute = rng.randint(14 * 60, 21 * 60)
    times = []
    for _ in range(n):
        times.append(_hm(minute))
        minute -= rng.randint(5, 55)
    c.text(16, 22, "Notifications", size=24, bold=True)
    _btn(c, w - 100, 22, w - 16, 50, "Clear all", outline="border", size=12, radius=14)
    c.text(16, 60, "Today", size=12, color="muted")
    _link(c, w - 16, 60, "Manage", size=12, right=True)
    y = 84
    for i in range(n):
        c.rect(12, y, w - 12, y + 92, fill="surface", radius=12)
        c.text(28, y + 12, apps[i], size=12, color="accent", field=f"app_{i}")
        c.text_right(w - 28, y + 12, time_12h(times[i]), size=12, color="muted",
                     field=f"time_{i}", kind="time", value=times[i])
        c.text(28, y + 36, notes[i][0], size=15, bold=True, field=f"title_{i}")
        c.text(28, y + 62, notes[i][1], size=12, color="muted")
        c.element(notes[i][0], 12, y, w - 12, y + 92, role="row")
        y += 102
    ia, ib, ic = rng.sample(range(n), 3)
    qs = [
        Question("q_time", "lookup", f"At what time was the notification from {apps[ib]} sent?",
                 times[ib], canon="time", fields=[f"app_{ib}", f"time_{ib}"]),
        Question("q_count", "count", "How many notifications are shown?", str(n),
                 canon="number", fields=[f"app_{i}" for i in range(n)]),
        Question("q_who", "who_said", f'Which app sent the notification "{notes[ia][0]}"?',
                 apps[ia], fields=[f"title_{ia}", f"app_{ia}"]),
    ]
    _tail(c, rng, abstain, qs,
          Question("q_title", "lookup", f"What is the title of the notification from {apps[ic]}?",
                   notes[ic][0], fields=[f"app_{ic}", f"title_{ic}"]),
          f"title_{ic}", ("q_badge", "What number is on the badge of the first app icon?",
                          "badge", f"{rng.randint(41, 99)} alerts", "text"))
    qs.append(_reading())
    data = {"notifications": [{"app": apps[i], "time": times[i], "title": notes[i][0]}
                              for i in range(n)]}
    return Rendered(c, "notifications", qs, data, default_year=YEAR)


# ---------------------------------------------------------------------------- maps card
_KINDS = ("Cafe", "Library", "Clinic", "Market", "Garage", "Pharmacy", "Gym")


def _maps(rng, *, theme, scale, abstain):
    inv = Inventor(rng)
    w, h = LOGICAL_SIZES["maps"]
    c = Canvas(w, h, scale=scale, theme=theme)
    c.rect(0, 0, w, 470, fill="surface")
    for _ in range(7):
        x = rng.randint(20, w - 20)
        c.line(x, 0, x + rng.randint(-60, 60), 470, color="border", width=6)
    for _ in range(5):
        y = rng.randint(20, 440)
        c.line(0, y, w, y + rng.randint(-50, 50), color="border", width=6)
    px, py = rng.randint(30, 120), rng.randint(300, 420)
    qx, qy = rng.randint(250, 350), rng.randint(84, 170)
    c.polyline([(px, py), (px, (py + qy) // 2), (qx, (py + qy) // 2), (qx, qy)], color="accent",
               width=6)
    c.ellipse(px - 9, py - 9, px + 9, py + 9, fill="accent", outline="bg", width=3)
    c.ellipse(qx - 11, qy - 11, qx + 11, qy + 11, fill="danger", outline="bg", width=3)
    c.rect(16, 16, w - 16, 56, fill="bg", outline="border", radius=20)
    c.text(36, 28, "Search here", size=14, color="muted")
    c.element("Search here", 16, 16, w - 16, 56, role="field")
    c.rect(0, 440, w, h, fill="bg", outline="border", radius=20)
    place = f"{inv.brand()} {rng.choice(_KINDS)}"
    street = inv.street_address()
    dist = f"{rng.randint(5, 99) / 10:.1f}"
    mins = rng.randint(6, 48)
    eta = _hm(rng.randint(9 * 60, 20 * 60))
    c.text(20, 470, place, size=24, bold=True, field="place")
    c.text(20, 506, street, size=14, color="muted", field="street")
    c.line(20, 540, w - 20, 540, color="grid")
    for x, label in ((20, "Distance"), (140, "Drive"), (240, "Arrive by")):
        c.text(x, 556, label, size=11, color="muted")
    c.text(20, 576, f"{dist} mi", size=20, bold=True, field="distance", kind="number", value=dist)
    c.text(140, 576, f"{mins} min", size=20, bold=True, field="drive", kind="number",
           value=str(mins))
    c.text(240, 576, time_12h(eta), size=20, bold=True, field="eta", kind="time", value=eta)
    _btn(c, 20, 640, 250, 688, "Start", fill="accent", color="accent_fg", size=17, radius=24)
    _btn(c, 266, 640, w - 20, 688, "Share", outline="border", size=17, radius=24)
    c.line(20, 712, w - 20, 712, color="grid")
    for k, tab in enumerate(("Directions", "Save", "Call", "Photos")):
        cx = 20 + (w - 40) * (k + 0.5) / 4
        c.text_center(cx, 740, tab, size=13, color="accent", anchor="lm")
        c.element(tab, cx - 40, 724, cx + 40, 756, role="tab")
    qs = [
        Question("q_place", "lookup", "What is the name of the place on the card?", place,
                 fields=["place"]),
        Question("q_dist", "lookup", "How many miles away is the destination?", dist,
                 canon="number", fields=["distance"]),
        Question("q_eta", "lookup", "At what time is the estimated arrival?", eta, canon="time",
                 fields=["eta"]),
    ]
    _tail(c, rng, abstain, qs,
          Question("q_street", "lookup", "What is the street address of the place?", street,
                   fields=["street"]),
          "street", ("q_toll", "How much is the toll on this route?", "toll",
                     f"{rng.randint(2, 9)}.{rng.randint(10, 99)} USD", "money"))
    qs.append(_reading())
    return Rendered(c, "maps", qs, {"place": place, "street": street, "distance_mi": dist,
                                    "drive_min": mins, "eta": eta}, default_year=YEAR)


# ---------------------------------------------------------------------------- contact card
def _contact(rng, *, theme, scale, abstain):
    inv = Inventor(rng)
    w, h = LOGICAL_SIZES["contact"]
    c = Canvas(w, h, scale=scale, theme=theme)
    name = inv.person()
    company = inv.merchant()
    mobile = inv.phone()
    work = inv.phone()
    while work == mobile:
        work = inv.phone()
    email = inv.email(name)
    street = inv.street_address()
    _link(c, w - 16, 16, "Edit", size=14, right=True)
    c.ellipse(w / 2 - 44, 40, w / 2 + 44, 128, fill="accent")
    c.text_center(w / 2, 62, "".join(p[0] for p in name.split()), size=36, bold=True,
                  color="accent_fg")
    c.text_center(w / 2, 148, name, size=24, bold=True, field="name")
    c.text_center(w / 2, 184, company, size=14, color="muted", field="company")
    y = 232
    for label, val, fname, kind in (("mobile", mobile, "mobile", "phone"),
                                    ("work", work, "work", "phone"),
                                    ("email", email, "email", "text"),
                                    ("address", street, "street", "text")):
        c.rect(16, y, w - 16, y + 72, fill="surface", radius=12)
        c.text(32, y + 12, label, size=12, color="muted")
        c.text(32, y + 36, val, size=17, color="accent" if kind == "phone" else "fg",
               field=fname, kind=kind)
        y += 84
    for k, label in enumerate(("Message", "Call", "Email")):
        x0 = 16 + k * ((w - 32) / 3)
        _btn(c, x0 + 3, 588, x0 + (w - 32) / 3 - 3, 632, label, fill="surface", color="accent",
             size=14, radius=10)
    _link(c, 16, 660, "Share contact", size=14)
    _link(c, 16, 700, "Delete contact", size=14, color="danger")
    qs = [
        Question("q_mobile", "lookup", "What is the mobile phone number?", mobile, canon="phone",
                 fields=["mobile"]),
        Question("q_work", "lookup", "What is the work phone number?", work, canon="phone",
                 fields=["work"]),
        Question("q_email", "lookup", "What is the email address on the card?", email,
                 fields=["email"]),
    ]
    _tail(c, rng, abstain, qs,
          Question("q_company", "lookup", "Which company does this contact belong to?", company,
                   fields=["company"]),
          "company", ("q_fax", "What is the fax number on the card?", "fax", inv.phone(), "phone"))
    qs.append(_reading())
    return Rendered(c, "contact", qs, {"name": name, "company": company, "mobile": mobile,
                                       "work": work, "email": email, "street": street},
                    default_year=YEAR)


# ---------------------------------------------------------------------------- music player
_SONG_A = ("Paper", "Slow", "Midnight", "Golden", "Quiet", "Open", "River", "Last", "Blue",
           "Hollow", "Neon", "Summer")
_SONG_B = ("Lanterns", "Harbor", "Train", "Garden", "Signals", "Weather", "Hours", "Window",
           "Orchard", "Echoes", "Tides", "Letters")


def _songs(rng: random.Random, n: int) -> list[str]:
    out: list[str] = []
    while len(out) < n:
        s = f"{rng.choice(_SONG_A)} {rng.choice(_SONG_B)}"
        if s not in out:
            out.append(s)
    return out


def _mmss(sec: int) -> tuple[str, str]:
    return f"{sec // 60}:{sec % 60:02d}", f"{sec // 60:02d}:{sec % 60:02d}"


def _music(rng, *, theme, scale, abstain):
    inv = Inventor(rng)
    w, h = LOGICAL_SIZES["music"]
    c = Canvas(w, h, scale=scale, theme=theme)
    songs = _songs(rng, 4)
    track, nxt = songs[0], songs[1:]
    artist = inv.person()
    album = f"{inv.brand()} Sessions"
    total = rng.randint(150, 330)
    elapsed = rng.randint(20, total - 40)
    playing = rng.random() < 0.5
    shuffle, repeat = rng.random() < 0.5, rng.random() < 0.5
    _link(c, 16, 18, "Back", size=14)
    c.text_center(w / 2, 20, "Now Playing", size=15, anchor="la")
    _link(c, w - 16, 18, "Queue", size=14, right=True)
    c.rect(75, 64, 315, 304, fill="surface", radius=16)
    for k, r in enumerate((90, 62, 34)):
        c.ellipse(195 - r, 184 - r, 195 + r, 184 + r, fill="accent" if k % 2 == 0 else "surface")
    c.text(24, 322, track, size=22, bold=True, field="track")
    c.text(24, 356, artist, size=15, field="artist")
    c.text(24, 380, album, size=13, color="muted", field="album")
    c.rect(24, 418, w - 24, 422, fill="border", radius=2)
    frac = elapsed / total
    c.rect(24, 418, 24 + (w - 48) * frac, 422, fill="accent", radius=2)
    c.ellipse(24 + (w - 48) * frac - 6, 414, 24 + (w - 48) * frac + 6, 426, fill="accent")
    e_txt, e_val = _mmss(elapsed)
    t_txt, t_val = _mmss(total)
    c.text(24, 436, e_txt, size=12, color="muted", field="elapsed", kind="time", value=e_val)
    c.text_right(w - 24, 436, t_txt, size=12, color="muted", field="length", kind="time",
                 value=t_val)
    # controls: previous, play or pause, next, each with its visible label under the icon
    for cx, label in ((90, "Previous"), (195, "Pause" if playing else "Play"), (300, "Next")):
        if label == "Previous":
            c.polygon([(cx + 8, 480), (cx + 8, 512), (cx - 12, 496)], fill="fg")
            c.rect(cx - 16, 480, cx - 12, 512, fill="fg")
        elif label == "Next":
            c.polygon([(cx - 8, 480), (cx - 8, 512), (cx + 12, 496)], fill="fg")
            c.rect(cx + 12, 480, cx + 16, 512, fill="fg")
        else:
            c.ellipse(cx - 30, 466, cx + 30, 526, fill="accent")
            if playing:
                c.rect(cx - 10, 482, cx - 3, 510, fill="accent_fg")
                c.rect(cx + 3, 482, cx + 10, 510, fill="accent_fg")
            else:
                c.polygon([(cx - 8, 480), (cx - 8, 512), (cx + 14, 496)], fill="accent_fg")
        c.text_center(cx, 538, label, size=12, color="muted", anchor="lm")
        c.element(label, cx - 36, 462, cx + 36, 548, role="icon")
    for k, (label, on) in enumerate((("Shuffle", shuffle), ("Repeat", repeat))):
        y = 570 + k * 40
        c.text(24, y + 3, label, size=14)
        c.text_right(w - 90, y + 4, "On" if on else "Off", size=13, field=f"{label.lower()}_state",
                     kind="state", value="on" if on else "off")
        _switch(c, w - 72, y, on)
        c.element(label, 24, y - 4, 24 + c.text_width(label, 14) + 8, y + 26, role="toggle")
        c.element(f"{label} switch", w - 74, y - 2, w - 28, y + 24, role="toggle")
    c.text(24, 654, "Up next", size=13, color="muted")
    for k, s in enumerate(nxt):
        y = 680 + k * 44
        c.line(24, y - 6, w - 24, y - 6, color="grid")
        c.text(24, y + 4, s, size=14, field=f"next_{k}")
        c.element(s, 16, y - 4, w - 16, y + 30, role="row")
    qs = [
        Question("q_track", "lookup", "What is the title of the track that is playing?", track,
                 fields=["track"]),
        Question("q_artist", "lookup", "Who is the artist of the current track?", artist,
                 fields=["artist"]),
        Question("q_shuffle", "state", "Is shuffle turned on or off?", "on" if shuffle else "off",
                 canon="state", fields=["shuffle_state"]),
    ]
    _tail(c, rng, abstain, qs,
          Question("q_len", "lookup", "How long is the current track? Give minutes and seconds.",
                   t_val, canon="time", fields=["length"]),
          "length", ("q_lyrics", "Who wrote the lyrics of this track?", "lyricist",
                     inv.person(), "text"))
    qs.append(_reading())
    return Rendered(c, "music", qs, {"track": track, "artist": artist, "album": album,
                                     "elapsed": e_val, "length": t_val, "playing": playing,
                                     "shuffle": shuffle, "repeat": repeat, "up_next": nxt},
                    default_year=YEAR)


# ---------------------------------------------------------------------------- file browser
_STEMS = ("Budget", "Notes", "Invoice", "Photo", "Draft", "Plan", "Minutes", "Backup", "Report",
          "Roster", "Slides", "Scan")
_EXT = ("pdf", "docx", "xlsx", "png", "txt", "zip", "csv")
_PLACES = ("Documents", "Downloads", "Pictures", "Projects", "Shared")


def _files(rng, *, theme, scale, abstain):
    inv = Inventor(rng)
    w, h = LOGICAL_SIZES["files"]
    c = Canvas(w, h, scale=scale, theme=theme)
    n = rng.randint(7, 10)
    names: list[str] = []
    while len(names) < n:
        s = f"{inv.brand()} {rng.choice(_STEMS).lower()}.{rng.choice(_EXT)}"
        if s not in names:
            names.append(s)
    sizes = []
    for _ in names:
        if rng.random() < 0.6:
            sizes.append(f"{rng.randint(12, 980)} KB")
        else:
            sizes.append(f"{rng.randint(1, 48)}.{rng.randint(0, 9)} MB")
    days = [inv.day(date(2027, 1, 4), 330) for _ in names]
    sel = rng.randrange(n)
    place = rng.randrange(len(_PLACES))
    c.rect(0, 0, 220, h, fill="surface")
    c.text(24, 24, inv.brand(), size=16, bold=True, color="accent")
    for i, s in enumerate(_PLACES):
        yy = 76 + i * 40
        if i == place:
            c.rect(12, yy - 8, 208, yy + 26, fill="bg", radius=8)
        c.text(28, yy, s, size=14, color="fg" if i == place else "muted")
        c.element(s, 12, yy - 8, 208, yy + 26, role="menu_item")
    # toolbar
    _btn(c, 244, 14, 344, 48, "New folder", outline="border", size=13)
    _btn(c, 354, 14, 430, 48, "Upload", outline="border", size=13)
    _btn(c, 440, 14, 504, 48, "Sort", outline="border", size=13)
    c.rect(640, 14, 840, 48, fill="bg", outline="border", radius=17)
    c.text(660, 23, "Search files", size=13, color="muted")
    c.element("Search files", 640, 14, 840, 48, role="field")
    _btn(c, 860, 14, 930, 48, "List", fill="accent", color="accent_fg", size=13, role="tab")
    _btn(c, 936, 14, 1006, 48, "Grid", outline="border", size=13, role="tab")
    c.text(244, 70, _PLACES[place], size=22, bold=True, field="folder")
    c.text(268, 118, "Name", size=12, color="muted")
    c.text(620, 118, "Modified", size=12, color="muted")
    c.text_right(990, 118, "Size", size=12, color="muted")
    c.line(244, 138, 1008, 138, color="grid")
    for i in range(n):
        y = 142 + i * 42
        fg = "accent_fg" if i == sel else "fg"
        fg2 = "accent_fg" if i == sel else "muted"
        if i == sel:
            c.rect(240, y, 1008, y + 38, fill="accent", radius=8)
        else:
            c.line(244, y + 40, 1008, y + 40, color="grid")
        c.rect(252, y + 9, 272, y + 29, fill="warn" if names[i].endswith("zip") else "border",
               radius=3, outline=None)
        c.text(284, y + 11, names[i], size=14, color=fg, field=f"fn_{i}", value=names[i])
        c.text(620, y + 12, date_short(days[i]), size=13, color=fg2, field=f"mod_{i}",
               kind="date", value=days[i].isoformat())
        c.text_right(990, y + 12, sizes[i], size=13, color=fg2, field=f"size_{i}", value=sizes[i])
        c.element(names[i], 240, y, 1008, y + 38, role="row")
    ia, ib = rng.sample([i for i in range(n) if i != sel], 2)
    qs = [
        Question("q_selected", "lookup", "Which file is selected (highlighted) in the list?",
                 names[sel], fields=[f"fn_{i}" for i in range(n)]),
        Question("q_mod", "lookup", f"On what date was {names[ib]} last modified?",
                 days[ib].isoformat(), canon="date", fields=[f"fn_{ib}", f"mod_{ib}"]),
        Question("q_count", "count", "How many files are listed in the folder?", str(n),
                 canon="number", fields=[f"fn_{i}" for i in range(n)]),
    ]
    _tail(c, rng, abstain, qs,
          Question("q_size", "lookup", f"What is the size of {names[ia]}?", sizes[ia],
                   fields=[f"fn_{ia}", f"size_{ia}"]),
          f"size_{ia}", ("q_owner", "Who is the owner of the selected file?", "owner",
                         inv.person(), "text"))
    qs.append(_reading())
    return Rendered(c, "files", qs, {"folder": _PLACES[place], "selected": names[sel], "files": [
        {"name": names[i], "size": sizes[i], "modified": days[i].isoformat()} for i in range(n)]},
        default_year=YEAR)


# ---------------------------------------------------------------------------- system alert
_ALERTS = (
    dict(title="Disk almost full", msg="Free up space to keep working.", metric="Free space",
         unit=" GB", kind="decimal", dev="Disk", buttons=("Cancel", "Manage storage", "Ignore"),
         default=1, lo=1, hi=9),
    dict(title="Update available", msg="A new version is ready to install.", metric="New version",
         unit="", kind="version", dev="App", buttons=("Later", "Install now", "Remind me"),
         default=1, lo=2, hi=9),
    dict(title="Battery low", msg="Connect the charger soon.", metric="Charge left", unit="%",
         kind="int", dev="Device", buttons=("Dismiss", "Low power mode"), default=1, lo=3, hi=19),
    dict(title="Network changed", msg="Your connection was switched.", metric="Speed",
         unit=" Mbps", kind="int", dev="Network", buttons=("Cancel", "Details", "Join"),
         default=2, lo=12, hi=480),
)


def _alert(rng, *, theme, scale, abstain):
    inv = Inventor(rng)
    w, h = LOGICAL_SIZES["alert"]
    c = Canvas(w, h, scale=scale, theme=theme)
    spec = rng.choice(_ALERTS)
    c.rect(0, 0, w, 40, fill="surface")
    c.text(24, 12, f"{inv.brand()} Desktop", size=13, color="muted")
    for i in range(6):                               # a window behind the alert
        c.rect(60, 70 + i * 86, 60 + rng.randint(420, 880), 110 + i * 86, fill="surface", radius=6)
    c.rect(296, 130, 728, 478, fill="bg", outline="border", radius=14, width=2)
    c.ellipse(332, 160, 376, 204, fill="warn")
    c.text_center(354, 182, "!", size=24, bold=True, color="accent_fg", anchor="lm")
    c.text(396, 160, spec["title"], size=19, bold=True, field="alert_title", value=spec["title"])
    c.text(396, 192, spec["msg"], size=13, color="muted")
    _link(c, 396, 214, "Learn more", size=13)
    dev = f"{inv.brand()} {rng.choice(('One', 'Plus', 'Air', 'Pro'))}"
    if spec["kind"] == "decimal":
        val = f"{rng.randint(spec['lo'], spec['hi'])}.{rng.randint(1, 9)}"
    elif spec["kind"] == "version":
        val = f"{rng.randint(spec['lo'], spec['hi'])}.{rng.randint(1, 30)}"
    else:
        val = str(rng.randint(spec["lo"], spec["hi"]))
    c.text(332, 240, f"{spec['dev']}: {dev}", size=15, field="device", value=dev)
    c.text(332, 272, f"{spec['metric']}: {val}{spec['unit']}", size=16,
           field="metric", kind="number", value=val)
    c.rect(332, 322, 350, 340, outline="fg", radius=3, width=1.5)
    c.text(360, 322, "Do not show this again", size=13)
    c.element("Do not show this again", 328, 318, 560, 344, role="toggle")
    btns = spec["buttons"]
    x = 696
    for i in range(len(btns) - 1, -1, -1):
        label = btns[i]
        bw = c.text_width(label, 14) + 36
        x0 = x - bw
        if i == spec["default"]:
            _btn(c, x0, 410, x, 450, label, fill="accent", color="accent_fg", size=14,
                 field="default_btn")
        else:
            _btn(c, x0, 410, x, 450, label, outline="border", size=14)
        x = x0 - 12
    default = btns[spec["default"]]
    qs = [
        Question("q_title", "lookup", "What is the title of the alert?", spec["title"],
                 fields=["alert_title"]),
        Question("q_metric", "lookup", f"What {spec['metric'].lower()} does the alert state? "
                 "Give the number.", val, canon="number", fields=["metric"]),
        Question("q_default", "lookup", "Which button is highlighted as the default button?",
                 default, fields=["default_btn"]),
    ]
    _tail(c, rng, abstain, qs,
          Question("q_device", "lookup", f"What is the name of the {spec['dev'].lower()} in the "
                   "alert?", dev, fields=["device"]),
          "device", ("q_code", "What is the error code in the alert?", "alert_code",
                     inv.code(2, 4), "text"))
    qs.append(_reading())
    return Rendered(c, "alert", qs, {"title": spec["title"], "device": dev, "metric": val,
                                     "buttons": list(btns), "default": default},
                    default_year=YEAR)


# ---------------------------------------------------------------------------- alarms
_ALARM_LABELS = ("Wake up", "Gym", "Take medicine", "Meeting prep", "School run", "Water plants",
                 "Bedtime", "Stand-up", "Laundry", "Pick up parcel")
_DAYS = ("Every day", "Mon Wed Fri", "Weekdays", "Sat Sun", "Tue Thu", "Mon only")


def _alarms(rng, *, theme, scale, abstain):
    inv = Inventor(rng)
    w, h = LOGICAL_SIZES["alarms"]
    c = Canvas(w, h, scale=scale, theme=theme)
    n = rng.randint(4, 6)
    mins = sorted(rng.sample(range(5 * 60, 23 * 60, 5), n))
    labels = rng.sample(_ALARM_LABELS, n)
    days = [rng.choice(_DAYS) for _ in range(n)]
    on = [rng.random() < 0.55 for _ in range(n)]
    on[rng.randrange(n)] = True
    nxt = rng.choice([i for i in range(n) if on[i]])
    c.text(16, 22, "Alarms", size=26, bold=True)
    _btn(c, w - 86, 24, w - 16, 54, "Add", outline="accent", color="accent", size=14, radius=15)
    y = 84
    for i in range(n):
        if i == nxt:
            c.rect(8, y - 4, w - 8, y + 82, fill="surface", radius=12)
        t12 = time_12h(_hm(mins[i]))
        c.text(20, y + 6, t12, size=30, field=f"time_{i}", kind="time", value=_hm(mins[i]))
        c.text(20, y + 48, labels[i], size=14, field=f"label_{i}", value=labels[i])
        c.text(20, y + 66, days[i], size=11, color="muted")
        if i == nxt:
            c.text_center(w - 49, y + 4, "Next", size=11, color="accent", anchor="la")
        _switch(c, w - 70, y + 24, on[i])
        c.text_center(w - 49, y + 52, "On" if on[i] else "Off", size=12, field=f"state_{i}",
                      kind="state", value="on" if on[i] else "off")
        c.element(labels[i], 8, y - 4, w - 8, y + 82, role="row")
        c.element(f"{labels[i]} switch", w - 72, y + 22, w - 26, y + 48, role="toggle")
        c.line(20, y + 86, w - 20, y + 86, color="grid")
        y += 92
    c.rect(0, 780, w, h, fill="surface")
    for k, tab in enumerate(("World Clock", "Alarm", "Timer", "Stopwatch")):
        cx = 49 + k * 97.5
        c.text_center(cx, 800, tab, size=12, color="accent" if tab == "Alarm" else "muted",
                      anchor="lm")
        c.element(tab, cx - 44, 784, cx + 44, 836, role="tab")
    ia, ib, ic = rng.sample([i for i in range(n) if i != nxt], 3)
    word = {True: "on", False: "off"}
    qs = [
        Question("q_time", "lookup", f'At what time is the alarm "{labels[ia]}" set?',
                 _hm(mins[ia]), canon="time", fields=[f"label_{ia}", f"time_{ia}"]),
        Question("q_next", "lookup", "Which alarm is highlighted and marked Next? Give its label.",
                 labels[nxt], fields=[f"label_{i}" for i in range(n)]),
        Question("q_label", "lookup", f"What is the label of the alarm set for "
                 f"{time_12h(_hm(mins[ic]))}?", labels[ic], fields=[f"label_{ic}", f"time_{ic}"]),
    ]
    _tail(c, rng, abstain, qs,
          Question("q_state", "state", f'Is the alarm "{labels[ib]}" turned on or off?',
                   word[on[ib]], canon="state", fields=[f"label_{ib}", f"state_{ib}"]),
          f"state_{ib}", ("q_snooze", "How many minutes is the snooze?", "snooze",
                          f"{rng.randint(5, 15)} min", "text"))
    qs.append(_reading())
    return Rendered(c, "alarms", qs, {"alarms": [{"time": _hm(mins[i]), "label": labels[i],
                                                   "days": days[i], "on": on[i]}
                                                  for i in range(n)], "next": labels[nxt]},
                    default_year=YEAR)
