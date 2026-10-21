# Provenance log

What changed between releases, what was re-verified, and what could not be. The original freeze manifest
(`analysis/estimator/freeze_manifest.json`) is never edited. Discrepancies are recorded here, not waived.

## v1.0.1 (2026-10-21): reporting and verification patch

**No new simulations.** Every number in this release was recomputed from the archived v1.0 run logs
(`hexa-blade-run-logs-v1.0.zip` on the [v1.0 release](https://github.com/DarkThyme/hexa-blade-na-ion-pack/releases/tag/v1.0),
SHA-256 `cef807c65dd04241fdaab140b5bf80a10ddcef8834123c42391ce1071ef54392`). The logs were checksummed before and
after the reports were rebuilt and are unchanged. Every safety metric and the verdict ("not yet") are unchanged. Only
medians and labels changed.

### 1. Two separate verification checks

| Check | What it verifies | Command | Where it runs |
|---|---|---|---|
| **Historical experiment audit** | The archived frozen artifacts in `analysis/estimator/frozen_v1.0/` against the original `freeze_manifest.json` | `cd analysis/estimator && python freeze.py verify` | `release-check` workflow, every push to `main` and every tag |
| **Release integrity** | This release's artifacts (estimator code, models, calibration, reports and figures, the archive, the project report PDF/DOCX, this log) against `release_manifest_v1.0.1.json` | `python release_integrity.py verify release_manifest_v1.0.1.json` | `release-check` workflow, on the release commit and on its tag |

Both exit with status 1 and print `Verification Failure: Hash discrepancies found.` on any mismatch or missing file.
Their exit behaviour is unit-tested with temporary manifests (`tests/test_reporting_patch.py`); the full artifact
checks run in release CI. The release manifest hashes text files with CRLF normalised to LF, so Windows and Linux
checkouts of the same commit agree. The archive is hashed byte for byte.

### 2. Historical audit: what happened to `closed_loop.py`

On the v1.0 tree, the old `freeze.py verify` (which then checked the *working* files) reported
`CHANGED since freeze: ['closed_loop.py']`. There were two causes.

1. **A post-freeze edit.** While the v1.0 repository was being prepared, after the final-family run had finished and
   been reported, three lines on the *default, no-preset* path of `closed_loop.py` were changed to read and write
   `v1/openloop.json`, `v1/ml_model.pkl` and `v1/closed_loop.jsonl`. The `A_dev`, `rollout`, `B_dev` and `final`
   presets, controllers, margins, cases and stopping rules were not touched, and no result was produced with the
   edited file. The v1.0 tag is not rewritten.
2. **Line endings.** Several frozen files were hashed with CRLF line endings on Windows, but git stored them as LF, so
   a fresh checkout did not reproduce their bytes even where the code was unchanged.

**What was recoverable.** Every one of the 10 frozen files was recovered with its exact hash.
- Nine are the original bytes from the working tree the freeze was taken from.
- `closed_loop.py` was **reconstructed** by reverting the three documented lines and keeping CRLF line endings. The
  result matches the frozen SHA-256 (`6a2e17c5…1c3c`) exactly. This is a reconstruction, not a preserved copy; the hash
  match is the evidence that it is byte-identical.

All 10 are archived in `analysis/estimator/frozen_v1.0/`, stored byte for byte (`.gitattributes`: `-text`). The
historical audit of that archive passes. Nothing was unrecoverable. The working copies one level up are no longer
required to match the old manifest. In v1.0.1 they equal the frozen code but are stored with normal line endings.

`freeze.py freeze` now refuses to overwrite an existing `freeze_manifest.json`.

### 3. Median among completed runs

`analysis/estimator/report.py` took the median as `t[len(t) // 2]`, the *upper* middle value for an even count of
completed runs. v1.0.1 uses `statistics.median`, which gives the mean of the two middle runs for an even count, and
`None` when no run completed. Only records whose Boolean `completed` field is `True` enter the median; unfinished runs
(time limit, stalled, plant solver failure) carry no time. Odd counts are unchanged. The values that changed are below
(10 → 90 % time, median among completed runs):

| Table | Controller | v1.0 | v1.0.1 (completed N/M) |
|---|---|---|---|
| Development holdout, v1 | map, 15 mV | 81 min | 61 min (14/14) |
| Development holdout, v1 | oracle feedback (reference) | 71 min | 60 min (10/14) |
| Development holdout, v1 | observer, 15 mV | 61 min | 57 min (14/14) |
| Development holdout, v1 / network versions | ml, calibrated 24 mV | 62 min | 59 min (10/14) |
| Development holdout, network versions | ml A, calibrated 24 mV | 86 min | 71 min (10/14) |
| Development holdout, network versions | ml B, 15 mV | 74 min | 61 min (10/14) |
| Development holdout, network versions | ml B, calibrated 20 mV | 83 min | 68 min (10/14) |
| Final family | map, 15 mV | 81 min | 70 min (16/16) |
| Final family | ml B, calibrated 20 mV | 87 min | 78 min (16/16) |
| Final family | ml v1, calibrated 24 mV | 101 min | 70 min (16/16) |
| Final family | ml B, 15 mV | 79 min | 70 min (16/16) |

Unchanged: the oracle (65 min, 15/16), the calibrated observer (139 min, 12/16) and the standard 40 mV map
(127 min, 8/16).

### 4. Labels and terminology

- Tables now read **"Completed: N/M; median among completed runs"**, with N and M taken from each row, plus a separate
  "Incomplete runs" column giving the statuses.
- **"Maximum estimated cumulative duration below 0 mV, based on two-second endpoint samples"** replaces "longest below
  0 mV", which suggested a single continuous interval. The endpoint-sample note appears only on that time metric.
- **"Maximum across per-run p99 optimistic errors"** replaces "optimistic error p99".
- Early phases: the anode-aware strategy is described as **ideal-information control** (it reads the modelled anode
  potential directly). "Model-based anode observer" was replaced only where it wrongly described that strategy; the
  observer actually implemented in Phases 11–12 keeps its name.
- The aging metric is a **modelled SEI exposure index**.
- The 22 min standard-BMS figure is described as consistent with real cars' published charging curves, not
  "real-world validated".
- The case-study summary speaks of **violations of selected modelled constraints**, not "safety bounds".
- The ablation and matrix reports were rebuilt from their saved results for the renamed index; no numbers changed.
  `results/phase3_tradeoff.png` still carries the old axis label, because it is regenerated only by rerunning
  `phase3_tradeoff.py`.

### 5. Added

- `analysis/predictor/PROTOCOL.md`: the optional next experiment, with its partitions, gate and final closed-loop
  evaluation fixed before any code exists. Not started.
