"""
Turn EVBattery features into realism targets (targets.json) + figures, and compare with our simulation.

Run after analyze.py:   .venv/Scripts/python analysis/evbattery/targets.py
"""

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))


def load():
    z = np.load(HERE / "features.npz")
    names = list(z["feature_names"])
    F = {n: z["features"][:, i] for i, n in enumerate(names)}
    R = {n: z["rows"][:, i] for i, n in enumerate(z["row_names"])}
    return F, R


def pct(x, q):
    x = x[np.isfinite(x)]
    return {f"p{p}": round(float(np.percentile(x, p)), 4) for p in q} | {"n": int(x.size)} if x.size else {"n": 0}


def binned(x, y, bins, q=(50, 90, 95, 99), min_n=200):
    out = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        m = (x >= lo) & (x < hi) & np.isfinite(y)
        if m.sum() >= min_n:
            out.append({"bin": [float(lo), float(hi)], **pct(y[m], q)})
    return out


def main():
    F, R = load()
    soc_bins = np.arange(0, 101, 5)
    targets = {"source": "EVBattery (He et al., arXiv 2201.12358), 464 EVs, 3 manufacturers; lithium-ion, "
                         "values anonymised/perturbed by the authors. Behavioural benchmarks, not Na-ion parameters.",
               "n_snippets": int(len(F["car"])), "by_dataset": {}}
    for ds in (1, 2, 3, "all"):
        m = np.ones(len(F["car"]), bool) if ds == "all" else F["dataset"] == ds
        r = np.ones(len(R["soc"]), bool) if ds == "all" else R["dataset"] == ds
        fast = m & (F["c_mean"] > 0.5)
        d = {
            "snippets": int(m.sum()),
            "cars": int(len(np.unique(F["car"][m]))),
            "c_rate_envelope_by_soc": binned(R["soc"][r], R["c_local"][r], soc_bins),
            "taper_onset_soc_fast_charge": pct(F["taper_soc"][fast], (10, 25, 50, 75, 90)),
            "dTdt_k_per_min_vs_c": binned(F["c_mean"][m], F["dTdt_k_per_min"][m], np.array([0, .25, .5, .75, 1, 1.5, 2, 3])),
            "temp_spread_c_vs_c": binned(F["c_mean"][m], F["tspread_mean"][m], np.array([0, .25, .5, .75, 1, 1.5, 2, 3])),
            "cell_voltage_spread_mv_by_soc": binned(R["soc"][r], R["vspread_mv"][r], soc_bins, q=(50, 90, 99)),
            "max_temp_c": pct(F["tmax1"][m], (5, 50, 95, 99)),
        }
        hi = m & (F["soc1"] > 80)
        d["anomaly_vs_normal_above_80soc"] = {
            lab: {"c_mean": pct(F["c_mean"][hi & (F["label"] == v)], (50, 90)),
                  "vspread_mv": pct(F["vspread_mv_mean"][hi & (F["label"] == v)], (50, 90)),
                  "tmax_c": pct(F["tmax1"][hi & (F["label"] == v)], (50, 90))}
            for lab, v in (("normal", 0), ("anomaly", 1))
        }
        targets["by_dataset"][str(ds)] = d
    (HERE / "targets.json").write_text(json.dumps(targets, indent=1))
    plots(F, R)
    compare(targets)


def plots(F, R):
    fig, ax = plt.subplots(2, 2, figsize=(13, 9))
    colors = {1: "tab:blue", 2: "tab:orange", 3: "tab:green"}
    bins = np.arange(0, 101, 5)
    mid = (bins[:-1] + bins[1:]) / 2
    for ds, col in colors.items():
        r = R["dataset"] == ds
        for q, ls in ((50, "-"), (95, "--")):
            ys = [np.percentile(R["c_local"][r & (R["soc"] >= lo) & (R["soc"] < hi)], q)
                  if (r & (R["soc"] >= lo) & (R["soc"] < hi)).sum() > 200 else np.nan for lo, hi in zip(bins[:-1], bins[1:])]
            ax[0, 0].plot(mid, ys, ls, color=col, label=f"maker {ds} p{q}")
        m = F["dataset"] == ds
        fast = m & (F["c_mean"] > 0.5) & np.isfinite(F["taper_soc"])
        ax[0, 1].hist(F["taper_soc"][fast], bins=np.arange(0, 101, 2.5), alpha=0.5, color=col, label=f"maker {ds}",
                      density=True)
        cb = np.array([0, .25, .5, .75, 1, 1.5, 2, 3])
        cm = (cb[:-1] + cb[1:]) / 2
        med = [np.nanmedian(F["dTdt_k_per_min"][m & (F["c_mean"] >= lo) & (F["c_mean"] < hi)])
               if (m & (F["c_mean"] >= lo) & (F["c_mean"] < hi)).sum() > 100 else np.nan for lo, hi in zip(cb[:-1], cb[1:])]
        ax[1, 0].plot(cm, med, "o-", color=col, label=f"maker {ds}")
        vs = [np.nanmedian(R["vspread_mv"][(R["dataset"] == ds) & (R["soc"] >= lo) & (R["soc"] < hi)])
              if ((R["dataset"] == ds) & (R["soc"] >= lo) & (R["soc"] < hi)).sum() > 200 else np.nan
              for lo, hi in zip(bins[:-1], bins[1:])]
        ax[1, 1].plot(mid, vs, "o-", color=col, label=f"maker {ds}")
    ax[0, 0].set(xlabel="SOC [%]", ylabel="charge C-rate (from dSOC/dt)", title="Charging rate envelope vs SOC")
    ax[0, 1].set(xlabel="SOC where current starts tapering [%]", ylabel="density", title="Taper onset (fast-charge snippets)")
    ax[1, 0].set(xlabel="mean C-rate of snippet", ylabel="max-cell temperature rise [K/min]", title="Heating rate vs C-rate (median)")
    ax[1, 1].set(xlabel="SOC [%]", ylabel="max - min cell voltage [mV]", title="Cell imbalance vs SOC (median)")
    for a in ax.flat:
        a.grid(alpha=0.3)
        a.legend(fontsize=8)
    fig.suptitle("EVBattery: real-world EV charging behaviour (464 vehicles, Li-ion)")
    fig.tight_layout()
    fig.savefig(HERE / "evbattery_overview.png", dpi=140)


def compare(targets):
    """Put our simulated pack next to the real-world benchmarks (behavioural, not like-for-like chemistry)."""
    import warnings

    warnings.simplefilter("ignore")
    from ev_pack.pack_sim import simulate

    rows = []
    for arch in ("blade", "module"):
        for st in ("standard", "optimized"):
            for ch in ("125 kW DC", "250 kW DC", "350 kW DC"):
                r = simulate(arch, ch, st, 0.10, 0.90)
                s = r.series
                c = s["c_rate"]
                peak = c.max()
                below = np.flatnonzero((c < 0.8 * np.maximum.accumulate(c)) & (s["soc"] > 0.2))
                taper = float(s["soc"][below[0]] * 100) if below.size else float("nan")
                # heating rate during the high-current phase (first half)
                h = len(s["time_min"]) // 2
                dTdt = float(np.polyfit(s["time_min"][:h], s["temp_max"][:h], 1)[0]) if h > 5 else float("nan")
                rows.append({"arch": arch, "strategy": st, "charger": ch, "peak_c": round(float(peak), 2),
                             "mean_c_first_half": round(float(c[:h].mean()), 2), "taper_onset_soc": round(taper, 1),
                             "dTdt_k_per_min": round(dTdt, 2), "max_temp_spread_c": round(r.summary["max_temp_spread_c"], 2),
                             "t80": r.summary["time_to_soc_min"].get("0.8"), "t90": r.summary["time_to_soc_min"].get("0.9")})
    (HERE / "sim_vs_real.json").write_text(json.dumps(rows, indent=1))
    real = targets["by_dataset"]["all"]
    print("\nREAL (all makers): taper onset SOC", real["taper_onset_soc_fast_charge"])
    print("REAL dT/dt vs C:", [(b["bin"], b.get("p50")) for b in real["dTdt_k_per_min_vs_c"]])
    print("REAL temp spread vs C:", [(b["bin"], b.get("p50")) for b in real["temp_spread_c_vs_c"]])
    print("REAL C envelope p95 by SOC:", [(b["bin"][0], b.get("p95")) for b in real["c_rate_envelope_by_soc"]])
    print("\nSIM:")
    for r in rows:
        print(r)


if __name__ == "__main__":
    main()
