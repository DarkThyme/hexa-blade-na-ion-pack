"""
Shapley attribution of the standard -> anode-aware speed-up, from the 2^4 factorial in results.json.

Run:  .venv/Scripts/python analysis/ablation/report.py
"""

import itertools
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
ROWS = json.loads((HERE / "results.json").read_text())
G = ["margin", "precond", "thermal", "crate"]
LABEL = {"margin": "Plating margin 40 → 15 mV", "precond": "Precondition / coolant 30/30 → 35/33 °C",
         "thermal": "Thermal derate/cutoff 45/55 → 50/58 °C", "crate": "Max C-rate 4C → 5C"}
COL = {"margin": "#2a78d6", "precond": "#eb6834", "thermal": "#1baf7a", "crate": "#6250d6"}  # reference palette, fixed order
INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
CASES = sorted({(r["arch"], r["charger"], r["ambient_c"], r["precondition"]) for r in ROWS},
               key=lambda c: (c[0], c[1], -c[2]))


def v(case, S):
    name = "+".join(g for g in G if g in S) or "standard"
    return next(r for r in ROWS if (r["arch"], r["charger"], r["ambient_c"], r["precondition"]) == case and r["config"] == name)


def shapley(case):
    out = {}
    for i in G:
        others = [g for g in G if g != i]
        tot = 0.0
        for k in range(len(G)):
            for S in itertools.combinations(others, k):
                w = math.factorial(k) * math.factorial(len(G) - k - 1) / math.factorial(len(G))
                tot += w * (v(case, set(S))["t90_min"] - v(case, set(S) | {i})["t90_min"])
        out[i] = tot
    return out


def label(case):
    arch, ch, amb, pre = case
    soak = "precond." if pre else ("hot-soaked" if amb > 25 else "cold-soaked")
    return f"{'Blade' if arch == 'blade' else 'Module'} · {ch.replace(' DC', '')} · {amb} °C {soak}"


def main():
    res = []
    for c in CASES:
        base, full = v(c, set())["t90_min"], v(c, set(G))["t90_min"]
        res.append((c, base, full, shapley(c)))
    # ---- figure: stacked attribution of the gap (minutes saved), one bar per case
    plt.rcParams.update({"font.size": 10, "axes.edgecolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
                         "figure.facecolor": SURF, "axes.facecolor": SURF, "axes.spines.top": False,
                         "axes.spines.right": False})
    fig, ax = plt.subplots(figsize=(11, 7.6))
    y = list(range(len(res)))[::-1]
    for yi, (c, base, full, sh) in zip(y, res):
        left = 0.0
        for g in G:
            w = sh[g]
            if w > 0.05:
                ax.barh(yi, w, left=left, color=COL[g], height=0.62, edgecolor=SURF, linewidth=2)
            left += max(w, 0)
        ax.text(left + 0.4, yi, f"{base:.1f} → {full:.1f} min", va="center", fontsize=9, color=INK)
    ax.set_yticks(y, [label(c) for c, *_ in res], fontsize=9)
    ax.set_xlabel("minutes saved from 10 → 90 %, split by setting (Shapley attribution)", color=INK)
    ax.grid(axis="x", color=GRID)
    ax.set_axisbelow(True)
    handles = [plt.Rectangle((0, 0), 1, 1, color=COL[g]) for g in G]
    zero = {g for g in G if all(abs(sh[g]) < 0.05 for *_, sh in res)}
    ax.legend(handles, [LABEL[g] + (" (0 min)" if g in zero else "") for g in G], frameon=False, fontsize=8.5,
              loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2)
    ax.set_xlim(0, max(b - f for _, b, f, _ in res) * 1.25)
    ax.set_title("Where the anode-aware BMS's speed-up comes from", loc="left", color=INK, fontsize=12, pad=46)
    fig.tight_layout()
    fig.savefig(HERE / "fig_attribution.png", dpi=150)
    plt.close(fig)

    # ---- markdown
    frac = lambda sh, base, full, g: (lambda x: 0.0 if abs(x) < 0.005 else x)(sh[g] / (base - full))  # noqa: E731
    hot = [(c, base, full, sh) for c, base, full, sh in res if c[1] == "350 kW DC" and c[2] == 45]
    rest = [(c, base, full, sh) for c, base, full, sh in res if not (c[1] == "350 kW DC" and c[2] == 45)]
    rng = lambda rows, g: (100 * min(frac(sh, b_, f_, g) for c, b_, f_, sh in rows),  # noqa: E731
                           100 * max(frac(sh, b_, f_, g) for c, b_, f_, sh in rows))
    share = [frac(sh, base, full, "margin") for c, base, full, sh in res]
    pre_share = [frac(sh, base, full, "precond") for c, base, full, sh in res]
    m_lo, m_hi = rng(rest, "margin")
    p_lo, p_hi = rng(rest, "precond")
    rest_tc = max(abs(sh["thermal"]) + abs(sh["crate"]) for c, b_, f_, sh in rest)
    hot_txt = "; ".join(
        f"{'blade' if c[0] == 'blade' else 'module'} {base:.1f} → {full:.1f} min: margin {100 * frac(sh, base, full, 'margin'):.0f} %, "
        f"thermal limits {100 * frac(sh, base, full, 'thermal'):.0f} %, C-rate cap {100 * frac(sh, base, full, 'crate'):.0f} %, "
        f"temperature settings {100 * frac(sh, base, full, 'precond'):.0f} %" for c, base, full, sh in hot)
    std_hot = [v(c, set()) for c, *_ in hot]
    full_hot = [v(c, set(G)) for c, *_ in hot]
    L = ["# Ablation: which setting makes the anode-aware BMS faster?", "",
         "The two BMS strategies in `ev_pack/pack_sim.py` differ in four setting groups, so the original comparison "
         "could not say which one causes the speed-up. This study runs the **full 2⁴ factorial**: all 16 combinations, "
         "from the standard BMS (nothing switched) to the anode-aware BMS (all four switched). That covers switching "
         "each setting on from the standard BMS, switching each off from the anode-aware BMS, and every interaction.", "",
         "| Setting group | Standard | Anode-aware |", "|---|---|---|",
         "| Plating margin | 40 mV | 15 mV |", "| Preconditioning target / coolant setpoint | 30 / 30 °C | 35 / 33 °C |",
         "| Thermal derate start / cutoff | 45 / 55 °C | 50 / 58 °C |", "| Max C-rate | 4C | 5C |", "",
         f"Cases: both packs × 250 / 350 kW DC × {{25 °C preconditioned; 0 °C and −20 °C cold-soaked; 35 °C and 45 °C "
         f"hot-soaked}}, 10 → 90 %: {len(res)} cases, {len(ROWS)} runs (`run_ablation.py`, 8 single-threaded workers). "
         f"Every run kept its plating margin and stayed below its cutoff temperature: "
         f"{sum(r['margin_held'] for r in ROWS)}/{len(ROWS)} and {sum(r['below_cutoff'] for r in ROWS)}/{len(ROWS)}. "
         "Soaked packs (no preconditioning) start at the ambient temperature.", "",
         "Attribution uses **Shapley values**: each setting's marginal saving, averaged over every order in which the "
         "settings could be switched on. The four shares add up exactly to the total gap, and interactions are "
         "split between the settings involved. They are allocations for these deterministic runs (one seeded "
         "simulation per case), not confidence intervals.", "",
         "## Result", "",
         f"**In {len(rest)} of the {len(res)} cases** (everything except 350 kW at 45 °C):",
         f"- **The plating margin explains {m_lo:.0f}–{m_hi:.0f} % of the gap.** This is the effect of the anode observer.",
         f"- **The warmer temperature settings explain the rest ({p_lo:.0f}–{p_hi:.0f} %).** Preconditioned cases warm the "
         "pack to 35 °C; in soaked cases no preconditioning happens, so this group acts only through the 33 °C coolant setpoint.",
         f"- **The thermal limits and the C-rate cap contribute nothing** (at most {rest_tc:.2f} min). The 4C cap never "
         f"binds (peak {max(r['peak_c_rate'] for r in ROWS):.2f}C in any run). Crossing the 45 °C derate start is not "
         "the same as being limited by it: at 25 °C preconditioned the pack passes 45 °C only after about 64 % SOC, "
         "when the charger's power limit already holds the current near 2.7C, below the roughly 3.0C the standard "
         "derate still allows at 47 °C. In cold-soaked cases the plating guard holds the current instead.", "",
         "**Hot exception: 350 kW, hot-soaked at 45 °C.** Here the standard BMS spends "
         f"{100 * std_hot[0]['limiter_share'].get('Thermal derate', 0):.0f} % (blade) and "
         f"{100 * std_hot[1]['limiter_share'].get('Thermal derate', 0):.0f} % (module) of the session on its thermal "
         f"derate, and the attribution shifts: {hot_txt}.",
         "- The C-rate cap matters here only through the derate: in the simulator the derated limit is "
         "(derate fraction) × (max C-rate), so a 5C cap raises the derated current too, even though peak current stays "
         "below 4C.",
         f"- The price is heat: the hottest cell reaches {full_hot[0]['peak_temp_c']:.1f} / {full_hot[1]['peak_temp_c']:.1f} °C "
         f"(blade / module) with the relaxed limits against {std_hot[0]['peak_temp_c']:.1f} / {std_hot[1]['peak_temp_c']:.1f} °C. "
         f"The modelled SEI exposure index is still lower ({full_hot[0]['aging_h25']:.2f} vs {std_hot[0]['aging_h25']:.2f} for the blade) "
         "because the session is shorter.",
         "- At 250 kW the same 45 °C start generates less heat and the thermal limits again contribute 0.", "",
         "**The settings interact.** The margin alone saves more when switched on first than it costs when switched "
         "off last (blade, 350 kW, 25 °C: 5.9 vs 4.0 min). Warmer cells and a smaller margin both relax the same "
         "plating limit, so their effects overlap rather than add.", "",
         "![attribution](fig_attribution.png)", "",
         "## Every case", "",
         "| Case | Standard | Anode-aware | Gap | Margin | Temp. settings | Thermal | C-rate | Margin alone (from standard) | Margin removed (from anode-aware) |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for c, base, full, sh in res:
        alone = base - v(c, {"margin"})["t90_min"]
        removed = v(c, set(G) - {"margin"})["t90_min"] - full
        L.append(f"| {label(c)} | {base:.1f} | {full:.1f} | {base - full:.1f} | {sh['margin']:.1f} | {sh['precond']:.1f} | "
                 f"{sh['thermal']:.1f} | {sh['crate']:.1f} | {alone:.1f} | {removed:.1f} |")
    L += ["", "Minutes, 10 → 90 %. The Shapley columns (Margin … C-rate) sum to the gap.", "",
          "## What this means", "",
          f"Across the tested scenarios, the 15 mV margin explains most of the simulated improvement ({m_lo:.0f}–{m_hi:.0f} % "
          "in 18 of 20 cases). Warmer temperature settings explain the rest. In those 18 cases the gain does not need "
          "relaxed thermal limits or a higher current cap. In the one hot, high-power condition tested (350 kW, "
          "45 °C hot-soaked) about 3 minutes of the gain does depend on them, at the cost of a hotter pack. A production "
          "version could keep the standard thermal limits and 4C cap and give up those minutes on hot days. "
          "The open question is unchanged: whether real cells and a production observer can safely hold a 15 mV "
          "margin across aging, observer error and cell spread.", "",
          "Reproduce:", "```bash", ".venv/Scripts/python analysis/ablation/run_ablation.py --workers 8",
          ".venv/Scripts/python analysis/ablation/report.py", "```"]
    (HERE / "REPORT.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    json.dump([{"case": list(c), "standard": base, "anode_aware": full, "shapley": sh} for c, base, full, sh in res],
              open(HERE / "attribution.json", "w"), indent=1)
    print("margin share", round(min(share), 3), round(max(share), 3), "precond", round(min(pre_share), 3), round(max(pre_share), 3))


if __name__ == "__main__":
    main()
