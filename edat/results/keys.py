"""Entry keys, comparison keys and the refusals that keep comparisons honest.

docs/benchmark/results-record.md section 6 is the rulebook. Every refusal is an
`IncompatibleResults` whose message names the field that differs, so a person
can see at once why two runs cannot be merged or compared.
"""
from __future__ import annotations

from typing import Iterable

RECOMMENDED_SOURCES = {"author_recommended", "engine_default", "provider_default"}


class IncompatibleResults(ValueError):
    """Two sets of results cannot be merged, compared or differenced."""


def sampling_class(source: str | None) -> str:
    """`recommended` (each model at its author's or provider's settings) or
    `fixed_sensitivity` (temperature 0.6, top-p 0.95 for everyone)."""
    if source == "fixed_sensitivity":
        return "fixed_sensitivity"
    if source in RECOMMENDED_SOURCES:
        return "recommended"
    raise IncompatibleResults(f"unknown sampling_source {source!r}")


def weights_id(model: dict) -> str:
    """The full content hash when there is one, else Toolbench's fingerprint."""
    if model.get("weights_sha256"):
        return "sha256:" + model["weights_sha256"][:16]
    if model.get("weights_fingerprint"):
        return "fp:" + model["weights_fingerprint"][:16]
    return "-"


def entry_key(rec: dict) -> str:
    """What the board ranks: kind | model id | quantisation | runtime | thinking.
    The device is a separate dimension. Weights are not in the key, because one
    tier may record a full hash and another only Toolbench's fingerprint;
    check_entry_weights refuses two different weights under one key instead."""
    model = rec["model"]
    quant = "-" if rec["kind"] == "cloud_reference" else ((model.get("quant") or {}).get("scheme") or "-")
    kind = "cloud" if rec["kind"] == "cloud_reference" else "local"
    return "|".join([kind, model["id"], quant, rec["runtime"]["name"], rec["settings"]["thinking"]])


def check_entry_weights(records: Iterable[dict]) -> None:
    """Refuse one entry key that carries two different full hashes, or two
    different fingerprints: that is two different models under one name."""
    seen: dict[tuple, str] = {}
    for rec in records:
        ek = entry_key(rec)
        for field in ("weights_sha256", "weights_fingerprint"):
            v = rec["model"].get(field)
            if v and seen.setdefault((ek, field), v) != v:
                raise IncompatibleResults(
                    f"refusing to combine records of {ek}: they carry two different {field} values "
                    f"({seen[(ek, field)][:12]}… and {v[:12]}…). Give the second model its own id.")


def device_id(rec: dict) -> str:
    return "cloud" if rec.get("device") is None else rec["device"]["id"]


def selection_of(rec: dict) -> dict | None:
    """The record's selection tier (core, extended, full) and manifest, or None.

    Reads the `selection` field; records the media runner wrote before the field
    existed carry the same values in `metrics` (`media_tier`, `manifest_sha256`,
    `tier_n`, `ci95_half_width_pts`) and are read the same way."""
    sel = rec.get("selection")
    if sel:
        return sel
    m = rec.get("metrics") or {}
    if isinstance(m.get("media_tier"), str):
        return {"tier": m["media_tier"], "manifest_sha256": m.get("manifest_sha256"),
                "n_items": m.get("tier_n"), "ci95_half_width_pts": m.get("ci95_half_width_pts")}
    return None


def selection_key(rec: dict) -> str | None:
    """`<tier>/<manifest sha256>`: two records pool only when this is equal (None for both included)."""
    sel = selection_of(rec)
    return None if sel is None else f"{sel['tier']}/{sel.get('manifest_sha256')}"


def comparison_key(rec: dict) -> dict:
    """What every record of one run shares, and what two runs must share to be
    merged or compared (thinking is checked per entry, not per run)."""
    key = {"board_version": rec["board_version"], "harness_commit": rec["harness_commit"],
           "sampling_class": sampling_class(rec["settings"]["sampling_source"]),
           "lang": rec["lang"], "tier": rec["tier"],
           "policy_fingerprint": rec["settings"].get("policy_fingerprint")}
    sel = selection_key(rec)
    if sel is not None:
        key["selection"] = sel
    return key


_HARD = ("board_version", "harness_commit", "sampling_class", "lang", "tier", "selection")
_LABELS = {"board_version": "board version", "harness_commit": "harness commit",
           "sampling_class": "sampling", "lang": "language", "tier": "tier",
           "policy_fingerprint": "run policy", "selection": "selection tier (core/extended/full and manifest)"}
WITHOUT_SELECTION = tuple(f for f in _HARD if f != "selection")


def check_same(a: dict, b: dict, *, what: str, fields: Iterable[str] = _HARD,
               a_name: str = "first", b_name: str = "second") -> None:
    """Refuse when two comparison keys differ on any of `fields`."""
    diffs = [f for f in fields if a.get(f) != b.get(f)]
    # Toolbench's policy fingerprint (timeout, step ceiling, window, treatment, ...)
    # is a hard key when both sides record one (07-harness-review.md finding 9).
    if a.get("policy_fingerprint") and b.get("policy_fingerprint") and \
            a["policy_fingerprint"] != b["policy_fingerprint"]:
        diffs.append("policy_fingerprint")
    if diffs:
        detail = "; ".join(f"{_LABELS.get(f, f)}: {a_name} {a.get(f)!r}, {b_name} {b.get(f)!r}" for f in diffs)
        raise IncompatibleResults(f"refusing to {what}: the results were made under different "
                                  f"{', '.join(_LABELS.get(f, f) for f in diffs)} ({detail}). "
                                  "Results are comparable only within one board version, harness commit, "
                                  "sampling class and language.")


def check_entry_thinking(records: Iterable[dict]) -> None:
    """Thinking-on and thinking-off are separate entries; refuse a model whose
    records claim one entry but carry two thinking modes (which entry_key makes
    impossible unless a record was edited by hand)."""
    seen: dict[str, str] = {}
    for rec in records:
        key = entry_key(rec)
        mode = rec["settings"]["thinking"]
        if seen.setdefault(key, mode) != mode:
            raise IncompatibleResults(f"refusing to mix thinking modes inside entry {key}")


def check_same_thinking(a_modes: set[str], b_modes: set[str], *, what: str) -> None:
    if a_modes != b_modes:
        raise IncompatibleResults(
            f"refusing to {what}: the results were made with different thinking modes "
            f"({sorted(a_modes)} vs {sorted(b_modes)}). Thinking-on and thinking-off are separate "
            "entries and are never compared run against run.")


def trial_key(rec: dict) -> tuple:
    return (entry_key(rec), device_id(rec), rec["case_id"], rec["trial"])
