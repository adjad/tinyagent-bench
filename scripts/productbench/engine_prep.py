"""Before and after a live in-assistant run: one model resident in oMLX, then put things back.

    python -m scripts.productbench.engine_prep status
    python -m scripts.productbench.engine_prep keep <engine id> --save <run folder>/resident-before.json
    python -m scripts.productbench.engine_prep restore <run folder>/resident-before.json
    python -m scripts.productbench.engine_prep weights <model folder>

Every engine call takes `--engine-port` (default `$TINYAGENT_ENGINE_PORT`, else 8000), the same
default the runner's `--engine-port` uses, so unloads and chats go to one engine.

`status` only reads (GET /v1/models/status). `keep` writes what was loaded to --save the first
time (never overwrites it), then unloads every other loaded chat model, so only the model under
test can be resident (the user's standing rule: never two models loaded at once). The engine
loads the model under test itself on the first request. `restore` unloads what the run left
and loads back exactly what was resident before. Helper models (embedders, rerankers) are left
alone. Only for an approved run window; it never starts, stops or restarts oMLX, and it never
writes an oMLX setting.

The save file also records each model's pinned and default flags (oMLX's model_settings.json,
read only) and when it was written. `restore` loads back what was resident, then checks the
flags against the saved ones and exits 3 if any differ (it never writes them; a person decides).

A guarded session (session.py) passes `--token`: `keep --token T` writes T into a new save file
and refuses an existing file that does not carry T (a state saved by an earlier run, in the same
folder or not, is never reused), and `restore --token T` refuses a file without T. Without
`--token` both behave as before, for callers that keep one save file across several models.

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
import time
from pathlib import Path

import httpx

AUX = ("embed", "rerank", "bge", "e5-", "minilm", "colbert")
PORT = int(os.environ.get("TINYAGENT_ENGINE_PORT", "8000"))
SETTINGS = Path.home() / ".omlx" / "model_settings.json"
FLAGS = ("is_pinned", "is_default")


class StaleSave(SystemExit):
    """A save file this session did not write: never restored, never reused."""


def _headers() -> dict:
    try:
        settings = json.loads((Path.home() / ".omlx" / "settings.json").read_text())
        key = (settings.get("auth") or {}).get("api_key")
    except (OSError, ValueError):
        key = None
    return {"Authorization": f"Bearer {key}"} if key else {}


def _client(port: int | None = None) -> httpx.Client:
    """The engine on this Mac only: the base URL is always loopback; only the port varies."""
    return httpx.Client(base_url=f"http://127.0.0.1:{int(port or PORT)}", headers=_headers(), timeout=120)


def model_flags(settings: Path | None = None) -> dict[str, dict]:
    """{model id: {"is_pinned": bool, "is_default": bool}} for models with either flag set. Read only."""
    try:
        data = json.loads(Path(settings or SETTINGS).read_text())
    except (OSError, ValueError):
        return {}
    rows = data.get("models", data) if isinstance(data, dict) else {}
    out = {}
    for mid, row in (rows.items() if isinstance(rows, dict) else []):
        if isinstance(row, dict) and any(row.get(f) for f in FLAGS):
            out[mid] = {f: bool(row.get(f)) for f in FLAGS}
    return out


def read_save(path: Path, token: str | None) -> dict:
    """The saved state; with a token, only a file that carries the same one."""
    data = json.loads(Path(path).read_text())
    if token is not None and data.get("token") != token:
        raise StaleSave(f"{path} was not written by this session (token {data.get('token')!r}, saved "
                        f"{data.get('saved_at')!r}): not restoring or reusing it; remove it once you "
                        f"have checked what it holds")
    return data


def write_save(path: Path, now_loaded: list[str], token: str | None, port: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"loaded": now_loaded, "flags": model_flags(), "token": token,
                                "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "engine_port": port}))


def flag_changes(saved: dict, now: dict) -> list[str]:
    """Models whose pinned or default flag differs from the saved state."""
    return sorted(m for m in set(saved) | set(now)
                  if any(bool(saved.get(m, {}).get(f)) != bool(now.get(m, {}).get(f)) for f in FLAGS))


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
    port_help = "the engine's port (default $TINYAGENT_ENGINE_PORT, else 8000; loopback only)"
    st = sub.add_parser("status")
    st.add_argument("--engine-port", type=int, default=PORT, help=port_help)
    k = sub.add_parser("keep")
    k.add_argument("model")
    k.add_argument("--save", required=True)
    k.add_argument("--token", default=None, help="the session's token (session.py)")
    k.add_argument("--engine-port", type=int, default=PORT, help=port_help)
    r = sub.add_parser("restore")
    r.add_argument("saved")
    r.add_argument("--token", default=None, help="restore only a file this session wrote")
    r.add_argument("--engine-port", type=int, default=PORT, help=port_help)
    w = sub.add_parser("weights")
    w.add_argument("folder")
    args = ap.parse_args(argv)
    if args.cmd == "weights":
        print(weights_sha256(args.folder))
        return 0
    port = args.engine_port
    if args.cmd == "keep" and args.token is not None and Path(args.save).exists():
        read_save(Path(args.save), args.token)            # refuses a file this session did not write
    if args.cmd == "restore":
        saved = read_save(Path(args.saved), args.token)
        if saved.get("engine_port") not in (None, port):
            raise StaleSave(f"{args.saved} was saved from the engine on port {saved['engine_port']}, "
                            f"not {port}: not restoring")
    with _client(port) as c:
        now = loaded(c)
        if args.cmd == "status":
            print(json.dumps({"loaded": now}))
            return 0
        if args.cmd == "keep":
            save = Path(args.save)
            if not save.exists():
                write_save(save, now, args.token, port)
            pinned = model_flags()
            for m in plan_keep(now, args.model):
                if (pinned.get(m) or {}).get("is_pinned"):
                    print(f"{m} is pinned; unloading it for the run (restore loads it back)")
                print(f"unloading {m}")
                c.post(f"/v1/models/{m}/unload").raise_for_status()
            left = [m for m in loaded(c) if not is_aux(m) and m != args.model]
            if left:
                print(f"still loaded: {left}; stopping")
                return 1
            print(f"ready: only {args.model} may load")
            return 0
        before = saved.get("loaded", [])
        unload, load = plan_restore(now, before)
        for m in unload:
            print(f"unloading {m}")
            c.post(f"/v1/models/{m}/unload").raise_for_status()
        for m in load:
            print(f"loading {m}")
            c.post(f"/v1/models/{m}/load").raise_for_status()
        print(f"restored: {before}")
        if "flags" in saved:
            changed = flag_changes(saved["flags"], model_flags())
            if changed:
                print(f"pinned or default flags differ from the saved state for {changed}; "
                      f"not changing any oMLX setting: check them by hand")
                return 3
        return 0


if __name__ == "__main__":
    sys.exit(main())
