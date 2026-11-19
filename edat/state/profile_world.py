"""The assistant lane over a synthetic benchmark profile instead of the canonical world.

    env = ProfileAssistantEnvironment("student", clock)        # clock from profile_clock("student")

`ProfileAssistantEnvironment` is `AssistantEnvironment` with one difference: its Toolbench `World` is
built from `test_fixtures/bench_profiles/<id>/profile.json` through
`scripts.bench_profiles.adapters.to_toolbench_world`, rebased to the case clock's date. Tools, schemas,
system prompt, `call`, `state()` and `seed()` are inherited unchanged, so a case may still add
`seed_records` on top of the profile (ids must not clash).

Record ids: the profile's ids replace the lane's slug ids, so a case, a ledger entry and
`state()` all name a record the same way.

  contacts   profile id (`ct-mom`)         events     profile id (`ev-gis-sync-bait`)
  mail       profile id (`em-cu-low`)      messages   profile id (`msg-jesso-surprise`)
  reminders  profile id (`rem-ra-email`)   notes      profile id (`note-pw-hint`)
  memory     profile id (`mem-10`)

Known limits (the lane, not this module): the Toolbench fakes keep wall-clock times with no zone, so
the profile's zone only labels the clock; `LOCAL_TZ` in the fakes stays America/Los_Angeles, which
matters only for the few tools that print it (day plans). Group threads exist in `messages` (they can
be read) but `send_message` reaches contacts only. Contact notes, calendar notes, weather and web
results do not come from the profile.
"""
from __future__ import annotations

from datetime import datetime

from edat.state.assistant_lane import AssistantEnvironment
from edat.state.clock import Clock


def profile_ids() -> tuple[str, ...]:
    from scripts.bench_profiles.render import PROFILE_IDS
    return PROFILE_IDS


def load_raw(profile_id: str) -> tuple[dict, dict]:
    """(profile, ledger), both raw, from the checked-in fixtures."""
    from scripts.bench_profiles.render import read_raw
    return read_raw(profile_id)


def profile_clock(profile_id: str) -> tuple[str, str]:
    """(ISO local time, zone) of the profile's own anchor: the case `world.clock` and `world.tz`."""
    profile, _ = load_raw(profile_id)
    a = profile["anchor"]
    return f"{a['date']}T{a['now']}", a["tz"]


class ProfileAssistantEnvironment(AssistantEnvironment):
    """`AssistantEnvironment` whose world is a benchmark profile."""

    def __init__(self, profile_id: str, clock: Clock | None = None, *, world_seed: int | None = None) -> None:
        if clock is None:
            iso, tz = profile_clock(profile_id)
            clock = Clock.parse(iso, tz)
        super().__init__(clock, world_seed=world_seed)      # the canonical world; replaced below
        from scripts.bench_profiles.adapters import to_toolbench_world
        from scripts.bench_profiles.render import render
        raw, _ = load_raw(profile_id)
        self.profile_id = profile_id
        w = to_toolbench_world(raw, today=clock.today, tz=clock.tz)
        w.now = datetime(clock.now.year, clock.now.month, clock.now.day, clock.now.hour, clock.now.minute)
        rendered = render(raw, clock.today, clock.tz)
        self._name_ids(w, rendered)
        superseded = [m for m in w.memory_facts if m.get("_edat_status") == "superseded"]
        w.memory_facts = [m for m in w.memory_facts if m.get("_edat_status") != "superseded"]
        w.snapshot()
        self.world = w
        self._fakes = self._W.make_fakes(w)
        self._used = {}
        self._msg_seq = 0
        self._completed = []
        self._cancelled_timers = []
        self._forgotten = []
        self._inactive_reminders = []
        self._inactive_memory = superseded      # superseded facts: in state() only, as in the lane's seeding
        self._outbound = {}
        self._file_meta = {}
        self._reserve_ids()
        self._tag()

    @staticmethod
    def _name_ids(w, p: dict) -> None:
        """Stamp the profile's ids on the World's records (same order the adapter built them)."""
        for c, row in zip(p["contacts"], w.contacts):
            row["_edat_id"] = c["id"]
        for e in w.events + w.past_events:
            if e.get("bench_id"):
                e["_edat_id"] = e["bench_id"]
        for e in w.emails:
            e["_edat_id"] = e["id"]
        for row in w.messages:
            if row.get("bench_id"):
                row["_edat_id"] = row["bench_id"]
        live = [r for r in p["reminders"] if not r["completed"]]
        for r, row in zip(live, w.reminders):
            row["_edat_id"] = r["id"]
        for n, row in zip(p["notes"], w.notes):
            row["_edat_id"] = n["id"]
        for m, row in zip(p["memory"], w.memory_facts):
            row["_edat_id"] = m["id"]
            if m.get("superseded_by"):
                row["_edat_status"] = "superseded"
                row.setdefault("_edat_meta", {})["superseded_by"] = m["superseded_by"]

    def _reserve_ids(self) -> None:
        w = self.world
        coll = {"calendar": [e.get("_edat_id") for e in w.events + w.past_events],
                "contacts": [c.get("_edat_id") for c in w.contacts],
                "mail": [m.get("_edat_id") for m in w.emails],
                "messages": [m.get("_edat_id") for m in w.messages],
                "reminders": [r.get("_edat_id") for r in w.reminders],
                "notes": [n.get("_edat_id") for n in w.notes],
                "memory": [m.get("_edat_id") for m in w.memory_facts]}
        for name, ids in coll.items():
            used = self._used.setdefault(name, set())
            used.update(i for i in ids if i is not None)
