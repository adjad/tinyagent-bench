"""Media workloads for the performance runner (spec 05 §8 V4, §4 A2, §5 A9).

This part owns *what* is sent; the performance runner (``edat/perf/``, spec 04)
owns the samplers (memory at 10 Hz, power, the fit verdict) and the run
document. Each workload is a plain JSON object, schema
``edat.media.workload/1``:

``id``             stable id, ``<suite>/<test>/<level>``
``suite``          ``v4`` (image cost) or ``a2`` / ``a9`` (audio cost)
``test``           ``image_tokens``, ``ttft_images``, ``decode_with_images``,
                   ``preprocess``, ``audio_tokens``, ``ttft_audio``, ``audio_memory``,
                   ``audio_energy``
``level``          what varies: ``long_side``, ``images``, ``tiling``, ``audio_s``
``inputs``         item references: V1 items by (family, index, long side) or
                   generated clips by (kind, seconds); :func:`materialize` turns
                   them into bytes
``prompt``         the fixed text part
``max_tokens``     fixed per test
``pair``           the id of the matching no-media request (same prompt, no image
                   or audio) whose prompt tokens and TTFT are subtracted
``measure``        the numbers this workload exists for
``trials``         repeats (warm), after one cold request where ``cold`` is true

Derived numbers (spec 05 §8):

* image tokens = prompt tokens(with image) − prompt tokens(pair);
* image encode + prefill = TTFT(with) − TTFT(pair);
* memory added per image = slope of peak memory over N = 1, 2, 4, 8;
* energy per image = energy(with) − energy(pair);
* audio tokens per second and encode time per audio second likewise.
"""
from __future__ import annotations

import json
from typing import Iterable

WORKLOAD_SCHEMA = "edat.media.workload/1"
LONG_SIDES = (512, 768, 1024, 1568, 2048, 3000)
N_IMAGES = (1, 2, 4, 8)
AUDIO_LENGTHS_S = (2, 5, 10, 20, 30)
GEMMA4_IMAGE_BUDGETS = (70, 140, 280, 560, 1120)    # selectable soft-token budgets (spec 05 §8)

# Fixed V1 items the cost workloads use: a text-dense document and a phone screenshot.
DOC_ITEM = ("documents", 1)
SCREEN_ITEM = ("app_screens", 0)
COST_PROMPT = "Describe this image in one sentence."
READ_PROMPT = ("Read all the text in the image in reading order. Reply with the text only, "
               "one line of the image per line.")


def _wl(**kw) -> dict:
    return {"schema": WORKLOAD_SCHEMA, "trials": 5, "cold": False, **kw}


def vision_workloads() -> list[dict]:
    out = [_wl(id="v4/no_image/0", suite="v4", test="pair", level={}, inputs=[],
               prompt=COST_PROMPT, max_tokens=64, pair=None,
               measure=["prompt_tokens", "ttft_s", "footprint_peak_bytes", "energy_J"])]
    for fam, idx in (DOC_ITEM, SCREEN_ITEM):
        for ls in LONG_SIDES:
            out.append(_wl(id=f"v4/image_tokens/{fam}-ls{ls}", suite="v4", test="image_tokens",
                           level={"long_side": ls, "images": 1},
                           inputs=[{"family": fam, "index": idx, "long_side": ls}],
                           prompt=COST_PROMPT, max_tokens=64, pair="v4/no_image/0", cold=ls == 1568,
                           measure=["prompt_tokens", "ttft_s", "decode_tps", "energy_J"]))
    for n in N_IMAGES:
        out.append(_wl(id=f"v4/ttft_images/n{n}", suite="v4", test="ttft_images",
                       level={"images": n, "long_side": 1568},
                       inputs=[{"family": fam, "index": i, "long_side": 1568}
                               for fam, i in _distinct_items(n)],
                       prompt=COST_PROMPT, max_tokens=64, pair="v4/no_image/0", cold=n == 1,
                       measure=["prompt_tokens", "ttft_s", "footprint_peak_bytes", "energy_J"]))
        out.append(_wl(id=f"v4/decode_with_images/n{n}", suite="v4", test="decode_with_images",
                       level={"images": n, "long_side": 1568},
                       inputs=[{"family": fam, "index": i, "long_side": 1568}
                               for fam, i in _distinct_items(n)],
                       prompt=READ_PROMPT, max_tokens=256, pair=None,
                       measure=["decode_tps", "footprint_peak_bytes"]))
    # Harbor's tiling: one 1568 px overview + up to 8 overlapping 1400 px sections.
    out.append(_wl(id="v4/ttft_images/harbor_tiling", suite="v4", test="ttft_images",
                   level={"images": 9, "tiling": "harbor_overview_plus_sections", "long_side": 3000},
                   inputs=[{"family": DOC_ITEM[0], "index": DOC_ITEM[1], "long_side": 3000,
                            "tiling": "harbor"}],
                   prompt=READ_PROMPT, max_tokens=700, pair="v4/no_image/0",
                   measure=["prompt_tokens", "ttft_s", "footprint_peak_bytes", "energy_J"]))
    out.append(_wl(id="v4/ttft_images/single_downscaled", suite="v4", test="ttft_images",
                   level={"images": 1, "tiling": "none", "long_side": 1568},
                   inputs=[{"family": DOC_ITEM[0], "index": DOC_ITEM[1], "long_side": 1568}],
                   prompt=READ_PROMPT, max_tokens=700, pair="v4/no_image/0",
                   measure=["prompt_tokens", "ttft_s", "footprint_peak_bytes"]))
    for ls in (1568, 3000):
        out.append(_wl(id=f"v4/preprocess/ls{ls}", suite="v4", test="preprocess",
                       level={"long_side": ls}, inputs=[{"family": DOC_ITEM[0], "index": DOC_ITEM[1],
                                                         "long_side": ls, "tiling": "harbor"}],
                       prompt="", max_tokens=0, pair=None, measure=["preprocess_s"]))
    return out


def _distinct_items(n: int) -> list[tuple[str, int]]:
    fams = ("documents", "app_screens", "web_pages", "charts_tables")
    return [(fams[i % len(fams)], i // len(fams)) for i in range(n)]


def audio_workloads() -> list[dict]:
    out = [_wl(id="a2/no_audio/0", suite="a2", test="pair", level={}, inputs=[],
               prompt="transcribe", max_tokens=240, pair=None, measure=["prompt_tokens", "ttft_s"])]
    for s in AUDIO_LENGTHS_S:
        out.append(_wl(id=f"a2/ttft_audio/{s}s", suite="a2", test="ttft_audio",
                       level={"audio_s": s}, inputs=[{"clip": "speech", "seconds": s}],
                       prompt="transcribe", max_tokens=240, pair="a2/no_audio/0", cold=s == 5,
                       measure=["prompt_tokens", "ttft_s", "total_s", "energy_J"]))
    out.append(_wl(id="a9/audio_memory/60s", suite="a9", test="audio_memory",
                   level={"audio_s": 60, "window_s": 30, "overlap_s": 1},
                   inputs=[{"clip": "speech", "seconds": 60}], prompt="transcribe",
                   max_tokens=240, pair=None, trials=3,
                   measure=["footprint_peak_bytes", "footprint_steady_bytes"]))
    out.append(_wl(id="a9/audio_energy/600s", suite="a9", test="audio_energy",
                   level={"audio_s": 600, "window_s": 30, "overlap_s": 1},
                   inputs=[{"clip": "speech", "seconds": 600}], prompt="transcribe",
                   max_tokens=240, pair=None, trials=3, measure=["energy_J", "e2e_s"]))
    return out


def all_workloads() -> list[dict]:
    return vision_workloads() + audio_workloads()


def materialize_images(workload: dict) -> list[bytes]:
    """The PNG/JPEG bytes a vision workload sends, rendered from the public dev seed."""
    from edat.media.vision.generate import build, png_bytes
    out: list[bytes] = []
    for ref in workload["inputs"]:
        meta, png, rendered = build(ref["family"], ref["index"], split="dev",
                                    long_side=ref.get("long_side"))
        if ref.get("tiling") == "harbor":
            out.extend(harbor_tiles(png))
        else:
            out.append(png)
    return out


def harbor_tiles(png: bytes) -> list[bytes]:
    """Harbor's overview + sections for one picture (service/attachment_child.py), as bytes."""
    import base64
    from service.attachment_child import tiles
    plan = tiles(png)
    return [base64.b64decode(url.split(",", 1)[1]) for url in plan["images"]]


def dump(workloads: Iterable[dict]) -> str:
    return json.dumps(list(workloads), indent=1)


if __name__ == "__main__":
    print(dump(all_workloads()))
