"""
Phase 1 - Baseline CC-CV charging with a lumped thermal model.

Simulates charging an LG M50 21700 cell (5 Ah, Chen2020 parameter set) from
10% SOC with standard CC-CV at several C-rates, and reports how charge speed
trades off against cell temperature.

Run:
    .venv/Scripts/python phase1_baseline.py
    .venv/Scripts/python phase1_baseline.py --c-rates 0.5 1 2 --model DFN
"""

import argparse
import contextlib
import os
import sys
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # write files, no GUI window
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pybamm

RESULTS = Path(__file__).parent / "results"

AMBIENT_C = 25.0
TEMP_LIMIT_C = 45.0  # the thermal limit we'll optimize against in Phase 2
V_MAX = 4.2
INITIAL_SOC = 0.10
TARGET_SOC = 0.80


def build_model(name: str) -> pybamm.BaseModel:
    """SPMe is fast and good enough for a baseline; DFN is the full physics model."""
    options = {"thermal": "lumped"}
    models = {
        "SPM": pybamm.lithium_ion.SPM,
        "SPMe": pybamm.lithium_ion.SPMe,
        "DFN": pybamm.lithium_ion.DFN,
    }
    return models[name](options=options)


def build_params() -> pybamm.ParameterValues:
    params = pybamm.ParameterValues("Chen2020")
    t_k = AMBIENT_C + 273.15
    params.update({"Ambient temperature [K]": t_k, "Initial temperature [K]": t_k})
    return params


def first_var(solution, *names):
    """Variable names differ slightly between PyBaMM versions; take the first that exists."""
    for n in names:
        try:
            return solution[n].entries
        except KeyError:
            continue
    raise KeyError(f"None of {names} found in solution")


@contextlib.contextmanager
def quiet_solver():
    """Silence PyBaMM's error log and the C-level SUNDIALS messages it prints to stderr."""
    pybamm.set_logging_level("CRITICAL")
    sys.stderr.flush()
    saved_fd = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 2)
    try:
        yield
    finally:
        sys.stderr.flush()
        os.dup2(saved_fd, 2)
        os.close(devnull)
        os.close(saved_fd)
        pybamm.set_logging_level("WARNING")


def completed(sol) -> bool:
    """PyBaMM stops an experiment quietly on solver error; check both steps (CC and CV) ran to their end."""
    steps = sol.cycles[-1].steps if sol.cycles else []
    return len(steps) == 2 and "event" in str(steps[-1].termination)


def simulate_cccv(c_rate: float, model_name: str) -> pd.DataFrame:
    experiment = pybamm.Experiment(
        [
            (
                f"Charge at {c_rate}C until {V_MAX} V",
                f"Hold at {V_MAX} V until C/20",
            )
        ],
        period="10 seconds",
    )
    params = build_params()
    # High C-rates are numerically stiff; if the default solver gives up partway,
    # retry with progressively stricter solver settings. Built lazily so backups
    # are only created when needed. Solver chatter is silenced; completed() is the
    # real check, and a WARNING is printed below if every attempt fails.
    solvers = [
        lambda: None,
        lambda: pybamm.IDAKLUSolver(rtol=1e-6, atol=1e-8),
        lambda: pybamm.CasadiSolver(mode="safe", rtol=1e-6, atol=1e-8),
    ]
    for i, make_solver in enumerate(solvers):
        with warnings.catch_warnings(), quiet_solver():
            warnings.simplefilter("ignore", DeprecationWarning)
            sim = pybamm.Simulation(
                build_model(model_name),
                parameter_values=params,
                experiment=experiment,
                solver=make_solver(),
            )
            sol = sim.solve(initial_soc=INITIAL_SOC)
        if completed(sol):
            if i > 0:
                print(f"  (needed backup solver #{i} to finish)")
            break
    else:
        print(f"  WARNING: {c_rate}C did not complete with any solver - results are partial")

    capacity_ah = params["Nominal cell capacity [A.h]"]
    # PyBaMM convention: charging current is negative, so discharge capacity goes negative.
    charged_ah = -first_var(sol, "Discharge capacity [A.h]")

    df = pd.DataFrame(
        {
            "time_min": first_var(sol, "Time [min]"),
            "current_a": -first_var(sol, "Current [A]"),  # positive = charging
            "voltage_v": first_var(sol, "Voltage [V]", "Terminal voltage [V]"),
            "temp_c": first_var(
                sol,
                "X-averaged cell temperature [C]",
                "Volume-averaged cell temperature [C]",
            ),
            "soc": INITIAL_SOC + charged_ah / capacity_ah,
        }
    )
    df.attrs["completed"] = completed(sol)
    return df


def summarize(c_rate: float, df: pd.DataFrame) -> dict:
    reached = df[df["soc"] >= TARGET_SOC]
    t80 = reached["time_min"].iloc[0] if not reached.empty else np.nan
    t_at_80 = reached["temp_c"].iloc[0] if not reached.empty else np.nan
    peak = df["temp_c"].max()
    return {
        "c_rate": c_rate,
        "time_to_80pct_min": round(t80, 1),
        "temp_at_80pct_c": round(t_at_80, 1),
        "peak_temp_c": round(peak, 1),
        "total_time_min": round(df["time_min"].iloc[-1], 1),
        "final_soc": round(df["soc"].iloc[-1], 3),
        "exceeds_limit": bool(peak > TEMP_LIMIT_C),
        "completed": df.attrs.get("completed", True),
    }


def plot(runs: dict[float, pd.DataFrame], model_name: str) -> Path:
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), sharex=True)
    panels = [
        ("current_a", "Current [A]"),
        ("voltage_v", "Voltage [V]"),
        ("temp_c", "Cell temperature [°C]"),
        ("soc", "State of charge [-]"),
    ]
    for ax, (col, label) in zip(axes.flat, panels):
        for c_rate, df in runs.items():
            ax.plot(df["time_min"], df[col], label=f"{c_rate}C")
        ax.set_ylabel(label)
        ax.grid(alpha=0.3)

    axes[1, 0].axhline(TEMP_LIMIT_C, color="red", ls="--", lw=1, label=f"{TEMP_LIMIT_C:.0f}°C limit")
    axes[1, 1].axhline(TARGET_SOC, color="gray", ls=":", lw=1)
    for ax in axes[1]:
        ax.set_xlabel("Time [min]")
    axes[0, 0].legend(title="CC rate")
    axes[1, 0].legend()
    fig.suptitle(
        f"CC-CV charging, LG M50 (Chen2020), {model_name} + lumped thermal, "
        f"{INITIAL_SOC:.0%} SOC start, {AMBIENT_C:.0f}°C ambient"
    )
    fig.tight_layout()
    out = RESULTS / "phase1_cccv_comparison.png"
    fig.savefig(out, dpi=150)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--c-rates", type=float, nargs="+", default=[0.5, 1.0, 2.0, 3.0])
    ap.add_argument("--model", choices=["SPM", "SPMe", "DFN"], default="SPMe")
    args = ap.parse_args()

    RESULTS.mkdir(exist_ok=True)
    runs, rows = {}, []
    for c in args.c_rates:
        print(f"Simulating {c}C CC-CV ({args.model})...")
        df = simulate_cccv(c, args.model)
        df.to_csv(RESULTS / f"cccv_{c}C.csv", index=False)
        runs[c] = df
        rows.append(summarize(c, df))

    summary = pd.DataFrame(rows)
    summary.to_csv(RESULTS / "phase1_summary.csv", index=False)
    print("\n" + summary.to_string(index=False))
    print(f"\nPlot: {plot(runs, args.model)}")


if __name__ == "__main__":
    main()
