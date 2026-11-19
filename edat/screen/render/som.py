"""The screenshot track (spec 11 §3.1, S6): set-of-marks layout, HTML, scoring.

* `layout(vis)`: a deterministic 1,280 × 800 layout of the current screen —
  a header bar, the visible element lines (17 px rows, 13 px text, 24 px per
  indent level), a centred dialog box over a dimmed window when one is open,
  and the Dock along the bottom. Every actionable element gets a box and a
  numbered mark carrying its per-trial id.
* `to_html(vis, boxes, marks=True)`: the same layout as a self-contained HTML
  page (no network, no external fonts), kept as the readable description of
  the picture and for a browser renderer later. `marks=False` gives the
  unmarked page family P uses.
* `raster.draw(vis, boxes, marks=True)` (render/raster.py): the PNG the model
  sees, drawn with Pillow from the simulated layout. It is never a capture of
  the real screen: nothing in the screen tier can read the Mac's display.
* `hit(boxes, x, y)` and `score_point(...)`: family P's check (a
  `click_point(x, y)` is correct when it falls inside the target's box).
* `SomObserver`: the loop's observer for `screenshot_som` and
  `ax_plus_screenshot`. Its tool result is the status line plus the one-line
  header (and, for `ax_plus_screenshot`, the text list); the image goes in a
  user message right after it, as OpenAI-style tool messages cannot carry
  images. Only the latest screenshot is sent; earlier ones become stubs.

Not built: a browser renderer (`rasterise(html)`, which still raises), golden
image hashes across Pillow versions (the renderer and Pillow/FreeType
versions go into every trial's raw record instead), and spec 05 V4's measured
image-token cost of a 1,280 × 800 screenshot.
"""
from __future__ import annotations

import base64
import hashlib
import html as _html
from dataclasses import dataclass

from edat.screen.world.render_text import _q

WIDTH, HEIGHT = 1280, 800
HEADER_H = 40
DOCK_H = 56
ROW_H = 17
TOP = HEADER_H + 12
LEFT = 24
INDENT = 24
CHAR_W = 7.2          # 13 px text in the bundled font, measured once the font is pinned (estimate)
FONT_STACK = "'Bench Sans', 'Inter', sans-serif"


class RasterNotAvailable(RuntimeError):
    pass


@dataclass
class Box:
    id: str
    key: str
    role: str
    label: str
    x: int
    y: int
    w: int
    h: int
    marked: bool
    dimmed: bool = False

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x < self.x + self.w and self.y <= y < self.y + self.h


ACTIONABLE_ROLES = {"button", "link", "textbox", "checkbox", "switch", "radio", "popup", "option", "slider", "tab",
                    "row", "menuitem", "cell"}


def _w(text: str, pad: int = 16) -> int:
    return int(len(text) * CHAR_W) + pad


def _display(el) -> str:
    label = el.name
    if el.role == "switch":
        label += "  ● on" if el.on else "  ○ off"
    elif el.role in ("checkbox", "radio"):
        label = ("☑ " if el.checked else "☐ ") + label
    elif el.role in ("textbox", "popup", "slider") and el.value is not None:
        label += f": {el.value}" if el.value else ": ____"
    return label


def layout(vis) -> list[Box]:
    boxes: list[Box] = []
    lines = vis.text.split("\n")[2:]
    y = TOP
    els = list(vis.by_id.items())
    content = [(i, e) for i, e in els if not e.meta.get("dock")]
    depth = {}
    for ln in lines:
        s = ln.lstrip(" ")
        if s.startswith("["):
            depth[s[1:s.index("]")]] = (len(ln) - len(s)) // 2
    if vis.dialog is not None:
        dx, dw = (WIDTH - 560) // 2, 560
        dy = TOP + 60
        for i, e in content:
            d = depth.get(i, 0)
            label = _display(e)
            boxes.append(Box(i, e.key, e.role, label, dx + 16 + d * INDENT, dy, min(dw - 32, _w(label)), ROW_H + 6,
                             marked=e.role in ACTIONABLE_ROLES or e.clickable))
            dy += ROW_H + 10
    else:
        for i, e in content:
            d = depth.get(i, 0)
            label = _display(e)
            boxes.append(Box(i, e.key, e.role, label, LEFT + d * INDENT, y, _w(label), ROW_H,
                             marked=e.role in ACTIONABLE_ROLES or (e.clickable and e.role != "text")))
            y += ROW_H
    dock = [(i, e) for i, e in els if e.meta.get("dock")]
    x = (WIDTH - sum(_w(e.name, 28) for _, e in dock)) // 2
    for i, e in dock:
        w = _w(e.name, 28)
        boxes.append(Box(i, e.key, "dock", e.name, x, HEIGHT - DOCK_H + 10, w, DOCK_H - 20, marked=True))
        x += w + 8
    return boxes


def header_line(vis) -> str:
    return vis.text.split("\n", 1)[0].split(" · Visible")[0]


def to_html(vis, boxes: list[Box] | None = None, *, marks: bool = True) -> str:
    boxes = boxes if boxes is not None else layout(vis)
    parts = [f"<!doctype html><html><head><meta charset='utf-8'><style>"
             f"body{{margin:0;width:{WIDTH}px;height:{HEIGHT}px;font:13px/{ROW_H}px {FONT_STACK};"
             f"background:#f6f6f4;color:#1d1d1f;position:relative;overflow:hidden}}"
             f".hdr{{position:absolute;left:0;top:0;right:0;height:{HEADER_H}px;background:#e8e8e6;"
             f"line-height:{HEADER_H}px;padding-left:16px;font-weight:600}}"
             f".el{{position:absolute;white-space:nowrap}}"
             f".ctl{{border:1px solid #9a9a9a;border-radius:4px;background:#fff;padding:0 6px}}"
             f".dock{{position:absolute;left:0;right:0;bottom:0;height:{DOCK_H}px;background:#dcdcda}}"
             f".dim{{position:absolute;inset:{HEADER_H}px 0 {DOCK_H}px 0;background:rgba(0,0,0,.35)}}"
             f".dlg{{position:absolute;left:{(WIDTH - 560) // 2}px;top:{TOP + 48}px;width:560px;"
             f"background:#fff;border-radius:10px;box-shadow:0 8px 30px rgba(0,0,0,.3);height:{HEIGHT // 2}px}}"
             f".small{{font-size:9px;color:#9a9a9a}}"
             f".mk{{position:absolute;background:#d7263d;color:#fff;font:bold 10px/12px sans-serif;"
             f"padding:0 2px;border-radius:2px}}"
             f".bx{{position:absolute;border:2px solid #d7263d;pointer-events:none}}"
             f"</style></head><body><div class='hdr'>{_html.escape(header_line(vis))}</div>"]
    if vis.dialog is not None:
        parts.append("<div class='dim'></div><div class='dlg'></div>")
    parts.append("<div class='dock'></div>")
    for b in boxes:
        cls = "el ctl" if b.marked else "el"
        el = vis.by_id.get(b.id)
        if el is not None and (el.meta.get("small") or el.meta.get("low_contrast")):
            cls += " small"
        parts.append(f"<div class='{cls}' style='left:{b.x}px;top:{b.y}px;width:{b.w}px;height:{b.h}px'>"
                     f"{_html.escape(b.label)}</div>")
        if marks and b.marked:
            parts.append(f"<div class='bx' style='left:{b.x - 2}px;top:{b.y - 2}px;width:{b.w}px;height:{b.h}px'>"
                         f"</div><div class='mk' style='left:{b.x - 2}px;top:{max(0, b.y - 12)}px'>{b.id}</div>")
    parts.append("</body></html>")
    return "".join(parts)


def rasterise(html_text: str) -> bytes:
    raise RasterNotAvailable("the screenshot track needs a pinned offline headless browser with a bundled font "
                             "(S6) and the vision build's image path; neither is built yet")


def hit(boxes: list[Box], x: float, y: float) -> Box | None:
    inside = [b for b in boxes if b.contains(x, y)]
    return min(inside, key=lambda b: b.w * b.h) if inside else None


def score_point(boxes: list[Box], target_key: str, x: float, y: float) -> bool:
    """Family P: correct when the point falls inside the target element's box."""
    b = hit(boxes, x, y)
    return b is not None and b.key == target_key


class SomObserver:
    """Observer for the screenshot tracks: the simulated screen drawn as a PNG with numbered marks."""

    def __init__(self, with_text: bool = False, rasteriser=None) -> None:
        self.with_text = with_text
        self.track = "ax_plus_screenshot" if with_text else "screenshot_som"
        self.rasteriser = rasteriser          # callable(vis, boxes) -> PNG bytes; default: raster.draw

    def render(self, world):
        vis = world.observe()
        boxes = layout(vis)
        if self.rasteriser is None:
            from edat.screen.render.raster import draw
            png = draw(vis, boxes, marks=True)
        else:
            png = self.rasteriser(vis, boxes)
        text = vis.text if self.with_text else header_line(vis) + " · Screenshot follows with numbered marks"
        url = "data:image/png;base64," + base64.b64encode(png).decode()
        extra = {"role": "user", "content": [{"type": "text", "text": "Screenshot:"},
                                             {"type": "image_url", "image_url": {"url": url}}]}
        tokens = len(text) // 3 + 1
        return text, extra, tokens, hashlib.sha256(png).hexdigest()


def window_label(vis) -> str:
    return _q(vis.window)
