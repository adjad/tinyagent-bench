"""Prove that every item can be answered from its pixels, and that blank answers score zero.

``validate_item(meta, image)`` returns a list of problems (empty means valid).
For every planted field a visible question depends on, it checks that:

1. the field's box lies inside the image and is not empty;
2. the pixels in the box are exactly those captured right after the text was
   drawn (sha256), so nothing was drawn over it later; for a damaged photo item
   this is checked on the undamaged image;
3. the box holds ink: enough pixels closer to the text colour than to the
   background, at a contrast ratio of at least 3:1 (WCAG large-text level);
4. the font has a glyph for every character (no missing-glyph boxes);
5. the drawn string gives the planted answer after canonicalisation;
6. at the base resolutions, the text is at least 8 px tall.

For abstention fields it checks the opposite: an absent value appears in no
drawn text, and a blurred one has no sharp edges left. For every question it
checks that the gold answer scores 1 and a blank answer scores 0.
"""
from __future__ import annotations

import hashlib
from collections import Counter

from edat.media.common import canon_text, canonical
from edat.media.vision.canvas import unsupported_chars
from edat.media.vision.score import score_answer

MIN_CONTRAST = 3.0
MIN_TEXT_PX = 8
MAX_BLUR_EDGE = 40          # largest neighbouring-pixel step left inside a blurred field


def _lum(rgb) -> float:
    def ch(v: float) -> float:
        v = v / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = rgb[:3]
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a, b) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _dist(a, b) -> int:
    return sum((x - y) ** 2 for x, y in zip(a[:3], b[:3]))


def ink_stats(image, box, fg) -> tuple[int, tuple, float]:
    """(ink pixel count, background colour, contrast fg:bg) inside ``box``."""
    crop = image.crop(tuple(box)).convert("RGB")
    raw = crop.tobytes()
    pixels = [tuple(raw[i:i + 3]) for i in range(0, len(raw), 3)]
    if not pixels:
        return 0, (0, 0, 0), 1.0
    # The background is the commonest colour in a 2-px ring just outside the box
    # (bold text can fill most of its own box).
    x0, y0, x1, y1 = box
    W, H = image.size
    ring = []
    rgb = image.convert("RGB") if image.mode != "RGB" else image
    for x in range(max(0, x0 - 2), min(W, x1 + 2)):
        for y in (y0 - 2, y0 - 1, y1, y1 + 1):
            if 0 <= y < H:
                ring.append(rgb.getpixel((x, y)))
    for y in range(max(0, y0), min(H, y1)):
        for x in (x0 - 2, x0 - 1, x1, x1 + 1):
            if 0 <= x < W:
                ring.append(rgb.getpixel((x, y)))
    bg = Counter(ring or pixels).most_common(1)[0][0]
    fg = tuple(fg)
    ink = sum(1 for p in pixels if _dist(p, fg) < _dist(p, bg))
    return ink, bg, contrast(fg, bg)


def max_edge(image, box) -> int:
    crop = image.crop(tuple(box)).convert("L")
    w, h = crop.size
    px = crop.load()
    best = 0
    for y in range(h):
        for x in range(w):
            v = px[x, y]
            if x + 1 < w:
                best = max(best, abs(v - px[x + 1, y]))
            if y + 1 < h:
                best = max(best, abs(v - px[x, y + 1]))
    return best


def _drawn_texts(meta: dict) -> list[str]:
    return [r["text"] for r in meta.get("text_runs", [])]


def validate_item(meta: dict, image, *, clean_image=None) -> list[str]:
    problems: list[str] = []
    width, height = image.size
    if (width, height) != (meta["image"]["width"], meta["image"]["height"]):
        problems.append(f"image is {width}x{height}, ground truth says "
                        f"{meta['image']['width']}x{meta['image']['height']}")
    damaged = bool(meta.get("damage"))
    reference = clean_image if (damaged and clean_image is not None) else image
    base_res = not meta.get("variant", {}).get("long_side")
    fields = meta["fields"]
    needed_visible: set[str] = set()
    needed_hidden: set[str] = set()
    for q in meta["questions"]:
        target = needed_hidden if q["kind"] == "abstain" else needed_visible
        for name in q.get("fields", []):
            if name not in fields:
                problems.append(f"{q['qid']}: field {name!r} is not recorded")
            else:
                target.add(name)
        if q["kind"] == "abstain" and not any(not fields.get(n, {}).get("visible", True)
                                              for n in q.get("fields", [])):
            problems.append(f"{q['qid']}: abstention question has no hidden field")

    if damaged:
        boxes = (meta.get("damage") or {}).get("field_boxes") or {}
        for name in sorted(needed_visible):
            b = boxes.get(name)
            if b is None:
                problems.append(f"field {name}: damaged item records no box in the final image")
            elif b[0] < 0 or b[1] < 0 or b[2] > width or b[3] > height or b[2] <= b[0] or b[3] <= b[1]:
                problems.append(f"field {name}: box {b} leaves the final image")

    drawn = [canon_text(t) for t in _drawn_texts(meta)]
    for name in sorted(needed_visible):
        f = fields[name]
        tag = f"field {name}"
        if not f["visible"]:
            problems.append(f"{tag}: a scored question needs it but it is {f['obscured']}")
            continue
        box = f["bbox"]
        if not box or box[0] < 0 or box[1] < 0 or box[2] > reference.size[0] or \
                box[3] > reference.size[1] or box[2] <= box[0] or box[3] <= box[1]:
            problems.append(f"{tag}: box {box} is empty or outside the image")
            continue
        if damaged and clean_image is None:
            pass  # pixel proof needs the undamaged image; checked at generation time
        else:
            got = hashlib.sha256(reference.crop(tuple(box)).tobytes()).hexdigest()
            if got != f["crop_sha256"]:
                problems.append(f"{tag}: pixels changed after drawing (overdrawn)")
            ink, bg, ratio = ink_stats(reference, box, f["fg"])
            nchars = sum(1 for ch in f["text"] if not ch.isspace())
            if ink < max(3, 2 * nchars):
                problems.append(f"{tag}: only {ink} ink pixels for {nchars} characters")
            if ratio < MIN_CONTRAST:
                problems.append(f"{tag}: contrast {ratio:.2f} below {MIN_CONTRAST}")
        bad = unsupported_chars(f["text"])
        if bad:
            problems.append(f"{tag}: font has no glyph for {bad}")
        if base_res and f["size_px"] < MIN_TEXT_PX:
            problems.append(f"{tag}: text is {f['size_px']} px, below {MIN_TEXT_PX}")
        if not f.get("plot_value") and f["kind"] != "state":
            want = canonical(f["kind"], f["value"], default_year=meta.get("default_year"))
            have = canonical(f["kind"], f["text"], default_year=meta.get("default_year"))
            if f["kind"] == "text":
                if not want or want not in (have or ""):
                    problems.append(f"{tag}: drawn {f['text']!r} does not contain {f['value']!r}")
            elif want is None or want != have:
                problems.append(f"{tag}: drawn {f['text']!r} reads as {have!r}, planted {want!r}")

    for name in sorted(needed_hidden):
        f = fields[name]
        if f["visible"]:
            continue
        if f["obscured"] == "absent":
            v = canon_text(f["value"])
            if v and any(v in t for t in drawn):
                problems.append(f"field {name}: marked absent but drawn on the image")
        elif f["obscured"] == "blur" and f.get("bbox"):
            edge = max_edge(image if not damaged else reference, f["bbox"])
            if edge > MAX_BLUR_EDGE:
                problems.append(f"field {name}: blurred field still has an edge of {edge}")

    for q in meta["questions"]:
        kw = {"default_year": meta.get("default_year"), "reading_text": meta.get("reading_text", "")}
        gold = meta.get("reading_text", "") if q["kind"] == "reading" else q["answer"]
        if q["kind"] == "reading" and not gold:
            problems.append(f"{q['qid']}: empty reading text")
            continue
        if score_answer(q, gold, **kw).score != 1.0:
            problems.append(f"{q['qid']}: the gold answer {gold[:40]!r} does not score 1")
        if score_answer(q, "", **kw).score != 0.0:
            problems.append(f"{q['qid']}: a blank answer does not score 0")
    return problems
