"""Image and audio parts in chat messages.

A message's `content` is either a string or a list of OpenAI-style parts:

    {"type": "text", "text": "..."}
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
    {"type": "input_audio", "input_audio": {"data": "<base64>", "format": "wav"}}

Every client takes such lists unchanged. The stub records a short summary of
each media part (kind, MIME type or format, byte count, sha256) instead of the
base64 payload, so test logs stay small. The oMLX and OpenRouter clients pass
the list through as is; whether a given server or model accepts images or
audio is not checked here (untested against real servers).
"""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path

IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
              ".webp": "image/webp"}
AUDIO_FORMATS = ("wav", "mp3", "flac", "ogg", "m4a")


def _bytes(data: bytes | str | Path) -> tuple[bytes, str | None]:
    if isinstance(data, (str, Path)):
        p = Path(data)
        return p.read_bytes(), p.suffix.lower()
    return bytes(data), None


def text_part(text: str) -> dict:
    return {"type": "text", "text": text}


def image_part(data: bytes | str | Path, mime: str | None = None) -> dict:
    """An image as a data URL. `data` is bytes or a file path."""
    raw, suffix = _bytes(data)
    mime = mime or IMAGE_MIME.get(suffix or "", None)
    if not mime or not mime.startswith("image/"):
        raise ValueError("image_part needs an image MIME type")
    return {"type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{base64.b64encode(raw).decode()}"}}


def image_url_part(url: str) -> dict:
    """An image by URL (only for hosted models; a local server may not fetch it)."""
    return {"type": "image_url", "image_url": {"url": url}}


def audio_part(data: bytes | str | Path, fmt: str | None = None) -> dict:
    """Audio as base64 in an `input_audio` part. `data` is bytes or a file path."""
    raw, suffix = _bytes(data)
    fmt = (fmt or (suffix or "").lstrip(".")).lower()
    if fmt not in AUDIO_FORMATS:
        raise ValueError(f"audio_part needs a format in {AUDIO_FORMATS}")
    return {"type": "input_audio", "input_audio": {"data": base64.b64encode(raw).decode(), "format": fmt}}


def user_message(*parts: str | dict) -> dict:
    """A user message from text and media parts; plain strings become text parts."""
    return {"role": "user", "content": [text_part(p) if isinstance(p, str) else p for p in parts]}


def message_text(msg: dict) -> str:
    """The text of a message, joining text parts and skipping media."""
    c = msg.get("content")
    if isinstance(c, list):
        return "\n".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text")
    return c or ""


def summarise_part(part: dict) -> dict:
    """A small, payload-free description of a media part (text parts are kept)."""
    t = part.get("type")
    if t == "image_url":
        url = (part.get("image_url") or {}).get("url", "")
        if url.startswith("data:"):
            head, _, b64 = url.partition(",")
            raw = base64.b64decode(b64 or "")
            return {"type": t, "mime": head[5:].split(";")[0], "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest()}
        return {"type": t, "url": url}
    if t == "input_audio":
        a = part.get("input_audio") or {}
        raw = base64.b64decode(a.get("data") or "")
        return {"type": t, "format": a.get("format"), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    return dict(part)


def summarise_message(msg: dict) -> dict:
    c = msg.get("content")
    if isinstance(c, list):
        return {**msg, "content": [summarise_part(p) if isinstance(p, dict) else p for p in c]}
    return dict(msg)


def media_parts(messages: list[dict]) -> list[dict]:
    """Summaries of every image and audio part in `messages`, in order."""
    out = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            out += [summarise_part(p) for p in c if isinstance(p, dict) and p.get("type") in ("image_url", "input_audio")]
    return out
