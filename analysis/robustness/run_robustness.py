"""
How much of the ideal-information charging advantage survives imperfect state knowledge?

The controller sees only biased / noisy / delayed estimates (ev_pack.pack_sim.InfoErrors); the plant evolves on its
true state; the evaluator measures violations on the true state and never corrects the controller. The protection
envelope (anode-aware settings: 35 C preconditioning, 50/58 C thermal limits, 5C cap) is fixed; only the plating
margin the controller enforces on its ESTIMATE is swept.

Phase 1  sweep: 8 cases x 14 information scenarios x margins {15, 20, 25, 30, 40} mV.
Phase 3  the standard BMS facing the same errors (fair comparison).
Phase 2  fallback rule: each single error's "equivalent anode error" = how far it pushes the true anode below 15 mV
         at a 15 mV controller margin (worst case over the 8 cases). Rule: margin = 15 mV + sum of the equivalents of
         the declared errors. Derived from single-error runs only, then tested on the two combined scenarios
         (held out) and re-checked on the single errors.

All error ranges are ASSUMED stress values, not measured estimator or sensor performance.
Every result is appended to runs.jsonl as it finishes (resumable).

Run:  .venv/Scripts/python analysis/robustness/run_robustness.py [--workers 8]
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

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

ARCHS = ["blade", "module"]
CHARGERS = ["250 kW DC", "350 kW DC"]
CONDITIONS = [(25, True), (0, False)]
MARGINS = [15, 20, 25, 30, 40]
# name -> InfoErrors kwargs. "kind" groups single errors for the fallback rule.
SCENARIOS = {
    "ideal": {},
    "anode bias +5 mV": {"anode_bias_mv": 5.0},
    "anode bias +10 mV": {"anode_bias_mv": 10.0},
    "anode bias +15 mV": {"anode_bias_mv": 15.0},
    "anode noise 3 mV": {"anode_noise_mv": 3.0},
    "anode noise 6 mV": {"anode_noise_mv": 6.0},
    "temp sensor +3 °C": {"temp_bias_c": 3.0},
    "temp sensor −3 °C": {"temp_bias_c": -3.0},
    "SOC estimate −3 %": {"soc_bias": -0.03},
    "SOC estimate +3 %": {"soc_bias": 0.03},
    "resistance +20 %": {"r_mismatch": 0.20},
    "delay 2 steps (4 s)": {"delay_steps": 2},
    "combined realistic": {"anode_bias_mv": 5.0, "anode_noise_mv": 3.0, "temp_bias_c": 2.0, "soc_bias": -0.02,
                           "r_mismatch": 0.10, "delay_steps": 1},
    "combined harsh": {"anode_bias_mv": 10.0, "anode_noise_mv": 5.0, "temp_bias_c": 3.0, "soc_bias": -0.03,
                       "r_mismatch": 0.20, "delay_steps": 2},
}
COMBINED = ["combined realistic", "combined harsh"]
# single-error building blocks for the fallback rule (per unit of each error)
UNITS = {"anode_bias_mv": ("anode bias +5 mV", 5.0), "anode_noise_mv": ("anode noise 3 mV", 3.0),
         "temp_bias_c": ("temp sensor +3 °C", 3.0), "soc_bias": ("SOC estimate −3 %", -0.03),
         "r_mismatch": ("resistance +20 %", 0.20), "delay_steps": ("delay 2 steps (4 s)", 2)}


def run_one(job):
    warnings.simplefilter("ignore")
    from dataclasses import replace

    from ev_pack.pack_sim import STRATEGIES, InfoErrors, simulate

    phase, scen, margin, arch, charger, (amb, pre) = job
    if phase == 3:   # the standard BMS (40 mV, 30 C, 45/55 C, 4C) facing the same information errors
        st = STRATEGIES["standard"]
    else:
        st = replace(STRATEGIES["optimized"], id=f"aware-{margin:g}mV", plating_margin_v=round(margin / 1000, 6))
    t0 = time.time()
    r = simulate(arch, charger, st, 0.10, 0.90, ambient_c=amb, precondition=pre, info=InfoErrors(**SCENARIOS[scen]))
    s, sm = r.series, r.summary
    cr = s["c_rate"]
    jumps = [abs(cr[k] - cr[k - 1]) for k in range(1, len(cr))]
    return {"phase": phase, "scenario": scen, "margin_mv": margin, "arch": arch, "charger": charger,
            "ambient_c": amb, "precondition": pre, "t80_min": sm["time_to_soc_min"].get("0.8"),
            "t90_min": sm["time_to_soc_min"].get("0.9"), "peak_temp_c": round(sm["peak_temp_c"], 2),
            "main_limiter": sm["main_limiter"], **{k: round(v, 3) for k, v in sm["info"].items()},
            "max_c_jump": round(max(jumps) if jumps else 0.0, 3),
            "c_trace": [round(float(x), 3) for x in cr[::2]], "temp_mean": [round(float(x), 2) for x in s["temp_mean"][::2]],
            "runtime_s": round(time.time() - t0, 1)}


def key(r):
    return (r["phase"], r["scenario"], round(r["margin_mv"], 3), r["arch"], r["charger"], r["ambient_c"], r["precondition"])


def run_jobs(jobs, rows, log, workers):
    have = {key(r) for r in rows}
    todo = [j for j in jobs if (j[0], j[1], round(j[2], 3), j[3], j[4], j[5][0], j[5][1]) not in have]
    print(f"{len(jobs)} jobs, {len(jobs) - len(todo)} done, {len(todo)} to run", flush=True)
    if not todo:
        return
    with Pool(workers, maxtasksperchild=8) as pool:
        for i, r in enumerate(pool.imap_unordered(run_one, todo, chunksize=1), 1):
            rows.append(r)
            log.write(json.dumps(r) + "\n")
            log.flush()
            if i % 20 == 0 or i == len(todo):
                print(f"  [{i}/{len(todo)}] {r['scenario']:22s} {r['margin_mv']:5.1f} mV {r['arch']:6s} {r['charger']} "
                      f"{r['ambient_c']}C t90={r['t90_min']} true_min={r['true_min_anode_mv']}", flush=True)


def fallback_rule(rows):
    """Equivalent anode error per unit of each error type, from single-error phase-1 runs at margin 15 mV."""
    eq = {}
    for field, (scen, unit) in UNITS.items():
        short = [max(0.0, 15.0 - r["true_min_anode_mv"]) for r in rows
                 if r["phase"] == 1 and r["scenario"] == scen and r["margin_mv"] == 15]
        eq[field] = {"scenario": scen, "unit": unit, "equiv_mv_per_unit": max(short) / abs(unit)}
    return eq


def fallback_margin(eq, scen):
    kw = SCENARIOS[scen]
    return 15.0 + sum(eq[f]["equiv_mv_per_unit"] * abs(v) for f, v in kw.items() if f in eq)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--fresh", action="store_true")
    a = ap.parse_args()
    log_path = HERE / "runs.jsonl"
    if a.fresh and log_path.exists():
        log_path.unlink()
    rows = [json.loads(x) for x in log_path.read_text(encoding="utf-8").splitlines()] if log_path.exists() else []
    cases = [(ar, ch, cond) for ar in ARCHS for ch in CHARGERS for cond in CONDITIONS]
    t0 = time.time()
    with open(log_path, "a", encoding="utf-8") as log:
        jobs1 = [(1, sc, mg, ar, ch, cond) for sc in SCENARIOS for mg in MARGINS for ar, ch, cond in cases]
        jobs1.sort(key=lambda j: (j[5][1], -j[2]))   # cold first (slowest)
        run_jobs(jobs1, rows, log, a.workers)
        eq = fallback_rule(rows)
        jobs2 = [(2, sc, round(fallback_margin(eq, sc), 3), ar, ch, cond) for sc in SCENARIOS if sc != "ideal"
                 for ar, ch, cond in cases]
        run_jobs(jobs2, rows, log, a.workers)
        # Phase 3: the standard BMS under the same errors, so both controllers are compared on equal information
        jobs3 = [(3, sc, 40, ar, ch, cond) for sc in SCENARIOS for ar, ch, cond in cases]
        run_jobs(jobs3, rows, log, a.workers)
    (HERE / "results.json").write_text(json.dumps({"rows": rows, "fallback_equivalents": eq,
                                                    "fallback_margins": {sc: fallback_margin(eq, sc) for sc in SCENARIOS}}))
    print(f"done in {time.time() - t0:.0f}s: {len(rows)} runs")


if __name__ == "__main__":
    main()
