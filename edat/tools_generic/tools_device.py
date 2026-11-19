"""Device settings, smart home and timers (collections `device`, `timers`).

Device settings are `device` records whose id is a setting name and whose
`value` is its value: toggles (`wifi`, `bluetooth`, `do_not_disturb`,
`airplane_mode`, `dark_mode`) hold booleans; `volume`, `brightness` and
`battery` hold 0-100 integers. An unseeded setting reads as its default below
and is written as a new record on first change.

Every other `device` record is a smart-home device. Its kind is its `kind`
field, or is inferred: a `setpoint_c` field or an id starting "thermo" is a
thermostat; ids containing light, lock, plug or blind name those kinds. Home
fields: thermostat `setpoint_c`, `mode`; light `on`, `brightness`, `color`;
lock `locked`; plug `on`; blinds `position` (0 closed - 100 open).

Timers and alarms live in `timers` (`kind: timer | alarm`) and run on the
store's clock; a running timer whose `ends_at` has passed lists as done.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta

from edat.state.store import StateStore
from edat.tools_generic.registry import (
    PAGING, A, B, I, N, S, ToolError, arg_bool, arg_int, arg_list, arg_num, arg_str, clean, fmt,
    need_str, not_found, page, parse_hhmm, parse_when, record_dt, tool,
)

SETTING_DEFAULTS: dict[str, object] = {
    "airplane_mode": False, "battery": 80, "bluetooth": True, "brightness": 70,
    "dark_mode": False, "do_not_disturb": False, "volume": 50, "wifi": True,
}
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


# ================================================================== device settings
def _setting(store: StateStore, name: str):
    row = store.find("device", name)
    return row.get("value") if row is not None and "value" in row else SETTING_DEFAULTS[name]


def _set(store: StateStore, name: str, value, **extra) -> dict:
    old = _setting(store, name)
    if store.find("device", name) is None:
        store.add("device", {"id": name, "value": value, **extra})
    else:
        store.update("device", name, value=value, **extra)
    return {"ok": True, "setting": name, "value": value, "previous": old, **extra}


@tool("device_get_status", "device", "Get all device settings at once: wifi, bluetooth, volume, brightness, battery and modes.", {})
def device_get_status(store: StateStore, args: dict) -> dict:
    return {"settings": {k: _setting(store, k) for k in sorted(SETTING_DEFAULTS)}}


@tool("device_get_setting", "device", "Get the current value of one device setting.",
      {"setting": S("Which setting.", sorted(SETTING_DEFAULTS))}, ("setting",))
def device_get_setting(store: StateStore, args: dict) -> dict:
    name = need_str(args, "setting")
    if name not in SETTING_DEFAULTS:
        raise ToolError(f"unknown setting {name!r}; known settings: {', '.join(sorted(SETTING_DEFAULTS))}")
    return {"setting": name, "value": _setting(store, name)}


def _level_tool(name: str, what: str):
    @tool(f"device_set_{name}", "device", f"Set the device {what} level from 0 to 100.",
          {"level": I(f"{what.capitalize()} level, 0-100.", 0, 100)}, ("level",), effect=True)
    def handler(store: StateStore, args: dict) -> dict:
        return _set(store, name, arg_int(args, "level", lo=0, hi=100))
    return handler


def _toggle_tool(name: str, what: str, extra_doc: str = ""):
    @tool(f"device_set_{name}", "device", f"Turn {what} on or off.{extra_doc}",
          {"enabled": B(f"true to turn {what} on, false to turn it off.")}, ("enabled",), effect=True)
    def handler(store: StateStore, args: dict) -> dict:
        return _set(store, name, arg_bool(args, "enabled"))
    return handler


device_set_volume = _level_tool("volume", "sound volume")
device_set_brightness = _level_tool("brightness", "screen brightness")
device_set_wifi = _toggle_tool("wifi", "Wi-Fi")
device_set_bluetooth = _toggle_tool("bluetooth", "Bluetooth")


@tool("device_set_do_not_disturb", "device", "Turn Do Not Disturb on or off, optionally until a given time.",
      {"enabled": B("true to turn Do Not Disturb on, false to turn it off."),
       "until": S("When Do Not Disturb should end, local ISO time (only with enabled=true).")},
      ("enabled",), effect=True)
def device_set_do_not_disturb(store: StateStore, args: dict) -> dict:
    on = arg_bool(args, "enabled")
    extra = {}
    if args.get("until") is not None:
        if not on:
            raise ToolError("invalid until: only used when enabled is true")
        until = parse_when(store, args["until"], "until")[0]
        if until <= store.clock.now:
            raise ToolError("invalid until: must be in the future")
        extra["until"] = fmt(until)
    elif store.find("device", "do_not_disturb") is not None and "until" in store.find("device", "do_not_disturb"):
        extra["until"] = None
    return _set(store, "do_not_disturb", on, **extra)


device_set_airplane_mode = _toggle_tool("airplane_mode", "airplane mode", " Airplane mode also turns off Wi-Fi and Bluetooth radios.")
device_set_dark_mode = _toggle_tool("dark_mode", "dark mode")


# ================================================================== smart home
def home_kind(row: dict) -> str:
    if row.get("kind"):
        return str(row["kind"])
    rid = str(row.get("id", "")).lower()
    if "setpoint_c" in row or rid.startswith("thermo"):
        return "thermostat"
    for k, kind in (("light", "light"), ("lamp", "light"), ("lock", "lock"), ("plug", "plug"), ("blind", "blinds")):
        if k in rid:
            return kind
    return "device"


def _home_devices(store: StateStore) -> list[dict]:
    return [r for r in store.get("device") if r.get("id") not in SETTING_DEFAULTS]


def _home_view(row: dict) -> dict:
    out = clean(row)
    out["kind"] = home_kind(row)
    return out


def _pick(store: StateStore, kind: str, device_id: str | None, room: str | None, *, many: bool) -> list[dict]:
    devs = [d for d in _home_devices(store) if home_kind(d) == kind]
    if device_id:
        d = store.find("device", device_id)
        if d is None or d.get("id") in SETTING_DEFAULTS:
            raise not_found("device", device_id)
        if home_kind(d) != kind:
            raise ToolError(f"{device_id} is a {home_kind(d)}, not a {kind}")
        return [d]
    if room:
        r = room.strip().lower()
        devs = [d for d in devs if str(d.get("room", "")).lower() == r or r in str(d.get("name", "")).lower()]
        if not devs:
            raise ToolError(f"no {kind} found in {room!r}", outcome="no_match")
    if not devs:
        raise ToolError(f"no {kind} found", outcome="no_match")
    if not many and len(devs) > 1:
        listed = ", ".join(f"{d['id']}" + (f" ({d['room']})" if d.get("room") else "") for d in devs)
        raise ToolError(f"several {kind}s found: {listed}. Pass device_id.", outcome="needs_input")
    return devs


_DEV_ID = S("The device id, from home_get_status.")
_ROOM = S("Room name, e.g. kitchen.")


@tool("home_get_status", "home", "List smart-home devices (thermostats, lights, locks, plugs, blinds) and their state.",
      {"room": S("Only devices in this room."), "kind": S("Only this kind of device.", ["thermostat", "light", "lock", "plug", "blinds"])})
def home_get_status(store: StateStore, args: dict) -> dict:
    room, kind = arg_str(args, "room"), arg_str(args, "kind")
    rows = [_home_view(d) for d in _home_devices(store)
            if (not kind or home_kind(d) == kind) and (not room or str(d.get("room", "")).lower() == room.lower())]
    return {"devices": rows}


@tool("home_set_thermostat", "home", "Set a thermostat's target temperature and, optionally, its mode.",
      {"setpoint_c": N("Target temperature in degrees Celsius (5-32), or Fahrenheit when unit is fahrenheit."),
       "device_id": S("Thermostat id; may be omitted when there is only one thermostat."),
       "mode": S("Heating mode.", ["heat", "cool", "auto", "off"]),
       "unit": S("Unit of setpoint_c's value (default celsius).", ["celsius", "fahrenheit"])},
      ("setpoint_c",), effect=True)
def home_set_thermostat(store: StateStore, args: dict) -> dict:
    value = arg_num(args, "setpoint_c")
    if (arg_str(args, "unit") or "celsius") == "fahrenheit":
        value = round((value - 32) * 5 / 9 * 2) / 2
        value = int(value) if float(value).is_integer() else value
    if not 5 <= value <= 32:
        raise ToolError("invalid setpoint_c: must be between 5 and 32 degrees Celsius")
    dev = _pick(store, "thermostat", arg_str(args, "device_id"), None, many=False)[0]
    changes = {"setpoint_c": value}
    if arg_str(args, "mode"):
        changes["mode"] = arg_str(args, "mode")
    store.update("device", dev["id"], **changes)
    return {"ok": True, "device_id": dev["id"], **{k: dev.get(k) for k in ("setpoint_c", "mode") if k in dev}}


@tool("home_set_lights", "home", "Turn lights on or off, or dim them, by device, by room, or all lights when neither is given.",
      {"on": B("true to turn on, false to turn off."), "room": _ROOM, "device_id": S("One light's id."),
       "brightness": I("Brightness 1-100 (only when turning on).", 1, 100), "color": S("Colour name, e.g. warm white.")},
      ("on",), effect=True)
def home_set_lights(store: StateStore, args: dict) -> dict:
    on = arg_bool(args, "on")
    bright = arg_int(args, "brightness", lo=1, hi=100)
    if bright is not None and not on:
        raise ToolError("invalid brightness: only used when on is true")
    lights = _pick(store, "light", arg_str(args, "device_id"), arg_str(args, "room"), many=True)
    changes: dict = {"on": on}
    if bright is not None:
        changes["brightness"] = bright
    if arg_str(args, "color"):
        changes["color"] = arg_str(args, "color")
    for d in lights:
        store.update("device", d["id"], **changes)
    return {"ok": True, "lights": [d["id"] for d in lights], **changes}


@tool("home_set_plug", "home", "Switch a smart plug on or off.",
      {"device_id": S("The plug's id."), "on": B("true for on, false for off.")}, ("device_id", "on"), effect=True)
def home_set_plug(store: StateStore, args: dict) -> dict:
    dev = _pick(store, "plug", need_str(args, "device_id"), None, many=False)[0]
    store.update("device", dev["id"], on=arg_bool(args, "on"))
    return {"ok": True, "device_id": dev["id"], "on": dev["on"]}


@tool("home_lock_door", "home", "Lock a door lock.", {"device_id": S("The lock's id; may be omitted when there is only one lock.")},
      effect=True)
def home_lock_door(store: StateStore, args: dict) -> dict:
    dev = _pick(store, "lock", arg_str(args, "device_id"), None, many=False)[0]
    store.update("device", dev["id"], locked=True)
    return {"ok": True, "device_id": dev["id"], "locked": True}


@tool("home_unlock_door", "home", "Unlock a door lock, letting anyone in.",
      {"device_id": S("The lock's id; may be omitted when there is only one lock.")}, effect=True)
def home_unlock_door(store: StateStore, args: dict) -> dict:
    dev = _pick(store, "lock", arg_str(args, "device_id"), None, many=False)[0]
    store.update("device", dev["id"], locked=False)
    return {"ok": True, "device_id": dev["id"], "locked": False}


@tool("home_set_blinds", "home", "Open, close or partly open window blinds (0 = closed, 100 = fully open).",
      {"position": I("Position 0-100.", 0, 100), "room": _ROOM, "device_id": S("One blind's id.")},
      ("position",), effect=True)
def home_set_blinds(store: StateStore, args: dict) -> dict:
    pos = arg_int(args, "position", lo=0, hi=100)
    devs = _pick(store, "blinds", arg_str(args, "device_id"), arg_str(args, "room"), many=True)
    for d in devs:
        store.update("device", d["id"], position=pos)
    return {"ok": True, "blinds": [d["id"] for d in devs], "position": pos}


# ================================================================== timers and alarms
def _ends(row: dict) -> datetime | None:
    return record_dt(row.get("ends_at"))


def _timer_view(store: StateStore, row: dict) -> dict:
    out = clean(row)
    end = _ends(row)
    status = row.get("status", "running")
    if status == "running" and end is not None and end <= store.clock.now:
        status = "done"
    out["status"] = status
    if status == "running" and end is not None and row.get("kind", "timer") == "timer":
        out["remaining_minutes"] = round((end - store.clock.now).total_seconds() / 60, 1)
    return out


def _active(store: StateStore, kind: str) -> list[dict]:
    return [r for r in store.get("timers") if r.get("kind", "timer") == kind
            and _timer_view(store, r)["status"] == "running"]


def _choose(store: StateStore, kind: str, rid: str | None, label: str | None) -> dict:
    if rid:
        r = store.find("timers", rid)
        if r is None or r.get("kind", "timer") != kind:
            raise not_found(kind, rid)
        return r
    if not label:
        raise ToolError(f"give {kind}_id or label")
    hits = [r for r in _active(store, kind) if label.lower() in str(r.get("label", "")).lower()]
    if not hits:
        raise ToolError(f"no running {kind} labelled {label!r}", outcome="no_match")
    if len(hits) > 1:
        listed = ", ".join(f"{r['id']} ({r.get('label')})" for r in hits)
        raise ToolError(f"several {kind}s match {label!r}: {listed}. Pass {kind}_id.", outcome="needs_input")
    return hits[0]


def _cancel(store: StateStore, row: dict, kind: str) -> dict:
    if _timer_view(store, row)["status"] != "running":
        raise ToolError(f"{kind} {row['id']!r} is not running")
    store.update("timers", row["id"], status="cancelled")
    return {"ok": True, kind: _timer_view(store, row)}


@tool("timer_start", "timers", "Start a countdown timer.",
      {"minutes": N("Duration in minutes (fractions allowed, e.g. 0.5).", 0.05, 1440), "label": S("What the timer is for.")},
      ("minutes",), effect=True)
def timer_start(store: StateStore, args: dict) -> dict:
    minutes = arg_num(args, "minutes", lo=0.05, hi=1440)
    end = store.clock.now + timedelta(minutes=minutes)
    ends_at = end.strftime("%Y-%m-%dT%H:%M:%S") if end.second else fmt(end)
    row = {"label": arg_str(args, "label") or "", "minutes": minutes, "ends_at": ends_at, "kind": "timer",
           "status": "running", "started_at": store.clock.iso()}
    return {"ok": True, "timer": store.add("timers", row, prefix="timer")}


@tool("timer_cancel", "timers", "Cancel a running timer, by id or by its label.",
      {"timer_id": S("The timer's id, from timer_list."), "label": S("The timer's label, if no id is known.")}, effect=True)
def timer_cancel(store: StateStore, args: dict) -> dict:
    return _cancel(store, _choose(store, "timer", arg_str(args, "timer_id"), arg_str(args, "label")), "timer")


@tool("timer_list", "timers", "List timers with the minutes remaining.",
      {"include_finished": B("Include finished and cancelled timers (default false)."), **PAGING})
def timer_list(store: StateStore, args: dict) -> dict:
    every = arg_bool(args, "include_finished", False)
    rows = [_timer_view(store, r) for r in store.get("timers") if r.get("kind", "timer") == "timer"]
    rows = [r for r in rows if every or r["status"] == "running"]
    return page(rows, args)


@tool("alarm_set", "timers", "Set an alarm for a clock time (the next time it comes round) or a full date and time.",
      {"time": S("HH:MM for the next occurrence, or a local ISO date and time."), "label": S("Alarm label."),
       "repeat": A("Weekdays to repeat on, e.g. [\"monday\", \"friday\"].", {"type": "string", "enum": _WEEKDAYS})},
      ("time",), effect=True)
def alarm_set(store: StateStore, args: dict) -> dict:
    raw = need_str(args, "time").strip()
    if ":" in raw and len(raw) <= 5:
        h, m = parse_hhmm(raw, "time")
        at = datetime.combine(store.clock.today, time(h, m))
        if at <= store.clock.now:
            at += timedelta(days=1)
    else:
        at = parse_when(store, raw, "time")[0]
        if at <= store.clock.now:
            raise ToolError("invalid time: must be in the future")
    repeat = arg_list(args, "repeat") or []
    bad = [d for d in repeat if d.lower() not in _WEEKDAYS]
    if bad:
        raise ToolError(f"invalid repeat: unknown day {bad[0]!r}")
    row = {"label": arg_str(args, "label") or "", "ends_at": fmt(at), "kind": "alarm", "status": "running"}
    if repeat:
        row["repeat"] = [d.lower() for d in repeat]
    return {"ok": True, "alarm": store.add("timers", row, prefix="alarm")}


@tool("alarm_cancel", "timers", "Cancel an alarm, by id or by its label.",
      {"alarm_id": S("The alarm's id, from alarm_list."), "label": S("The alarm's label, if no id is known.")}, effect=True)
def alarm_cancel(store: StateStore, args: dict) -> dict:
    return _cancel(store, _choose(store, "alarm", arg_str(args, "alarm_id"), arg_str(args, "label")), "alarm")


@tool("alarm_list", "timers", "List alarms.", {"include_cancelled": B("Include cancelled and past alarms (default false)."), **PAGING})
def alarm_list(store: StateStore, args: dict) -> dict:
    every = arg_bool(args, "include_cancelled", False)
    rows = [_timer_view(store, r) for r in store.get("timers") if r.get("kind") == "alarm"]
    rows = sorted((r for r in rows if every or r["status"] == "running"), key=lambda r: str(r.get("ends_at")))
    return page(rows, args)
