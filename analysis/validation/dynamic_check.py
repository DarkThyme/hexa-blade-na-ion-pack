"""
Does the pack simulator's map-based cell model match a full DFN cell under CHANGING current?

The pack simulator (ev_pack/pack_sim.py) never solves a DFN per cell. It looks up overpotential and anode
potential vs Na in constant-current DFN maps (ev_pack/charge_maps.py) and lags the overpotential by
ETA_TAU_S = 15 s. The anode potential is looked up with no lag. This script drives a single cell with
identical current-vs-time profiles in both models and compares voltage and anode potential.

Both models run isothermal (PyBaMM's sodium BasicDFN uses a constant temperature), from the same start:
a 0.25C charge from empty to the session start SOC (true 12.3 %, i.e. display 10 %) and a 5 min rest.

Error sign convention: anode error = map - DFN. POSITIVE = the map thinks the anode is further from plating
than it is (unsafe direction for a BMS that trusts the map).

Run:  .venv/Scripts/python analysis/validation/dynamic_check.py [--workers 6]
"""

import os

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_v] = "1"

import argparse  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
import warnings  # noqa: E402
from multiprocessing import Pool  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

DT = 2.0
PRELUDE_C, REST_S = 0.25, 300.0


# ------------------------------------------------------------------ profiles (C-rate vs time, main part only)
def session_profile(strategy):
    from ev_pack.pack_sim import simulate

    warnings.simplefilter("ignore")
    s = simulate("blade", "350 kW DC", strategy, 0.10, 0.90, ambient_c=25, precondition=True).series
    t = np.asarray(s["time_min"]) * 60
    c = np.asarray(s["c_rate"])
    tt = np.arange(0, t[-1] + DT, DT)
    return np.interp(tt, t, c)


def steps_profile():
    seg = [(3.0, 240), (1.0, 240), (0.0, 60), (3.0, 240), (0.0, 60), (1.0, 180), (3.0, 180), (0.5, 240)]
    return np.concatenate([np.full(int(d / DT), c) for c, d in seg])


def pulse_profile():
    one = np.concatenate([np.full(int(30 / DT), 4.0), np.full(int(30 / DT), 1.0)])
    return np.tile(one, 10)


def cc_profile(c, minutes):
    return np.full(int(minutes * 60 / DT), c)


def with_prelude(c_main, start_true_soc):
    n_pre = int(np.ceil(start_true_soc / PRELUDE_C * 3600 / DT))
    pre = np.full(n_pre, PRELUDE_C)
    rest = np.zeros(int(REST_S / DT))
    c = np.concatenate([pre, rest, c_main])
    return c, len(pre) + len(rest)


# ------------------------------------------------------------------ models
def run_map(c, temp_c):
    from ev_pack.pack_sim import ETA_TAU_S, maps

    m = maps()
    T = np.array([temp_c])
    soc, eta_dyn = 0.0, 0.0
    V, A, S = [], [], []
    for ck in c:
        cc = np.array([ck])
        s_ = np.array([soc])
        eta_map = float(m.eta(T, cc, s_)[0])
        eta_dyn += (eta_map - eta_dyn) * min(1.0, DT / ETA_TAU_S)
        V.append(float(m.ocv(soc)) + eta_dyn)
        A.append(float(m.anode(T, cc, s_)[0]))
        S.append(soc)
        soc = min(soc + ck * DT / 3600, 1.0)
    return np.array(V), np.array(A), np.array(S)


def run_dfn(c, temp_c):
    import pybamm

    from ev_pack.na_cell import ANODE_SEP_VAR, CellDesign, _model, parameter_values

    d = CellDesign()
    p = parameter_values(d, temp_c)
    t = np.arange(len(c)) * DT
    # steps -> piecewise-linear with 1 s ramps so the solver is not handed a discontinuity
    tk, ik = [0.0], [c[0]]
    for k in range(1, len(c)):
        if c[k] != c[k - 1]:
            tk += [t[k] - 0.5, t[k] + 0.5]
            ik += [c[k - 1], c[k]]
    tk.append(t[-1] + DT)
    ik.append(c[-1])
    current = pybamm.Interpolant(np.array(tk), -np.array(ik) * d.capacity_ah, pybamm.t, interpolator="linear")
    p.update({"Current function [A]": current, "Upper voltage cut-off [V]": 4.45, "Lower voltage cut-off [V]": 1.0})
    sol = None
    for solver in (pybamm.IDAKLUSolver(rtol=1e-6, atol=1e-8),
                   pybamm.IDAKLUSolver(rtol=1e-7, atol=1e-9, options={"max_num_steps": 400000})):
        sim = pybamm.Simulation(_model(), parameter_values=p, solver=solver,
                                var_pts={"x_n": 16, "x_s": 8, "x_p": 16, "r_n": 16, "r_p": 12})
        try:
            pybamm.set_logging_level("CRITICAL")
            sol = sim.solve(t_eval=[0, float(t[-1])], t_interp=t, initial_soc=0.0)
            break
        except pybamm.SolverError:
            sol = None
        finally:
            pybamm.set_logging_level("WARNING")
    if sol is None:
        return None
    n = len(sol["Time [s]"].entries)
    q = -sol["Discharge capacity [A.h]"].entries
    return sol["Voltage [V]"].entries, sol[ANODE_SEP_VAR].entries, q / d.capacity_ah, n


# ------------------------------------------------------------------ one case
def run_case(case):
    warnings.simplefilter("ignore")
    from ev_pack.pack_sim import to_true

    name, temp_c = case
    t0 = time.time()
    main = {"session_anode_aware": lambda: session_profile("optimized"),
            "session_standard": lambda: session_profile("standard"),
            "steps": steps_profile, "pulses": pulse_profile,
            "cc_2C": lambda: cc_profile(2.0, 20)}[name]()
    c, i0 = with_prelude(main, to_true(0.10))
    Vm, Am, Sm = run_map(c, temp_c)
    dfn = run_dfn(c, temp_c)
    out = {"profile": name, "temp_c": temp_c, "n_main": len(main), "prelude_steps": i0}
    if dfn is None:
        out["dfn_failed"] = True
        return out
    Vd, Ad, Sd, n = dfn
    k = slice(i0, min(n, len(c)))          # compare the main part only, up to where the DFN got
    valid = (Vd[k] <= 4.30) & (Vm[k] <= 4.30)   # inside the 4.3 V window the maps cover
    if not valid.any():
        out["no_valid_window"] = True
        return out
    last = np.flatnonzero(valid)[-1] + 1
    sl = slice(i0, i0 + last)
    eV = Vm[sl] - Vd[sl]
    eA = Am[sl] - Ad[sl]
    tt = np.arange(last) * DT / 60
    out.update({
        "dfn_reached_end": bool(n >= len(c)), "compared_min": round(float(tt[-1]), 2),
        "soc_end_map": round(float(Sm[sl][-1]), 4), "soc_end_dfn": round(float(Sd[sl][-1]), 4),
        "v_rms_mv": round(float(np.sqrt(np.mean(eV ** 2))) * 1000, 2), "v_max_abs_mv": round(float(np.max(np.abs(eV))) * 1000, 2),
        "anode_rms_mv": round(float(np.sqrt(np.mean(eA ** 2))) * 1000, 2),
        "anode_unsafe_max_mv": round(float(np.max(eA)) * 1000, 2),       # map optimistic
        "anode_safe_max_mv": round(float(-np.min(eA)) * 1000, 2),        # map pessimistic
        "anode_unsafe_frac": round(float(np.mean(eA > 0.002)), 3),
        "min_anode_map_mv": round(float(Am[sl].min()) * 1000, 2), "min_anode_dfn_mv": round(float(Ad[sl].min()) * 1000, 2),
        "runtime_s": round(time.time() - t0, 1),
        "trace": {"t_min": [round(float(x), 3) for x in tt[::5]], "c": [round(float(x), 3) for x in c[sl][::5]],
                  "v_map": [round(float(x), 4) for x in Vm[sl][::5]], "v_dfn": [round(float(x), 4) for x in Vd[sl][::5]],
                  "a_map": [round(float(x) * 1000, 2) for x in Am[sl][::5]], "a_dfn": [round(float(x) * 1000, 2) for x in Ad[sl][::5]],
                  "soc": [round(float(x), 4) for x in Sd[sl][::5]]},
    })
    return out


CASES = ([("cc_2C", 25)] +
         [(p, T) for p in ("session_anode_aware", "session_standard") for T in (25, 35, 0, 45)] +
         [(p, T) for p in ("steps", "pulses") for T in (25, 0, 45)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--only", default=None, help="run one profile name (smoke test)")
    a = ap.parse_args()
    cases = [c for c in CASES if a.only is None or c[0] == a.only]
    t0 = time.time()
    rows = []
    with Pool(min(a.workers, len(cases)), maxtasksperchild=2) as pool:
        for r in pool.imap_unordered(run_case, cases):
            rows.append(r)
            brief = {k: v for k, v in r.items() if k != "trace"}
            print(json.dumps(brief), flush=True)
    rows.sort(key=lambda r: CASES.index((r["profile"], r["temp_c"])))
    out = HERE / ("results.json" if a.only is None else f"smoke_{a.only}.json")
    out.write_text(json.dumps(rows))
    print(f"done in {time.time() - t0:.0f}s -> {out}")


if __name__ == "__main__":
    main()
