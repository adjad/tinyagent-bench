"""Before and after a live in-assistant run: one model resident in oMLX, then put things back.

    python -m scripts.productbench.engine_prep status
    python -m scripts.productbench.engine_prep keep <engine id> --save <run folder>/resident-before.json
    python -m scripts.productbench.engine_prep restore <run folder>/resident-before.json
    python -m scripts.productbench.engine_prep weights <model folder>

`status` only reads (GET /v1/models/status). `keep` writes what was loaded to --save the first
time (never overwrites it), then unloads every other loaded chat model, so only the model under
test can be resident (the user's standing rule: never two models loaded at once). The engine
loads the model under test itself on the first request. `restore` unloads what the run left
and loads back exactly what was resident before. Helper models (embedders, rerankers) are left
alone. Only for an approved run window; it never starts, stops or restarts oMLX.

`weights` reads a model folder's `*.safetensors` files and prints `weights_sha256` as the
results record defines it (sha256 over the sorted `<file name>\0<file sha256>` lines); it never
talks to the engine.

The engine's key, when it has one, is read from oMLX's own settings file at call time, sent
only to 127.0.0.1, and never printed or written.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import httpx

AUX = ("embed", "rerank", "bge", "e5-", "minilm", "colbert")
PORT = int(os.environ.get("TINYAGENT_ENGINE_PORT", "8000"))


def _headers() -> dict:
    try:
        settings = json.loads((Path.home() / ".omlx" / "settings.json").read_text())
        key = (settings.get("auth") or {}).get("api_key")
    except (OSError, ValueError):
        key = None
    return {"Authorization": f"Bearer {key}"} if key else {}


def _client() -> httpx.Client:
    return httpx.Client(base_url=f"http://127.0.0.1:{PORT}", headers=_headers(), timeout=120)


def is_aux(model_id: str) -> bool:
    return any(a in model_id.lower() for a in AUX)


def loaded(c: httpx.Client) -> list[str]:
    rows = c.get("/v1/models/status").json().get("models", [])
    return [m["id"] for m in rows if m.get("loaded") and m.get("id")]


def plan_keep(now_loaded: list[str], keep: str) -> list[str]:
    """What to unload so only `keep` (and helper models) can stay resident."""
    return [m for m in now_loaded if m != keep and not is_aux(m)]


def weights_sha256(folder: Path | str) -> str:
    """The results record's full-content weights hash for one model folder."""
    folder = Path(folder)
    files = sorted(p for p in folder.rglob("*.safetensors") if p.is_file())
    if not files:
        raise SystemExit(f"no .safetensors files in {folder}")
    lines = []
    for p in files:
        h = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 22), b""):
                h.update(chunk)
        lines.append(f"{p.relative_to(folder).as_posix()}\0{h.hexdigest()}\n")
    return hashlib.sha256("".join(sorted(lines)).encode()).hexdigest()


def plan_restore(now_loaded: list[str], before: list[str]) -> tuple[list[str], list[str]]:
    """(unload, load) to return to `before`, helper models untouched."""
    chat_before = [m for m in before if not is_aux(m)]
    return ([m for m in now_loaded if m not in chat_before and not is_aux(m)],
            [m for m in chat_before if m not in now_loaded])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    k = sub.add_parser("keep")
    k.add_argument("model")
    k.add_argument("--save", required=True)
    r = sub.add_parser("restore")
    r.add_argument("saved")
    w = sub.add_parser("weights")
    w.add_argument("folder")
    args = ap.parse_args(argv)
    if args.cmd == "weights":
        print(weights_sha256(args.folder))
        return 0
    with _client() as c:
        now = loaded(c)
        if args.cmd == "status":
            print(json.dumps({"loaded": now}))
            return 0
        if args.cmd == "keep":
            save = Path(args.save)
            if not save.exists():
                save.parent.mkdir(parents=True, exist_ok=True)
                save.write_text(json.dumps({"loaded": now}))
            for m in plan_keep(now, args.model):
                print(f"unloading {m}")
                c.post(f"/v1/models/{m}/unload").raise_for_status()
            left = [m for m in loaded(c) if not is_aux(m) and m != args.model]
            if left:
                print(f"still loaded: {left}; stopping")
                return 1
            print(f"ready: only {args.model} may load")
            return 0
        before = json.loads(Path(args.saved).read_text()).get("loaded", [])
        unload, load = plan_restore(now, before)
        for m in unload:
            print(f"unloading {m}")
            c.post(f"/v1/models/{m}/unload").raise_for_status()
        for m in load:
            print(f"loading {m}")
            c.post(f"/v1/models/{m}/load").raise_for_status()
        print(f"restored: {before}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
