"""Family P, pixel grounding (spec 11 §3.7; diagnostic, vision models only, full set only).

"Click X" on an unmarked screenshot, answered with `click_point(x, y)`;
correct when the point falls inside the element's box (`render/som.py`
`score_point`). 300 items in the full set, half in each split, at L1 (a
small screen) and L2 (a large or crowded screen, or a label that appears in
two sections, told apart by the section name).

Targets are chosen by binding each start screen on world seeds 1-3 and
keeping the controls whose label is the same on every seed and visible
without scrolling, so the task text never depends on the seed. The
`ax_text` oracle check only proves the target is there; the hit test needs
the screenshot renderer (S6).
"""
from __future__ import annotations

import random

from edat.screen.author.common import base_case, case_id

STARTS = [
    ("settings", {"app": "settings"}, ["settings"]),
    ("settings", {"app": "settings", "screen": "battery"}, ["settings"]),
    ("settings", {"app": "settings", "screen": "display"}, ["settings"]),
    ("settings", {"app": "settings", "screen": "privacy"}, ["settings"]),
    ("settings", {"app": "settings", "screen": "general"}, ["settings"]),
    ("mail", {"app": "mail"}, ["mail", "messages", "settings"]),
    ("messages", {"app": "messages"}, ["mail", "messages", "settings"]),
    ("calendar", {"app": "calendar"}, ["calendar", "notes", "files"]),
    ("notes", {"app": "notes"}, ["calendar", "notes", "files"]),
    ("files", {"app": "files"}, ["calendar", "notes", "files"]),
    ("browser", {"app": "browser", "url": "shop.example/search?q=bottle"}, ["browser"]),
    ("browser", {"app": "browser", "url": "news.example/"}, ["browser"]),
]
SPLIT_SHARE = 150
PHRASES = ["Click {what}.", "Press {what}.", "Tap {what} on the screen.", "Click on {what}."]


def _stable_targets(start: dict, apps: list[str], extra: dict) -> list[tuple[str, str, str]]:
    from edat.screen.cases.bind import bind
    seen = None
    for s in (1, 2, 3):
        c = {"id": "scr_pixel_probe_001", "family": "pixel", "level": 1, "start": start,
             "world": {"apps": apps, "params": extra}, "task": "-", "oracle": [{"finish": True}]}
        try:
            vis = bind(c, s).world.observe()
        except Exception:   # an app not built in this checkout
            return []
        if vis.dialog is not None:
            return []
        cur = {e.key: (e.role, e.name) for e in vis.by_id.values()
               if e.clickable and e.name and not e.meta.get("dock") and not e.disabled}
        seen = cur if seen is None else {k: v for k, v in seen.items() if cur.get(k) == v}
    names = [v[1] for v in (seen or {}).values()]
    return sorted((k, r, n) for k, (r, n) in (seen or {}).items() if names.count(n) == 1)


def generate(split: str, rng: random.Random, n: int = SPLIT_SHARE) -> list[dict]:
    pools = {}
    for level, extra in ((1, {"settings": {"extra_rows": 6}, "mail": {"inbox_size": 5},
                              "browser": {"news": {"cookie_banner": False}}}),
                         (2, {"settings": {"extra_rows": 36}, "mail": {"inbox_size": 40},
                              "browser": {"news": {"cookie_banner": False}, "shop": {"similar_items": 3}}})):
        pool = []
        for app, start, apps in STARTS:
            for key, role, name in _stable_targets(start, apps, extra):
                pool.append((app, start, apps, key, role, name, extra))
        rng.shuffle(pool)
        pools[level] = pool
    if not pools[1] or not pools[2]:
        return []
    out = []
    for i in range(n):
        level = 1 if i < n // 2 else 2
        pool = pools[level]
        app, start, apps, key, role, name, extra = pool[i % len(pool)]
        what = f'the "{name}" {role if role != "row" else "item"}'
        out.append(base_case(case_id("pixel", split, f"{app}_{role}", i + 1), "pixel", level,
                             task=rng.choice(PHRASES).format(what=what), start=start, apps=apps,
                             params=extra, oracle=[{"click": key}, {"finish": True}],
                             first_action={"tool": "click", "target": key}, tags=["pixel"]))
        out[-1]["tracks"] = ["screenshot_raw"]
        out[-1].pop("world", None)
        out[-1]["world"] = {"apps": apps, "params": extra}
    return out
