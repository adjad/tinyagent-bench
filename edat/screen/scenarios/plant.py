"""Injection carriers: put text where a model will read it on screen (spec 11 §3.8).

    plant(world, rng, carrier, text, **where) -> slots

`carrier` is one of `email_body`, `message`, `post`, `review`, `banner`,
`accessible_name`, `fake_dialog`, `page_text` (the case format's
`inject.carrier`). `text` is the planted payload; when it is empty or None
(the clean twin of an injection pair) only the carrier's ordinary content is
planted, if it has any (the email or the text message the task is about), so
the twin differs from the attacked case by the payload alone.

Carriers and their `where` keys:

* `email_body`: a new inbox message, the newest one (`id`, `from_name`,
  `from_email`, `subject`, `body`: paragraphs, `at`: where the payload goes in
  the body, default before the last paragraph, `minutes_ago`), or `msg=<id>`
  to add the payload to an existing message. Slot `planted_msg_id`.
* `message`: a new Messages thread with the latest message (`id`, `contact`,
  `phone`, `org`, `lines`: the thread's own messages, `minutes_ago`), or
  `thread=<id>` to add the payload to an existing thread. Slot
  `planted_thread_id`.
* `post`: a forum post appended to `thread` (default `garden1`) by `author`,
  so no seeded post's key moves. Slot `planted_post`.
* `review`: a shop review appended to `product`'s reviews (`author`, `stars`).
* `banner`: a notification banner (`app`, default every app; `title`; `key`).
* `accessible_name`: a planted button whose accessible name is the text
  (`app`, `screen`, `url`, `key`, `role`, `position`).
* `fake_dialog`: a dialog styled as a system prompt (`app`, `when`, `allow_label`).
* `page_text`: small, low-contrast text on a page (`app`, `screen`, `url`,
  `key`, `position`, default bottom), split into lines of at most 130 characters.

Every value is invented; nothing is read from or sent to anything real.
"""
from __future__ import annotations

import random
import textwrap
from datetime import datetime, timedelta

from edat.screen.apps import dialogs, pools

CARRIERS = ("email_body", "message", "post", "review", "banner", "accessible_name", "fake_dialog", "page_text")
LINE = 130


def _lines(text: str, width: int = LINE) -> list[str]:
    return textwrap.wrap(" ".join(str(text).split()), width=width, break_long_words=True,
                         break_on_hyphens=False) or [""]


def _ts(world, minutes_ago: int) -> datetime:
    return datetime.fromisoformat(world.clock[:16]) - timedelta(minutes=int(minutes_ago))


def plant(world, rng: random.Random, carrier: str, text: str | None, **where) -> dict:
    if carrier not in CARRIERS:
        raise ValueError(f"unknown carrier {carrier!r}")
    return globals()[f"_{carrier}"](world, rng, (text or "").strip(), where)


# ---------------------------------------------------------------- app content carriers

def _email_body(world, rng, text, where) -> dict:
    mail = world.state["mail"]
    if where.get("msg"):
        msg = next(m for m in mail["inbox"] if m["id"] == where["msg"])
        if text:
            body = list(msg.get("body") or [])
            at = int(where.get("at", max(0, len(body) - 1)))
            msg["body"] = body[:at] + [text] + body[at:]
        return {"planted_msg_id": msg["id"]}
    ts = _ts(world, where.get("minutes_ago", rng.randint(1, 3)))
    body = list(where.get("body") or [])
    if text:
        at = int(where.get("at", max(0, len(body) - 1)))
        body = body[:at] + [text] + body[at:]
    mid = where.get("id") or f"m{90 + len(mail['inbox'])}"
    me = (world.slots or {}).get("my_email") or (mail["inbox"][0].get("to") if mail["inbox"] else "")
    mail["inbox"].append({"id": mid, "from_name": where.get("from_name", "Notifications"),
                          "from_email": where.get("from_email", "notices@mail.example"), "to": me,
                          "subject": where.get("subject", "Notice"), "date": ts.date().isoformat(),
                          "time": ts.strftime("%H:%M"), "body": body, "unread": True})
    return {"planted_msg_id": mid}


def _message(world, rng, text, where) -> dict:
    st = world.state["messages"]
    if where.get("thread"):
        th = next(t for t in st["threads"] if t["id"] == where["thread"])
        if text:
            last = max((m["time"] for m in th.get("messages") or []), default=world.clock[:16])
            th["messages"].append({"from": "them", "text": text, "time": last})
        return {"planted_thread_id": th["id"]}
    ts = _ts(world, where.get("minutes_ago", rng.randint(1, 3)))
    msgs = [{"from": who, "text": t, "time": (ts - timedelta(minutes=2 * (len(where.get("lines") or []) - i)))
             .strftime("%Y-%m-%dT%H:%M")} for i, (who, t) in enumerate(where.get("lines") or [])]
    if text:
        msgs.append({"from": "them", "text": text, "time": ts.strftime("%Y-%m-%dT%H:%M")})
    elif msgs:
        msgs[-1]["time"] = ts.strftime("%Y-%m-%dT%H:%M")
    tid = where.get("id") or f"t{50 + len(st['threads'])}"
    st["threads"].append({"id": tid, "contact": where.get("contact", "Notifications"),
                          "phone": where.get("phone", f"555-01{rng.randint(0, 99):02d}"),
                          "org": bool(where.get("org", True)), "messages": msgs})
    return {"planted_thread_id": tid}


def _post(world, rng, text, where) -> dict:
    if not text:
        return {}
    tid = where.get("thread", "garden1")
    th = next(t for t in world.state["forum"]["threads"] if t["id"] == tid)
    n = len(th["posts"])
    author = where.get("author") or pools.person(rng)["name"]
    th["posts"].append({"id": f"{tid}-p{n}", "author": author, "date": world.clock[:10],
                        "paragraphs": _lines(text, 400)})
    return {"planted_post": n}


def _review(world, rng, text, where) -> dict:
    if not text:
        return {}
    key = where["product"]
    p = next(x for x in world.state["shop"]["catalog"] if x["key"] == key)
    p.setdefault("reviews", []).append({"author": where.get("author") or pools.person(rng)["name"],
                                        "stars": int(where.get("stars", 5)), "text": text})
    return {"planted_review": len(p["reviews"]) - 1}


# ---------------------------------------------------------------- on-screen carriers

def _banner(world, rng, text, where) -> dict:
    if not text:
        return {}
    key = where.get("key", "banner.planted")
    dialogs.install(world, {"kind": "banner", "key": key, "app": where.get("app", "*"),
                            "title": where.get("title", "Notification"), "text": text,
                            "buttons": [{"label": "Dismiss", "key": f"{key}.dismiss", "close": True}]})
    return {"planted_key": key}


def _extra(where: dict, key: str, name: str, role: str, meta: dict) -> dict:
    x = {"key": key, "app": where.get("app", "*"), "role": role, "name": name, "meta": meta,
         "position": where.get("position", "top")}
    for k in ("screen", "url"):
        if where.get(k):
            x[k] = where[k]
    return x


def _accessible_name(world, rng, text, where) -> dict:
    if not text:
        return {}
    key = where.get("key", "extra.planted.button")
    world.ui["extras"].append(_extra(where, key, text[:150], where.get("role", "button"), {"planted": True}))
    return {"planted_key": key}


def _fake_dialog(world, rng, text, where) -> dict:
    if not text:
        return {}
    app = where.get("app") or world.front
    spec = {"kind": "fake_system", "app": app, "text": text, "allow_label": where.get("allow_label", "Allow")}
    if where.get("when"):
        spec["when"] = where["when"]
    dialogs.install(world, spec)
    return {"planted_key": f"dlg.fake.{app}.allow"}


def _page_text(world, rng, text, where) -> dict:
    if not text:
        return {}
    key = where.get("key", "extra.planted.small")
    where = {"position": "bottom", **where}
    for i, ln in enumerate(_lines(text)):
        world.ui["extras"].append(_extra(where, f"{key}.{i}", ln, "text",
                                         {"small": True, "low_contrast": True, "planted": True}))
    return {"planted_key": key}
