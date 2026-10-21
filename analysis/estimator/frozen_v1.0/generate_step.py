"""
Step-aligned open-loop ground truth (fix A).

The v1 data (generate.py) solved each session in one go with 1 s current ramps and sampled at the switch instants, so
a label was the anode potential halfway through a current change and the 'measured' voltage came from the same
instant. The closed loop instead holds a current for 2 s and reads voltage and anode potential at the END of the step.
This regenerates the same sessions (same profiles and seeds as v1) by stepping the DFN exactly like the closed loop:
for each step k, the recorded triple is (applied C-rate c_k, voltage at the end of the step, anode potential at the
end of the step), which is the decision horizon the controller needs.

Output: data_step/*.npz and sessions_step.jsonl (same keys as v1).
Run:  .venv/Scripts/python analysis/estimator/generate_step.py [--workers 8]
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
from generate import SESSIONS_PER_CASE, base_profiles, perturb  # noqa: E402

DATA = C.HERE / "data_step"
DATA.mkdir(exist_ok=True)


def step_session(combo, temp, c_main, stop_v=C.V_MAX, stop_soc=0.95):
    """Hold each C-rate for one 2 s step on the DFN; return per-step (c, v_end, anode_end, soc_end)."""
    from ev_pack.na_cell import ANODE_SEP_VAR

    sim, cap = C.make_sim(combo, temp)
    C.prelude(sim, cap)
    out = {"c": [], "v": [], "anode": [], "soc": []}
    for ck in c_main:
        sol = sim.step(C.DT, inputs={"Current function [A]": -float(ck) * cap}, save=False)
        v = float(sol["Voltage [V]"].entries[-1])
        soc = float(-sol["Discharge capacity [A.h]"].entries[-1] / cap)
        out["c"].append(float(ck))
        out["v"].append(v)
        out["anode"].append(float(sol[ANODE_SEP_VAR].entries[-1]))
        out["soc"].append(soc)
        if v > stop_v or soc > stop_soc:
            break
    return {k: np.array(v) for k, v in out.items()}


def run(job):
    warnings.simplefilter("ignore")
    combo, temp, idx, profile_key, seed = job
    rng = np.random.default_rng(seed)
    main = perturb(base_profiles()[profile_key], rng)       # identical profile to v1 (same seed)
    t0 = time.time()
    try:
        d = step_session(combo, temp, main)
    except Exception as e:  # noqa: BLE001  solver failures are recorded
        return {"combo": combo, "temp": temp, "idx": idx, "profile": profile_key, "failed": str(e)[:200]}
    name = f"{'_'.join(f'{x:g}' for x in combo)}_T{int(temp)}_{idx}"
    if len(d["c"]) < 30:
        return {"combo": combo, "temp": temp, "idx": idx, "profile": profile_key, "failed": "too short"}
    np.savez_compressed(DATA / f"{name}.npz", c=d["c"], v=d["v"], anode=d["anode"], soc=d["soc"], soc0=C.start_true_soc())
    return {"combo": combo, "temp": temp, "idx": idx, "profile": profile_key, "file": f"{name}.npz",
            "steps": int(len(d["c"])), "held_out": tuple(combo) in C.HELD_OUT,
            "min_anode_mv": round(float(d["anode"].min()) * 1000, 1), "solve_s": round(time.time() - t0, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    # reproduce v1's job list exactly (same RNG sequence)
    keys = sorted(base_profiles())
    rng = np.random.default_rng(7)
    jobs = []
    for combo in C.COMBOS:
        for temp in C.TEMPS:
            for idx in range(SESSIONS_PER_CASE):
                jobs.append((combo, temp, idx, keys[rng.integers(len(keys))], int(rng.integers(1 << 30))))
    index_path = C.HERE / "sessions_step.jsonl"
    done = set()
    if index_path.exists():
        for line in index_path.read_text().splitlines():
            r = json.loads(line)
            done.add((tuple(r["combo"]), r["temp"], r["idx"]))
    todo = [j for j in jobs if (tuple(j[0]), j[1], j[2]) not in done]
    print(f"{len(jobs)} sessions, {len(todo)} to run", flush=True)
    t0 = time.time()
    with Pool(a.workers, maxtasksperchild=8) as pool, open(index_path, "a") as log:
        for i, r in enumerate(pool.imap_unordered(run, todo), 1):
            log.write(json.dumps(r) + "\n")
            log.flush()
            if i % 25 == 0 or i == len(todo):
                print(f"  [{i}/{len(todo)}] {time.time() - t0:.0f}s", flush=True)
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
