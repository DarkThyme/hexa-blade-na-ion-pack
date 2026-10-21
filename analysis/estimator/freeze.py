"""
Utilities around the bounded aggregation iteration.

  python freeze.py index     build sessions_rollout.jsonl from the training-cell rollouts (for train_eval --set)
  python freeze.py freeze    write freeze_manifest.json BEFORE the final-family run: hashes of every model,
                             calibration file and script, the calibrated margins, final cases and stopping criteria
  python freeze.py verify    historical experiment audit: check the archived frozen artifacts in frozen_v1.0/ against the
                             original freeze_manifest.json (exit 1 on any mismatch or missing file)

The original manifest is never rewritten. The working copies of these files may change in later releases; release
integrity is checked separately by release_integrity.py in the repository root against its own manifest.
"""

import hashlib
import json
import sys
import time

import common as C

ARCHIVE = "frozen_v1.0"   # exact bytes of every file in freeze_manifest.json, as they were when it was written

FROZEN = ["v1/ml_model.pkl", "v1/openloop.json", "ml_model_A.pkl", "openloop_A.json", "ml_model_B.pkl", "openloop_B.json",
          "common.py", "closed_loop.py", "train_eval.py", "generate_step.py"]


def sha(path, base=None):
    return hashlib.sha256(((base or C.HERE) / path).read_bytes()).hexdigest()


def build_index():
    rows = [json.loads(x) for x in (C.HERE / "rollouts.jsonl").read_text().splitlines()]
    n = 0
    with open(C.HERE / "sessions_rollout.jsonl", "w") as f:
        for r in rows:
            tag = r["controller"].replace(",", "").replace(" ", "_")
            name = f"{'_'.join(f'{x:g}' for x in r['combo'])}_T{int(r['temp'])}_{tag}.npz"
            if not (C.HERE / "data_rollout" / name).exists():
                continue
            assert tuple(r["combo"]) in C.TRAIN, "rollouts must come from training cells only"
            f.write(json.dumps({"combo": r["combo"], "temp": r["temp"], "idx": tag, "profile": f"rollout:{r['controller']}",
                                "file": name, "held_out": False, "steps": None}) + "\n")
            n += 1
    print(f"indexed {n} rollouts from {len(rows)} runs")


def freeze():
    import closed_loop as CL

    if (C.HERE / "freeze_manifest.json").exists():
        sys.exit("freeze_manifest.json already exists; the historical manifest is never overwritten")

    manifest = {
        "frozen_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sha256": {p: sha(p) for p in FROZEN},
        "final_combos": CL.FINAL_COMBOS, "final_temps": list(CL.FINAL_TEMPS),
        "controllers": CL.preset("final"),
        "stopping": {"target_true_soc": C.target_true_soc(), "max_s": CL.MAX_S, "stall_s": CL.STALL_S,
                     "grid_c_max": C.C_MAX, "v_max": C.V_MAX, "dt_s": C.DT},
        "calibration_rule": "margin = 15 mV + p99 of (estimate - DFN truth) while truth < 50 mV, on the calibration set "
                            "named in openloop_<tag>.json (out-of-fold for ml); computed before the final family existed",
        "note": "final family = interior parameter levels and 5 C never used for fitting, calibration, or development",
    }
    (C.HERE / "freeze_manifest.json").write_text(json.dumps(manifest, indent=1))
    print("frozen:", json.dumps(manifest["sha256"], indent=1))


def verify():
    """Historical audit: archived frozen artifacts vs the original manifest. True only if every file exists and matches."""
    m = json.loads((C.HERE / "freeze_manifest.json").read_text())
    base = C.HERE / ARCHIVE
    missing = [p for p in m["sha256"] if not (base / p).is_file()]
    bad = [p for p, h in m["sha256"].items() if p not in missing and sha(p, base) != h]
    if missing:
        print("MISSING from the archive:", missing)
    if bad:
        print("CHANGED since freeze:", bad)
    if not (missing or bad):
        print(f"historical audit OK: all {len(m['sha256'])} archived artifacts match the manifest frozen {m['frozen_at']}")
    return not (missing or bad)


def verify_main():
    """Command-line entry point: exit status 1 on any hash mismatch, so CI fails loudly."""
    if not verify():
        print("Verification Failure: Hash discrepancies found.")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    {"index": build_index, "freeze": freeze, "verify": verify_main}[sys.argv[1]]()
