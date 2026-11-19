"""Pillow rasteriser for the screenshot track: draws what `som.to_html` describes, with no browser.

Same 1,280 x 800 layout, same colours; text uses Pillow's embedded font (ASCII only, see
`ascii_label`). The same screen gives byte-identical PNG bytes: no metadata, no optimiser.
"""
from __future__ import annotations

import io
import unicodedata

import PIL
from PIL import Image, ImageDraw, features

from edat.media.vision.canvas import font
from edat.screen.render import som

RENDERER_ID = "edat-screen-raster/1"

BG, HDR_BG, DOCK_BG = (246, 246, 244), (232, 232, 230), (220, 220, 218)
INK, SMALL_INK, BORDER, WHITE, RED = (29, 29, 31), (154, 154, 154), (154, 154, 154), (255, 255, 255), (215, 38, 61)
_MAP = {"☑": "[x]", "☐": "[ ]", "●": "(on)", "○": "(off)", "·": "-", "–": "-", "—": "-", "‑": "-", "−": "-",
        "‘": "'", "’": "'", "“": '"', "”": '"', "…": "...", "×": "x", " ": " ", " ": " "}


def renderer_versions() -> dict:
    return {"renderer": RENDERER_ID, "pillow": PIL.__version__, "freetype": features.version("freetype2")}


def ascii_label(s: str) -> str:
    """Readable ASCII for the embedded font: symbols mapped, accents dropped, the rest '?'."""
    out = []
    for ch in s.replace("● on", "(on)").replace("○ off", "(off)"):
        if ch in _MAP:
            out.append(_MAP[ch])
        elif ord(ch) < 128:
            out.append(ch)
        else:
            base = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
            out.append(base if base and base.isascii() else "?")
    return "".join(out)


MAX_LABEL = 200          # no box is wider than the screen: 200 characters is always more than fits


def _fit(d: ImageDraw.ImageDraw, text: str, f, width: float) -> str:
    text = text[:MAX_LABEL]
    lo, hi = 0, len(text)                 # the longest prefix that fits (binary search)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if d.textlength(text[:mid], font=f) <= width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo]


def draw(vis, boxes=None, *, marks: bool = True) -> bytes:
    boxes = boxes if boxes is not None else som.layout(vis)
    img = Image.new("RGB", (som.WIDTH, som.HEIGHT), BG)
    d = ImageDraw.Draw(img)
    body, small, tag = font(13), font(9), font(10)
    d.rectangle([0, 0, som.WIDTH - 1, som.HEADER_H - 1], fill=HDR_BG)
    d.text((16, som.HEADER_H // 2), ascii_label(som.header_line(vis)), font=body, fill=INK, anchor="lm",
           stroke_width=0)
    if vis.dialog is not None:
        dim = Image.new("RGB", (som.WIDTH, som.HEIGHT - som.HEADER_H - som.DOCK_H), (0, 0, 0))
        region = img.crop((0, som.HEADER_H, som.WIDTH, som.HEIGHT - som.DOCK_H))
        img.paste(Image.blend(region, dim, 0.35), (0, som.HEADER_H))
        dx = (som.WIDTH - 560) // 2
        d.rounded_rectangle([dx, som.TOP + 48, dx + 559, som.TOP + 48 + som.HEIGHT // 2 - 1], radius=10, fill=WHITE)
    d.rectangle([0, som.HEIGHT - som.DOCK_H, som.WIDTH - 1, som.HEIGHT - 1], fill=DOCK_BG)
    for b in boxes:
        el = vis.by_id.get(b.id)
        quiet = el is not None and bool(el.meta.get("small") or el.meta.get("low_contrast"))
        f, ink = (small, SMALL_INK) if quiet else (body, INK)
        pad = 7 if b.marked else 0                      # 6 px padding + 1 px border, as the .ctl CSS
        if b.marked:
            d.rounded_rectangle([b.x, b.y, b.x + b.w - 1, b.y + b.h - 1], radius=4, fill=WHITE, outline=BORDER)
        text = _fit(d, ascii_label(b.label), f, b.w - 2 * pad)
        d.text((b.x + pad, b.y + b.h // 2), text, font=f, fill=ink, anchor="lm")
        if marks and b.marked:
            d.rectangle([b.x - 2, b.y - 2, b.x - 2 + b.w - 1, b.y - 2 + b.h - 1], outline=RED, width=2)
            label = ascii_label(str(b.id))
            tw = int(d.textlength(label, font=tag))
            ty = max(0, b.y - 12)
            d.rectangle([b.x - 2, ty, b.x - 2 + tw + 3, ty + 11], fill=RED)
            d.text((b.x - 2 + 2, ty + 6), label, font=tag, fill=WHITE, anchor="lm", stroke_width=0)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False)
    return buf.getvalue()
