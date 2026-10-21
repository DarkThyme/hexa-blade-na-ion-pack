"""Tests for the physics-map pack model (v2), both architectures, and the viewer export contract.
Run: .venv/Scripts/python -m pytest -q"""

import json

import numpy as np
import pytest

from ev_pack.architectures import GROSS_KWH, build, n_series
from ev_pack.export import pack_json, write_scenario
from ev_pack.pack_sim import STRATEGIES, build_nominal, maps, simulate, to_display, to_true


# ---------------------------------------------------------------- physics maps

def test_maps_are_physically_ordered():
    m = maps()
    soc = np.linspace(0.05, 0.95, 19)
    assert np.all(np.diff(m.ocv(soc)) >= -1e-4), "OCV must rise with SOC"
    # more current -> more overpotential and a lower anode potential (closer to plating)
    for T in (0.0, 25.0, 45.0):
        eta = [m.eta(np.array([T]), np.array([c]), np.array([0.5]))[0] for c in (0.5, 1.0, 2.0)]
        an = [m.anode(np.array([T]), np.array([c]), np.array([0.5]))[0] for c in (0.5, 1.0, 2.0)]
        assert eta == sorted(eta) and an == sorted(an, reverse=True)


def test_plating_limit_tightens_with_soc_and_relaxes_with_heat():
    tbl = maps().plating_cmax(0.015)
    cmax = lambda T, s: float(tbl(np.array([[T, s]]))[0])  # noqa: E731
    assert cmax(25, 0.3) > cmax(25, 0.7) > cmax(25, 0.9)
    assert cmax(45, 0.8) > cmax(25, 0.8) > cmax(0, 0.8)


def test_usable_window_round_trip():
    assert to_display(to_true(0.42)) == pytest.approx(0.42)
    assert to_true(0.0) > 0 and to_true(1.0) < 1  # buffers at both ends


# ---------------------------------------------------------------- architectures

@pytest.mark.parametrize("arch", ["blade", "module"])
def test_architecture_is_800v_110kwh_and_fully_cooled(arch):
    a = build_nominal(arch)
    assert len(np.unique(a.group)) == a.n_series == n_series(arch)
    v_max = maps().v_max * a.n_series
    assert 760 <= v_max <= 920, "800 V class, within the 920 V limit of 800 V-class chargers"
    assert np.bincount(a.group).min() == np.bincount(a.group).max() == a.n_parallel
    assert a.n_cells * a.cell_energy_wh == pytest.approx(GROSS_KWH * 1000)
    assert np.all(a.g_plate > 0), "every cell must touch a cold plate"
    L, W, H = a.dims
    assert L < 2.6 and W < 1.7 and H < 0.25, "must fit an SUV floor"


# ---------------------------------------------------------------- simulation

@pytest.fixture(scope="module")
def fast_runs():
    return {st: simulate("blade", "350 kW DC", st, 0.10, 0.90) for st in ("standard", "optimized")}


def test_plating_margin_is_never_violated(fast_runs):
    for st, r in fast_runs.items():
        assert r.summary["min_anode_mv"] >= STRATEGIES[st].plating_margin_v * 1000 - 0.05


def test_optimized_reaches_90_percent_much_faster(fast_runs):
    std = fast_runs["standard"].summary["time_to_soc_min"]
    opt = fast_runs["optimized"].summary["time_to_soc_min"]
    assert opt["0.9"] < 0.8 * std["0.9"]
    assert opt["0.9"] - opt["0.8"] < std["0.9"] - std["0.8"], "the 80->90% stretch is where the gain is"


def test_energy_balance(fast_runs):
    r = fast_runs["optimized"]
    stored = GROSS_KWH * (to_true(0.90) - to_true(0.10))
    assert stored < r.summary["energy_to_pack_kwh"] < stored * 1.12


def test_cold_unconditioned_pack_is_plating_limited():
    s = simulate("module", "350 kW DC", "optimized", 0.10, 0.90, ambient_c=-10, precondition=False).summary
    assert s["main_limiter"] == "Na-plating guard"


# ---------------------------------------------------------------- export contract

def test_export_matches_contract(tmp_path, fast_runs):
    pj = pack_json("module")
    assert len(pj["cells"]) == len(pj["cell_module"]) == 800
    assert pj["cell_shape"]["axis"] == "z"
    r = fast_runs["optimized"]
    write_scenario(r, "t", tmp_path)
    doc = json.loads((tmp_path / "t.json").read_text())
    raw = (tmp_path / "t.frames.bin").read_bytes()
    count = doc["frames"]["count"]
    assert len(raw) == count * r.arch.n_cells * 2
    lens = {len(v) for v in doc["series"].values()}
    assert len(lens) == 1, "series arrays must be equal length"
    # decode the last frame's temperatures and compare with the simulation
    f = np.frombuffer(raw, dtype=np.uint8).reshape(count, 2 * r.arch.n_cells)
    temp = doc["frames"]["temp_offset_c"] + f[-1, : r.arch.n_cells] * doc["frames"]["temp_step_c"]
    assert np.allclose(temp, r.frame_temp_c[-1], atol=0.13)


def test_cells_are_bigger_than_the_46mm_design():
    assert build_nominal("blade").cell_shape["across_flats_m"] == pytest.approx(0.064)
    assert build_nominal("module").cell_shape["across_flats_m"] == pytest.approx(0.058)
    assert build_nominal("module").n_cells == 800
    # each blade is a series cell, so the blade count stays near the series count
    assert build_nominal("blade").n_cells == 204


def test_cold_pack_never_draws_more_than_the_charger_rating():
    # regression: the power limit used last step's voltage, so a cold-soaked pack overshot by 3-7 %
    from ev_pack.spec import CHARGERS
    ch = next(c for c in CHARGERS if c.name == "125 kW DC")
    s = simulate("blade", ch, "standard", 0.10, 0.90, ambient_c=0, precondition=False).series
    assert max(s["power_kw"]) <= ch.power_kw * ch.efficiency * 1.002


def test_zero_information_error_reproduces_the_ideal_controller():
    # the controller/plant/evaluator split must not change anything when the controller knows the truth
    from ev_pack.pack_sim import InfoErrors
    for arch, st, amb, pre in (("blade", "optimized", 25, True), ("module", "standard", 0, False)):
        a = simulate(arch, "250 kW DC", st, 0.10, 0.90, ambient_c=amb, precondition=pre).summary
        b = simulate(arch, "250 kW DC", st, 0.10, 0.90, ambient_c=amb, precondition=pre, info=InfoErrors()).summary
        assert a["time_to_soc_min"] == b["time_to_soc_min"]
        assert b["info"]["true_min_anode_mv"] == pytest.approx(a["min_anode_mv"], abs=1e-6)
        assert b["info"]["danger_err_max_mv"] == pytest.approx(0.0, abs=1e-6)


def test_optimistic_anode_estimate_causes_true_margin_violations_when_plating_limited():
    # cold-soaked = plating-guard limited: a +10 mV optimistic estimate must show up as a true margin breach
    from ev_pack.pack_sim import InfoErrors
    r = simulate("blade", "350 kW DC", "optimized", 0.10, 0.90, ambient_c=0, precondition=False,
                 info=InfoErrors(anode_bias_mv=10.0)).summary["info"]
    assert r["below_margin_s"] > 0
    assert r["true_min_anode_mv"] == pytest.approx(5.0, abs=0.6)
