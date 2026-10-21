"""
Open-loop evaluation of the three measurement-only estimators, ML training, and margin calibration.

At every 2 s step each estimator predicts the anode potential for the current about to be applied (before seeing its
effect), then updates with the measured voltage. Errors are estimate - DFN truth; POSITIVE = optimistic (dangerous).

Calibration (training parameter cases only): margin = 15 mV + the 99th percentile of the estimator's optimistic error
while the true anode is below 50 mV. The ML margin uses out-of-fold predictions grouped by parameter case.

Run:  .venv/Scripts/python analysis/estimator/train_eval.py [--workers 8]
"""

import os

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_v] = "1"

import argparse  # noqa: E402
import json  # noqa: E402
import pickle  # noqa: E402
import time  # noqa: E402
import warnings  # noqa: E402
from multiprocessing import Pool  # noqa: E402

import numpy as np  # noqa: E402

import common as C  # noqa: E402

TAUS = [2.0, 15.0, 30.0, 60.0, 120.0]


def load_index(sets=(("sessions.jsonl", "data", "openloop"),)):
    """sets: (index file, data folder, source label). Rows get '_dir' and 'source'."""
    out = []
    for index, folder, source in sets:
        for x in (C.HERE / index).read_text().splitlines():
            r = json.loads(x)
            if "file" in r:
                r["_dir"], r["source"] = folder, source
                out.append(r)
    return out


def session_arrays(r):
    d = np.load(C.HERE / r.get("_dir", "data") / r["file"])
    return d["c"], d["v"], d["anode"], float(d["soc0"])


def run_estimators(r):
    """Causal predictions of every estimator (map, observers at each tau) + ML features/targets for one session."""
    warnings.simplefilter("ignore")
    c, v, an, soc0 = session_arrays(r)
    T = float(r["temp"])
    ests = {"map": C.MapEstimator(T, soc0)}
    for tau in TAUS:
        ests[f"obs_{tau:g}"] = C.ObserverEstimator(T, soc0, tau_a=tau)
    fs = C.FeatureState(T, soc0)
    pred = {k: np.empty(len(c)) for k in ests}
    X = np.empty((len(c), len(C.FEATURE_NAMES)))
    for k in range(len(c)):
        ck = float(c[k])
        for name, e in ests.items():
            pred[name][k] = float(e.predict(np.array([ck]))[0])
            e.update(ck, float(v[k]))
        X[k] = fs.row(np.array([ck]))[0]
        fs.update(ck, float(v[k]))
    return {"key": r["file"], "combo": tuple(r["combo"]), "temp": T, "held_out": r["held_out"], "source": r["source"], "truth": an,
            "pred": pred, "X": X}


def tail_metrics(err_mv, truth_mv):
    near = truth_mv < C.NEAR_LIMIT_MV
    e = err_mv[near]
    if e.size == 0:
        return None
    return {"n_near": int(e.size), "p95": float(np.percentile(e, 95)), "p99": float(np.percentile(e, 99)),
            "max": float(e.max()), "bias": float(e.mean()), "frac_opt_2mv": float(np.mean(e > 2.0)),
            "rmse_near": float(np.sqrt(np.mean(e ** 2))), "rmse_all": float(np.sqrt(np.mean(err_mv ** 2)))}


def pooled(results, name, which):
    err, tru = [], []
    for s in results:
        if (which == "train") == (not s["held_out"]):
            err.append((s["pred"][name] - s["truth"]) * 1000)
            tru.append(s["truth"] * 1000)
    return tail_metrics(np.concatenate(err), np.concatenate(tru))


def longest_optimistic_run_s(err_mv, truth_mv):
    """Longest stretch (s) with the estimate > 2 mV optimistic while the true anode is near the limit."""
    bad = (err_mv > 2.0) & (truth_mv < C.NEAR_LIMIT_MV)
    best = cur = 0
    for b in bad:
        cur = cur + 1 if b else 0
        best = max(best, cur)
    return best * C.DT


def train_ml(sessions, seed=0):
    from sklearn.neural_network import MLPRegressor

    X = np.concatenate([s["X"] for s in sessions])
    y = np.concatenate([(s["truth"] - s["X"][:, 4]) * 1000 for s in sessions])          # map correction, mV
    truth = np.concatenate([s["truth"] * 1000 for s in sessions])
    rng = np.random.default_rng(seed)
    keep = (truth < 150) | (rng.random(len(truth)) < 0.3)                              # focus on the region near the limit
    X, y = X[keep], y[keep]
    mu, sd = X.mean(0), X.std(0) + 1e-9
    reg = MLPRegressor(hidden_layer_sizes=(32, 32), alpha=1e-3, learning_rate_init=2e-3, max_iter=400,
                       early_stopping=True, validation_fraction=0.15, n_iter_no_change=15, random_state=seed)
    reg.fit((X - mu) / sd, y)
    return (mu, sd, reg)


def ml_predict(model, X):
    mu, sd, reg = model
    return X[:, 4] + reg.predict((X - mu) / sd) / 1000.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--set", action="append", default=None,
                    help="index.jsonl:data_dir:source (repeatable). Default: v1 open-loop data")
    ap.add_argument("--tag", default="", help="output suffix, e.g. A or B")
    a = ap.parse_args()
    sets = [tuple(s.split(":")) for s in a.set] if a.set else [("sessions.jsonl", "data", "openloop")]
    sfx = f"_{a.tag}" if a.tag else ""
    idx = load_index(sets)
    t0 = time.time()
    with Pool(a.workers, maxtasksperchild=20) as pool:
        res = pool.map(run_estimators, idx, chunksize=2)
    print(f"estimators over {len(res)} sessions in {time.time() - t0:.0f}s", flush=True)
    train = [s for s in res if not s["held_out"]]
    test = [s for s in res if s["held_out"]]

    # observer lag chosen on training cases: smallest p99 optimistic tail near the limit (RMSE as tiebreak)
    obs_scores = {tau: pooled(res, f"obs_{tau:g}", "train") for tau in TAUS}
    best_tau = min(TAUS, key=lambda t: (round(obs_scores[t]["p99"], 1), obs_scores[t]["rmse_near"]))
    for s in res:
        s["pred"]["observer"] = s["pred"][f"obs_{best_tau:g}"]

    # ML: out-of-fold on training (grouped by parameter combo) for calibration, final model on all training
    combos = sorted({s["combo"] for s in train})
    folds = [combos[k::5] for k in range(5)]
    for fold in folds:
        model = train_ml([s for s in train if s["combo"] not in fold])
        for s in train:
            if s["combo"] in fold:
                s["pred"]["ml"] = ml_predict(model, s["X"])
    final = train_ml(train)
    for s in test:
        s["pred"]["ml"] = ml_predict(final, s["X"])
    with open(C.HERE / f"ml_model{sfx}.pkl", "wb") as f:
        pickle.dump(final, f)

    out = {"best_tau": best_tau, "observer_tau_scores_train": {f"{t:g}": obs_scores[t] for t in TAUS},
           "held_out": C.HELD_OUT, "n_train": len(train), "n_test": len(test), "estimators": {}}
    cal_sessions = [s for s in train if s["source"] == "rollout"] or train
    out["calibration_set"] = "training-cell closed-loop rollouts (out-of-fold for ml)" if cal_sessions is not train \
        else "all training sessions (out-of-fold for ml)"
    out["sources"] = {src: sum(1 for s in res if s["source"] == src) for src in {s["source"] for s in res}}
    for name in ("map", "observer", "ml"):
        tr, te = pooled(res, name, "train"), pooled(res, name, "test")
        cal = tail_metrics(np.concatenate([(s["pred"][name] - s["truth"]) * 1000 for s in cal_sessions]),
                           np.concatenate([s["truth"] * 1000 for s in cal_sessions]))
        runs = [longest_optimistic_run_s((s["pred"][name] - s["truth"]) * 1000, s["truth"] * 1000) for s in test]
        per_temp = {}
        for T in C.TEMPS:
            err = np.concatenate([(s["pred"][name] - s["truth"]) * 1000 for s in test if s["temp"] == T])
            tru = np.concatenate([s["truth"] * 1000 for s in test if s["temp"] == T])
            per_temp[f"{T:g}"] = tail_metrics(err, tru)
        out["estimators"][name] = {"train": tr, "test": te, "test_per_temp": per_temp,
                                   "test_longest_optimistic_s": float(max(runs)),
                                   "calibration": cal, "calibrated_margin_mv": round(15.0 + max(0.0, cal["p99"]), 1)}
        print(f"{name:9s} cal p99 {cal['p99']:6.1f}  train p99 {tr['p99']:6.1f}  test p95/p99/max {te['p95']:6.1f}/{te['p99']:6.1f}/{te['max']:6.1f} mV  "
              f"bias {te['bias']:+5.1f}  rmse_near {te['rmse_near']:5.1f}  -> margin {out['estimators'][name]['calibrated_margin_mv']} mV",
              flush=True)
    (C.HERE / f"openloop{sfx}.json").write_text(json.dumps(out, indent=1))
    # small traces for the report figure
    pick = [s for s in test if s["temp"] == 0.0][:2] + [s for s in test if s["temp"] == 25.0][:1]
    (C.HERE / f"openloop_traces{sfx}.json").write_text(json.dumps([
        {"key": s["key"], "temp": s["temp"], "combo": s["combo"], "truth": (s["truth"] * 1000).round(2).tolist(),
         **{n: (s["pred"][n] * 1000).round(2).tolist() for n in ("map", "observer", "ml")},
         "c": s["X"][:, 0].round(3).tolist()} for s in pick]))
    print(f"done in {time.time() - t0:.0f}s; observer tau = {best_tau:g} s")


if __name__ == "__main__":
    main()
