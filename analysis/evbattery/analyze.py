"""
EVBattery realism targets: behavioural benchmarks for EV fast charging from 464 real vehicles.

Streams all snippets through the restricted loader (safe_load.py), computes per-snippet features,
and writes analysis/evbattery/features.npz, targets.json, REPORT figures.

C-rate is derived from dSOC/dt (fraction of capacity per hour), so it needs no pack capacity.
Charging current is negative in the raw data; we flip it.

Run:  .venv/Scripts/python -I analysis/evbattery/analyze.py
"""

import json
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import safe_load  # noqa: E402

ARCHIVES = {
    1: ROOT / "external_data/evbattery/battery_dataset1.tar.gz",
    2: ROOT / "external_data/evbattery/battery_dataset2.tar.gz",
    3: ROOT / "external_data/evbattery/battery_dataset3 (1).tar.gz",
}
FEATURES = ["dataset", "car", "label", "mileage", "soc0", "soc1", "c_mean", "c_max", "amps_mean",
            "tmax0", "tmax1", "dTdt_k_per_min", "tspread_mean", "vspread_mv_mean", "vspread_mv_max",
            "duration_min", "taper_soc", "cell_v_mean"]
SOC_BINS = np.arange(0, 101, 5)


def snippet_features(a: np.ndarray, ds: int, car: int, label: int, mileage: float):
    c = {k: a[:, i] for i, k in enumerate(safe_load.COLUMNS)}
    t_h = (c["timestamp"] - c["timestamp"][0]) / 3600.0
    dur = t_h[-1]
    amps = -c["current"]  # charging positive
    if dur <= 0 or np.nanmean(amps) <= 0.5:
        return None, None
    soc = c["soc"]
    # local C-rate from SOC slope over ~2 min windows (robust to 0.4 % SOC quantisation)
    w = 12
    dsoc = (soc[w:] - soc[:-w]) / 100.0
    dt = t_h[w:] - t_h[:-w]
    ok = dt > 0
    c_local = np.where(ok, dsoc / np.where(ok, dt, 1), np.nan)
    soc_mid = (soc[w:] + soc[:-w]) / 2
    c_mean = (soc[-1] - soc[0]) / 100.0 / dur
    # temperature rise rate (linear fit, K/min)
    tmin = t_h * 60
    slope = np.polyfit(tmin, c["max_temp"], 1)[0] if np.ptp(tmin) > 0 else np.nan
    vspread = (c["max_single_volt"] - c["min_single_volt"]) * 1000
    # taper onset: first SOC where current falls below 80 % of the snippet's running max, staying low
    taper = np.nan
    run_max = np.maximum.accumulate(amps)
    below = amps < 0.8 * run_max
    if amps.max() > 0 and below.any():
        k = np.argmax(below)
        if k > 5 and below[k:].mean() > 0.8:
            taper = soc[k]
    f = [ds, car, label, mileage, soc[0], soc[-1], c_mean, np.nanmax(c_local) if ok.any() else np.nan,
         amps.mean(), c["max_temp"][0], c["max_temp"][-1], slope, (c["max_temp"] - c["min_temp"]).mean(),
         vspread.mean(), vspread.max(), dur * 60, taper, c["volt"].mean()]
    rows = np.column_stack([soc_mid, c_local, vspread[w // 2: w // 2 + len(soc_mid)],
                            np.full(len(soc_mid), ds), np.full(len(soc_mid), label)])
    return np.array(f, dtype=np.float64), rows[ok]


def main():
    feats, rows_all = [], []
    for ds, path in ARCHIVES.items():
        labels = safe_load.read_labels(str(path))
        t0 = time.time()
        n = 0
        for arr, meta in safe_load.iter_archive(str(path)):
            car = int(meta.get("car", -1))
            f, rows = snippet_features(arr, ds, car, labels.get(car, -1), float(meta.get("mileage", np.nan)))
            n += 1
            if f is None:
                continue
            feats.append(f)
            # keep a 1-in-8 sample of per-row points for the SOC-binned envelopes
            if n % 8 == 0:
                rows_all.append(rows.astype(np.float32))
            if n % 100_000 == 0:
                print(f"  dataset {ds}: {n} snippets ({time.time() - t0:.0f}s)", flush=True)
        print(f"dataset {ds}: {n} snippets, {len(labels)} cars, {time.time() - t0:.0f}s, "
              f"refused so far {len(safe_load.REFUSED)}", flush=True)
    F = np.array(feats)
    R = np.concatenate(rows_all)
    np.savez_compressed(HERE / "features.npz", features=F, feature_names=np.array(FEATURES), rows=R,
                        row_names=np.array(["soc", "c_local", "vspread_mv", "dataset", "label"]))
    print("saved", F.shape, R.shape, "refused", len(safe_load.REFUSED))
    if safe_load.REFUSED:
        (HERE / "refused.txt").write_text("\n".join(safe_load.REFUSED))


if __name__ == "__main__":
    main()
