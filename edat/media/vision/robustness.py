"""V5 robustness: the same questions under 25 image conditions (spec 05 §7.4).

``python -m edat.media.vision.robustness generate --split dev --out DIR``

**Base set.** 600 images, 60 from each of ten text-bearing V1 families (every
V1 family except ``photo_text``, whose images are already damaged photographs).
Each base image is a clean V1 item rendered in the light theme at pixel ratio 2
with no abstention, and carries two of its own scored questions. Every
condition asks the same two questions, so a model's score under a condition is
compared item by item with its clean score. 600 × 2 × 25 = 30,000 requests.

**Conditions** (25), each built from the base item's own data:

========================  =====================================================
``small_text_{7,9,11,13}``  re-rendered at pixel ratio 1 with every font scaled
                            so the smallest asked field is 7, 9, 11 or 13 px
``lowres_{384..2048}``      re-rendered at a long side of 384, 512, 768, 1,024,
                            1,568 or 2,048 px (the V4 ladder)
``rotate_{5,15,90,180}``    rotated counter-clockwise, canvas expanded
``crop_tight``              cut to the text with a small margin
``crop_cut``                cut through one asked field: that question's right
                            answer becomes "not shown" (an abstention item)
``crop_offcentre``          placed in a corner of a larger frame
``glare_{1,2}``             a white highlight; placed so the asked fields keep
                            a 3:1 contrast, so they remain answerable
``blur_{1,2,3}``            Gaussian blur of 0.6, 1.0 or 1.6 logical px
``jpeg_{40,20}``            JPEG at quality 40 or 20 (saved as JPEG)
``dark``                    re-rendered in the dark theme
========================  =====================================================

A base image is used only when every condition built from it passes its checks:
re-rendered conditions keep the same questions and answers and pass the V1
validator (the 8 px minimum is lifted for small text, and the ink and contrast
checks for the low-resolution ladder, whose point is to go below them); geometric
conditions keep every asked field's box inside the image.

**Unreadable set** (spec 05 §7.4): 500 further images that cannot be read at
all, 100 of each kind: heavy blur, a nearly black frame, pure noise, a 32 px
thumbnail scaled back up, and an overexposed frame. Each asks one question from
its source item; the right answer is "not shown" and the metric is the
false-answer rate.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import sys
from collections import Counter
from pathlib import Path

from PIL import Image, ImageFilter

from edat.media.common import canon_text, rng_for, sha256_bytes
from edat.media.vision import generate as gen
from edat.media.vision.canvas import render_stamp, text_scale
from edat.media.vision.items import ABSTAIN_ANSWER
from edat.media.vision.score import score_answer
from edat.media.vision.validate import contrast, ink_stats, max_edge, validate_item

SCHEMA = "edat.media.vision-robust/1"
BASE_FAMILIES = ("app_screens", "web_pages", "documents", "receipts_forms", "tables", "charts",
                 "handwriting", "calendar", "spreadsheet", "diagrams")
BASE_PER_FAMILY = 60                     # 10 families × 60 = 600 base images
QUESTIONS_PER_BASE = 2
SMALL_TEXT_PX = (7, 9, 11, 13)
LOWRES_LONG_SIDES = (384, 512, 768, 1024, 1568, 2048)
ROTATIONS = (5, 15, 90, 180)
GLARE_ALPHA = {1: 0.45, 2: 0.7}
BLUR_LOGICAL_PX = {1: 0.6, 2: 1.0, 3: 1.6}
JPEG_QUALITIES = (40, 20)
BASE_DPR = 2

CONDITIONS: tuple[str, ...] = (
    *(f"small_text_{p}" for p in SMALL_TEXT_PX),
    *(f"lowres_{s}" for s in LOWRES_LONG_SIDES),
    *(f"rotate_{a}" for a in ROTATIONS),
    "crop_tight", "crop_cut", "crop_offcentre",
    *(f"glare_{k}" for k in GLARE_ALPHA),
    *(f"blur_{k}" for k in BLUR_LOGICAL_PX),
    *(f"jpeg_{q}" for q in JPEG_QUALITIES),
    "dark",
)
assert len(CONDITIONS) == 25

UNREADABLE_KINDS = ("heavy_blur", "black_frame", "noise", "thumbnail", "overexposed")
UNREADABLE_TOTAL = 500
UNREADABLE_INDEX_OFFSET = 5000           # source items distinct from the base images
SCORED_KINDS = ("field", "anls", "relaxed")


class Ineligible(Exception):
    """This base image cannot carry every condition; the next index is tried."""


# ---------------------------------------------------------------- geometry

def _shift(box, dx, dy):
    return [box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy]


def _inside(box, size) -> bool:
    return box[0] >= 0 and box[1] >= 0 and box[2] <= size[0] and box[3] <= size[1] \
        and box[2] > box[0] and box[3] > box[1]


def _rotate_box(box, angle_deg, old_size, new_size):
    """The axis-aligned box around ``box`` after Pillow's counter-clockwise ``rotate``."""
    t = math.radians(angle_deg)
    c, s = math.cos(t), math.sin(t)
    cx, cy = old_size[0] / 2, old_size[1] / 2
    nx, ny = new_size[0] / 2, new_size[1] / 2
    xs, ys = [], []
    for x, y in ((box[0], box[1]), (box[2], box[1]), (box[0], box[3]), (box[2], box[3])):
        dx, dy = x - cx, y - cy
        xs.append(nx + dx * c + dy * s)
        ys.append(ny - dx * s + dy * c)
    return [math.floor(min(xs)), math.floor(min(ys)), math.ceil(max(xs)), math.ceil(max(ys))]


def _corner_bg(image) -> tuple[int, int, int]:
    rgb = image.convert("RGB")
    w, h = rgb.size
    return Counter(rgb.getpixel(p) for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))
                   ).most_common(1)[0][0]


# ---------------------------------------------------------------- base items

def _pick_questions(meta: dict, rng) -> list[dict]:
    fields = meta["fields"]
    def ok(q):
        return q["kind"] in SCORED_KINDS and q.get("fields") and all(
            fields.get(n, {}).get("visible") and fields[n].get("bbox") for n in q["fields"])
    printed = [q for q in meta["questions"] if ok(q) and q["kind"] != "relaxed"]
    pool = printed if len(printed) >= QUESTIONS_PER_BASE else [q for q in meta["questions"] if ok(q)]
    if len(pool) < QUESTIONS_PER_BASE:
        raise Ineligible("fewer than two answerable questions")
    picked = rng.sample(pool, len(pool))
    out, used = [], set()
    for q in picked:
        if used.isdisjoint(q["fields"]):
            out.append(q)
            used.update(q["fields"])
        if len(out) == QUESTIONS_PER_BASE:
            return out
    raise Ineligible("no two questions on distinct fields")


def _base(family: str, index: int, *, split: str, seed: str):
    meta, data, rendered = gen.build(family, index, split=split, seed=seed, theme="light",
                                     dpr=BASE_DPR, abstain=False)
    if rendered.damage is not None:
        raise Ineligible("damaged base")
    problems = validate_item(meta, rendered.image)
    if problems:
        raise Ineligible(f"base fails validation: {problems[:2]}")
    return meta, rendered


def _same_questions(base_qs: list[dict], meta: dict) -> bool:
    by = {q["qid"]: q for q in meta["questions"]}
    for q in base_qs:
        r = by.get(q["qid"])
        if r is None or (r["answer"], r["kind"], r["fields"]) != (q["answer"], q["kind"], q["fields"]):
            return False
    return True


_LIFTED = {"small_text": ("text is",), "lowres": ("only", "contrast")}


def _rerender_problems(cond: str, base_qs, meta, rendered) -> list[str]:
    if not _same_questions(base_qs, meta):
        return ["questions changed on re-rendering"]
    sub = dict(meta, questions=[q for q in meta["questions"]
                                if q["qid"] in {b["qid"] for b in base_qs}])
    lifted = next((v for k, v in _LIFTED.items() if cond.startswith(k)), ())
    out = []
    for p in validate_item(sub, rendered.image):
        tail = p.split(": ", 1)[-1]
        if not any(tail.startswith(x) for x in lifted):
            out.append(p)
    return out


# ---------------------------------------------------------------- conditions

def _encode(image, fmt: str = "PNG", quality: int | None = None) -> bytes:
    buf = io.BytesIO()
    if fmt == "JPEG":
        image.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=False)
    else:
        image.save(buf, format="PNG", optimize=False, compress_level=9)
    return buf.getvalue()


def _glare(image, center, radius, peak):
    w, h = image.size
    mask = Image.radial_gradient("L").resize((2 * radius, 2 * radius), Image.BILINEAR)
    mask = mask.point(lambda v: round(255 * peak * max(0.0, 1 - v / 255) ** 1.5))
    full = Image.new("L", (w, h), 0)
    full.paste(mask, (center[0] - radius, center[1] - radius))
    white = Image.new("RGB", (w, h), (255, 255, 255))
    return Image.composite(white, image.convert("RGB"), full), full


def _glare_keeps_contrast(base_img, glare_mask, fields, names) -> bool:
    for n in names:
        f = fields[n]
        box = f["bbox"]
        _, bg, _ = ink_stats(base_img, box, f["fg"])
        a = glare_mask.crop(tuple(box)).getextrema()[1] / 255
        fg2 = tuple(c + a * (255 - c) for c in f["fg"])
        bg2 = tuple(c + a * (255 - c) for c in bg)
        if contrast(fg2, bg2) < 3.0:
            return False
    return True


def _crop_cut(base_img, fields, qs, runs):
    """Cut through one asked field (keeping the other whole); return (image, box map, cut qid)."""
    W, H = base_img.size
    for cut_q, keep_q in ((qs[1], qs[0]), (qs[0], qs[1])):
        if cut_q["kind"] == "relaxed":
            continue
        cb = fields[cut_q["fields"][0]]["bbox"]
        keep = [fields[n]["bbox"] for n in keep_q["fields"]]
        mx, my = (cb[0] + cb[2]) // 2, (cb[1] + cb[3]) // 2
        for region in ((0, 0, mx, H), (mx, 0, W, H), (0, 0, W, my), (0, my, W, H)):
            if not all(region[0] <= b[0] and region[1] <= b[1] and b[2] <= region[2] and
                       b[3] <= region[3] for b in keep):
                continue
            value = canon_text(fields[cut_q["fields"][0]]["value"])
            whole = [r for r in runs if region[0] <= r["bbox"][0] and region[1] <= r["bbox"][1]
                     and r["bbox"][2] <= region[2] and r["bbox"][3] <= region[3]
                     and r.get("field") != cut_q["fields"][0]]
            if value and any(value in canon_text(r["text"]) for r in whole):
                continue           # the value is also printed elsewhere in what is kept
            img = base_img.crop(region)
            dx, dy = -region[0], -region[1]
            return img, (dx, dy), cut_q["qid"], region
    raise Ineligible("no cut keeps the other field whole")


def _geometric(cond: str, base_meta, rendered, qs, rng):
    """Apply one geometric or pixel condition: (image, fmt, quality, box_fn, params, cut_qid)."""
    img = rendered.image.convert("RGB")
    W, H = img.size
    fields = base_meta["fields"]
    scale = base_meta["image"]["scale"]
    names = [n for q in qs for n in q["fields"]]
    ident = lambda b: list(b)
    if cond.startswith("rotate_"):
        angle = int(cond.split("_")[1])
        out = img.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=_corner_bg(img))
        return out, "PNG", None, lambda b: _rotate_box(b, angle, (W, H), out.size), \
            {"angle_deg": angle, "direction": "counter-clockwise"}, None
    if cond == "crop_tight":
        boxes = [r["bbox"] for r in base_meta["text_runs"]] + [fields[n]["bbox"] for n in names]
        m = round(4 * scale)
        region = (max(0, min(b[0] for b in boxes) - m), max(0, min(b[1] for b in boxes) - m),
                  min(W, max(b[2] for b in boxes) + m), min(H, max(b[3] for b in boxes) + m))
        return img.crop(region), "PNG", None, lambda b: _shift(b, -region[0], -region[1]), \
            {"region": list(region)}, None
    if cond == "crop_cut":
        out, (dx, dy), cut_qid, region = _crop_cut(img, fields, qs, base_meta["text_runs"])
        return out, "PNG", None, lambda b: _shift(b, dx, dy), \
            {"region": list(region)}, cut_qid
    if cond == "crop_offcentre":
        fw, fh = round(W * 1.6), round(H * 1.6)
        corner = rng.choice(("top_left", "top_right", "bottom_left", "bottom_right"))
        ox = 0 if "left" in corner else fw - W
        oy = 0 if "top" in corner else fh - H
        frame = Image.new("RGB", (fw, fh), (118, 118, 124))
        frame.paste(img, (ox, oy))
        return frame, "PNG", None, lambda b: _shift(b, ox, oy), {"corner": corner}, None
    if cond.startswith("glare_"):
        k = int(cond.split("_")[1])
        radius = max(8, round(0.35 * min(W, H)))
        for _ in range(16):
            n = rng.choice(names)
            b = fields[n]["bbox"]
            cx = (b[0] + b[2]) // 2 + rng.randint(-radius // 2, radius // 2)
            cy = (b[1] + b[3]) // 2 + rng.randint(-radius // 2, radius // 2)
            out, mask = _glare(img, (cx, cy), radius, GLARE_ALPHA[k])
            if _glare_keeps_contrast(img, mask, fields, names):
                return out, "PNG", None, ident, \
                    {"center": [cx, cy], "radius_px": radius, "peak_alpha": GLARE_ALPHA[k]}, None
        raise Ineligible("no glare position keeps the fields readable")
    if cond.startswith("blur_"):
        r = BLUR_LOGICAL_PX[int(cond.split("_")[1])] * scale
        return img.filter(ImageFilter.GaussianBlur(r)), "PNG", None, ident, {"radius_px": r}, None
    if cond.startswith("jpeg_"):
        q = int(cond.split("_")[1])
        return img, "JPEG", q, ident, {"quality": q}, None
    raise ValueError(cond)


def _rerender(cond: str, family: str, index: int, *, split: str, seed: str, base_meta, qs):
    if cond.startswith("small_text_"):
        target = int(cond.rsplit("_", 1)[1])
        # The smallest asked field is set to the target size; larger ones scale with it.
        base_px = min(base_meta["fields"][n]["size_px"] for q in qs for n in q["fields"]) / BASE_DPR
        with text_scale(target / base_px):
            meta, _, rendered = gen.build(family, index, split=split, seed=seed, theme="light",
                                          dpr=1, abstain=False)
        sizes = [meta["fields"][n]["size_px"] for q in qs for n in q["fields"]
                 if n in meta["fields"]]
        if not sizes or abs(min(sizes) - target) > 1:
            raise Ineligible(f"small text {target}: fields are {sizes} px")
        return meta, rendered, {"field_px": sizes, "text_scale": round(target / base_px, 4)}
    if cond.startswith("lowres_"):
        ls = int(cond.split("_")[1])
        meta, _, rendered = gen.build(family, index, split=split, seed=seed, theme="light",
                                      dpr=BASE_DPR, abstain=False, long_side=ls)
        return meta, rendered, {"long_side": ls}
    if cond == "dark":
        meta, _, rendered = gen.build(family, index, split=split, seed=seed, theme="dark",
                                      dpr=BASE_DPR, abstain=False)
        return meta, rendered, {"theme": "dark"}
    raise ValueError(cond)


RERENDERED = ("small_text_", "lowres_", "dark")


def build_base(family: str, index: int, *, split: str, seed: str | None = None,
               conditions=None, questions_per_image: int | None = None
               ) -> list[tuple[dict, bytes]]:
    """Every condition for one base image, or :class:`Ineligible`.

    ``conditions`` and ``questions_per_image`` cut the output for a tier (spec 05 §17). Every
    condition is still built and checked, so which bases are eligible does not depend on the
    tier and a smaller tier's bases are always the first ones of a larger tier. A cut
    question (``crop_cut``) comes first, so one question per image keeps the abstention item.
    """
    seed = seed if seed is not None else gen.seed_for(split)
    base_meta, rendered = _base(family, index, split=split, seed=seed)
    rng = rng_for(seed, "v5", family, index)
    qs = _pick_questions(base_meta, rng)
    base_id = base_meta["item_id"]
    out = []
    for cond in CONDITIONS:
        cut_qid = None
        if cond.startswith(RERENDERED):
            meta, rr, params = _rerender(cond, family, index, split=split, seed=seed,
                                         base_meta=base_meta, qs=qs)
            problems = _rerender_problems(cond, qs, meta, rr)
            if problems:
                raise Ineligible(f"{cond}: {problems[:2]}")
            image, data = rr.image, gen.png_bytes(rr.image)
            boxes = {n: meta["fields"][n]["bbox"] for q in qs for n in q["fields"]}
            fmt, default_year = "png", meta.get("default_year")
        else:
            image, fmt_name, quality, box_fn, params, cut_qid = _geometric(
                cond, base_meta, rendered, qs, rng)
            data = _encode(image, fmt_name, quality)
            fmt = fmt_name.lower()
            boxes = {n: box_fn(base_meta["fields"][n]["bbox"]) for q in qs for n in q["fields"]}
            default_year = base_meta.get("default_year")
        if cut_qid is not None:          # the cut field's box is clipped to what is left
            for q in qs:
                if q["qid"] == cut_qid:
                    for n in q["fields"]:
                        b = boxes[n]
                        boxes[n] = [max(0, b[0]), max(0, b[1]), min(image.size[0], b[2]),
                                    min(image.size[1], b[3])]
        questions = []
        for q in qs:
            if q["qid"] == cut_qid:
                questions.append(dict(q, kind="abstain", answer=ABSTAIN_ANSWER, accept=[],
                                      qtype="absence", cut_from=q["answer"]))
                continue
            for n in q["fields"]:
                if not _inside(boxes[n], image.size):
                    raise Ineligible(f"{cond}: field {n} box {boxes[n]} leaves {image.size}")
            questions.append(q)
        for q in questions:
            if score_answer(q, q["answer"], default_year=default_year).score != 1.0 or \
                    score_answer(q, "", default_year=default_year).score != 0.0:
                raise Ineligible(f"{cond}: {q['qid']} gold or blank scores wrong")
        ext = "jpg" if fmt == "jpeg" else "png"
        iid = f"v5-{family}-{split}-{index:04d}-{cond}"
        meta_out = {
            "schema": SCHEMA, "item_id": iid, "base_item_id": base_id, "family": family,
            "template": base_meta["template"], "split": split,
            "seed": base_meta["seed"], "index": index, "condition": cond,
            "group": cond.rsplit("_", 1)[0] if cond[-1].isdigit() else cond, "params": params,
            "image": {"file": f"{iid}.{ext}", "sha256": sha256_bytes(data), "format": fmt,
                      "width": image.size[0], "height": image.size[1]},
            "render": render_stamp(), "default_year": default_year,
            "field_boxes": boxes, "questions": questions,
        }
        if questions_per_image is not None:
            order = sorted(questions, key=lambda q: "cut_from" not in q)
            meta_out["questions"] = order[:questions_per_image]
        out.append((meta_out, data))
    if conditions is not None:
        keep = set(conditions)
        unknown = keep - set(CONDITIONS)
        if unknown:
            raise ValueError(f"unknown V5 conditions: {sorted(unknown)}")
        out = [(m, d) for m, d in out if m["condition"] in keep]
    return out


def base_indices(family: str, *, split: str, seed: str | None = None, count: int = BASE_PER_FAMILY,
                 start: int = 0, limit: int = 4000, conditions=None,
                 questions_per_image: int | None = None):
    """Yield (index, items) for the first ``count`` eligible base images of ``family``."""
    got = 0
    for index in range(start, start + limit):
        if got == count:
            return
        try:
            items = build_base(family, index, split=split, seed=seed, conditions=conditions,
                               questions_per_image=questions_per_image)
        except Ineligible:
            continue
        got += 1
        yield index, items
    raise RuntimeError(f"{family}: only {got} eligible base images in {limit} indices")


# ---------------------------------------------------------------- unreadable set

def build_unreadable(n: int, *, split: str, seed: str | None = None) -> tuple[dict, bytes]:
    """The ``n``-th unreadable image (0 ≤ n < 500)."""
    seed = seed if seed is not None else gen.seed_for(split)
    kind = UNREADABLE_KINDS[n % len(UNREADABLE_KINDS)]
    families = BASE_FAMILIES
    family = families[(n // len(UNREADABLE_KINDS)) % len(families)]
    rng = rng_for(seed, "v5-unreadable", n)
    for attempt in range(40):
        index = UNREADABLE_INDEX_OFFSET + n * 40 + attempt
        try:
            base_meta, rendered = _base(family, index, split=split, seed=seed)
            q = _pick_questions(base_meta, rng)[0]
        except Ineligible:
            continue
        break
    else:
        raise RuntimeError(f"no source item for unreadable image {n}")
    img = rendered.image.convert("RGB")
    W, H = img.size
    scale = base_meta["image"]["scale"]
    if kind == "heavy_blur":
        out = img.filter(ImageFilter.GaussianBlur(10 * scale)).filter(
            ImageFilter.GaussianBlur(10 * scale))
    elif kind == "black_frame":
        out = img.point(lambda v: round(v * 0.02))
    elif kind == "noise":
        out = Image.frombytes("RGB", (W, H), rng.randbytes(W * H * 3))
    elif kind == "thumbnail":
        f = 32 / max(W, H)
        small = img.resize((max(1, round(W * f)), max(1, round(H * f))), Image.BOX)
        out = small.resize((W, H), Image.BILINEAR)
    else:
        out = Image.blend(img, Image.new("RGB", (W, H), (255, 255, 255)), 0.97)
    if kind != "noise":
        for name in q["fields"]:
            edge = max_edge(out, base_meta["fields"][name]["bbox"])
            if edge > 40:
                raise RuntimeError(f"unreadable {n} ({kind}): field {name} keeps an edge of {edge}")
    data = gen.png_bytes(out)
    iid = f"v5u-{split}-{n:04d}-{kind}"
    question = dict(q, kind="abstain", qtype="absence", answer=ABSTAIN_ANSWER, accept=[],
                    unreadable_from=q["answer"])
    assert score_answer(question, ABSTAIN_ANSWER).score == 1.0
    assert score_answer(question, "").score == 0.0
    assert score_answer(question, q["answer"]).false_answer
    return {
        "schema": SCHEMA, "item_id": iid, "base_item_id": base_meta["item_id"], "family": family,
        "template": base_meta["template"], "split": split, "seed": base_meta["seed"],
        "index": index, "condition": f"unreadable_{kind}", "group": "unreadable",
        "params": {"kind": kind},
        "image": {"file": f"{iid}.png", "sha256": sha256_bytes(data), "format": "png",
                  "width": W, "height": H},
        "render": render_stamp(), "default_year": base_meta.get("default_year"),
        "field_boxes": {}, "questions": [question],
    }, data


# ---------------------------------------------------------------- writing

def _write(out_dir: Path, meta: dict, data: bytes) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / meta["image"]["file"]).write_bytes(data)
    (out_dir / f"{meta['item_id']}.json").write_text(json.dumps(meta, indent=1) + "\n")


def generate(out: Path, *, split: str, families=BASE_FAMILIES, per_family=BASE_PER_FAMILY,
             unreadable=UNREADABLE_TOTAL, conditions=None,
             questions_per_image: int | None = None) -> dict:
    """Write V5 items. ``per_family`` is a count or a {family: count} map, ``unreadable`` a count
    or a list of unreadable-image numbers; both maps come from a tier's selection."""
    if split == "heldout" and gen._inside_repo(out):
        raise RuntimeError("held-out items must not be written inside the repository")
    counts: dict = {"base": {}, "requests": 0, "unreadable": 0}
    for fam in families:
        n = 0
        k = per_family.get(fam, 0) if isinstance(per_family, dict) else per_family
        if not k:
            continue
        for _, items in base_indices(fam, split=split, count=k, conditions=conditions,
                                     questions_per_image=questions_per_image):
            for meta, data in items:
                _write(out / fam / meta["condition"], meta, data)
                counts["requests"] += len(meta["questions"])
            n += 1
        counts["base"][fam] = n
    for i in (range(unreadable) if isinstance(unreadable, int) else unreadable):
        meta, data = build_unreadable(i, split=split)
        _write(out / "unreadable", meta, data)
        counts["unreadable"] += 1
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="tinyagent-bench V5 robustness items")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--split", default="dev", choices=("dev", "heldout"))
    g.add_argument("--family", action="append")
    g.add_argument("--per-family", type=int, default=BASE_PER_FAMILY)
    g.add_argument("--unreadable", type=int, default=UNREADABLE_TOTAL)
    g.add_argument("--tier", choices=("core", "extended", "full"),
                   help="take bases, conditions and questions from a tier manifest")
    g.add_argument("--out", required=True)
    sub.add_parser("conditions")
    args = ap.parse_args(argv)
    if args.cmd == "conditions":
        print("\n".join(CONDITIONS))
        return 0
    if args.tier:
        from edat.media import tiers
        v5 = tiers.select_vision(tiers.load_manifest(args.tier))["v5"]
        counts = generate(Path(args.out), split=args.split,
                          families=args.family or BASE_FAMILIES,
                          per_family=v5["bases_per_family"], unreadable=v5["unreadable"],
                          conditions=v5["conditions"],
                          questions_per_image=v5["questions_per_image"])
    else:
        counts = generate(Path(args.out), split=args.split,
                          families=args.family or BASE_FAMILIES,
                          per_family=args.per_family, unreadable=args.unreadable)
    json.dump(counts, sys.stdout, indent=1)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
