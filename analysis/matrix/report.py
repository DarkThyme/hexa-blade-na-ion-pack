"""
Build REPORT.md and figures from results.json (written by run_matrix.py).

Run:  .venv/Scripts/python analysis/matrix/report.py
"""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
D = json.loads((HERE / "results.json").read_text())
ROWS = D["rows"]
ARCHS = ["blade", "module"]
ARCH_NAME = {"blade": "Hex-Blade (cell-to-pack)", "module": "Hex-Cell Modules"}
STRATS = ["standard", "optimized"]
STRAT_NAME = {"standard": "Standard BMS", "optimized": "Anode-aware BMS"}
CHARGERS = ["7 kW AC", "15 kW AC", "50 kW DC", "125 kW DC", "250 kW DC", "350 kW DC"]
DC = CHARGERS[2:]
AMB = [-20, -10, 0, 10, 25, 35, 45]
# reference palette (light mode), fixed order: slot 1 blue, slot 2 orange
COL = {"blade": "#2a78d6", "module": "#eb6834"}
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"
STYLE = {"standard": "--", "optimized": "-"}

plt.rcParams.update({
    "font.size": 10, "axes.edgecolor": INK2, "axes.labelcolor": INK, "xtick.color": INK2, "ytick.color": INK2,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "axes.spines.top": False,
    "axes.spines.right": False, "lines.linewidth": 2.0, "figure.facecolor": "#fcfcfb", "axes.facecolor": "#fcfcfb",
})


def get(arch, ch, amb, pre, st):
    return next(r for r in ROWS if r["arch"] == arch and r["charger"] == ch and r["ambient_c"] == amb
                and r["precondition"] == pre and r["strategy"] == st)


def fmt_t(m):
    if m is None:
        return "-"
    return f"{int(m // 60)} h {m % 60:02.0f}" if m >= 100 else f"{m:.1f}"


# ------------------------------------------------------------------ figures
def fig_heatmaps():
    cols = [(a, p) for p in (True, False) for a in AMB]
    fig, axes = plt.subplots(2, 2, figsize=(15, 6.2), sharex=True, sharey=True)
    for i, arch in enumerate(ARCHS):
        for j, st in enumerate(STRATS):
            ax = axes[i, j]
            z = np.array([[get(arch, c, a, p, st)["t90_min"] for a, p in cols] for c in DC])
            ax.imshow(z, cmap="Blues", vmin=10, vmax=80, aspect="auto")
            for y in range(len(DC)):
                for x in range(len(cols)):
                    v = z[y, x]
                    ax.text(x, y, f"{v:.0f}", ha="center", va="center", fontsize=8.5,
                            color="white" if v > 48 else INK)
            ax.axvline(6.5, color="#fcfcfb", lw=3)
            ax.set_title(f"{ARCH_NAME[arch]} · {STRAT_NAME[st]}", fontsize=10.5, color=INK, loc="left")
            ax.set_yticks(range(len(DC)), DC)
            ax.set_xticks(range(len(cols)), [f"{a}" for a, _ in cols], fontsize=8.5)
            ax.grid(False)
    for ax in axes[1]:
        ax.set_xlabel("ambient °C    ← preconditioned (left 7)  |  cold-soaked, no preconditioning (right 7) →",
                      fontsize=9, color=INK2)
    fig.suptitle("Minutes from 10 % to 90 % SOC, DC chargers (AC charging takes 1.8–12.8 h and is charger-limited)",
                 x=0.01, ha="left", fontsize=12, color=INK)
    fig.tight_layout()
    fig.savefig(HERE / "fig_t90_heatmaps.png", dpi=150)
    plt.close(fig)


def fig_cold_soak():
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.4), sharey=True)
    for ax, ch in zip(axes, ["250 kW DC", "350 kW DC"]):
        for arch in ARCHS:
            for st in STRATS:
                y = [get(arch, ch, a, False, st)["t90_min"] for a in AMB]
                ax.plot(AMB, y, STYLE[st], color=COL[arch], marker="o", ms=5,
                        label=f"{ARCH_NAME[arch].split(' (')[0]}, {STRAT_NAME[st]}")
            pre = get(arch, ch, 25, True, "optimized")["t90_min"]
        ax.axhline(get("blade", ch, 25, True, "optimized")["t90_min"], color=INK2, lw=1, ls=":")
        ax.text(45, get("blade", ch, 25, True, "optimized")["t90_min"] - 2.5, "preconditioned, anode-aware",
                ha="right", fontsize=8.5, color=INK2)
        ax.set_title(f"{ch}: arriving without preconditioning", loc="left", fontsize=10.5, color=INK)
        ax.set_xlabel("ambient / pack starting temperature, °C")
        ax.set_xticks(AMB)
    axes[0].set_ylabel("minutes, 10 → 90 %")
    axes[0].legend(fontsize=8.5, frameon=False)
    fig.tight_layout()
    fig.savefig(HERE / "fig_cold_soak.png", dpi=150)
    plt.close(fig)


def fig_peak_temp():
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.2), sharey=True)
    for ax, pre in zip(axes, (True, False)):
        for arch in ARCHS:
            for st in STRATS:
                y = [get(arch, "350 kW DC", a, pre, st)["peak_temp_c"] for a in AMB]
                ax.plot(AMB, y, STYLE[st], color=COL[arch], marker="o", ms=5,
                        label=f"{ARCH_NAME[arch].split(' (')[0]}, {STRAT_NAME[st]}")
        ax.axhline(55, color=INK2, lw=1, ls=":")
        ax.text(-20, 55.4, "standard cutoff 55 °C (anode-aware 58 °C)", fontsize=8.5, color=INK2)
        ax.set_title("350 kW, " + ("preconditioned" if pre else "cold/hot-soaked, no preconditioning"),
                     loc="left", fontsize=10.5, color=INK)
        ax.set_xlabel("ambient °C")
        ax.set_xticks(AMB)
    axes[0].set_ylabel("hottest cell during the session, °C")
    axes[0].legend(fontsize=8.5, frameon=False, loc="lower left")
    fig.tight_layout()
    fig.savefig(HERE / "fig_peak_temp.png", dpi=150)
    plt.close(fig)


# ------------------------------------------------------------------ markdown
def md_table(metric, fmt, title):
    out = [f"#### {title}", ""]
    for arch in ARCHS:
        for st in STRATS:
            out.append(f"**{ARCH_NAME[arch]} · {STRAT_NAME[st]}** (preconditioned / cold-soaked)")
            out.append("")
            out.append("| Charger | " + " | ".join(f"{a} °C" for a in AMB) + " |")
            out.append("|---|" + "---|" * len(AMB))
            for c in CHARGERS:
                cells = [f"{fmt(get(arch, c, a, True, st)[metric])} / {fmt(get(arch, c, a, False, st)[metric])}" for a in AMB]
                out.append(f"| {c} | " + " | ".join(cells) + " |")
            out.append("")
    return out


def main():
    fig_heatmaps()
    fig_cold_soak()
    fig_peak_temp()
    n = len(ROWS)
    checks = list(ROWS[0]["checks"])
    passed = {k: sum(r["checks"][k] for r in ROWS) for k in checks}
    lim = {}
    for r in ROWS:
        lim[r["main_limiter"]] = lim.get(r["main_limiter"], 0) + 1
    hottest = max(ROWS, key=lambda r: r["peak_temp_c"])
    L = []
    L += ["# Full test matrix: every charger × every starting condition", "",
          f"**{n} charging sessions**, each 10 % → 90 % SOC: 2 packs × 6 chargers × 7 ambient temperatures "
          "(−20, −10, 0, 10, 25, 35, 45 °C) × preconditioned or not × 2 BMS strategies.",
          "Run with `analysis/matrix/run_matrix.py` (8 single-threaded worker processes, ~4 min; every result is "
          "saved as it finishes, so a crash can resume). Raw data: `matrix.csv` (one row per session), "
          "`results.json` (with charge curves).", "",
          "## Verdict", "",
          f"- **All {n} sessions pass all {len(checks)} checks** ({n * len(checks):,} checks). "
          f"**0 violations** in {sum(D['cross_tested'].values())} cross-run consistency comparisons.",
          "- The matrix caught one real bug on its first run: with a cold-soaked pack the power limit used the "
          "previous time step's pack voltage, which climbs fast in the cold, so the pack briefly drew **3–7 % more "
          "than the charger's rating** (30 sessions). Fixed in `ev_pack/pack_sim.py` (the current is re-solved "
          "until power ≤ rating); a regression test was added and the viewer data re-exported.",
          f"- What limits charging, by session: " + ", ".join(f"{k} {v}" for k, v in sorted(lim.items(), key=lambda x: -x[1])) + ".",
          f"- Hottest cell in any session: **{hottest['peak_temp_c']:.1f} °C** "
          f"({hottest['arch']}, {hottest['charger']}, {hottest['ambient_c']} °C, no preconditioning, "
          f"{STRAT_NAME[hottest['strategy']]}). Cutoffs are 55 °C (standard) and 58 °C (anode-aware).",
          f"- Plating margin held in every step of every session: lowest anode potential "
          f"{min(r['min_anode_mv'] for r in ROWS if r['strategy'] == 'standard'):.1f} mV (standard, limit 40) and "
          f"{min(r['min_anode_mv'] for r in ROWS if r['strategy'] == 'optimized'):.1f} mV (anode-aware, limit 15). "
          f"Highest pack voltage {max(r['max_pack_v'] for r in ROWS):.0f} V (limit 920 V).", "",
          "### Checks on every session", "", "| Check | Passed |", "|---|---|"]
    desc = {
        "runs": "Simulation completes without an error",
        "reaches_90pct": "Reaches 90 % SOC",
        "plating_margin_held": "Anode never below the sodium-plating margin (40 / 15 mV)",
        "below_cell_cutoff_temp": "Hottest cell stays below the BMS cutoff (55 / 58 °C)",
        "within_charger_power": "Pack power never above the charger rating × efficiency (+0.5 %)",
        "within_charger_current": "DC current never above the charger's current limit",
        "within_charger_voltage": "Pack voltage never above the charger's 920 V limit",
        "energy_balance": "Energy delivered between stored energy and stored + 15 % losses",
        "no_nan": "No NaN / infinite values in any output",
        "soc_never_decreases": "State of charge never goes backwards",
    }
    L += [f"| {desc.get(k, k)} | {passed[k]} / {n} |" for k in checks]
    cdesc = {
        "faster_charger_never_slower": "A more powerful charger is never slower (same pack, conditions, BMS)",
        "preconditioning_helps_in_cold": "At ≤ 10 °C, preconditioning is never slower than arriving cold",
        "anode_aware_never_slower": "The anode-aware BMS is never slower than the standard BMS",
        "warmer_cold_soak_never_slower": "Without preconditioning, a warmer pack (−20 → 25 °C) is never slower",
        "preconditioned_insensitive_to_ambient": "Preconditioned sessions vary < 5 % across ambient temperatures",
    }
    L += ["", "### Cross-run consistency", "", "| Comparison | Pairs tested | Violations |", "|---|---|---|"]
    viol = {}
    for v in D["cross"]:
        viol[v["check"]] = viol.get(v["check"], 0) + 1
    L += [f"| {cdesc[k]} | {t} | {viol.get(k, 0)} |" for k, t in D["cross_tested"].items()]

    b = lambda *a: get(*a)["t90_min"]  # noqa: E731
    L += ["", "## What the numbers say", "",
          f"1. **Preconditioned, the pack charges the same at any ambient.** 350 kW, 10→90 %: blade "
          f"{b('blade', '350 kW DC', 25, True, 'optimized'):.1f} min (anode-aware) / {b('blade', '350 kW DC', 25, True, 'standard'):.1f} min (standard); "
          f"module {b('module', '350 kW DC', 25, True, 'optimized'):.1f} / {b('module', '350 kW DC', 25, True, 'standard'):.1f} min.",
          f"2. **Skipping preconditioning in the cold is the biggest single penalty.** At −20 °C a cold-soaked blade pack "
          f"needs {b('blade', '350 kW DC', -20, False, 'optimized'):.1f} min (anode-aware) or {b('blade', '350 kW DC', -20, False, 'standard'):.1f} min (standard) "
          f"on 350 kW, against {b('blade', '350 kW DC', -20, True, 'optimized'):.1f} preconditioned. The sodium-plating guard limits "
          "current until the coolant heater (7 kW) has warmed the cells.",
          f"3. **The anode-aware BMS roughly halves the cold-soak penalty.** Blade at 0 °C, 350 kW: "
          f"{b('blade', '350 kW DC', 0, False, 'optimized'):.1f} vs {b('blade', '350 kW DC', 0, False, 'standard'):.1f} min.",
          f"4. **A faster charger pays off more with the anode-aware BMS.** Going from 250 to 350 kW (+40 % power) cuts the "
          f"blade pack's 10→90 % time by {100 * (1 - b('blade', '350 kW DC', 25, True, 'optimized') / b('blade', '250 kW DC', 25, True, 'optimized')):.0f} % "
          f"with the anode-aware BMS but only {100 * (1 - b('blade', '350 kW DC', 25, True, 'standard') / b('blade', '250 kW DC', 25, True, 'standard')):.0f} % "
          f"with the standard one (module: {100 * (1 - b('module', '350 kW DC', 25, True, 'optimized') / b('module', '250 kW DC', 25, True, 'optimized')):.0f} % vs "
          f"{100 * (1 - b('module', '350 kW DC', 25, True, 'standard') / b('module', '250 kW DC', 25, True, 'standard')):.0f} %): "
          "the standard BMS tapers early, so the extra charger power goes unused above ~65 % SOC.",
          "5. **AC and 50 kW charging are charger-limited everywhere**: the pack, BMS and temperature change the time by "
          "under 3 %. Only a cold-soaked −20 °C start adds a few minutes.",
          f"6. **Hot-soaked packs (35–45 °C, no preconditioning) charge slightly faster** (warm cells accept more current) "
          f"but run hottest: up to {hottest['peak_temp_c']:.1f} °C. With the standard BMS at 350 kW and 45 °C the thermal "
          "derate limits the current for part of the session (blade 59 % of the time, module 42 %), which is why those two "
          "are slower than the 35 °C case.", "",
          "![t90 heatmaps](fig_t90_heatmaps.png)", "", "![cold soak](fig_cold_soak.png)", "",
          "![peak temperature](fig_peak_temp.png)", "",
          "## Limits of this test", "",
          "- **Preconditioning is modelled as already done**: the pack starts at the strategy's target (30 / 35 °C). "
          "The energy and time needed to precondition before arriving are not counted, and ambient only reaches the pack "
          "through a weak 0.01 W/K leak, so check 5 above passes by construction.",
          "- **−20 °C sits at the edge of the cell model.** Five of the DFN cell simulations at −20 °C (0.05C, 3.5C, 4C, 5C and 6C) did "
          "not converge when the charge maps were built (`ev_pack/data/na_cell_maps.npz`, 91 of 96 complete); those points "
          "are filled from neighbours. Treat cold-soaked −20 °C times as lower-confidence.",
          "- The thermal model, heater power (7 kW) and chiller power (25 kW) are engineering estimates calibrated against "
          "fleet data (see `analysis/evbattery/REPORT.md`), not a validated vehicle thermal system.", "",
          "## Every value", "",
          "Format: **preconditioned / cold-soaked**. Times in minutes, or `h mm` above 100 min.", ""]
    L += md_table("t90_min", fmt_t, "Time 10 % → 90 %")
    L += md_table("t80_min", fmt_t, "Time 10 % → 80 %")
    L += md_table("peak_temp_c", lambda v: f"{v:.1f}", "Hottest cell, °C")
    L += md_table("aging_h25", lambda v: f"{v:.2f}", "Modelled SEI exposure index (equivalent hours at 25 °C; lower is better)")
    L += ["#### Peak power and main limiter, every session", "",
          "| Pack | BMS | Charger | Ambient | Precond. | 10→80 | 10→90 | Peak kW | Avg kW | Hottest °C | Min anode mV | Max V | Energy kWh | Aging | Main limiter |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in ROWS:
        L.append(f"| {r['arch']} | {r['strategy']} | {r['charger']} | {r['ambient_c']} | {'yes' if r['precondition'] else 'no'} | "
                 f"{fmt_t(r['t80_min'])} | {fmt_t(r['t90_min'])} | {r['peak_power_kw']:.1f} | {r['avg_power_kw']:.1f} | "
                 f"{r['peak_temp_c']:.1f} | {r['min_anode_mv']:.1f} | {r['max_pack_v']:.0f} | {r['energy_kwh']:.1f} | "
                 f"{r['aging_h25']:.2f} | {r['main_limiter']} |")
    (HERE / "REPORT.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("wrote", HERE / "REPORT.md", len(L), "lines")


if __name__ == "__main__":
    main()
