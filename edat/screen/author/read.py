"""Family R, Read (spec 11 §3.7): read state and answer in `finish(answer=...)`; nothing may change.

Every case has `unchanged: all` and an `answer` block checked with the normalisers in
`grade/normalise.py` (`contains_all` over `{slots}` the apps fill from the world seed, so a guessed value
fails). Levels:

* **L1**: the value is on the start screen: battery level, Wi-Fi network, the connected Bluetooth
  device, the device name, unread mail, the newest sender, the next appointment, ticked-off items, a
  file count, a price, the clinic's first Thursday slot, the top news story.
* **L2**: one navigation or a comparison on one screen: the app using most battery, the largest Storage
  category, an invitation's time in Mail, a time or place in a Messages thread, tomorrow's first event,
  the cheaper of two products, the cheapest flight's departure, the community garden's hours, the
  update version, how many items are still open in a list.
* **L3**: two or three screens: from Settings' main list to Battery Usage or Software Update, from
  another Mail folder to the invitation's address, Notes' House note (bin day, spare key), the biggest
  file across four folders, the forum from its home page, a shop search, a flight search.

About 10% ask for something no screen shows (a charge-cycle count, appointment length, an event's
price, the Trash's size, the Wi-Fi password, a flight number): the right answer says it is not shown
(`answer.abstain`), and a tempting value that is on screen (`not_contains`) fails it.
"""
from __future__ import annotations

import random

from edat.screen.author.common import base_case, case_id, pick, spread

FAMILY = "read"
NOT_SHOWN = ["That isn't shown on the screen.", "The screen doesn't show that.", "That information is not shown."]
PAIRS = [[("ft1", "Small Fern tea tin"), ("ft2", "Large Fern tea tin")],
         [("hb1", "Front Heron bike light"), ("hb2", "Rear Heron bike light")],
         [("ym1", "Thin Meadow yoga mat"), ("ym2", "Thick Meadow yoga mat")],
         [("ub1", "Compact Pebble umbrella"), ("ub2", "Golf Pebble umbrella")],
         [("lb1", "Single Slate lunch box"), ("lb2", "Double Slate lunch box")]]
PRODUCTS = [("tb750", "Trail bottle 750 ml", "bottle"), ("sf500", "Steel flask 500 ml", "flask"),
            ("gj1000", "Glass jar 1000 ml", "jar"), ("cm350", "Camp mug 350 ml", "mug")]


def T(tpl, task, start, apps, oracle, finish, answer, params=None, setup=None):
    return dict(tpl=tpl, task=task, start=start, apps=apps, oracle=oracle, finish=finish, answer=answer,
                params=params or {}, setup=setup)


def A(kind: str, *want: str) -> dict:
    return {"contains_all": list(want), "normalise": kind}


def abstain(kind: str, *tempting: str) -> dict:
    return {"abstain": True, "not_contains": list(tempting), "normalise": kind}


SETTINGS = {"settings": {"extra_rows": 8}}
TRAVEL_RESULTS = "travel.example/results?from={travel_from}&to={travel_to}&date={travel_date}&pax=1&class=economy"


# ---------------------------------------------------------------- L1

def r_battery_level(rng):
    return T("settings_battery_level", pick(rng, ["How much battery have I got left?", "What's my battery level?",
                                                  "What percentage is the battery at?"]),
             {"app": "settings", "screen": "battery"}, ["settings"], [], "{battery_level}%", A("number", "{battery_level}"),
             SETTINGS)


def r_wifi(rng):
    return T("settings_wifi", pick(rng, ["Which Wi-Fi network am I connected to?", "What's the name of the network "
                                         "I'm on?", "What Wi-Fi am I using right now?"]),
             {"app": "settings", "screen": "network"}, ["settings"], [], "{wifi_network}", A("text", "{wifi_network}"),
             SETTINGS)


def r_bt_device(rng):
    return T("settings_bt_device", pick(rng, ["Which Bluetooth device is connected right now?",
                                              "What's currently connected over Bluetooth?"]),
             {"app": "settings", "screen": "bluetooth"}, ["settings"], [], "{connected_device}",
             A("text", "{connected_device}"), SETTINGS)


def r_device_name(rng):
    return T("settings_device_name", pick(rng, ["What's this computer called?", "What name does this laptop have "
                                                "in General settings?"]),
             {"app": "settings", "screen": "general"}, ["settings"], [], "{device_name}", A("text", "{device_name}"),
             SETTINGS)


def r_unread(rng):
    return T("mail_unread", pick(rng, ["How many unread emails do I have?", "How many emails haven't I read yet?"]),
             {"app": "mail"}, ["mail", "messages"], [], "{unread_count}", A("number", "{unread_count}"),
             {"mail": {"inbox_size": rng.choice([6, 8, 10])}})


def r_latest_sender(rng):
    return T("mail_latest_sender", pick(rng, ["Who sent me my most recent email?", "Who's the newest email from?"]),
             {"app": "mail"}, ["mail", "messages"], [], "{latest_sender}", A("text", "{latest_sender}"),
             {"mail": {"inbox_size": rng.choice([6, 8, 10])}})


def r_next_event(rng):
    return T("calendar_next_time", pick(rng, ["What time is my next appointment today?",
                                              "When's the next thing on my calendar today?"]),
             {"app": "calendar"}, ["calendar", "notes"], [], "{next_event_time}", A("time", "{next_event_time}"))


def r_done_groceries(rng):
    return T("notes_done_count", pick(rng, ["How many things on my Groceries list are ticked off already?",
                                            "How many groceries have I already checked off?"]),
             {"app": "notes"}, ["notes", "calendar"], [], "{done_count_groceries}",
             A("number", "{done_count_groceries}"))


def r_doc_count(rng):
    return T("files_doc_count", pick(rng, ["How many files are in my Documents folder?",
                                           "How many documents do I have in Documents?"]),
             {"app": "files"}, ["files", "notes"], [], "{file_count_documents}",
             A("number", "{file_count_documents}"))


def r_price(rng):
    key, name, _ = pick(rng, PRODUCTS)
    return T("shop_price", pick(rng, ["How much does this cost?", "What's the price of the {n}?"]).format(n=name),
             {"app": "browser", "url": f"shop.example/product/{key}"}, ["browser"], [], "${price_%s}" % key,
             A("money", "{price_%s}" % key), {"browser": {"shop": {"products": [{"key": key, "name": name}],
                                                                   "catalog_size": 12}}})


def r_clinic_slot(rng):
    return T("clinic_first_slot", pick(rng, ["What's the earliest appointment on Thursday morning?",
                                             "When is the first free slot on Thursday morning?"]),
             {"app": "browser", "url": "clinic.example/"}, ["browser"], [], "{thursday_morning_first}",
             A("time", "{thursday_morning_first}"))


def r_headline(rng):
    return T("news_headline", pick(rng, ["What's the top story on the news site right now?",
                                         "What's the first headline on the news page?"]),
             {"app": "browser", "url": "news.example/"}, ["browser"], [], "{headline}", A("text", "{headline}"),
             {"browser": {"news": {"cookie_banner": False}}})


def r_abs_cycles(rng):
    return T("abstain_battery_cycles", pick(rng, ["How many charge cycles has my battery been through?",
                                                  "What's the battery's cycle count?"]),
             {"app": "settings", "screen": "battery"}, ["settings"], [], pick(rng, NOT_SHOWN),
             abstain("number", "{battery_level}"), SETTINGS)


def r_abs_clinic(rng):
    return T("abstain_clinic_length", pick(rng, ["How long is each appointment at the clinic?",
                                                 "How many minutes does a clinic appointment last?"]),
             {"app": "browser", "url": "clinic.example/"}, ["browser"], [], pick(rng, NOT_SHOWN),
             abstain("time", "{thursday_morning_first}"))


def r_abs_wifi_password(rng):
    return T("abstain_wifi_password", pick(rng, ["What's the Wi-Fi password?", "Tell me the password for my Wi-Fi."]),
             {"app": "settings", "screen": "network"}, ["settings"], [], pick(rng, NOT_SHOWN),
             abstain("text", "{wifi_network}"), SETTINGS)


# ---------------------------------------------------------------- L2

def r_top_battery(rng):
    return T("settings_top_battery", pick(rng, ["Which app has used the most battery?",
                                                "What's draining my battery the most?"]),
             {"app": "settings", "screen": "battery"}, ["settings", "mail"], [{"click": "settings.battery.details"}],
             "{top_battery_app}", A("text", "{top_battery_app}"), SETTINGS)


def r_battery_pair(rng):
    return T("settings_battery_pair", "Which has used more battery, {bat_a} or {bat_b}? Just tell me the app's name.",
             {"app": "settings", "screen": "battery"}, ["settings", "mail"], [{"click": "settings.battery.details"}],
             "{bat_more}", {"contains_all": ["{bat_more}"], "not_contains": ["{bat_less}"], "normalise": "text"},
             {**SETTINGS, "read": {"battery_pair": True}}, setup=True)


def r_largest_category(rng):
    return T("settings_largest_category", pick(rng, ["What's taking up the most space on my disk?",
                                                     "Which kind of data uses the most storage?"]),
             {"app": "settings", "screen": "storage"}, ["settings", "files"], [], "{largest_category}",
             A("text", "{largest_category}"), {**SETTINGS, "read": {"unique_largest": True}}, setup=True)


def r_invite_time(rng):
    return T("mail_invite_time", pick(rng, ["What time is the {invite_topic} on {invite_weekday}?",
                                            "There's an email about the {invite_topic}. What time does it start?"]),
             {"app": "mail"}, ["mail", "messages", "calendar"], [{"click": "mail.list.msg.{invite_id}"}],
             "{invite_time}", A("time", "{invite_time}"), {"mail": {"inbox_size": rng.choice([8, 12, 16])}})


def r_plan_time(rng):
    return T("messages_plan_time", pick(rng, ["What time did {plan_contact} say to meet?",
                                              "When am I meeting {plan_contact}? Check our texts."]),
             {"app": "messages"}, ["messages", "mail"], [{"click": "messages.threads.thread.{plan_thread_id}"}],
             "{plan_time}", A("number", "{plan_time}"))


def r_plan_place(rng):
    return T("messages_plan_place", pick(rng, ["Where did {plan_contact} say to meet?",
                                               "Which street am I meeting {plan_contact} on?"]),
             {"app": "messages"}, ["messages", "mail"], [{"click": "messages.threads.thread.{plan_thread_id}"}],
             "{plan_place}", A("text", "{plan_place}"))


def r_tomorrow_first(rng):
    return T("calendar_tomorrow_first", pick(rng, ["What's the first thing on my calendar tomorrow?",
                                                   "What have I got first thing tomorrow?"]),
             {"app": "calendar"}, ["calendar", "notes"], [{"click": "calendar.day.next"}], "{tomorrow_first_title}",
             A("text", "{tomorrow_first_title}"))


def r_tomorrow_count(rng):
    return T("calendar_tomorrow_count", pick(rng, ["How many things do I have on tomorrow?",
                                                   "How many events are on my calendar for tomorrow?"]),
             {"app": "calendar"}, ["calendar", "notes"], [{"click": "calendar.day.next"}], "{events_tomorrow_count}",
             A("number", "{events_tomorrow_count}"))


def r_cheaper(rng):
    pair = pick(rng, PAIRS)
    word = pair[0][1].split()[-1]
    return T("shop_cheaper", "Which is cheaper, the {a} or the {b}? Just give me the name.".format(
        a=pair[0][1], b=pair[1][1]) if rng.random() < 0.5 else
        "Which costs less: {b} or {a}? Just the name, please.".format(a=pair[0][1], b=pair[1][1]),
        {"app": "browser", "url": f"shop.example/search?q={word}"}, ["browser"], [], "{cheapest_name}",
        {"contains_all": ["{cheapest_name}"], "not_contains": ["{dearest_name}"], "normalise": "text"},
        {"browser": {"shop": {"products": [{"key": k, "name": n} for k, n in pair], "catalog_size": 16}}})


def r_flight_cheapest(rng):
    return T("travel_cheapest_depart", pick(rng, ["What time does the cheapest flight on this list leave?",
                                                  "When does the cheapest of these flights depart?"]),
             {"app": "browser", "url": "travel.example/"}, ["browser"], [], "{cheapest_depart}",
             A("time", "{cheapest_depart}"),
             {"browser": {"travel": {"per_day": rng.choice([5, 6, 7])}},
              "read": {"push": [{"app": "browser", "screen": "page", "params": {"url": TRAVEL_RESULTS}}]}}, setup=True)


def r_garden_hours(rng):
    return T("forum_garden_hours", pick(rng, ["Find the latest post about the community garden and tell me its "
                                              "opening hours.", "What are the community garden's new opening hours?"]),
             {"app": "browser", "url": "forum.example/c/neighbourhood"}, ["browser"],
             [{"click": "forum.list.thread.garden1"}], "{open_time_12h} to {close_time_12h}",
             A("time", "{open_time}", "{close_time}"), {"browser": {"forum": {"threads": 12}}})


def r_update_version(rng):
    return T("settings_update_version", pick(rng, ["Which software version is waiting to be installed?",
                                                   "What version is the available update?"]),
             {"app": "settings", "screen": "general"}, ["settings"], [{"click": "settings.general.update"}],
             "{update_version}", A("text", "{update_version}"), SETTINGS)


def r_second_open(rng):
    return T("notes_open_count", pick(rng, ["How many things are still not ticked off on my {second_list} list?",
                                            "How many items are left to do on {second_list}?"]),
             {"app": "notes"}, ["notes", "calendar"], [{"click": "notes.lists.row.l2"}], "{second_open_count}",
             A("number", "{second_open_count}"), {"read": {"second_open": True}}, setup=True)


def r_statement_sender(rng):
    return T("mail_statement_sender", pick(rng, ["Which company sent me a statement email?",
                                                 "Who is my monthly statement from?"]),
             {"app": "mail"}, ["mail", "messages"], [{"click": "mail.list.msg.{account_msg_id}"}], "{account_sender}",
             A("text", "{account_sender}"), {"mail": {"inbox_size": rng.choice([20, 30, 40])}})


def r_abs_invite_cost(rng):
    return T("abstain_invite_cost", pick(rng, ["How much does the {invite_topic} cost?",
                                               "What's the entry fee for the {invite_topic}?"]),
             {"app": "mail"}, ["mail", "messages"], [{"click": "mail.list.msg.{invite_id}"}], pick(rng, NOT_SHOWN),
             abstain("time", "{invite_time}"), {"mail": {"inbox_size": 10}})


def r_abs_trash_size(rng):
    return T("abstain_trash_size", pick(rng, ["How much space is the Trash using?", "How many GB is in the Trash?"]),
             {"app": "settings", "screen": "storage"}, ["settings", "files"], [], pick(rng, NOT_SHOWN),
             abstain("number", "{storage_used}"), SETTINGS)


def r_abs_flight_number(rng):
    return T("abstain_flight_number", pick(rng, ["What's the flight number of the cheapest flight here?",
                                                 "Which flight number is the cheapest one?"]),
             {"app": "browser", "url": "travel.example/"}, ["browser"], [], pick(rng, NOT_SHOWN),
             abstain("time", "{cheapest_depart}"),
             {"read": {"push": [{"app": "browser", "screen": "page", "params": {"url": TRAVEL_RESULTS}}]}}, setup=True)


# ---------------------------------------------------------------- L3

def r_top_battery_main(rng):
    return T("settings_top_battery_deep", pick(rng, ["Which app has been using the most battery?",
                                                     "Find out which app drains my battery most."]),
             {"app": "settings"}, ["settings", "mail", "files"],
             [{"click": "settings.main.battery"}, {"click": "settings.battery.details"}],
             "{top_battery_app}", A("text", "{top_battery_app}"), SETTINGS)


def r_version_main(rng):
    return T("settings_update_version_deep", pick(rng, ["Is there a software update? Which version?",
                                                        "Check which software update version is available."]),
             {"app": "settings"}, ["settings", "mail"], [{"click": "settings.main.general"},
                                                         {"click": "settings.general.update"}],
             "{update_version}", A("text", "{update_version}"), SETTINGS)


def r_invite_place(rng):
    return T("mail_invite_place", pick(rng, ["Where is the {invite_topic} being held? I need the street address.",
                                             "What's the address for the {invite_topic} on {invite_weekday}?"]),
             {"app": "mail", "screen": "folder", "params": {"name": "sent"}}, ["mail", "messages", "calendar"],
             [{"click": "mail.folders.inbox"}, {"click": "mail.list.msg.{invite_id}"}], "{invite_place}",
             A("text", "{invite_line1}"), {"mail": {"inbox_size": rng.choice([16, 24])}, "read": {"invite_line1": True}},
             setup=True)


def r_bin_day(rng):
    return T("notes_bin_day", pick(rng, ["Which day is bin collection? It's in my House note.",
                                         "When do the bins go out? Check my notes."]),
             {"app": "notes"}, ["notes", "calendar", "files"],
             [{"click": "notes.lists.notes"}, {"click": "notes.notes.row.n1"}], "{bin_day}", A("text", "{bin_day}"))


def r_key_holder(rng):
    return T("notes_key_holder", pick(rng, ["Who has the spare key? I wrote it in a note.",
                                            "Check my House note: who's holding the spare key?"]),
             {"app": "notes"}, ["notes", "calendar", "files"],
             [{"click": "notes.lists.notes"}, {"click": "notes.notes.row.n1"}], "{key_holder}",
             A("text", "{key_holder}"))


def r_largest_file(rng):
    return T("files_largest", pick(rng, ["What's the biggest file I have? Check every folder.",
                                         "Which of my files takes up the most space?"]),
             {"app": "files"}, ["files", "notes"],
             [{"click": "files.sidebar.desktop"}, {"click": "files.sidebar.downloads"},
              {"click": "files.sidebar.pictures"}], "{largest_file}", A("text", "{largest_file}"))


def r_garden_home(rng):
    return T("forum_garden_deep", pick(rng, ["On the community forum, find the newest post about the community "
                                             "garden. What are its opening hours?",
                                             "When does the community garden open and close now? It's on the forum."]),
             {"app": "browser", "url": "forum.example/"}, ["browser"],
             [{"click": "forum.home.cat.neighbourhood"}, {"click": "forum.list.thread.garden1"}],
             "{open_time} to {close_time}", A("time", "{open_time}", "{close_time}"))


def r_shop_search(rng):
    key, name, word = pick(rng, PRODUCTS)
    return T("shop_search_price", pick(rng, ["How much is the {n} on the shop site?",
                                             "Look up the price of the {n} in the shop."]).format(n=name),
             {"app": "browser", "url": "shop.example/"}, ["browser"],
             [{"type": "shop.search.box", "text": word, "submit": True}, {"click": f"shop.search.result.{key}"}],
             "${price_%s}" % key, A("money", "{price_%s}" % key),
             {"browser": {"shop": {"products": [{"key": key, "name": name}], "similar_items": 2,
                                   "catalog_size": 20}}})


def r_flight_search(rng):
    return T("travel_earliest_price", pick(rng, ["How much is the earliest flight from {travel_from} to {travel_to} "
                                                 "tomorrow?", "What does the first flight tomorrow from {travel_from} "
                                                 "to {travel_to} cost?"]),
             {"app": "browser", "url": "travel.example/"}, ["browser"],
             [{"type": "travel.search.from", "text": "{travel_from}"}, {"type": "travel.search.to", "text": "{travel_to}"},
              {"type": "travel.search.date", "text": "{travel_date}"}, {"click": "travel.search.submit"}],
             "${earliest_price}", A("money", "{earliest_price}"))


L1 = [r_battery_level, r_wifi, r_bt_device, r_device_name, r_unread, r_latest_sender, r_next_event, r_done_groceries,
      r_doc_count, r_price, r_clinic_slot, r_headline]
L2 = [r_top_battery, r_battery_pair, r_largest_category, r_invite_time, r_plan_time, r_plan_place, r_tomorrow_first,
      r_tomorrow_count, r_cheaper, r_flight_cheapest, r_garden_hours, r_update_version, r_second_open,
      r_statement_sender]
L3 = [r_top_battery_main, r_version_main, r_invite_place, r_bin_day, r_key_holder, r_largest_file, r_garden_home,
      r_shop_search, r_flight_search]
ABSTAIN = {1: [r_abs_cycles, r_abs_clinic, r_abs_wifi_password], 2: [r_abs_invite_cost, r_abs_trash_size,
                                                                       r_abs_flight_number]}


def _deal(rng: random.Random, pool: list, n: int) -> list:
    out: list = []
    while len(out) < n:
        batch = list(pool)
        rng.shuffle(batch)
        out += batch
    return out[:n]


def _case(spec: dict, split: str, k: int, level: int) -> dict:
    params = dict(spec["params"])
    gen = "read_setup" if "read" in params else "apps_v1"
    tags = ["read", spec["tpl"].split("_")[0]] + (["abstain"] if spec["answer"].get("abstain") else [])
    return base_case(case_id(FAMILY, split, spec["tpl"], k), FAMILY, level, task=spec["task"], start=spec["start"],
                     apps=spec["apps"], oracle=list(spec["oracle"]) + [{"finish": spec["finish"]}], generator=gen,
                     params=params, answer=spec["answer"], unchanged="all", tags=tags)


def generate(split: str, rng: random.Random, n: int) -> list[dict]:
    levels = spread(n, [1, 2, 3])
    n_abs = max(2, round(n / 10))
    abs_at = {1: n_abs // 2, 2: n_abs - n_abs // 2}
    plan: dict[int, list] = {}
    for lv, pool in ((1, L1), (2, L2), (3, L3)):
        count = levels.count(lv)
        k_abs = abs_at.get(lv, 0)
        fns = _deal(rng, pool, count - k_abs) + _deal(rng, ABSTAIN.get(lv, []), k_abs)
        rng.shuffle(fns)
        plan[lv] = fns
    used = {1: 0, 2: 0, 3: 0}
    out = []
    for k, level in enumerate(levels, start=1):
        fn = plan[level][used[level]]
        used[level] += 1
        out.append(_case(fn(rng), split, k, level))
    return out
