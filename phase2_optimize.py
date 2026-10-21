"""
Phase 2 - Optimize a multi-step fast-charge profile.

The 10% -> 80% SOC window is split into equal SOC bins. Each bin gets its own
constant C-rate (with a 4.2 V cap; if the cap is hit, the charger switches to
constant-voltage until 80%). Bayesian optimization (Optuna TPE) searches the
C-rates to minimize time-to-80% subject to two safety limits:

  * thermal:  peak cell temperature <= 45 C
  * plating:  anode potential vs Li/Li+ at the separator >= 0 V
              (below 0 V, metallic lithium can plate on the graphite -
              the main cause of fast-charge degradation)

The result is compared against the fastest plain CC-CV charge that obeys the
same limits.

Run:
    .venv/Scripts/python phase2_optimize.py
    .venv/Scripts/python phase2_optimize.py --n-trials 150 --n-steps 6
"""

import argparse
import json
import warnings

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import optuna
import pandas as pd
import pybamm

from phase1_baseline import (
    INITIAL_SOC,
    RESULTS,
    TARGET_SOC,
    TEMP_LIMIT_C,
    V_MAX,
    build_model,
    build_params,
    first_var,
    quiet_solver,
)

PLATING_LIMIT_V = 0.0
C_MIN, C_MAX = 0.5, 3.0
CV_HOLD_MIN = 40  # time allowed in CV to finish reaching 80% if the voltage cap is hit
MODEL = "SPMe"

# Penalty weights: minutes added per unit of constraint violation
TEMP_PENALTY_MIN_PER_C = 5.0
PLATING_PENALTY_MIN_PER_MV = 1.0
FAILED_SCORE = 500.0


# ---------------------------------------------------------------- simulation


def profile_steps(c_rates: list[float]) -> list[str]:
    """One CC step per equal SOC bin, then a CV hold in case the voltage cap was hit."""
    bin_soc = (TARGET_SOC - INITIAL_SOC) / len(c_rates)
    steps = []
    for c in c_rates:
        seconds = bin_soc * 3600 / c  # at 1C the cell charges 100% SOC per hour
        steps.append(f"Charge at {c:.4f}C for {seconds:.2f} seconds or until {V_MAX} V")
    steps.append(f"Hold at {V_MAX} V for {CV_HOLD_MIN} minutes")
    return steps


def cccv_steps(c_rate: float) -> list[str]:
    return [
        f"Charge at {c_rate:.4f}C until {V_MAX} V",
        f"Hold at {V_MAX} V for {CV_HOLD_MIN} minutes",
    ]


def run(steps: list[str]) -> pd.DataFrame | None:
    """Simulate a charge protocol from 10% SOC; return time series truncated at 80% SOC, or None."""
    experiment = pybamm.Experiment([tuple(steps)], period="10 seconds")
    params = build_params()
    capacity_ah = params["Nominal cell capacity [A.h]"]

    solvers = [
        lambda: None,
        lambda: pybamm.IDAKLUSolver(rtol=1e-6, atol=1e-8),
        lambda: pybamm.CasadiSolver(mode="safe", rtol=1e-6, atol=1e-8),
    ]
    for make_solver in solvers:
        try:
            with warnings.catch_warnings(), quiet_solver():
                warnings.simplefilter("ignore")
                sim = pybamm.Simulation(
                    build_model(MODEL),
                    parameter_values=params,
                    experiment=experiment,
                    solver=make_solver(),
                )
                sol = sim.solve(initial_soc=INITIAL_SOC)
        except pybamm.SolverError:
            continue
        df = pd.DataFrame(
            {
                "time_min": first_var(sol, "Time [min]"),
                "current_a": -first_var(sol, "Current [A]"),
                "voltage_v": first_var(sol, "Voltage [V]", "Terminal voltage [V]"),
                "temp_c": first_var(sol, "X-averaged cell temperature [C]"),
                "anode_potential_v": first_var(
                    sol, "Negative electrode surface potential difference at separator interface [V]"
                ),
                "soc": INITIAL_SOC - first_var(sol, "Discharge capacity [A.h]") / capacity_ah,
            }
        )
        reached = np.flatnonzero(df["soc"].to_numpy() >= TARGET_SOC)
        if reached.size:
            return df.iloc[: reached[0] + 1].reset_index(drop=True)
    return None


def evaluate(steps: list[str]) -> dict:
    df = run(steps)
    if df is None:
        return {"score": FAILED_SCORE, "feasible": False, "df": None,
                "time_to_80_min": np.nan, "peak_temp_c": np.nan, "min_anode_potential_mv": np.nan}
    t80 = df["time_min"].iloc[-1]
    peak = df["temp_c"].max()
    min_anode_mv = df["anode_potential_v"].min() * 1000
    temp_violation = max(0.0, peak - TEMP_LIMIT_C)
    plating_violation = max(0.0, PLATING_LIMIT_V * 1000 - min_anode_mv)
    return {
        "score": t80
        + TEMP_PENALTY_MIN_PER_C * temp_violation
        + PLATING_PENALTY_MIN_PER_MV * plating_violation,
        "feasible": temp_violation == 0 and plating_violation == 0,
        "time_to_80_min": t80,
        "peak_temp_c": peak,
        "min_anode_potential_mv": min_anode_mv,
        "df": df,
    }


# ---------------------------------------------------------------- baseline


def best_safe_cccv(rates=np.round(np.arange(0.6, 1.51, 0.05), 2)) -> tuple[float, dict]:
    """Fastest single-rate CC-CV charge that respects both safety limits."""
    best = None
    for c in rates:
        r = evaluate(cccv_steps(c))
        flag = "ok" if r["feasible"] else "violates limits"
        print(f"  CC-CV {c:.2f}C: {r['time_to_80_min']:.1f} min, peak {r['peak_temp_c']:.1f} C, "
              f"anode {r['min_anode_potential_mv']:.0f} mV -> {flag}")
        if r["feasible"] and (best is None or r["time_to_80_min"] < best[1]["time_to_80_min"]):
            best = (float(c), r)
    if best is None:
        raise RuntimeError("No CC-CV rate in the sweep satisfies the limits")
    return best


# ---------------------------------------------------------------- optimization


def optimize(n_steps: int, n_trials: int, seed: int) -> optuna.Study:
    def objective(trial: optuna.Trial) -> float:
        c_rates = [trial.suggest_float(f"c{i + 1}", C_MIN, C_MAX) for i in range(n_steps)]
        r = evaluate(profile_steps(c_rates))
        for k in ("time_to_80_min", "peak_temp_c", "min_anode_potential_mv", "feasible"):
            trial.set_user_attr(k, r[k])
        return r["score"]

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(
        direction="minimize",
        sampler=optuna.samplers.TPESampler(seed=seed, multivariate=True),
    )
    # Seed with sensible starting guesses: flat 1C, and a tapering profile
    study.enqueue_trial({f"c{i + 1}": 1.0 for i in range(n_steps)})
    study.enqueue_trial({f"c{i + 1}": c for i, c in enumerate(np.linspace(2.0, 0.8, n_steps))})

    def progress(study, trial):
        best = study.best_trial
        print(f"  trial {trial.number + 1:>3}/{n_trials}: score {trial.value:6.1f} | "
              f"best {best.value:5.1f} min (feasible={best.user_attrs['feasible']})")

    study.optimize(objective, n_trials=n_trials, callbacks=[progress])
    return study


# ---------------------------------------------------------------- reporting


def plot_comparison(base_c: float, base: dict, best_rates: list[float], best: dict):
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), sharex=True)
    runs = [(f"Best safe CC-CV ({base_c:.2f}C)", base["df"], "tab:blue"),
            ("Optimized " + " / ".join(f"{c:.2f}" for c in best_rates) + " C", best["df"], "tab:orange")]
    panels = [("current_a", "Current [A]"), ("temp_c", "Cell temperature [°C]"),
              ("soc", "State of charge [-]"), ("anode_potential_v", "Anode potential vs Li [V]")]
    for ax, (col, label) in zip(axes.flat, panels):
        for name, df, color in runs:
            ax.plot(df["time_min"], df[col], label=name, color=color)
        ax.set_ylabel(label)
        ax.grid(alpha=0.3)
    axes[0, 1].axhline(TEMP_LIMIT_C, color="red", ls="--", lw=1, label="45 °C limit")
    axes[1, 1].axhline(PLATING_LIMIT_V, color="red", ls="--", lw=1, label="plating threshold")
    axes[0, 0].legend(fontsize=8)
    axes[0, 1].legend(fontsize=8)
    axes[1, 1].legend(fontsize=8)
    for ax in axes[1]:
        ax.set_xlabel("Time [min]")
    saved = base["time_to_80_min"] - best["time_to_80_min"]
    fig.suptitle(f"10→80% fast charge: optimized {best['time_to_80_min']:.1f} min vs "
                 f"CC-CV {base['time_to_80_min']:.1f} min ({saved:.1f} min faster)")
    fig.tight_layout()
    out = RESULTS / "phase2_optimized_vs_cccv.png"
    fig.savefig(out, dpi=150)
    return out


def plot_history(trials: pd.DataFrame, base_time: float):
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ok = trials["feasible"]
    ax.scatter(trials.loc[ok, "number"], trials.loc[ok, "time_to_80_min"], s=14, label="feasible", color="tab:green")
    ax.scatter(trials.loc[~ok, "number"], trials.loc[~ok, "time_to_80_min"], s=14, label="violates limits",
               color="tab:red", alpha=0.5)
    best_so_far = trials["time_to_80_min"].where(ok).cummin()
    ax.step(trials["number"], best_so_far, where="post", color="black", label="best feasible so far")
    ax.axhline(base_time, color="tab:blue", ls="--", label="best safe CC-CV")
    ax.set_xlabel("Trial")
    ax.set_ylabel("Time 10→80% [min]")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    out = RESULTS / "phase2_optimization_history.png"
    fig.savefig(out, dpi=150)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n-steps", type=int, default=5, help="number of constant-current steps (SOC bins)")
    ap.add_argument("--n-trials", type=int, default=100)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    RESULTS.mkdir(exist_ok=True)

    print("Baseline: sweeping CC-CV rates for the fastest one within the limits...")
    base_c, base = best_safe_cccv()
    print(f"-> best safe CC-CV: {base_c:.2f}C, {base['time_to_80_min']:.1f} min\n")

    print(f"Optimizing a {args.n_steps}-step profile ({args.n_trials} trials)...")
    study = optimize(args.n_steps, args.n_trials, args.seed)

    trials = pd.DataFrame(
        [{"number": t.number, **t.params, **t.user_attrs, "score": t.value} for t in study.trials]
    )
    trials.to_csv(RESULTS / "phase2_trials.csv", index=False)

    feasible = trials[trials["feasible"]]
    if feasible.empty:
        print("No feasible profile found - try more trials.")
        return
    top = feasible.sort_values("time_to_80_min").iloc[0]
    best_rates = [float(top[f"c{i + 1}"]) for i in range(args.n_steps)]
    best = evaluate(profile_steps(best_rates))

    summary = {
        "cell": "LG M50 21700 (Chen2020), 5 Ah",
        "model": f"{MODEL} + lumped thermal",
        "window": f"{INITIAL_SOC:.0%} -> {TARGET_SOC:.0%} SOC",
        "limits": {"peak_temp_c": TEMP_LIMIT_C, "anode_potential_v_min": PLATING_LIMIT_V},
        "baseline_cccv": {
            "c_rate": base_c,
            "time_to_80_min": round(base["time_to_80_min"], 2),
            "peak_temp_c": round(base["peak_temp_c"], 2),
            "min_anode_potential_mv": round(base["min_anode_potential_mv"], 1),
        },
        "optimized": {
            "c_rates_per_soc_bin": [round(c, 3) for c in best_rates],
            "time_to_80_min": round(best["time_to_80_min"], 2),
            "peak_temp_c": round(best["peak_temp_c"], 2),
            "min_anode_potential_mv": round(best["min_anode_potential_mv"], 1),
        },
        "time_saved_min": round(base["time_to_80_min"] - best["time_to_80_min"], 2),
    }
    (RESULTS / "phase2_best_profile.json").write_text(json.dumps(summary, indent=2))

    print("\n" + json.dumps(summary, indent=2))
    print(f"\nPlots: {plot_comparison(base_c, base, best_rates, best)}")
    print(f"       {plot_history(trials, base['time_to_80_min'])}")


if __name__ == "__main__":
    main()
