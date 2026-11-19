"""edat.perf.fit: every verdict branch of §5.5, §5.6, §5.7 rule 5 and the iPhone Air fit. Synthetic numbers.

B16 = 10.0 GiB, threshold 0.9 x B16 = 9.0 GiB, fixed helper-model allowance 0.4 GiB unless a test says otherwise.
"""
from __future__ import annotations

import pytest

from edat.perf import fit as F
from edat.perf.constants import GIB, MIB


def unc(**peaks_gib):
    """Unconstrained level facts; keys like l2048=6.0 (GiB), decode 100 tok/s everywhere, status ok."""
    out = {}
    for k, v in peaks_gib.items():
        lv = int(k[1:])
        status, peak = ("ok", v) if not isinstance(v, tuple) else v
        out[lv] = {"status": status, "peak_bytes": int(peak * GIB), "decode_tps": 100.0}
    return out


def ob(level, status="ok", decode=100.0, swap=0, comp=0, **kw):
    d = {"level": level, "status": status, "decode_tps": decode, "swap_delta_bytes": swap,
         "compressed_delta_bytes": comp}
    d.update(kw)
    return d


def emu_ok(*levels):
    return [ob(lv) for lv in levels for _ in range(3)]


def test_fits():
    # S 6.0 + 0.4 = 6.4 GiB, W 7.0 + 0.4 = 7.4 GiB, both <= 9.0 GiB; emulation passes at S and W
    v, d = F.fit16_verdict(unc(l2048=6.0, l10240=7.0), emu_ok(2048, 10240), run_id="r1", wired_limit_mb=10900)
    assert v == "fits"
    assert d["budget_bytes"] == 10 * GIB and d["budget_limited_by"] == "b16" and d["budget_source"] == "derived-v1"
    assert d["threshold_bytes"] == pytest.approx(9 * GIB) and d["margin"] == 0.10
    assert d["aux_allowance_bytes"] == int(0.4 * GIB) and d["aux_allowance_source"] == "fixed-0.4GiB"
    assert d["peak_bytes_at_2048"] == 6 * GIB and d["compared_bytes_at_2048"] == 6 * GIB + int(0.4 * GIB)
    assert d["emulation"]["run_id"] == "r1" and d["emulation"]["pass_small"] and d["emulation"]["pass_working"]
    assert d["max_level_fits_16gb"] == 10240 and d["balloon_gib"] == 10.5 and d["wired_limit_mb"] == 10900


def test_borderline_9_to_10_gib_is_small_context_at_best():
    # W 8.8 + 0.4 = 9.2 GiB: between 9.0 and 10.0 -> not "fits", even though emulation passed at W
    v, d = F.fit16_verdict(unc(l2048=6.0, l10240=8.8), emu_ok(2048, 10240))
    assert v == "fits_small_context"
    assert d["footprint"]["10240"]["borderline"] is True
    assert "peak_between_0.9_and_1.0_of_budget_at_W" in d["reasons"]
    assert d["max_level_fits_16gb"] == 2048


def test_small_context_only():
    # W 9.8 + 0.4 = 10.2 GiB is over the budget itself; S passes
    v, d = F.fit16_verdict(unc(l2048=6.0, l10240=9.8), emu_ok(2048, 10240))
    assert v == "fits_small_context" and d["footprint"]["10240"]["borderline"] is False
    # W emulation slowed by 11 %: small context too
    v2, d2 = F.fit16_verdict(unc(l2048=6.0, l10240=7.0), emu_ok(2048) + [ob(10240, decode=89.0)] * 3)
    assert v2 == "fits_small_context" and "emulation_fails_at_W" in d2["reasons"]
    assert d2["emulation"]["levels"][1]["decode_slowdown_pct"] == pytest.approx(11.0)


def test_slowdown_boundary_10pct():
    # 90 tok/s against 100 unconstrained is exactly 10 % slower: passes ("no more than 10 % below")
    v, d = F.fit16_verdict(unc(l2048=6.0, l10240=7.0), emu_ok(2048) + [ob(10240, decode=90.0)] * 3)
    assert v == "fits" and d["emulation"]["levels"][1]["pass"] is True


def test_swap_boundary_256_mib():
    # growth must be under 256 MiB: 255 MiB passes, 256 MiB (swap 200 + compressed 56) fails
    v, _ = F.fit16_verdict(unc(l2048=6.0, l10240=7.0), emu_ok(2048) + [ob(10240, swap=255 * MIB)])
    assert v == "fits"
    v2, d2 = F.fit16_verdict(unc(l2048=6.0, l10240=7.0), emu_ok(2048) + [ob(10240, swap=200 * MIB, comp=56 * MIB)])
    assert v2 == "fits_small_context" and "memory_pressure" in d2["emulation"]["levels"][1]["reasons"]
    # a shrinking compressor does not offset swap growth: max(swap,0) + max(comp,0)
    v3, _ = F.fit16_verdict(unc(l2048=6.0, l10240=7.0), emu_ok(2048) + [ob(10240, swap=300 * MIB, comp=-100 * MIB)])
    assert v3 == "fits_small_context"


def test_does_not_fit_via_refusal():
    v, d = F.fit16_verdict(unc(l2048=6.0, l10240=7.0), [ob(None, status="engine_refused")])
    assert v == "does_not_fit" and "load_failed_under_emulation" in d["reasons"]
    assert d["max_level_fits_16gb"] is None


def test_does_not_fit_via_kill():
    v, d = F.fit16_verdict(unc(l2048=6.0, l10240=7.0), emu_ok(2048) + [ob(10240, status="oom_killed")])
    assert v == "does_not_fit" and "killed_at_10240" in d["reasons"]
    v2, _ = F.fit16_verdict(unc(l2048=6.0, l10240=7.0), [ob(None, status="oom_killed_at_load")])
    assert v2 == "does_not_fit"


def test_does_not_fit_at_S_footprint():
    # S 8.7 + 0.4 = 9.1 GiB > 9.0 GiB
    v, d = F.fit16_verdict(unc(l2048=8.7, l10240=9.5), emu_ok(2048, 10240))
    assert v == "does_not_fit" and "footprint_fails_at_S" in d["reasons"]
    # S emulation fails (slowdown)
    v2, d2 = F.fit16_verdict(unc(l2048=6.0, l10240=7.0), [ob(2048, decode=50.0)] + emu_ok(10240))
    assert v2 == "does_not_fit" and "emulation_fails_at_S" in d2["reasons"]


def test_not_measured():
    v, d = F.fit16_verdict(unc(l2048=6.0, l10240=7.0), [])
    assert v == "not_measured" and d["emulation"]["pass_small"] is None and d["max_level_fits_16gb"] is None
    assert v not in F.RANKED_VERDICTS


def test_wired_limit_tighter():
    # wired 9000 MiB < B16 (10240 MiB): budget 9000 MiB, threshold 8100 MiB = 7.91 GiB.
    # W 7.6 + 0.4 = 8.0 GiB fits under B16 alone but not under the wired limit -> small context
    peaks = unc(l2048=6.0, l10240=7.6)
    assert F.fit16_verdict(peaks, emu_ok(2048, 10240))[0] == "fits"
    v, d = F.fit16_verdict(peaks, emu_ok(2048, 10240), wired_limit_mb=9000)
    assert v == "fits_small_context"
    assert d["budget_bytes"] == 9000 * MIB and d["budget_limited_by"] == "wired_limit"
    assert d["threshold_bytes"] == pytest.approx(0.9 * 9000 * MIB)


def test_aux_allowance_recorded_and_used():
    # W 8.7 GiB: + 0.4 -> 9.1 (small); + 0.2 measured -> 8.9 (fits)
    peaks = unc(l2048=6.0, l10240=8.7)
    assert F.fit16_verdict(peaks, emu_ok(2048, 10240))[0] == "fits_small_context"
    v, d = F.fit16_verdict(peaks, emu_ok(2048, 10240), aux_allowance_bytes=int(0.2 * GIB),
                           aux_allowance_source="measured_aux_baseline")
    assert v == "fits" and d["aux_allowance_source"] == "measured_aux_baseline"


def test_max_level_fits_with_unemulated_levels():
    # all peaks small; emulated S, W, 32768 pass and 65536 is killed (the sweep's expected end)
    peaks = unc(l512=5.0, l1024=5.1, l2048=5.2, l5120=5.5, l10240=6.0, l32768=7.0, l65536=8.0)
    obs = emu_ok(2048, 10240, 32768) + [ob(65536, status="oom_killed")]
    v, d = F.fit16_verdict(peaks, obs)
    assert v == "fits" and d["max_level_fits_16gb"] == 32768
    lf = d["level_fits"]
    # 512 and 1024 count through S, 5120 through W (the next emulated level above it)
    assert lf["512"] and lf["1024"] and lf["5120"] and lf["32768"] and not lf["65536"]
    # if S had failed its emulation, levels below S would not count
    obs2 = [ob(2048, decode=50.0)] * 3 + emu_ok(10240)
    assert F.fit16_verdict(peaks, obs2)[1]["level_fits"]["512"] is False


def test_unconstrained_memory_pressure_fails_footprint():
    v, d = F.fit16_verdict(unc(l2048=6.0, l10240=("memory_pressure", 7.0)), emu_ok(2048, 10240))
    assert v == "fits_small_context" and d["footprint"]["10240"]["pass"] is False


def test_under_load_app_swapped_is_small_at_best():
    peaks = unc(l2048=6.0, l10240=7.0)
    obs = [ob(lv, app_load_gib=2.5, app_load_swapped_bytes=10 * MIB) for lv in (2048, 10240) for _ in range(3)]
    v, d = F.fit16_under_load_verdict(peaks, obs, wired_limit_mb=10900)
    assert v == "fits" and d["app_load_swapped"] is False and d["balloon_gib"] == 8.0 and d["app_load_gib"] == 2.5
    obs[-1]["app_load_swapped_bytes"] = 300 * MIB
    v2, d2 = F.fit16_under_load_verdict(peaks, obs)
    assert v2 == "fits_small_context" and "app_load_swapped" in d2["reasons"]
    assert d2["app_load_swapped_bytes_max"] == 300 * MIB
    assert F.fit16_under_load_verdict(peaks, [])[0] == "not_measured"


def test_pressure_knee():
    peaks = unc(l2048=6.0, l10240=7.0)
    sweep = [ob(2048, decode=98.0, app_load_gib=2.5), ob(10240, decode=97.0, app_load_gib=2.5),
             ob(2048, decode=90.0, app_load_gib=4.0), ob(10240, decode=92.0, app_load_gib=4.0),   # exactly 10 %: not yet
             ob(2048, decode=85.0, app_load_gib=5.5), ob(10240, decode=93.0, app_load_gib=5.5)]
    curve, knee = F.pressure_curve(peaks, sweep)
    assert knee == 5.5
    assert [(r["app_load_gib"], r["level"]) for r in curve] == [(2.5, 2048), (2.5, 10240), (4.0, 2048),
                                                                (4.0, 10240), (5.5, 2048), (5.5, 10240)]
    assert curve[4]["decode_slowdown_pct"] == pytest.approx(15.0)
    # a failure counts as the knee too
    _, knee2 = F.pressure_curve(peaks, [ob(2048, app_load_gib=2.5), ob(10240, status="oom_killed", app_load_gib=4.0)])
    assert knee2 == 4.0
    assert F.pressure_curve(peaks, [ob(2048, decode=99.0, app_load_gib=2.5)])[1] is None


def test_fits_iphone_air():
    balloon = {"budget_bytes": 6_000_000_000}           # threshold 0.9 x 6.0e9 = 5.4e9 B
    def u(s_peak, w_peak, s_status="ok", w_status="ok"):
        return {2048: {"status": s_status, "peak_bytes": s_peak}, 10240: {"status": w_status, "peak_bytes": w_peak}}
    v, d = F.fits_iphone_air_verdict(u(5.0e9, 5.3e9), balloon=balloon, balloon_run_id="b1")
    assert v == "fits" and d["threshold_bytes"] == pytest.approx(5.4e9) and d["max_level_fits"] == 10240
    assert d["budget_source"] == "balloon" and d["balloon_run_id"] == "b1"
    v2, d2 = F.fits_iphone_air_verdict(u(5.0e9, 5.5e9), balloon=balloon)
    assert v2 == "fits_small_context" and d2["max_level_fits"] == 2048
    assert F.fits_iphone_air_verdict(u(5.0e9, None, w_status="oom_killed"), balloon=balloon)[0] == "fits_small_context"
    assert F.fits_iphone_air_verdict(u(5.0e9, None, s_status="oom_killed"), balloon=balloon)[0] == "does_not_fit"
    assert F.fits_iphone_air_verdict(u(5.5e9, 5.6e9), balloon=balloon)[0] == "does_not_fit"
    assert F.fits_iphone_air_verdict(u(5.0e9, 5.3e9), balloon=balloon, load_killed=True)[0] == "does_not_fit"
    assert F.fits_iphone_air_verdict(u(5.0e9, 5.3e9), balloon=None)[0] == "not_measured"
    assert F.fits_iphone_air_verdict({10240: {"status": "ok", "peak_bytes": 1}}, balloon=balloon)[0] == "not_measured"


def test_cell_fits_and_8gb_estimate():
    # 4.0e9 x 1.05 + 0.5 GiB = 4.737e9 B < 5 GiB = 5.369e9 B -> estimated yes; 5.0e9 -> 5.787e9 B -> no
    assert F.estimate_8gb_mac(4_000_000_000)["fits"] is True
    assert F.estimate_8gb_mac(5_000_000_000)["fits"] is False
    fits, est = F.cell_fits(device_class="mac-ref", status="ok", swap_growth_bytes=0, level=2048, peak_bytes=1,
                            fit16_level_fits={"2048": True}, iphone_threshold_bytes=None, weight_bytes=4_000_000_000)
    assert fits == {"mac-ref": "yes", "mac-16gb": "yes", "mac-8gb": "estimated"} and est["mac-8gb"]["fits"] is True
    fits2, _ = F.cell_fits(device_class="mac-ref", status="ok", swap_growth_bytes=300 * MIB, level=2048,
                           peak_bytes=1, fit16_level_fits=None, iphone_threshold_bytes=None, weight_bytes=None)
    assert fits2 == {"mac-ref": "no"}
    fits3, _ = F.cell_fits(device_class="iphone-ref", status="ok", swap_growth_bytes=None, level=2048,
                           peak_bytes=5_000_000_000, fit16_level_fits=None, iphone_threshold_bytes=5.4e9,
                           weight_bytes=4_000_000_000)
    assert fits3 == {"iphone-ref": "yes"}


def test_worst_status_unknown_is_worst():
    assert F.worst_status(["ok", "memory_pressure"]) == "memory_pressure"
    assert F.worst_status(["oom_killed_at_load", "error"]) == "error"
    assert F.worst_status([]) is None
