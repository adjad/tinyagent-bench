"""Generate vision items from a seed: ``python -m edat.media.vision.generate``.

Every item is determined by (seed, family, index): the same inputs give the same
data, the same questions and, with the same Pillow and FreeType, the same pixels.

Splits:

* ``dev`` uses the public seed :data:`DEV_SEED`. A small sample is committed in
  ``test_fixtures/media/vision/dev/``; the full set is generated on demand.
* ``heldout`` uses a private seed read from ``$EDAT_PRIVATE_DIR/media/heldout_seed``
  and is written only under ``$EDAT_PRIVATE_DIR``. It is never committed; the
  generator refuses to write it inside the repository.

Variants: the template rotates with the index; within one template, theme
(light, dark) and device pixel ratio (1, 2) rotate with the template's own
counter, and 8 items in 20 are abstention items, four of each theme, so every
template meets every variant (spec 05 §7.1: 10 % of scored questions are abstention
items). The V5 sweep re-renders an item at a target long side (``--long-side``) instead of
resizing it; a family whose final image is larger than its printed piece (the
photo family) declares ``FINAL_FACTOR`` so the long side is that of the final image.
"""
from __future__ import annotations

import argparse
import importlib
import io
import json
import os
import sys
from pathlib import Path

from edat.media.common import rng_for, sha256_bytes
from edat.media.vision.canvas import render_stamp
from edat.media.vision.items import Rendered, item_json

DEV_SEED = "edat-media-dev-2027.1"
FAMILY_MODULES = {          # spec 05 §7.1: eleven V1 families
    "app_screens": "edat.media.vision.families.app_screens",
    "web_pages": "edat.media.vision.families.web_pages",
    "documents": "edat.media.vision.families.documents",
    "receipts_forms": "edat.media.vision.families.receipts_forms",
    "tables": "edat.media.vision.families.tables",
    "charts": "edat.media.vision.families.charts",
    "handwriting": "edat.media.vision.families.handwriting",
    "photo_text": "edat.media.vision.families.photo_text",
    "calendar": "edat.media.vision.families.calendar",
    "spreadsheet": "edat.media.vision.families.spreadsheet",
    "diagrams": "edat.media.vision.families.diagrams",
}
FULL_SET_PER_FAMILY = 500            # spec 05 §7.1: 500 images per family
V5_LONG_SIDES = (512, 768, 1024, 1568, 2048, 3000)
REPO_ROOT = Path(__file__).resolve().parents[3]


def family_module(name: str):
    try:
        return importlib.import_module(FAMILY_MODULES[name])
    except KeyError:
        raise KeyError(f"unknown family {name!r}; known: {sorted(FAMILY_MODULES)}") from None


def available_families() -> list[str]:
    out = []
    for name in FAMILY_MODULES:
        try:
            family_module(name)
        except ModuleNotFoundError:
            continue
        out.append(name)
    return out


def heldout_seed() -> str:
    private = os.environ.get("EDAT_PRIVATE_DIR")
    if not private:
        raise RuntimeError("the held-out split needs EDAT_PRIVATE_DIR")
    path = Path(private) / "media" / "heldout_seed"
    if not path.exists():
        raise RuntimeError(f"no held-out seed at $EDAT_PRIVATE_DIR/media/heldout_seed; "
                           "create it with: python -m edat.media.vision.generate init-heldout-seed")
    return path.read_text().strip()


def seed_for(split: str) -> str:
    if split == "dev":
        return DEV_SEED
    if split == "heldout":
        return heldout_seed()
    raise ValueError(f"unknown split {split!r}")


# Positions in each run of 20 per template that are abstention items: 8 in 20, so about
# 40 % of images carry one abstention question and 10 % of the scored questions are
# abstention items (spec 05 §7.1). Four are dark and four light, four at each pixel ratio.
ABSTAIN_SLOTS = (1, 3, 6, 9, 12, 14, 16, 19)


def variant_for(index: int, n_templates: int = 1, *, long_side: int | None = None) -> dict:
    """Theme, scale and abstention for an item, rotating within its template."""
    k = index // n_templates
    return {"theme": ("light", "dark")[k % 2], "dpr": (1, 2)[(k // 2) % 2],
            "abstain": k % 20 in ABSTAIN_SLOTS, "long_side": long_side}


def render_item(family: str, index: int, *, seed: str, long_side: int | None = None,
                theme: str | None = None, dpr: int | None = None,
                abstain: bool | None = None, **extra) -> tuple[Rendered, dict]:
    """Render one item. The data stream depends only on (seed, family, index)."""
    mod = family_module(family)
    variant = variant_for(index, len(mod.TEMPLATES), long_side=long_side)
    if theme is not None:
        variant["theme"] = theme
    if dpr is not None:
        variant["dpr"] = dpr
    if abstain is not None:
        variant["abstain"] = abstain
    variant.update(extra)
    template = mod.TEMPLATES[index % len(mod.TEMPLATES)]
    logical = mod.LOGICAL_SIZES[template]
    factor = getattr(mod, "FINAL_FACTOR", 1.0)
    scale = (long_side / (max(logical) * factor)) if long_side else float(variant["dpr"])
    variant["scale"] = scale
    rng = rng_for(seed, family, index)
    kwargs = {"template": template, "theme": variant["theme"], "scale": scale,
              "abstain": variant["abstain"]}
    if "damage" in variant:
        kwargs["damage"] = variant["damage"]
    rendered = mod.render(rng, **kwargs)
    if rendered.damage is not None:
        variant["damage"] = rendered.damage.get("level")
    return rendered, variant


def png_bytes(image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=False, compress_level=9)
    return buf.getvalue()


def item_id_for(family: str, split: str, index: int, variant: dict) -> str:
    suffix = f"-ls{variant['long_side']}" if variant.get("long_side") else ""
    if variant.get("damage") is not None and "damage_override" in variant:
        suffix += f"-d{variant['damage']}"
    return f"v1-{family}-{split}-{index:04d}{suffix}"


def build(family: str, index: int, *, split: str, seed: str | None = None,
          long_side: int | None = None, **overrides) -> tuple[dict, bytes, Rendered]:
    seed = seed if seed is not None else seed_for(split)
    rendered, variant = render_item(family, index, seed=seed, long_side=long_side, **overrides)
    data = png_bytes(rendered.image)
    iid = item_id_for(family, split, index, variant)
    seed_label = seed if split == "dev" else "private"
    meta = item_json(item_id=iid, family=family, split=split, seed_label=seed_label, index=index,
                     rendered=rendered, image_file=f"{iid}.png", image_sha256=sha256_bytes(data),
                     stamp=render_stamp(), variant=variant)
    return meta, data, rendered


def _inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(REPO_ROOT)
        return True
    except ValueError:
        return False


def tier_questions(meta: dict, *, questions_per_image: int, reading: bool, seed: int) -> dict:
    """Cut an item's questions to a tier's: the first ``questions_per_image`` scored questions in
    the tier's seeded order, plus the full reading only when the item is one of the tier's
    readings. The order does not depend on k, so a smaller tier's questions are a prefix."""
    from edat.media.tiers import question_order
    scored = [q for q in meta["questions"] if q["kind"] != "reading"]
    keep = set(question_order([q["qid"] for q in scored], seed=seed,
                              item_id=meta["item_id"])[:questions_per_image])
    qs = [q for q in meta["questions"]
          if (q["kind"] == "reading" and reading) or q["qid"] in keep]
    return dict(meta, questions=qs)


def write_items(family: str, indices, out_dir: Path, *, split: str,
                long_side: int | None = None, validate: bool = True,
                trim=None) -> list[dict]:
    if split == "heldout" and _inside_repo(out_dir):
        raise RuntimeError("held-out items must not be written inside the repository")
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for index in indices:
        meta, data, rendered = build(family, index, split=split, long_side=long_side)
        if validate:
            from edat.media.vision.validate import validate_item
            problems = validate_item(meta, rendered.image, clean_image=rendered.clean_image)
            if problems:
                raise RuntimeError(f"{meta['item_id']} failed validation: {problems}")
        if trim is not None:
            meta = trim(index, meta)
        (out_dir / meta["image"]["file"]).write_bytes(data)
        (out_dir / f"{meta['item_id']}.json").write_text(json.dumps(meta, indent=1) + "\n")
        written.append(meta)
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="write items")
    g.add_argument("--family", action="append", help="family (repeatable; default all)")
    g.add_argument("--split", default="dev", choices=("dev", "heldout"))
    g.add_argument("--count", type=int, default=FULL_SET_PER_FAMILY)
    g.add_argument("--start", type=int, default=0)
    g.add_argument("--long-side", type=int, default=None)
    g.add_argument("--tier", choices=("core", "extended", "full"),
                   help="write the tier's images, questions and readings instead of --start/--count")
    g.add_argument("--out", required=True)
    sub.add_parser("init-heldout-seed", help="create the private held-out seed")
    sub.add_parser("families", help="list families")
    args = ap.parse_args(argv)
    if args.cmd == "families":
        print("\n".join(available_families()))
        return 0
    if args.cmd == "init-heldout-seed":
        private = os.environ.get("EDAT_PRIVATE_DIR")
        if not private:
            print("EDAT_PRIVATE_DIR is not set", file=sys.stderr)
            return 2
        path = Path(private) / "media" / "heldout_seed"
        if path.exists():
            print("held-out seed already exists; not replacing it")
            return 0
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(os.urandom(16).hex() + "\n")
        print("held-out seed created")
        return 0
    fams = args.family or available_families()
    out = Path(args.out)
    v1 = None
    if args.tier:
        from edat.media import tiers
        m = tiers.load_manifest(args.tier)
        v1 = tiers.select_vision(m)["v1"]
    for fam in fams:
        if v1 is None:
            indices, trim = range(args.start, args.start + args.count), None
        else:
            sel = v1.get(fam, {"indices": [], "readings": [], "questions_per_image": 0})
            indices, readings = sel["indices"], set(sel["readings"])

            def trim(index, meta, sel=sel, readings=readings):
                return tier_questions(meta, questions_per_image=sel["questions_per_image"],
                                      reading=index in readings, seed=m.seed)
        metas = write_items(fam, indices, out / fam, split=args.split,
                            long_side=args.long_side, trim=trim)
        nq = sum(len(m["questions"]) for m in metas)
        print(f"{fam}: {len(metas)} images, {nq} questions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
