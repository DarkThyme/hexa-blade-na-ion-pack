"""Tests for the real-car benchmark metrics. Run: .venv/Scripts/python -m pytest -q"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent / "analysis" / "realcars"))
from benchmark import metrics  # noqa: E402


def test_constant_power_charge_time_is_exact():
    # 100 kWh usable at a flat 100 kW: 10 -> 80 % = 70 kWh = 42 min
    soc = np.array([0.0, 100.0])
    m = metrics(soc, np.array([100.0, 100.0]), 100.0)
    assert m["t10_80"] == pytest.approx(42.0, abs=0.1)
    assert m["t80_90"] == pytest.approx(6.0, abs=0.1)
    assert m["avg_kw_10_80"] == pytest.approx(100.0, abs=0.5)


def test_taper_onset_and_partial_curves():
    soc = np.array([5.0, 60.0, 61.0, 85.0])
    power = np.array([200.0, 200.0, 100.0, 50.0])
    m = metrics(soc, power, 80.0)
    assert m["taper_soc"] == pytest.approx(60.25, abs=0.5)  # last SOC at >= 80 % of peak
    assert m["t10_90"] is None                              # curve stops at 85 %: no extrapolation
    assert m["peak_c"] == pytest.approx(2.5)
