"""
Benchmark the Hexa-Blade Na-ion pack against real 800 V cars' DC fast-charging curves.

Real data: Morin, Chatra, Whitacre, Michalek (2025), "Digitized Electric Vehicle Fast-Charging Profiles by
Model (Power vs SOC/SOE, CSV Format)", Figshare, doi:10.6084/m9.figshare.30570653.v1, CC BY 4.0.
Curves were digitized from Fastned / InsideEVs charts (mostly 300 kW Fastned chargers).

All charge times (real and simulated) are computed the same way, from the power-vs-SOC curve:
    t = integral( usable_kWh * dSOC / P(SOC) )
so differences come from the curves, not from bookkeeping.

Run:  .venv/Scripts/python analysis/realcars/benchmark.py
"""

import json
import sys
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
warnings.simplefilter("ignore")

DATA = ROOT / "external_data" / "figshare-fastcharge" / "main-data-fastcharge-profile-het.csv"
# 800 V-class platforms (Porsche J1 / PPE, Hyundai-Kia E-GMP); everything else is shown as 400 V reference
ARCH_800V = ("taycan", "e-tron gt", "ioniq 5", "ioniq 6", "ev6", "macan")  # single words match whole words
REFERENCE_400V = ("model s plaid", "model 3 long range", "i4", "eqs suv 107")
GRID = np.linspace(0, 100, 401)


def load_real() -> dict:
    df = pd.read_csv(DATA, encoding="utf-8-sig")
    df.columns = [c.strip() for c in df.columns]
    out = {}
    for fname, g in df.groupby("filename"):
        name = f"{g['oem_attributed'].iloc[0]} {g['model'].iloc[0]}".strip()
        key = name.lower()
        words = set(key.replace("-", " ").split()) | {key}
        is_800 = any((k in key) if " " in k else (k in words) for k in ARCH_800V)
        cls = "800V" if is_800 else ("400V" if any(k in key for k in REFERENCE_400V) else None)
        if cls is None:
            continue
        g = g.sort_values("soe.percent").drop_duplicates("soe.percent")
        usable = float(g["usable_pack.kWh"].iloc[0])
        label = f"{name} ({usable:.0f} kWh)"
        out[label] = {"class": cls, "usable_kwh": usable, "charger_kw": float(g["charger_rating.kW"].iloc[0]),
                      "soc": g["soe.percent"].to_numpy(float), "power": g["power.kW"].to_numpy(float),
                      "source": str(fname)}
    return out


def metrics(soc, power, usable_kwh) -> dict:
    """Charge metrics from a power-vs-SOC curve (SOC in %, power in kW)."""
    lo, hi = soc.min(), soc.max()
    p = np.interp(GRID, soc, power, left=np.nan, right=np.nan)

    def t(a, b):
        if a < lo - 0.5 or b > hi + 0.5:
            return None
        m = (GRID >= a) & (GRID <= b) & np.isfinite(p) & (p > 0)
        return float(np.trapezoid(usable_kwh / 100 / p[m] * 60, GRID[m]))

    peak = float(np.nanmax(power))
    above = GRID[np.isfinite(p) & (p >= 0.8 * peak)]
    taper = float(above.max()) if above.size else None  # last SOC still at >= 80 % of peak
    return {
        "usable_kwh": usable_kwh, "peak_kw": round(peak, 1),
        "peak_c": round(peak / usable_kwh, 2),
        "t10_80": None if t(10, 80) is None else round(t(10, 80), 1),
        "t10_90": None if t(10, 90) is None else round(t(10, 90), 1),
        "t80_90": None if t(80, 90) is None else round(t(80, 90), 1),
        "avg_kw_10_80": None if t(10, 80) is None else round(usable_kwh * 0.7 / (t(10, 80) / 60), 1),
        "p_at_80_kw": round(float(np.interp(80, soc, power)), 1) if hi >= 80 else None,
        "p_at_90_kw": round(float(np.interp(90, soc, power)), 1) if hi >= 90 else None,
        "taper_soc": None if taper is None else round(taper, 1),
    }


def load_sim() -> dict:
    from ev_pack.pack_sim import SOC_HI, SOC_LO, simulate
    from ev_pack.architectures import GROSS_KWH
    from ev_pack.spec import ChargerSpec

    usable = GROSS_KWH * (SOC_HI - SOC_LO)
    fastned_300 = ChargerSpec("300 kW DC", "DC", 300.0, 500.0, 920.0, 0.975, "Fastned-class 300 kW / 500 A")
    out = {}
    for arch in ("blade", "module"):
        for st, st_name in (("standard", "standard BMS"), ("optimized", "anode-aware BMS")):
            for ch in (fastned_300, "350 kW DC"):
                r = simulate(arch, ch, st, 0.05, 0.98)
                s = r.series
                soc, idx = np.unique(np.round(s["soc"] * 100, 2), return_index=True)
                cname = ch.name if hasattr(ch, "name") else ch
                label = f"Hexa-Blade {arch}, {st_name}, {cname}"
                out[label] = {"class": "ours", "usable_kwh": usable, "charger_kw": float(cname.split()[0]),
                              "soc": soc, "power": s["power_kw"][idx], "arch": arch, "strategy": st}
    return out


def main():
    real = load_real()
    sim = load_sim()
    rows = []
    for label, d in {**real, **sim}.items():
        m = metrics(d["soc"], d["power"], d["usable_kwh"])
        rows.append({"vehicle": label, "class": d["class"], "charger_kw": d["charger_kw"], **m})
    rows.sort(key=lambda r: (r["class"] != "ours", r["class"], r["t10_80"] if r["t10_80"] is not None else 999))
    (HERE / "benchmark.json").write_text(json.dumps(rows, indent=1))

    df = pd.DataFrame(rows)
    pd.set_option("display.width", 220)
    print(df[["vehicle", "class", "charger_kw", "usable_kwh", "peak_kw", "peak_c", "avg_kw_10_80", "t10_80",
              "t10_90", "t80_90", "p_at_80_kw", "p_at_90_kw", "taper_soc"]].to_string(index=False))

    # ---------------------------------------------------------------- figure
    fig, ax = plt.subplots(1, 3, figsize=(19, 6))
    cmap = plt.get_cmap("tab20")
    k = 0
    for label, d in real.items():
        if d["class"] != "800V":
            continue
        col = cmap(k % 20)
        k += 1
        ax[0].plot(d["soc"], d["power"], color=col, lw=1.3, label=label)
        ax[1].plot(d["soc"], d["power"] / d["usable_kwh"], color=col, lw=1.3)
    for label, d in sim.items():
        if "350" in label:
            continue
        style = "-" if d["strategy"] == "optimized" else "--"
        col = "black" if d["arch"] == "blade" else "dimgray"
        ax[0].plot(d["soc"], d["power"], style, color=col, lw=2.6, label=label)
        ax[1].plot(d["soc"], d["power"] / d["usable_kwh"], style, color=col, lw=2.6)
    ax[0].set(xlabel="SOC [%]", ylabel="DC power [kW]", title="Real 800 V cars (300 kW chargers) vs Hexa-Blade",
              xlim=(0, 100))
    ax[1].set(xlabel="SOC [%]", ylabel="C-rate [kW per usable kWh]", title="Normalised by pack size", xlim=(0, 100))
    ax[0].legend(fontsize=7, loc="lower left")
    # bar chart: 10-80 and 80-90 minutes
    sel = [r for r in rows if r["t10_90"] is not None and (r["class"] == "800V" or ("300 kW" in r["vehicle"]))]
    sel.sort(key=lambda r: r["t10_90"])
    names = [r["vehicle"].replace("Hexa-Blade ", "HB ").replace(", 300 kW DC", "") for r in sel]
    a = [r["t10_80"] for r in sel]
    b = [r["t80_90"] for r in sel]
    colors = ["black" if r["class"] == "ours" else "tab:blue" for r in sel]
    y = np.arange(len(sel))
    ax[2].barh(y, a, color=colors, alpha=0.85, label="10 → 80 %")
    ax[2].barh(y, b, left=a, color=colors, alpha=0.35, label="80 → 90 %")
    for i, r in enumerate(sel):
        ax[2].text(r["t10_90"] + 0.3, i, f"{r['t10_90']:.1f}", va="center", fontsize=8)
    ax[2].set_yticks(y, names, fontsize=7)
    ax[2].invert_yaxis()
    ax[2].set(xlabel="minutes", title="10 → 90 % (dark: to 80 %, light: 80 → 90 %)")
    ax[2].legend(fontsize=8)
    for x in ax:
        x.grid(alpha=0.3)
    fig.suptitle("DC fast-charging benchmark — real data: Morin et al. 2025 (Figshare, CC BY 4.0); "
                 "Hexa-Blade: this project's simulation")
    fig.tight_layout()
    fig.savefig(HERE / "benchmark_800v.png", dpi=140)
    print("saved", HERE / "benchmark_800v.png")


if __name__ == "__main__":
    main()
