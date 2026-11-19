"""Write the model authors' sampling settings into oMLX's per-model settings, for newly added models.

    python -m edat.sidekick.ceiling omlx-profile            # dry run: what would change, nothing sent
    python -m edat.sidekick.ceiling omlx-profile --apply    # back up, write, read back and check

This is a one-off step that needs its own go-ahead; the run never calls it. The benchmark runner sends
each entry's sampling with every request, so results do not depend on these settings. They only make
the engine's own defaults match the authors' advice for everyday use.

How it writes: through oMLX's own per-model settings endpoint (`PUT /admin/api/models/<id>/settings`), one
model at a time, sending only the listed fields. oMLX merges them into the model's entry and saves its
settings file itself. A hand edit of the file is not used: oMLX keeps the settings in memory, does not
see a changed file until it reloads (which also preloads pinned models), and rewrites the whole file
from memory whenever any setting is saved, so a hand edit can be lost.

Before writing, the settings file is copied to `model_settings.json.bak-sampling-<date>`. After
writing, the file is read back: every listed field must hold its new value, and every other model and
every other field must be exactly as in the backup. A model the engine does not list (not downloaded,
or not servable) is reported and left alone. If the engine refuses a write, the step stops there, reads
the file back all the same, and names the models already written and the backup.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

SETTINGS = Path.home() / ".omlx" / "model_settings.json"
_GEMMA = {"temperature": 1.0, "top_p": 0.95, "top_k": 64, "enable_thinking": True}
# oMLX model id -> the fields to write (the values in the authors' cards).
WRITE: dict[str, dict] = {
    "Qwen3.5-4B-oQ4e-mtp": {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0.0, "presence_penalty": 1.5,
                            "repetition_penalty": 1.0, "enable_thinking": True},
    "Qwen3.5-9B-oQ4e-mtp": {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0, "presence_penalty": 1.5,
                            "repetition_penalty": 1.0, "enable_thinking": False},
    "gemma-4-E2B-it-oQ4e-mtp": dict(_GEMMA),
    "gemma-4-E2B-it-oQ6e-mtp": dict(_GEMMA),
    "gemma-4-12B-it-oQ4e-mtp": dict(_GEMMA),
    "Ling-3.0-tiny-oQ4e": {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "enable_thinking": True},
    "Phi-4-mini-instruct-oQ4e": {"temperature": 0.0},
    "SenseNova-Loop-9B-MLX-4bit": {"temperature": 1.0, "top_p": 0.95},
}
# If the Qwen3.5 9B build turns out to switch thinking on after all, it gets the 4B's thinking-on values.
QWEN9_THINKING_ON = {**WRITE["Qwen3.5-4B-oQ4e-mtp"]}
NEVER = ("gemma-4-E4B-it-oQ4e-mtp", "gemma-4-E4B-it-r5-step-01800-oQ4e-mtp")   # the two references


def payloads(qwen9_thinks: bool = False) -> dict[str, dict]:
    out = {k: dict(v) for k, v in WRITE.items()}
    if qwen9_thinks:
        out["Qwen3.5-9B-oQ4e-mtp"] = dict(QWEN9_THINKING_ON)
    assert not set(out) & set(NEVER)
    return out


def changes(current: dict, plan: dict[str, dict]) -> list[tuple[str, str, object, object]]:
    """(model, field, now, new) for every field that would change."""
    rows = []
    for mid, fields in plan.items():
        now = (current.get("models") or {}).get(mid)
        for k, v in fields.items():
            old = None if now is None else now.get(k)
            if old != v:
                rows.append((mid, k, old, v))
    return rows


def check_after(before: dict, after: dict, written: dict[str, dict]) -> list[str]:
    """Problems: a written field without its value, or anything else that differs from the backup."""
    bad = []
    bm, am = before.get("models") or {}, after.get("models") or {}
    for mid in sorted(set(bm) | set(am)):
        b, a = bm.get(mid), am.get(mid)
        w = written.get(mid)
        if w is None:
            if a != b:
                bad.append(f"{mid}: changed but was not to be touched")
            continue
        if a is None:
            bad.append(f"{mid}: no entry after writing")
            continue
        for k, v in w.items():
            if a.get(k) != v:
                bad.append(f"{mid}.{k}: {a.get(k)!r}, expected {v!r}")
        for k in sorted(set(b or {}) | set(a)):
            if k not in w and (b or {}).get(k) != a.get(k) and b is not None:
                bad.append(f"{mid}.{k}: changed from {(b or {}).get(k)!r} to {a.get(k)!r} but was not to be touched")
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m edat.sidekick.ceiling omlx-profile")
    ap.add_argument("--apply", action="store_true", help="write (needs the go-ahead); default is a dry run")
    ap.add_argument("--qwen9-thinks", action="store_true",
                    help="the Qwen3.5 9B build switched thinking on in its smoke check: give it the thinking-on values")
    ap.add_argument("--settings", default=str(SETTINGS))
    a = ap.parse_args(argv)
    path = Path(a.settings)
    before = json.loads(path.read_text())
    plan = payloads(a.qwen9_thinks)
    from scripts.productbench import engine_prep
    with engine_prep._client() as c:
        listed = {m["id"] for m in c.get("/v1/models").json().get("data", [])}
    todo = {k: v for k, v in plan.items() if k in listed}
    for mid in sorted(set(plan) - set(todo)):
        print(f"not listed by the engine, left alone: {mid}")
    rows = changes(before, todo)
    for mid, k, old, new in rows:
        print(f"{mid}: {k} {old!r} -> {new!r}")
    if not rows:
        print("nothing to change")
        return 0
    if not a.apply:
        print("dry run: nothing written (add --apply once the go-ahead is given)")
        return 0
    backup = path.with_name(f"{path.name}.bak-sampling-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, backup)
    print(f"backup: {backup}")
    written: dict[str, dict] = {}
    refused = None
    with engine_prep._client() as c:
        for mid, fields in todo.items():
            r = c.put(f"/admin/api/models/{mid}/settings", json=fields)
            if r.status_code >= 400:
                refused = f"{mid}: refused ({r.status_code}): {r.text[:200]}"
                break
            written[mid] = fields
    after = json.loads(path.read_text())
    bad = check_after(before, after, written)       # what was written must hold; nothing else may change
    for b in bad:
        print(f"CHECK FAILED: {b}", file=sys.stderr)
    if refused:
        print(f"{refused}; stopped after {len(written)} of {len(todo)} models", file=sys.stderr)
        print(f"already written: {', '.join(written) or 'none'}; the backup is {backup}", file=sys.stderr)
    print("read back: every written field holds its value; nothing else changed" if not bad else
          f"read back: {len(bad)} problems; the backup is {backup}")
    return 2 if refused else (1 if bad else 0)
