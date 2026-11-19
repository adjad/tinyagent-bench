"""Media tiers: core, extended and full, nested by one seed (spec 05 §17).

A tier is a manifest (``core.yaml``, ``extended.yaml``, ``full.yaml`` beside this
file) that sets how many items each suite and family takes, which conditions run,
and how many trials. The suites, generators and scorers are the same at every
tier; only the selection changes.

**Nested selection.** Every family has one *order* of its items. The order is a
seeded permutation inside each stratum (template, speaker, emotion, ...),
interleaved across strata in proportion to their sizes. A tier takes the first
*k* items of that order. The order does not depend on *k*, and every manifest
carries the same seed, so the core's items are a prefix of the extended tier's,
which are a prefix of the full set: core ⊂ extended ⊂ full. Every prefix is also
stratified, so 25 V1 images still cover every template of their family.

**Public sets** (LibriSpeech, AMI, Earnings-22, CREMA-D, ESC-10, OCRBench) are
selected by committed id lists in ``ids/<set>.<tier>.txt``, which
:func:`write_id_list` writes from a listing of the downloaded set. Nothing is
downloaded until the user approves the downloads, so those lists are written
later. Generated families need no download; their selections come from the
generators' own item indices and are committed now.

**Results** carry the tier: every record gets ``media_tier``, ``manifest_sha256``,
the item count ``tier_n`` of its group, and the 95 % interval half-width
``ci95_half_width_pts`` (worst case p = 0.5). Records from different tiers or
manifests are never pooled (:func:`assert_one_tier`).
"""
from __future__ import annotations

import hashlib
import heapq
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Hashable, Iterable, Sequence

import yaml

TIERS = ("core", "extended", "full")
DEFAULT_TIER = "core"
TIER_DIR = Path(__file__).resolve().parent
IDS_DIR = TIER_DIR / "ids"
ALL = "all"
Z95 = 1.96

# Manifest condition ids -> the generators' own names.
V5_CONDITIONS = {
    "text7px": "small_text_7", "text9px": "small_text_9", "text11px": "small_text_11",
    "text13px": "small_text_13",
    "side384": "lowres_384", "side512": "lowres_512", "side768": "lowres_768",
    "side1024": "lowres_1024", "side1568": "lowres_1568", "side2048": "lowres_2048",
    "rot5": "rotate_5", "rot15": "rotate_15", "rot90": "rotate_90", "rot180": "rotate_180",
    "crop_tight": "crop_tight", "crop_partial": "crop_cut", "crop_offcentre": "crop_offcentre",
    "glare_mild": "glare_1", "glare_strong": "glare_2",
    "blur_light": "blur_1", "blur_mid": "blur_2", "blur_heavy": "blur_3",
    "jpeg40": "jpeg_40", "jpeg20": "jpeg_20", "dark": "dark",
}
A4_CONDITIONS = {
    "snr20": {"type": "noise", "snr_db": 20}, "snr10": {"type": "noise", "snr_db": 10},
    "snr5": {"type": "noise", "snr_db": 5}, "snr0": {"type": "noise", "snr_db": 0},
    "rir1m": {"type": "room", "distance_m": 1}, "rir3m": {"type": "room", "distance_m": 3},
    "rir5m": {"type": "room", "distance_m": 5},
    "sir10": {"type": "overlap", "sir_db": 10}, "sir5": {"type": "overlap", "sir_db": 5},
    "sir0": {"type": "overlap", "sir_db": 0},
    "dbfs-30": {"type": "low_volume", "peak_dbfs": -30},
    "dbfs-40": {"type": "low_volume", "peak_dbfs": -40},
    "dbfs-50": {"type": "low_volume", "peak_dbfs": -50},
    "clip6": {"type": "clipping", "overdrive_db": 6}, "clip12": {"type": "clipping", "overdrive_db": 12},
    "headcut": {"type": "truncation", "kind": "head_cut"},
    "tailcut": {"type": "truncation", "kind": "tail_cut"},
    "dropouts": {"type": "truncation", "kind": "dropouts"},
    "narrowband": {"type": "narrowband"},
}
V7_SKILLS = {"counting": "counting", "spatial": "spatial", "control_location": "locate",
             "multi_image": "multi_image", "chart_trend": "chart_trend"}
# Public sets selected by committed id lists, with the stratum their listing must give.
PUBLIC_SETS = {
    "librispeech_clean": ("a1", "speaker"), "librispeech_other": ("a1", "speaker"),
    "ami_sdm": ("a1", "meeting"), "earnings22": ("a1", "file"),
    "cremad": ("a7", "emotion"), "esc10": ("a7", "class"), "ocrbench": ("v2", "task"),
}


# ---------------------------------------------------------------- manifests

@dataclass(frozen=True)
class Manifest:
    tier: str
    data: dict
    sha256: str
    path: Path

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    @property
    def seed(self) -> int:
        return int(self.data["seed"])


def manifest_path(tier: str) -> Path:
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r}; choose one of {', '.join(TIERS)}")
    return TIER_DIR / f"{tier}.yaml"


def load_manifest(tier: str = DEFAULT_TIER) -> Manifest:
    path = manifest_path(tier)
    raw = path.read_bytes()
    data = yaml.safe_load(raw)
    if data.get("tier") != tier:
        raise ValueError(f"{path.name} says tier {data.get('tier')!r}")
    return Manifest(tier, data, hashlib.sha256(raw).hexdigest(), path)


def load_pace() -> dict:
    return yaml.safe_load((TIER_DIR / "pace.yaml").read_text())


def count(value: Any, universe: int) -> int:
    """A manifest count: ``all`` (or ``on``) is the whole universe; off, 0 or missing is 0."""
    if value in (ALL, "on", True):
        return universe
    if value in (None, "off", False):
        return 0
    n = int(value)
    if n > universe:
        raise ValueError(f"asks for {n} of {universe}")
    return n


def _le(a: Any, b: Any) -> bool:
    """``a`` is no larger than ``b`` for manifest values (numbers, ``all``, off, lists)."""
    if isinstance(a, dict) and isinstance(b, dict):
        return all(_le(v, b.get(k)) for k, v in a.items() if k not in ("source", "base"))
    if isinstance(a, list):
        if b == ALL:
            return True
        return isinstance(b, list) and set(map(str, a)) <= set(map(str, b))
    if b == ALL or a in (None, "off", False, 0):
        return True
    if a == ALL:
        return b == ALL
    if isinstance(a, str) or isinstance(b, str):
        return a == b or b in ("on", "where_supported", "optional") or a in ("optional",)
    if isinstance(b, dict):                     # e.g. voice_variance off < {commands, voices}
        return not a
    return float(a) <= float(b)


def check_nesting(smaller: Manifest, larger: Manifest) -> list[str]:
    """Every count and list in ``smaller`` fits inside ``larger`` (and the seeds agree)."""
    problems = []
    if smaller.seed != larger.seed:
        problems.append(f"seed {smaller.seed} != {larger.seed}: tiers would not nest")
    skip = {"tier", "seed", "trials", "strata"}
    for suite, block in smaller.data.items():
        if suite in skip:
            continue
        other = larger.data.get(suite)
        if isinstance(block, dict):
            for key, v in block.items():
                if key in ("strata", "source", "base", "families", "native_one_call",
                           "noise_source"):
                    continue
                if not _le(v, (other or {}).get(key)):
                    problems.append(f"{suite}.{key}: {v!r} is not inside {larger.tier}'s "
                                    f"{(other or {}).get(key)!r}")
        elif not _le(block, other):
            problems.append(f"{suite}: {block!r} is not inside {larger.tier}'s {other!r}")
    return problems


# ---------------------------------------------------------------- nested order

def _h(*parts: object) -> bytes:
    return hashlib.sha256("\x1f".join(map(str, parts)).encode("utf-8")).digest()


def nested_order(items: Iterable[tuple[Hashable, Hashable]], *, seed: int | str, label: str,
                 within: str = "seeded") -> list[Hashable]:
    """The one order of a family's items; any tier takes a prefix of it.

    ``items`` are ``(key, stratum)`` pairs. Inside each stratum the keys are
    ordered by a seeded hash (``within="seeded"``) or kept in the given order
    (``within="given"``, for generators whose own order is already seeded and
    expensive to skip ahead in). Strata are interleaved so every prefix holds
    each stratum in proportion to its size (largest deficit first, ties by a
    seeded hash). Same items, seed and label give the same order.
    """
    groups: dict[Hashable, list[Hashable]] = {}
    for key, stratum in items:
        groups.setdefault(stratum, []).append(key)
    if within == "seeded":
        for s, keys in groups.items():
            keys.sort(key=lambda k: _h(seed, label, s, k))
    elif within != "given":
        raise ValueError(within)
    total = sum(len(v) for v in groups.values())
    heap = [((1) / len(keys), _h(seed, label, "stratum", s), s) for s, keys in groups.items()]
    heapq.heapify(heap)
    taken = {s: 0 for s in groups}
    out: list[Hashable] = []
    while heap:
        _, tie, s = heapq.heappop(heap)
        out.append(groups[s][taken[s]])
        taken[s] += 1
        if taken[s] < len(groups[s]):
            heapq.heappush(heap, ((taken[s] + 1) / len(groups[s]), tie, s))
    assert len(out) == total
    return out


def take(items: Iterable[tuple[Hashable, Hashable]], k: Any, *, seed: int | str, label: str,
         within: str = "seeded") -> list[Hashable]:
    order = nested_order(items, seed=seed, label=label, within=within)
    return order[:count(k, len(order))]


def half_width(n: int, p: float = 0.5) -> float | None:
    """95 % interval half-width of a pass rate, in points: 1.96 × √(p(1−p)/n) × 100."""
    if not n:
        return None
    return round(Z95 * math.sqrt(p * (1 - p) / n) * 100, 1)


# ---------------------------------------------------------------- public id lists

def id_list_path(set_name: str, tier: str) -> Path:
    return IDS_DIR / f"{set_name}.{tier}.txt"


def read_listing(path: str | Path) -> list[tuple[str, str]]:
    """A set's listing: one ``<id><TAB><stratum>`` per line, written by its loader."""
    rows = []
    for line in Path(path).read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            item_id, _, stratum = line.partition("\t")
            rows.append((item_id.strip(), stratum.strip() or "-"))
    return rows


def sample_ids(set_name: str, listing: Sequence[tuple[str, str]], tier: str,
               manifest: Manifest | None = None) -> list[str]:
    """The tier's ids of a public set: a prefix of the set's one stratified order."""
    if set_name not in PUBLIC_SETS:
        raise KeyError(f"unknown public set {set_name!r}; known: {sorted(PUBLIC_SETS)}")
    m = manifest or load_manifest(tier)
    suite, _ = PUBLIC_SETS[set_name]
    k = m[suite].get(set_name, 0) if isinstance(m[suite], dict) else 0
    if len({i for i, _ in listing}) != len(listing):
        raise ValueError(f"{set_name}: the listing repeats an id")
    return take(listing, k, seed=m.seed, label=f"public:{set_name}")


def write_id_list(set_name: str, listing: Sequence[tuple[str, str]], tier: str) -> Path:
    ids = sample_ids(set_name, listing, tier)
    m = load_manifest(tier)
    path = id_list_path(set_name, tier)
    path.parent.mkdir(parents=True, exist_ok=True)
    head = (f"# {set_name}, tier {tier}: {len(ids)} of {len(listing)} ids, seed {m.seed}, "
            f"stratified by {PUBLIC_SETS[set_name][1]}; written by edat.media.tiers\n")
    path.write_text(head + "".join(f"{i}\n" for i in ids))
    return path


def read_id_list(set_name: str, tier: str) -> list[str] | None:
    """The committed ids, or None when the list is not written yet (set not downloaded)."""
    path = id_list_path(set_name, tier)
    if not path.exists():
        return None
    return [l.strip() for l in path.read_text().splitlines() if l.strip() and not l.startswith("#")]


# ---------------------------------------------------------------- generated families

def v1_items(family: str) -> list[tuple[int, int]]:
    from edat.media.vision import generate as gen
    t = len(gen.family_module(family).TEMPLATES)
    return [(i, i % t) for i in range(gen.FULL_SET_PER_FAMILY)]


def v3_items() -> list[tuple[int, str]]:
    from edat.media.vision import actions
    return [(i, actions.case_for(i)) for i in range(actions.TOTAL)]


def v7_items(skill: str) -> list[tuple[int, int]]:
    from edat.media.vision import reasoning as r
    n_templates = {"counting": len(r.COUNT_TEMPLATES), "spatial": len(r.SPATIAL_TEMPLATES),
                   "multi_image": len(r.MULTI_TEMPLATES), "chart_trend": len(r.TREND_QTYPES),
                   "locate": len(r.LOCATE_FAMILIES)}[skill]
    return [(i, i % n_templates) for i in range(r.SKILL_COUNTS[skill])]


def question_order(qids: Sequence[str], *, seed: int, item_id: str) -> list[str]:
    """The order an item's questions are taken in; a tier asking k per item takes the first k."""
    return sorted(qids, key=lambda q: _h(seed, "questions", item_id, q))


@dataclass
class Selection:
    """What one tier runs: item keys per suite and family, and what is still missing."""

    manifest: Manifest
    vision: dict = field(default_factory=dict)
    audio: dict = field(default_factory=dict)
    pending: list[str] = field(default_factory=list)   # public sets whose id lists are not written

    def to_json(self) -> dict:
        return {"tier": self.manifest.tier, "manifest_sha256": self.manifest.sha256,
                "seed": self.manifest.seed, "vision": self.vision, "audio": self.audio,
                "pending": self.pending, "groups": group_sizes(self)}


def _families(value) -> list[str]:
    from edat.media.vision import generate as gen
    return list(gen.FAMILY_MODULES) if value in (ALL, None) else list(value)


def select_vision(m: Manifest) -> dict:
    from edat.media.vision import robustness
    out: dict = {}
    v1 = m["v1"]
    fams = _families(v1.get("families"))
    out["v1"] = {}
    for fam in fams:
        items = v1_items(fam)
        idx = take(items, v1["images_per_family"], seed=m.seed, label=f"v1:{fam}")
        out["v1"][fam] = {"indices": idx,
                          "questions_per_image": v1["questions_per_image"],
                          "readings": idx[:count(v1["readings_per_family"], len(idx))]}
    out["v3"] = take(v3_items(), m["v3"]["n"], seed=m.seed, label="v3")
    v5 = m["v5"]
    bases = [((fam, j), fam) for fam in robustness.BASE_FAMILIES
             for j in range(robustness.BASE_PER_FAMILY)]
    picked = take(bases, v5["images"], seed=m.seed, label="v5:bases", within="given")
    per_family: dict[str, int] = {}
    for fam, j in picked:
        per_family[fam] = max(per_family.get(fam, 0), j + 1)
    conds = v5["conditions"]
    out["v5"] = {"bases_per_family": per_family,
                 "conditions": list(robustness.CONDITIONS) if conds == ALL else
                 [V5_CONDITIONS[c] for c in conds],
                 "questions_per_image": v5.get("questions_per_image", 2),
                 "unreadable": take([(n, n % len(robustness.UNREADABLE_KINDS))
                                     for n in range(robustness.UNREADABLE_TOTAL)],
                                    v5["unreadable"], seed=m.seed, label="v5:unreadable",
                                    within="given")}
    out["v7"] = {}
    for key, skill in V7_SKILLS.items():
        n = m["v7"].get(key, 0)
        out["v7"][skill] = take(v7_items(skill), n, seed=m.seed, label=f"v7:{skill}")
    out["v4"] = dict(m["v4"])
    return out


def select_audio(m: Manifest, *, agentic_dir=None) -> tuple[dict, list[str]]:
    from edat.media.audio import nonspeech as ns
    from edat.media.audio import spoken_cases as sc
    from edat.media.audio import understanding as u
    out: dict = {}
    pending: list[str] = []
    for set_name, (suite, _) in PUBLIC_SETS.items():
        if suite == "v2":
            continue
        k = m[suite].get(set_name) if isinstance(m[suite], dict) else None
        if not k:
            continue
        ids = read_id_list(set_name, m.tier)
        out.setdefault(suite, {})[set_name] = ids
        if ids is None:
            pending.append(set_name)
    a4 = m["a4"]
    conds = list(A4_CONDITIONS) if a4["conditions"] == ALL else list(a4["conditions"])
    clean = out.get("a1", {}).get("librispeech_clean")
    out["a4"] = {"conditions": conds, "noise_source": a4.get("noise_source", "generated"),
                 "utterances": None if clean is None else clean[:count(a4["n"], len(clean))]}
    a3 = m["a3"]
    suite = sc.build_suite(agentic_dir=agentic_dir)
    ent = [(c.case_id, "entity_dense") for c in suite.singles if c.source == "entity_dense"]
    sources = a3.get("single_sources") or ["agentic", "toolbench"]
    other = [(c.case_id, c.source) for c in suite.singles if c.source in sources]
    n_ent = count(a3.get("entity_dense", 0), len(ent))
    if a3["single"] == ALL:
        n_other = len(other)
    else:
        n_other = count(a3["single"], len(ent) + len(other)) - n_ent
    singles = (take(ent, n_ent, seed=m.seed, label="a3:entity_dense")
               + take(other, max(0, n_other), seed=m.seed, label="a3:single"))
    noise = a3.get("noise") or {}
    out["a3"] = {
        "singles": singles,
        "followups": take([(d.dialogue_id, d.source) for d in suite.dialogues],
                          a3["followups"], seed=m.seed, label="a3:followups"),
        "corrections": take([(c.case_id, c.tags[1] if len(c.tags) > 1 else "-")
                             for c in suite.corrections], a3["corrections"], seed=m.seed,
                            label="a3:corrections"),
        "disfluency": take([(c.case_id, c.tags[1] if len(c.tags) > 1 else "-")
                            for c in suite.disfluency], a3["disfluency"], seed=m.seed,
                           label="a3:disfluency"),
        "noise": {"commands": singles[:count(noise.get("n", 0), len(singles))],
                  "snr_db": list(noise.get("snr_db", []))},
        "voice_variance": a3.get("voice_variance"),
        "configurations": list(a3["configurations"]),
    }
    a5 = m["a5"]
    out["a5"] = {
        "non_speech": take([(r["item_id"], r["kind"]) for r in ns.nonspeech_plan(0)],
                           a5["non_speech"], seed=m.seed, label="a5:non_speech"),
        "answer_leak": take([(r["item_id"], r["kind"]) for r in ns.answer_leak_prompts(0)],
                            a5["answer_leak"], seed=m.seed, label="a5:answer_leak"),
        "trailing_silence": {"n": a5["trailing_silence"], "source": "librispeech_clean"},
    }
    a7 = m["a7"]
    rec = a7["recordings"]
    plans = u.recording_plans()
    by_kind = {kind: [(p["id"], kind) for p in plans if p["recording_kind"] == kind]
               for kind in ("memo", "meeting", "talk")}
    recordings = []
    for kind, key in (("memo", "memos"), ("meeting", "meetings"), ("talk", "talks")):
        recordings += take(by_kind[kind], rec.get(key, 0), seed=m.seed, label=f"a7:{kind}")
    out["a7"] = {
        "conversations": take([(p["id"], p["n_speakers"]) for p in u.conversation_plans()],
                              a7["conversations"], seed=m.seed, label="a7:conversations"),
        "urgency": take([(p["id"], f"{p['content']}/{p['delivery']}") for p in u.urgency_plan()],
                        a7["urgency"], seed=m.seed, label="a7:urgency"),
        "generated_sounds": take([(p["id"], p["class"]) for p in u.sound_plan()],
                                 a7.get("generated_sounds", 0), seed=m.seed, label="a7:sounds"),
        "recordings": recordings,
        "recording_questions": rec.get("questions", 0),
        "cremad": out.get("a7", {}).get("cremad"), "esc10": out.get("a7", {}).get("esc10"),
    }
    for key in ("a8", "a9", "a2"):
        out[key] = dict(m[key])
    return out, pending


def select(tier: str = DEFAULT_TIER, *, agentic_dir=None) -> Selection:
    m = load_manifest(tier)
    audio, pending = select_audio(m, agentic_dir=agentic_dir)
    v2 = {s: k for s, k in m["v2"].items()}
    for s in v2:
        if s in PUBLIC_SETS and read_id_list(s, tier) is None:
            pending.append(s)
    vision = select_vision(m)
    vision["v2"] = {s: read_id_list(s, tier) for s in v2 if s in PUBLIC_SETS}
    return Selection(m, vision, audio, pending)


# ---------------------------------------------------------------- group sizes

def _n(x) -> int:
    if x is None:
        return 0
    if isinstance(x, (list, tuple)):
        return len(x)
    return int(x)


def group_sizes(sel: Selection) -> dict[str, int]:
    """Items per reporting group (``<suite>/<category>``, ``<suite>`` for the pool).

    These are the n behind each row's interval. Public sets not yet listed count
    the manifest's number, since their lists will hold exactly that many.
    """
    m, v, a = sel.manifest, sel.vision, sel.audio
    g: dict[str, int] = {}
    for fam, s in v["v1"].items():
        g[f"v1/{fam}"] = len(s["indices"]) * min(int(s["questions_per_image"]), 4)
        g[f"v1/reading/{fam}"] = len(s["readings"])
    g["v1"] = sum(n for k, n in g.items() if k.count("/") == 1 and k.startswith("v1/"))
    g["v3"] = len(v["v3"])
    images = sum(v["v5"]["bases_per_family"].values())
    for c in v["v5"]["conditions"]:
        g[f"v5/{c}"] = images * int(v["v5"]["questions_per_image"])
    g["v5/unreadable"] = len(v["v5"]["unreadable"])
    for skill, idx in v["v7"].items():
        if idx:
            g[f"v7/{skill}"] = len(idx)
    for s, k in m["v2"].items():
        g[f"v2/{s}"] = _n(v["v2"].get(s)) or (int(k) if k != ALL else 0)
    a1 = m["a1"]
    for s in ("librispeech_clean", "librispeech_other", "ami_sdm", "earnings22"):
        if a1.get(s):
            g[f"a1/{s}"] = _n(a["a1"].get(s)) or (int(a1[s]) if a1[s] != ALL else 0)
    a4n = m["a4"]["n"]
    for c in a["a4"]["conditions"]:
        g[f"a4/{c}"] = _n(a["a4"]["utterances"]) or (int(a4n) if a4n != ALL else 0)
    a3 = a["a3"]
    g["a3/single"] = len(a3["singles"])
    g["a3/followups"] = len(a3["followups"])
    g["a3/corrections"] = len(a3["corrections"])
    g["a3/disfluency"] = len(a3["disfluency"])
    for snr in a3["noise"]["snr_db"]:
        g[f"a3/noise_snr{snr}"] = len(a3["noise"]["commands"])
    a5 = a["a5"]
    g["a5/non_speech"] = len(a5["non_speech"])
    g["a5/answer_leak"] = len(a5["answer_leak"])
    g["a5/trailing_silence"] = _n(a5["trailing_silence"]["n"]) if a5["trailing_silence"]["n"] != ALL else 0
    a7, m7 = a["a7"], m["a7"]
    g["a7/conversations"] = len(a7["conversations"])
    g["a7/urgency"] = len(a7["urgency"])
    g["a7/generated_sounds"] = len(a7["generated_sounds"])
    g["a7/recording_questions"] = _n(a7["recording_questions"]) if a7["recording_questions"] != ALL else 0
    for s in ("cremad", "esc10"):
        g[f"a7/{s}"] = _n(a7.get(s)) or (int(m7[s]) if m7.get(s) not in (None, ALL) else 0)
    return {k: n for k, n in g.items() if n}


@dataclass(frozen=True)
class TierRun:
    """What a runner stamps on every record (spec 05 §17.7 item 6)."""

    tier: str
    manifest_sha256: str
    groups: dict

    @classmethod
    def for_tier(cls, tier: str = DEFAULT_TIER, *, groups: dict | None = None) -> "TierRun":
        m = load_manifest(tier)
        return cls(tier, m.sha256, groups if groups is not None else group_sizes(select(tier)))

    def n_for(self, suite: str, category: str | None) -> int | None:
        for key in (f"{suite}/{category}", suite):
            if key in self.groups:
                return self.groups[key]
        return None

    def stamp(self, suite: str, category: str | None) -> dict:
        n = self.n_for(suite, category)
        return {"media_tier": self.tier, "manifest_sha256": self.manifest_sha256,
                "tier_n": n, "ci95_half_width_pts": half_width(n) if n else None}


def assert_one_tier(records: Iterable[dict]) -> str | None:
    """Raise if records from different tiers or manifests would be pooled; return the tier."""
    seen = {(r.get("metrics", {}).get("media_tier"), r.get("metrics", {}).get("manifest_sha256"))
            for r in records}
    if len(seen) > 1:
        raise ValueError(f"records from different tiers or manifests must not be pooled: {sorted(map(str, seen))}")
    return next(iter(seen))[0] if seen else None


# ---------------------------------------------------------------- hours per model

# Whole-set sizes for counts of "all" (None: not known until the set is downloaded).
FULL_SIZES = {"librispeech_clean": 2620, "librispeech_other": 2939, "ami_sdm": None,
              "earnings22": None}

def estimate_hours(tier: str = DEFAULT_TIER, *, pace: dict | None = None) -> dict:
    """Mac hours per model from the manifest's counts and the pace assumptions (§17.3).

    Counts of ``all`` use the full design's sizes. Returns minutes per suite and
    trial, and the per-model total with stripping and overhead.
    """
    m = load_manifest(tier)
    p = pace or load_pace()
    rtf = p["rtf"]

    def n(v, full):
        if v == ALL and full is None:
            raise ValueError(f"{tier}: a count of 'all' on a set whose size is not known yet; "
                             "the full design's hours are in spec 05 §12.2")
        return count(v, full if full is not None else 10 ** 9)

    a1 = m["a1"]
    a1_n = sum(n(a1.get(s, 0), FULL_SIZES.get(s)) for s in
               ("librispeech_clean", "librispeech_other", "ami_sdm", "earnings22"))
    a4 = m["a4"]
    a4_conds = len(A4_CONDITIONS) if a4["conditions"] == ALL else len(a4["conditions"])
    a3 = m["a3"]
    noise = a3.get("noise") or {}
    singles = n(a3["single"], 1592)
    clips = singles + 3 * n(a3["followups"], 400) + n(a3["corrections"], 500) + \
        n(a3["disfluency"], 500)
    vv = a3.get("voice_variance")
    if isinstance(vv, dict):
        clips += int(vv["commands"]) * int(vv["voices"])
    a5 = m["a5"]
    a7 = m["a7"]
    rec = a7["recordings"]
    v1 = m["v1"]
    v1_images = n(v1["images_per_family"], 500) * 11
    v1_readings = n(v1["readings_per_family"], 500) * 11
    v5 = m["v5"]
    v5_conds = 25 if v5["conditions"] == ALL else len(v5["conditions"])
    v7 = m["v7"]
    v4 = m["v4"]
    minutes = {
        "a1": a1_n * p["a1_utterance_s"] * rtf / 60,
        "a4": n(a4["n"], 2620) * a4_conds * p["a4_utterance_s"] * rtf / 60,
        "a3_two_pass": clips * p["command_two_pass_s"] / 60,
        "a3_noise": n(noise.get("n", 0), singles) * len(noise.get("snr_db", [])) *
        p["command_two_pass_s"] / 60,
        "a5": (n(a5["non_speech"], 1000) * (p["a5_non_speech_s"] * rtf + p["a5_overhead_s"]) +
               n(a5["trailing_silence"], 1000) * p["a5_trailing_s"] * rtf +
               n(a5["answer_leak"], 500) * p["a5_answer_leak_s"]) / 60,
        "a7": (n(a7["conversations"], 1000) * p["a7_conversation_s"] * rtf +
               (n(a7["cremad"], 7442) + n(a7["esc10"], 400) + n(a7.get("generated_sounds"), 1000))
               * p["a7_short_question_s"] + n(a7["urgency"], 600) * p["a7_urgency_s"] +
               n(rec.get("memos", 0), 100) * p["a7_memo_s"] * rtf +
               n(rec.get("questions", 0), 1500) * p["a7_recording_question_s"]) / 60,
        "a8_a9": sum(m["a8"]["rungs_s"]) * int(m["a8"]["clips_per_rung"]) * rtf / 60,
        "a2": (sum(m["a2"]["lengths_s"]) * int(m["a2"]["clips_per_length"]) * rtf +
               len(m["a2"]["lengths_s"]) * int(m["a2"]["clips_per_length"]) * p["a2_overhead_s"]) / 60,
        "v1": (v1_images * min(int(v1["questions_per_image"]), 4) * p["image_question_s"] +
               v1_readings * p["reading_s"]) / 60,
        "v2": sum(n(k, 1000) for k in m["v2"].values()) * p["public_image_s"] / 60,
        "v3": n(m["v3"]["n"], 1000) * p["action_image_s"] / 60,
        "v5": (n(v5["images"], 600) * v5_conds * int(v5.get("questions_per_image", 2)) +
               n(v5["unreadable"], 500)) * p["image_question_s"] / 60,
        "v7": ((n(v7.get("counting"), 1000) + n(v7.get("spatial"), 1000) +
                n(v7.get("control_location"), 2000) + n(v7.get("chart_trend"), 500)) *
               p["image_question_s"] + n(v7.get("multi_image"), 1000) * p["multi_image_s"]) / 60,
        "v4": (len(v4["images_per_request"]) * int(v4["per_rung"]) * p["v4_rung_s"] +
               (len(v4["long_side_px"]) * int(v4["per_size"]) +
                len(v4["token_budgets"]) * int(v4["per_budget"])) * p["image_question_s"]) / 60,
    }
    audio = sum(v for k, v in minutes.items() if k.startswith("a"))
    vision = sum(v for k, v in minutes.items() if k.startswith("v"))
    trials = int(m["trials"])
    subtotal = (audio + vision) / 60 * trials + p["strip_hours"]
    return {"tier": tier, "manifest_sha256": m.sha256, "minutes_per_trial": {k: round(v, 1) for k, v in minutes.items()},
            "audio_minutes_per_trial": round(audio, 1), "vision_minutes_per_trial": round(vision, 1),
            "trials": trials, "strip_hours": p["strip_hours"],
            "subtotal_hours": round(subtotal, 2),
            "hours_per_model": round(subtotal * (1 + p["overhead_fraction"]), 2)}


# ---------------------------------------------------------------- generator plans for a tier

def a3_clips(sel: Selection, *, agentic_dir=None) -> list:
    """The A3 clip plan (``spoken_cases.ClipSpec``) cut to the tier's selection."""
    from edat.media.audio import spoken_cases as sc
    a3 = sel.audio["a3"]
    suite = sc.build_suite(agentic_dir=agentic_dir)
    plan = sc.plan_for(suite)
    want = {"single": set(a3["singles"]), "follow_ups": set(a3["followups"]),
            "corrections": set(a3["corrections"]), "disfluency": set(a3["disfluency"])}
    noise_cmds, snrs = set(a3["noise"]["commands"]), set(a3["noise"]["snr_db"])
    vv = a3.get("voice_variance")
    vv_cases: set = set()
    vv_voices: set = set()
    if isinstance(vv, dict):
        vv_cases = set(take([(c, "-") for c in suite.voice_variance], vv["commands"],
                            seed=sel.manifest.seed, label="a3:voice_variance"))
        voices = sorted({s.voice for s in plan if s.family == "voice_variance"})
        vv_voices = set(voices[:count(vv["voices"], len(voices))])
    out = []
    for s in plan:
        if s.family in want and s.case_id in want[s.family]:
            out.append(s)
        elif s.family == "noisy" and s.case_id in noise_cmds and s.condition.get("snr_db") in snrs:
            out.append(s)
        elif s.family == "voice_variance" and s.case_id in vv_cases and s.voice in vv_voices:
            out.append(s)
    return out


def audio_plans(sel: Selection) -> dict:
    """A5 and A7 plan rows (from the generators) cut to the tier's selection, in order."""
    from edat.media.audio import nonspeech as ns
    from edat.media.audio import understanding as u
    a5, a7 = sel.audio["a5"], sel.audio["a7"]

    def pick(rows, ids, key):
        by = {r[key]: r for r in rows}
        return [by[i] for i in ids]
    recs = pick(u.recording_plans(), a7["recordings"], "id")
    n_q = a7["recording_questions"]
    per = math.ceil(count(n_q, 10 ** 9) / len(recs)) if recs and n_q not in (ALL, 0) else None
    if per is not None:
        recs = [dict(r, questions=r["questions"][:per]) for r in recs]
    return {
        "a5_non_speech": pick(ns.nonspeech_plan(0), a5["non_speech"], "item_id"),
        "a5_answer_leak": pick(ns.answer_leak_prompts(0), a5["answer_leak"], "item_id"),
        "a7_conversations": pick(u.conversation_plans(), a7["conversations"], "id"),
        "a7_urgency": pick(u.urgency_plan(), a7["urgency"], "id"),
        "a7_sounds": pick(u.sound_plan(), a7["generated_sounds"], "id"),
        "a7_recordings": recs,
    }


def selection_path(tier: str) -> Path:
    return IDS_DIR / f"selection.{tier}.json"


def generated_selection(sel: Selection) -> dict:
    """The committed part of a selection: everything that needs no download."""
    v, a = sel.vision, sel.audio
    return {
        "tier": sel.manifest.tier, "seed": sel.manifest.seed,
        "note": "Generated families only. Public sets are in ids/<set>.<tier>.txt, written by "
                "'python -m edat.media.tiers sample-ids' once their downloads are approved.",
        "v1": {f: {"indices": s["indices"], "readings": s["readings"],
                   "questions_per_image": s["questions_per_image"]} for f, s in v["v1"].items()},
        "v3": v["v3"], "v5": {**v["v5"], "unreadable": v["v5"]["unreadable"]},
        "v7": v["v7"],
        "a3": {k: a["a3"][k] for k in ("singles", "followups", "corrections", "disfluency", "noise")},
        "a4": {"conditions": a["a4"]["conditions"], "noise_source": a["a4"]["noise_source"]},
        "a5": {"non_speech": a["a5"]["non_speech"], "answer_leak": a["a5"]["answer_leak"]},
        "a7": {k: a["a7"][k] for k in ("conversations", "urgency", "generated_sounds", "recordings",
                                         "recording_questions")},
    }


def a4_plan(sel: Selection, utterance_ids: list[str] | None = None, *, seed: int | None = None
            ) -> list[dict]:
    """The A4 plan for a tier: its utterances (from the A1 id list) under its conditions."""
    from edat.media.audio import mix
    a4 = sel.audio["a4"]
    utts = utterance_ids if utterance_ids is not None else a4["utterances"]
    if utts is None:
        raise LookupError("the LibriSpeech test-clean id list is not written yet")
    return mix.a4_plan(list(utts), sel.manifest.seed if seed is None else seed,
                       conditions=[A4_CONDITIONS[c] for c in a4["conditions"]])
