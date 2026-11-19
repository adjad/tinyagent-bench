"""Read-only information tools: weather, maps, math and units, time, stocks.

Weather for a place with a seeded `weather` record (matched on `location` or
`id`, and on `date`, default today) returns that record. Any other place gets
values from a SHA-256 hash of the lower-cased place name and the date, so the
same city gives the same weather on every run. Directions come from a seeded
`routes` record ({origin, destination, mode?, distance_km, duration_min}) or
from a hash of the two place names. Place search reads a seeded `places`
collection ({id, name, category, address, city, phone, rating, hours}) and
otherwise invents a stable list from a hash of the query.
"""
from __future__ import annotations

import ast
import calendar as _cal
import hashlib
import math
import operator
import re
import statistics
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from edat.state.store import StateStore
from edat.tools_generic.registry import (
    PAGING, A, I, N, S, ToolError, arg_int, arg_num, arg_str, clean, fmt, matches, need_str, page,
    parse_hhmm, parse_when, tool,
)

_CONDITIONS = ["sunny", "clear", "partly cloudy", "cloudy", "overcast", "light rain", "rain", "showers", "fog", "windy"]


def _h(*parts: object) -> int:
    return int(hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:12], 16)


def _key(place: str) -> str:
    return " ".join(place.lower().split())


def _place_match(record: dict, place: str) -> bool:
    q = _key(place)
    for field in ("location", "id"):
        v = _key(str(record.get(field) or ""))
        if v and (v == q or v.split(",")[0].strip() == q.split(",")[0].strip()):
            return True
    return False


# ================================================================== weather
def _day(store: StateStore, place: str, day: str) -> dict:
    today = store.clock.today.isoformat()
    for r in store.get("weather"):
        if _place_match(r, place) and (r.get("date") or today) == day:
            out = clean(r, "id")
            out.update(location=r.get("location") or place, date=day)
            return out
    k = _key(place)
    base = _h("climate", k) % 30 - 4                 # a place's typical temperature, -4..25 C
    swing = _h("day", k, day) % 9 - 4                # day-to-day change, -4..4
    high = base + swing + 3 + _h("range", k, day) % 5
    low = base + swing - 3 - _h("low", k, day) % 4
    cond = _CONDITIONS[_h("cond", k, day) % len(_CONDITIONS)]
    if low <= 0 and "rain" in cond:
        cond = "snow"
    return {"location": place, "date": day, "high_c": high, "low_c": low, "condition": cond,
            "precip_chance_pct": (_h("rain", k, day) % 10) * 10 if "rain" in cond or "showers" in cond or cond == "snow"
            else (_h("rain", k, day) % 3) * 5,
            "humidity_pct": 35 + _h("hum", k, day) % 60, "wind_kph": 3 + _h("wind", k, day) % 35}


def _temp_at(d: dict, hour: float) -> int | None:
    if d.get("high_c") is None or d.get("low_c") is None:
        return d.get("temp_c")
    f = max(0.0, 1 - abs(hour - 15) / 12)            # warmest at 15:00
    return round(d["low_c"] + (d["high_c"] - d["low_c"]) * f)


def _units(d: dict, args: dict) -> dict:
    if (arg_str(args, "units") or "celsius") == "fahrenheit":
        for k in [k for k in d if k.endswith("_c") and isinstance(d[k], (int, float))]:
            d[k[:-2] + "_f"] = round(d[k] * 9 / 5 + 32)
    return d


_UNITS = S("Temperature units (default celsius).", ["celsius", "fahrenheit"])


@tool("weather_get_current", "weather", "Get the current weather for a place.",
      {"location": S("City or place name, e.g. Lisbon."), "units": _UNITS}, ("location",))
def weather_get_current(store: StateStore, args: dict) -> dict:
    place = need_str(args, "location").strip()
    d = _day(store, place, store.clock.today.isoformat())
    if "temp_c" not in d:
        t = _temp_at(d, store.clock.now.hour + store.clock.now.minute / 60)
        if t is not None:
            d["temp_c"] = t
    d["as_of"] = store.clock.iso()
    return _units(d, args)


@tool("weather_get_forecast", "weather", "Get the daily forecast (high, low, conditions) for a place, starting today.",
      {"location": S("City or place name."), "days": I("Number of days, 1-10 (default 3).", 1, 10), "units": _UNITS},
      ("location",))
def weather_get_forecast(store: StateStore, args: dict) -> dict:
    place = need_str(args, "location").strip()
    n = arg_int(args, "days", 3, lo=1, hi=10)
    days = [_units(_day(store, place, (store.clock.today + timedelta(days=i)).isoformat()), args) for i in range(n)]
    return {"location": place, "days": days}


@tool("weather_get_hourly", "weather", "Get an hour-by-hour forecast for a place, starting this hour.",
      {"location": S("City or place name."), "hours": I("Number of hours, 1-24 (default 12).", 1, 24), "units": _UNITS},
      ("location",))
def weather_get_hourly(store: StateStore, args: dict) -> dict:
    place = need_str(args, "location").strip()
    n = arg_int(args, "hours", 12, lo=1, hi=24)
    start = store.clock.now.replace(minute=0)
    out = []
    for i in range(n):
        at = start + timedelta(hours=i)
        d = _day(store, place, at.date().isoformat())
        row = {"time": fmt(at), "temp_c": _temp_at(d, at.hour), "condition": d.get("condition")}
        if "precip_chance_pct" in d:
            row["precip_chance_pct"] = d["precip_chance_pct"]
        out.append(_units(row, args))
    return {"location": place, "hours": out}


# ================================================================== maps
_SPEED_KPH = {"driving": 38, "transit": 24, "cycling": 15, "walking": 4.8}
_STREETS = ["Maple Avenue", "Ridge Road", "Canal Street", "Elm Street", "Station Road", "Park Lane",
            "Mill Road", "Orchard Way", "Bridge Street", "Hillcrest Drive", "Lake Boulevard", "Juniper Court"]
_ADJ = ["Blue", "Copper", "Golden", "Little", "Quiet", "Red", "Silver", "Green", "Old", "Bright", "North", "Willow"]
_NOUN = ["Fern", "Lantern", "Kettle", "Sparrow", "Anchor", "Oak", "Pine", "Harvest", "Corner", "Stone", "Meadow", "Brook"]
_MODE = S("Travel mode (default driving).", list(_SPEED_KPH))


def _route(store: StateStore, origin: str, dest: str, mode: str) -> dict:
    if mode not in _SPEED_KPH:
        raise ToolError(f"invalid mode: use one of {', '.join(_SPEED_KPH)}")
    for r in store.get("routes"):
        ends = {_key(str(r.get("origin", ""))), _key(str(r.get("destination", "")))}
        if ends == {_key(origin), _key(dest)} and r.get("mode", mode) == mode:
            out = clean(r, "id")
            out.update(origin=origin, destination=dest, mode=mode)
            return out
    a, b = sorted([_key(origin), _key(dest)])
    if a == b:
        return {"origin": origin, "destination": dest, "mode": mode, "distance_km": 0, "duration_min": 0, "steps": []}
    seed = _h("route", a, b)
    km = round(1.2 + (seed % 420) / 10, 1)
    extra = 8 if mode == "transit" else 0
    minutes = round(km / _SPEED_KPH[mode] * 60 + extra)
    s1, s2, s3 = (_STREETS[(seed >> n) % len(_STREETS)] for n in (3, 9, 15))
    turn = "left" if seed % 2 else "right"
    steps = [f"Head {['north', 'south', 'east', 'west'][seed % 4]} on {s1}", f"Turn {turn} onto {s2}",
             f"Continue on {s3} for {round(km * 0.6, 1)} km", f"Arrive at {dest}"]
    return {"origin": origin, "destination": dest, "mode": mode, "distance_km": km, "duration_min": max(1, minutes),
            "steps": steps}


@tool("maps_get_directions", "maps", "Get turn-by-turn directions, distance and travel time between two places.",
      {"origin": S("Start place or address."), "destination": S("End place or address."), "mode": _MODE},
      ("origin", "destination"))
def maps_get_directions(store: StateStore, args: dict) -> dict:
    return _route(store, need_str(args, "origin").strip(), need_str(args, "destination").strip(),
                  arg_str(args, "mode") or "driving")


@tool("maps_get_travel_time", "maps", "Get just the travel time and distance between two places, and the arrival time for a departure time.",
      {"origin": S("Start place or address."), "destination": S("End place or address."), "mode": _MODE,
       "depart_at": S("Departure time, local ISO (default now).")},
      ("origin", "destination"))
def maps_get_travel_time(store: StateStore, args: dict) -> dict:
    r = _route(store, need_str(args, "origin").strip(), need_str(args, "destination").strip(),
               arg_str(args, "mode") or "driving")
    leave = parse_when(store, args["depart_at"], "depart_at")[0] if args.get("depart_at") else store.clock.now
    out = {k: r.get(k) for k in ("origin", "destination", "mode", "distance_km", "duration_min")}
    out.update(depart_at=fmt(leave), arrive_at=fmt(leave + timedelta(minutes=r.get("duration_min") or 0)))
    return out


@tool("maps_search_places", "maps", "Search for places such as shops, restaurants or services near a location.",
      {"query": S("What to look for, e.g. pharmacy or thai restaurant."), "near": S("Place or address to search near."),
       **PAGING}, ("query",))
def maps_search_places(store: StateStore, args: dict) -> dict:
    query = need_str(args, "query").strip()
    near = (arg_str(args, "near") or "").strip()
    seeded = [clean(p) for p in store.get("places")
              if matches(query, p, ("name", "category", "address"))
              and (not near or matches(near, p, ("city", "address", "near")))]
    if seeded or store.get("places"):
        return page(seeded, args)
    k = f"{_key(query)}|{_key(near)}"
    n = 3 + _h("count", k) % 4
    items = []
    for i in range(n):
        s = _h("place", k, i)
        open_h, close_h = 7 + s % 4, 18 + (s >> 4) % 5
        items.append({
            "id": f"pl-{s % 16**8:08x}", "name": f"{_ADJ[s % 12]} {_NOUN[(s >> 5) % 12]} {query.title()}",
            "address": f"{100 + s % 900} {_STREETS[(s >> 9) % 12]}" + (f", {near}" if near else ""),
            "phone": f"+1 555 01{s % 100:02d}", "rating": round(3.5 + (s % 15) / 10, 1),
            "distance_km": round(0.3 + ((s >> 13) % 80) / 10, 1), "hours": f"{open_h:02d}:00-{close_h:02d}:00",
            "open_now": open_h <= store.clock.now.hour < close_h,
        })
    items.sort(key=lambda p: (p["distance_km"], p["id"]))
    return page(items, args)


# ================================================================== math and units
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_UN = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {"sqrt": math.sqrt, "abs": abs, "round": round, "min": min, "max": max, "floor": math.floor,
          "ceil": math.ceil, "log": math.log, "log10": math.log10, "exp": math.exp, "sin": math.sin,
          "cos": math.cos, "tan": math.tan}
_CONSTS = {"pi": math.pi, "e": math.e}


def _eval(node: ast.AST):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        a, b = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and (abs(b) > 1000 or abs(a) > 1e6 and b > 10):
            raise ToolError("exponent too large")
        return _BIN[type(node.op)](a, b)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UN:
        return _UN[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
        return _FUNCS[node.func.id](*[_eval(a) for a in node.args])
    raise ToolError("unsupported expression: use numbers, + - * / // % ** ( ), and functions such as sqrt, round, min, max")


def _num(x):
    if isinstance(x, float):
        if math.isinf(x) or math.isnan(x):
            raise ToolError("result is not a finite number")
        if x.is_integer() and abs(x) < 1e15:
            return int(x)
        return round(x, 10)
    return x


@tool("math_calculate", "math", "Evaluate an arithmetic expression exactly, e.g. (1240 * 0.15) + 12 or 18% of 64.",
      {"expression": S("The expression. Supports + - * / // % ** ^, parentheses, percentages like 15%, "
                       "and sqrt, abs, round, min, max, floor, ceil, log, exp, sin, cos, tan, pi, e.")},
      ("expression",))
def math_calculate(store: StateStore, args: dict) -> dict:
    expr = need_str(args, "expression")
    text = expr.replace("×", "*").replace("÷", "/").replace("^", "**")
    text = re.sub(r"%\s*of\b", "%*", text)
    text = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"(\1/100)", text)
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except SyntaxError:
        raise ToolError(f"could not parse expression {expr!r}") from None
    try:
        value = _eval(tree)
    except ZeroDivisionError:
        raise ToolError("division by zero") from None
    except (ValueError, OverflowError, TypeError) as exc:
        raise ToolError(f"math error: {exc}") from None
    return {"expression": expr, "result": _num(value)}


@tool("math_statistics", "math", "Summary statistics of a list of numbers: count, sum, mean, median, min, max, standard deviation.",
      {"values": A("The numbers.", {"type": "number"})}, ("values",))
def math_statistics(store: StateStore, args: dict) -> dict:
    vals = args.get("values")
    if not isinstance(vals, list) or not vals or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
        raise ToolError("invalid values: expected a non-empty list of numbers")
    return {"count": len(vals), "sum": _num(float(sum(vals))), "mean": _num(statistics.fmean(vals)),
            "median": _num(float(statistics.median(vals))), "min": _num(float(min(vals))), "max": _num(float(max(vals))),
            "stdev": _num(statistics.stdev(vals)) if len(vals) > 1 else 0}


_UNIT_TABLE: dict[str, tuple[str, float]] = {}
for _dim, _rows in {
    "length": {"mm": .001, "millimeter": .001, "cm": .01, "centimeter": .01, "m": 1, "meter": 1, "metre": 1,
               "km": 1000, "kilometer": 1000, "kilometre": 1000, "in": .0254, "inch": .0254, "ft": .3048,
               "foot": .3048, "feet": .3048, "yd": .9144, "yard": .9144, "mi": 1609.344, "mile": 1609.344,
               "nmi": 1852, "nautical mile": 1852},
    "mass": {"mg": 1e-6, "milligram": 1e-6, "g": .001, "gram": .001, "kg": 1, "kilogram": 1, "oz": .028349523125,
             "ounce": .028349523125, "lb": .45359237, "pound": .45359237, "st": 6.35029318, "stone": 6.35029318,
             "t": 1000, "tonne": 1000},
    "volume": {"ml": .001, "milliliter": .001, "millilitre": .001, "l": 1, "liter": 1, "litre": 1,
               "tsp": .00492892159375, "teaspoon": .00492892159375, "tbsp": .01478676478125,
               "tablespoon": .01478676478125, "cup": .2365882365, "fl oz": .0295735295625, "floz": .0295735295625,
               "fluid ounce": .0295735295625, "pt": .473176473, "pint": .473176473, "qt": .946352946,
               "quart": .946352946, "gal": 3.785411784, "gallon": 3.785411784},
    "speed": {"m/s": 1, "kph": 1 / 3.6, "km/h": 1 / 3.6, "mph": .44704, "knot": .514444, "kn": .514444},
    "area": {"m2": 1, "sq m": 1, "square meter": 1, "km2": 1e6, "sq km": 1e6, "ft2": .09290304, "sq ft": .09290304,
             "square foot": .09290304, "acre": 4046.8564224, "ha": 10000, "hectare": 10000, "mi2": 2589988.110336,
             "sq mi": 2589988.110336},
    "time": {"s": 1, "sec": 1, "second": 1, "min": 60, "minute": 60, "h": 3600, "hr": 3600, "hour": 3600,
             "day": 86400, "week": 604800},
    "data": {"b": 1, "byte": 1, "kb": 1e3, "kilobyte": 1e3, "mb": 1e6, "megabyte": 1e6, "gb": 1e9, "gigabyte": 1e9,
             "tb": 1e12, "terabyte": 1e12},
}.items():
    for _u, _f in _rows.items():
        _UNIT_TABLE[_u] = (_dim, _f)
_TEMPS = {"c": "c", "celsius": "c", "°c": "c", "f": "f", "fahrenheit": "f", "°f": "f", "k": "k", "kelvin": "k"}


def _unit(name: str) -> tuple[str, float | str]:
    u = " ".join(name.strip().lower().split())
    if u in _TEMPS:
        return "temperature", _TEMPS[u]
    for cand in (u, u[:-1] if u.endswith("s") else u, u[:-2] if u.endswith("es") else u):
        if cand in _UNIT_TABLE:
            return _UNIT_TABLE[cand]
    if u == "feet":
        return _UNIT_TABLE["ft"]
    raise ToolError(f"unknown unit {name!r}")


@tool("units_convert", "math", "Convert a value between units of length, mass, volume, temperature, speed, area, time or data size.",
      {"value": N("The amount to convert."), "from_unit": S("Unit to convert from, e.g. km, lb, cup, F."),
       "to_unit": S("Unit to convert to, e.g. mi, kg, ml, C.")},
      ("value", "from_unit", "to_unit"))
def units_convert(store: StateStore, args: dict) -> dict:
    v = arg_num(args, "value")
    (d1, f1), (d2, f2) = _unit(need_str(args, "from_unit")), _unit(need_str(args, "to_unit"))
    if d1 != d2:
        raise ToolError(f"cannot convert {d1} ({args['from_unit']}) to {d2} ({args['to_unit']})")
    if d1 == "temperature":
        c = {"c": v, "f": (v - 32) * 5 / 9, "k": v - 273.15}[f1]
        out = {"c": c, "f": c * 9 / 5 + 32, "k": c + 273.15}[f2]
    else:
        out = v * f1 / f2
    return {"value": v, "from_unit": args["from_unit"], "to_unit": args["to_unit"], "result": _num(round(float(out), 6))}


# ================================================================== time
def _zone(name: str | None, store: StateStore, key: str) -> ZoneInfo:
    if not name:
        return store.clock.zone
    try:
        return ZoneInfo(name.strip())
    except (ZoneInfoNotFoundError, ValueError):
        raise ToolError(f"invalid {key}: unknown time zone {name!r}; use an IANA name such as Europe/Paris") from None


@tool("time_get_current", "time", "Get the current local date, time and weekday, optionally in another time zone.",
      {"timezone": S("IANA time zone, e.g. Asia/Tokyo (default: the user's zone).")})
def time_get_current(store: StateStore, args: dict) -> dict:
    z = _zone(arg_str(args, "timezone"), store, "timezone")
    now = store.clock.aware().astimezone(z)
    return {"now": fmt(now), "weekday": now.strftime("%A"), "timezone": str(z), "utc_offset": now.strftime("%z")}


@tool("time_convert_timezone", "time", "Convert a time from one time zone to another.",
      {"time": S("HH:MM (today) or a local ISO date and time."), "to_timezone": S("IANA zone to convert to, e.g. Europe/London."),
       "from_timezone": S("IANA zone the time is in (default: the user's zone).")},
      ("time", "to_timezone"))
def time_convert_timezone(store: StateStore, args: dict) -> dict:
    raw = need_str(args, "time").strip()
    src = _zone(arg_str(args, "from_timezone"), store, "from_timezone")
    dst = _zone(arg_str(args, "to_timezone"), store, "to_timezone")
    if ":" in raw and len(raw) <= 5:
        h, m = parse_hhmm(raw, "time")
        local = datetime.combine(store.clock.aware().astimezone(src).date(), time(h, m))
    else:
        local = parse_when(store, raw, "time")[0]
    out = local.replace(tzinfo=src).astimezone(dst)
    return {"from": fmt(local), "from_timezone": str(src), "to": fmt(out), "to_timezone": str(dst),
            "weekday": out.strftime("%A")}


@tool("date_add", "time", "Add (or with negative numbers, subtract) years, months, weeks, days, hours or minutes to a date or time.",
      {"date": S("Start date (YYYY-MM-DD) or local ISO time."), "years": I("Years to add."), "months": I("Months to add."),
       "weeks": I("Weeks to add."), "days": I("Days to add."), "hours": I("Hours to add."), "minutes": I("Minutes to add.")},
      ("date",))
def date_add(store: StateStore, args: dict) -> dict:
    dt, date_only = parse_when(store, args["date"], "date")
    months = (arg_int(args, "years", 0) or 0) * 12 + (arg_int(args, "months", 0) or 0)
    if months:
        y, m = divmod(dt.month - 1 + months, 12)
        y += dt.year
        dt = dt.replace(year=y, month=m + 1, day=min(dt.day, _cal.monthrange(y, m + 1)[1]))
    hm = (arg_int(args, "hours", 0) or 0) * 60 + (arg_int(args, "minutes", 0) or 0)
    dt += timedelta(weeks=arg_int(args, "weeks", 0) or 0, days=arg_int(args, "days", 0) or 0, minutes=hm)
    keep_date = date_only and not hm
    return {"result": dt.date().isoformat() if keep_date else fmt(dt), "weekday": dt.strftime("%A")}


@tool("date_difference", "time", "The time between two dates or times, in days, hours and minutes.",
      {"start": S("Earlier date or local ISO time."), "end": S("Later date or local ISO time.")}, ("start", "end"))
def date_difference(store: StateStore, args: dict) -> dict:
    a = parse_when(store, args["start"], "start")[0]
    b = parse_when(store, args["end"], "end")[0]
    mins = int((b - a).total_seconds() // 60)
    sign = -1 if mins < 0 else 1
    d, rem = divmod(abs(mins), 1440)
    return {"total_minutes": mins, "days": sign * d, "hours": sign * (rem // 60), "minutes": sign * (rem % 60),
            "total_days": round(mins / 1440, 4), "weeks": round(mins / 10080, 4)}


# ================================================================== stocks
@tool("stocks_get_quote", "finance", "Get the latest price and daily change for a stock ticker symbol.",
      {"symbol": S("Ticker symbol, e.g. ACME.")}, ("symbol",))
def stocks_get_quote(store: StateStore, args: dict) -> dict:
    sym = need_str(args, "symbol").strip().upper()
    for r in store.get("stocks"):
        if str(r.get("id", "")).upper() == sym or str(r.get("symbol", "")).upper() == sym:
            out = clean(r)
            out["symbol"] = sym
            return out
    raise ToolError(f"unknown symbol {sym!r}", outcome="no_match")
