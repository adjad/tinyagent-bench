"""Load a benchmark profile and render it for a given day.

``load_profile("student")`` returns profile.json with every date token rendered for the
anchor date and an absolute ``ts`` (epoch seconds) added to each timed item.
``load_profile("student", today=date(2026, 11, 17))`` rebases it to another day first.

Times are wall-clock: they resolve in the host's local zone unless ``tz`` is given
(see 09-profile-schema.md §2).
"""
from __future__ import annotations

import copy
import json
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

from scripts.bench_profiles.lib import TOKEN_RE

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "test_fixtures" / "bench_profiles"
PROFILE_IDS = ("student", "tech", "mixed")

_DOW = {"en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
        "es": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]}
_MON = {"en": ["January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December"],
        "es": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
               "septiembre", "octubre", "noviembre", "diciembre"]}


def render_tokens(text: str, anchor: date) -> str:
    """Replace every {{dow:±N[:lang]}} / {{date:±N[:fmt[:lang]]}} in ``text``."""
    def sub(m) -> str:
        kind, off = m.group(1), int(m.group(2))
        d = anchor + timedelta(days=off)
        if kind == "dow":
            return _DOW[m.group(3) or "en"][d.weekday()]
        fmt, lang = m.group(3) or "long", m.group(4) or "en"
        if fmt == "iso":
            return d.isoformat()
        if fmt == "num":
            return f"{d.month}/{d.day}"
        if fmt == "short":
            return f"{_MON[lang][d.month - 1][:3]} {d.day}"
        if lang == "es":
            return f"{_DOW['es'][d.weekday()]} {d.day} de {_MON['es'][d.month - 1]}"
        return f"{_DOW['en'][d.weekday()]}, {_MON['en'][d.month - 1]} {d.day}"
    return TOKEN_RE.sub(sub, text)


def _walk(obj: Any, fn) -> Any:
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, list):
        return [_walk(v, fn) for v in obj]
    if isinstance(obj, dict):
        return {k: _walk(v, fn) for k, v in obj.items()}
    return obj


def resolve(anchor_date: date, day: int, hhmm: str | None, tz: str | None = None) -> float:
    """Epoch seconds for (anchor_date + day, HH:MM) in the host zone (or ``tz``)."""
    d = anchor_date + timedelta(days=day)
    t = dtime(int(hhmm[:2]), int(hhmm[3:5])) if hhmm else dtime(0, 0)
    if tz and ZoneInfo is not None:
        return datetime.combine(d, t, tzinfo=ZoneInfo(tz)).timestamp()
    return datetime.combine(d, t).timestamp()


def read_raw(profile_id: str, root: Path | None = None) -> tuple[dict, dict]:
    base = (root or FIXTURES) / profile_id
    profile = json.loads((base / "profile.json").read_text(encoding="utf-8"))
    ledger = json.loads((base / "ledger.json").read_text(encoding="utf-8"))
    return profile, ledger


def render(profile: dict, today: date | None = None, tz: str | None = None) -> dict:
    """A rendered copy of ``profile``: tokens filled in, ``anchor.date`` moved to ``today``,
    and ``ts`` / ``end_ts`` / ``due_ts`` added (epoch seconds)."""
    p = copy.deepcopy(profile)
    anchor = today or date.fromisoformat(p["anchor"]["date"])
    p["anchor"] = {**p["anchor"], "date": anchor.isoformat(),
                   "weekday": _DOW["en"][anchor.weekday()]}
    p = _walk(p, lambda s: render_tokens(s, anchor) if "{{" in s else s)
    p["anchor"]["now_ts"] = resolve(anchor, 0, p["anchor"]["now"], tz)
    for th in p["threads"]:
        for m in th["messages"]:
            m["ts"] = resolve(anchor, m["day"], m["time"], tz)
    for coll in ("emails", "notes", "documents", "browser"):
        for r in p[coll]:
            r["ts"] = resolve(anchor, r["day"], r["time"], tz)
    for e in p["events"]:
        e["ts"] = resolve(anchor, e["day"], e["time"], tz)
        if e["all_day"]:
            e["end_ts"] = resolve(anchor, e["day"] + e.get("days", 1), None, tz)
        elif e.get("end"):
            e["end_ts"] = resolve(anchor, e.get("end_day", e["day"]), e["end"], tz)
    for r in p["reminders"]:
        r["due_ts"] = resolve(anchor, r["day"], r["time"], tz) if r["day"] is not None else None
    for m in p["memory"]:
        m["ts"] = resolve(anchor, m["day"], "09:00", tz)
    return p


def render_ledger(ledger: dict, anchor: date) -> dict:
    return _walk(ledger, lambda s: render_tokens(s, anchor) if "{{" in s else s)


def load_profile(profile_id: str, today: date | None = None, tz: str | None = None,
                 root: Path | None = None) -> dict:
    profile, _ = read_raw(profile_id, root)
    return render(profile, today, tz)


def load_ledger(profile_id: str, today: date | None = None, root: Path | None = None) -> dict:
    profile, ledger = read_raw(profile_id, root)
    return render_ledger(ledger, today or date.fromisoformat(profile["anchor"]["date"]))
