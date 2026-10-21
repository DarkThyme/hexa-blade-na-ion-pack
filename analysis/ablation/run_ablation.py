"""
Ablation of the two BMS strategies: which setting makes the anode-aware BMS faster?

`standard` and `optimized` (ev_pack/pack_sim.py) differ in four setting groups:
    margin   plating margin                      40 mV        -> 15 mV
    precond  preconditioning / coolant setpoint  30 / 30 C    -> 35 / 33 C
    thermal  thermal derate start / cutoff       45 / 55 C    -> 50 / 58 C
    crate    max C-rate                          4C           -> 5C
This runs the full 2^4 factorial (16 configurations, standard = none switched, optimized = all four) on
both packs x 250 / 350 kW DC x {25 C preconditioned, 0 C and -20 C cold-soaked, 35 C and 45 C hot-soaked}, 10 -> 90 %.
320 runs; each result is appended to runs.jsonl as it finishes (resumable).

Run:  .venv/Scripts/python analysis/ablation/run_ablation.py [--workers 8]
"""

import os

# one maths thread per process, before numpy loads (20 workers x 24 threads once exhausted memory)
for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_v] = "1"

import argparse  # noqa: E402
import itertools  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
import warnings  # noqa: E402
from multiprocessing import Pool  # noqa: E402
from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

GROUPS = ["margin", "precond", "thermal", "crate"]
ARCHS = ["blade", "module"]
CHARGERS = ["250 kW DC", "350 kW DC"]
CONDITIONS = [(25, True), (0, False), (-20, False), (35, False), (45, False)]
FIELDS = {"margin": ("plating_margin_v",), "precond": ("precondition_c", "coolant_setpoint_c"),
          "thermal": ("derate_start_c", "cutoff_c"), "crate": ("max_c_rate",)}


def config_name(switched):
    return "+".join(g for g in GROUPS if g in switched) or "standard"


def make_strategy(switched):
    from dataclasses import replace

    from ev_pack.pack_sim import STRATEGIES

    std, opt = STRATEGIES["standard"], STRATEGIES["optimized"]
    changes = {f: getattr(opt, f) for g in switched for f in FIELDS[g]}
    return replace(std, id=config_name(switched), name=config_name(switched), **changes)


def run_one(case):
    warnings.simplefilter("ignore")
    import numpy as np

    from ev_pack.pack_sim import simulate

    switched, arch, charger, (amb, pre) = case
    st = make_strategy(set(switched))
    t0 = time.time()
    r = simulate(arch, charger, st, 0.10, 0.90, ambient_c=amb, precondition=pre)
    s, sm = r.series, r.summary
    return {
        "config": st.id, "switched": sorted(switched), "arch": arch, "charger": charger, "ambient_c": amb,
        "precondition": pre, "t80_min": sm["time_to_soc_min"].get("0.8"), "t90_min": sm["time_to_soc_min"].get("0.9"),
        "peak_temp_c": round(sm["peak_temp_c"], 2), "peak_c_rate": round(sm["peak_c_rate"], 2),
        "min_anode_mv": round(sm["min_anode_mv"], 2), "aging_h25": sm["sei_aging_h25"],
        "main_limiter": sm["main_limiter"], "limiter_share": {k: round(v, 3) for k, v in sm["limiter_share"].items() if v > 0},
        "margin_held": bool(sm["min_anode_mv"] >= st.plating_margin_v * 1000 - 0.05),
        "below_cutoff": bool(sm["peak_temp_c"] < st.cutoff_c), "runtime_s": round(time.time() - t0, 1),
        "temp_at_90_c": round(float(np.asarray(s["temp_max"])[-1]), 2),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--fresh", action="store_true")
    args = ap.parse_args()
    configs = [tuple(c) for k in range(len(GROUPS) + 1) for c in itertools.combinations(GROUPS, k)]
    cases = [(c, a, ch, cond) for c in configs for a in ARCHS for ch in CHARGERS for cond in CONDITIONS]
    log_path = HERE / "runs.jsonl"
    if args.fresh and log_path.exists():
        log_path.unlink()
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()] if log_path.exists() else []
    have = {(r["config"], r["arch"], r["charger"], r["ambient_c"], r["precondition"]) for r in rows}
    todo = [c for c in cases if (config_name(set(c[0])), c[1], c[2], c[3][0], c[3][1]) not in have]
    print(f"{len(cases)} runs, {len(rows)} done, {len(todo)} to run on {args.workers} workers", flush=True)
    t0 = time.time()
    with Pool(args.workers, maxtasksperchild=8) as pool, open(log_path, "a", encoding="utf-8") as log:
        for i, r in enumerate(pool.imap_unordered(run_one, todo, chunksize=1), 1):
            rows.append(r)
            log.write(json.dumps(r) + "\n")
            log.flush()
            print(f"[{i:3d}/{len(todo)}] {r['config']:28s} {r['arch']:6s} {r['charger']:9s} {r['ambient_c']:>4}C "
                  f"pre={r['precondition']!s:5s} t90={r['t90_min']}  Tmax={r['peak_temp_c']}", flush=True)
    (HERE / "results.json").write_text(json.dumps(rows, indent=0))
    print(f"done in {time.time() - t0:.0f}s: {len(rows)} runs")


if __name__ == "__main__":
    main()
