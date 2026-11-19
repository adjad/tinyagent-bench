"""App screens: chat, inbox, settings, error dialog, notifications, maps card, contact card.

Planted fields: sender, message, time, error code, toggle states, unread count, place, phone.
Question types: lookup, who_said, state, count (and the reading).

Seven templates on purpose: the generator picks template = index % len(TEMPLATES), and an odd
count that does not divide 10 keeps every template reachable in light and dark, at both scales,
and in abstention items.
"""
from __future__ import annotations

import random

from edat.media.common import GIVEN_NAMES, Inventor, time_12h
from edat.media.vision.canvas import Canvas
from edat.media.vision.items import ABSTAIN_ANSWER, Question, Rendered

FAMILY = "app_screens"
TEMPLATES = ("chat", "inbox", "settings", "error", "notifications", "maps", "contact")
LOGICAL_SIZES = {"chat": (390, 844), "inbox": (390, 844), "settings": (1024, 640),
                 "error": (1024, 640), "notifications": (390, 844), "maps": (390, 844),
                 "contact": (390, 844)}
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
    c.text(16, 22, title, size=17, bold=True, field="chat_title", value=title)
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
        c.rect(800, y + 11, 842, y + 33, fill="ok" if on else "border", radius=11)
        kx = 822 if on else 802
        c.ellipse(kx, y + 13, kx + 18, y + 31, fill="bg")
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
    c.rect(560, 400, 660, 440, fill="accent", radius=8)
    c.text_center(610, 411, "Retry", size=15, bold=True, color="accent_fg")
    c.rect(676, 400, 776, 440, outline="border", radius=8, width=2)
    c.text_center(726, 411, "Cancel", size=15)
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
    c.text(16, 56, "Today", size=12, color="muted")
    y = 84
    for i in range(n):
        c.rect(12, y, w - 12, y + 92, fill="surface", radius=12)
        c.text(28, y + 12, apps[i], size=12, color="accent", field=f"app_{i}")
        c.text_right(w - 28, y + 12, time_12h(times[i]), size=12, color="muted",
                     field=f"time_{i}", kind="time", value=times[i])
        c.text(28, y + 36, notes[i][0], size=15, bold=True, field=f"title_{i}")
        c.text(28, y + 62, notes[i][1], size=12, color="muted")
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
    qx, qy = rng.randint(250, 350), rng.randint(60, 160)
    c.polyline([(px, py), (px, (py + qy) // 2), (qx, (py + qy) // 2), (qx, qy)], color="accent",
               width=6)
    c.ellipse(px - 9, py - 9, px + 9, py + 9, fill="accent", outline="bg", width=3)
    c.ellipse(qx - 11, qy - 11, qx + 11, qy + 11, fill="danger", outline="bg", width=3)
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
    c.rect(20, 640, 250, 688, fill="accent", radius=24)
    c.text_center(135, 653, "Start", size=17, bold=True, color="accent_fg")
    c.rect(266, 640, w - 20, 688, outline="border", radius=24, width=2)
    c.text_center((266 + w - 20) / 2, 653, "Share", size=17)
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
