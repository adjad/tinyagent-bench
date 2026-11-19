"""End to end on fakes: runner -> power trace -> run document -> aggregate.

The fake engine draws 60 W in prefill, 40 W in decode and 5 W idle with a model
loaded, at 2,000 tok/s prefill and 50 tok/s decode, so by hand:
  e_dec = (40 - 5) W / 50 tok/s = 0.7 J = 700 mJ per token;
  E_A (512 tokens, 1 out) = 0.256 s x 55 W + 0.01 s x 35 W = 14.43 J active;
  E_prefill = E_A - e_dec = 13.73 J, so e_pre = 13.73 / 512 = 26.82 mJ per token.
"""
from __future__ import annotations

import json

import pytest

from edat.perf.aggregate import aggregate
from edat.perf.power import TraceWriter
from tests.test_edat_perf_runner import entry, rig


def fake_power(clock, eng, path, rate_hz=1000.0):
    w = TraceWriter(path, source="smc_pstr", rate_hz=rate_hz, note="synthetic test trace")
    period = int(1e9 / rate_hz)
    state = {"next": clock.t}

    def on_advance(old, new):
        while state["next"] <= new:
            w.append(state["next"], eng.power_at(state["next"]))
            state["next"] += period
    clock.listeners.append(on_advance)
    return w


def test_energy_pair_numbers_by_hand(tmp_path):
    r, eng, clock, _, w = rig(tmp_path, entries=[entry("M")])
    name = f"power-{w.run_id}-smc_pstr.jsonl"
    tw = fake_power(clock, eng, w.dir / name)
    r.i.power.trace_name = name
    w.attach(name, "power_trace")
    e = r.entries["M"]
    r.select(e, "as_shipped")
    r.energy_pairs(e, "as_shipped", 512, k=4)
    r.first_token(e, "as_shipped")
    r.levels(e, "as_shipped", (512, 1024), reps=3)
    tw.close()
    path = w.finalize(ended_utc=clock.utc(), device={"class": "mac-ref", "ram_bytes": 24 << 30},
                      runtime={"name": "omlx"}, models=r.finish_models(), prompt_pack={"M": {}},
                      conditions_start={}, conditions_end={})
    doc = aggregate(json.loads(path.read_text()), run_dir=w.dir)
    pair = next(c for c in doc["cells"] if c["test"] == "energy_pair")
    assert pair["e_dec_mJ_per_tok"] == pytest.approx(700.0, rel=0.01)
    assert pair["e_pre_mJ_per_tok"] == pytest.approx(13.73e3 / 512, rel=0.02)
    assert pair["scope"] == "whole_device_internal"
    lvl = next(c for c in doc["cells"] if c["test"] == "level" and c["level_tokens"] == 512)
    assert lvl["decode_tps"]["median"] == pytest.approx(50.0)
    assert lvl["n_ok"] == 3
    assert doc["verdicts"] and doc["verdicts"][0]["model_id"] == "M"
