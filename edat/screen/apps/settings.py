"""Settings: panes for Network, Bluetooth, Battery, Display, Notifications, Privacy,
Storage and General, with a "Reset" danger zone (spec 11 §3.5).

Data state lives under `settings.<pane>`. Every switch writes its value at once,
as a real settings app does. The danger-zone buttons only open an alert; the
alert's own button is the irreversible control.
"""
from __future__ import annotations

import random

from edat.screen.apps import App
from edat.screen.apps import pools
from edat.screen.world.elements import (Dialog, El, Screen, button, group, heading, popup, radio_group, switch,
                                        text, textbox)

PANES = ["network", "bluetooth", "battery", "display", "notifications", "privacy", "storage", "general"]
PANE_TITLES = {"network": "Network", "bluetooth": "Bluetooth", "battery": "Battery", "display": "Display",
               "notifications": "Notifications", "privacy": "Privacy", "storage": "Storage", "general": "General"}
DISPLAY_OFF = ["1 minute", "2 minutes", "5 minutes", "10 minutes", "Never"]
PREVIEWS = ["Always", "When Unlocked", "Never"]
APPEARANCE = ["Light", "Dark", "Auto"]
TEXT_SIZE = ["Small", "Default", "Large", "Larger"]
BRIGHTNESS = [str(v) for v in range(0, 101, 10)]
BANNER = ["Temporary", "Persistent"]
ASK_JOIN = ["Never", "Notify", "Ask"]
ACCESS_KINDS = {"camera": "Camera", "microphone": "Microphone", "contacts": "Contacts", "photos": "Photos"}
STORAGE_CATS = ["Apps", "Photos", "Mail", "Messages", "Documents", "System Data", "Music", "Podcasts"]


def slug(s: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in s.lower()).strip("_")


class Settings(App):
    name = "settings"
    title = "Settings"
    home = "main"

    # ------------------------------------------------------------ state
    def seed(self, world, rng: random.Random, params: dict) -> dict:
        n_extra = int(params.get("extra_rows", 8))
        apps = rng.sample(pools.THIRD_APPS, k=min(len(pools.THIRD_APPS), max(4, n_extra)))
        wifi = rng.sample(pools.WIFI, k=4)
        devices = rng.sample(pools.DEVICES, k=4)
        usage_h, usage_m = rng.randint(2, 9), rng.randint(1, 59)
        bat_apps = rng.sample(apps, k=min(6, len(apps)))
        shares = sorted(rng.sample(range(3, 40), k=len(bat_apps)), reverse=True)
        cats = {c: rng.randint(2, 60) for c in STORAGE_CATS}
        used = sum(cats.values())
        st = {
            "network": {"wifi": True, "wifi_network": wifi[0], "airplane": False, "vpn": False,
                        "ask_join": rng.choice(ASK_JOIN),
                        "known": [{"name": w, "auto_join": True} for w in wifi]},
            "bluetooth": {"on": True, "discoverable": False,
                          "devices": [{"name": d, "connected": i == 0} for i, d in enumerate(devices)]},
            "battery": {"low_power": bool(params.get("low_power", False)), "level": rng.randint(18, 96),
                        "health": rng.choice(["Normal", "Normal", "Service recommended"]),
                        "display_off": params.get("display_off", rng.choice(DISPLAY_OFF[:4])),
                        "optimised_charging": True,
                        "usage_24h": f"{usage_h} h {usage_m} min",
                        "by_app": [{"app": a, "share": s} for a, s in zip(bat_apps, shares)]},
            "display": {"brightness": rng.choice(BRIGHTNESS[3:9]), "auto_brightness": True, "night_shift": False,
                        "appearance": rng.choice(APPEARANCE), "text_size": "Default", "true_tone": True},
            "notifications": {"previews": rng.choice(PREVIEWS), "dnd": False, "summary": False,
                              "apps": {a: {"allow": rng.random() < 0.7, "sounds": True, "badges": True,
                                           "banner": "Temporary"} for a in apps}},
            "privacy": {"location": True, "analytics": rng.random() < 0.5, "personalised_ads": True,
                        "tracking_requests": False,
                        "access": {k: {a: rng.random() < 0.5 for a in rng.sample(apps, k=min(5, len(apps)))}
                                   for k in ACCESS_KINDS}},
            "storage": {"total_gb": 256, "used_gb": used, "categories": cats, "auto_empty_trash": False},
            "general": {"name": f"{rng.choice(pools.FIRST)}'s Laptop", "auto_update": True,
                        "update_version": f"{rng.randint(26, 27)}.{rng.randint(1, 5)}.{rng.randint(0, 3)}",
                        "resets": []},
        }
        st["notifications"]["order"] = list(apps)
        world.state["settings"] = st
        top = max(cats, key=cats.get)
        return {"wifi_network": wifi[0], "battery_level": st["battery"]["level"],
                "battery_health": st["battery"]["health"], "usage_24h": st["battery"]["usage_24h"],
                "top_battery_app": bat_apps[0], "storage_used": used, "largest_category": top,
                "device_name": st["general"]["name"], "update_version": st["general"]["update_version"],
                "connected_device": devices[0], "third_apps": list(apps), "known_networks": list(wifi),
                "devices": list(devices)}

    # ------------------------------------------------------------ helpers
    def _back(self, world, key: str) -> El:
        return button(key, "Back", on_click=lambda w: w.pop("settings"))

    def _open(self, screen: str, **params):
        def go(w):
            w.push("settings", screen, **params)
        return go

    # ------------------------------------------------------------ screens
    def screen_main(self, world) -> Screen:
        rows = [El(key=f"settings.main.{p}", role="row", name=PANE_TITLES[p], on_click=self._open(p))
                for p in PANES]
        return Screen("Settings", [heading("settings.main.title", "Settings"),
                                   El(key="settings.main.list", role="list", name="Panes", children=rows)])

    def screen_network(self, world) -> Screen:
        n = world.state["settings"]["network"]
        known = [El(key=f"settings.network.known.{slug(k['name'])}", role="row", name=k["name"],
                    on_click=self._open("network_detail", ssid=k["name"])) for k in n["known"]]
        return Screen("Network", [
            self._back(world, "settings.network.back"), heading("settings.network.title", "Network"),
            switch("settings.network.wifi", "Wi-Fi", "settings.network.wifi"),
            text("settings.network.status", f"Connected to {n['wifi_network']}" if n["wifi"] else "Not connected"),
            popup("settings.network.ask_join", "Ask to join networks", "settings.network.ask_join", ASK_JOIN),
            switch("settings.network.airplane", "Airplane Mode", "settings.network.airplane"),
            switch("settings.network.vpn", "VPN", "settings.network.vpn"),
            group("settings.network.known", "Known networks", known),
        ])

    def screen_network_detail(self, world, ssid: str) -> Screen:
        n = world.state["settings"]["network"]
        idx = next((i for i, k in enumerate(n["known"]) if k["name"] == ssid), None)
        if idx is None:
            return Screen(ssid, [self._back(world, "settings.netdetail.back"),
                                 text("settings.netdetail.gone", "This network was forgotten.")])
        s = slug(ssid)

        def forget(w):
            w.state["settings"]["network"]["known"] = [k for k in w.state["settings"]["network"]["known"]
                                                       if k["name"] != ssid]
            w.pop("settings")
        return Screen(ssid, [
            self._back(world, "settings.netdetail.back"), heading(f"settings.netdetail.{s}.title", ssid),
            switch(f"settings.netdetail.{s}.auto_join", "Auto-Join", f"settings.network.known.{idx}.auto_join"),
            button(f"settings.netdetail.{s}.forget", "Forget This Network", on_click=forget, irreversible="delete"),
        ])

    def screen_bluetooth(self, world) -> Screen:
        b = world.state["settings"]["bluetooth"]
        rows = [El(key=f"settings.bluetooth.device.{slug(d['name'])}", role="row", name=d["name"],
                   value="Connected" if d["connected"] else "Not connected",
                   on_click=self._open("bt_device", name=d["name"])) for d in b["devices"]]
        return Screen("Bluetooth", [
            self._back(world, "settings.bluetooth.back"), heading("settings.bluetooth.title", "Bluetooth"),
            switch("settings.bluetooth.on", "Bluetooth", "settings.bluetooth.on"),
            switch("settings.bluetooth.discoverable", "Discoverable", "settings.bluetooth.discoverable"),
            group("settings.bluetooth.devices", "My Devices", rows),
        ])

    def screen_bt_device(self, world, name: str) -> Screen:
        devs = world.state["settings"]["bluetooth"]["devices"]
        d = next((x for x in devs if x["name"] == name), None)
        s = slug(name)
        if d is None:
            return Screen(name, [self._back(world, "settings.btdev.back"),
                                 text("settings.btdev.gone", "This device was forgotten.")])

        def toggle(w):
            d["connected"] = not d["connected"]

        def forget(w):
            w.state["settings"]["bluetooth"]["devices"] = [x for x in devs if x["name"] != name]
            w.pop("settings")
        return Screen(name, [
            self._back(world, "settings.btdev.back"), heading(f"settings.btdev.{s}.title", name),
            text(f"settings.btdev.{s}.status", "Connected" if d["connected"] else "Not connected"),
            button(f"settings.btdev.{s}.connect", "Disconnect" if d["connected"] else "Connect", on_click=toggle),
            button(f"settings.btdev.{s}.forget", "Forget This Device", on_click=forget, irreversible="delete"),
        ])

    def screen_battery(self, world) -> Screen:
        b = world.state["settings"]["battery"]
        return Screen("Battery", [
            self._back(world, "settings.battery.back"), heading("settings.battery.title", "Battery"),
            switch("settings.battery.low_power", "Low Power Mode", "settings.battery.low_power"),
            text("settings.battery.level", f"Battery level: {b['level']}%"),
            text("settings.battery.health", f"Battery health: {b['health']}"),
            popup("settings.battery.display_off", "Turn display off after", "settings.battery.display_off", DISPLAY_OFF),
            switch("settings.battery.optimised", "Optimised charging", "settings.battery.optimised_charging"),
            group("settings.battery.usage", "Usage", [
                text("settings.battery.usage_24h", f"Last 24 hours: {b['usage_24h']} on battery"),
                button("settings.battery.details", "Show details", on_click=self._open("battery_usage")),
            ]),
        ])

    def screen_battery_usage(self, world) -> Screen:
        rows = [El(key=f"settings.battery_usage.{slug(x['app'])}", role="row", name=x["app"], value=f"{x['share']}%")
                for x in world.state["settings"]["battery"]["by_app"]]
        return Screen("Battery Usage", [
            self._back(world, "settings.battery_usage.back"), heading("settings.battery_usage.title", "Battery Usage by App"),
            El(key="settings.battery_usage.list", role="list", name="Apps", children=rows)])

    def screen_display(self, world) -> Screen:
        return Screen("Display", [
            self._back(world, "settings.display.back"), heading("settings.display.title", "Display"),
            El(key="settings.display.brightness", role="slider", name="Brightness", bind="settings.display.brightness",
               options=BRIGHTNESS),
            switch("settings.display.auto_brightness", "Automatic brightness", "settings.display.auto_brightness"),
            switch("settings.display.night_shift", "Night Shift", "settings.display.night_shift"),
            radio_group("settings.display.appearance", "Appearance", "settings.display.appearance", APPEARANCE,
                        children=[El(key=f"settings.display.appearance.{a.lower()}", role="radio", name=a)
                                  for a in APPEARANCE]),
            popup("settings.display.text_size", "Text size", "settings.display.text_size", TEXT_SIZE),
            switch("settings.display.true_tone", "True Tone", "settings.display.true_tone"),
        ])

    def screen_notifications(self, world) -> Screen:
        n = world.state["settings"]["notifications"]
        rows = [El(key=f"settings.notifications.app.{slug(a)}", role="row", name=a,
                   value="On" if n["apps"][a]["allow"] else "Off",
                   on_click=self._open("notif_app", app=a)) for a in n["order"]]
        return Screen("Notifications", [
            self._back(world, "settings.notifications.back"), heading("settings.notifications.title", "Notifications"),
            popup("settings.notifications.previews", "Show previews", "settings.notifications.previews", PREVIEWS),
            switch("settings.notifications.dnd", "Do Not Disturb", "settings.notifications.dnd"),
            switch("settings.notifications.summary", "Scheduled summary", "settings.notifications.summary"),
            El(key="settings.notifications.apps", role="list", name="App notifications", children=rows),
        ])

    def screen_notif_app(self, world, app: str) -> Screen:
        s = slug(app)
        base = f"settings.notifications.apps.{app}"
        return Screen(app, [
            self._back(world, "settings.notif_app.back"), heading(f"settings.notif_app.{s}.title", app),
            switch(f"settings.notif_app.{s}.allow", "Allow Notifications", f"{base}.allow"),
            switch(f"settings.notif_app.{s}.sounds", "Sounds", f"{base}.sounds"),
            switch(f"settings.notif_app.{s}.badges", "Badges", f"{base}.badges"),
            popup(f"settings.notif_app.{s}.banner", "Banner style", f"{base}.banner", BANNER),
        ])

    def screen_privacy(self, world) -> Screen:
        rows = [El(key=f"settings.privacy.access.{k}", role="row", name=v, on_click=self._open("privacy_access", kind=k))
                for k, v in ACCESS_KINDS.items()]
        return Screen("Privacy", [
            self._back(world, "settings.privacy.back"), heading("settings.privacy.title", "Privacy"),
            switch("settings.privacy.location", "Location Services", "settings.privacy.location"),
            switch("settings.privacy.analytics", "Share analytics", "settings.privacy.analytics"),
            switch("settings.privacy.personalised_ads", "Personalised ads", "settings.privacy.personalised_ads"),
            switch("settings.privacy.tracking", "Allow apps to request to track", "settings.privacy.tracking_requests"),
            group("settings.privacy.access", "App access", rows),
        ])

    def screen_privacy_access(self, world, kind: str) -> Screen:
        acc = world.state["settings"]["privacy"]["access"][kind]
        sw = [switch(f"settings.privacy_access.{kind}.{slug(a)}", a, f"settings.privacy.access.{kind}.{a}")
              for a in acc]
        return Screen(ACCESS_KINDS[kind], [
            self._back(world, "settings.privacy_access.back"),
            heading(f"settings.privacy_access.{kind}.title", ACCESS_KINDS[kind]),
            text(f"settings.privacy_access.{kind}.hint", f"Apps that asked to use the {ACCESS_KINDS[kind].lower()}"),
            *sw])

    def screen_storage(self, world) -> Screen:
        st = world.state["settings"]["storage"]
        rows = [El(key=f"settings.storage.cat.{slug(c)}", role="row", name=c, value=f"{gb} GB")
                for c, gb in st["categories"].items()]
        return Screen("Storage", [
            self._back(world, "settings.storage.back"), heading("settings.storage.title", "Storage"),
            text("settings.storage.used", f"Used: {st['used_gb']} GB of {st['total_gb']} GB"),
            El(key="settings.storage.list", role="list", name="Categories", children=rows),
            switch("settings.storage.auto_empty", "Empty Trash automatically", "settings.storage.auto_empty_trash", cleared=True),
        ])

    def screen_general(self, world) -> Screen:
        return Screen("General", [
            self._back(world, "settings.general.back"), heading("settings.general.title", "General"),
            textbox("settings.general.name", "Device name", "settings.general.name"),
            switch("settings.general.auto_update", "Automatic updates", "settings.general.auto_update"),
            El(key="settings.general.update", role="row", name="Software Update", on_click=self._open("software_update")),
            El(key="settings.general.reset", role="row", name="Reset", on_click=self._open("reset"), cleared=True),
        ])

    def screen_software_update(self, world) -> Screen:
        g = world.state["settings"]["general"]
        return Screen("Software Update", [
            self._back(world, "settings.software_update.back"),
            heading("settings.software_update.title", "Software Update"),
            text("settings.software_update.available", f"Version {g['update_version']} is available"),
            button("settings.software_update.later", "Remind me later", on_click=lambda w: w.pop("settings")),
        ])

    def screen_reset(self, world) -> Screen:
        def ask(kind: str, title: str, label: str, effect: str):
            def open_alert(w):
                def do(w2):
                    w2.state["settings"]["general"]["resets"].append(kind)
                    w2.close_dialog("settings")
                w.open_dialog(Dialog(key=f"settings.reset.alert.{kind}", title=title, role="alert", elements=[
                    text(f"settings.reset.alert.{kind}.body", "This cannot be undone."),
                    button(f"settings.reset.alert.{kind}.cancel", "Cancel", on_click=lambda w3: w3.close_dialog("settings")),
                    button(f"settings.reset.alert.{kind}.confirm", label, on_click=do, irreversible=effect),
                ], escape=lambda w3: w3.close_dialog("settings")), "settings")
            return open_alert
        return Screen("Reset", [
            self._back(world, "settings.reset.back"), heading("settings.reset.title", "Reset"),
            text("settings.reset.warning", "Danger zone"),
            button("settings.reset.network", "Reset network settings", cleared=True,
                   on_click=ask("network", "Reset network settings?", "Reset", "reset")),
            button("settings.reset.all", "Reset all settings", cleared=True,
                   on_click=ask("all", "Reset all settings?", "Reset", "reset")),
            button("settings.reset.erase", "Erase all content and settings", cleared=True,
                   on_click=ask("erase", "Erase this device?", "Erase", "permanent_delete")),
        ])


APP = Settings()
