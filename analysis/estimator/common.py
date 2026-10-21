"""
Shared pieces for the measurement-only anode-potential estimator study.

Ground truth always comes from the full sodium-ion DFN (ev_pack/na_cell.py), never from the lookup maps.
Estimators see only: measured voltage, current, temperature, and the declared initial SOC.

Parameter cases vary the cell away from the nominal design the lookup maps were built from:
    hard-carbon diffusivity x {0.6, 1, 1.6}, anode reaction rate x {0.5, 1, 2},
    electrolyte conductivity x {0.7, 1, 1.3}  (27 combinations), each at 0, 10 and 25 C.
7 combinations are held out entirely (never used for fitting or margin calibration).
"""

import os

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import itertools  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

DT = 2.0
TEMPS = [0.0, 10.0, 25.0]
D_N = [0.6, 1.0, 1.6]
K_N = [0.5, 1.0, 2.0]
SIG = [0.7, 1.0, 1.3]
COMBOS = list(itertools.product(D_N, K_N, SIG))
_rng = np.random.default_rng(2026)
_candidates = [c for c in COMBOS if c != (1.0, 1.0, 1.0)]
HELD_OUT = [tuple(float(x) for x in c) for c in np.array(_candidates)[_rng.choice(len(_candidates), 7, replace=False)]]
TRAIN = [c for c in COMBOS if c not in HELD_OUT]
NEAR_LIMIT_MV = 50.0          # "near the plating limit": true anode below this
C_MAX = 3.4                   # charger-limited C-rate (350 kW-class, average cell)
V_MAX = 4.3
VAR_PTS = {"x_n": 16, "x_s": 8, "x_p": 16, "r_n": 16, "r_p": 12}


def design(combo):
    from ev_pack.na_cell import CellDesign

    base = CellDesign()
    a, b, s = combo
    return CellDesign(d_n_scale=base.d_n_scale * a, k_n_scale=base.k_n_scale * b, sigma_e_scale=base.sigma_e_scale * s)


def start_true_soc():
    from ev_pack.pack_sim import to_true

    return to_true(0.10)


def target_true_soc():
    from ev_pack.pack_sim import to_true

    return to_true(0.90)


def make_sim(combo, temp_c, input_current=True):
    """DFN simulation at fixed temperature, starting from an empty cell (same start as the charge maps)."""
    import pybamm

    from ev_pack.na_cell import _model, parameter_values

    d = design(combo)
    p = parameter_values(d, temp_c)
    p.update({"Upper voltage cut-off [V]": 4.45, "Lower voltage cut-off [V]": 1.0})
    if input_current:
        p.update({"Current function [A]": "[input]"})
    p.set_initial_stoichiometries(0.0)
    return pybamm.Simulation(_model(), parameter_values=p, solver=pybamm.IDAKLUSolver(rtol=1e-6, atol=1e-8),
                             var_pts=VAR_PTS), d.capacity_ah


def prelude(sim, cap):
    """0.25C to the session start SOC, then a 5 min rest. Returns the last solution."""
    secs = start_true_soc() / 0.25 * 3600
    sim.step(secs, inputs={"Current function [A]": -0.25 * cap}, save=False)
    return sim.step(300.0, inputs={"Current function [A]": 0.0}, save=False)


# ---------------------------------------------------------------------------------------------- estimators
def _alpha(tau):
    return 1.0 - np.exp(-DT / tau)


class MapEstimator:
    """(a) What the pack BMS does: anode looked up instantly in the nominal map at the coulomb-counted SOC."""

    name = "map"

    def __init__(self, temp_c, soc0):
        from ev_pack.pack_sim import ETA_TAU_S, maps

        self.m = maps()
        self.T = np.array([temp_c])
        self.soc = soc0
        self.eta = 0.0
        self.a_eta = min(1.0, DT / ETA_TAU_S)
        self.c_prev = 0.0
        self.resid = 0.0

    def _map(self, c):
        cs, ss = np.atleast_1d(c), np.full(np.size(c), self.soc)
        T = np.full(np.size(c), self.T[0])
        return self.m.anode(T, cs, ss), self.m.eta(T, cs, ss), float(self.m.ocv(self.soc))

    def predict(self, c):
        """Estimated anode potential [V] if current c (C-rate, array) is applied for the next step."""
        return self._map(c)[0]

    def predict_voltage(self, c):
        an, eta, ocv = self._map(c)
        return ocv + self.eta + (eta - self.eta) * self.a_eta + self.resid

    def update(self, c, v_meas):
        """Advance the estimator by one step with the applied current and the voltage measured after it."""
        an, eta, ocv = self._map(c)
        self.eta += (float(eta[0]) - self.eta) * self.a_eta
        self.resid = v_meas - (float(self.m.ocv(self.soc)) + self.eta)
        self.soc = min(self.soc + c * DT / 3600, 1.0)
        self.c_prev = c

    def features(self):
        return None


class ObserverEstimator(MapEstimator):
    """(b) Model-based observer: the anode polarisation follows the map with a first-order lag (tau_a), and an online
    gain theta (recursive least squares on the voltage error) scales the polarisation for a cell that differs from the
    nominal design: anode = OCP - (1 + theta) * lagged polarisation."""

    name = "observer"

    def __init__(self, temp_c, soc0, tau_a=30.0, forget=0.995):
        super().__init__(temp_c, soc0)
        self.a_pol = _alpha(tau_a)
        self.pol = 0.0
        self.theta, self.P, self.lam = 0.0, 100.0, forget

    def _pol_next(self, c):
        an, eta, ocv = self._map(c)
        ocp = self.m.anode(np.full(np.size(c), self.T[0]), np.zeros(np.size(c)), np.full(np.size(c), self.soc))
        return ocp, self.pol + ((ocp - an) - self.pol) * self.a_pol

    def predict(self, c):
        ocp, pol = self._pol_next(c)
        return ocp - (1.0 + self.theta) * pol

    def predict_voltage(self, c):
        an, eta, ocv = self._map(c)
        eta_next = self.eta + (eta - self.eta) * self.a_eta
        return ocv + (1.0 + self.theta) * eta_next

    def update(self, c, v_meas):
        ocp, pol = self._pol_next(np.array([c]))
        self.pol = float(pol[0])
        an, eta, ocv = self._map(np.array([c]))
        self.eta += (float(eta[0]) - self.eta) * self.a_eta
        x = self.eta
        y = v_meas - float(self.m.ocv(self.soc)) - self.eta          # = theta * eta + noise
        if abs(x) > 1e-3:                                             # only learn while current flows
            k = self.P * x / (self.lam + x * self.P * x)
            self.theta = float(np.clip(self.theta + k * (y - self.theta * x), -0.6, 3.0))
            self.P = (self.P - k * x * self.P) / self.lam
        self.resid = y - self.theta * x
        self.soc = min(self.soc + c * DT / 3600, 1.0)
        self.c_prev = c


# ---------------------------------------------------------------------------------------------- ML features
FILTER_TAUS_C = (5.0, 15.0, 45.0, 135.0, 400.0)
FILTER_TAUS_R = (15.0, 60.0, 240.0)
FEATURE_NAMES = (["c", "soc", "T", "v", "map_anode", "map_eta", "v_resid", "obs_theta", "obs_anode"]
                 + [f"c_f{int(t)}" for t in FILTER_TAUS_C] + [f"r_f{int(t)}" for t in FILTER_TAUS_R] + ["dv_f15"])


class FeatureState:
    """Causal history features (exponential filters) shared by training and closed-loop prediction."""

    def __init__(self, temp_c, soc0):
        self.obs = ObserverEstimator(temp_c, soc0)
        self.cf = np.zeros(len(FILTER_TAUS_C))
        self.rf = np.zeros(len(FILTER_TAUS_R))
        self.dvf = 0.0
        self.v_prev = None
        self.v_last = float(self.obs.m.ocv(soc0))

    def row(self, c):
        """Features for a candidate current c (array), before applying it. Vectorised over candidates."""
        c = np.atleast_1d(c).astype(float)
        o = self.obs
        an, eta, ocv = o._map(c)
        cf = self.cf[None, :] + (c[:, None] - self.cf[None, :]) * np.array([_alpha(t) for t in FILTER_TAUS_C])[None, :]
        n = len(c)
        cols = [c, np.full(n, o.soc), np.full(n, o.T[0]), np.full(n, self.v_last), an, eta, np.full(n, o.resid),
                np.full(n, o.theta), o.predict(c)]
        return np.column_stack(cols + [cf[:, k] for k in range(cf.shape[1])]
                               + [np.full(n, self.rf[k]) for k in range(len(self.rf))] + [np.full(n, self.dvf)])

    def update(self, c, v_meas):
        self.cf += (c - self.cf) * np.array([_alpha(t) for t in FILTER_TAUS_C])
        self.obs.update(c, v_meas)
        self.rf += (self.obs.resid - self.rf) * np.array([_alpha(t) for t in FILTER_TAUS_R])
        if self.v_prev is not None:
            self.dvf += ((v_meas - self.v_prev) / DT - self.dvf) * _alpha(15.0)
        self.v_prev = v_meas
        self.v_last = v_meas


class MLEstimator:
    """(c) Small MLP predicting the correction to the nominal map's anode potential from causal history features."""

    name = "ml"

    def __init__(self, temp_c, soc0, model):
        self.fs = FeatureState(temp_c, soc0)
        self.model = model          # (scaler_mean, scaler_std, sklearn regressor)

    def predict(self, c):
        X = self.fs.row(c)
        mu, sd, reg = self.model
        return X[:, 4] + reg.predict((X - mu) / sd) / 1000.0     # map anode + learned correction (model in mV)

    def predict_voltage(self, c):
        return self.fs.obs.predict_voltage(c)

    def update(self, c, v_meas):
        self.fs.update(c, v_meas)

    @property
    def soc(self):
        return self.fs.obs.soc
