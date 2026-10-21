"""Sanity checks on the baseline simulation physics. Run: .venv/Scripts/python -m pytest -q"""

import pytest

from phase1_baseline import AMBIENT_C, INITIAL_SOC, simulate_cccv, summarize


@pytest.fixture(scope="module")
def runs():
    return {c: simulate_cccv(c, "SPM") for c in (0.5, 2.0)}


def test_starts_at_initial_soc_and_ambient(runs):
    df = runs[0.5]
    assert df["soc"].iloc[0] == pytest.approx(INITIAL_SOC, abs=0.01)
    assert df["temp_c"].iloc[0] == pytest.approx(AMBIENT_C, abs=0.5)


def test_charges_nearly_full(runs):
    assert runs[0.5]["soc"].iloc[-1] > 0.95


def test_voltage_never_exceeds_cutoff(runs):
    for df in runs.values():
        assert df["voltage_v"].max() <= 4.2 + 1e-3


def test_faster_charge_is_quicker_but_hotter(runs):
    slow, fast = summarize(0.5, runs[0.5]), summarize(2.0, runs[2.0])
    assert fast["time_to_80pct_min"] < slow["time_to_80pct_min"]
    assert fast["peak_temp_c"] > slow["peak_temp_c"]
