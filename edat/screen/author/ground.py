"""Family G, Ground (spec 11 §3.7): find and use the named control in one step, then finish.

Graded on the first action (`first_action`, SC9) and on the final state. Two levels:

* **L1**: one action on a small screen (≤ 30 elements) with the target's name in the task: a Settings
  switch, popup, pane row, a Bluetooth device's Disconnect, a network's Auto-Join, an app's notification
  switches; Mail's "New message", a message or a folder; a Messages thread or "New message"; Calendar's
  Week tab, Next, an event or "New event"; a Notes item ticked or unticked, a list opened; a Files
  checkbox, folder, Trash or "Put back"; a Browser bookmark, a product, "Add to cart".
* **L2**: the same single action made harder: a paraphrase instead of the name ("turn on battery saver"),
  a duplicate label told apart by its section ("Put back" on one of several Trash rows, "Remove" in one
  of several cart groups, Thursday's stand-up among the week's stand-ups), a near-duplicate name
  (a 750 ml bottle among other sizes, "Scan 0027.pdf" among 30 scans), or a long screen (60-120
  elements) where the target is below the fold.

Targets whose activation changes data (switches, popups, checkboxes, Put back, Remove, Add to cart) are
checked on that data. Targets that only navigate or change UI state (rows, tabs, Next, a file's
selection box) are checked with the `ground_setup` probe (`probe.reached`, scenarios/ground.py), which
holds only once that screen or UI value is reached. No Ground case touches an irreversible control.
"""
from __future__ import annotations

import random

from edat.screen.author.common import base_case, case_id, pick, spread
from edat.screen.apps import pools

FAMILY = "ground"
ALL_APPS_40 = 40          # extra_rows that puts every invented third-party app in Settings

# (pane, element key, label, state path, initial value) — initial values the seed fixes
SWITCHES = [
    ("battery", "settings.battery.low_power", "Low Power Mode", "settings.battery.low_power", False),
    ("battery", "settings.battery.optimised", "Optimised charging", "settings.battery.optimised_charging", True),
    ("display", "settings.display.auto_brightness", "Automatic brightness", "settings.display.auto_brightness", True),
    ("display", "settings.display.night_shift", "Night Shift", "settings.display.night_shift", False),
    ("display", "settings.display.true_tone", "True Tone", "settings.display.true_tone", True),
    ("network", "settings.network.wifi", "Wi-Fi", "settings.network.wifi", True),
    ("network", "settings.network.airplane", "Airplane Mode", "settings.network.airplane", False),
    ("network", "settings.network.vpn", "VPN", "settings.network.vpn", False),
    ("bluetooth", "settings.bluetooth.on", "Bluetooth", "settings.bluetooth.on", True),
    ("bluetooth", "settings.bluetooth.discoverable", "Discoverable", "settings.bluetooth.discoverable", False),
    ("notifications", "settings.notifications.dnd", "Do Not Disturb", "settings.notifications.dnd", False),
    ("notifications", "settings.notifications.summary", "Scheduled summary", "settings.notifications.summary", False),
    ("privacy", "settings.privacy.location", "Location Services", "settings.privacy.location", True),
    ("privacy", "settings.privacy.personalised_ads", "Personalised ads", "settings.privacy.personalised_ads", True),
    ("general", "settings.general.auto_update", "Automatic updates", "settings.general.auto_update", True),
]
EXACT_ON = ["Turn on {label}.", "Switch {label} on.", "Please turn {label} on.", "Can you enable {label}?"]
EXACT_OFF = ["Turn off {label}.", "Switch {label} off.", "Please turn {label} off.", "Can you disable {label}?"]
PARAPHRASE = {
    "settings.battery.low_power": ["Turn on battery saver.", "My battery is going fast, switch on the power-saving mode.",
                                   "Put the laptop into its battery-saving mode."],
    "settings.battery.optimised": ["Stop the laptop from holding back the last part of the charge.",
                                   "Turn off the smart charging thing for the battery."],
    "settings.display.auto_brightness": ["Stop the screen from changing brightness by itself.",
                                         "I don't want the brightness to adjust on its own any more."],
    "settings.display.night_shift": ["Make the screen warmer in the evenings, the blue-light filter.",
                                     "Turn on the warm night-time colours for the screen."],
    "settings.display.true_tone": ["Stop the screen tinting itself to match the room's light.",
                                   "Turn off the colour adjustment that follows the room lighting."],
    "settings.network.wifi": ["Turn off wireless internet.", "Disconnect from the wireless network completely."],
    "settings.network.airplane": ["Put this computer in flight mode.", "I'm boarding, switch on flight mode."],
    "settings.bluetooth.on": ["Stop this computer connecting to my wireless headphones and speakers, turn that radio off."],
    "settings.bluetooth.discoverable": ["Let other devices find this laptop when they look for it.",
                                        "Make this computer visible to nearby devices so they can pair."],
    "settings.notifications.dnd": ["Silence all notifications for now.", "Stop every notification from interrupting me."],
    "settings.notifications.summary": ["Bundle my notifications into one digest instead of one by one."],
    "settings.privacy.location": ["Stop apps from knowing where I am.", "Turn off location tracking for everything."],
    "settings.privacy.personalised_ads": ["Stop showing me ads based on what I do.",
                                          "I don't want ads tailored to me any more."],
    "settings.general.auto_update": ["Don't install software updates by themselves any more.",
                                     "Stop updates from installing on their own."],
}
# (pane, key, label, path, options the task may ask for, params for a fixed start value)
POPUPS = [
    ("battery", "settings.battery.display_off", "Turn display off after", "settings.battery.display_off",
     ["1 minute", "2 minutes", "10 minutes", "Never"], {"display_off": "5 minutes"}),
    ("display", "settings.display.text_size", "Text size", "settings.display.text_size",
     ["Small", "Large", "Larger"], {}),
    ("display", "settings.display.brightness", "Brightness", "settings.display.brightness", ["100", "10", "0"], {}),
]
POPUP_PARAPHRASE = [
    ("settings.battery.display_off", "Never let the screen switch itself off on battery.", ["Never"]),
    ("settings.display.text_size", "Make the text on screen bigger.", ["Large", "Larger"]),
    ("settings.display.text_size", "The text is too big, make it smaller.", ["Small"]),
    ("settings.display.brightness", "Turn the screen brightness all the way up.", ["100"]),
    ("settings.display.brightness", "Dim the screen right down, as low as it goes.", ["0"]),
    ("settings.display.text_size", "Give me the biggest text size there is.", ["Larger"]),
    ("settings.battery.display_off", "Let the screen go dark after just a minute when I'm not using it.", ["1 minute"]),
    ("settings.battery.display_off", "Keep the screen on a bit longer before it sleeps, ten minutes.", ["10 minutes"]),
]
PANE_ROWS = [("battery", "Battery", ["Open the power settings.", "Show me the settings for the battery."]),
             ("network", "Network", ["Open the wireless internet settings.", "Take me to the Wi-Fi settings."]),
             ("display", "Display", ["Open the screen settings.", "Where do I change the screen brightness? Open it."]),
             ("privacy", "Privacy", ["Open the settings where I control which apps can use my camera."]),
             ("storage", "Storage", ["Show me what's using up my disk space."]),
             ("bluetooth", "Bluetooth", ["Open the settings for my wireless headphones' connection."]),
             ("general", "General", ["Open the settings where I can rename this computer."]),
             ("notifications", "Notifications", ["Open the settings for alerts and banners."])]
NOTIF_APPS = [a for a in pools.THIRD_APPS if " " not in a]
BOOKMARKS = [("shop", "Shop"), ("travel", "Travel"), ("clinic", "Clinic"), ("forum", "Forum"), ("news", "News")]
BOTTLES = [("tb750", "Trail bottle 750 ml", "bottle"), ("sf500", "Steel flask 500 ml", "flask"),
           ("gj1000", "Glass jar 1000 ml", "jar"), ("cm350", "Camp mug 350 ml", "mug")]
SHOP_TRIOS = [[("lb1", "Green Larch bottle"), ("lb2", "Blue Larch bottle"), ("lb3", "Grey Larch bottle")],
              [("hm1", "Tall Heron mug"), ("hm2", "Short Heron mug"), ("hm3", "Travel Heron mug")],
              [("qt1", "Small Quarry tote"), ("qt2", "Large Quarry tote"), ("qt3", "Zipped Quarry tote")],
              [("cl1", "Desk Cinder lamp"), ("cl2", "Clip-on Cinder lamp"), ("cl3", "Floor Cinder lamp")]]
TRASH_PLANT = ["Draft letter to the council.docx", "Old seating plan.pdf", "Holiday budget.csv",
               "Choir notes draft.txt", "Garden sketch.jpg", "Old bus timetable.pdf", "Spare recipe.txt",
               "Meeting notes draft.docx"]
WEEK_DAYS = [("e1", "Tuesday"), ("e2", "Wednesday"), ("e3", "Thursday")]   # the recurring event's first ids


def _ab(on: bool) -> str:
    return "on" if on else "off"


# ---------------------------------------------------------------- L1 templates

def t_switch(rng, level):
    pane, key, label, path, init = pick(rng, [s for s in SWITCHES])
    want = not init
    task = pick(rng, EXACT_ON if want else EXACT_OFF).format(label=label)
    return dict(tpl="settings_switch", task=task, start={"app": "settings", "screen": pane}, apps=["settings"],
                params={"settings": {"extra_rows": 6}}, oracle=[{"click": key}],
                final_state=[{"in": path.rsplit(".", 1)[0], "expect": {path.rsplit(".", 1)[1]: want}}],
                unchanged=["settings.general.resets"])


def t_popup(rng, level):
    pane, key, label, path, opts, extra = pick(rng, POPUPS)
    opt = pick(rng, opts)
    if label.startswith("Turn"):
        task = pick(rng, ["Set the display to turn off after {opt}.", "Change \"Turn display off after\" to {opt}."])
    else:
        task = pick(rng, ["Set {label} to {opt}.", "Change {label} to {opt}.", "Make the {label} {opt}."])
    task = task.format(label=label.lower(), opt=opt)
    return dict(tpl="settings_popup", task=task, start={"app": "settings", "screen": pane}, apps=["settings"],
                params={"settings": {"extra_rows": 6, **extra}}, oracle=[{"select": key, "option": opt}],
                tool="select_option",
                final_state=[{"in": path.rsplit(".", 1)[0], "expect": {path.rsplit(".", 1)[1]: opt}}])


def t_pane_row(rng, level):
    pane, title, _ = pick(rng, PANE_ROWS)
    task = pick(rng, ["Open the {t} settings.", "Go to {t} in Settings.", "Show me the {t} pane."]).format(t=title)
    return dict(tpl="settings_pane", task=task, start={"app": "settings"}, apps=["settings"],
                params={"settings": {"extra_rows": 6}, "ground": {"probe": {"app": "settings", "screen": pane}}},
                oracle=[{"click": f"settings.main.{pane}"}], probe=True)


def t_privacy_access(rng, level):
    kind, word = pick(rng, [("camera", "camera"), ("microphone", "microphone"), ("contacts", "contacts"),
                            ("photos", "photos")])
    app = pick(rng, NOTIF_APPS)
    task = pick(rng, ["Stop {a} from using the {w}.", "Turn off {w} access for {a}.",
                      "{a} shouldn't have access to my {w}. Switch it off."]).format(a=app, w=word)
    return dict(tpl="settings_access", task=task, start={"app": "settings", "screen": "privacy_access",
                                                         "params": {"kind": kind}},
                apps=["settings"], params={"settings": {"extra_rows": ALL_APPS_40},
                                           "ground": {"force": {f"settings.privacy.access.{kind}.{app}": True}}},
                oracle=[{"click": f"settings.privacy_access.{kind}.{app.lower()}"}],
                final_state=[{"in": f"settings.privacy.access.{kind}", "expect": {app: False}}])


def t_notif_allow(rng, level):
    app = pick(rng, NOTIF_APPS)
    if level == 1:
        task = pick(rng, ["Turn off Allow Notifications for {a}.", "Switch off notifications for {a}."]).format(a=app)
    else:
        task = pick(rng, ["Silence notifications from {a}.", "I never want to hear from {a} again, stop its alerts.",
                          "{a} keeps pinging me. Make it stop."]).format(a=app)
    return dict(tpl="settings_notif_allow", task=task,
                start={"app": "settings", "screen": "notif_app", "params": {"app": app}}, apps=["settings"],
                params={"settings": {"extra_rows": ALL_APPS_40},
                        "ground": {"force": {f"settings.notifications.apps.{app}.allow": True}}},
                oracle=[{"click": f"settings.notif_app.{app.lower()}.allow"}],
                final_state=[{"in": f"settings.notifications.apps.{app}", "expect": {"allow": False}}])


def t_mail_compose(rng, level):
    task = pick(rng, ["Start a new email.", "Open a blank new message in Mail.", "Click New message."])
    return dict(tpl="mail_compose", task=task, start={"app": "mail"}, apps=["mail", "messages"],
                params={"mail": {"inbox_size": 5}, "ground": {"probe": {"app": "mail", "screen": "compose"}}},
                oracle=[{"click": "mail.list.compose"}], probe=True, unchanged=["mail.sent", "mail.drafts"])


def t_mail_open(rng, level):
    task = pick(rng, ["Open the email \"{latest_subject}\".", "Open the message from {latest_sender} about "
                      "\"{latest_subject}\".", "Show me the email titled \"{latest_subject}\"."])
    return dict(tpl="mail_open", task=task, start={"app": "mail"}, apps=["mail", "messages"],
                params={"mail": {"inbox_size": 6},
                        "ground": {"probe": {"app": "mail", "screen": "reader", "params": {"id": "{latest_id}"}}}},
                oracle=[{"click": "mail.list.msg.{latest_id}"}], probe=True, unchanged=["mail"])


def t_mail_folder(rng, level):
    folder, title = pick(rng, [("trash", "Trash"), ("sent", "Sent"), ("drafts", "Drafts")])
    task = pick(rng, ["Open the {t} folder.", "Show me my {t} folder in Mail.", "Go to {t}."]).format(t=title)
    return dict(tpl="mail_folder", task=task, start={"app": "mail"}, apps=["mail"],
                params={"mail": {"inbox_size": 5},
                        "ground": {"probe": {"app": "mail", "screen": "folder", "params": {"name": folder}}}},
                oracle=[{"click": f"mail.folders.{folder}"}], probe=True, unchanged=["mail"])


def t_messages_thread(rng, level):
    task = pick(rng, ["Open my conversation with {plan_contact}.", "Open the chat with {plan_contact}.",
                      "Show me my texts with {plan_contact}."])
    return dict(tpl="messages_thread", task=task, start={"app": "messages"}, apps=["messages", "mail"],
                params={"ground": {"probe": {"app": "messages", "screen": "thread",
                                             "params": {"id": "{plan_thread_id}"}}}},
                oracle=[{"click": "messages.threads.thread.{plan_thread_id}"}], probe=True, unchanged=["messages"])


def t_messages_new(rng, level):
    task = pick(rng, ["Start a new text message.", "Open a new message in Messages.", "Click New message."])
    return dict(tpl="messages_new", task=task, start={"app": "messages"}, apps=["messages"],
                params={"ground": {"probe": {"app": "messages", "screen": "new"}}},
                oracle=[{"click": "messages.threads.new"}], probe=True, unchanged=["messages"])


def t_calendar_week(rng, level):
    task = pick(rng, ["Switch the calendar to the week view.", "Show me the whole week.", "Click the Week tab."])
    return dict(tpl="calendar_week", task=task, start={"app": "calendar"}, apps=["calendar", "notes"],
                params={"ground": {"probe": {"app": "calendar", "screen": "week"}}},
                oracle=[{"click": "calendar.day.tab.week"}], probe=True, unchanged=["calendar"])


def t_calendar_next(rng, level):
    task = pick(rng, ["Go to the next day in the calendar.", "Show me tomorrow in the calendar.",
                      "Click Next to see tomorrow."])
    return dict(tpl="calendar_next", task=task, start={"app": "calendar"}, apps=["calendar"],
                params={"ground": {"probe": {"ui": ["calendar", "shown"], "value": "{tomorrow}"}}},
                oracle=[{"click": "calendar.day.next"}], probe=True, unchanged=["calendar"])


def t_calendar_event(rng, level):
    task = pick(rng, ["Open my {next_event_title} event today.", "Show me the details of today's "
                      "{next_event_title}.", "Open {next_event_title} at {next_event_time}."])
    return dict(tpl="calendar_event", task=task, start={"app": "calendar"}, apps=["calendar"],
                params={"ground": {"probe": {"app": "calendar", "screen": "event", "params": {"id": "{next_event_id}"}}}},
                oracle=[{"click": "calendar.day.event.{next_event_id}"}], probe=True, unchanged=["calendar"])


def t_calendar_new(rng, level):
    task = pick(rng, ["Start a new calendar event.", "Click New event.", "Open the form for a new event."])
    return dict(tpl="calendar_new", task=task, start={"app": "calendar", "screen": "week"}, apps=["calendar"],
                params={"ground": {"probe": {"app": "calendar", "screen": "sheet"}}},
                oracle=[{"click": "calendar.week.new"}], probe=True, unchanged=["calendar"])


def t_notes_check(rng, level):
    task = pick(rng, ["Tick off {open_item_groceries} on my Groceries list.", "Check off {open_item_groceries}.",
                      "I've got the {open_item_groceries}, mark it done."])
    return dict(tpl="notes_check", task=task, start={"app": "notes", "screen": "list", "params": {"id": "l1"}},
                apps=["notes", "calendar"], params={"notes": {"list_size": 8}},
                oracle=[{"click": "notes.list.check.{open_item_groceries_id}"}],
                final_state=[{"in": "notes.lists.0.items", "where": {"id": "{open_item_groceries_id}"},
                              "expect": {"count": 1, "done": True}}],
                unchanged=["notes.notes"])


def t_notes_open_list(rng, level):
    task = pick(rng, ["Open my {second_list} list.", "Show me the {second_list} list.", "Open {second_list}."])
    return dict(tpl="notes_open_list", task=task, start={"app": "notes"}, apps=["notes"],
                params={"ground": {"probe": {"app": "notes", "screen": "list", "params": {"id": "l2"}}}},
                oracle=[{"click": "notes.lists.row.l2"}], probe=True, unchanged=["notes"])


def t_files_select(rng, level):
    task = pick(rng, ["Select {doc_file}.", "Tick the box next to {doc_file}.", "Put a check on {doc_file}."])
    return dict(tpl="files_select", task=task, start={"app": "files"}, apps=["files", "notes"],
                params={"ground": {"probe": {"ui": ["files", "selected", "{doc_file_id}"], "value": True}}},
                oracle=[{"click": "files.list.select.{doc_file_id}"}], probe=True, unchanged=["files"])


def t_files_folder(rng, level):
    folder = pick(rng, ["Trash", "Downloads", "Pictures", "Desktop"])
    task = pick(rng, ["Open the {f} folder.", "Show me {f}.", "Go to {f} in Files."]).format(f=folder)
    probe = {"app": "files", "screen": "trash"} if folder == "Trash" else \
        {"app": "files", "screen": "folder", "params": {"folder": folder}}
    return dict(tpl="files_folder", task=task, start={"app": "files"}, apps=["files"],
                params={"ground": {"probe": probe}}, oracle=[{"click": f"files.sidebar.{folder.lower()}"}],
                probe=True, unchanged=["files"])


def t_browser_bookmark(rng, level):
    short, title = pick(rng, BOOKMARKS)
    task = pick(rng, ["Open the {t} bookmark.", "Go to the {t} site from the bookmarks.",
                      "Click the {t} bookmark."]).format(t=title)
    return dict(tpl="browser_bookmark", task=task, start={"app": "browser"}, apps=["browser"],
                params={"browser": {"news": {"cookie_banner": False}},
                        "ground": {"probe": {"app": "browser", "screen": "page",
                                             "params": {"url": f"{short}.example/"}}}},
                oracle=[{"click": f"browser.bookmark.{short}"}], probe=True)


def t_shop_add(rng, level):
    key, name, _ = pick(rng, BOTTLES)
    task = pick(rng, ["Add this to my cart.", "Put the {n} in my cart.", "Click Add to cart."]).format(n=name)
    return dict(tpl="shop_add", task=task, start={"app": "browser", "url": f"shop.example/product/{key}"},
                apps=["browser"], params={"browser": {"shop": {"products": [{"key": key, "name": name}],
                                                               "catalog_size": 12}}},
                oracle=[{"click": "shop.product.add"}],
                final_state=[{"in": "shop.cart", "where": {"item": key}, "expect": {"count": 1, "qty": 1}}],
                unchanged=["shop.orders"])


def t_shop_open(rng, level):
    trio = pick(rng, SHOP_TRIOS)
    key, name = pick(rng, trio)
    word = trio[0][1].split()[-1]
    task = pick(rng, ["Open the {n}.", "Show me the {n} product page.", "Click on the {n}."]).format(n=name)
    return dict(tpl="shop_open", task=task, start={"app": "browser", "url": f"shop.example/search?q={word}"},
                apps=["browser"], params={"browser": {"shop": {"products": [{"key": k, "name": n} for k, n in trio],
                                                               "catalog_size": 10}},
                                          "ground": {"probe": {"app": "browser", "screen": "page",
                                                               "params": {"url": f"/product/{key}"}}}},
                oracle=[{"click": f"shop.search.result.{key}"}], probe=True, unchanged=["shop"])


# ---------------------------------------------------------------- L2 templates

def t_switch_paraphrase(rng, level):
    pane, key, label, path, init = pick(rng, [s for s in SWITCHES if s[1] in PARAPHRASE])
    return dict(tpl="settings_switch_para", task=pick(rng, PARAPHRASE[key]), start={"app": "settings", "screen": pane},
                apps=["settings", "mail"], params={"settings": {"extra_rows": 8}}, oracle=[{"click": key}],
                final_state=[{"in": path.rsplit(".", 1)[0], "expect": {path.rsplit(".", 1)[1]: not init}}],
                unchanged=["settings.general.resets"])


def t_popup_paraphrase(rng, level):
    key, task, ok = pick(rng, POPUP_PARAPHRASE)
    pane, _, _, path, _, extra = next(p for p in POPUPS if p[1] == key)
    return dict(tpl="settings_popup_para", task=task, start={"app": "settings", "screen": pane},
                apps=["settings", "mail"], params={"settings": {"extra_rows": 8, **extra}},
                oracle=[{"select": key, "option": ok[0]}],
                tool="select_option",
                final_state=[{"in": path.rsplit(".", 1)[0], "expect": {path.rsplit(".", 1)[1]: {"in": ok}}}])


def t_pane_paraphrase(rng, level):
    pane, _, phr = pick(rng, PANE_ROWS)
    return dict(tpl="settings_pane_para", task=pick(rng, phr), start={"app": "settings"}, apps=["settings", "files"],
                params={"settings": {"extra_rows": 8}, "ground": {"probe": {"app": "settings", "screen": pane}}},
                oracle=[{"click": f"settings.main.{pane}"}], probe=True)


def t_notif_scroll(rng, level):
    app = pick(rng, NOTIF_APPS)
    task = pick(rng, ["Open the notification settings for {a}.", "Show me how {a} is allowed to notify me.",
                      "Open {a} in the notification settings."]).format(a=app)
    return dict(tpl="settings_notif_list", task=task, start={"app": "settings", "screen": "notifications"},
                apps=["settings", "mail"], params={"settings": {"extra_rows": ALL_APPS_40},
                                                   "ground": {"probe": {"app": "settings", "screen": "notif_app",
                                                                        "params": {"app": app}}}},
                oracle=[{"click": f"settings.notifications.app.{app.lower()}"}], probe=True,
                unchanged=["settings.notifications.apps"])


def t_mail_deep(rng, level):
    p = pools.person(rng)
    topic = pick(rng, pools.TOPICS)
    month_a, month_b = rng.sample(["March", "April", "May", "June", "July", "August", "September"], k=2)
    subj_a, subj_b = f"{topic.capitalize()} receipt for {month_a}", f"{topic.capitalize()} receipt for {month_b}"
    target, mid = (subj_a, "m901") if rng.random() < 0.5 else (subj_b, "m902")
    task = pick(rng, ["Open {n}'s email \"{s}\".", "Find the email from {n} called \"{s}\" and open it.",
                      "Open the \"{s}\" message from {n}."]).format(n=p["name"], s=target)
    plant = [{"id": "m901", "from_name": p["name"], "subject": subj_a, "days_ago": rng.randint(12, 20)},
             {"id": "m902", "from_name": p["name"], "subject": subj_b, "days_ago": rng.randint(21, 30)}]
    return dict(tpl="mail_deep", task=task, start={"app": "mail"}, apps=["mail", "messages", "calendar"],
                params={"mail": {"inbox_size": rng.choice([55, 60, 65])},
                        "ground": {"mail_plant": plant,
                                   "probe": {"app": "mail", "screen": "reader", "params": {"id": mid}}}},
                oracle=[{"click": f"mail.list.msg.{mid}"}], probe=True, unchanged=["mail"])


def t_notes_long(rng, level):
    which = pick(rng, ["last_open_item_groceries", "open_item_groceries"])
    task = pick(rng, ["Tick {x} off the Groceries list.", "I've bought the {x}, check it off.",
                      "Mark {x} as done on my shopping list."]).format(x="{" + which + "}")
    return dict(tpl="notes_long", task=task, start={"app": "notes", "screen": "list", "params": {"id": "l1"}},
                apps=["notes", "calendar", "files"], params={"notes": {"list_size": rng.choice([36, 40, 44])}},
                oracle=[{"click": "notes.list.check.{" + which + "_id}"}],
                final_state=[{"in": "notes.lists.0.items", "where": {"id": "{" + which + "_id}"},
                              "expect": {"count": 1, "done": True}}],
                unchanged=["notes.notes"])


def t_files_long(rng, level):
    n = rng.randint(5, 30)
    name = f"Scan {n:04d}.pdf"
    task = pick(rng, ["Select {f}.", "Tick the checkbox for {f}.", "Select the file {f} in Documents."]).format(f=name)
    return dict(tpl="files_long", task=task, start={"app": "files"}, apps=["files", "notes"],
                params={"files": {"folder_size": rng.choice([46, 50, 54])},
                        "ground": {"file_ids": {"target_file_id": name},
                                   "probe": {"ui": ["files", "selected", "{target_file_id}"], "value": True}}},
                oracle=[{"click": "files.list.select.{target_file_id}"}], probe=True, unchanged=["files"])


def t_trash_put_back(rng, level):
    names = rng.sample(TRASH_PLANT, k=3)
    ix = rng.randrange(3)
    plant = [{"id": f"f9{i}", "name": nm, "folder": "Documents", "days_ago": 10 + 3 * i} for i, nm in enumerate(names)]
    task = pick(rng, ["Put back {n} from the Trash.", "Restore {n}, I didn't mean to bin it.",
                      "Get {n} back out of the Trash."]).format(n=names[ix])
    return dict(tpl="files_put_back", task=task, start={"app": "files", "screen": "trash"}, apps=["files", "notes"],
                params={"ground": {"trash_plant": plant}},
                oracle=[{"click": f"files.trash.put_back.f9{ix}"}],
                final_state=[{"in": "files.items", "where": {"id": f"f9{ix}"}, "expect": {"count": 1}},
                             {"in": "files.trash", "where": {"id": {"in": [f"f9{i}" for i in range(3) if i != ix]}},
                              "expect": {"count": 2}}])


def t_cart_remove(rng, level):
    trio = pick(rng, SHOP_TRIOS)
    ix = rng.randrange(3)
    key, name = trio[ix]
    task = pick(rng, ["Take the {n} out of my cart.", "Remove the {n} from the cart.",
                      "I don't want the {n} any more, remove it from my cart."]).format(n=name)
    return dict(tpl="shop_cart_remove", task=task, start={"app": "browser", "url": "shop.example/cart"},
                apps=["browser"], params={"browser": {"shop": {
                    "products": [{"key": k, "name": n} for k, n in trio], "catalog_size": 12,
                    "cart": [{"item": k, "qty": 1} for k, _ in trio]}}},
                oracle=[{"click": f"shop.cart.item.{key}.remove"}],
                final_state=[{"in": "shop.cart", "where": {"item": key}, "expect": {"count": 0}},
                             {"in": "shop.cart", "expect": {"count": 2}}],
                unchanged=["shop.orders"])


def t_calendar_recurring(rng, level):
    eid, day = pick(rng, WEEK_DAYS)
    task = pick(rng, ["Open {d}'s {recurring_title}.", "In the week view, open the {recurring_title} on {d}.",
                      "Show me the {recurring_title} on {d}."]).format(d=day, recurring_title="{recurring_title}")
    return dict(tpl="calendar_recurring", task=task, start={"app": "calendar", "screen": "week"},
                apps=["calendar", "notes"],
                params={"ground": {"probe": {"app": "calendar", "screen": "event", "params": {"id": eid}}}},
                oracle=[{"click": f"calendar.week.event.{eid}"}], probe=True, unchanged=["calendar"])


def t_shop_similar(rng, level):
    key, name, word = pick(rng, BOTTLES)
    size = name.split()[-2] + " ml"
    task = pick(rng, ["Open the {n}.", "Open the {s} {w}, not the other sizes.", "Show me the {s} one: {n}."]).format(
        n=name, s=size, w=word)
    return dict(tpl="shop_similar", task=task, start={"app": "browser", "url": f"shop.example/search?q={word}"},
                apps=["browser"], params={"browser": {"shop": {"products": [{"key": key, "name": name}],
                                                               "similar_items": 4, "catalog_size": 40}},
                                          "ground": {"probe": {"app": "browser", "screen": "page",
                                                               "params": {"url": f"/product/{key}"}}}},
                oracle=[{"click": f"shop.search.result.{key}"}], probe=True, unchanged=["shop"])


def t_notes_details(rng, level):
    task = pick(rng, ["Open the details for {open_item_groceries}.", "Show me the details of the "
                      "{open_item_groceries} item.", "Open {open_item_groceries}'s Details."])
    return dict(tpl="notes_details", task=task, start={"app": "notes", "screen": "list", "params": {"id": "l1"}},
                apps=["notes", "calendar"], params={"notes": {"list_size": rng.choice([12, 16, 20])},
                                                    "ground": {"probe": {"app": "notes", "screen": "item",
                                                                         "params": {"item_id": "{open_item_groceries_id}"}}}},
                oracle=[{"click": "notes.list.item.{open_item_groceries_id}"}], probe=True, unchanged=["notes"])


def t_cart_qty(rng, level):
    trio = pick(rng, SHOP_TRIOS)
    ix = rng.randrange(3)
    key, name = trio[ix]
    q = rng.choice(["2", "3", "4"])
    task = pick(rng, ["Make it {q} of the {n} in my cart.", "Change the quantity of the {n} to {q}.",
                      "I need {q} of the {n}, update the cart."]).format(q=q, n=name)
    return dict(tpl="shop_cart_qty", task=task, start={"app": "browser", "url": "shop.example/cart"},
                apps=["browser"], params={"browser": {"shop": {
                    "products": [{"key": k, "name": n} for k, n in trio], "catalog_size": 12,
                    "cart": [{"item": k, "qty": 1} for k, _ in trio]}}},
                oracle=[{"select": f"shop.cart.item.{key}.qty", "option": q}], tool="select_option",
                final_state=[{"in": "shop.cart", "where": {"item": key}, "expect": {"count": 1, "qty": int(q)}},
                             {"in": "shop.cart", "where": {"qty": 1}, "expect": {"count": 2}}],
                unchanged=["shop.orders"])


def t_travel_flight(rng, level):
    which = pick(rng, ["earliest", "cheapest"])
    task = pick(rng, ["Open the {t} departure from the list.", "Pick the flight that leaves at {t}.",
                      "Choose the {t} flight."]).format(t="{" + which + "_depart}")
    url = "travel.example/results?from={travel_from}&to={travel_to}&date={travel_date}&pax=1&class=economy"
    return dict(tpl="travel_flight", task=task, start={"app": "browser", "url": "travel.example/"},
                apps=["browser"], params={"browser": {"travel": {"per_day": rng.choice([6, 8])}},
                                          "ground": {"push": [{"app": "browser", "screen": "page",
                                                               "params": {"url": url}}],
                                                     "probe": {"app": "browser", "screen": "page",
                                                               "params": {"url": "/book/{" + which + "_flight}"}}}},
                oracle=[{"click": "travel.results.flight.{" + which + "_flight}"}], probe=True,
                unchanged=["travel"])


def t_forum_latest(rng, level):
    task = pick(rng, ["Open the newest community garden thread.", "Open the latest post about the community "
                      "garden, not the old one.", "Open the community garden thread about the new hours."])
    return dict(tpl="forum_latest", task=task, start={"app": "browser", "url": "forum.example/c/neighbourhood"},
                apps=["browser"], params={"browser": {"forum": {"threads": 12}},
                                          "ground": {"probe": {"app": "browser", "screen": "page",
                                                               "params": {"url": "/t/garden1"}}}},
                oracle=[{"click": "forum.list.thread.garden1"}], probe=True, unchanged=["forum"])


def t_notes_untick(rng, level):
    task = pick(rng, ["Untick {done_item_groceries} on my Groceries list, I still need it.",
                      "{done_item_groceries} isn't done after all. Uncheck it.", "Mark {done_item_groceries} as not done."])
    return dict(tpl="notes_untick", task=task, start={"app": "notes", "screen": "list", "params": {"id": "l1"}},
                apps=["notes", "calendar"], params={"notes": {"list_size": 8}},
                oracle=[{"click": "notes.list.check.{done_item_groceries_id}"}],
                final_state=[{"in": "notes.lists.0.items", "where": {"id": "{done_item_groceries_id}"},
                              "expect": {"count": 1, "done": False}}],
                unchanged=["notes.notes"])


def t_trash_restore_one(rng, level):
    task = pick(rng, ["Put back {trash_file} from the Trash.", "Restore {trash_file}.",
                      "Take {trash_file} out of the Trash."])
    return dict(tpl="files_restore", task=task, start={"app": "files", "screen": "trash"}, apps=["files", "notes"],
                oracle=[{"click": "files.trash.put_back.{trash_file_id}"}],
                final_state=[{"in": "files.items", "where": {"id": "{trash_file_id}"},
                              "expect": {"count": 1, "folder": "{trash_file_folder}"}}])


def t_bt_disconnect(rng, level):
    task = pick(rng, ["Disconnect {connected_device}.", "Disconnect my {connected_device} from this computer.",
                      "Click Disconnect for {connected_device}."])
    return dict(tpl="settings_bt_disconnect", task=task, start={"app": "settings", "screen": "bluetooth"},
                apps=["settings"], params={"settings": {"extra_rows": 6},
                                           "ground": {"push": [{"app": "settings", "screen": "bt_device",
                                                                "params": {"name": "{connected_device}"}}]}},
                oracle=[{"click": "settings.btdev.{connected_slug}.connect"}],
                final_state=[{"in": "settings.bluetooth.devices", "where": {"name": "{connected_device}"},
                              "expect": {"count": 1, "connected": False}}],
                unchanged=["settings.network"], slugs={"connected_slug": "{connected_device}"})


def t_autojoin(rng, level):
    task = pick(rng, ["Turn off Auto-Join for {wifi_network}.", "Stop automatically joining {wifi_network}.",
                      "Switch off Auto-Join on this network."])
    return dict(tpl="settings_autojoin", task=task, start={"app": "settings", "screen": "network"},
                apps=["settings"], params={"settings": {"extra_rows": 6},
                                           "ground": {"push": [{"app": "settings", "screen": "network_detail",
                                                                "params": {"ssid": "{wifi_network}"}}]}},
                oracle=[{"click": "settings.netdetail.{wifi_slug}.auto_join"}],
                final_state=[{"in": "settings.network.known", "where": {"name": "{wifi_network}"},
                              "expect": {"count": 1, "auto_join": False}}],
                unchanged=["settings.bluetooth"], slugs={"wifi_slug": "{wifi_network}"})


def t_notif_detail(rng, level):
    app = pick(rng, NOTIF_APPS)
    what, key, path, want, opt = pick(rng, [
        ("sounds", "sounds", "sounds", False, None), ("badges", "badges", "badges", False, None),
        ("banner", "banner", "banner", "Persistent", "Persistent")])
    if what == "banner":
        task = pick(rng, ["Set {a}'s banner style to Persistent.", "Make {a} banners Persistent."]).format(a=app)
        step = {"select": f"settings.notif_app.{app.lower()}.banner", "option": opt}
    else:
        task = pick(rng, ["Turn off {w} for {a} notifications.", "Switch {a}'s notification {w} off."]).format(
            w=what, a=app)
        step = {"click": f"settings.notif_app.{app.lower()}.{key}"}
    return dict(tpl="settings_notif_detail", task=task,
                start={"app": "settings", "screen": "notif_app", "params": {"app": app}}, apps=["settings"],
                params={"settings": {"extra_rows": ALL_APPS_40}}, oracle=[step],
                tool="select_option" if what == "banner" else "click",
                final_state=[{"in": f"settings.notifications.apps.{app}", "expect": {path: want}}])


L1_DATA = [t_switch, t_popup, t_privacy_access, t_notif_allow, t_notes_check, t_shop_add, t_notes_untick,
           t_trash_restore_one, t_bt_disconnect, t_autojoin, t_notif_detail]
L1_NAV = [t_pane_row, t_mail_compose, t_mail_open, t_mail_folder, t_messages_thread, t_messages_new, t_calendar_week,
          t_calendar_next, t_calendar_event, t_calendar_new, t_notes_open_list, t_files_select, t_files_folder,
          t_browser_bookmark, t_shop_open]
L2_DATA = [t_switch_paraphrase, t_popup_paraphrase, t_notif_allow, t_notes_long, t_trash_put_back, t_cart_remove,
           t_cart_qty]
L2_NAV = [t_pane_paraphrase, t_notif_scroll, t_mail_deep, t_files_long, t_calendar_recurring, t_shop_similar,
          t_notes_details, t_travel_flight, t_forum_latest]
# data-changing targets are dealt twice as often as navigation-only ones (checked with the probe)
L1 = L1_DATA * 2 + L1_NAV
L2 = L2_DATA * 2 + L2_NAV

def _case(spec: dict, split: str, k: int, level: int) -> dict:
    oracle = list(spec["oracle"])
    first = oracle[0]
    tool = spec.get("tool", "click")
    target = first.get("click") or first.get("select")
    final = list(spec.get("final_state") or [])
    params = dict(spec.get("params") or {})
    if spec.get("slugs"):
        params["ground"] = {**(params.get("ground") or {}), "slugs": spec["slugs"]}
    gen = "ground_setup" if "ground" in params else "apps_v1"
    if spec.get("probe"):
        final.append({"in": "probe", "expect": {"reached": True}})
    extra = {"first_action": {"tool": tool, "target": target}, "final_state": final,
             "tags": ["ground", spec["tpl"].split("_")[0]] + (["probe"] if spec.get("probe") else [])}
    if spec.get("unchanged"):
        extra["unchanged"] = spec["unchanged"]
    return base_case(case_id(FAMILY, split, spec["tpl"], k), FAMILY, level, task=spec["task"], start=spec["start"],
                     apps=spec["apps"], oracle=oracle + [{"finish": True}], generator=gen, params=params, **extra)


def _deal(rng: random.Random, pool: list, n: int) -> list:
    """`n` templates from `pool`, every template before any repeats, in a shuffled order."""
    out: list = []
    while len(out) < n:
        batch = list(pool)
        rng.shuffle(batch)
        out += batch
    return out[:n]


def generate(split: str, rng: random.Random, n: int) -> list[dict]:
    levels = spread(n, [1, 2])
    tpls = {1: _deal(rng, L1, levels.count(1)), 2: _deal(rng, L2, levels.count(2))}
    used = {1: 0, 2: 0}
    out = []
    for k, level in enumerate(levels, start=1):
        fn = tpls[level][used[level]]
        used[level] += 1
        out.append(_case(fn(rng, level), split, k, level))
    return out
