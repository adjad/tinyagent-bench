#!/usr/bin/env python3
"""Export the portable quality subset to ios-bench/Resources/portable-cases.json.

Select/abstain cases are taken from Harbor's Toolbench YAML (scripts/toolbench/cases);
extract/summary cases are authored below (invented data, persona "Test User" at
test.user@example.com, .example/.invalid domains only). Tool schemas come from the real
registry in its terse form (the descriptions the model was trained on).

    HOME=$(mktemp -d) python ios-bench/tools/export_portable_cases.py \
        --exported-at 2026-11-18T00:00:00Z

The exporter points HOME at a fresh empty directory itself before importing `service.*`,
so nothing reads user data. Output is deterministic apart from `exported_at`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO / "ios-bench" / "Resources" / "portable-cases.json"
SCHEMA = "edgebench.portable-cases/0.1"
MAX_TOKENS = 1024

# "{today}" is filled by each runner with the run day's local date as "Wednesday 2026-11-18"
# (English weekday, ISO date), so date_offset and weekday cases do not depend on a guess.
SYSTEM_PROMPT = (
    "Today is {today}. You are a helpful assistant on the user's own device. When a request needs one of the "
    "provided tools, call the single best tool with accurate arguments; use ISO 8601 for "
    "dates and times. When no tool fits, answer directly and briefly. When asked for JSON "
    "only, reply with just the JSON object."
)

# (case id, family) picked from Toolbench. Order here is irrelevant; output is sorted.
SELECT_IDS = [
    "cal_book_vet", "cal_plumber_call", "cal_when_is_midterm", "cal_next_10_days",
    "cal_block_duration_lc", "rem_water_tonight", "rem_nudge_invoice", "rem_call_mom_sunday",
    "rem_timesheet_friday", "mail_draft_mom", "mail_draft_landlord", "msg_mom_said",
    "notes_append_power_bank", "notes_jot", "files_move_photos", "files_read_notes_txt",
    "web_msft", "web_open_sunday", "dev_timer_set_label", "dev_open_spotify", "dev_time_tokyo",
    "dev_convert_km_miles", "contact_save_number", "day_plan_tomorrow_typo",
]
ABSTAIN_IDS = ["chat_joke", "chat_wording", "mem_not_fact_sister_moving", "chat_define"]
DISTRACTORS = ["find_free_time"]
SMOKE = ["rem_water_tonight", "dev_time_tokyo", "web_open_sunday", "chat_define",
         "extract_receipt", "summary_library"]

AUTHORED = [
    {"id": "extract_receipt", "family": "extract",
     "prompt": ("Here is a receipt.\n\nBriar & Pine Hardware (store 0417)\nReceipt BP-20931\n"
                "Date: 2026-03-14\nCashier: Rowan Ibe\n2 x cedar plank 6ft   $18.40\n"
                "1 x wood glue         $6.25\nSubtotal $24.65\nTax $1.97\nTOTAL $26.62\n"
                "Paid by card ending 4444.\n\nReply with only a JSON object with the keys "
                "\"store\", \"receipt_number\", \"date\" and \"total\"."),
     "fields": {"store": {"icontains": "briar"}, "receipt_number": {"icontains": "BP-20931"},
                "date": {"regex": "2026-03-14"}, "total": {"regex": "26\\.62"}}},
    {"id": "extract_flight", "family": "extract",
     "prompt": ("Itinerary for Test User (test.user@example.com)\n\nFlight KX 482\n"
                "Depart: Harbor Field (HFD) on 2026-05-09 at 07:45\n"
                "Arrive: Northgate International (NGI) on 2026-05-09 at 10:20\n"
                "Seat 14C, confirmation code QZ7M3L\n\nReply with only a JSON object with the "
                "keys \"flight\", \"from\", \"to\", \"depart_time\" and \"confirmation\"."),
     "fields": {"flight": {"regex": "KX\\s?482"}, "from": {"icontains": "HFD"},
                "to": {"icontains": "NGI"}, "depart_time": {"icontains": "07:45"},
                "confirmation": {"eq": "QZ7M3L"}}},
    {"id": "extract_meeting", "family": "extract",
     "prompt": ("From: Mira Okafor <mira.okafor@example.com>\nTo: test.user@example.com\n"
                "Subject: Quarterly planning moved\n\nHi Test User, planning has moved to "
                "Thursday 2026-06-04 at 14:30 in room Birch 2. Please bring the draft "
                "roadmap and the Lumen Labs cost estimate. The call-in line is 555-0142 "
                "if you can't make it in person.\n\nReply with only a JSON object with the "
                "keys \"sender\", \"date\", \"time\", \"room\" and \"dial_in\"."),
     "fields": {"sender": {"icontains": "okafor"}, "date": {"regex": "2026-06-04"},
                "time": {"regex": "14:30|2:30"}, "room": {"icontains": "birch"},
                "dial_in": {"regex": "555-?0142"}}},
    {"id": "extract_parcel", "family": "extract",
     "prompt": ("Subject: Your parcel is on its way\n\nHello Test User,\nParcel 8841-DK-27 "
                "from Tern & Company shipped with Swiftline Courier. Estimated delivery: "
                "2026-04-22 between 09:00 and 13:00. Weight 2.3 kg. Track it at "
                "https://track.swiftline.example/8841-DK-27\n\nReply with only a JSON object "
                "with the keys \"tracking_number\", \"carrier\", \"delivery_date\" and "
                "\"weight_kg\"."),
     "fields": {"tracking_number": {"icontains": "8841-DK-27"}, "carrier": {"icontains": "swiftline"},
                "delivery_date": {"regex": "2026-04-22"}, "weight_kg": {"regex": "^2\\.3"}}},
    {"id": "summary_library", "family": "summary",
     "prompt": ("Summarize the following in at most 40 words.\n\n"
                "The Elmgrove branch library announced on Monday that it will close for "
                "renovation from 3 February until the end of June. The work, funded by a "
                "municipal grant of 1.2 million, will replace the roof, add a quiet study "
                "wing and widen the main entrance so wheelchairs and strollers can pass "
                "easily. During the closure, borrowers can return books at the Cedar Street "
                "branch, and a bookmobile will visit the Elmgrove market square every "
                "Saturday morning. Staff will move to Cedar Street, so opening hours there "
                "will extend by two hours on weekdays. Children's story time continues "
                "without interruption in the community hall on Fridays at 4 pm. The head "
                "librarian, Tomasz Varga, said residents had asked for more seating and "
                "quieter rooms for years and that the project answers both requests. "
                "Contractors expect noise only on weekdays, and the car park will stay open "
                "except for a fenced section near the delivery gate. A public viewing of the "
                "plans takes place at the town hall on 20 January. The library will reopen "
                "on 1 July with a free open day, and late fees accrued during the closure "
                "will be waived for every member."),
     "contains_all": ["3 February", "Cedar Street", "1 July"],
     "not_contains": ["September"]},
    {"id": "summary_trial", "family": "summary",
     "prompt": ("Summarize the following in at most 40 words.\n\n"
                "Researchers at the Halden Institute reported results from a twelve-month "
                "trial of four-day school weeks in nine rural districts. The trial enrolled "
                "about 3,400 students and compared attendance, test scores and teacher "
                "turnover with nine similar districts that kept five-day weeks. Attendance "
                "rose by 6 percent in the four-day districts, and teacher turnover fell from "
                "14 percent to 9 percent. Reading scores were unchanged, while maths scores "
                "dipped slightly, by about 1.5 points on a 100-point scale, a difference the "
                "authors called statistically weak. Families reported that the extra day off "
                "was useful for appointments but a burden for those without childcare, so "
                "three districts opened a supervised activity day paid for by the "
                "institute. Lead author Dr. Ines Marchetti cautioned that the districts were "
                "small and that results may not carry over to cities. The authors recommend "
                "a second trial lasting three years and a closer look at pupils who receive "
                "free meals, who relied most on the missing fifth day. Funding came from a "
                "regional education foundation, and the full data will be posted online "
                "next spring."),
     "contains_all": ["attendance", "turnover", "three years"],
     "not_contains": ["scores rose"]},
    {"id": "summary_ferry", "family": "summary",
     "prompt": ("Summarize the following in at most 40 words.\n\n"
                "The Sundial Island ferry operator said on Wednesday that it will switch to a "
                "winter timetable from 12 October. Crossings between Port Marlow and the "
                "island will drop from eight a day to five, with the first boat leaving at "
                "7:10 am and the last at 5:40 pm. Fares remain unchanged at 9 for adults "
                "and 4 for children, but the operator is ending its cash payment option "
                "and will accept only cards and the Sundial travel pass. The change follows "
                "a fall in passenger numbers of almost a third since August and the planned "
                "refit of the larger vessel, the Aurelia, which will be out of service until "
                "March. A smaller boat, the Wren, will cover the route and cannot carry "
                "more than six cars, so vehicle bookings must be made at least two days "
                "ahead. Island residents who depend on the early crossing for work were "
                "invited to a meeting at the community hall on 2 October, where the "
                "operator's manager, Beatriz Calloway, will take questions. Freight "
                "deliveries will continue on Tuesdays and Fridays, and a water taxi can be "
                "arranged for emergencies by calling the harbour office."),
     "contains_all": ["12 October", "five", "Wren"],
     "not_contains": ["cash only"]},
]


def _terse_schemas(names: list[str]) -> list[dict]:
    """OpenAI-style schemas, in the terse form the model was trained on."""
    os.environ["HARBOR_TERSE_TOOL_SCHEMAS"] = "1"
    sys.path.insert(0, str(REPO))
    import service.tools  # noqa: F401  (registers the tools)
    from service.tools.registry import REGISTRY, tool_schemas
    missing = [n for n in names if n not in REGISTRY]
    if missing:
        raise SystemExit(f"tools missing from the registry: {missing}")
    schemas = tool_schemas(sorted(names))
    got = {s["function"]["name"] for s in schemas}
    if got != set(names):
        raise SystemExit(f"tools unavailable in the registry: {sorted(set(names) - got)}")
    return schemas


def _toolbench_cases() -> dict:
    sys.path.insert(0, str(REPO))
    from scripts.toolbench.cases import load_cases
    return {c.id: c for c in load_cases()}


def _check_portable(c, want_no_tool: bool) -> None:
    raw = repr((c.prompt, c.expect))
    if c.history or c.access_needed or c.refused or c.access != "full":
        raise SystemExit(f"{c.id}: has history/world/refused/access, not portable")
    if re.search(r"\{[a-z_][a-z_0-9]*\}", raw):
        raise SystemExit(f"{c.id}: has a {{fact}} placeholder")
    if want_no_tool and not c.no_tool:
        raise SystemExit(f"{c.id}: not a no_tool case")
    if not want_no_tool and (c.no_tool or len(c.expect) != 1):
        raise SystemExit(f"{c.id}: not a single-step expect")


def _category_file(c) -> str:
    return f"{c.category}.yaml"


def build_bundle(exported_at: str) -> dict:
    tb = _toolbench_cases()
    cases = []
    tools_used: set[str] = set()
    for cid in SELECT_IDS:
        c = tb[cid]
        _check_portable(c, False)
        step = c.expect[0]
        expect = {"kind": "tool_call"}
        if len(step.tools) == 1:
            expect["tool"] = step.tools[0]
        else:
            expect["any_of"] = list(step.tools)
        if step.args:
            expect["args"] = dict(step.args)
        tools_used.update(step.tools)
        cases.append({"id": cid, "family": "select",
                      "source": f"toolbench:{_category_file(c)}#{cid} ({c.split})",
                      "messages": [{"role": "user", "content": c.prompt}],
                      "tool_set": "core", "max_tokens": MAX_TOKENS, "expect": expect})
    for cid in ABSTAIN_IDS:
        c = tb[cid]
        _check_portable(c, True)
        cases.append({"id": cid, "family": "abstain",
                      "source": f"toolbench:{_category_file(c)}#{cid} ({c.split})",
                      "messages": [{"role": "user", "content": c.prompt}],
                      "tool_set": "core", "max_tokens": MAX_TOKENS, "expect": {"kind": "no_tool"}})
    for a in AUTHORED:
        if a["family"] == "extract":
            expect = {"kind": "json", "fields": a["fields"]}
        else:
            words = len(a["prompt"].split("\n\n", 1)[1].split())
            if not 150 <= words <= 300:
                raise SystemExit(f"{a['id']}: passage is {words} words, want 150-300")
            expect = {"kind": "text", "contains_all": a["contains_all"],
                      "not_contains": a["not_contains"], "max_words": 60}
        cases.append({"id": a["id"], "family": a["family"], "source": "authored",
                      "messages": [{"role": "user", "content": a["prompt"]}],
                      "tool_set": None, "max_tokens": MAX_TOKENS, "expect": expect})
    order = {"select": 0, "abstain": 1, "extract": 2, "summary": 3}
    cases.sort(key=lambda c: (order[c["family"]], c["id"]))
    ids = [c["id"] for c in cases]
    if len(set(ids)) != len(ids):
        raise SystemExit("duplicate case ids")
    if any(s not in ids for s in SMOKE):
        raise SystemExit("smoke id missing")
    commit = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True).stdout.strip() or "unknown"
    menu = sorted(tools_used | set(DISTRACTORS))
    return {
        "schema": SCHEMA,
        "source": {
            "harness": "harbor-toolbench", "repo_commit": commit, "exported_at": exported_at,
            "exporter": "ios-bench/tools/export_portable_cases.py",
            "note": ("Portable subset: single-step select and abstain cases from Toolbench, plus "
                     "authored extract and summary cases. One neutral system prompt and one fixed "
                     "tool menu (terse schemas); scores are comparable phone-vs-Mac, not with full "
                     "Toolbench numbers."),
        },
        "system_prompt": SYSTEM_PROMPT,
        "tool_sets": {"core": _terse_schemas(menu)},
        "sets": {"smoke": list(SMOKE), "parity": ids},
        "cases": cases,
    }


def render(bundle: dict) -> str:
    return json.dumps(bundle, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--exported-at", default=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    args = ap.parse_args(argv)
    # Never read the real home directory while importing Harbor's service package.
    os.environ["HOME"] = tempfile.mkdtemp(prefix="portable-export-home-")
    text = render(build_bundle(args.exported_at))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out} ({len(text)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
