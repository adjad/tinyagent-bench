"""``python -m edat.media.tiers``: plan, select and sample the media tiers (spec 05 §17)."""
from __future__ import annotations

import argparse
import json
import sys

from edat.media import tiers as T


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m edat.media.tiers",
                                 description="tinyagent-bench media tiers: core, extended, full")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, help_ in (("plan", "counts, intervals and hours per model for a tier"),
                        ("select", "the tier's item selection as JSON"),
                        ("write-selection", "write ids/selection.<tier>.json (generated families)")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--tier", default=T.DEFAULT_TIER, choices=T.TIERS)
    s = sub.add_parser("sample-ids", help="write a public set's id list from its listing")
    s.add_argument("--set", required=True, choices=sorted(T.PUBLIC_SETS))
    s.add_argument("--listing", required=True, help="file of <id><TAB><stratum> lines")
    s.add_argument("--tier", action="append", choices=T.TIERS,
                   help="tiers to write (default core and extended)")
    sub.add_parser("check", help="check that the manifests nest: core ⊂ extended ⊂ full")
    a = ap.parse_args(argv)
    if a.cmd == "check":
        ms = [T.load_manifest(t) for t in T.TIERS]
        problems = T.check_nesting(ms[0], ms[1]) + T.check_nesting(ms[1], ms[2])
        print("\n".join(problems) or "manifests nest: core ⊂ extended ⊂ full")
        return 1 if problems else 0
    if a.cmd == "sample-ids":
        listing = T.read_listing(a.listing)
        for tier in a.tier or ["core", "extended"]:
            print(T.write_id_list(a.set, listing, tier))
        return 0
    sel = T.select(a.tier)
    if a.cmd == "select":
        json.dump(sel.to_json(), sys.stdout, indent=1)
        print()
        return 0
    if a.cmd == "write-selection":
        path = T.selection_path(a.tier)
        path.write_text(json.dumps(T.generated_selection(sel), indent=1, sort_keys=True) + "\n")
        print(path)
        return 0
    groups = T.group_sizes(sel)
    hours = T.estimate_hours(a.tier)
    print(f"tier {a.tier}  manifest sha256 {sel.manifest.sha256}")
    print(f"{'group':38} {'n':>6}  ±pts")
    for k, n in groups.items():
        print(f"{k:38} {n:6d}  {T.half_width(n):5.1f}")
    print(f"public id lists not written yet (need the approved downloads): "
          f"{', '.join(sel.pending) or 'none'}")
    print(f"audio {hours['audio_minutes_per_trial']} min + vision {hours['vision_minutes_per_trial']} "
          f"min per trial; {hours['trials']} trials + {hours['strip_hours']} h stripping = "
          f"{hours['subtotal_hours']} h; with overhead {hours['hours_per_model']} h per model")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
