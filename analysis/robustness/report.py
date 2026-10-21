"""
REPORT.md and figures from results.json (run_robustness.py) and dfn_recheck.json (dfn_recheck.py).

Run:  .venv/Scripts/python analysis/robustness/report.py
"""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
D = json.loads((HERE / "results.json").read_text(encoding="utf-8"))
ROWS = D["rows"]
DFN = json.loads((HERE / "dfn_recheck.json").read_text(encoding="utf-8"))
MATRIX = json.loads((ROOT / "analysis/matrix/results.json").read_text())["rows"]
P1 = [r for r in ROWS if r["phase"] == 1]
P2 = [r for r in ROWS if r["phase"] == 2]
P3 = [r for r in ROWS if r["phase"] == 3]
# the combinations are assumptions, not measured error distributions
DISP = {"combined realistic": "combined (assumed moderate)", "combined harsh": "combined (assumed harsh)"}


def nm(sc):
    return DISP.get(sc, sc)


def std_err(sc, c):
    """Standard BMS (40 mV, its own settings) facing the same information errors."""
    return next(r for r in P3 if r["scenario"] == sc and (r["arch"], r["charger"], r["ambient_c"]) == c)
CASES = sorted({(r["arch"], r["charger"], r["ambient_c"]) for r in ROWS}, key=lambda c: (c[0], c[1], -c[2]))
MARGINS = [15, 20, 25, 30, 40]
SCENS = ["ideal", "anode bias +5 mV", "anode bias +10 mV", "anode bias +15 mV", "anode noise 3 mV", "anode noise 6 mV",
         "temp sensor +3 °C", "temp sensor −3 °C", "SOC estimate −3 %", "SOC estimate +3 %", "resistance +20 %",
         "delay 2 steps (4 s)", "combined realistic", "combined harsh"]
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]  # reference palette
INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"


def g(sc, mg, c):
    return next(r for r in P1 if r["scenario"] == sc and r["margin_mv"] == mg and (r["arch"], r["charger"], r["ambient_c"]) == c)


def fb(sc, c):
    return next(r for r in P2 if r["scenario"] == sc and (r["arch"], r["charger"], r["ambient_c"]) == c)


def std_ref(c):
    return next(x for x in MATRIX if (x["arch"], x["charger"], x["ambient_c"]) == c and x["strategy"] == "standard"
                and x["precondition"] == (c[2] == 25))["t90_min"]


def label(c):
    return f"{'Blade' if c[0] == 'blade' else 'Module'} · {c[1].replace(' DC', '')} · {'25 °C precond.' if c[2] == 25 else '0 °C cold-soaked'}"


def worst(sc, mg):
    return min(g(sc, mg, c)["true_min_anode_mv"] for c in CASES)


def required(sc, floor):
    return next((mg for mg in MARGINS if worst(sc, mg) >= floor - 0.05), None)


def fm(v):
    return "did not reach 90 %" if v is None else f"{v:.1f}"


def figures():
    plt.rcParams.update({"font.size": 9.5, "axes.edgecolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
                         "figure.facecolor": SURF, "axes.facecolor": SURF, "axes.spines.top": False,
                         "axes.spines.right": False, "axes.grid": True, "grid.color": GRID})
    # 1) worst true anode vs controller margin
    fig, ax = plt.subplots(figsize=(8.6, 5))
    show = ["ideal", "anode bias +10 mV", "temp sensor +3 °C", "SOC estimate −3 %", "resistance +20 %",
            "anode noise 6 mV", "combined realistic", "combined harsh"]
    for k, sc in enumerate(show):
        ax.plot(MARGINS, [worst(sc, m) for m in MARGINS], "-o", color=SERIES[k], lw=2, ms=6, label=nm(sc))
    ax.axhline(15, color=INK2, lw=1, ls=":")
    ax.axhline(0, color="#e34948", lw=1)
    ax.text(40.6, 15, "design\nmargin\n15 mV", fontsize=8, color=INK2, va="center")
    ax.text(40.6, 0, "0 mV\n(plating-\nrisk\ncriterion)", fontsize=8, color="#e34948", va="center")
    ax.set_xlim(14, 43.8)
    ax.set(xlabel="plating margin the controller enforces on its ESTIMATE, mV",
           ylabel="lowest TRUE anode potential, worst of 8 cases, mV", xticks=MARGINS)
    ax.legend(fontsize=8, frameon=False, ncol=2, loc="upper left")
    ax.set_title("What the cells actually see when the controller's information is wrong", loc="left", fontsize=11)
    fig.tight_layout()
    fig.savefig(HERE / "fig_true_anode_vs_margin.png", dpi=150)
    plt.close(fig)

    # 2) charging time with EQUAL information: both controllers face the same assumed moderate errors
    fig, ax = plt.subplots(figsize=(10, 4.6))
    w = 0.26
    xs = range(len(CASES))
    sc = "combined realistic"
    bars = [("anode-aware, perfect information, 15 mV (reference)", [g("ideal", 15, c)["t90_min"] for c in CASES], SERIES[0]),
            ("standard BMS, assumed moderate errors, 40 mV", [std_err(sc, c)["t90_min"] for c in CASES], "#8a8984"),
            ("anode-aware, assumed moderate errors, 40 mV", [g(sc, 40, c)["t90_min"] for c in CASES], SERIES[1])]
    for k, (lab, vals, col) in enumerate(bars):
        ax.bar([x + (k - 1) * w for x in xs], vals, width=w - 0.02, color=col, label=lab)
    short = [f"{'Blade' if c[0] == 'blade' else 'Module'}\n{c[1].replace(' DC', '')}\n{'25 °C pre.' if c[2] == 25 else '0 °C cold'}"
             for c in CASES]
    ax.set_xticks(list(xs), short, fontsize=8.5)
    ax.set_ylabel("minutes, 10 → 90 %")
    ax.legend(fontsize=8, frameon=False, loc="lower left", bbox_to_anchor=(0, 1.0), ncol=1)
    ax.set_title("Same information errors, same 40 mV margin: the anode-aware settings stay 1–2 min faster",
                 loc="left", fontsize=11, pad=50)
    fig.tight_layout()
    fig.savefig(HERE / "fig_time_cost.png", dpi=150)
    plt.close(fig)

    # 3) trade-off: charging time vs lowest true anode, one point per controller margin
    picks = [("blade", "350 kW DC", 25), ("module", "350 kW DC", 0)]
    trade = ["ideal", "SOC estimate −3 %", "combined realistic", "combined harsh"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.6), sharey=True)
    for ax, c in zip(axes, picks):
        for k, sc in enumerate(trade):
            pts = [(g(sc, m, c)["t90_min"], g(sc, m, c)["true_min_anode_mv"], m) for m in MARGINS]
            pts = [p for p in pts if p[0] is not None]
            ax.plot([p[0] for p in pts], [p[1] for p in pts], "-o", color=SERIES[k], lw=2, ms=6, label=nm(sc))
            for x, y, m in pts:
                ax.annotate(f"{m}", (x, y), textcoords="offset points", xytext=(5, -10), fontsize=7.5, color=INK2)
        ax.axhline(15, color=INK2, lw=1, ls=":")
        ax.axhline(0, color="#e34948", lw=1)
        ax.axvline(std_ref(c), color="#8a8984", lw=1.2, ls="--")
        ax.text(std_ref(c), ax.get_ylim()[1] if False else 44, " standard BMS", fontsize=8, color=INK2, va="top")
        ax.set_title(label(c), loc="left", fontsize=10.5)
        ax.set_xlabel("minutes, 10 → 90 %")
    axes[0].set_ylabel("lowest TRUE anode potential, mV")
    axes[0].legend(fontsize=8, frameon=False, loc="lower right")
    fig.suptitle("Trade-off: faster charging costs true margin (numbers = controller margin in mV)",
                 x=0.01, ha="left", fontsize=11)
    fig.tight_layout()
    fig.savefig(HERE / "fig_tradeoff.png", dpi=150)
    plt.close(fig)


def main():
    figures()
    eq = D["fallback_equivalents"]
    fbm = D["fallback_margins"]
    mod, harsh = "combined realistic", "combined harsh"
    f1 = lambda v: "not reached" if v is None else f"{v:.1f}"  # noqa: E731
    L = ["# Imperfect-knowledge stress test: how much of the ideal-information advantage survives?", "",
         "**Question.** How does charging performance change when the BMS cannot see the true anode potential, and how much "
         "of the simulated anode-aware advantage survives under a declared protection target (true modelled anode ≥ 15 mV)?", "",
         "**Status: simulation stress test.** The errors are *injected* with assumed sizes; no estimator has been built. "
         "The combinations are labelled *assumed moderate* and *assumed harsh*: their sizes and joint occurrence are not "
         "supported by measurements or specifications, so the results show sensitivity to those assumptions, not the error "
         "distribution of a real BMS. 15.3 min (blade, 350 kW, 10 → 90 %) is the *ideal-information simulation reference*.", "",
         "**Two different thresholds.** Falling below **15 mV** violates the selected safety buffer (a *margin violation*). "
         "Crossing **0 mV** crosses the model's *plating-risk criterion*. Neither is evidence that sodium actually deposited; "
         "that would need a deposition model or an experiment.", "",
         "## Set-up: three roles (`ev_pack.pack_sim.InfoErrors`)", "",
         "- **Controller** sees only declared measurements and estimates: temperature readings (with bias), an SOC estimate, "
         "and an anode-potential estimate from its own nominal map model plus bias and noise, optionally delayed. It runs the "
         "same limits and bisection as the ideal BMS **on those estimates only**. The true-anode clamp is disabled, and its "
         "plating table shares the estimate's bias.",
         "- **Plant** evolves on its true state. A resistance mismatch makes real cells polarise more than the controller's "
         "model (true anode = OCP − (1 + r)·(OCP − model anode)), with heat to match.",
         "- **Evaluator** reads the true state: lowest true anode potential, time and depth below 15 mV and below 0 mV, cell "
         "over-voltage, target completion, and the **dangerous error direction** (estimate higher, i.e. safer, than truth).", "",
         "Defaults are unchanged (tests: zero error reproduces the ideal controller exactly; the 336-session matrix values "
         "reproduce).", "",
         f"**Cases:** blade and module × 250 and 350 kW × 25 °C preconditioned and 0 °C cold-soaked (8). **Scenarios:** "
         f"{len(SCENS)}. **Anode-aware controller margins:** {', '.join(map(str, MARGINS))} mV. Runs: {len(P1)} sweep + "
         f"{len(P2)} fallback + {len(P3)} standard-BMS runs facing the same errors, one seeded simulation each "
         "(8 single-threaded workers).", "",
         "| Scenario | Error (assumed) |", "|---|---|",
         "| anode bias | estimate reads +5 / +10 / +15 mV too high (optimistic) |",
         "| anode noise | 3 / 6 mV (1 σ), independent per cell and 2 s step |",
         "| temp sensor | readings +3 °C (optimistic) / −3 °C |",
         "| SOC estimate | −3 % (thinks emptier) / +3 % |",
         "| resistance | real cells polarise 20 % more than the model |",
         "| delay | controller acts on a state 2 steps (4 s) old |",
         "| combined (assumed moderate) | +5 mV bias, 3 mV noise, +2 °C, −2 % SOC, +10 % resistance, 1-step delay |",
         "| combined (assumed harsh) | +10 mV bias, 5 mV noise, +3 °C, −3 % SOC, +20 % resistance, 2-step delay |", "",
         "## Summary: the answer depends on the operating condition", "",
         "| Operating condition | What the results show |", "|---|---|",
         "| Warm and charger-limited (25 °C preconditioned) | The tested errors have limited effect: the plating guard rarely "
         "controls the current, so information quality hardly matters. |",
         "| Cold-soaked and plating-limited (0 °C) | Estimation errors directly reduce the true modelled margin; conservative "
         "compensation removes much of the advantage that came from the smaller margin. |",
         "| Single assumed errors | A 20–30 mV controller margin protects the 15 mV target, and the anode-aware controller "
         "stays several minutes faster than the standard BMS facing the same error. |",
         "| Assumed moderate combination | Same errors, same 40 mV setting: the anode-aware configuration is still 1–2 min "
         "faster, but both end slightly below the 15 mV target, so this is a same-setting comparison, not a "
         "protection-matched one. The difference is plausibly from its temperature settings (not tested under errors). |",
         "| Assumed harsh combination | Neither controller holds the 15 mV target at 40 mV; the fallback margin protects it "
         "but the charge stalls before 80 % (constraint-respecting, task-incomplete). |",
         "| Changing-current profiles | The reduced cell model is optimistic after sharp current changes; that needs a "
         "separate, profile-dependent allowance (see below). |", "",
         "## Margin each error needs (anode-aware controller, worst of the 8 cases)", "",
         "![true anode vs margin](fig_true_anode_vs_margin.png)", "",
         "| Scenario | Lowest true anode at a 15 mV controller margin | Margin to keep true ≥ 15 mV | Margin to stay ≥ 0 mV | Dangerous error near the limit (max, and how long) |",
         "|---|---|---|---|---|"]
    for sc in SCENS:
        r15 = [g(sc, 15, c) for c in CASES]
        k15, k0 = required(sc, 15.0), required(sc, 0.0)
        dmax = max(r["danger_err_near_limit_mv"] for r in r15)
        dlen = max(r["danger_err_near_limit_s"] for r in r15)
        L.append(f"| {nm(sc)} | {worst(sc, 15):.1f} mV | {k15 if k15 else '> 40'} mV | {k0 if k0 else '> 40'} mV | "
                 f"{max(dmax, 0):.1f} mV for up to {dlen / 60:.0f} min |")
    L += ["", "Grid margins only (15, 20, 25, 30, 40 mV); the true requirement can lie between grid points.", "",
          "- **Single errors need a 20–30 mV margin** to keep the true anode at 15 mV. At a 15 mV margin none crosses 0 mV, "
          "though a +15 mV bias and a −3 % SOC error reach it. Optimistic anode bias passes straight through; a +3 °C sensor "
          "bias costs about 10 mV, a −3 % SOC error about 15 mV, 20 % extra resistance about 11 mV. Noise and a 4 s delay "
          "matter less (the BMS acts on the lowest of hundreds of noisy cell estimates).",
          f"- **Errors combine roughly additively.** The assumed moderate set needs about 41 mV to keep 15 mV (worst true anode "
          f"{worst(mod, 40):.1f} mV at 40 mV) and crosses 0 mV below 30 mV ({worst(mod, 15):.1f} mV at 15 mV). The assumed "
          f"harsh set crosses 0 mV even at 40 mV ({worst(harsh, 40):.1f} mV).",
          "- **The dangerous direction persists:** biased sensors or estimates leave the controller believing the anode is "
          "5–21 mV safer than it is for 15–17 minutes, the stretch of the session near the limit.",
          "- **SOC errors in either direction are risky:** even an SOC estimate 3 % too high costs about 6 mV, because at a "
          "fixed current the anode potential is not monotonic in SOC.", "",
          "## Equal information: standard BMS vs anode-aware BMS facing the same errors", "",
          "Configured margins are not equivalent protections, so both controllers are compared on what the evaluator "
          "measures: true margin, time below 15 mV and 0 mV, completion and charging time. The standard BMS keeps its own "
          "settings (40 mV, 30 °C, 45 / 55 °C, 4C).", "",
          "![equal information](fig_time_cost.png)", "",
          "| Scenario | Controller | Lowest true anode (worst case) | Time below 15 mV (max) | Time below 0 mV (max) | Reached 90 % | 10 → 90 % range (min) |",
          "|---|---|---|---|---|---|---|"]

    def summ(rows):
        ts = [r["t90_min"] for r in rows if r["t90_min"] is not None]
        return (min(r["true_min_anode_mv"] for r in rows), max(r["below_15mv_s"] for r in rows),
                max(r["plating_s"] for r in rows), f"{len(ts)} / {len(rows)}",
                f"{min(ts):.1f}–{max(ts):.1f}" if ts else "—")

    for sc in ["ideal", "temp sensor +3 °C", "SOC estimate −3 %", "resistance +20 %", mod, harsh]:
        groups = [("standard BMS, 40 mV", [std_err(sc, c) for c in CASES]),
                  ("anode-aware, 40 mV", [g(sc, 40, c) for c in CASES])]
        if sc != "ideal":
            groups.append((f"anode-aware, fallback {fb(sc, CASES[0])['margin_mv']:.0f} mV", [fb(sc, c) for c in CASES]))
        else:
            groups.append(("anode-aware, 15 mV", [g(sc, 15, c) for c in CASES]))
        for k, (ctl, rows) in enumerate(groups):
            tmin, b15, b0, done, tr = summ(rows)
            L.append(f"| {nm(sc) if k == 0 else ''} | {ctl} | {tmin:.1f} mV | {b15:.0f} s | {b0:.0f} s | {done} | {tr} |")
    eq_rows = [(c, std_err(mod, c), g(mod, 40, c)) for c in CASES]
    gain = [s_["t90_min"] - a_["t90_min"] for c, s_, a_ in eq_rows]
    L += ["", f"- **Assumed moderate errors, equal 40 mV margin:** the anode-aware configuration is {min(gain):.1f}–"
          f"{max(gain):.1f} min faster than the standard BMS in every case, at the same setting and facing the same errors. "
          "**This is a same-setting comparison, not a protection-matched one:** both controllers end slightly below the 15 mV "
          f"target (worst {min(a_['true_min_anode_mv'] for c, s_, a_ in eq_rows):.1f} vs "
          f"{min(s_['true_min_anode_mv'] for c, s_, a_ in eq_rows):.1f} mV), so it is not evidence of faster charging while "
          "meeting the target. A protection-matched comparison would choose each controller's margin on calibration cases "
          "and evaluate both on held-out cases against the same true-state criterion. The remaining difference is "
          "*plausibly* from the warmer temperature settings (the perfect-information ablation attributes 5–19 % of the gap "
          "to them), but that attribution was not re-tested under injected errors; a matched temperature-setting toggle "
          "under the same errors would be needed to confirm it.",
          "- **The standard BMS is not automatically protected either.** Facing the same errors it ends at "
          f"{min(r['true_min_anode_mv'] for r in [std_err(mod, c) for c in CASES]):.1f}–"
          f"{max(r['true_min_anode_mv'] for r in [std_err(mod, c) for c in CASES]):.1f} mV true (moderate) and crosses 0 mV "
          f"under the harsh set (worst {min(r['true_min_anode_mv'] for r in [std_err(harsh, c) for c in CASES]):.1f} mV). It also "
          "finishes faster than with perfect information, because the errors make it optimistic too.",
          "- **Single errors:** the anode-aware controller with its fallback margin stays several minutes faster than the "
          "standard BMS facing the same error, with the 15 mV target held (within 0.1 mV). Most of the advantage survives single errors of "
          "these sizes.", "",
          "## The fallback rule (derived on single errors, tested on held-out combinations)", "",
          "Each single error's *equivalent anode error* is how far it pushes the true anode below 15 mV at a 15 mV margin "
          "(worst case): anode-estimate bias "
          f"{eq['anode_bias_mv']['equiv_mv_per_unit']:.1f} mV per mV, anode noise {eq['anode_noise_mv']['equiv_mv_per_unit']:.1f} mV per mV (1 σ), "
          f"temperature-sensor bias {eq['temp_bias_c']['equiv_mv_per_unit']:.1f} mV per °C, SOC error {eq['soc_bias']['equiv_mv_per_unit'] / 100:.1f} mV per %, "
          f"resistance mismatch {eq['r_mismatch']['equiv_mv_per_unit'] / 10:.1f} mV per 10 %, delay {eq['delay_steps']['equiv_mv_per_unit']:.1f} mV per 2 s step. "
          "**Rule: margin = 15 mV + the sum of the declared errors' equivalents** "
          f"({fbm[mod]:.0f} mV for the assumed moderate set, {fbm[harsh]:.0f} mV for the assumed harsh set).", "",
          "| Scenario | Fallback margin | Lowest true anode (8 cases) | Outcome |", "|---|---|---|---|"]
    for sc in SCENS[1:]:
        rows = [fb(sc, c) for c in CASES]
        ts = [r["t90_min"] for r in rows if r["t90_min"] is not None]
        if len(ts) == len(rows):
            out = f"target held, 90 % reached ({min(ts):.1f}–{max(ts):.1f} min)"
        elif not ts:
            out = "**constraint-respecting, task-incomplete**: 90 % not reached in any case"
        else:
            out = f"90 % not reached in {len(rows) - len(ts)} of 8 cases"
        L.append(f"| {nm(sc)} | {rows[0]['margin_mv']:.1f} mV | {min(r['true_min_anode_mv'] for r in rows):.1f} mV | {out} |")
    L += ["", "The rule was **empirically successful on the tested combinations**: the true anode stayed at or above 15 mV "
          "(within 0.1 mV) in every scenario, including the two held-out combinations. That is not a general safety "
          "guarantee: it holds for these error types, sizes and cases. Under the assumed harsh set the 66 mV margin keeps "
          "the target but the charge stalls before 80 % in all 8 cases, which counts as constraint-respecting but "
          "task-incomplete, not a pass.", "",
          "## Trade-off: charging time vs true margin", "",
          "![trade-off](fig_tradeoff.png)", "",
          "Each line walks the controller margin from 15 to 40 mV (numbers on the points). **Warm and charger-limited "
          "(blade, 25 °C preconditioned):** the plating guard rarely binds, so even the assumed moderate errors leave the true "
          "anode at 15.7–21 mV at every margin and the harsh set dips to about 13 mV. **Cold-soaked (module, 0 °C):** the guard "
          "binds for most of the session and every error turns into lost true margin. The worst-case margins above are set "
          "by the cold, plating-limited cases.", "",
          "Time cost of the margin itself, with perfect information (minutes, 10 → 90 %):", "",
          "| Case | " + " | ".join(f"{m} mV" for m in MARGINS) + " | Standard BMS (perfect information) |",
          "|---|" + "---|" * (len(MARGINS) + 1)]
    for c in CASES:
        L.append(f"| {label(c)} | " + " | ".join(f"{g('ideal', m, c)['t90_min']:.1f}" for m in MARGINS) + f" | {std_ref(c):.1f} |")
    L += ["", "## The reduced cell model's own error: two different measures", "",
          "The plant here is the pack simulator's reduced (lookup-map) cell model. Its error relative to the full DFN must be "
          "reported two ways, because they answer different questions:", "",
          "- **Pointwise optimistic error** (the map says the anode is safer than the DFN at that moment): 0.5–1.5 mV for "
          "smooth anode-aware tapers, up to 5.5 mV for standard tapers (model card); 4–10 mV around current jumps caused by "
          "noisy estimates (replays below); up to 50–60 mV after sharp cuts or pulses (model card).",
          "- **Difference between session minima** (map minimum vs DFN minimum): at most 1.4 mV in the replays below, and in "
          "6 of 7 the DFN minimum was higher. Brief optimistic errors can occur away from the session minimum, so this "
          "number alone cannot justify a universal allowance.", "",
          "So the allowance is **profile-dependent**: about 1.5 mV is reasonable for the smooth tapers of the existing "
          "controllers; a controller with jumpy current needs 4–10 mV more or a limit on how fast the current may change; "
          "controllers that cut, pause or pulse must be evaluated with the full DFN.", "",
          "| Replayed run | Max C jump | Replay temp | Min anode, map / DFN (mV) | Pointwise map optimism near the limit (max) |",
          "|---|---|---|---|---|"]
    for r in DFN:
        if r.get("dfn_failed"):
            continue
        L.append(f"| {nm(r['scenario'])}, {r['margin_mv']} mV, {r['arch']} {r['charger'].replace(' DC', '')} {r['ambient_c']} °C | "
                 f"{r['max_c_jump']:.2f}C | {r['replay_temp_c']} °C | {r['map_min_mv']:.1f} / {r['dfn_min_mv']:.1f} | "
                 f"{max(r['map_optimism_near_limit_mv'], 0):.1f} mV |")
    L += ["", "Replays use the average cell, isothermally at the session's mean temperature.", "",
          "## What this does and doesn't show", "",
          "- **Shows:** a controller–plant–evaluator benchmark that quantifies how estimation uncertainty changes charging "
          "performance across charger-limited and plating-limited conditions. Under the assumed errors, the part of the "
          "advantage that comes from a smaller plating margin depends on estimator quality; a 1–2 min same-setting "
          "difference remains, plausibly from the temperature settings.",
          "- **Doesn't show:** what errors a real estimator achieves (none was built), real-cell behaviour (the plant is the "
          "same reduced model; internal-potential validation needs instrumented cells with micro-reference electrodes), "
          "actual sodium deposition, or run-to-run variability (one seeded run per case).",
          "- **Assumptions to watch:** error sizes and combinations are assumed; noise is independent per cell; the "
          "controller's model and the plant share the same maps except for the resistance mismatch.", "",
          "## Next bounded experiment", "",
          "Can a **measurement-only estimator** recover charging performance in cold, plating-limited conditions while "
          "keeping the declared modelled protection target? Start with one cell and a limited operating range; compare a "
          "simple model-based observer with a small history-based ML estimator using only voltage, current, temperature "
          "and declared initialisation information. Judge them on optimistic-error tails, persistent bias, true-state "
          "violations, target completion and charging time on held-out sessions and parameter cases, not just average "
          "error.", "",
          "Reproduce:", "```bash", ".venv/Scripts/python analysis/robustness/run_robustness.py --workers 8   # ~9 min, resumable",
          ".venv/Scripts/python analysis/robustness/dfn_recheck.py", ".venv/Scripts/python analysis/robustness/report.py", "```"]
    (HERE / "REPORT.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("equal-information gain (min):", round(min(gain), 2), round(max(gain), 2))


if __name__ == "__main__":
    main()
