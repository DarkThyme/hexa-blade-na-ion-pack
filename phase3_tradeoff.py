"""
Phase 3 trade-off: how hot should the pack be to charge 10 -> 90% fast, and what does it cost in aging?

Sweeps the pack preconditioning / coolant temperature for the anode-aware BMS (15 mV margin) and
reports time to 80/85/90% and the modelled SEI exposure index (equivalent hours at 25 C) for each charger.

Run:  .venv/Scripts/python phase3_tradeoff.py
"""

import json
import warnings
from dataclasses import replace

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from ev_pack.pack_sim import STRATEGIES, simulate
from phase1_baseline import RESULTS

warnings.simplefilter("ignore")
TEMPS = [25, 30, 35, 40, 45]
CHARGERS = ["250 kW DC", "350 kW DC"]
ARCH = "blade"


def main():
    rows = []
    base = STRATEGIES["optimized"]
    for ch in CHARGERS:
        for t in TEMPS:
            st = replace(base, id=f"opt{t}", precondition_c=float(t), coolant_setpoint_c=float(t) - 2)
            s = simulate(ARCH, ch, st, 0.10, 0.90).summary
            tt = s["time_to_soc_min"]
            rows.append({"charger": ch, "pack_temp_c": t, "t80": tt.get("0.8"), "t85": tt.get("0.85"),
                         "t90": tt.get("0.9"), "peak_temp_c": round(s["peak_temp_c"], 1),
                         "sei_aging_h25": s["sei_aging_h25"], "limiter": s["main_limiter"]})
            print(rows[-1], flush=True)
        std = simulate(ARCH, ch, "standard", 0.10, 0.90).summary
        rows.append({"charger": ch, "pack_temp_c": "standard", "t80": std["time_to_soc_min"].get("0.8"),
                     "t85": std["time_to_soc_min"].get("0.85"), "t90": std["time_to_soc_min"].get("0.9"),
                     "peak_temp_c": round(std["peak_temp_c"], 1), "sei_aging_h25": std["sei_aging_h25"],
                     "limiter": std["main_limiter"]})
        print(rows[-1], flush=True)
    (RESULTS / "phase3_tradeoff.json").write_text(json.dumps(rows, indent=1))

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    for ch, color in zip(CHARGERS, ("tab:orange", "tab:red")):
        r = [x for x in rows if x["charger"] == ch and x["pack_temp_c"] != "standard"]
        std = next(x for x in rows if x["charger"] == ch and x["pack_temp_c"] == "standard")
        axes[0].plot([x["pack_temp_c"] for x in r], [x["t90"] for x in r], "o-", color=color, label=f"{ch} anode-aware")
        axes[0].axhline(std["t90"], color=color, ls="--", lw=1, label=f"{ch} standard BMS")
        axes[1].plot([x["sei_aging_h25"] for x in r], [x["t90"] for x in r], "o-", color=color, label=ch)
        for x in r:
            axes[1].annotate(f"{x['pack_temp_c']}°C", (x["sei_aging_h25"], x["t90"]), fontsize=8,
                             textcoords="offset points", xytext=(4, 4))
        axes[1].scatter([std["sei_aging_h25"]], [std["t90"]], marker="s", color=color)
    axes[0].set_xlabel("Pack preconditioning temperature [°C]")
    axes[0].set_ylabel("10 → 90% time [min]")
    axes[1].set_xlabel("Modelled SEI exposure index per session [equiv. h at 25 °C]")
    axes[1].set_ylabel("10 → 90% time [min]")
    axes[0].legend(fontsize=8)
    axes[1].legend(fontsize=8)
    for ax in axes:
        ax.grid(alpha=0.3)
    fig.suptitle("Na-ion Hex-Blade pack: charging hotter is faster - what does it cost? (squares = standard BMS)")
    fig.tight_layout()
    fig.savefig(RESULTS / "phase3_tradeoff.png", dpi=150)
    print("saved", RESULTS / "phase3_tradeoff.png")


if __name__ == "__main__":
    main()
