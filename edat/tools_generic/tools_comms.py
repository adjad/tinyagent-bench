"""Messages, email and contacts.

Reads `messages`, `mail`, `contacts`; writes `messages_sent`, `message_drafts`,
`mail_sent`, `mail_drafts`, `scheduled_sends`, `contacts` and the `unread` /
`folder` fields of `mail`.

Recipient resolution (sends, drafts, schedules, forwards):
* a contact id, an email address or a phone number is matched against
  `contacts`; an address or phone with no contact is still allowed;
* a name must match exactly one contact (every word of the name is the start of
  a word of the contact's name; an exact full-name match wins). Several matches
  are an error naming every candidate; none is an error. A message may also go
  to a group thread by its thread name.
The sent record keeps the resolved address in `to` (a phone for a message, a
list of addresses for email), the contact's name and its `contact_id`.
"""
from __future__ import annotations

import re
from datetime import datetime

from edat.state.store import StateStore
from edat.tools_generic.registry import (
    PAGING, TEXT_WINDOW, A, B, S, ToolError, arg_bool, arg_list, arg_str, clean, matches,
    need_str, norm_path, not_found, page, parse_dt, parse_when, record_dt, tool, fmt, window,
)

_PHONE_RE = re.compile(r"^\+?[\d\s().-]{7,}$")
_TOKEN = re.compile(r"[a-z0-9']+")


# ================================================================== recipients
def _digits(phone: str | None) -> str:
    """The last seven digits: "+1 555 0108", "555-0108" and "(555) 0108" are one number."""
    return re.sub(r"\D", "", phone or "")[-7:]


def address_of(sender: str | None) -> str:
    m = re.search(r"<([^>]+)>", sender or "")
    return (m.group(1) if m else (sender or "")).strip()


def _name_candidates(store: StateStore, name: str) -> list[dict]:
    q = _TOKEN.findall(name.lower())
    if not q:
        return []
    exact, partial = [], []
    for c in store.get("contacts"):
        n = _TOKEN.findall(str(c.get("name", "")).lower())
        if n == q:
            exact.append(c)
        elif all(any(w.startswith(x) for w in n) for x in q):
            partial.append(c)
    return exact if len(exact) == 1 else exact + partial


def _group_thread(store: StateStore, name: str) -> str | None:
    for m in store.get("messages"):
        if m.get("group") and str(m.get("thread", "")).lower() == name.lower():
            return m["thread"]
    return None


def resolve_recipient(store: StateStore, given, channel: str) -> dict:
    """Resolve one recipient for channel "message" or "email" (see module doc)."""
    raw = given.strip() if isinstance(given, str) else ""
    if not raw:
        raise ToolError("recipient must not be empty")
    contact = store.find("contacts", raw)
    if contact is None and "@" in raw:
        addr = address_of(raw)
        hit = next((c for c in store.get("contacts") if str(c.get("email", "")).lower() == addr.lower()), None)
        return {"address": addr, "name": hit.get("name") if hit else None,
                "contact_id": hit["id"] if hit else None}
    if contact is None and _PHONE_RE.match(raw) and len(_digits(raw)) >= 7:
        hit = next((c for c in store.get("contacts") if c.get("phone") and _digits(c["phone"]) == _digits(raw)), None)
        if channel == "message":
            return {"address": hit["phone"] if hit else raw, "name": hit.get("name") if hit else None,
                    "contact_id": hit["id"] if hit else None}
        if hit is None:
            raise ToolError(f"{raw} is a phone number; email needs an email address")
        contact = hit
    if contact is None:
        cands = _name_candidates(store, raw)
        if len(cands) > 1:
            listed = "; ".join(f"{c.get('name')} ({c['id']})" for c in cands)
            raise ToolError(f"{raw!r} matches {len(cands)} contacts: {listed}. "
                            "Say which one, or use a phone number or address.", outcome="needs_input")
        if not cands:
            thread = _group_thread(store, raw) if channel == "message" else None
            if thread:
                return {"address": thread, "name": thread, "contact_id": None, "thread": thread}
            raise ToolError(f"no contact matches {raw!r}. Search contacts, or use a phone number or address.",
                            outcome="no_match")
        contact = cands[0]
    if channel == "message":
        addr = contact.get("phone") or contact.get("email")
        what = "phone number"
    else:
        addr = contact.get("email")
        what = "email address"
    if not addr:
        raise ToolError(f"{contact.get('name')} ({contact['id']}) has no {what} in contacts")
    return {"address": addr, "name": contact.get("name"), "contact_id": contact["id"]}


def _thread_for(store: StateStore, rcpt: dict) -> str | None:
    if rcpt.get("thread"):
        return rcpt["thread"]
    name = str(rcpt.get("name") or "").lower()
    for m in store.get("messages"):
        if m.get("group"):
            continue
        t = str(m.get("thread", ""))
        if t == rcpt.get("contact_id") or (name and (t.lower() == name or str(m.get("from", "")).lower() == name)):
            return m["thread"]
        if not rcpt.get("contact_id") and _digits(t) and _digits(t) == _digits(rcpt["address"]):
            return m["thread"]
    return None


def _future(store: StateStore, value, key: str) -> str:
    when = parse_dt(store, value, key)
    if when <= store.clock.now:
        raise ToolError(f"invalid {key}: {fmt(when)} is not in the future (now is {store.clock.iso()})")
    return fmt(when)


_ATTACH = A("Optional paths of files to attach, e.g. ~/Documents/plan.pdf.")
_AFTER = S("Only items received on or after this date (YYYY-MM-DD) or local ISO time.")
_BEFORE = S("Only items received on or before this date (YYYY-MM-DD) or local ISO time.")


def _attachments(store: StateStore, args: dict) -> dict:
    paths = arg_list(args, "attachments")
    if not paths:
        return {}
    out = []
    for p in paths:
        np = norm_path(p)
        row = store.find("files", np)
        if row is None or row.get("kind") == "folder":
            raise ToolError(f"cannot attach {np}: no such file", outcome="no_match")
        out.append(np)
    return {"attachments": out}


def _received_filter(store: StateStore, args: dict):
    lo = hi = None
    if args.get("after") is not None:
        lo = parse_when(store, args["after"], "after")[0]
    if args.get("before") is not None:
        dt, d_only = parse_when(store, args["before"], "before")
        hi = dt.replace(hour=23, minute=59) if d_only else dt

    def ok(row: dict) -> bool:
        if lo is None and hi is None:
            return True
        r = record_dt(row.get("received"))
        return r is not None and (lo is None or r >= lo) and (hi is None or r <= hi)
    return ok


def _recent_first(rows: list[dict], key: str) -> list[dict]:
    return sorted(rows, key=lambda r: (record_dt(r.get(key)) or datetime.min, str(r.get("id"))), reverse=True)


# ================================================================== messages
_TO_MSG = S("Who to text: a contact name, a phone number, or a group thread name.")


def _message_record(store: StateStore, args: dict) -> dict:
    text = need_str(args, "text")
    rcpt = resolve_recipient(store, args["to"], "message")
    return {"to": rcpt["address"], "to_name": rcpt.get("name"), "contact_id": rcpt.get("contact_id"),
            "thread": _thread_for(store, rcpt), "text": text, **_attachments(store, args)}


@tool("messages_send", "messages", "Send a text message now. It is delivered immediately and cannot be unsent. "
      "To prepare a message without sending it, use messages_draft.",
      {"to": _TO_MSG, "text": S("The message text."), "attachments": _ATTACH}, ("to", "text"), effect=True, egress=True)
def messages_send(store: StateStore, args: dict) -> dict:
    row = _message_record(store, args)
    row["sent_at"] = store.clock.iso()
    return {"ok": True, "status": "sent", "message": store.add("messages_sent", row, prefix="msg")}


@tool("messages_draft", "messages", "Save a text message as a draft for the user to review. Nothing is sent.",
      {"to": _TO_MSG, "text": S("The message text."), "attachments": _ATTACH}, ("to", "text"), effect=True, egress=True)
def messages_draft(store: StateStore, args: dict) -> dict:
    row = _message_record(store, args)
    row["created_at"] = store.clock.iso()
    return {"ok": True, "status": "draft saved", "draft": store.add("message_drafts", row, prefix="mdraft")}


@tool("messages_schedule", "messages", "Schedule a text message to be sent automatically at a later time.",
      {"to": _TO_MSG, "text": S("The message text."), "send_at": S("When to send, local ISO time in the future.")},
      ("to", "text", "send_at"), effect=True, egress=True)
def messages_schedule(store: StateStore, args: dict) -> dict:
    row = _message_record(store, args)
    row.update(channel="message", when=_future(store, args["send_at"], "send_at"), status="queued")
    return {"ok": True, "scheduled": store.add("scheduled_sends", row, prefix="sched")}


@tool("messages_list_scheduled", "messages", "List scheduled messages and emails that have not been sent yet.",
      {"channel": S("Only this channel.", ["message", "email"]),
       "include_cancelled": B("Include cancelled items (default false)."), **PAGING})
def messages_list_scheduled(store: StateStore, args: dict) -> dict:
    ch = arg_str(args, "channel")
    cancelled = arg_bool(args, "include_cancelled", False)
    rows = [clean(r) for r in store.get("scheduled_sends")
            if (not ch or r.get("channel") == ch) and (cancelled or r.get("status", "queued") == "queued")]
    rows.sort(key=lambda r: (str(r.get("when")), str(r["id"])))
    return page(rows, args)


@tool("messages_cancel_scheduled", "messages", "Cancel a scheduled message or email before it is sent.",
      {"schedule_id": S("The scheduled item's id, from messages_list_scheduled.")}, ("schedule_id",), effect=True)
def messages_cancel_scheduled(store: StateStore, args: dict) -> dict:
    r = store.find("scheduled_sends", args["schedule_id"])
    if r is None:
        raise not_found("scheduled item", args["schedule_id"])
    if r.get("status", "queued") != "queued":
        raise ToolError(f"scheduled item {r['id']!r} is already {r.get('status')}")
    store.update("scheduled_sends", r["id"], status="cancelled")
    return {"ok": True, "scheduled": clean(r)}


@tool("messages_search", "messages", "Search received text messages by words in the text, sender or thread. Newest first.",
      {"query": S("Words to look for."), "thread": S("Only this thread."),
       "unread_only": B("Only unread messages (default false)."), "after": _AFTER, "before": _BEFORE, **PAGING},
      ("query",))
def messages_search(store: StateStore, args: dict) -> dict:
    thread = arg_str(args, "thread")
    unread = arg_bool(args, "unread_only", False)
    when = _received_filter(store, args)
    rows = [clean(m) for m in store.get("messages")
            if matches(args["query"], m, ("text", "from", "thread")) and when(m)
            and (not thread or str(m.get("thread", "")).lower() == thread.lower())
            and (not unread or m.get("unread"))]
    return page(_recent_first(rows, "received"), args)


@tool("messages_list_threads", "messages", "List message threads with their latest message and unread count, newest first.",
      {"unread_only": B("Only threads with unread messages (default false)."), **PAGING})
def messages_list_threads(store: StateStore, args: dict) -> dict:
    threads: dict[str, dict] = {}
    for m in _recent_first(list(store.get("messages")), "received"):
        t = str(m.get("thread"))
        th = threads.get(t)
        if th is None:
            th = threads[t] = {"thread": m.get("thread"), "group": bool(m.get("group")), "last_from": m.get("from"),
                               "last_text": m.get("text"), "last_received": m.get("received"), "unread": 0, "count": 0}
        th["count"] += 1
        th["unread"] += 1 if m.get("unread") else 0
    rows = [t for t in threads.values() if not arg_bool(args, "unread_only", False) or t["unread"]]
    return page(rows, args)


@tool("messages_read_thread", "messages", "Read the messages in one thread, oldest first.",
      {"thread": S("Thread id or name, from messages_list_threads."), **PAGING}, ("thread",))
def messages_read_thread(store: StateStore, args: dict) -> dict:
    t = need_str(args, "thread").lower()
    rows = [clean(m) for m in store.get("messages") if str(m.get("thread", "")).lower() == t]
    if not rows:
        raise ToolError(f"no thread named {args['thread']!r}", outcome="no_match")
    rows = list(reversed(_recent_first(rows, "received")))
    return page(rows, args)


@tool("messages_list_drafts", "messages", "List saved text-message drafts.", {**PAGING})
def messages_list_drafts(store: StateStore, args: dict) -> dict:
    return page([clean(d) for d in store.get("message_drafts")], args)


@tool("messages_delete_draft", "messages", "Delete a saved text-message draft.",
      {"draft_id": S("The draft id, from messages_list_drafts.")}, ("draft_id",), effect=True)
def messages_delete_draft(store: StateStore, args: dict) -> dict:
    if store.find("message_drafts", args["draft_id"]) is None:
        raise not_found("draft", args["draft_id"])
    store.remove("message_drafts", args["draft_id"])
    return {"ok": True, "deleted": args["draft_id"]}


# ================================================================== email
_TO_MAIL = A("Recipients: contact names or email addresses.")


def _mail_recipients(store: StateStore, args: dict, key: str, required: bool) -> list[dict]:
    given = arg_list(args, key) or []
    if required and not given:
        raise ToolError(f"invalid {key}: give at least one recipient")
    return [resolve_recipient(store, g, "email") for g in given]


def _mail_fields(rcpts: list[dict], cc: list[dict]) -> dict:
    return {"to": [r["address"] for r in rcpts], "to_names": [r.get("name") for r in rcpts],
            "contact_id": rcpts[0].get("contact_id") if rcpts else None,
            "contact_ids": [r.get("contact_id") for r in rcpts], "cc": [r["address"] for r in cc]}


def _mail(store: StateStore, mid: str) -> dict:
    m = store.find("mail", mid)
    if m is None:
        raise not_found("email", mid)
    return m


def _mail_summary(m: dict) -> dict:
    body = str(m.get("body") or "")
    return {"id": m["id"], "from": m.get("from"), "to": m.get("to"), "subject": m.get("subject"),
            "received": m.get("received"), "unread": bool(m.get("unread")), "folder": m.get("folder") or "inbox",
            "snippet": body[:160]}


_MAIL_ID = S("The email's id, from email_search or email_list.")


@tool("email_send", "email", "Send an email now. It cannot be unsent. To prepare an email without sending it, use email_draft.",
      {"to": _TO_MAIL, "subject": S("Subject line."), "body": S("Email text."), "cc": A("Optional CC recipients."),
       "attachments": _ATTACH},
      ("to", "subject", "body"), effect=True, egress=True)
def email_send(store: StateStore, args: dict) -> dict:
    rcpts, cc = _mail_recipients(store, args, "to", True), _mail_recipients(store, args, "cc", False)
    row = {**_mail_fields(rcpts, cc), "subject": arg_str(args, "subject") or "", "body": need_str(args, "body"),
           "kind": "send", "sent_at": store.clock.iso(), **_attachments(store, args)}
    return {"ok": True, "status": "sent", "email": store.add("mail_sent", row, prefix="sent")}


@tool("email_draft", "email", "Save an email as a draft for the user to review. Nothing is sent.",
      {"to": _TO_MAIL, "subject": S("Subject line."), "body": S("Email text."), "cc": A("Optional CC recipients."),
       "attachments": _ATTACH},
      ("to", "subject", "body"), effect=True, egress=True)
def email_draft(store: StateStore, args: dict) -> dict:
    rcpts, cc = _mail_recipients(store, args, "to", True), _mail_recipients(store, args, "cc", False)
    row = {**_mail_fields(rcpts, cc), "subject": arg_str(args, "subject") or "", "body": need_str(args, "body"),
           "created_at": store.clock.iso(), **_attachments(store, args)}
    return {"ok": True, "status": "draft saved", "draft": store.add("mail_drafts", row, prefix="draft")}


@tool("email_reply", "email", "Send a reply to a received email now. With reply_all, everyone else on the email is copied.",
      {"message_id": _MAIL_ID, "body": S("Reply text."), "reply_all": B("Also reply to the other recipients (default false)."),
       "attachments": _ATTACH},
      ("message_id", "body"), effect=True, egress=True)
def email_reply(store: StateStore, args: dict) -> dict:
    m = _mail(store, args["message_id"])
    sender = address_of(m.get("from"))
    rcpts = [resolve_recipient(store, sender, "email")]
    cc = []
    if arg_bool(args, "reply_all", False):
        cc = [resolve_recipient(store, a, "email") for a in list(m.get("to") or []) + list(m.get("cc") or [])
              if address_of(a).lower() != sender.lower()]
    subject = str(m.get("subject") or "")
    row = {**_mail_fields(rcpts, cc), "subject": subject if subject.lower().startswith("re:") else f"Re: {subject}",
           "body": need_str(args, "body"), "kind": "reply", "in_reply_to": m["id"], "sent_at": store.clock.iso(),
           **_attachments(store, args)}
    return {"ok": True, "status": "sent", "email": store.add("mail_sent", row, prefix="sent")}


@tool("email_forward", "email", "Forward a received email, with an optional note, to other people now.",
      {"message_id": _MAIL_ID, "to": _TO_MAIL, "note": S("Optional text to put above the forwarded email.")},
      ("message_id", "to"), effect=True, egress=True)
def email_forward(store: StateStore, args: dict) -> dict:
    m = _mail(store, args["message_id"])
    rcpts = _mail_recipients(store, args, "to", True)
    note = arg_str(args, "note") or ""
    body = (f"{note}\n\n" if note else "") + (f"---------- Forwarded message ----------\nFrom: {m.get('from')}\n"
                                               f"Subject: {m.get('subject')}\n\n{m.get('body') or ''}")
    subject = str(m.get("subject") or "")
    row = {**_mail_fields(rcpts, []), "subject": subject if subject.lower().startswith("fwd:") else f"Fwd: {subject}",
           "body": body, "kind": "forward", "in_reply_to": m["id"], "sent_at": store.clock.iso()}
    return {"ok": True, "status": "sent", "email": store.add("mail_sent", row, prefix="sent")}


@tool("email_schedule", "email", "Schedule an email to be sent automatically at a later time.",
      {"to": _TO_MAIL, "subject": S("Subject line."), "body": S("Email text."),
       "send_at": S("When to send, local ISO time in the future.")},
      ("to", "subject", "body", "send_at"), effect=True, egress=True)
def email_schedule(store: StateStore, args: dict) -> dict:
    rcpts = _mail_recipients(store, args, "to", True)
    row = {**_mail_fields(rcpts, []), "subject": arg_str(args, "subject") or "", "text": need_str(args, "body"),
           "channel": "email", "when": _future(store, args["send_at"], "send_at"), "status": "queued"}
    return {"ok": True, "scheduled": store.add("scheduled_sends", row, prefix="sched")}


@tool("email_search", "email", "Search emails by words in the sender, recipients, subject or body. Newest first.",
      {"query": S("Words to look for."), "folder": S("Only this folder.", ["inbox", "archive", "trash"]),
       "unread_only": B("Only unread emails (default false)."), "after": _AFTER, "before": _BEFORE, **PAGING},
      ("query",))
def email_search(store: StateStore, args: dict) -> dict:
    folder = arg_str(args, "folder")
    unread = arg_bool(args, "unread_only", False)
    when = _received_filter(store, args)
    rows = [m for m in store.get("mail") if matches(args["query"], m, ("from", "to", "subject", "body")) and when(m)
            and (not folder or (m.get("folder") or "inbox") == folder) and (not unread or m.get("unread"))]
    return page([_mail_summary(m) for m in _recent_first(rows, "received")], args)


@tool("email_list", "email", "List emails in a folder, newest first.",
      {"folder": S("Folder (default inbox).", ["inbox", "archive", "trash"]),
       "unread_only": B("Only unread emails (default false)."), **PAGING})
def email_list(store: StateStore, args: dict) -> dict:
    folder = arg_str(args, "folder") or "inbox"
    unread = arg_bool(args, "unread_only", False)
    rows = [m for m in store.get("mail") if (m.get("folder") or "inbox") == folder and (not unread or m.get("unread"))]
    return page([_mail_summary(m) for m in _recent_first(rows, "received")], args)


@tool("email_read", "email", "Read one email in full. Long bodies can be read in parts with offset and limit.",
      {"message_id": _MAIL_ID, **TEXT_WINDOW}, ("message_id",))
def email_read(store: StateStore, args: dict) -> dict:
    m = _mail(store, args["message_id"])
    head = {k: m.get(k) for k in ("id", "from", "to", "cc", "subject", "received") if m.get(k) is not None}
    head["folder"] = m.get("folder") or "inbox"
    win = window(str(m.get("body") or ""), args)
    return {**head, "body": win.pop("text"), **win}


@tool("email_mark_read", "email", "Mark an email as read, or as unread with read=false.",
      {"message_id": _MAIL_ID, "read": B("true to mark read (default), false to mark unread.")},
      ("message_id",), effect=True)
def email_mark_read(store: StateStore, args: dict) -> dict:
    m = _mail(store, args["message_id"])
    store.update("mail", m["id"], unread=not arg_bool(args, "read", True))
    return {"ok": True, "id": m["id"], "unread": m["unread"]}


@tool("email_archive", "email", "Move an email to the archive folder. It is kept, not deleted.",
      {"message_id": _MAIL_ID}, ("message_id",), effect=True)
def email_archive(store: StateStore, args: dict) -> dict:
    m = _mail(store, args["message_id"])
    store.update("mail", m["id"], folder="archive")
    return {"ok": True, "id": m["id"], "folder": "archive"}


@tool("email_trash", "email", "Move an email to the trash folder.", {"message_id": _MAIL_ID}, ("message_id",), effect=True)
def email_trash(store: StateStore, args: dict) -> dict:
    m = _mail(store, args["message_id"])
    store.update("mail", m["id"], folder="trash")
    return {"ok": True, "id": m["id"], "folder": "trash"}


@tool("email_list_drafts", "email", "List saved email drafts.", {**PAGING})
def email_list_drafts(store: StateStore, args: dict) -> dict:
    return page([clean(d) for d in store.get("mail_drafts")], args)


@tool("email_send_draft", "email", "Send a saved email draft now, exactly as it was saved.",
      {"draft_id": S("The draft id, from email_list_drafts.")}, ("draft_id",), effect=True, egress=True)
def email_send_draft(store: StateStore, args: dict) -> dict:
    d = store.find("mail_drafts", args["draft_id"])
    if d is None:
        raise not_found("draft", args["draft_id"])
    if not d.get("to"):
        raise ToolError(f"draft {d['id']!r} has no recipients")
    store.remove("mail_drafts", d["id"])
    row = clean(d, "id", "created_at")
    row.update(kind="send", from_draft=d["id"], sent_at=store.clock.iso())
    return {"ok": True, "status": "sent", "email": store.add("mail_sent", row, prefix="sent")}


# ================================================================== contacts
_CONTACT_ID = S("The contact id, from contacts_search or contacts_list.")
_CONTACT_FIELDS = ("name", "phone", "email", "relation", "birthday", "notes")


def _contact(store: StateStore, cid: str) -> dict:
    c = store.find("contacts", cid)
    if c is None:
        raise not_found("contact", cid)
    return c


def _by_name(rows: list[dict]) -> list[dict]:
    return sorted((clean(c) for c in rows), key=lambda c: (str(c.get("name", "")).lower(), str(c["id"])))


@tool("contacts_search", "contacts", "Search contacts by words in the name, phone, email or relation (e.g. 'sister').",
      {"query": S("Words to look for."), **PAGING}, ("query",))
def contacts_search(store: StateStore, args: dict) -> dict:
    return page(_by_name([c for c in store.get("contacts") if matches(args["query"], c, ("name", "phone", "email", "relation"))]), args)


@tool("contacts_get", "contacts", "Get one contact's full details by id.", {"contact_id": _CONTACT_ID}, ("contact_id",))
def contacts_get(store: StateStore, args: dict) -> dict:
    return {"contact": clean(_contact(store, args["contact_id"]))}


@tool("contacts_list", "contacts", "List all contacts in name order.", {**PAGING})
def contacts_list(store: StateStore, args: dict) -> dict:
    return page(_by_name(store.get("contacts")), args)


@tool("contacts_create", "contacts", "Add a new contact.",
      {"name": S("Full name."), "phone": S("Phone number."), "email": S("Email address."),
       "relation": S("Relation to the user, e.g. 'dentist'."), "birthday": S("Birthday, YYYY-MM-DD."),
       "notes": S("Free-text notes.")},
      ("name",), effect=True)
def contacts_create(store: StateStore, args: dict) -> dict:
    row = {"name": need_str(args, "name")}
    for k in _CONTACT_FIELDS[1:]:
        if arg_str(args, k):
            row[k] = arg_str(args, k)
    return {"ok": True, "contact": store.add("contacts", row, prefix="c")}


@tool("contacts_update", "contacts", "Change fields of an existing contact. Only the fields you pass change.",
      {"contact_id": _CONTACT_ID, "name": S("New full name."), "phone": S("New phone number."),
       "email": S("New email address."), "relation": S("New relation."), "notes": S("New notes.")},
      ("contact_id",), effect=True)
def contacts_update(store: StateStore, args: dict) -> dict:
    c = _contact(store, args["contact_id"])
    changes = {k: arg_str(args, k) for k in _CONTACT_FIELDS if args.get(k) is not None}
    if not changes:
        raise ToolError("nothing to update: pass at least one field to change")
    store.update("contacts", c["id"], **changes)
    return {"ok": True, "contact": clean(c)}


@tool("contacts_delete", "contacts", "Delete a contact.", {"contact_id": _CONTACT_ID}, ("contact_id",), effect=True)
def contacts_delete(store: StateStore, args: dict) -> dict:
    c = _contact(store, args["contact_id"])
    store.remove("contacts", c["id"])
    return {"ok": True, "deleted": c["id"], "name": c.get("name")}
