"""The wire formats real Swift readers push to /assistant/sync/* — as pure
render functions, no I/O, no clock.

This is the SINGLE implementation of every format documented across
MailReader.swift / MessagesReader.swift / NotesReader.swift /
BrowserHistoryReader.swift, shared by scripts/harbor_testdata.py (in-place
DB/cache seeding) and the sandbox (service/fixtures used to follow this
plan — see the sandbox package once it lands). Two formats had drifted
between the fixtures and what the real backend parsers actually expect
(group-chat context labels, the email read/unread flag) before this module
existed; keeping one implementation is what stops that happening again.

Every function takes plain dicts with ALREADY-RESOLVED epoch timestamps —
the caller owns the clock — and returns the exact string
service/tools/{imessage,email,notes,browser_history}_tools.py's parsers
expect. Each format is pinned against those real parsers in
tests/test_sandbox_wire.py.
"""
from __future__ import annotations

import json
import re

from service.tools.imessage_tools import _norm_handle

FS = "\x01"  # field separator (email_raw, notes)
RS = "\x02"  # record separator (email_raw, notes)


def thread_context(name: str | None, members: list[str], is_group: bool) -> str:
    """Matches MessagesReader.swift's `label(...)` exactly:
      - named group   -> Group "Name"
      - unnamed group -> Group of N (a, b, c, d, +K more)
      - named 1:1     -> the contact/handle name
    `members` should be the OTHER participants' display names/handles (not
    including "Me"). imessage_tools._label_members's regex depends on the
    exact "Group of N (...)" shape, including the ", +K more" suffix only
    appearing past 4 members.
    """
    name = (name or "").strip()
    if name:
        return f'Group "{name}"' if is_group else name
    if is_group:
        shown = sorted(members)[:4]
        more = f", +{len(members) - 4} more" if len(members) > 4 else ""
        return f"Group of {len(members)} ({', '.join(shown)}{more})"
    return members[0] if members else "Unknown"


def messages_lines(rows: list[dict]) -> str:
    """rows: {ts, context, who, text} -> "{ts} | {context} | {who}: {text}"
    joined by "\\n", NO trailing newline, newest-first — matches
    MessagesReader.swift's own merge order. `text` has embedded newlines
    flattened to spaces, same as `oneLine` in the Swift reader.

    Parsed by imessage_tools._parse_lines: `line.split(" | ", 2)` then the
    third field is split again on the FIRST ":" to recover who/text — so a
    colon inside the body text is safe (only the label's own colon matters),
    but a " | " inside `text` or `who` is NOT — same constraint the real
    reader has.
    """
    ordered = sorted(rows, key=lambda r: r["ts"], reverse=True)
    lines = []
    for r in ordered:
        text = str(r["text"]).replace("\r", " ").replace("\n", " ")
        lines.append(f"{r['ts']} | {r['context']} | {r['who']}: {text}")
    return "\n".join(lines)


# MessagesReader.swift's structured-record bounds (Apple silicon build).
STRUCTURED_LIMIT = 2000
STRUCTURED_BYTE_LIMIT = 4_000_000
STRUCTURED_TEXT_LIMIT = 8192
STRUCTURED_LINK_LIMIT = 32
STRUCTURED_URL_LIMIT = 8192
STRUCTURED_WINDOW_DAYS = 365
STRUCTURED_ROW_LIMIT = 20000
SERVICES = ("iMessage", "SMS", "RCS")
_LITERAL_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def _v3(obj: dict) -> str:
    # JSONSerialization with .sortedKeys: compact, keys sorted, one line.
    return "V3 | " + json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def message_links(text: str) -> list[dict]:
    """The literal http(s) links in a message's text, as the reader's
    actualLinks reports them ({"url", "provenance": "literal_text"}), with
    trailing sentence punctuation left out. A simplification of the reader's
    scanner, which also balances brackets; every url is a substring of the text,
    which is what imessage_tools.structured_messages_snapshot requires."""
    out, seen = [], set()
    for match in _LITERAL_URL.finditer(text):
        url = match.group(0).rstrip(".,;:!?)]}")
        if url and url not in seen and len(url) <= STRUCTURED_URL_LIMIT:
            seen.add(url)
            out.append({"url": url, "provenance": "literal_text"})
    return out[:STRUCTURED_LINK_LIMIT]


def messages_structured(rows: list[dict]) -> list[str]:
    """rows: {ts, guid, chat_guid, sender, direction, text, service} -> the V3
    block MessagesReader.swift puts at the head of the Messages carrier: one
    coverage line, then one record line per message, newest first.

    `chat_guid` is the chat's GUID ("iMessage;-;+15551234567" for one-to-one,
    "iMessage;+;chat123..." for a group), `sender` the handle an incoming
    message came from ("Me" for the user's own, as the reader writes it), and
    `service` how Messages delivered it ("iMessage", "SMS", "RCS").

    Parsed by imessage_tools.structured_messages_snapshot and joined to the
    plain lines by timestamp in proactive/sources.text_records, which is where
    a text's chat, delivery service and sender handle come from."""
    ordered = sorted(rows, key=lambda r: r["ts"], reverse=True)
    records: list[str] = []
    used = truncated = 0
    for r in ordered:
        if len(records) >= STRUCTURED_LIMIT:
            truncated += 1
            continue
        text = str(r["text"])
        clipped = text[:STRUCTURED_TEXT_LIMIT]
        partial = len(clipped) < len(text)
        if partial:
            truncated += 1
        record = {"guid": r["guid"], "conversation": r["chat_guid"], "sender": r["sender"],
                  "direction": r["direction"], "timestamp": r["ts"], "text": clipped,
                  "links": message_links(clipped)}
        source = {"kind": "messages", "message_guid": r["guid"], "chat_guid": r["chat_guid"],
                  "navigation_url": None,
                  "service": r["service"] if r.get("service") in SERVICES else None}
        state = "partial" if partial else "complete"
        line = _v3({"version": 1, "kind": "record", "record": record, "source": source,
                    "coverage": {"text": state, "links": state}})
        size = len(line.encode("utf-8"))
        if used + size > STRUCTURED_BYTE_LIMIT:
            truncated += 1
            continue
        records.append(line)
        used += size
    coverage = {"version": 1, "kind": "coverage", "attempted": len(ordered),
                "emitted": len(records), "skipped": 0, "truncated": truncated,
                "limit": STRUCTURED_LIMIT, "byte_limit": STRUCTURED_BYTE_LIMIT,
                "window_days": STRUCTURED_WINDOW_DAYS, "row_limit": STRUCTURED_ROW_LIMIT,
                "reached_row_limit": len(ordered) == STRUCTURED_ROW_LIMIT}
    return [_v3(coverage)] + records


def contacts_map(raw: dict[str, str]) -> dict[str, str]:
    """raw handle (any format) -> name  =>  normalized handle -> name, the
    shape cache_contacts()/imessage_tools._contacts expects."""
    return {_norm_handle(h): name for h, name in raw.items() if h and name}


def contact_handles_map(raw: dict[str, str]) -> dict[str, list[str]]:
    """raw handle -> name  =>  lowercased name -> [original-form handles],
    the shape _name_handles expects (send_message needs the UN-normalized
    handle to actually address Messages/Mail)."""
    by_name: dict[str, list[str]] = {}
    for handle, name in raw.items():
        if handle and name:
            by_name.setdefault(str(name).strip().lower(), []).append(str(handle).strip())
    return by_name


def email_header_line(e: dict) -> str:
    """e: {ts, unread, account, sender_name, sender_addr, subject}
    -> "{ts} | {R|U} | {account} | {name} <{addr}> | {subject}"

    R/U is field 2 (not last) so subject can safely contain " | " — see
    email_tools._parse_pipe_lines's docstring for why."""
    flag = "U" if e.get("unread") else "R"
    sender = f"{e['sender_name']} <{e['sender_addr']}>"
    return f"{e['ts']} | {flag} | {e['account']} | {sender} | {e['subject']}"


def email_headers(rows: list[dict]) -> str:
    """Newest-first, joined "\\n", WITH a trailing "\\n" — matches
    MailReader.swift's mergeHeaderChunks exactly (headers and history share
    this renderer; the only difference is which rows the caller passes)."""
    ordered = sorted(rows, key=lambda r: r["ts"], reverse=True)
    if not ordered:
        return ""
    return "\n".join(email_header_line(e) for e in ordered) + "\n"


def email_raw(rows: list[dict]) -> str:
    """e: {ts, unread, account, sender_name, sender_addr, to: [addr,...],
    subject, message_id, body, account_id?} -> 9 FS-separated fields when a
    native account ID is supplied, otherwise the legacy 8 fields. Each
    record RS-TERMINATED (a trailing RS on the last record is correct —
    email_tools._parse_raw splits on RS and strips each piece).

    Field order: ts, R|U, account, optional account_id, "name <addr>", to(", "-joined), subject,
    message_id, body — matches MailReader.swift's rawScript exactly."""
    parts = []
    for e in rows:
        flag = "U" if e.get("unread") else "R"
        sender = f"{e['sender_name']} <{e['sender_addr']}>"
        to = ", ".join(e.get("to") or [])
        fields = [str(e["ts"]), flag, e["account"], sender, to,
                  e["subject"], e.get("message_id", ""), e["body"]]
        if "account_id" in e:
            fields.insert(3, e["account_id"])
        parts.append(FS.join(fields) + RS)
    return "".join(parts)


def notes_raw(rows: list[dict]) -> str:
    """n: {ts, title, folder, body} -> 4 FS-separated fields per record,
    each record RS-terminated. Matches NotesReader.swift's AppleScript
    exactly — no sort/merge, notes_tools._parse doesn't require one either."""
    parts = []
    for n in rows:
        fields = [str(n["ts"]), n["title"], n.get("folder", ""), n["body"]]
        parts.append(FS.join(fields) + RS)
    return "".join(parts)


def browser_lines(rows: list[dict]) -> str:
    """b: {ts, host, path, title} -> "{ts} | {host} | {path} | {title}"
    joined "\\n", newest-first — matches BrowserHistoryReader.swift's
    formatLine. Parsed via `line.split(" | ", 3)`."""
    ordered = sorted(rows, key=lambda r: r["ts"], reverse=True)
    return "\n".join(f"{r['ts']} | {r['host']} | {r['path']} | {r['title']}"
                     for r in ordered)
