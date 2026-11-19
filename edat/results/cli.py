"""Command line for the EDAT results store.

    python -m edat.results validate RUN_DIR [RUN_DIR ...] [--deep]
    python -m edat.results merge RUN_DIR RUN_DIR [...] --out DIR
    python -m edat.results aggregate RUN_DIR [...] [--json OUT]
    python -m edat.results compare RUN_DIR RUN_DIR
    python -m edat.results export RUN_DIR [...] --data-dir data/results --site-dir site/data
    python -m edat.results diff SNAPSHOT_A SNAPSHOT_B [--allow-board-change]
    python -m edat.results convert toolbench|perf|productbench SRC --board-version V [...]
    python -m edat.results sample --data-dir data/results --site-dir site/data

Exit codes: 0 ok, 1 validation or integrity failure, 2 refusal (results not
comparable), 3 usage error.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from edat.results import keys
from edat.results.aggregate import DEFAULT_REFERENCES, Options, aggregate
from edat.results.convert import ConversionError, convert_perf, convert_productbench, convert_toolbench
from edat.results.export import diff_snapshots, export, load_snapshot
from edat.results.reader import IntegrityError, merge_runs, read_set, thinking_by_model
from edat.results.schema import ValidationError
from edat.results.writer import default_store

OK, INVALID, REFUSED, USAGE = 0, 1, 2, 3


def _options(args) -> Options:
    refs = {k: tuple(v) for k, v in DEFAULT_REFERENCES.items()}
    if getattr(args, "tuned", None):
        refs["tuned"] = tuple(args.tuned)
    if getattr(args, "base", None):
        refs["base"] = tuple(args.base)
    return Options(references=refs, resamples=getattr(args, "resamples", 10_000))


def _sets(paths) -> list:
    return [read_set(p) for p in paths]


def cmd_validate(args) -> int:
    bad = 0
    for p in args.runs:
        try:
            s = read_set(p, deep=args.deep)
            print(f"ok       {p}: {len(s.records)} records, {len({keys.entry_key(r) for r in s.records})} entries")
        except (ValidationError, IntegrityError, keys.IncompatibleResults, FileNotFoundError, ValueError) as e:
            bad += 1
            print(f"INVALID  {p}: {e}")
    return INVALID if bad else OK


def cmd_merge(args) -> int:
    s = merge_runs(_sets(args.runs), args.out, allow_thinking_mismatch=args.allow_two_entries)
    print(f"merged {len(args.runs)} runs into {s.path}: {len(s.records)} records")
    return OK


def _summary_lines(agg: dict) -> list[str]:
    lines = [f"board version {agg['board_version']}" + ("  (SAMPLE DATA)" if agg.get("sample") else "")]
    for e in agg["entries"]:
        tag = "ranked" if e["ranked"] else e["rank_group"].replace("_", " ")
        lines.append(f"\n{e['device_id']}  {e['entry_key']}  [{tag}; fit {e['fit']['verdict']}]")
        for tier, b in sorted(e["tiers"].items()):
            h = b.get("headline")
            if h:
                lo, hi = h["interval"] or (None, None)
                rng = f" [{100 * lo:.1f}, {100 * hi:.1f}]" if lo is not None else ""
                vs = (e.get("vs", {}).get("tuned") or {}).get("tiers", {}).get(tier) or {}
                d = vs.get(h["metric"]) or vs.get(h["source"]) or vs.get("pass1")
                dtxt = (f"  Δtuned {100 * d['delta']:+.1f} pp ({d['tag'].replace('_', ' ')})" if d and "delta" in d
                        else ("  (refused: " + vs["refused"] + ")" if vs.get("refused") else ""))
                lines.append(f"  {tier:<13} {h['metric']:<16} {100 * h['value']:5.1f}%{rng}{dtxt}")
            s = b.get("safety") or {}
            if s.get("unsafe_rate") is not None or s.get("leak_rate") is not None:
                parts = []
                if s.get("unsafe_rate") is not None:
                    parts.append(f"unsafe {100 * s['unsafe_rate']:.1f}% of trials")
                if s.get("leak_rate") is not None:
                    parts.append(f"leaks {100 * s['leak_rate']:.1f}% of trials")
                lines.append(f"  {'':<13} safety: " + ", ".join(parts))
            if b.get("excluded"):
                lines.append(f"  {'':<13} excluded {b['excluded']} ({100 * b['excluded_rate']:.1f}%)"
                             + ("  INVALID: over 2 %" if b.get("invalid") else ""))
    for w in agg.get("warnings") or ():
        lines.append(f"warning: {w}")
    return lines


def cmd_aggregate(args) -> int:
    records = [r for s in _sets(args.runs) for r in s.records]
    agg = aggregate(records, _options(args))
    if args.json:
        Path(args.json).write_text(json.dumps(agg, indent=1, default=str) + "\n")
    print("\n".join(_summary_lines(agg)))
    return OK


def cmd_compare(args) -> int:
    """Two runs of the same tier: refuse unless comparable, then aggregate them together."""
    a, b = _sets([args.a, args.b])
    if not a.records or not b.records:
        raise keys.IncompatibleResults("refusing to compare: one of the runs has no records")
    keys.check_same(a.comparison_key, b.comparison_key, what="compare these runs",
                    a_name=Path(args.a).name, b_name=Path(args.b).name)
    ta, tb = thinking_by_model(a.records), thinking_by_model(b.records)
    for model in set(ta) & set(tb):
        keys.check_same_thinking(ta[model], tb[model], what=f"compare runs of {model}")
    if len({x for m in (ta, tb) for v in m.values() for x in v}) > 1 and not (set(ta) & set(tb)):
        modes_a = {x for v in ta.values() for x in v}
        modes_b = {x for v in tb.values() for x in v}
        keys.check_same_thinking(modes_a, modes_b, what="compare these runs")
    agg = aggregate(a.records + b.records, _options(args))
    print("\n".join(_summary_lines(agg)))
    return OK


def cmd_export(args) -> int:
    res = export(_sets(args.runs), data_dir=args.data_dir, site_dir=args.site_dir, options=_options(args))
    for p in res["written"]:
        print(f"wrote {p}")
    return OK


def cmd_diff(args) -> int:
    d = diff_snapshots(load_snapshot(args.a), load_snapshot(args.b), allow_board_change=args.allow_board_change)
    if args.json:
        print(json.dumps(d, indent=1))
        return OK
    print(f"device {d['device_id']}: board {d['board_versions'][0]} -> {d['board_versions'][1]}")
    for k in d["added"]:
        print(f"  + {k}")
    for k in d["removed"]:
        print(f"  - {k}")
    for c in d["changes"]:
        if "change" in c:
            mark = "" if c["intervals_overlap"] else "  (intervals do not overlap)"
            print(f"  {c['entry']}  {c['tier']} {c['metric']}: {100 * c['before']:.1f}% -> {100 * c['after']:.1f}%"
                  f" ({100 * c['change']:+.1f} pp){mark}")
        else:
            print(f"  {c['entry']}  {c['tier']} {c['metric']}: {c['before']} -> {c['after']}")
    for r in d["refused"]:
        print(f"  not compared: {r['entry']} {r['tier']}: {r['reason']}")
    return OK


def _kv(pairs) -> dict:
    out = {}
    for p in pairs or ():
        if "=" not in p:
            raise SystemExit(f"expected MODEL=VALUE, got {p!r}")
        k, v = p.rsplit("=", 1)
        out[k] = v
    return out


def cmd_convert(args) -> int:
    store = Path(args.store).expanduser() if args.store else default_store()
    common = {"board_version": args.board_version, "harness_commit": args.harness_commit, "run_dir": args.out}
    if args.format == "toolbench":
        out = convert_toolbench(args.src, store, device_id=args.device_id, default_thinking=args.default_thinking,
                                weights=_kv(args.weights), quant=_kv(args.quant), **common)
    elif args.format == "perf":
        out = convert_perf(args.src, store, device_id=args.device_id, sampling_source=args.sampling_source, **common)
    else:
        out = convert_productbench(args.src, store, device_id=args.device_id, default_thinking=args.default_thinking,
                                   sampling_source=args.sampling_source, **common)
    s = read_set(out)
    print(f"converted {args.src} -> {out}: {len(s.records)} records")
    return OK


def cmd_sample(args) -> int:
    from edat.results.sample import build_sample_export
    with tempfile.TemporaryDirectory() as tmp:
        res = build_sample_export(Path(tmp), Path(args.data_dir), Path(args.site_dir), resamples=args.resamples,
                                  write_records=not args.no_records)
    for p in res["written"]:
        print(f"wrote {p}")
    return OK


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m edat.results", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    def refs(sp):
        sp.add_argument("--tuned", nargs="+", metavar="MODEL_ID", help="model ids of the tuned baseline")
        sp.add_argument("--base", nargs="+", metavar="MODEL_ID", help="model ids of the base reference")
        sp.add_argument("--resamples", type=int, default=10_000)

    sp = sub.add_parser("validate", help="check run directories or merged sets")
    sp.add_argument("runs", nargs="+")
    sp.add_argument("--deep", action="store_true", help="also re-hash every raw file")
    sp.set_defaults(fn=cmd_validate)

    sp = sub.add_parser("merge", help="combine runs of one tier under one comparison key")
    sp.add_argument("runs", nargs="+")
    sp.add_argument("--out", required=True)
    sp.add_argument("--allow-two-entries", action="store_true",
                    help="allow one model in different thinking modes in different runs (two separate entries)")
    sp.set_defaults(fn=cmd_merge)

    sp = sub.add_parser("aggregate", help="per-entry scores, intervals, references, fit gate")
    sp.add_argument("runs", nargs="+")
    sp.add_argument("--json")
    refs(sp)
    sp.set_defaults(fn=cmd_aggregate)

    sp = sub.add_parser("compare", help="two runs side by side; refuses incomparable runs")
    sp.add_argument("a")
    sp.add_argument("b")
    refs(sp)
    sp.set_defaults(fn=cmd_compare)

    sp = sub.add_parser("export", help="write a snapshot and the website files")
    sp.add_argument("runs", nargs="+")
    sp.add_argument("--data-dir", default="data/results")
    sp.add_argument("--site-dir", default="site/data")
    refs(sp)
    sp.set_defaults(fn=cmd_export)

    sp = sub.add_parser("diff", help="what changed between two snapshots")
    sp.add_argument("a")
    sp.add_argument("b")
    sp.add_argument("--allow-board-change", action="store_true",
                    help="list changes across board versions without comparing scores")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_diff)

    sp = sub.add_parser("convert", help="turn existing harness output into an EDAT run")
    sp.add_argument("format", choices=("toolbench", "perf", "productbench"))
    sp.add_argument("src")
    sp.add_argument("--board-version", required=True)
    sp.add_argument("--store", help="results store root (default $EDAT_RESULTS_DIR or ~/.edat/results)")
    sp.add_argument("--out", help="write the run here instead of <store>/runs/<run_id>")
    sp.add_argument("--device-id")
    sp.add_argument("--harness-commit")
    sp.add_argument("--default-thinking", choices=("on", "off"),
                    help="what a run's 'default' thinking variant was (results-record.md D2)")
    sp.add_argument("--weights", nargs="*", metavar="MODEL=FINGERPRINT")
    sp.add_argument("--quant", nargs="*", metavar="MODEL=SCHEME")
    sp.add_argument("--sampling-source", default=None,
                    choices=("author_recommended", "fixed_sensitivity", "engine_default"))
    sp.set_defaults(fn=cmd_convert)

    sp = sub.add_parser("sample", help="write the synthetic sample snapshot (invented models)")
    sp.add_argument("--data-dir", default="data/results")
    sp.add_argument("--site-dir", default="site/data")
    sp.add_argument("--resamples", type=int, default=2000)
    sp.add_argument("--no-records", action="store_true",
                    help="write snapshot.json without records.jsonl (what the repository keeps)")
    sp.set_defaults(fn=cmd_sample)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if getattr(args, "cmd", None) == "convert" and args.sampling_source is None:
        args.sampling_source = "engine_default" if args.format == "perf" else "author_recommended"
    try:
        return args.fn(args)
    except keys.IncompatibleResults as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return REFUSED
    except (ValidationError, IntegrityError) as e:
        print(f"INVALID: {e}", file=sys.stderr)
        return INVALID
    except (ConversionError, FileNotFoundError, FileExistsError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return USAGE


if __name__ == "__main__":
    sys.exit(main())
