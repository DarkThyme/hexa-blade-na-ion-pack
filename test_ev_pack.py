"""Tests for the Hexa-Blade EV pack model. Run: .venv/Scripts/python -m pytest -q"""

import numpy as np
import pytest

from ev_pack import chemistry
from ev_pack.geometry import build_layout
from ev_pack.simulate import LIMITERS, simulate
from ev_pack.spec import CHARGERS, DEFAULT_PACK, spec_sheet


# ---------------------------------------------------------------- chemistry

def test_ocv_rises_monotonically_and_stays_in_window():
    soc = np.linspace(0, 1, 101)
    v = chemistry.ocv(soc)
    assert np.all(np.diff(v) >= -1e-6)
    assert 2.0 <= v[0] < 3.0 and 4.0 < v[-1] <= 4.25


def test_anode_potential_falls_toward_plating_as_soc_rises():
    u = chemistry.anode_potential(np.array([0.1, 0.5, 0.9]))
    assert u[0] > u[1] > u[2] > 0  # positive at rest, closest to 0 V when full


# ---------------------------------------------------------------- spec & geometry

def test_pack_meets_design_targets():
    p = DEFAULT_PACK
    assert p.energy_kwh == pytest.approx(110, abs=2)
    assert 700 < p.nominal_v < 800 and p.max_v <= 850  # 800 V class
    assert p.n_cells == 200
    assert set(spec_sheet()) >= {"Chemistry", "Energy", "Voltage", "Pack size"}


def test_honeycomb_neighbors_are_touching_cells():
    lay = build_layout(DEFAULT_PACK)
    a, b = lay.neighbors.T
    dist = np.hypot(lay.x[a] - lay.x[b], lay.z[a] - lay.z[b])
    assert dist == pytest.approx(np.full_like(dist, DEFAULT_PACK.pitch_m), rel=1e-6)
    # interior cells of a hex lattice touch 6 neighbours
    deg = np.bincount(lay.neighbors.ravel(), minlength=DEFAULT_PACK.n_cells)
    assert deg.max() == 6
    assert lay.cooled.sum() == 2 * DEFAULT_PACK.columns


# ---------------------------------------------------------------- simulation

@pytest.fixture(scope="module")
def runs():
    return {c.name: simulate(c) for c in CHARGERS if c.kind == "DC"}


def test_more_power_charges_faster(runs):
    times = [runs[n].summary["time_min"] for n in ("50 kW DC", "125 kW DC", "250 kW DC", "350 kW DC")]
    assert times == sorted(times, reverse=True)


def test_reaches_target_and_conserves_energy(runs):
    r = runs["250 kW DC"]
    assert r.summary["final_soc"] == pytest.approx(0.80, abs=0.01)
    # energy into pack ~ OCV-integral of 70% SOC plus resistive losses
    stored = DEFAULT_PACK.energy_kwh * 0.70
    assert stored < r.summary["energy_to_pack_kwh"] < stored * 1.15


def test_power_never_exceeds_charger_rating(runs):
    for name, r in runs.items():
        charger = next(c for c in CHARGERS if c.name == name)
        assert r.summary["peak_power_kw"] <= charger.power_kw * charger.efficiency + 1e-6


def test_bms_keeps_cells_safe(runs):
    for r in runs.values():
        assert r.summary["min_anode_mv"] >= DEFAULT_PACK.cell.plating_margin_v * 1000 - 0.5
        assert r.summary["peak_temp_c"] < DEFAULT_PACK.cell.t_cutoff_c


def test_cold_pack_is_plating_limited_and_preconditioning_fixes_it():
    cold = simulate("350 kW DC", ambient_c=-10, precondition=False).summary
    warm = simulate("350 kW DC", ambient_c=-10, precondition=True).summary
    assert cold["main_limiter"] == "Na-plating guard"
    assert warm["time_min"] < cold["time_min"] * 0.75


def test_result_frames_are_consistent(runs):
    r = runs["350 kW DC"]
    assert r.frame_temp_c.shape == r.frame_soc.shape == (len(r.frame_time_min), DEFAULT_PACK.n_cells)
    assert set(r.series["limiter"]) <= set(LIMITERS)
