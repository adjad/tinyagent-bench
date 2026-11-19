"""A drawing surface that records what it draws, so every answer can be checked.

Families draw in logical (CSS-like) pixels; the canvas multiplies by ``scale``
(the device pixel ratio, 1 or 2, or any factor for the V5 resolution sweep) and
re-renders at that size rather than resizing a bitmap.

Every piece of text is recorded with its pixel box, in drawing order, which the
families keep equal to reading order: that list is the ground truth for the
full-reading CER score. A *planted field* is text a question asks about. For
each one the canvas also records the sha256 of its pixel box right after it was
drawn; the validator recomputes it from the saved PNG, which proves nothing was
drawn over the field later.

Font: Pillow's embedded Aileron Regular (CC0), at any size, so the committed
images need no font download and render the same on every machine with the same
Pillow and FreeType versions (recorded with each item). It covers ASCII only;
:func:`unsupported_chars` lets the validator refuse anything else. Bold is a
1-px stroke. Open fonts (Noto) can be added later through ``EDAT_FONT_DIR``
without changing any family.
"""
from __future__ import annotations

import contextlib
import contextvars
import functools
import hashlib
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, features
import PIL

RENDERER_ID = "edat-media-canvas/1"

THEMES: dict[str, dict[str, tuple[int, int, int]]] = {
    "light": {
        "bg": (255, 255, 255), "fg": (28, 28, 30), "muted": (99, 99, 102),
        "surface": (242, 242, 247), "border": (209, 209, 214), "accent": (0, 92, 197),
        "accent_fg": (255, 255, 255), "danger": (192, 28, 40), "ok": (22, 120, 60),
        "warn": (153, 92, 0), "bubble_in": (229, 229, 234), "bubble_out": (0, 92, 197),
        "bubble_out_fg": (255, 255, 255), "grid": (220, 220, 225),
    },
    "dark": {
        "bg": (28, 28, 30), "fg": (242, 242, 247), "muted": (174, 174, 178),
        "surface": (44, 44, 46), "border": (72, 72, 74), "accent": (100, 170, 255),
        "accent_fg": (10, 10, 12), "danger": (255, 105, 97), "ok": (90, 210, 120),
        "warn": (255, 190, 80), "bubble_in": (58, 58, 60), "bubble_out": (40, 110, 210),
        "bubble_out_fg": (255, 255, 255), "grid": (64, 64, 66),
    },
}

Color = tuple[int, int, int] | str

# V5 "small text": every font size is multiplied by this factor while the layout stays put.
_TEXT_SCALE: contextvars.ContextVar[float] = contextvars.ContextVar("edat_text_scale", default=1.0)
BODY_TEXT_PX = 12.0          # the families' typical body size; small-text levels are set against it


@contextlib.contextmanager
def text_scale(factor: float):
    """Render with every font ``factor`` times its normal size (V5 small-text condition)."""
    token = _TEXT_SCALE.set(float(factor))
    try:
        yield
    finally:
        _TEXT_SCALE.reset(token)


def render_stamp() -> dict:
    """The library versions that fix the pixels, recorded beside each image."""
    return {"renderer": RENDERER_ID, "pillow": PIL.__version__,
            "freetype": features.version("freetype2"), "font": font_name()}


def font_name() -> str:
    custom = os.environ.get("EDAT_FONT_DIR")
    return f"custom:{custom}" if custom else "Pillow embedded Aileron Regular (CC0)"


@functools.lru_cache(maxsize=256)
def font(px: int) -> ImageFont.FreeTypeFont:
    px = max(1, int(px))
    custom = os.environ.get("EDAT_FONT_DIR")
    if custom:
        regular = sorted(Path(custom).glob("*Regular*.ttf"))
        if regular:
            return ImageFont.truetype(str(regular[0]), px)
    return ImageFont.load_default(size=px)


@functools.lru_cache(maxsize=8)
def _notdef_mask(px: int) -> bytes:
    return bytes(font(px).getmask("￿"))


def unsupported_chars(text: str, px: int = 24) -> list[str]:
    """Characters the font would draw as the missing-glyph box."""
    bad = []
    nd = _notdef_mask(px)
    for ch in set(text):
        if ch.isspace():
            continue
        if bytes(font(px).getmask(ch)) == nd:
            bad.append(ch)
    return sorted(bad)


@dataclass
class TextRun:
    text: str
    bbox: list[int]                 # pixel box [x0, y0, x1, y1], end-exclusive
    size_px: int                    # font size in output pixels
    field: str | None = None
    reading: bool = True            # part of the full-reading ground truth


@dataclass
class Field:
    """A planted value a question asks about."""

    name: str
    kind: str                       # canonical kind: text, number, date, time, money, phone, state
    value: str                      # the planted answer, in a form canonical() reads
    text: str = ""                  # the exact string drawn ("" when absent)
    bbox: list[int] | None = None
    size_px: int = 0
    visible: bool = True
    obscured: str | None = None     # None, "absent", "blur", "crop"
    crop_sha256: str | None = None  # sha256 of the box's RGB bytes right after drawing
    fg: list[int] | None = None     # the colour the text was drawn in
    plot_value: bool = False        # read from a plot, not printed (relaxed scoring)

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class Element:
    """A UI element a person might ask to find ("where is the Save button?"), V7."""

    name: str                       # the label a question uses ("Save", "Wi-Fi toggle")
    role: str                       # button, toggle, tab, link, field, icon, menu_item, row
    bbox: list[int]                 # pixel box [x0, y0, x1, y1], end-exclusive
    unique: bool = True             # no other element on the image shares this name

    def to_json(self) -> dict:
        return asdict(self)


class Canvas:
    def __init__(self, width: int, height: int, *, scale: float = 1.0, theme: str = "light",
                 bg: Color = "bg") -> None:
        if theme not in THEMES:
            raise ValueError(f"unknown theme {theme!r}")
        self.logical = (int(width), int(height))
        self.scale = float(scale)
        self.theme = theme
        self.pal = THEMES[theme]
        self.size = (max(1, round(width * scale)), max(1, round(height * scale)))
        self.image = Image.new("RGB", self.size, self.color(bg))
        self.draw = ImageDraw.Draw(self.image)
        self.runs: list[TextRun] = []
        self.fields: dict[str, Field] = {}
        self.elements: list[Element] = []

    # ---------------------------------------------------------------- units
    def px(self, v: float) -> int:
        return round(v * self.scale)

    def color(self, c: Color) -> tuple[int, int, int]:
        return self.pal[c] if isinstance(c, str) else tuple(c)  # type: ignore[return-value]

    def font_px(self, size: float) -> int:
        return max(1, round(size * self.scale * _TEXT_SCALE.get()))

    def text_width(self, s: str, size: float = 14, bold: bool = False) -> float:
        """Width of ``s`` in logical pixels."""
        f = font(self.font_px(size))
        x0, _, x1, _ = self.draw.textbbox((0, 0), s, font=f, anchor="la",
                                          stroke_width=self._stroke(bold))
        return (x1 - x0) / self.scale

    def _stroke(self, bold: bool) -> int:
        return max(1, round(0.6 * self.scale)) if bold else 0

    # ---------------------------------------------------------------- shapes
    def rect(self, x0: float, y0: float, x1: float, y1: float, *, fill: Color | None = None,
             outline: Color | None = None, radius: float = 0, width: float = 1) -> None:
        box = [self.px(x0), self.px(y0), self.px(x1) - 1, self.px(y1) - 1]
        box[2], box[3] = max(box[0], box[2]), max(box[1], box[3])   # thinner than 1 px: 1 px
        kw = {"fill": self.color(fill) if fill is not None else None,
              "outline": self.color(outline) if outline is not None else None,
              "width": max(1, self.px(width)) if outline is not None else 0}
        if radius:
            self.draw.rounded_rectangle(box, radius=self.px(radius), **kw)
        else:
            self.draw.rectangle(box, **kw)

    def line(self, x0: float, y0: float, x1: float, y1: float, *, color: Color = "border",
             width: float = 1) -> None:
        self.draw.line([self.px(x0), self.px(y0), self.px(x1), self.px(y1)],
                       fill=self.color(color), width=max(1, self.px(width)))

    def polyline(self, pts: list[tuple[float, float]], *, color: Color = "accent",
                 width: float = 2) -> None:
        self.draw.line([(self.px(x), self.px(y)) for x, y in pts], fill=self.color(color),
                       width=max(1, self.px(width)), joint="curve")

    def ellipse(self, x0: float, y0: float, x1: float, y1: float, *, fill: Color | None = None,
                outline: Color | None = None, width: float = 1) -> None:
        self.draw.ellipse([self.px(x0), self.px(y0), self.px(x1) - 1, self.px(y1) - 1],
                          fill=self.color(fill) if fill is not None else None,
                          outline=self.color(outline) if outline is not None else None,
                          width=max(1, self.px(width)) if outline is not None else 0)

    def pieslice(self, x0: float, y0: float, x1: float, y1: float, start: float, end: float, *,
                 fill: Color) -> None:
        self.draw.pieslice([self.px(x0), self.px(y0), self.px(x1) - 1, self.px(y1) - 1],
                           start, end, fill=self.color(fill))

    def polygon(self, pts: list[tuple[float, float]], *, fill: Color) -> None:
        self.draw.polygon([(self.px(x), self.px(y)) for x, y in pts], fill=self.color(fill))

    # ---------------------------------------------------------------- text
    def text(self, x: float, y: float, s: str, *, size: float = 14, color: Color = "fg",
             bold: bool = False, anchor: str = "la", reading: bool = True,
             field: str | None = None, kind: str = "text", value: str | None = None,
             plot_value: bool = False) -> list[int]:
        """Draw ``s`` with its top-left (anchor ``la``) at logical (x, y); return the pixel box.

        With ``field`` set, the text is a planted field whose answer is ``value``
        (default: ``s`` itself) read as ``kind``.
        """
        fpx = self.font_px(size)
        f = font(fpx)
        stroke = self._stroke(bold)
        fill = self.color(color)
        pos = (self.px(x), self.px(y))
        self.draw.text(pos, s, font=f, fill=fill, anchor=anchor, stroke_width=stroke,
                       stroke_fill=fill if stroke else None)
        x0, y0, x1, y1 = self.draw.textbbox(pos, s, font=f, anchor=anchor, stroke_width=stroke)
        box = [max(0, x0), max(0, y0), min(self.size[0], x1), min(self.size[1], y1)]
        self.runs.append(TextRun(s, box, fpx, field, reading))
        if field:
            clipped = box != [x0, y0, x1, y1]
            self.fields[field] = Field(
                name=field, kind=kind, value=s if value is None else value, text=s, bbox=box,
                size_px=fpx, visible=not clipped, obscured="crop" if clipped else None,
                crop_sha256=self.crop_sha256(box), fg=list(fill), plot_value=plot_value)
        return box

    def text_right(self, x_right: float, y: float, s: str, **kw) -> list[int]:
        return self.text(x_right - self.text_width(s, kw.get("size", 14), kw.get("bold", False)),
                         y, s, **kw)

    def text_center(self, x_mid: float, y: float, s: str, **kw) -> list[int]:
        return self.text(x_mid - self.text_width(s, kw.get("size", 14), kw.get("bold", False)) / 2,
                         y, s, **kw)

    def mark(self, name: str, x0: float, y0: float, x1: float, y1: float, *, value: str,
             kind: str = "number", color: Color = "accent") -> list[int]:
        """Record an already-drawn plot mark (bar, point, slice) as a field read off the plot.

        Its answer is scored with relaxed accuracy (within 5 %), never printed text.
        Call it right after drawing the mark, with the mark's own colour.
        """
        box = [max(0, self.px(x0)), max(0, self.px(y0)), min(self.size[0], self.px(x1)),
               min(self.size[1], self.px(y1))]
        self.fields[name] = Field(name=name, kind=kind, value=value, text="", bbox=box,
                                  size_px=max(box[3] - box[1], box[2] - box[0]), visible=True,
                                  crop_sha256=self.crop_sha256(box), fg=list(self.color(color)),
                                  plot_value=True)
        return box

    def element(self, name: str, x0: float, y0: float, x1: float, y1: float, *,
                role: str = "button") -> list[int]:
        """Record a named UI element's box (logical units) for V7 control location."""
        box = [max(0, self.px(x0)), max(0, self.px(y0)), min(self.size[0], self.px(x1)),
               min(self.size[1], self.px(y1))]
        dup = [e for e in self.elements if e.name.casefold() == name.casefold()]
        for e in dup:
            e.unique = False
        self.elements.append(Element(name, role, box, unique=not dup))
        return box

    def absent(self, name: str, value: str, kind: str = "text") -> None:
        """Record a field a question asks about that is deliberately not on the image."""
        self.fields[name] = Field(name=name, kind=kind, value=value, visible=False,
                                  obscured="absent")

    def blur_field(self, name: str, radius: float = 6.0) -> None:
        """Blur a drawn field until it cannot be read; it becomes an abstention field."""
        f = self.fields[name]
        assert f.bbox is not None
        pad = self.px(3)
        box = (max(0, f.bbox[0] - pad), max(0, f.bbox[1] - pad),
               min(self.size[0], f.bbox[2] + pad), min(self.size[1], f.bbox[3] + pad))
        region = self.image.crop(box).filter(ImageFilter.GaussianBlur(radius * self.scale))
        region = region.filter(ImageFilter.GaussianBlur(radius * self.scale))
        self.image.paste(region, box)
        f.visible, f.obscured = False, "blur"
        f.crop_sha256 = self.crop_sha256(f.bbox)
        for run in self.runs:
            if run.field == name:
                run.reading = False

    # ---------------------------------------------------------------- output
    def crop_sha256(self, box: list[int]) -> str:
        return hashlib.sha256(self.image.crop(tuple(box)).tobytes()).hexdigest()

    def reading_text(self) -> str:
        return "\n".join(r.text for r in self.runs if r.reading)

    def text_runs(self) -> list[dict]:
        return [asdict(r) for r in self.runs]
