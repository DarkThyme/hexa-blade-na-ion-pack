"""
REPORT.md and figures for the measurement-only estimator study. Reads results only; tunes nothing.

Run:  .venv/Scripts/python analysis/estimator/report.py
"""

import collections
import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import common as C
import freeze

H = C.HERE
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"


def jl(path):
    return [json.loads(x) for x in (H / path).read_text().splitlines()]


def summarise(rows):
    by = collections.OrderedDict()
    for r in rows:
        by.setdefault(r["controller"], []).append(r)
    out = []
    for ctl, rs in by.items():
        done = [r for r in rs if r["completed"]]
        t = sorted(r["t90_min"] for r in done)
        out.append({"controller": ctl, "n": len(rs), "done": len(done),
                    "runs_below15": sum(r["below_15mv_s"] > 0 for r in rs), "runs_below0": sum(r["below_0mv_s"] > 0 for r in rs),
                    "max_below0_min": max(r["below_0mv_s"] for r in rs) / 60, "worst_mv": min(r["true_min_anode_mv"] for r in rs),
                    "opt_p99": max((r["opt_err_p99_near_mv"] or 0) for r in rs),
                    "t90_median": t[len(t) // 2] if t else None,
                    "time_limit": sum(r["status"] == "time limit" for r in rs), "stalled": sum(r["status"] == "stalled" for r in rs),
                    "solver_failed": sum(r["status"].startswith("plant solver") for r in rs)})
    return out


def table(summary):
    L = ["| Controller | Reached 90 % | Runs below 15 mV | Runs below 0 mV | Longest below 0 mV | Worst modelled anode | Optimistic error p99 near limit | Median 10→90 % |",
         "|---|---|---|---|---|---|---|---|"]
    for s in summary:
        notes = []
        if s["time_limit"]:
            notes.append(f"{s['time_limit']} time limit")
        if s["stalled"]:
            notes.append(f"{s['stalled']} stalled")
        if s["solver_failed"]:
            notes.append(f"{s['solver_failed']} plant solver failure")
        done = f"{s['done']}/{s['n']}" + (f" ({', '.join(notes)})" if notes else "")
        med = "—" if s["t90_median"] is None else f"{s['t90_median']:.0f} min"
        L.append(f"| {s['controller']} | {done} | {s['runs_below15']} | {s['runs_below0']} | {s['max_below0_min']:.1f} min | "
                 f"{s['worst_mv']:.1f} mV | {s['opt_p99']:.1f} mV | {med} |")
    return L


def openloop_rows():
    rows = []
    for tag, path in (("v1 (ramp-sampled open-loop data)", "v1/openloop.json"), ("A (step-aligned open-loop data)", "openloop_A.json"),
                      ("B (A + training-cell rollouts)", "openloop_B.json")):
        d = json.loads((H / path).read_text())
        for est in ("map", "observer", "ml"):
            e = d["estimators"][est]
            rows.append((tag, est, e["test"], e.get("calibration", e["train"]), e["calibrated_margin_mv"], d["best_tau"]))
    return rows


def figures(dev, final):
    plt.rcParams.update({"font.size": 9.5, "axes.edgecolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
                         "figure.facecolor": SURF, "axes.facecolor": SURF, "axes.spines.top": False,
                         "axes.spines.right": False, "axes.grid": True, "grid.color": GRID})
    # 1) open-loop vs closed-loop optimistic tail, network versions, development holdout
    ol = {t: json.loads((H / p).read_text())["estimators"]["ml"] for t, p in
          (("v1", "v1/openloop.json"), ("A", "openloop_A.json"), ("B", "openloop_B.json"))}
    cl = {"v1": [s for s in dev["v1"] if s["controller"].startswith("ml, calibrated")][0],
          "A": [s for s in dev["A"] if "calibrated" in s["controller"]][0],
          "B": [s for s in dev["B"] if "calibrated" in s["controller"]][0]}
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
    xs = np.arange(3)
    ax[0].bar(xs - 0.2, [ol[t]["test"]["p99"] for t in ("v1", "A", "B")], 0.38, color=SERIES[0], label="open loop (recorded sessions)")
    ax[0].bar(xs + 0.2, [cl[t]["opt_p99"] for t in ("v1", "A", "B")], 0.38, color=SERIES[1], label="closed loop (own controller)")
    ax[0].set_xticks(xs, ["v1", "A: horizon fix", "B: + rollouts"])
    ax[0].set_ylabel("optimistic error p99 near the limit, mV")
    ax[0].set_title("Network: open-loop accuracy did not predict closed loop", loc="left", fontsize=10.5)
    ax[0].legend(fontsize=8, frameon=False)
    vals = [cl[t]["runs_below0"] for t in ("v1", "A", "B")]
    ax[1].bar(xs, vals, 0.5, color=SERIES[7])
    for x, t, n in zip(xs, ("v1", "A", "B"), vals):
        ax[1].text(x, n + 0.3, f"longest {cl[t]['max_below0_min']:.1f} min", ha="center", fontsize=8.5, color=INK2)
    ax[1].set_xticks(xs, ["v1", "A: horizon fix", "B: + rollouts"])
    ax[1].set_ylabel("runs below 0 mV (of 14)")
    ax[1].set_ylim(0, 15)
    ax[1].set_title("Development holdout, calibrated margins", loc="left", fontsize=10.5)
    fig.tight_layout()
    fig.savefig(H / "fig_openloop_vs_closedloop.png", dpi=150)
    plt.close(fig)

    # 2) final family: completion vs runs crossing the plating-risk criterion
    fig, ax = plt.subplots(figsize=(11, 4.6))
    labs = [s["controller"].replace(" (reference)", "\n(reference)").replace(", ", "\n") for s in final]
    xs = np.arange(len(final))
    ax.bar(xs - 0.2, [s["done"] for s in final], 0.38, color=SERIES[0], label="reached 90 %")
    ax.bar(xs + 0.2, [s["runs_below0"] for s in final], 0.38, color=SERIES[7], label="runs below 0 mV (plating-risk criterion)")
    ax.set_xticks(xs, labs, fontsize=7.5)
    ax.set_ylabel(f"runs (of {final[0]['n']})")
    ax.legend(fontsize=8, frameon=False, loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2)
    ax.set_title("Final confirmation family (frozen): completion vs modelled plating-risk crossings", loc="left", fontsize=11, pad=24)
    fig.tight_layout()
    fig.savefig(H / "fig_final.png", dpi=150)
    plt.close(fig)


def main():
    ok = freeze.verify()
    dev = {"v1": summarise(jl("v1/closed_loop.jsonl")), "A": summarise(jl("closed_loop_A.jsonl")),
           "B": summarise(jl("closed_loop_B.jsonl"))}
    final_rows = jl("closed_loop_final.jsonl")
    final = summarise(final_rows)
    figures(dev, final)
    man = json.loads((H / "freeze_manifest.json").read_text())
    L = ["# Measurement-only anode-potential estimators: open loop vs closed loop", "",
         "**Question.** Can a measurement-only estimator recover charging performance in cold, plating-limited conditions "
         "while holding a declared modelled protection target (true modelled anode ≥ 15 mV)?", "",
         "**Boundaries.** Everything here is simulation. The full sodium-ion DFN is the *higher-fidelity simulation "
         "reference*, not experimental ground truth; holding out parameter cases tests generalisation *within that model "
         "family*. Below 15 mV is a violation of the selected buffer; below 0 mV crosses the model's *plating-risk "
         "criterion*. The DFN does not model sodium deposition, so a run below 0 mV is a diagnostic model result, not "
         "evidence of deposited sodium. Experimental validation of internal potential would need instrumented cells "
         "(e.g. micro-reference electrodes).", "",
         "## Set-up", "",
         "- **Ground truth:** the full DFN (isothermal per run), never the lookup maps. Cells vary from the nominal design: "
         "hard-carbon diffusivity ×0.6/1/1.6, anode reaction rate ×0.5/1/2, electrolyte conductivity ×0.7/1/1.3 (27 "
         "combinations), at 0, 10 and 25 °C.",
         f"- **Splits:** 20 training combinations; 7 **development holdout** combinations ({C.HELD_OUT}). The development "
         "holdout was inspected and its failures motivated the fixes below, so it is *not* a final test. A separate "
         f"**final confirmation family**: {len(man['final_combos'])} interior combinations (×0.8/1.3, ×0.7/1.4, ×0.85/1.15) "
         f"at {man['final_temps']} °C (5 °C never used before), simulated only after the freeze below.",
         "- **Estimators** (inputs: measured voltage, current, temperature, declared initial SOC): (a) **map**, the nominal "
         "lookup map at the coulomb-counted SOC, which is what the pack BMS does; (b) **observer**, anode polarisation "
         "with a first-order lag plus an online gain fitted to the voltage error (recursive least squares); (c) **ml**, "
         "a small MLP (32-32) predicting the correction to the map from causal history features (exponential filters of "
         "current and voltage residual).",
         "- **Causality:** at each 2 s decision every candidate current is fed to the estimator explicitly; inputs are "
         "measurements available before the decision; the target is the anode potential at the end of the 2 s step under "
         "that candidate; the terminal voltage of a candidate is predicted by the estimator's own model, never read from "
         "the DFN before choosing.",
         "- **Calibration:** margin = 15 mV + the 99th percentile of the estimator's optimistic error (estimate − truth) "
         "while the true anode is below 50 mV, on a calibration set from training cells only (out-of-fold for ml).",
         "- **Closed loop:** each controller charges the DFN live: every 2 s it picks the highest current (≤ 3.4C) its "
         "estimator says keeps the anode ≥ margin and voltage ≤ 4.3 V. The evaluator reads the true state and never "
         "corrects the controller. Stop at 90 % (true SOC window), a 5 min stall, or 2.5 h. *Oracle feedback* uses the "
         "true previous anode potential (not measurable) as a reference.", "",
         "## 1. Open loop looked good; closed loop did not (v1)", "",
         "| Version | Estimator | Development holdout: optimistic error p95 / p99 / max near the limit | Bias | Calibration set p99 | Calibrated margin |",
         "|---|---|---|---|---|---|"]
    for tag, est, te, cal, margin, tau in openloop_rows():
        L.append(f"| {tag} | {est}{f' (lag {tau:g} s)' if est == 'observer' else ''} | {te['p95']:.1f} / {te['p99']:.1f} / {te['max']:.1f} mV | "
                 f"{te['bias']:+.1f} mV | {cal['p99']:.1f} mV | {margin:.1f} mV |")
    L += ["", "Open loop (predicting along recorded sessions), the network looked far better than the map and observer. "
          "**In closed loop on the development holdout, v1's optimistic error reached a p99 of "
          f"{[s for s in dev['v1'] if s['controller'].startswith('ml, calibrated')][0]['opt_p99']:.0f} mV**, against "
          f"{json.loads((H / 'v1/openloop.json').read_text())['estimators']['ml']['test']['p99']:.0f} mV open loop: an estimator "
          "with good open-loop accuracy developed sustained optimistic error on trajectories its own controller created, "
          "letting the modelled anode potential stay below the plating-risk criterion.", "",
          "### Development holdout, v1 (all controllers)", ""]
    L += table(dev["v1"])
    L += ["", "## 2. Two candidate causes, isolated one at a time", "",
          "1. **Decision-horizon mismatch (fix A).** The v1 training sessions were solved in one go with 1 s current ramps "
          "and sampled at the switch instants, so each label was the anode potential halfway through a current change, "
          "and the 'measured' voltage came from the same instant. The closed loop holds a current for 2 s and reads the "
          "end of the step. Fix A regenerates the *same* profiles (same seeds) by stepping the DFN exactly as the closed "
          "loop does; coverage is unchanged.",
          "2. **Training coverage (fix B).** Open-loop training sessions at 0 °C mostly hit the voltage limit within "
          "minutes at high current; in closed loop the controller spends hours at low current near full charge. Fix B is "
          "**DAgger-inspired closed-loop data aggregation** (one bounded iteration): run controllers in the loop on "
          "*training cells only* (160 rollouts: map 15 mV, network A at 15 and 25 mV, observer 15 mV), aggregate with "
          "the open-loop data, refit, and calibrate the margin on out-of-fold errors from those rollouts. Unlike original "
          "DAgger it aggregates DFN anode labels for an estimator, not expert actions, so its guarantees do not transfer.", "",
          "### Development holdout, network versions (14 runs each)", ""]
    L += table([s for s in dev["v1"] if s["controller"].startswith("ml")] + dev["A"] + dev["B"])
    L += ["", "![open loop vs closed loop](fig_openloop_vs_closedloop.png)", "",
          "Fix A shrank the closed-loop optimistic tail and the depth and duration of crossings sharply but not their "
          "number; fix B reduced the number of crossings as well. Both causes contributed. Because the development holdout "
          "guided these fixes, it cannot confirm them.", "",
          "## 3. Final confirmation on a frozen, untouched family", "",
          f"Before any final-family run existed, `freeze_manifest.json` recorded SHA-256 hashes of all three network "
          f"versions, their calibration files and the scripts, the controllers and margins, the cases and the stopping "
          f"rules (frozen {man['frozen_at']}). Check at report time: **{'unchanged' if ok else 'CHANGED, results not valid as a frozen test'}**.", ""]
    L += table(final)
    L += ["", "![final family](fig_final.png)", ""]
    s = {x["controller"]: x for x in final}
    b = next(x for k, x in s.items() if k.startswith("ml B, calibrated"))
    v1 = next(x for k, x in s.items() if k.startswith("ml v1"))
    std = s["standard map, 40 mV"]
    orc = s["oracle feedback (reference)"]
    mp = s["map, 15 mV"]
    L += ["### Reading the final family", "",
          f"- **Network v1 (frozen, open-loop trained):** {v1['runs_below0']} of {v1['n']} runs below 0 mV, worst "
          f"{v1['worst_mv']:.0f} mV, closed-loop optimistic p99 {v1['opt_p99']:.0f} mV.",
          f"- **Network B (frozen, aggregated):** {b['runs_below0']} of {b['n']} runs below 0 mV, worst {b['worst_mv']:.0f} mV, "
          f"longest {b['max_below0_min']:.1f} min, p99 {b['opt_p99']:.0f} mV; reached 90 % in {b['done']} of {b['n']}.",
          f"- **Standard map at 40 mV:** {std['runs_below0']} of {std['n']} below 0 mV, worst {std['worst_mv']:.0f} mV; "
          f"reached 90 % in {std['done']}. **Map at 15 mV (the pack BMS method):** {mp['runs_below0']} below 0 mV, worst "
          f"{mp['worst_mv']:.0f} mV.",
          f"- **Oracle feedback reference:** {orc['runs_below0']} below 0 mV, reached 90 % in {orc['done']}, median "
          f"{'—' if orc['t90_median'] is None else f'{orc['t90_median']:.0f} min'}.",
          "", "### Verdict", "",
          "**Not yet.** No measurement-only estimator recovered charging performance while holding the declared target on "
          "the frozen family. Closed-loop aggregation (B) removed most of the severity (worst crossing "
          f"{v1['worst_mv']:.0f} → {b['worst_mv']:.0f} mV, longest {v1['max_below0_min']:.0f} → {b['max_below0_min']:.1f} min) and "
          f"completed every run, but {b['runs_below0']} of {b['n']} runs still crossed 0 mV briefly: its margin, calibrated on "
          f"training-cell rollouts (p99 {json.loads((H / 'openloop_B.json').read_text())['estimators']['ml']['calibration']['p99']:.1f} mV), "
          f"underestimated the tail on unseen cells (p99 {b['opt_p99']:.0f} mV). The calibrated observer was the only "
          "measurement-only controller that held both 15 mV and 0 mV here, at a much longer charge. A conservative "
          "map margin is safe but stalls. The protection–speed trade-off is set by how well the estimator's error tail "
          "transfers to unseen cells.", "",
          "## What this establishes and what it doesn't", "",
          "- **Establishes:** open-loop accuracy is not evidence of closed-loop safety for a charging estimator; a "
          "decision-horizon mismatch and training coverage both caused optimistic closed-loop error here; one round of "
          "closed-loop data aggregation on training cells changed the result on a frozen family of unseen parameter cases "
          "(numbers above).",
          "- **Doesn't establish:** behaviour of a real cell (the DFN is a simulation reference with assumed parameters), "
          "any sodium deposition, generalisation beyond this model family, or variability across seeds (one run per case). "
          "The calibration is empirical: a margin that held on these cases is not a safety guarantee.",
          f"- **Also true:** the nominal-map controllers are not a safe baseline on off-nominal cells. On the development "
          f"holdout the standard 40 mV controller crossed 0 mV in {[s for s in dev['v1'] if s['controller'] == 'standard map, 40 mV'][0]['runs_below0']} "
          f"of 14 runs; on the milder final family it did not cross but reached 90 % in only {std['done']} of {std['n']} "
          "within 2.5 h. The 15 mV map controller (the pack BMS method) crossed in both.", "",
          "## Reproduce", "", "```bash", "cd analysis/estimator",
          "../../.venv/Scripts/python generate.py && ../../.venv/Scripts/python train_eval.py && ../../.venv/Scripts/python closed_loop.py   # v1 (preserved in v1/)",
          "../../.venv/Scripts/python generate_step.py && ../../.venv/Scripts/python train_eval.py --set sessions_step.jsonl:data_step:openloop --tag A",
          "../../.venv/Scripts/python closed_loop.py --preset A_dev --out closed_loop_A.jsonl",
          "../../.venv/Scripts/python closed_loop.py --preset rollout --out rollouts.jsonl && ../../.venv/Scripts/python freeze.py index",
          "../../.venv/Scripts/python train_eval.py --set sessions_step.jsonl:data_step:openloop --set sessions_rollout.jsonl:data_rollout:rollout --tag B",
          "../../.venv/Scripts/python closed_loop.py --preset B_dev --out closed_loop_B.jsonl",
          "../../.venv/Scripts/python freeze.py freeze && ../../.venv/Scripts/python closed_loop.py --preset final --out closed_loop_final.jsonl",
          "../../.venv/Scripts/python report.py", "```"]
    (H / "REPORT.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("wrote REPORT.md; manifest", "OK" if ok else "CHANGED")


if __name__ == "__main__":
    main()
