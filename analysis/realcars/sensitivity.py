"""
How much of the 10->90 % gain survives slower hard-carbon "plateau" kinetics?

Runs the blade and module packs on a Fastned-class 300 kW charger with the base charge maps and the
plateau-limited variants (hard-carbon diffusion x0.1 and x0.03 above ~55 % anode fill), then compares
against the real 800 V cars from benchmark.json.

Run:  .venv/Scripts/python analysis/realcars/sensitivity.py   (after ev_pack.charge_maps --plateau-d-factor ...)
"""

import json
import sys
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
warnings.simplefilter("ignore")

from benchmark import metrics  # noqa: E402
from ev_pack.architectures import GROSS_KWH  # noqa: E402
from ev_pack.charge_maps import MAPS  # noqa: E402
from ev_pack.pack_sim import SOC_HI, SOC_LO, simulate  # noqa: E402
from ev_pack.spec import ChargerSpec  # noqa: E402

VARIANTS = {"base data": None,
            "plateau D x0.1": MAPS.with_name("na_cell_maps_plateau0.1.npz"),
            "plateau D x0.03": MAPS.with_name("na_cell_maps_plateau0.03.npz")}
CHARGER = ChargerSpec("300 kW DC", "DC", 300.0, 500.0, 920.0, 0.975, "Fastned-class")


def main():
    usable = GROSS_KWH * (SOC_HI - SOC_LO)
    rows, curves = [], {}
    for vname, path in VARIANTS.items():
        for arch in ("blade", "module"):
            for st in ("standard", "optimized"):
                r = simulate(arch, CHARGER, st, 0.05, 0.98, maps_path=path)
                s = r.series
                soc, idx = np.unique(np.round(s["soc"] * 100, 2), return_index=True)
                m = metrics(soc, s["power_kw"][idx], usable)
                rows.append({"cell": vname, "arch": arch, "strategy": st, **m,
                             "min_anode_mv": round(r.summary["min_anode_mv"], 1),
                             "peak_temp_c": round(r.summary["peak_temp_c"], 1)})
                curves[(vname, arch, st)] = (soc, s["power_kw"][idx] / usable)
                print(rows[-1], flush=True)
    (HERE / "sensitivity.json").write_text(json.dumps(rows, indent=1))

    real = [r for r in json.loads((HERE / "benchmark.json").read_text()) if r["class"] == "800V"]
    fig, ax = plt.subplots(1, 2, figsize=(15, 5.5))
    for (vname, arch, st), (soc, c) in curves.items():
        if arch != "blade":
            continue
        col = {"base data": "black", "plateau D x0.1": "tab:orange", "plateau D x0.03": "tab:red"}[vname]
        ax[0].plot(soc, c, "-" if st == "optimized" else "--", color=col, lw=2,
                   label=f"{vname}, {'anode-aware' if st == 'optimized' else 'standard'}")
    ax[0].set(xlabel="SOC [%]", ylabel="C-rate [kW / usable kWh]", title="Blade pack, 300 kW: plateau kinetics sensitivity",
              xlim=(0, 100))
    ax[0].legend(fontsize=8)
    # 10->90 times: real 800 V cars vs variants
    t_real = sorted(r["t10_90"] for r in real if r["t10_90"])
    ax[1].scatter(t_real, [0] * len(t_real), color="tab:blue", s=60, label="real 800 V cars")
    ys = {"standard": 1, "optimized": 2}
    for r in rows:
        if r["t10_90"] is None:
            continue
        col = {"base data": "black", "plateau D x0.1": "tab:orange", "plateau D x0.03": "tab:red"}[r["cell"]]
        mk = "o" if r["arch"] == "blade" else "s"
        ax[1].scatter(r["t10_90"], ys[r["strategy"]], color=col, marker=mk, s=70)
    ax[1].set_yticks([0, 1, 2], ["real 800 V\ncars", "Hexa-Blade\nstandard BMS", "Hexa-Blade\nanode-aware"])
    ax[1].set(xlabel="10 -> 90 % [min] on 300 kW", title="o blade  □ module;  black base / orange x0.1 / red x0.03")
    ax[1].legend(fontsize=8)
    for a in ax:
        a.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(HERE / "sensitivity_plateau.png", dpi=140)
    print("saved", HERE / "sensitivity_plateau.png")


if __name__ == "__main__":
    main()
