"""
Charge maps from the physics-based (DFN) sodium-ion cell.

For a grid of temperatures and C-rates we run constant-current charges from empty and record, as a
function of SOC:
  * overpotential  eta = V - OCV            (drives voltage and heat:  q = I * eta)
  * anode potential at the separator vs Na  (sodium-plating indicator)

The pack simulator interpolates these tables per cell, per step, so each of thousands of cells behaves
like the DFN without running thousands of DFNs. This is also how real BMS charge maps are built.

Run:  .venv/Scripts/python -m ev_pack.charge_maps     (~5 min, writes ev_pack/data/na_cell_maps.npz)
"""

import json
import time
import warnings
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path

import numpy as np

from .na_cell import V_MAX, CellDesign, charge_cc

MAPS = Path(__file__).parent / "data" / "na_cell_maps.npz"
T_GRID = np.array([-20.0, -10.0, 0.0, 10.0, 25.0, 35.0, 45.0, 55.0])
C_GRID = np.array([0.05, 0.25, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0])
SOC_GRID = np.linspace(0.0, 1.0, 201)


def build(design: CellDesign = CellDesign(), path: Path = MAPS) -> dict:
    warnings.simplefilter("ignore")
    shape = (len(T_GRID), len(C_GRID), len(SOC_GRID))
    voltage = np.full(shape, np.nan)
    anode = np.full(shape, np.nan)
    complete = np.zeros(shape[:2], dtype=bool)
    for i, T in enumerate(T_GRID):
        for j, c in enumerate(C_GRID):
            t0 = time.time()
            try:
                r = charge_cc(float(c), float(T), design)
            except RuntimeError as e:
                print(f"  {T:+5.0f} C {c:4.2f}C: FAILED ({e})")
                continue
            soc, order = np.unique(r["soc"], return_index=True)
            reach = soc[-1]
            inside = SOC_GRID <= reach
            voltage[i, j, inside] = np.interp(SOC_GRID[inside], soc, r["voltage"][order])
            anode[i, j, inside] = np.interp(SOC_GRID[inside], soc, r["anode_v"][order])
            complete[i, j] = r["complete"]
            print(f"  {T:+5.0f} C {c:4.2f}C: reached SOC {reach:.2f}{'' if r['complete'] else ' (partial)'}"
                  f"  min anode {np.nanmin(anode[i, j]) * 1000:6.1f} mV  [{time.time() - t0:.1f}s]", flush=True)

    # Open-circuit references from the slowest, warmest run (closest to equilibrium)
    ocv = voltage[-2, 0].copy()
    anode_ocp = anode[-2, 0].copy()
    out = {"T_grid": T_GRID, "C_grid": C_GRID, "soc_grid": SOC_GRID, "voltage": voltage, "anode": anode,
           "complete": complete, "ocv": ocv, "anode_ocp": anode_ocp, "v_max": np.array(V_MAX),
           "design_json": np.array(json.dumps(asdict(design)))}
    path.parent.mkdir(exist_ok=True)
    np.savez_compressed(path, **out)
    return out


@lru_cache(maxsize=4)
def load(path: Path = MAPS) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"{path} missing - run `python -m ev_pack.charge_maps` first")
    with np.load(path) as z:
        return {k: z[k] for k in z.files}


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--plateau-d-factor", type=float, default=1.0,
                    help="hard-carbon plateau diffusion penalty (e.g. 0.1); writes na_cell_maps_plateau<f>.npz")
    a = ap.parse_args()
    path = MAPS if a.plateau_d_factor == 1.0 else MAPS.with_name(f"na_cell_maps_plateau{a.plateau_d_factor:g}.npz")
    t = time.time()
    m = build(CellDesign(plateau_d_factor=a.plateau_d_factor), path)
    MAPS_OUT = path
    print(f"saved {MAPS_OUT} in {time.time() - t:.0f}s; complete runs {m['complete'].sum()}/{m['complete'].size}")
