"""
Export simulation scenarios for the web viewer (docs/viewer_data_contract.md, schema hexablade.v1).

Run:
    .venv/Scripts/python -m ev_pack.export                 # full grid -> web_data/
    .venv/Scripts/python -m ev_pack.export --quick         # 350 kW only, both strategies
"""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .architectures import GROSS_KWH, build
from .pack_sim import LIMITERS, SOC_HI, SOC_LO, STRATEGIES, Result, build_nominal, maps, simulate
from .spec import CHARGERS

OUT = Path(__file__).resolve().parent.parent / "web_data"
TEMP_STEP, SOC_STEP = 0.25, 0.005
MAX_SERIES = 2000

VEHICLE = {"wheelbase_m": 2.95, "track_m": 1.66, "wheel_radius_m": 0.37, "body_length_m": 4.85,
           "body_width_m": 1.95, "body_height_m": 1.62, "pack_floor_z_m": 0.15}


def pack_json(arch_id: str) -> dict:
    a = build_nominal(arch_id)
    m = maps()
    usable = GROSS_KWH * (SOC_HI - SOC_LO)
    L, W, H = a.dims
    v_nom = float(np.trapezoid(m.ocv_table, m.S)) * a.n_series
    shape = a.cell_shape
    spec = {
        "Chemistry": "Sodium-ion — NVPF cathode / hard-carbon anode (DFN physics, Chayambuka 2022 base)",
        "Architecture": a.name,
        "Cell": f"Hexagonal prism {shape['across_flats_m'] * 1000:.0f} mm AF × {shape['length_m'] * 1000:.0f} mm, "
                f"{a.cell_capacity_ah:.1f} Ah, {a.cell_energy_wh:.0f} Wh, {a.cell_mass_kg:.2f} kg",
        "Configuration": f"{a.n_series}s{a.n_parallel}p, {a.n_cells} cells"
                         + (f", {len(a.modules)} modules" if a.id == "module" else ", cell-to-pack"),
        "Energy (gross)": f"{GROSS_KWH:.1f} kWh",
        "Usable": f"{usable:.1f} kWh ({SOC_LO:.0%} bottom / {1 - SOC_HI:.0%} top buffer)",
        "Voltage": f"{v_nom:.0f} V nominal, {m.v_max * a.n_series:.0f} V max (800 V class)",
        "Pack size": f"{L:.2f} × {W:.2f} × {H:.2f} m",
        "Pack mass (est.)": f"{a.n_cells * a.cell_mass_kg / 0.78:.0f} kg",
        "Cooling": "every cell on a cold plate; 30 L/min, 25 kW chiller, 7 kW heater",
    }
    return {
        "arch": a.id,
        "spec": spec,
        "dimensions_m": [round(L, 4), round(W, 4), round(H, 4)],
        "cell_shape": shape,
        "cells": np.round(a.pos, 5).tolist(),
        "cell_module": a.module.tolist(),
        "modules": [{"center": [round(v, 4) for v in md["center"]], "size": [round(v, 4) for v in md["size"]]}
                    for md in a.modules],
        "cooling": [{"type": p["type"], "center": [round(float(v), 4) for v in p["center"]],
                     "size": [round(float(v), 4) for v in p["size"]]} for p in a.plates],
        "coolant": {"inlet": [round(-L / 2 - 0.05, 3), -0.3, 0.02], "outlet": [round(-L / 2 - 0.05, 3), 0.3, 0.02]},
        "vehicle": VEHICLE,
    }


def scenario_id(charger: str, ambient: float, pre: bool, strategy: str, start: float, target: float) -> str:
    kw = charger.split()[0] + "kW"
    return f"{kw}_{int(ambient)}C_{'pre' if pre else 'nopre'}_{strategy}_{int(start * 100)}-{int(target * 100)}"


def write_scenario(res: Result, sid: str, out_dir: Path) -> dict:
    s = res.series
    n = len(s["time_min"])
    idx = np.unique(np.linspace(0, n - 1, min(n, MAX_SERIES)).round().astype(int))
    series = {k: (np.round(np.asarray(s[k])[idx], 3).tolist() if k != "limiter" else [s[k][i] for i in idx])
              for k in ("time_min", "power_kw", "current_a", "c_rate", "pack_v", "soc", "temp_max", "temp_min",
                        "temp_mean", "coolant_in", "coolant_out", "anode_min_mv", "limiter")}
    # frame series_index points into the downsampled series
    frame_series_index = np.searchsorted(idx, res.frame_index).clip(0, len(idx) - 1)
    temp_offset = min(-20.0, float(np.floor(res.frame_temp_c.min())))
    if res.frame_temp_c.max() > temp_offset + 255 * TEMP_STEP:
        temp_offset = float(np.ceil(res.frame_temp_c.max() - 255 * TEMP_STEP))
    t8 = np.clip(np.round((res.frame_temp_c - temp_offset) / TEMP_STEP), 0, 255).astype(np.uint8)
    s8 = np.clip(np.round(res.frame_soc / SOC_STEP), 0, 255).astype(np.uint8)
    frames = np.concatenate([t8, s8], axis=1)  # per frame: temps then socs
    (out_dir / f"{sid}.frames.bin").write_bytes(frames.tobytes(order="C"))
    sm = dict(res.summary)
    doc = {
        "id": sid,
        "summary": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in sm.items()},
        "series": series,
        "limiters": LIMITERS,
        "frames": {"count": int(len(res.frame_time_min)),
                   "time_min": np.round(res.frame_time_min, 3).tolist(),
                   "series_index": frame_series_index.tolist(),
                   "temp_offset_c": temp_offset, "temp_step_c": TEMP_STEP, "soc_step": SOC_STEP},
    }
    (out_dir / f"{sid}.json").write_text(json.dumps(doc, separators=(",", ":")))
    return sm


def export(chargers=None, strategies=("standard", "optimized"), conditions=((25, True), (-10, False), (-10, True)),
           archs=("blade", "module"), start=0.10, target=0.90, out: Path = OUT) -> dict:
    chargers = chargers or [c.name for c in CHARGERS]
    manifest = {"schema": "hexablade.v1", "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "architectures": [], "chargers": [c.name for c in CHARGERS], "scenarios": []}
    for arch in archs:
        d = out / arch
        d.mkdir(parents=True, exist_ok=True)
        pj = pack_json(arch)
        (d / "pack.json").write_text(json.dumps(pj, separators=(",", ":")))
        manifest["architectures"].append({"id": arch, "name": build(arch, 1.0).name, "pack": f"{arch}/pack.json"})
        for ch in chargers:
            for st in strategies:
                for amb, pre in conditions:
                    t0 = time.time()
                    res = simulate(arch, ch, st, start, target, amb, pre)
                    sid = scenario_id(ch, amb, pre, st, start, target)
                    sm = write_scenario(res, sid, d)
                    manifest["scenarios"].append({
                        "id": sid, "arch": arch, "charger": ch, "ambient_c": amb, "precondition": pre,
                        "strategy": st, "start_soc": start, "target_soc": target,
                        "file": f"{arch}/{sid}.json", "frames": f"{arch}/{sid}.frames.bin"})
                    tt = sm["time_to_soc_min"]
                    print(f"{arch:>6} {ch:>9} {st:>9} {amb:>4}C pre={pre!s:5}: "
                          f"80% {tt.get('0.8', float('nan')):6.1f} | 90% {tt.get('0.9', float('nan')):6.1f} min | "
                          f"peak {sm['peak_power_kw']:5.0f} kW | Tmax {sm['peak_temp_c']:4.1f} C | "
                          f"anode {sm['min_anode_mv']:5.1f} mV | {sm['main_limiter']} [{time.time() - t0:.0f}s]",
                          flush=True)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return manifest


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="only 350 kW DC at 25 C preconditioned")
    args = ap.parse_args()
    if args.quick:
        export(chargers=["350 kW DC"], conditions=((25, True),))
    else:
        export()
