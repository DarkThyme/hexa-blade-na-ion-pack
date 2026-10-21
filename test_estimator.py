"""Tests for the measurement-only estimator study (analysis/estimator). Run: .venv/Scripts/python -m pytest -q"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent / "analysis" / "estimator"))
import common as C  # noqa: E402


def test_held_out_cases_are_disjoint_and_nominal_is_trained():
    assert len(C.HELD_OUT) == 7 and len(C.TRAIN) == 20
    assert not set(C.HELD_OUT) & set(C.TRAIN)
    assert (1.0, 1.0, 1.0) in C.TRAIN


def test_map_estimator_matches_pack_simulator_maps():
    from ev_pack.pack_sim import maps

    m = maps()
    e = C.MapEstimator(25.0, 0.4)
    c = np.array([0.5, 2.0, 3.0])
    expect = m.anode(np.full(3, 25.0), c, np.full(3, 0.4))
    assert np.allclose(e.predict(c), expect)


def test_observer_learns_a_larger_polarisation_from_voltage():
    # feed voltages from a cell that polarises 50 % more than the map: theta must move towards +0.5
    e = C.ObserverEstimator(25.0, 0.3, tau_a=2.0)
    ref = C.MapEstimator(25.0, 0.3)
    for _ in range(200):
        _, eta, ocv = ref._map(np.array([2.0]))
        ref.update(2.0, 0.0)
        v = float(ocv) + 1.5 * ref.eta
        e.update(2.0, v)
    assert e.theta == pytest.approx(0.5, abs=0.15)
    # and its anode estimate is then lower (more cautious) than the plain map's
    assert e.predict(np.array([2.0]))[0] < ref.predict(np.array([2.0]))[0]


def test_feature_rows_are_causal_and_vectorised():
    fs = C.FeatureState(10.0, 0.2)
    X = fs.row(np.array([0.0, 1.0, 3.0]))
    assert X.shape == (3, len(C.FEATURE_NAMES))
    before = fs.row(np.array([1.0]))[0].copy()
    fs.row(np.array([3.0]))                     # evaluating a candidate must not change the state
    assert np.allclose(fs.row(np.array([1.0]))[0], before)
