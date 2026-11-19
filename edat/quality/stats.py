"""Rating statistics for tinyagent-bench's human-preference measure (spec 03 s1.1, s1.3, s4.3).

Pure standard library (no numpy/scipy). Every resampling function takes a seed
and uses ``random.Random(seed)``, so results are reproducible.

Conventions
-----------
* Outcomes of one pair are "A", "B" or a tie. A tie counts as half a win to each
  side. The vote tool's ``tie_good`` / ``tie_bad`` are both ties here; ``"tie"``
  is accepted too.
* Rates and win rates are on a 0-100 scale; Wilson intervals are fractions 0-1.
* Bradley-Terry strengths are natural-log strengths with the anchor model at 0.
  A model's "win rate vs anchor" is ``100 / (1 + exp(-strength))``, so the
  anchor sits at 50.
"""
from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Iterable, Mapping, Sequence

STATS_VERSION = "edat-stats/1"

DEFAULT_ANCHOR = "Gemma-Wisp"

# Covariates for style control: differences "A minus B" (log ratio for length).
STYLE_FEATURES = ("log_len", "list_items", "bold", "headers", "emoji")

_TIES = {"tie", "tie_good", "tie_bad"}


def is_tie(outcome: str) -> bool:
    return outcome in _TIES


# ---------------------------------------------------------------------------
# Intervals, rates
# ---------------------------------------------------------------------------

def wilson_interval(k: float, n: float, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for k successes in n trials (fractions in 0-1).

    k may be fractional (ties counted as half). With n == 0 the interval is
    uninformative: (0.0, 1.0).
    """
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2.0 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def win_rate(outcomes: Iterable[str]) -> float:
    """Win rate on 0-100 from "win"/"loss"/"tie" (ties are half). NaN if empty."""
    wins = ties = total = 0
    for o in outcomes:
        total += 1
        if o == "win":
            wins += 1
        elif o in _TIES:
            ties += 1
        elif o != "loss":
            raise ValueError(f"unknown outcome {o!r}")
    if total == 0:
        return float("nan")
    return 100.0 * (wins + 0.5 * ties) / total


def _percentile(sorted_vals: Sequence[float], q: float) -> float:
    if not sorted_vals:
        return float("nan")
    pos = q * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def _case_value(v) -> float:
    if isinstance(v, (list, tuple)):
        return _mean([float(x) for x in v])
    return float(v)


def paired_bootstrap(cand: Mapping[str, float], ref: Mapping[str, float], *,
                     n: int = 10000, seed: int = 0) -> dict:
    """Paired bootstrap over cases of the mean difference candidate - reference.

    Returns {"delta", "lo", "hi", "n_cases", "label"}; label is "better" when the
    95 % interval is above 0, "worse" when below, else "approx_baseline".
    Values may be numbers or lists (seeds), averaged per case first.
    """
    ids = sorted(set(cand) & set(ref))
    diffs = [_case_value(cand[i]) - _case_value(ref[i]) for i in ids]
    if not diffs:
        nan = float("nan")
        return {"delta": nan, "lo": nan, "hi": nan, "n_cases": 0, "label": "approx_baseline"}
    rng = random.Random(seed)
    m = len(diffs)
    means = []
    for _ in range(n):
        means.append(sum(rng.choices(diffs, k=m)) / m)
    means.sort()
    lo, hi = _percentile(means, 0.025), _percentile(means, 0.975)
    delta = sum(diffs) / m
    if lo > 0:
        label = "better"
    elif hi < 0:
        label = "worse"
    else:
        label = "approx_baseline"
    return {"delta": delta, "lo": lo, "hi": hi, "n_cases": m, "label": label}


def bootstrap_mean(values_by_case: Mapping[str, float], n: int = 10000, seed: int = 0) -> dict:
    """Case-level bootstrap of a mean: {"mean", "lo", "hi", "n_cases"}."""
    vals = [_case_value(values_by_case[k]) for k in sorted(values_by_case)]
    if not vals:
        nan = float("nan")
        return {"mean": nan, "lo": nan, "hi": nan, "n_cases": 0}
    rng = random.Random(seed)
    m = len(vals)
    means = sorted(sum(rng.choices(vals, k=m)) / m for _ in range(n))
    return {"mean": sum(vals) / m, "lo": _percentile(means, 0.025),
            "hi": _percentile(means, 0.975), "n_cases": m}


def _median(xs: Sequence[float]) -> float:
    s = sorted(xs)
    if not s:
        return float("nan")
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0


def _ratios(cand: Mapping[str, float], ref: Mapping[str, float]) -> list[float]:
    out = []
    for i in sorted(set(cand) & set(ref)):
        r = _case_value(ref[i])
        if r > 0:
            out.append(_case_value(cand[i]) / r)
    return out


def paired_ratio_median(cand: Mapping[str, float], ref: Mapping[str, float]) -> float:
    """Median per-case ratio cand/ref over shared cases with ref > 0 (tokens_vs_baseline)."""
    return _median(_ratios(cand, ref))


def paired_ratio_median_ci(cand: Mapping[str, float], ref: Mapping[str, float], *,
                           n: int = 2000, seed: int = 0) -> dict:
    """Bootstrap interval for the median ratio: {"median", "lo", "hi", "n_cases"}."""
    ratios = _ratios(cand, ref)
    if not ratios:
        nan = float("nan")
        return {"median": nan, "lo": nan, "hi": nan, "n_cases": 0}
    rng = random.Random(seed)
    m = len(ratios)
    meds = sorted(_median(rng.choices(ratios, k=m)) for _ in range(n))
    return {"median": _median(ratios), "lo": _percentile(meds, 0.025),
            "hi": _percentile(meds, 0.975), "n_cases": m}


def cohen_kappa(labels_a: Sequence[str], labels_b: Sequence[str]) -> float:
    """Cohen's kappa between two raters over categorical labels (here the four outcomes)."""
    if len(labels_a) != len(labels_b):
        raise ValueError("label lists differ in length")
    n = len(labels_a)
    if n == 0:
        return float("nan")
    cats = sorted(set(labels_a) | set(labels_b))
    po = sum(1 for x, y in zip(labels_a, labels_b) if x == y) / n
    pe = 0.0
    for c in cats:
        pe += (sum(1 for x in labels_a if x == c) / n) * (sum(1 for y in labels_b if y == c) / n)
    if pe >= 1.0:
        return 1.0 if po >= 1.0 else 0.0
    return (po - pe) / (1.0 - pe)


# ---------------------------------------------------------------------------
# Bradley-Terry (maximum likelihood, MM algorithm), ties as half a win
# ---------------------------------------------------------------------------

def _outcome_score(outcome: str, ties: str) -> float | None:
    """Score of side A: 1, 0, 0.5, or None when the comparison is dropped."""
    if outcome == "A":
        return 1.0
    if outcome == "B":
        return 0.0
    if outcome in _TIES:
        return 0.5 if ties == "half" else None
    raise ValueError(f"unknown outcome {outcome!r}")


def strength_to_win_rate(strength: float) -> float:
    """Log-strength relative to the anchor -> 0-100 win rate against the anchor."""
    if strength >= 0:
        return 100.0 / (1.0 + math.exp(-strength))
    e = math.exp(strength)
    return 100.0 * e / (1.0 + e)


def bt_win_rates(strengths: Mapping[str, float]) -> dict[str, float]:
    return {m: strength_to_win_rate(s) for m, s in strengths.items()}


def _bt_aggregate(comparisons, ties: str):
    """-> (models, games{(i,j): n} with i<j, wins{(i,j): score of i})."""
    games: dict[tuple[str, str], float] = defaultdict(float)
    wins: dict[tuple[str, str], float] = defaultdict(float)
    models: set[str] = set()
    for comp in comparisons:
        a, b, outcome = comp[0], comp[1], comp[2]
        if a == b:
            continue
        s = _outcome_score(outcome, ties)
        models.add(a)
        models.add(b)
        if s is None:
            continue
        if a < b:
            key, sc = (a, b), s
        else:
            key, sc = (b, a), 1.0 - s
        games[key] += 1.0
        wins[key] += sc
    return models, games, wins


def _bt_fit(models, games, wins, anchor, *, iters, tol, prior):
    models = sorted(models)
    if not models:
        return {}
    ref = anchor if anchor in models else models[0]
    g = defaultdict(float, games)
    w = defaultdict(float, wins)
    if prior > 0:
        for m in models:
            if m == ref:
                continue
            key = (m, ref) if m < ref else (ref, m)
            g[key] += prior
            w[key] += prior * 0.5
    nbrs: dict[str, list[tuple[str, float]]] = defaultdict(list)
    total_w: dict[str, float] = defaultdict(float)
    for (i, j), n_ij in g.items():
        nbrs[i].append((j, n_ij))
        nbrs[j].append((i, n_ij))
        total_w[i] += w[(i, j)]
        total_w[j] += n_ij - w[(i, j)]
    p = {m: 1.0 for m in models}
    for _ in range(iters):
        new = {}
        for m in models:
            denom = sum(n_ij / (p[m] + p[o]) for o, n_ij in nbrs[m])
            new[m] = total_w[m] / denom if denom > 0 and total_w[m] > 0 else 1e-12
        scale = new[ref]
        new = {m: v / scale for m, v in new.items()}
        delta = max(abs(math.log(new[m]) - math.log(p[m])) for m in models)
        p = new
        if delta < tol:
            break
    logs = {m: math.log(p[m]) for m in models}
    if anchor not in models:
        mean = sum(logs.values()) / len(logs)
        logs = {m: v - mean for m, v in logs.items()}
    return logs


def bradley_terry(comparisons, *, ties: str = "half", iters: int = 1000, tol: float = 1e-9,
                  anchor: str = DEFAULT_ANCHOR, prior: float = 0.05) -> dict[str, float]:
    """Maximum-likelihood Bradley-Terry log-strengths, anchor fixed at 0.

    comparisons: iterable of (model_a, model_b, outcome) with outcome "A"/"B"/"tie"
    (or tie_good/tie_bad). Ties count as half a win to each side (ties="half") or
    are dropped (ties="drop"). ``prior`` adds a tiny virtual tie between each model
    and the anchor so a model with no wins or no losses still gets a finite
    strength (0.05 of a game; negligible with real data). If the anchor is absent
    the strengths are centred to mean 0 instead.
    """
    models, games, wins = _bt_aggregate(comparisons, ties)
    return _bt_fit(models, games, wins, anchor, iters=iters, tol=tol, prior=prior)


def bt_bootstrap(comparisons, n: int = 1000, seed: int = 0, *, anchor: str = DEFAULT_ANCHOR,
                 ties: str = "half", controlled: bool = False, **kw) -> dict[str, dict]:
    """95 % bootstrap intervals (resampling comparisons) for each model.

    Returns {model: {"strength", "lo", "hi", "win_rate", "win_rate_lo", "win_rate_hi"}}.
    With controlled=True the comparisons are 4-tuples with style features and the
    style-controlled fit is bootstrapped (win rates with style terms at zero).
    """
    comps = list(comparisons)
    rng = random.Random(seed)

    def fit(cs):
        if controlled:
            return style_controlled_bt(cs, anchor=anchor, ties=ties, **kw)["strengths"]
        return bradley_terry(cs, ties=ties, anchor=anchor, **kw)

    point = fit(comps)
    samples: dict[str, list[float]] = {m: [] for m in point}
    if comps:
        for _ in range(n):
            res = fit(rng.choices(comps, k=len(comps)))
            for m in point:
                samples[m].append(res.get(m, 0.0 if m == anchor else float("nan")))
    out = {}
    for m, s in point.items():
        vals = sorted(v for v in samples[m] if not math.isnan(v))
        lo = _percentile(vals, 0.025)
        hi = _percentile(vals, 0.975)
        out[m] = {
            "strength": s, "lo": lo, "hi": hi,
            "win_rate": strength_to_win_rate(s),
            "win_rate_lo": strength_to_win_rate(lo) if vals else float("nan"),
            "win_rate_hi": strength_to_win_rate(hi) if vals else float("nan"),
        }
    return out


# ---------------------------------------------------------------------------
# Style-controlled Bradley-Terry (logistic, covariates), damped Newton
# ---------------------------------------------------------------------------

def _solve(mat: list[list[float]], rhs: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting (mat is copied)."""
    n = len(rhs)
    a = [row[:] + [rhs[i]] for i, row in enumerate(mat)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(a[r][c]))
        if abs(a[piv][c]) < 1e-14:
            raise ZeroDivisionError("singular matrix")
        a[c], a[piv] = a[piv], a[c]
        for r in range(c + 1, n):
            f = a[r][c] / a[c][c]
            if f:
                for k in range(c, n + 1):
                    a[r][k] -= f * a[c][k]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (a[r][n] - sum(a[r][k] * x[k] for k in range(r + 1, n))) / a[r][r]
    return x


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def _feature_vector(feats, names) -> list[float]:
    if isinstance(feats, Mapping):
        return [float(feats.get(n, 0.0)) for n in names]
    vals = [float(v) for v in feats]
    if len(vals) != len(names):
        raise ValueError("feature vector length does not match feature names")
    return vals


def style_controlled_bt(comparisons_with_features, *, anchor: str = DEFAULT_ANCHOR,
                        feature_names: Sequence[str] | None = None, ties: str = "half",
                        ridge: float = 1e-4, iters: int = 100, tol: float = 1e-9) -> dict:
    """Logistic Bradley-Terry with style covariates.

    comparisons_with_features: (model_a, model_b, outcome, features) where
    features are the A-minus-B differences, as a dict keyed by feature name or a
    sequence in ``feature_names`` order (default STYLE_FEATURES; log_len is the log
    ratio of visible tokens A/B, the others are count differences).
    P(A beats B) = sigmoid(theta_A - theta_B + beta . x). Ties are soft labels of
    0.5. A small ridge penalty keeps the fit finite under perfect separation.

    Returns {"strengths": {model: theta (anchor 0)}, "win_rate": {model: 0-100 with
    style terms at zero}, "coef": {feature: beta}, "feature_names": [...]}.
    """
    names = list(feature_names) if feature_names is not None else list(STYLE_FEATURES)
    obs = []
    models: set[str] = set()
    for comp in comparisons_with_features:
        a, b, outcome, feats = comp[0], comp[1], comp[2], comp[3]
        if a == b:
            continue
        models.add(a)
        models.add(b)
        y = _outcome_score(outcome, ties)
        if y is None:
            continue
        obs.append((a, b, y, _feature_vector(feats, names)))
    ref = anchor if anchor in models else (sorted(models)[0] if models else anchor)
    free = [m for m in sorted(models) if m != ref]
    idx = {m: i for i, m in enumerate(free)}
    k_models = len(free)
    dim = k_models + len(names)
    theta = [0.0] * dim

    def design(a, b, x):
        z: dict[int, float] = {}
        if a in idx:
            z[idx[a]] = z.get(idx[a], 0.0) + 1.0
        if b in idx:
            z[idx[b]] = z.get(idx[b], 0.0) - 1.0
        for j, v in enumerate(x):
            if v:
                z[k_models + j] = v
        return z

    rows = [(design(a, b, x), y) for a, b, y, x in obs]

    def objective(par):
        ll = 0.0
        for z, y in rows:
            eta = sum(par[i] * v for i, v in z.items())
            # log-likelihood with soft label y: y*eta - log(1+e^eta)
            ll += y * eta - (eta + math.log1p(math.exp(-eta)) if eta > 0 else math.log1p(math.exp(eta)))
        return ll - 0.5 * ridge * sum(v * v for v in par)

    if dim > 0 and rows:
        cur = objective(theta)
        for _ in range(iters):
            grad = [-ridge * theta[i] for i in range(dim)]
            hess = [[0.0] * dim for _ in range(dim)]
            for i in range(dim):
                hess[i][i] = ridge
            for z, y in rows:
                eta = sum(theta[i] * v for i, v in z.items())
                p = _sigmoid(eta)
                w = p * (1.0 - p)
                for i, vi in z.items():
                    grad[i] += (y - p) * vi
                    row = hess[i]
                    for j, vj in z.items():
                        row[j] += w * vi * vj
            try:
                step = _solve(hess, grad)
            except ZeroDivisionError:
                break
            t = 1.0
            while t > 1e-6:
                cand = [theta[i] + t * step[i] for i in range(dim)]
                val = objective(cand)
                if val >= cur - 1e-12:
                    break
                t *= 0.5
            else:
                break
            moved = max(abs(t * s) for s in step)
            theta, cur = cand, val
            if moved < tol:
                break
    strengths = {m: theta[idx[m]] for m in free}
    if models:
        strengths[ref] = 0.0
    if anchor not in models and strengths:
        mean = sum(strengths.values()) / len(strengths)
        strengths = {m: v - mean for m, v in strengths.items()}
    coef = {n: theta[k_models + j] for j, n in enumerate(names)}
    return {"strengths": strengths, "win_rate": bt_win_rates(strengths),
            "coef": coef, "feature_names": names}


def style_controlled_win_rate(comparisons_with_features, *, anchor: str = DEFAULT_ANCHOR,
                              **kw) -> dict[str, float]:
    """Anchor-relative win rate (0-100) with all style terms set to zero."""
    return style_controlled_bt(comparisons_with_features, anchor=anchor, **kw)["win_rate"]


# ---------------------------------------------------------------------------
# Standing pair (spec 1.3)
# ---------------------------------------------------------------------------

def tuning_pref(outcomes_tuned_vs_base: Iterable[str]) -> dict:
    """Standing-pair summary from outcomes of Gemma-Wisp (tuned) vs base Gemma.

    outcomes are "win" (tuned preferred), "loss", "tie". Returns
    {"tuning_pref": win_rate - 50, "win_rate", "lo", "hi" (Wilson, win-rate percent,
    k = wins + ties/2 over all pairs), "n", "n_non_tie", "excludes_zero"}.
    """
    outs = list(outcomes_tuned_vs_base)
    n = len(outs)
    wins = sum(1 for o in outs if o == "win")
    ties = sum(1 for o in outs if o in _TIES)
    if n == 0:
        nan = float("nan")
        return {"tuning_pref": nan, "win_rate": nan, "lo": nan, "hi": nan, "n": 0,
                "n_non_tie": 0, "excludes_zero": False}
    wr = win_rate(outs)
    lo, hi = wilson_interval(wins + 0.5 * ties, n)
    lo, hi = 100.0 * lo, 100.0 * hi
    return {"tuning_pref": wr - 50.0, "win_rate": wr, "lo": lo, "hi": hi, "n": n,
            "n_non_tie": n - ties, "excludes_zero": lo > 50.0 or hi < 50.0}


def style_share(raw, ctrl) -> float | None:
    """Style share = 1 - tuning_pref_style_ctrl / tuning_pref (spec 1.3).

    raw: the dict from tuning_pref(). ctrl: tuning_pref_style_ctrl as a number
    (style-controlled win rate - 50) or a dict with a "tuning_pref" key. None unless
    raw's interval excludes 0 (and tuning_pref is not 0).
    """
    if not isinstance(raw, Mapping):
        return None
    if not raw.get("excludes_zero"):
        return None
    base = raw.get("tuning_pref")
    if base is None or base == 0 or math.isnan(base):
        return None
    c = ctrl.get("tuning_pref") if isinstance(ctrl, Mapping) else ctrl
    if c is None or (isinstance(c, float) and math.isnan(c)):
        return None
    return 1.0 - float(c) / base
