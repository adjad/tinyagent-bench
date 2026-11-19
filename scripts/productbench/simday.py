"""Play a scripted day against the isolated assistant and log when each job ran.

The performance spec (04, section 9.1) measures battery and energy over a
simulated day; the agentic spec (02, section 11.4) writes the script. This
module is the in-assistant driver: it plays each scripted event through the
real backend (Brief build, Ask turns) on a timetable and writes one log line
per event and per idle gap, with wall-clock and monotonic start and end
times, so power samples taken by another process can be lined up with it.
It measures no power itself.

Script, `edat-sim-day-v1` (agentic spec 11.4):

    {"schema": "edat-sim-day-v1", "profile_id": "student", "day_start": "08:00",
     "tz": "America/Chicago", "mode": "live", "lang": "en",
     "events": [{"t": "08:00:00", "kind": "brief"},
                {"t": "08:15:00", "kind": "task", "ref": "Q-L-student-03"},
                {"t": "09:00:00", "kind": "mail_digest",
                 "world_events_before": [{"kind": "email", "from_name": "…", "from_addr": "…",
                                          "subject": "…", "body": "…"}]},
                {"t": "17:30:00", "kind": "review"}]}

Kinds: brief (Harbor's Brief build), task (`ref` = a case id of this profile,
or an inline `prompt`), mail_digest, message_digest, review. An event may carry
its own `prompt`. `world_events_before` adds synthetic mail (`kind: email`) or
messages (`kind: message`, `from`, `text`, optional `thread_id`) to the sandbox
just before the event. Only `mode: live` runs here: the assistant's own calls
against the world (teacher-forced `forced` mode belongs to the agentic harness).

Pace: `real` keeps the scripted times (a 10-hour day); `scaled` multiplies
every gap by `scale` (0.2: about 2 hours); `compressed` runs events back to
back with a fixed `gap_s` between them (the performance spec's method; idle
energy is composed afterwards from measured idle power). The backend's clock
starts at `day_start` and then runs in real time, so outside `real` pace the
assistant's "now" falls behind the script; both are logged.

`default_script(profile, cases)` builds the 11.4 shape (1 brief, 32 tasks, 9
mail digests, 4 message digests, 1 review) from the profile's own Ask cases,
for when the agentic script is not at hand.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path

import httpx

from scripts.productbench.score_ask import DRAFT_ACTIONS

SCHEMA = "edat-sim-day-v1"
KINDS = ("brief", "task", "mail_digest", "message_digest", "review")
PROMPTS = {
    "mail_digest": "Summarise the new email that came in since my last check.",
    "message_digest": "Summarise the new messages since my last check.",
    "review": "What got done today, and what moves to tomorrow?",
}
AUX = ("embed", "rerank")


class ScriptError(ValueError):
    pass


def _secs(t: str) -> int:
    parts = [int(x) for x in t.split(":")]
    while len(parts) < 3:
        parts.append(0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def load(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    check(data)
    return data


def check(data: dict) -> None:
    if data.get("schema") != SCHEMA:
        raise ScriptError(f"schema must be {SCHEMA}")
    if data.get("lang", "en") != "en":
        raise ScriptError("English only")
    if data.get("mode", "live") != "live":
        raise ScriptError("only live mode is played inside the assistant")
    last = -1
    for i, e in enumerate(data.get("events", [])):
        if e.get("kind") not in KINDS:
            raise ScriptError(f"event {i}: kind must be one of {KINDS}")
        s = _secs(e["t"])
        if s < last:
            raise ScriptError(f"event {i}: times must not go backwards")
        last = s
        if e["kind"] == "task" and not (e.get("ref") or e.get("prompt")):
            raise ScriptError(f"event {i}: a task needs ref or prompt")


def schedule(data: dict, pace: str, *, scale: float = 0.2, gap_s: float = 30.0) -> list[float | None]:
    """Planned start, in seconds after the day starts, per event (None: right after the previous
    one plus gap_s, for compressed pace)."""
    start = _secs(data.get("day_start", "08:00"))
    if pace == "real":
        return [float(_secs(e["t"]) - start) for e in data["events"]]
    if pace == "scaled":
        return [scale * (_secs(e["t"]) - start) for e in data["events"]]
    if pace == "compressed":
        return [None for _ in data["events"]]
    raise ScriptError(f"pace must be real, scaled or compressed, not {pace}")


def prompt_for(event: dict, cases_by_id: dict[str, dict]) -> str | None:
    if event.get("prompt"):
        return event["prompt"]
    if event["kind"] == "task":
        c = cases_by_id.get(event.get("ref") or "")
        return c["prompt"] if c else None
    return PROMPTS.get(event["kind"])


def default_script(profile, cases: dict) -> dict:
    """The agentic spec's day shape from this profile's own cases (invented arrivals)."""
    asks = [c for c in cases.get("ask", []) if not c.get("act") and not c.get("privacy")
            and not c.get("pref_case")]
    ids = [c["id"] for c in asks] or ["inline"]
    events: list[dict] = [{"t": "08:00:00", "kind": "brief"}]
    k = 0

    def task(t):
        nonlocal k
        ref = ids[k % len(ids)]
        k += 1
        return {"t": t, "kind": "task", "ref": ref} if ref != "inline" else \
            {"t": t, "kind": "task", "prompt": "What's next on my calendar?"}
    for m in (15, 25, 40, 50):
        events.append(task(f"08:{m:02d}:00"))
    clusters = [("09", 20), ("10", 10), ("11", 0), ("11", 40), ("13", 15), ("14", 10), ("15", 0),
                ("15", 40), ("16", 30)]
    sizes = [3, 3, 3, 3, 4, 3, 3, 3, 3]
    for (h, m), n in zip(clusters, sizes):
        for j in range(n):
            events.append(task(f"{h}:{m + j:02d}:30"))
    for h in range(9, 18):
        events.append({"t": f"{h:02d}:00:00", "kind": "mail_digest",
                       "world_events_before": _arrivals(profile, h)})
    for h in (10, 12, 14, 16):
        events.append({"t": f"{h:02d}:05:00", "kind": "message_digest",
                       "world_events_before": [{"kind": "message", "from": _a_contact(profile, h),
                                                "text": f"Quick check-in, are we still on for later? ({h}:00)"}]})
    events.append({"t": "17:30:00", "kind": "review"})
    events = [e for e in events if not (e["kind"] == "task" and "12:00:00" <= e["t"] < "13:00:00")]
    events.sort(key=lambda e: _secs(e["t"]))
    return {"schema": SCHEMA, "profile_id": profile.id, "day_start": "08:00",
            "tz": profile.data["timezone"], "mode": "live", "lang": "en", "events": events,
            "generated": "productbench default_script (the agentic spec's 11.4 shape, this profile's cases)"}


def _a_contact(profile, i: int) -> str:
    cs = profile.data.get("contacts", [])
    return cs[i % len(cs)]["name"] if cs else "A friend"


def _arrivals(profile, hour: int) -> list[dict]:
    n = 2 + hour % 4
    return [{"kind": "email", "from_name": f"Sim Sender {hour}-{j}",
             "from_addr": f"sender{hour}{j}@simday.example",
             "subject": f"Update {hour}:{j:02d}", "body": f"A short synthetic note number {j} for {hour}:00."}
            for j in range(n)]


def engine_resident(engine_port: int | None) -> dict:
    """Models the engine holds right now (read-only GET), with the helper models apart."""
    if not engine_port:
        return {"available": False}
    try:
        r = httpx.get(f"http://127.0.0.1:{engine_port}/v1/models/status", timeout=3)
        rows = r.json().get("models", []) if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError):
        return {"available": False}
    loaded = [m.get("id") for m in rows if m.get("loaded")]
    aux = [m for m in loaded if m and any(a in m.lower() for a in AUX)]
    return {"available": True, "loaded": loaded, "aux_models_resident": aux}


def _iso(ts: float, tz) -> str:
    return datetime.fromtimestamp(ts, tz).isoformat(timespec="seconds")


async def play(bench, script: dict, cases: dict, log: Path, *, pace: str = "compressed",
               scale: float = 0.2, gap_s: float = 30.0, engine_port: int | None = None,
               on_event=None) -> list[dict]:
    """Play the day. One JSON line per event and per idle gap in `log`."""
    from scripts.productbench.capture import TeeTransport
    by_id = {c["id"]: c for c in cases.get("ask", [])}
    plan = schedule(script, pace, scale=scale, gap_s=gap_s)
    tz = bench.profile.tz
    rows: list[dict] = []
    t0 = time.monotonic()
    session = None

    def write(row):
        rows.append(row)
        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    tee = TeeTransport()
    async with httpx.AsyncClient(transport=tee) as client:
        for i, ev in enumerate(script["events"]):
            planned = plan[i]
            now = time.monotonic() - t0
            wait = (planned - now) if planned is not None else (gap_s if i else 0.0)
            if wait > 0:
                idle_start = time.time()
                await asyncio.sleep(wait)
                write({"i": i, "kind": "idle", "start_epoch": idle_start, "end_epoch": time.time(),
                       "seconds": round(wait, 3)})
            for w in ev.get("world_events_before") or []:
                await _arrive(client, bench, w)
            start_epoch, start_mono, bench_start = time.time(), time.monotonic(), bench.bench_now()
            resident_before = engine_resident(engine_port)
            row = {"i": i, "t": ev["t"], "kind": ev["kind"], "ref": ev.get("ref"),
                   "planned_offset_s": planned, "late_s": round(max(0.0, -(wait)), 3) if planned is not None else 0.0,
                   "start_epoch": start_epoch, "bench_start": _iso(bench_start, tz),
                   "world_events": len(ev.get("world_events_before") or [])}
            try:
                if ev["kind"] == "brief" and not ev.get("prompt"):
                    cap = await bench.brief(client)
                    row.update({"status": "ok", "usage": cap.get("usage"),
                                "brief_state": (cap.get("snapshot") or {}).get("state")})
                else:
                    prompt = prompt_for(ev, by_id)
                    if prompt is None:
                        row.update({"status": "skipped", "error": f"unknown ref {ev.get('ref')!r}"})
                    else:
                        turn = await bench.ask(client, tee, f"day-{i}", prompt, session_id=session)
                        row.update({"status": "error" if turn.get("error") else "ok", "prompt": prompt,
                                    "error": turn.get("error") or None, "answer": turn["answer"][:400],
                                    "tools": turn["tools"], "usage": turn.get("usage"),
                                    "first_token_s": turn.get("first_token_s"),
                                    "unsafe_actions": len([a for a in turn.get("actions") or []
                                                           if a.get("type") not in DRAFT_ACTIONS])})
            except Exception as exc:  # noqa: BLE001 — log and keep the day going
                row.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
            row.update({"end_epoch": time.time(), "elapsed_s": round(time.monotonic() - start_mono, 3),
                        "bench_end": _iso(bench.bench_now(), tz),
                        "aux_models_resident": sorted(set(resident_before.get("aux_models_resident") or [])
                                                      | set(engine_resident(engine_port).get(
                                                          "aux_models_resident") or []))
                        if resident_before.get("available") else None})
            write(row)
            if on_event:
                on_event(row)
    return rows


async def _arrive(client: httpx.AsyncClient, bench, w: dict) -> None:
    if w.get("kind") == "email":
        body = {k: w[k] for k in ("from_name", "from_addr", "to", "cc", "subject", "body", "account") if k in w}
        await client.post(bench.sandbox + "/sandbox/emails", json=body, timeout=10)
    elif w.get("kind") == "message":
        body = {k: w[k] for k in ("thread_id", "from", "text") if k in w}
        await client.post(bench.sandbox + "/sandbox/messages", json=body, timeout=10)


def summarize(rows: list[dict]) -> dict:
    ev = [r for r in rows if r["kind"] != "idle"]
    idle = [r for r in rows if r["kind"] == "idle"]
    by_kind: dict[str, int] = {}
    for r in ev:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
    return {"events": len(ev), "by_kind": by_kind,
            "ok": sum(1 for r in ev if r.get("status") == "ok"),
            "errors": sum(1 for r in ev if r.get("status") == "error"),
            "skipped": sum(1 for r in ev if r.get("status") == "skipped"),
            "active_s": round(sum(r.get("elapsed_s") or 0 for r in ev), 1),
            "idle_s": round(sum(r.get("seconds") or 0 for r in idle), 1),
            "unsafe_actions": sum(r.get("unsafe_actions") or 0 for r in ev),
            "aux_models_seen": sorted({m for r in ev for m in r.get("aux_models_resident") or []}),
            "first_start_epoch": ev[0]["start_epoch"] if ev else None,
            "last_end_epoch": ev[-1]["end_epoch"] if ev else None}
