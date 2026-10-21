"""Checks for the Phase 2 profile builder and constraint scoring. Run: .venv/Scripts/python -m pytest -q"""

import re

import pytest

from phase1_baseline import INITIAL_SOC, TARGET_SOC
from phase2_optimize import FAILED_SCORE, cccv_steps, evaluate, profile_steps


def test_profile_bins_add_up_to_target_soc():
    rates = [2.0, 1.5, 1.0, 0.5]
    steps = profile_steps(rates)
    assert len(steps) == len(rates) + 1  # CC steps + final CV hold
    soc_added = 0.0
    for c, step in zip(rates, steps):
        seconds = float(re.search(r"for ([\d.]+) seconds", step).group(1))
        soc_added += c * seconds / 3600
    assert soc_added == pytest.approx(TARGET_SOC - INITIAL_SOC, abs=1e-4)
    assert steps[-1].startswith("Hold at 4.2 V")


def test_gentle_charge_is_feasible_and_unpenalized():
    r = evaluate(cccv_steps(0.6))
    assert r["feasible"]
    assert r["score"] == pytest.approx(r["time_to_80_min"])
    assert r["df"]["soc"].iloc[-1] >= TARGET_SOC


def test_aggressive_charge_is_penalized():
    r = evaluate(cccv_steps(2.0))
    assert not r["feasible"]
    assert r["peak_temp_c"] > 45
    assert r["min_anode_potential_mv"] < 0
    assert r["score"] > r["time_to_80_min"]
    assert r["score"] < FAILED_SCORE
