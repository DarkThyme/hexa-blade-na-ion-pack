"""
Closed loop on the full DFN: each controller charges a held-out cell live, 10 -> 90 % (true SOC window).

Every 2 s the controller picks the highest current on a grid (0 ... 3.4C charger limit) for which ITS estimator predicts
anode >= margin and voltage <= 4.3 V. The DFN plant then advances; the controller only sees the measured voltage.
The evaluator records the true anode potential, completion and stalls, and never corrects the controller.

Controllers: map / observer / ml at 15 mV and at their calibrated margins (openloop.json), the standard 40 mV map
controller, and an "oracle feedback" reference that uses the TRUE previous anode potential (not measurable).
Cases: the 7 held-out parameter combinations at 0 and 10 C (cold, plating-limited; isothermal DFN).

Run:  .venv/Scripts/python analysis/estimator/closed_loop.py [--workers 8]
"""

import os

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_v] = "1"

import argparse  # noqa: E402
import json  # noqa: E402
import pickle  # noqa: E402
import time  # noqa: E402
import warnings  # noqa: E402
from multiprocessing import Pool  # noqa: E402

import numpy as np  # noqa: E402

import common as C  # noqa: E402

GRID = np.linspace(0.0, C.C_MAX, 69)
# Final confirmation family: interior parameter levels never used anywhere before, at a new temperature (5 C) and 0 C.
FINAL_COMBOS = [(d, k, s) for d in (0.8, 1.3) for k in (0.7, 1.4) for s in (0.85, 1.15)]
FINAL_TEMPS = (5.0, 0.0)
MAX_S = 2.5 * 3600
STALL_S = 300.0


class OracleEstimator(C.MapEstimator):
    """Reference only: the true anode potential of the previous step plus the map's change for the new current."""

    name = "oracle"

    def __init__(self, temp_c, soc0, a0):
        super().__init__(temp_c, soc0)
        self.a_prev = a0

    def predict(self, c):
        an, _, _ = self._map(c)
        an_prev, _, _ = self._map(np.array([self.c_prev]))
        return self.a_prev + (an - an_prev[0])

    def observe_truth(self, a_true):
        self.a_prev = a_true


def ctl(label, kind, margin, model=None, tau=None):
    return {"label": label, "kind": kind, "margin": float(margin), "model": model, "tau": tau}


def preset(name):
    """Controller sets. Each reads its margins from the openloop json of the model version it uses."""
    def ol(tag):
        p = C.HERE / (f"openloop_{tag}.json" if tag else "v1/openloop.json")
        return json.loads(p.read_text())

    def m(tag, est):
        return ol(tag)["estimators"][est]["calibrated_margin_mv"]

    v1model = str(C.HERE / "v1" / "ml_model.pkl")
    if name == "A_dev":
        return [ctl("ml A, 15 mV", "ml", 15, str(C.HERE / "ml_model_A.pkl")),
                ctl(f"ml A, calibrated {m('A', 'ml'):.0f} mV", "ml", m("A", "ml"), str(C.HERE / "ml_model_A.pkl"))]
    if name == "rollout":       # data collection on TRAINING cells only
        return [ctl("map, 15 mV", "map", 15), ctl("ml A, 15 mV", "ml", 15, str(C.HERE / "ml_model_A.pkl")),
                ctl("ml A, 25 mV", "ml", 25, str(C.HERE / "ml_model_A.pkl")), ctl("observer, 15 mV", "observer", 15, tau=ol("A")["best_tau"])]
    if name == "B_dev":
        return [ctl("ml B, 15 mV", "ml", 15, str(C.HERE / "ml_model_B.pkl")),
                ctl(f"ml B, calibrated {m('B', 'ml'):.0f} mV", "ml", m("B", "ml"), str(C.HERE / "ml_model_B.pkl"))]
    if name == "final":
        tauB = ol("B")["best_tau"]
        return [ctl("oracle feedback (reference)", "oracle", 15), ctl("map, 15 mV", "map", 15),
                ctl("standard map, 40 mV", "map", 40),
                ctl(f"map, calibrated {m('B', 'map'):.0f} mV", "map", m("B", "map")),
                ctl(f"observer, calibrated {m('B', 'observer'):.0f} mV", "observer", m("B", "observer"), tau=tauB),
                ctl(f"ml v1, calibrated {m('', 'ml'):.0f} mV", "ml", m("", "ml"), v1model),
                ctl(f"ml A, calibrated {m('A', 'ml'):.0f} mV", "ml", m("A", "ml"), str(C.HERE / "ml_model_A.pkl")),
                ctl(f"ml B, calibrated {m('B', 'ml'):.0f} mV", "ml", m("B", "ml"), str(C.HERE / "ml_model_B.pkl")),
                ctl("ml B, 15 mV", "ml", 15, str(C.HERE / "ml_model_B.pkl"))]
    raise ValueError(name)


def controllers(openloop):
    m = {k: v["calibrated_margin_mv"] for k, v in openloop["estimators"].items()}
    return [("oracle feedback (reference)", "oracle", 15.0), ("map, 15 mV", "map", 15.0),
            (f"map, calibrated {m['map']:.0f} mV", "map", m["map"]), ("observer, 15 mV", "observer", 15.0),
            (f"observer, calibrated {m['observer']:.0f} mV", "observer", m["observer"]), ("ml, 15 mV", "ml", 15.0),
            (f"ml, calibrated {m['ml']:.0f} mV", "ml", m["ml"]), ("standard map, 40 mV", "map", 40.0)]


def run(job):
    warnings.simplefilter("ignore")
    from ev_pack.na_cell import ANODE_SEP_VAR

    label, kind, margin, combo, temp, tau, model_path = job[:7]
    save = job[7] if len(job) > 7 else None
    sim, cap = C.make_sim(combo, temp)
    sol = C.prelude(sim, cap)
    soc0 = C.start_true_soc()
    a0 = float(sol[ANODE_SEP_VAR].entries[-1])
    if kind == "map":
        est = C.MapEstimator(temp, soc0)
    elif kind == "observer":
        est = C.ObserverEstimator(temp, soc0, tau_a=tau)
    elif kind == "ml":
        with open(model_path, "rb") as f:
            est = C.MLEstimator(temp, soc0, pickle.load(f))
    else:
        est = OracleEstimator(temp, soc0, a0)
    target = C.target_true_soc()
    t, low, rec = 0.0, 0.0, {"c": [], "a": [], "est": [], "soc": [], "v": []}
    status, t0 = "time limit", time.time()
    while t < MAX_S:
        ea = np.asarray(est.predict(GRID))
        ev = np.asarray(est.predict_voltage(GRID))
        ok = np.flatnonzero((ea >= margin / 1000.0) & (ev <= C.V_MAX))
        k = int(ok[-1]) if ok.size else 0
        c = float(GRID[k])
        try:
            sol = sim.step(C.DT, inputs={"Current function [A]": -c * cap}, save=False)
        except Exception as e:  # noqa: BLE001  (a plant solver failure ends the run and is reported)
            status = f"plant solver failed: {str(e)[:80]}"
            break
        v = float(sol["Voltage [V]"].entries[-1])
        a = float(sol[ANODE_SEP_VAR].entries[-1])
        soc = float(-sol["Discharge capacity [A.h]"].entries[-1] / cap)
        rec["c"].append(c)
        rec["a"].append(a * 1000)
        rec["est"].append(float(ea[k]) * 1000)
        rec["soc"].append(soc)
        rec["v"].append(v)
        est.update(c, v)
        if kind == "oracle":
            est.observe_truth(a)
        t += C.DT
        low = low + C.DT if c < 0.02 else 0.0
        if soc >= target:
            status = "reached 90 %"
            break
        if low >= STALL_S:
            status = "stalled"
            break
    if save and len(rec["c"]) >= 30:   # full-resolution rollout for aggregation (training cells only)
        np.savez_compressed(save, c=np.array(rec["c"]), v=np.array(rec["v"]), anode=np.array(rec["a"]) / 1000,
                            soc=np.array(rec["soc"]), soc0=soc0)
    a_mv, e_mv = np.array(rec["a"]), np.array(rec["est"])
    err = e_mv - a_mv
    near = a_mv < C.NEAR_LIMIT_MV
    return {"controller": label, "kind": kind, "margin_mv": margin, "combo": combo, "temp": temp, "status": status,
            "completed": status == "reached 90 %", "t90_min": round(t / 60, 2) if status == "reached 90 %" else None,
            "soc_end": round(rec["soc"][-1], 4) if rec["soc"] else None, "true_min_anode_mv": round(float(a_mv.min()), 2),
            "below_15mv_s": float(np.sum(a_mv < 15.0) * C.DT), "below_0mv_s": float(np.sum(a_mv < 0.0) * C.DT),
            "opt_err_p99_near_mv": round(float(np.percentile(err[near], 99)), 2) if near.any() else None,
            "opt_err_max_near_mv": round(float(err[near].max()), 2) if near.any() else None,
            "max_c": round(float(max(rec["c"])), 2) if rec["c"] else 0.0, "wall_s": round(time.time() - t0, 1),
            "trace": {k: [round(x, 3) for x in v_[::5]] for k, v_ in rec.items()}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--preset", default=None, help="A_dev | rollout | B_dev | final (default: the v1 run)")
    ap.add_argument("--out", default="v1/closed_loop.jsonl")
    a = ap.parse_args()
    if a.preset is None:
        ol = json.loads((C.HERE / "v1" / "openloop.json").read_text())
        jobs = [(lab, kind, mg, tuple(combo), T, ol["best_tau"], str(C.HERE / "v1" / "ml_model.pkl"))
                for combo in C.HELD_OUT for T in (0.0, 10.0) for lab, kind, mg in controllers(ol)]
    else:
        cases = {"A_dev": [(c, T) for c in C.HELD_OUT for T in (0.0, 10.0)],
                 "B_dev": [(c, T) for c in C.HELD_OUT for T in (0.0, 10.0)],
                 "rollout": [(c, T) for c in C.TRAIN for T in (0.0, 10.0)],
                 "final": [(c, T) for c in FINAL_COMBOS for T in FINAL_TEMPS]}[a.preset]
        roll_dir = C.HERE / "data_rollout"
        roll_dir.mkdir(exist_ok=True)
        jobs = []
        for c, T in cases:
            for k in preset(a.preset):
                save = None
                if a.preset == "rollout":
                    tag = k["label"].replace(",", "").replace(" ", "_")
                    save = str(roll_dir / f"{'_'.join(f'{x:g}' for x in c)}_T{int(T)}_{tag}.npz")
                jobs.append((k["label"], k["kind"], k["margin"], tuple(c), T, k["tau"] or 30.0, k["model"], save))
    log_path = C.HERE / a.out
    rows = [json.loads(x) for x in log_path.read_text().splitlines()] if log_path.exists() else []
    have = {(r["controller"], tuple(r["combo"]), r["temp"]) for r in rows}
    todo = [j for j in jobs if (j[0], j[3], j[4]) not in have]
    print(f"{len(jobs)} closed-loop runs, {len(todo)} to run", flush=True)
    t0 = time.time()
    todo.sort(key=lambda j: j[4])        # 0 C first (slowest)
    with Pool(a.workers, maxtasksperchild=4) as pool, open(log_path, "a") as log:
        for i, r in enumerate(pool.imap_unordered(run, todo), 1):
            log.write(json.dumps(r) + "\n")
            log.flush()
            print(f"  [{i}/{len(todo)}] {r['controller']:28s} {r['combo']} {r['temp']:>4} C: {r['status']:13s} "
                  f"t90={r['t90_min']} true_min={r['true_min_anode_mv']} ({r['wall_s']}s)", flush=True)
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
