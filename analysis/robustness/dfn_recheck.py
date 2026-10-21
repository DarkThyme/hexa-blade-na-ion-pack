"""
Re-check the jumpiest robustness traces against the full DFN.

The model card (analysis/validation/MODEL_CARD.md) found the lookup maps up to 50-60 mV optimistic right after sharp
current cuts. Noisy anode estimates make the controller's current jump, so the map-based "true" anode potential in
those runs may itself be optimistic. This replays the average-cell C-rate trace of selected runs through both the map
model and the full DFN (isothermal at the session's mean temperature, as in dynamic_check.py) and reports how much
lower the DFN anode potential goes than the map's.

Run:  .venv/Scripts/python analysis/robustness/dfn_recheck.py
"""

import os

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_v] = "1"

import json  # noqa: E402
import sys  # noqa: E402
import warnings  # noqa: E402
from multiprocessing import Pool  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "analysis" / "validation"))

PICKS = [  # (phase, scenario, margin, arch, charger, ambient)
    (1, "ideal", 15, "blade", "350 kW DC", 25),
    (1, "anode noise 6 mV", 15, "blade", "350 kW DC", 25),
    (1, "anode noise 6 mV", 15, "module", "350 kW DC", 0),
    (1, "anode noise 3 mV", 15, "module", "350 kW DC", 0),
    (1, "combined realistic", 40, "blade", "350 kW DC", 25),
    (1, "combined realistic", 40, "module", "350 kW DC", 0),
    (1, "combined harsh", 40, "blade", "350 kW DC", 0),
]


def recheck(job):
    warnings.simplefilter("ignore")
    import dynamic_check as dc

    from ev_pack.pack_sim import to_true

    row = job
    c4 = np.asarray(row["c_trace"])               # stored every 2nd step (4 s)
    c = np.repeat(c4, 2)                           # back to the 2 s grid
    temp = float(np.mean(row["temp_mean"]))
    full, i0 = dc.with_prelude(c, to_true(0.10))
    Vm, Am, _ = dc.run_map(full, temp)
    d = dc.run_dfn(full, temp)
    out = {k: row[k] for k in ("scenario", "margin_mv", "arch", "charger", "ambient_c")}
    out["replay_temp_c"] = round(temp, 1)
    out["max_c_jump"] = row["max_c_jump"]
    if d is None:
        out["dfn_failed"] = True
        return out
    Vd, Ad, _, n = d
    sl = slice(i0, min(n, len(full)))
    ok = (Vd[sl] <= 4.30) & (Vm[sl] <= 4.30)
    if not ok.any():
        out["no_window"] = True
        return out
    am, ad = Am[sl][ok] * 1000, Ad[sl][ok] * 1000
    out.update({"map_min_mv": round(float(am.min()), 2), "dfn_min_mv": round(float(ad.min()), 2),
                "map_optimism_max_mv": round(float((am - ad).max()), 2),
                "map_optimism_near_limit_mv": round(float((am - ad)[ad < 50].max()), 2) if (ad < 50).any() else 0.0,
                "dfn_reached_end": bool(n >= len(full))})
    return out


def main():
    rows = json.loads((HERE / "results.json").read_text())["rows"]
    jobs = []
    for ph, sc, mg, ar, ch, amb in PICKS:
        jobs.append(next(r for r in rows if r["phase"] == ph and r["scenario"] == sc and r["margin_mv"] == mg
                         and r["arch"] == ar and r["charger"] == ch and r["ambient_c"] == amb))
    with Pool(min(6, len(jobs)), maxtasksperchild=2) as pool:
        res = pool.map(recheck, jobs)
    for r in res:
        print(json.dumps(r, ensure_ascii=False), flush=True)
    (HERE / "dfn_recheck.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
