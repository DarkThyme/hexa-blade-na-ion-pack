"""
Open-loop ground truth: drive the full DFN with varied BMS-style current profiles for every parameter case.

Each session: prelude (0.25C to 12.3 % true SOC + 5 min rest), then a perturbed per-cell C-rate trace taken from the
pack simulator (cold and warm sessions, both BMS strategies), randomly scaled (x0.75-1.25) with inserted rests and
steps. Recorded: applied C-rate, terminal voltage, true anode potential at the separator, true SOC.

Run:  .venv/Scripts/python analysis/estimator/generate.py [--workers 8]
"""

import os

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_v] = "1"

import argparse  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
import warnings  # noqa: E402
from multiprocessing import Pool  # noqa: E402

import numpy as np  # noqa: E402

import common as C  # noqa: E402

DATA = C.HERE / "data"
DATA.mkdir(exist_ok=True)
SESSIONS_PER_CASE = 4


def base_profiles():
    path = C.HERE / "base_profiles.json"
    if path.exists():
        return json.loads(path.read_text())
    from ev_pack.pack_sim import simulate

    warnings.simplefilter("ignore")
    out = {}
    for arch in ("blade", "module"):
        for ch in ("250 kW DC", "350 kW DC"):
            for amb, pre in ((25, True), (0, False)):
                for st in ("standard", "optimized"):
                    s = simulate(arch, ch, st, 0.10, 0.90, ambient_c=amb, precondition=pre).series
                    out[f"{arch}|{ch}|{amb}|{st}"] = [round(float(x), 4) for x in s["c_rate"]]
    path.write_text(json.dumps(out))
    return out


def perturb(c, rng):
    c = np.asarray(c, float) * rng.uniform(0.75, 1.25)
    c = np.clip(c, 0.0, 4.5)
    if rng.random() < 0.6:                          # rests
        for _ in range(rng.integers(1, 3)):
            k = rng.integers(10, max(11, len(c) - 10))
            c = np.concatenate([c[:k], np.zeros(int(rng.uniform(30, 120) / C.DT)), c[k:]])
    if rng.random() < 0.4:                          # a sharp step down then back up
        k = rng.integers(10, max(11, len(c) - 60))
        n = int(rng.uniform(40, 120) / C.DT)
        c = c.copy()
        c[k:k + n] = c[k:k + n] * rng.uniform(0.2, 0.5)
    return c


def run_session(job):
    warnings.simplefilter("ignore")
    import pybamm

    from ev_pack.na_cell import ANODE_SEP_VAR

    combo, temp, idx, profile_key, seed = job
    rng = np.random.default_rng(seed)
    main = perturb(base_profiles()[profile_key], rng)
    pre_n = int(np.ceil(C.start_true_soc() / 0.25 * 3600 / C.DT))
    rest_n = int(300 / C.DT)
    c = np.concatenate([np.full(pre_n, 0.25), np.zeros(rest_n), main])
    t = np.arange(len(c)) * C.DT
    sim, cap = C.make_sim(combo, temp, input_current=False)
    tk, ik = [0.0], [c[0]]
    for k in range(1, len(c)):
        if c[k] != c[k - 1]:
            tk += [t[k] - 0.5, t[k] + 0.5]
            ik += [c[k - 1], c[k]]
    tk.append(t[-1] + C.DT)
    ik.append(c[-1])
    sim.parameter_values.update({"Current function [A]": pybamm.Interpolant(np.array(tk), -np.array(ik) * cap, pybamm.t)})
    sim = pybamm.Simulation(sim.model, parameter_values=sim.parameter_values, solver=sim.solver, var_pts=C.VAR_PTS)
    t0 = time.time()
    try:
        pybamm.set_logging_level("CRITICAL")
        sol = sim.solve(t_eval=[0, float(t[-1])], t_interp=t)
    except Exception as e:  # solver failure is recorded, not hidden
        return {"combo": combo, "temp": temp, "idx": idx, "profile": profile_key, "failed": str(e)[:200]}
    finally:
        pybamm.set_logging_level("WARNING")
    n = len(sol["Time [s]"].entries)
    v = sol["Voltage [V]"].entries
    a = sol[ANODE_SEP_VAR].entries
    q = -sol["Discharge capacity [A.h]"].entries / cap
    i0 = pre_n + rest_n
    stop = n
    over = np.flatnonzero((v[i0:] > C.V_MAX) | (q[i0:] > 0.95))
    if over.size:
        stop = i0 + int(over[0])
    sl = slice(i0, stop)
    name = f"{'_'.join(f'{x:g}' for x in combo)}_T{int(temp)}_{idx}"
    if stop - i0 < 30:
        return {"combo": combo, "temp": temp, "idx": idx, "profile": profile_key, "failed": "too short"}
    np.savez_compressed(DATA / f"{name}.npz", c=c[sl], v=v[sl], anode=a[sl], soc=q[sl], soc0=q[i0])
    return {"combo": combo, "temp": temp, "idx": idx, "profile": profile_key, "file": f"{name}.npz",
            "steps": stop - i0, "held_out": tuple(combo) in C.HELD_OUT, "min_anode_mv": round(float(a[sl].min()) * 1000, 1),
            "solve_s": round(time.time() - t0, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    DATA.mkdir(exist_ok=True)
    keys = sorted(base_profiles())
    rng = np.random.default_rng(7)
    jobs = []
    for combo in C.COMBOS:
        for temp in C.TEMPS:
            for idx in range(SESSIONS_PER_CASE):
                jobs.append((combo, temp, idx, keys[rng.integers(len(keys))], int(rng.integers(1 << 30))))
    index_path = C.HERE / "sessions.jsonl"
    done = set()
    if index_path.exists():
        for line in index_path.read_text().splitlines():
            r = json.loads(line)
            done.add((tuple(r["combo"]), r["temp"], r["idx"]))
    todo = [j for j in jobs if (tuple(j[0]), j[1], j[2]) not in done]
    print(f"{len(jobs)} sessions, {len(todo)} to run; held-out combos: {C.HELD_OUT}", flush=True)
    t0 = time.time()
    with Pool(a.workers, maxtasksperchild=8) as pool, open(index_path, "a") as log:
        for i, r in enumerate(pool.imap_unordered(run_session, todo), 1):
            log.write(json.dumps(r) + "\n")
            log.flush()
            if i % 25 == 0 or i == len(todo):
                print(f"  [{i}/{len(todo)}] {time.time() - t0:.0f}s", flush=True)
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
