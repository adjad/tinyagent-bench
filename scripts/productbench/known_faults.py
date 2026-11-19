"""Known capture faults: inputs the harness or the sandbox world got wrong, not the model.

A record carrying one of these is not evidence about the model on that point. The runner stamps
`known_faults` on each record as it is written; the findings page reads them back (and finds the
same faults in older records from their captured tool results, so earlier runs are marked too).

  mail_texts_unanalysed     a tool told the model mail and texts were not analysed yet: a home
                            that asked before any Brief cycle had read them (fixed: each such
                            home now runs one unscored Brief first)
  reminder_receipt_untrusted  a reminder write returned no receipt Harbor trusts: the sandbox's
                            reminders writer, not the model (open)
  availability_not_ready    find_free_time said Calendar and Reminders were not ready in the
                            sandbox (open)

Run-level gaps (a surface not run, a fault that touches every record of one surface) go in the
run's meta.json under `known_gaps`.
"""
from __future__ import annotations

import json

SIGNS = {
    "mail_texts_unanalysed": "have not been analyzed yet",
    "reminder_receipt_untrusted": "did not return a verified completion receipt",
    "availability_not_ready": "could not check current Calendar and Reminders availability",
}

ABOUT = {
    "mail_texts_unanalysed": "the assistant had not yet analysed mail and texts in this trial home "
                             "(no Brief cycle ran before the question); the model could not see changes "
                             "announced only there",
    "reminder_receipt_untrusted": "a reminder write got no receipt Harbor trusts from the sandbox, so the "
                                  "model was told it failed",
    "availability_not_ready": "Harbor reported Calendar and Reminders as not ready in the sandbox, so free "
                              "time could not be checked",
}

GAPS = {
    "act_not_run": "act for me was not run: in the sandbox, saved contacts are not recognised in texts "
                   "(the Messages feed carries no chat id or service), so it would measure the sandbox",
}


def _turns(captured: dict) -> list[dict]:
    if not isinstance(captured, dict):
        return []
    if isinstance(captured.get("turns"), list):
        return [t for t in captured["turns"] if isinstance(t, dict)]
    return [captured]


def of_record(rec: dict) -> list[str]:
    """The known faults this record's own captured tool results show."""
    found = set()
    for turn in _turns(rec.get("captured") or {}):
        for call in turn.get("tool_calls") or []:
            text = json.dumps(call.get("result"), ensure_ascii=False) if isinstance(call, dict) else ""
            for fault, sign in SIGNS.items():
                if sign in text:
                    found.add(fault)
    return sorted(found)


def stamp(rec: dict) -> dict:
    faults = sorted(set(rec.get("known_faults") or []) | set(of_record(rec)))
    if faults:
        rec["known_faults"] = faults
    return rec
