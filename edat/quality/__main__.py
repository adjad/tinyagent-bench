"""Command line for tinyagent-bench's output-quality tests.

    python -m edat.quality run --model Gemma-Wisp --role baseline --base-url http://127.0.0.1:<port> [--thinking]
    python -m edat.quality run --cloud anthropic/claude-haiku-5.5 --provider <pinned provider> --seedless
    python -m edat.quality headline <run dir> [<run dir> ...] [--pref prefs.json --judge-validated]

`run` builds the client from the shared model adapter (`edat.models`, owned by
the agentic tier); it never loads, unloads or switches models. Held-out runs
read cases from and write records to $EDAT_PRIVATE_DIR only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from edat.quality import records as R
from edat.quality.headline import headline_report
from edat.quality.runner import Entry, run_quality
from edat.quality.splits import load_dev, load_heldout


def _client(a):
    try:
        from edat import models as M  # the shared adapter (build/agentic until merged)
    except ImportError:
        sys.exit("edat.models (the shared model adapter) is not on this branch yet")
    if a.cloud:
        return M.OpenRouterClient(a.cloud, provider=a.provider), M
    return M.OmlxClient(a.model, base_url=a.base_url), M


def _device(a) -> dict:
    if not a.device_id:
        sys.exit("a local run needs --device-id (and --weights-sha256 or --weights-fingerprint)")
    return {"id": a.device_id, "class": a.device_class or a.device_id,
            "ram_bytes": a.ram_gb * 2**30 if a.ram_gb else None, "os": a.os}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m edat.quality", description="tinyagent-bench output quality and text understanding")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run one entry over a split")
    r.add_argument("--model")
    r.add_argument("--role", default="candidate", choices=["baseline", "reference", "candidate", "variant"])
    r.add_argument("--base-url", default=None)
    r.add_argument("--cloud", default=None, help="OpenRouter id of a cloud reference (dev split only)")
    r.add_argument("--provider", default=None)
    r.add_argument("--seedless", action="store_true")
    r.add_argument("--thinking", action="store_true")
    r.add_argument("--fixed-sensitivity", action="store_true", help="temperature 0.6, top-p 0.95")
    r.add_argument("--temperature", type=float, default=None, help="the model author's recommended setting")
    r.add_argument("--top-p", type=float, default=None)
    r.add_argument("--top-k", type=int, default=None)
    r.add_argument("--context-window", type=int, default=None)
    r.add_argument("--weights-sha256", default=None, help="sha256 of the weights (a local record needs this or --weights-fingerprint)")
    r.add_argument("--weights-fingerprint", default=None)
    r.add_argument("--device-id", default=None, help="device id from the results store's device list (local runs)")
    r.add_argument("--device-class", default=None)
    r.add_argument("--ram-gb", type=int, default=None)
    r.add_argument("--os", default=None)
    r.add_argument("--split", default="dev", choices=["dev", "heldout"])
    r.add_argument("--board-version", required=True)
    r.add_argument("--harness-commit", required=True)
    r.add_argument("--store", default=None)
    r.add_argument("--limit", type=int, default=None)
    h = sub.add_parser("headline", help="headline scores and differences from run directories")
    h.add_argument("runs", nargs="+")
    h.add_argument("--pref", default=None, help="JSON {model_id: pref_judge_style_ctrl}")
    h.add_argument("--judge-validated", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        client, M = _client(a)
        if a.fixed_sensitivity:
            sampling, source = M.FIXED_SENSITIVITY, "fixed_sensitivity"
        elif any(v is not None for v in (a.temperature, a.top_p, a.top_k)):
            sampling = M.Sampling(temperature=a.temperature, top_p=a.top_p, top_k=a.top_k, source="author_recommended")
            source = "author_recommended"
        else:
            sampling, source = None, ("provider_default" if a.cloud else "engine_default")
        entry = Entry(a.cloud or a.model, role=a.role, kind="cloud_reference" if a.cloud else "local",
                      thinking=a.thinking, sampling=sampling, seedless=a.seedless, sampling_source=source,
                      runtime={"name": "openrouter" if a.cloud else "omlx", "version": None},
                      context_window=a.context_window, weights_sha256=a.weights_sha256,
                      weights_fingerprint=a.weights_fingerprint, upstream_provider=a.provider,
                      device=None if a.cloud else _device(a))
        fams = load_heldout() if a.split == "heldout" else load_dev()
        sink = run_quality(client, entry, fams, split=a.split, board_version=a.board_version,
                           harness_commit=a.harness_commit, limit=a.limit)
        sink.store = Path(a.store) if a.store else R.default_store(a.split)
        print(sink.write())
        return 0
    recs = [x for p in a.runs for x in R.read_records(Path(p))]
    pref = json.loads(Path(a.pref).read_text()) if a.pref else None
    print(json.dumps(headline_report(recs, pref=pref, judge_validated=a.judge_validated), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
