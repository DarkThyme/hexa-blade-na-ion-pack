"""
Full test matrix: every pack x charger x ambient temperature x preconditioning x BMS strategy, 10 -> 90 %.

Each run is checked against hard limits (plating margin, cell cutoff temperature, charger power / current /
voltage, energy balance, numerical sanity). Then runs are checked against each other (a faster charger must
not be slower, preconditioning must help in the cold, ...). Results: matrix.csv, checks.json, REPORT figures.

Run:  .venv/Scripts/python analysis/matrix/run_matrix.py [--workers 8]
Each finished run is appended to runs.jsonl straight away; a rerun skips cases already there (resume after a crash).
"""

import os

# One maths thread per process, set before numpy loads (also inherited by the worker processes).
# 20 workers x 24 OpenBLAS threads each exhausted memory and the page file on the first attempt.
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

import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

ARCHS = ["blade", "module"]
CHARGERS = ["7 kW AC", "15 kW AC", "50 kW DC", "125 kW DC", "250 kW DC", "350 kW DC"]
AMBIENTS = [-20, -10, 0, 10, 25, 35, 45]
PRECONDITION = [False, True]
STRATEGIES = ["standard", "optimized"]
START, TARGET = 0.10, 0.90


def run_one(case):
    warnings.simplefilter("ignore")
    from ev_pack.architectures import GROSS_KWH
    from ev_pack.pack_sim import STRATEGIES as ST, simulate, to_true
    from ev_pack.spec import CHARGERS as CH

    arch, charger, amb, pre, strat = case
    t0 = time.time()
    try:
        r = simulate(arch, charger, strat, START, TARGET, ambient_c=amb, precondition=pre)
    except Exception as e:  # a crash is a test failure, not a reason to stop the matrix
        return {"arch": arch, "charger": charger, "ambient_c": amb, "precondition": pre, "strategy": strat,
                "error": f"{type(e).__name__}: {e}", "checks": {"runs": False}}
    s, sm = r.series, r.summary
    st = ST[strat]
    ch = next(c for c in CH if c.name == charger)
    boost = ch.kind == "DC" and ch.max_voltage_v < 800
    stored = GROSS_KWH * (to_true(TARGET) - to_true(START))
    numeric = np.concatenate([np.asarray(s[k], float) for k in s if k != "limiter"])
    t90 = sm["time_to_soc_min"].get("0.9")
    checks = {
        "runs": True,
        "reaches_90pct": t90 is not None,
        "plating_margin_held": sm["min_anode_mv"] >= st.plating_margin_v * 1000 - 0.05,
        "below_cell_cutoff_temp": sm["peak_temp_c"] < st.cutoff_c,
        "within_charger_power": sm["peak_power_kw"] <= ch.power_kw * ch.efficiency * 1.005,
        "within_charger_current": boost or ch.kind == "AC" or float(np.max(s["current_a"])) <= ch.max_current_a * 1.001,
        "within_charger_voltage": boost or ch.kind == "AC" or float(np.max(s["pack_v"])) <= ch.max_voltage_v,
        "energy_balance": stored < sm["energy_to_pack_kwh"] < stored * 1.15,
        "no_nan": bool(np.all(np.isfinite(numeric))),
        "soc_never_decreases": bool(np.all(np.diff(s["soc"]) >= -1e-6)),
    }
    checks = {k: bool(v) for k, v in checks.items()}
    k = np.unique(np.linspace(0, len(s["time_min"]) - 1, 160).round().astype(int))
    return {
        "arch": arch, "charger": charger, "ambient_c": amb, "precondition": pre, "strategy": strat,
        "t80_min": sm["time_to_soc_min"].get("0.8"), "t90_min": t90,
        "peak_power_kw": round(sm["peak_power_kw"], 1), "avg_power_kw": round(sm["avg_power_kw"], 1),
        "peak_temp_c": round(sm["peak_temp_c"], 2), "start_temp_c": round(float(s["temp_mean"][0]), 1),
        "max_spread_c": round(sm["max_temp_spread_c"], 2), "min_anode_mv": round(sm["min_anode_mv"], 2),
        "max_pack_v": round(float(np.max(s["pack_v"])), 1), "energy_kwh": round(sm["energy_to_pack_kwh"], 2),
        "aging_h25": sm["sei_aging_h25"], "main_limiter": sm["main_limiter"],
        "limiter_share": {a: round(b, 3) for a, b in sm["limiter_share"].items() if b > 0},
        "checks": checks, "runtime_s": round(time.time() - t0, 1),
        "curve": {"t": [round(float(x), 2) for x in s["time_min"][k]], "p": [round(float(x), 1) for x in s["power_kw"][k]],
                  "soc": [round(float(x), 4) for x in np.asarray(s["soc"])[k]], "tmax": [round(float(x), 2) for x in s["temp_max"][k]]},
    }


def _plain(o):
    """JSON fallback for numpy scalars."""
    if isinstance(o, np.generic):
        return o.item()
    raise TypeError(type(o).__name__)


def cross_checks(rows):
    """Consistency between runs. Returns a list of {check, case, detail} violations and the number tested."""
    key = {(r["arch"], r["charger"], r["ambient_c"], r["precondition"], r["strategy"]): r for r in rows}
    out, tested = [], {}

    def flag(name, case, detail):
        out.append({"check": name, "case": case, "detail": detail})

    def t(k):
        r = key.get(k)
        return None if r is None or r.get("t90_min") is None else r["t90_min"]

    for arch, amb, pre, st in itertools.product(ARCHS, AMBIENTS, PRECONDITION, STRATEGIES):
        times = [(c, t((arch, c, amb, pre, st))) for c in CHARGERS]
        for (c0, a), (c1, b) in zip(times, times[1:]):
            tested["faster_charger_never_slower"] = tested.get("faster_charger_never_slower", 0) + 1
            if a is not None and b is not None and b > a * 1.005 + 0.05:
                flag("faster_charger_never_slower", f"{arch} {amb}C pre={pre} {st}", f"{c1} {b:.1f} min > {c0} {a:.1f} min")
    for arch, c, amb, st in itertools.product(ARCHS, CHARGERS, AMBIENTS, STRATEGIES):
        a, b = t((arch, c, amb, False, st)), t((arch, c, amb, True, st))
        if amb <= 10:
            tested["preconditioning_helps_in_cold"] = tested.get("preconditioning_helps_in_cold", 0) + 1
            if a is not None and b is not None and b > a * 1.005 + 0.05:
                flag("preconditioning_helps_in_cold", f"{arch} {c} {amb}C {st}", f"pre {b:.1f} min > cold-soaked {a:.1f} min")
    for arch, c, amb, pre in itertools.product(ARCHS, CHARGERS, AMBIENTS, PRECONDITION):
        a, b = t((arch, c, amb, pre, "standard")), t((arch, c, amb, pre, "optimized"))
        tested["anode_aware_never_slower"] = tested.get("anode_aware_never_slower", 0) + 1
        if a is not None and b is not None and b > a * 1.005 + 0.05:
            flag("anode_aware_never_slower", f"{arch} {c} {amb}C pre={pre}", f"optimized {b:.1f} min > standard {a:.1f} min")
    for arch, c, st in itertools.product(ARCHS, CHARGERS, STRATEGIES):
        cold = [(amb, t((arch, c, amb, False, st))) for amb in AMBIENTS if amb <= 25]
        for (a0, x), (a1, y) in zip(cold, cold[1:]):
            tested["warmer_cold_soak_never_slower"] = tested.get("warmer_cold_soak_never_slower", 0) + 1
            if x is not None and y is not None and y > x * 1.005 + 0.05:
                flag("warmer_cold_soak_never_slower", f"{arch} {c} {st}", f"{a1}C {y:.1f} min > {a0}C {x:.1f} min")
        # preconditioned packs start at the same temperature, so ambient should barely matter
        pre_t = [t((arch, c, amb, True, st)) for amb in AMBIENTS]
        pre_t = [v for v in pre_t if v is not None]
        tested["preconditioned_insensitive_to_ambient"] = tested.get("preconditioned_insensitive_to_ambient", 0) + 1
        if pre_t and max(pre_t) > min(pre_t) * 1.05 + 0.1:
            flag("preconditioned_insensitive_to_ambient", f"{arch} {c} {st}", f"{min(pre_t):.1f}-{max(pre_t):.1f} min across ambients")
    return out, tested


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--fresh", action="store_true", help="ignore runs.jsonl and start over")
    args = ap.parse_args()
    cases = list(itertools.product(ARCHS, CHARGERS, AMBIENTS, PRECONDITION, STRATEGIES))
    done_file = HERE / "runs.jsonl"
    if args.fresh and done_file.exists():
        done_file.unlink()
    rows = []
    if done_file.exists():
        for line in done_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r["checks"].get("runs"):
                    rows.append(r)
    have = {(r["arch"], r["charger"], r["ambient_c"], r["precondition"], r["strategy"]) for r in rows}
    todo = [c for c in cases if c not in have]
    # slow AC runs first so the pool stays busy
    todo.sort(key=lambda c: (c[1] not in ("7 kW AC", "15 kW AC"), c))
    print(f"{len(cases)} cases, {len(rows)} already done, {len(todo)} to run on {args.workers} workers", flush=True)
    t0 = time.time()
    # maxtasksperchild recycles workers so memory can't creep up over hundreds of runs
    with Pool(args.workers, maxtasksperchild=8) as pool, open(done_file, "a", encoding="utf-8") as log:
        for i, r in enumerate(pool.imap_unordered(run_one, todo, chunksize=1), 1):
            rows.append(r)
            log.write(json.dumps(r) + "\n")
            log.flush()
            bad = [k for k, v in r["checks"].items() if not v]
            print(f"[{i:3d}/{len(todo)}] {r['arch']:6s} {r['charger']:9s} {r['ambient_c']:>4}C pre={str(r['precondition']):5s} "
                  f"{r['strategy']:9s} t90={r.get('t90_min')}  Tmax={r.get('peak_temp_c')}  "
                  f"{'FAIL ' + ','.join(bad) if bad else 'ok'}", flush=True)
    rows.sort(key=lambda r: (r["arch"], CHARGERS.index(r["charger"]), r["ambient_c"], r["precondition"], r["strategy"]))
    cross, tested = cross_checks(rows)
    (HERE / "results.json").write_text(json.dumps({"rows": rows, "cross": cross, "cross_tested": tested}, indent=0, default=_plain))
    cols = ["arch", "charger", "ambient_c", "precondition", "strategy", "t80_min", "t90_min", "peak_power_kw",
            "avg_power_kw", "start_temp_c", "peak_temp_c", "max_spread_c", "min_anode_mv", "max_pack_v", "energy_kwh",
            "aging_h25", "main_limiter"]
    with open(HERE / "matrix.csv", "w", encoding="utf-8") as f:
        f.write(",".join(cols + ["failed_checks"]) + "\n")
        for r in rows:
            bad = ";".join(k for k, v in r["checks"].items() if not v)
            f.write(",".join(str(r.get(c, "")) for c in cols) + f",{bad}\n")
    n_fail = sum(1 for r in rows if not all(r["checks"].values()))
    print(f"done in {time.time() - t0:.0f}s: {len(rows)} runs, {n_fail} with failed checks, {len(cross)} cross-check violations")


if __name__ == "__main__":
    main()
