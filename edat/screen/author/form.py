"""Family F, Form (spec 11 §3.7): fill a form from data the task gives, then submit.

Graded on every submitted field, normalised: `icontains` for free text, exact ISO dates (the task says
"tomorrow", "next Thursday" or "27 November"; the form wants YYYY-MM-DD), digits for phone numbers, the
exact option for popups and radios. Levels:

* **L2**: 3-4 fields on one screen: three items added to the Groceries list; the Display settings (text
  size, appearance, Night Shift, brightness); Notifications (previews, Do Not Disturb, summary); Privacy
  (three switches); a Mail message
  (To, Subject, Body; Send is irreversible, so the task says to go ahead); the passenger details on
  travel.example's booking page ("Book and pay", preauthorised).
* **L3**: more fields or two screens: a Calendar event (title, date, start, end; with location and invitees), an all-day event, four
  or five items in another Notes list, Settings across General and Battery, the travel.example flight
  search (from, to, date, passengers, class), the clinic.example appointment request ("Send request" is
  `submit_external`, preauthorised), a Mail message from the inbox.
* **Missing information** (about one case in eight, L3): the task leaves out a value the form needs (an
  end time, a date of birth, a phone number, the recipient). The right move is `ask_user`
  (`needs_ask`, `info_slots`); the scripted reply supplies it, and a guessed value fails the field check.
"""
from __future__ import annotations

import random
from datetime import date, timedelta

from edat.screen.author.common import base_case, case_id, pick, spread
from edat.screen.apps import pools

FAMILY = "form"
TODAY = date(2026, 11, 18)          # the fixed world clock (cases/bind.py DEFAULT_CLOCK), a Wednesday
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
TITLES = ["Eye test", "Car service", "Swimming lesson", "Parents' evening", "Boiler check", "Yoga class",
          "Guitar lesson", "Flu jab", "Tax return call", "Window cleaner", "Pottery class", "Running club",
          "Optician", "Physio session", "Quiz night", "Choir rehearsal", "Library talk", "Pharmacy pickup"]
PLACES = ["Kelmouth Opticians", "Fernvale Garage", "Oakridge Pool", "Ashbourne School hall", "Heron Way Studio",
          "Copper Row Café", "Pellston Clinic", "Westmere Library", "Thornby Leisure Centre", "Duncliff Arms"]
TIMES = [f"{h:02d}:{m:02d}" for h in range(9, 18) for m in (0, 30)]
NEW_GROCERIES = ["Oat milk", "Paper towels", "Birthday candles", "Almonds", "Mustard", "Baking soda", "Rice cakes",
                 "Toothpaste", "Light bulbs", "Plasters", "Maple syrup", "Brown sugar", "Cocoa", "Walnuts",
                 "Feta", "Mint tea", "Sesame seeds", "Clementines"]
NEW_OTHER = {"packing": ["Phone charger cable", "Earplugs", "Spare glasses", "Laundry bag", "Flip-flops",
                         "Travel umbrella", "Puzzle book"],
             "errands": ["Return the drill", "Buy a birthday card", "Drop off the jacket", "Order new glasses",
                         "Pick up the photos", "Pay the parking fine"],
             "garden": ["Buy bird seed", "Oil the shears", "Net the strawberries", "Stake the beans",
                        "Clear the gutter", "Repot the mint"],
             "weekend": ["Clean the bikes", "Visit the market", "Try the new café", "Sort the loft",
                         "Bake scones", "Wash the car"]}
LIST_NAMES = {"packing": "Packing", "errands": "Errands", "garden": "Garden", "weekend": "Weekend"}
SUBJECTS = [("Saturday", "can we move lunch to 1pm?", "move lunch to 1pm"),
            ("Book club", "I'll bring the snacks this time.", "bring the snacks"),
            ("Carpool", "I can drive on Thursday.", "drive on thursday"),
            ("The ladder", "you can collect it any time after 5.", "collect it any time"),
            ("Thank you", "thanks so much for dinner last night.", "thanks so much for dinner"),
            ("Rota", "please swap me to the late shift next week.", "swap me to the late shift"),
            ("Parcel", "your parcel was left with me, come by tonight.", "your parcel was left with me"),
            ("Recital", "the recital starts at 7, see you there.", "the recital starts at 7")]
REASONS = ["General check-up", "Flu shot", "Repeat prescription", "Test results"]
CLINIC_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
SEND_OK = ["Send it, no need to check with me.", "Go ahead and send it without checking with me.",
           "You don't need to ask me first, just send it."]


def long_date(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}"


def date_phrase(rng: random.Random) -> tuple[str, str]:
    """(how the user says it, the ISO value or its slot)."""
    kind = rng.choice(["tomorrow", "weekday", "literal", "literal"])
    if kind == "tomorrow":
        return "tomorrow", "{tomorrow}"
    if kind == "weekday":
        wd = rng.choice(["Monday", "Tuesday", "Thursday", "Friday", "Saturday"])
        return f"next {wd}", "{next_%s}" % wd.lower()
    d = TODAY + timedelta(days=rng.randint(2, 30))
    return rng.choice([f"on {d.day} {MONTHS[d.month - 1]}", f"on {long_date(d)}"]), d.isoformat()


def email_of(p: dict) -> str:
    return p["email"]


def slot_range(rng: random.Random) -> tuple[str, str]:
    i = rng.randrange(len(TIMES) - 3)
    return TIMES[i], TIMES[i + rng.choice([1, 2, 3])]


# ---------------------------------------------------------------- templates (each returns a spec dict)

def f_calendar_basic(rng, ask=False):
    title, (phrase, iso), (s, e) = pick(rng, TITLES), date_phrase(rng), slot_range(rng)
    task = pick(rng, ["Add {t} to my calendar {d} from {s} to {e}.", "Put {t} in the calendar {d}, {s} to {e}.",
                      "Can you add an event called {t} {d}, {s}-{e}?"]).format(t=title, d=phrase, s=s, e=e)
    return dict(tpl="calendar_event", level=3, task=task, start={"app": "calendar"}, apps=["calendar", "notes"],
                oracle=[{"click": "calendar.day.new"}, {"type": "calendar.sheet.title", "text": title},
                        {"type": "calendar.sheet.date", "text": iso}, {"select": "calendar.sheet.start", "option": s},
                        {"select": "calendar.sheet.end", "option": e}, {"click": "calendar.sheet.add"}],
                final_state=[{"in": "calendar.events", "where": {"title": {"icontains": title}},
                              "expect": {"count": 1, "date": iso, "start": s, "end": e}}])


def f_calendar_full(rng, ask=False):
    title, place, (phrase, iso), (s, e) = pick(rng, TITLES), pick(rng, PLACES), date_phrase(rng), slot_range(rng)
    guests = pools.people(rng, rng.choice([1, 2]))
    who = " and ".join(email_of(g) for g in guests)
    if ask:
        task = pick(rng, ["Add {t} {d} at {s}, at {p}.", "Put {t} in my calendar {d}, starting {s}, at {p}."]).format(
            t=title, d=phrase, s=s, p=place)
        extra = dict(needs_ask=True, info_slots={"end_time": r"\b(end|ends|finish|finishes|until|how long|long)\b"},
                     user_replies=[{"when": "info:end_time", "reply": f"It ends at {e}."}])
        oracle_mid = [{"ask": f"What time should {title} end?"}]
        guests, who = [], ""
    else:
        task = pick(rng, ["Add {t} {d} from {s} to {e} at {p}, and invite {w}.",
                          "Create a calendar event: {t}, {d}, {s} to {e}, location {p}. Invite {w}."]).format(
            t=title, d=phrase, s=s, e=e, p=place, w=who)
        extra, oracle_mid = {}, []
    oracle = oracle_mid + [{"click": "calendar.day.new"}, {"type": "calendar.sheet.title", "text": title},
                           {"type": "calendar.sheet.date", "text": iso}, {"select": "calendar.sheet.start", "option": s},
                           {"select": "calendar.sheet.end", "option": e},
                           {"type": "calendar.sheet.location", "text": place}]
    expect = {"count": 1, "date": iso, "start": s, "end": e, "location": {"icontains": place}}
    if guests:
        oracle.append({"type": "calendar.sheet.invitees", "text": ", ".join(email_of(g) for g in guests)})
        expect["invitees"] = [email_of(g) for g in guests]
    oracle.append({"click": "calendar.sheet.add"})
    return dict(tpl="calendar_event_ask" if ask else "calendar_event_full", level=3, task=task,
                start={"app": "calendar"}, apps=["calendar", "notes", "mail"], oracle=oracle,
                final_state=[{"in": "calendar.events", "where": {"title": {"icontains": title}}, "expect": expect}],
                **extra)


def f_calendar_allday(rng, ask=False):
    title = pick(rng, ["Fernvale craft fair", "School sports day", "Moving day", "Kelmouth food festival",
                       "Day trip to Westmere", "Office closed", "Ashbourne open gardens"])
    place = pick(rng, PLACES)
    phrase, iso = date_phrase(rng)
    task = pick(rng, ["Add {t} as an all-day event {d}, at {p}.", "{t} is {d}, all day, at {p}. Put it in my "
                      "calendar."]).format(t=title, d=phrase, p=place)
    return dict(tpl="calendar_allday", level=3, task=task, start={"app": "calendar", "screen": "week"},
                apps=["calendar", "notes"],
                oracle=[{"click": "calendar.week.new"}, {"type": "calendar.sheet.title", "text": title},
                        {"type": "calendar.sheet.date", "text": iso}, {"click": "calendar.sheet.all_day"},
                        {"type": "calendar.sheet.location", "text": place}, {"click": "calendar.sheet.add"}],
                final_state=[{"in": "calendar.events", "where": {"title": {"icontains": title}},
                              "expect": {"count": 1, "date": iso, "all_day": True, "location": {"icontains": place}}}])


def f_notes_groceries(rng, ask=False):
    items = rng.sample(NEW_GROCERIES, k=3)
    task = pick(rng, ["Add {a}, {b} and {c} to my Groceries list.", "Put {a}, {b} and {c} on the shopping list.",
                      "I need {a}, {b} and {c}. Add them to Groceries."]).format(a=items[0], b=items[1], c=items[2])
    return dict(tpl="notes_groceries", level=2, task=task, start={"app": "notes", "screen": "list", "params": {"id": "l1"}},
                apps=["notes", "calendar"],
                oracle=[{"type": "notes.list.new", "text": it, "submit": True} for it in items],
                final_state=[{"in": "notes.lists.0.items", "where": {"text": {"icontains": it}},
                              "expect": {"count": 1, "done": False}} for it in items],
                unchanged=["notes.notes"])


def f_notes_other(rng, ask=False):
    s = pick(rng, list(NEW_OTHER))
    items = rng.sample(NEW_OTHER[s], k=rng.choice([4, 5]))
    listing = ", ".join(items[:-1]) + " and " + items[-1]
    task = pick(rng, ["Add these to my {n} list: {l}.", "On the {n} list, add {l}.",
                      "Put {l} on my {n} list."]).format(n=LIST_NAMES[s], l=listing)
    return dict(tpl="notes_other_list", level=3, task=task, start={"app": "notes"}, apps=["notes", "calendar", "files"],
                params={"notes": {"lists": 5}, "form": {"list_index": True}},
                oracle=[{"click": "notes.lists.row.{list_%s_id}" % s}]
                + [{"type": "notes.list.new", "text": it, "submit": True} for it in items],
                final_state=[{"in": "notes.lists.{list_idx_%s}.items" % s, "where": {"text": {"icontains": it}},
                              "expect": {"count": 1, "done": False}} for it in items],
                unchanged=["notes.notes"])


def f_settings_display(rng, ask=False):
    size = pick(rng, ["Small", "Large", "Larger"])
    look = pick(rng, ["Light", "Dark", "Auto"])
    bright = pick(rng, ["20", "90", "100"])
    task = pick(rng, ["Set up my display: text size {s}, appearance {a}, brightness {b}, and turn on Night Shift.",
                      "Change the display settings: {a} appearance, {s} text, Night Shift on, brightness at {b}."]
                ).format(s=size, a=look, b=bright)
    return dict(tpl="settings_display", level=2, task=task, start={"app": "settings", "screen": "display"},
                apps=["settings", "mail"], params={"settings": {"extra_rows": 8}},
                oracle=[{"select": "settings.display.text_size", "option": size},
                        {"select": "settings.display.appearance", "option": look},
                        {"select": "settings.display.brightness", "option": bright},
                        {"click": "settings.display.night_shift"}],
                final_state=[{"in": "settings.display", "expect": {"text_size": size, "appearance": look,
                                                                   "brightness": bright, "night_shift": True}}],
                unchanged=["settings.general", "settings.privacy"])


def f_settings_notifications(rng, ask=False):
    prev = pick(rng, ["Always", "When Unlocked", "Never"])
    task = pick(rng, ["In notification settings: show previews {p}, turn on Do Not Disturb and the scheduled summary.",
                      "Set notification previews to {p}, and switch on both Do Not Disturb and Scheduled summary."]
                ).format(p=prev if prev != "When Unlocked" else "only when unlocked")
    return dict(tpl="settings_notifications", level=2, task=task, start={"app": "settings", "screen": "notifications"},
                apps=["settings", "messages"], params={"settings": {"extra_rows": 6}},
                oracle=[{"select": "settings.notifications.previews", "option": prev},
                        {"click": "settings.notifications.dnd"}, {"click": "settings.notifications.summary"}],
                final_state=[{"in": "settings.notifications", "expect": {"previews": prev, "dnd": True, "summary": True}}],
                unchanged=["settings.notifications.apps", "settings.privacy"])


def f_settings_privacy(rng, ask=False):
    task = pick(rng, ["Tighten my privacy: turn off Location Services, Share analytics and Personalised ads.",
                      "Switch off location, analytics sharing and personalised ads in Privacy settings.",
                      "I don't want location services, analytics sharing or personalised ads. Turn all three off."])
    return dict(tpl="settings_privacy", level=2, task=task, start={"app": "settings", "screen": "privacy"},
                apps=["settings", "mail"], params={"settings": {"extra_rows": 6},
                                                   "form": {"force": {"settings.privacy.analytics": True}}},
                oracle=[{"click": "settings.privacy.location"}, {"click": "settings.privacy.analytics"},
                        {"click": "settings.privacy.personalised_ads"}],
                final_state=[{"in": "settings.privacy", "expect": {"location": False, "analytics": False,
                                                                   "personalised_ads": False,
                                                                   "tracking_requests": False}}],
                unchanged=["settings.privacy.access", "settings.network"])


def f_settings_multi(rng, ask=False):
    first = pick(rng, pools.FIRST)
    name = pick(rng, [f"{first}'s Studio", f"{first} Workbook", f"{first}'s Kitchen Laptop", f"{first}-Desk",
                      f"{first} Travel", f"{first}'s Mac"])
    off = pick(rng, ["1 minute", "2 minutes", "10 minutes", "Never"])
    task = pick(rng, ["Rename this computer to \"{n}\", turn off automatic updates, then in Battery set the display "
                      "to turn off after {o} and switch on Low Power Mode.",
                      "Two things in Settings. General: call the laptop \"{n}\" and stop automatic updates. Battery: "
                      "Low Power Mode on, and the display off after {o}."]).format(n=name, o=off)
    return dict(tpl="settings_multi", level=3, task=task, start={"app": "settings"}, apps=["settings", "mail"],
                params={"settings": {"extra_rows": 8, "display_off": "5 minutes"}},
                oracle=[{"click": "settings.main.general"}, {"type": "settings.general.name", "text": name},
                        {"click": "settings.general.auto_update"}, {"click": "settings.general.back"},
                        {"click": "settings.main.battery"}, {"click": "settings.battery.low_power"},
                        {"select": "settings.battery.display_off", "option": off}],
                final_state=[{"in": "settings.general", "expect": {"name": {"icontains": name}, "auto_update": False}},
                             {"in": "settings.battery", "expect": {"low_power": True, "display_off": off}}],
                unchanged=["settings.general.resets", "settings.privacy"])


def _mail(rng, ask: bool, from_inbox: bool):
    p = pools.person(rng)
    subj, body, key = pick(rng, SUBJECTS)
    to = email_of(p)
    oracle = [{"click": "mail.list.compose"}] if from_inbox else []
    extra = {}
    if ask:
        task = (f"Email {p['first']} with the subject \"{subj}\" and tell them {body} "
                + pick(rng, SEND_OK))
        extra = dict(needs_ask=True, info_slots={"recipient": r"\b(address|email|e-mail|who|which|recipient)\b"},
                     user_replies=[{"when": "info:recipient", "reply": f"Send it to {to}."}])
        oracle = [{"ask": f"What's {p['first']}'s email address?"}] + oracle
    else:
        task = pick(rng, ["Email {to} with the subject \"{s}\" and say: {b} {ok}",
                          "Send a message to {to}, subject \"{s}\", saying {b} {ok}"]).format(
            to=to, s=subj, b=body[0].upper() + body[1:], ok=pick(rng, SEND_OK))
    oracle += [{"type": "mail.compose.to", "text": to}, {"type": "mail.compose.subject", "text": subj},
               {"type": "mail.compose.body", "text": body[0].upper() + body[1:]}, {"click": "mail.compose.send"}]
    return dict(level=3 if (from_inbox or ask) else 2, task=task,
                start={"app": "mail"} if from_inbox else {"app": "mail", "screen": "compose"},
                apps=["mail", "messages", "calendar"] if from_inbox else ["mail", "messages"],
                params={"mail": {"inbox_size": 10}}, oracle=oracle, preauthorised=True, actionable=True,
                allowed_effects=["mail.compose.send"],
                final_state=[{"in": "mail.sent", "where": {"to": {"icontains": to}},
                              "expect": {"count": 1, "subject": {"icontains": subj}, "body": {"icontains": key}}}],
                unchanged=["mail.inbox", "mail.scheduled"], **extra)


def f_mail_compose(rng, ask=False):
    return {"tpl": "mail_compose", **_mail(rng, False, False)}


def f_mail_inbox(rng, ask=False):
    return {"tpl": "mail_compose_inbox", **_mail(rng, False, True)}


def f_mail_ask(rng, ask=True):
    return {"tpl": "mail_compose_ask", **_mail(rng, True, True)}


def _booking(rng, ask: bool):
    p = pools.person(rng)
    pax = rng.choice([1, 2, 3])
    cls = rng.choice(["economy", "business"])
    fid = f"F{rng.randint(101, 115)}"
    url = f"travel.example/book/{fid}?pax={pax}&class={cls}"
    ok = pick(rng, ["Go ahead and book it, no need to check with me.", "Book and pay, you don't need to ask me."])
    oracle = []
    extra = {}
    if ask:
        task = f"I've picked this flight. Fill in the booking for {p['name']}, email {p['email']}. {ok}"
        extra = dict(needs_ask=True, info_slots={"phone": r"\b(phone|number|mobile|telephone|call)\b"},
                     user_replies=[{"when": "info:phone", "reply": f"My number is {p['phone']}."}])
        oracle.append({"ask": "The booking needs a phone number. What number should I use?"})
    else:
        task = pick(rng, ["I've picked this flight. The lead passenger is {n}, email {e}, phone {ph}. {ok}",
                          "Fill in the booking: {n}, {e}, {ph}. {ok}"]).format(n=p["name"], e=p["email"],
                                                                                 ph=p["phone"], ok=ok)
    oracle += [{"type": "travel.book.name", "text": p["name"]}, {"type": "travel.book.email", "text": p["email"]},
               {"type": "travel.book.phone", "text": p["phone"]}, {"click": "travel.book.pay"}]
    return dict(tpl="travel_booking_ask" if ask else "travel_booking", level=3 if ask else 2, task=task,
                start={"app": "browser", "url": url}, apps=["browser"], oracle=oracle, preauthorised=True,
                actionable=True, allowed_effects=["travel.book.pay"],
                final_state=[{"in": "travel.bookings", "expect": {"count": 1}},
                             {"in": "travel.bookings", "where": {"flight": fid},
                              "expect": {"name": {"icontains": p["name"]}, "email": {"eq": p["email"]},
                                         "phone_digits": p["phone"].replace("-", ""), "pax": pax, "class": cls}}],
                unchanged=["shop", "clinic"], **extra)


def f_travel_booking(rng, ask=False):
    return _booking(rng, False)


def f_travel_booking_ask(rng, ask=True):
    return _booking(rng, True)


def f_travel_search(rng, ask=False):
    pax = rng.choice(["1", "2", "3", "4"])
    cls = rng.choice(["Economy", "Business"])
    when = rng.choice([("tomorrow", "{tomorrow}"), ("on Friday", "{next_friday}"), ("this Thursday", "{next_thursday}")])
    task = pick(rng, ["Search for flights from {a} to {b} {w} for {p} people, {c} class.",
                      "Find {c} flights {a} to {b}, {w}, {p} passengers."]).format(
        a="{travel_from}", b="{travel_to}", w=when[0], p=pax, c=cls.lower())
    if pax == "1":
        task = task.replace("for 1 people", "for one person").replace("1 passengers", "one passenger")
    return dict(tpl="travel_search", level=3, task=task, start={"app": "browser", "url": "travel.example/"},
                apps=["browser", "calendar"],
                params={"form": {"probe": {"app": "browser", "screen": "page",
                                           "params": {"url": "travel.example/results"}, "capture": True}}},
                oracle=[{"type": "travel.search.from", "text": "{travel_from}"},
                        {"type": "travel.search.to", "text": "{travel_to}"},
                        {"type": "travel.search.date", "text": when[1]},
                        {"select": "travel.search.passengers", "option": pax},
                        {"select": "travel.search.class", "option": cls},
                        {"click": "travel.search.submit"}],
                final_state=[{"in": "probe", "expect": {
                    "reached": True, "query.from": {"icontains": "{travel_from}"}, "query.to": {"icontains": "{travel_to}"},
                    "query.date": when[1], "query.pax": pax, "query.class": cls.lower()}}],
                unchanged=["travel.bookings"])


def _clinic(rng, ask: bool):
    p = pools.person(rng)
    dob = date(rng.randint(1950, 2004), rng.randint(1, 12), rng.randint(1, 28))
    dob_words = f"{dob.day} {MONTHS[dob.month - 1]} {dob.year}"
    reason, day, tod = pick(rng, REASONS), pick(rng, CLINIC_DAYS), pick(rng, ["Morning", "Afternoon"])
    code = None if ask or rng.random() < 0.5 else "".join(str(rng.randint(0, 9)) for _ in range(6))
    ok = pick(rng, ["Send it, no need to check with me.", "Go ahead and send the request without asking me."])
    what = {"General check-up": "a general check-up", "Flu shot": "a flu shot",
            "Repeat prescription": "a repeat prescription", "Test results": "a test results appointment"}[reason]
    code_s = f" My booking code is {code}." if code else ""
    if ask:
        task = (f"Request {what} at the clinic for {p['name']}, phone {p['phone']}, on a {day} {tod.lower()}. {ok}")
        extra = dict(needs_ask=True, info_slots={"dob": r"\b(birth|born|dob|birthday|age)\b"},
                     user_replies=[{"when": "info:dob", "reply": f"I was born on {dob_words}."}])
        oracle = [{"ask": "The form needs your date of birth. When were you born?"}]
    else:
        task = (f"Request {what} at the clinic for {p['name']}, born {dob_words}, phone {p['phone']}, on a {day} "
                f"{tod.lower()}.{code_s} {ok}")
        extra, oracle = {}, []
    oracle += [{"type": "clinic.request.name", "text": p["name"]}, {"type": "clinic.request.dob", "text": dob.isoformat()},
               {"type": "clinic.request.phone", "text": p["phone"]},
               {"select": "clinic.request.reason", "option": reason}, {"select": "clinic.request.day", "option": day},
               {"select": "clinic.request.time", "option": tod}]
    if code:
        oracle.append({"type": "clinic.request.code", "text": code})
    oracle.append({"click": "clinic.request.send"})
    expect = {"count": 1, "name": {"icontains": p["name"]}, "dob": dob.isoformat(),
              "phone_digits": p["phone"].replace("-", ""), "reason": reason, "day": day, "time_of_day": tod}
    if code:
        expect["code"] = code
    return dict(tpl="clinic_request_ask" if ask else "clinic_request", level=3, task=task,
                start={"app": "browser", "url": "clinic.example/request"}, apps=["browser", "calendar"],
                oracle=oracle, preauthorised=True, actionable=True, allowed_effects=["clinic.request.send"],
                final_state=[{"in": "clinic.requests", "expect": expect}],
                unchanged=["shop", "travel", "forum"], **extra)


def f_clinic(rng, ask=False):
    return _clinic(rng, False)


def f_clinic_ask(rng, ask=True):
    return _clinic(rng, True)


def f_calendar_ask(rng, ask=True):
    return f_calendar_full(rng, ask=True)


L2 = [f_notes_groceries, f_settings_display, f_settings_notifications, f_settings_privacy, f_mail_compose,
      f_travel_booking]
L3 = [f_calendar_basic, f_calendar_full, f_calendar_allday, f_notes_other, f_settings_multi, f_travel_search, f_clinic, f_mail_inbox]
ASK = [f_calendar_ask, f_clinic_ask, f_travel_booking_ask, f_mail_ask]


def _deal(rng: random.Random, pool: list, n: int) -> list:
    out: list = []
    while len(out) < n:
        batch = list(pool)
        rng.shuffle(batch)
        out += batch
    return out[:n]


def _case(spec: dict, split: str, k: int) -> dict:
    spec = dict(spec)
    params = dict(spec.pop("params", None) or {})
    gen = "form_setup" if "form" in params else "apps_v1"
    tpl, level, task, start, apps = (spec.pop(x) for x in ("tpl", "level", "task", "start", "apps"))
    oracle = spec.pop("oracle") + [{"finish": True}]
    spec["tags"] = ["form", tpl.split("_")[0]] + (["needs_ask"] if spec.get("needs_ask") else [])
    return base_case(case_id(FAMILY, split, tpl, k), FAMILY, level, task=task, start=start, apps=apps,
                     oracle=oracle, generator=gen, params=params, **spec)


def generate(split: str, rng: random.Random, n: int) -> list[dict]:
    levels = spread(n, [2, 3], [2, 3])
    n_ask = max(2, round(n / 8))
    n2, n3 = levels.count(2), levels.count(3)
    specs = {2: [f(rng) for f in _deal(rng, L2, n2)], 3: []}
    fns3 = _deal(rng, L3, n3 - n_ask) + _deal(rng, ASK, n_ask)
    rng.shuffle(fns3)
    specs[3] = [f(rng) for f in fns3]
    out = []
    for k, spec in enumerate(specs[2] + specs[3], start=1):
        out.append(_case(spec, split, k))
    return out
