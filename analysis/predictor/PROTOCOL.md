# Bounded experiment protocol: next-interval anode-potential predictor

**Status: not started.** This experiment is optional and comes after mentor feedback on v1.0.1. A clearly documented
"no improvement" is an acceptable outcome. This file fixes the data partitions, the baseline, the go/no-go gate and
the final closed-loop evaluation **before any predictor code or result exists**. Changing any of it after development
results have been looked at must be recorded in `../../PROVENANCE_LOG.md` as a protocol deviation.

## Question

Given only the measurement history (voltage, current, temperature, declared initial SOC) and a candidate current for
the next 2 s control interval, can a reduced-order electrochemical predictor estimate the end-of-interval modelled
anode potential well enough that a controller using it charges a single sodium-ion cell faster than the calibrated
observer **without more violations of the selected modelled constraints** (true modelled anode ≥ 15 mV; plating-risk
criterion 0 mV) on cells it has never seen?

## Scope

- **Single cell only.** The plant is the full PyBaMM sodium-ion DFN (`ev_pack/na_cell.py`), a higher-fidelity
  simulation reference, not experimental truth. No pack simulation.
- Cold, plating-limited conditions. The causality rules of the estimator study apply unchanged: every candidate is
  fed to the predictor explicitly, inputs are measurements available before the decision, and nothing is read from
  the DFN before choosing.
- Code lives in `analysis/predictor/` and reuses `analysis/estimator/common.py` by import only. It writes nothing into
  `analysis/estimator/`.

## 0. Feasibility check (before any fitting)

Show analytically and with nominal-cell runs that the chosen reduced structure (for example a single-particle model
with electrolyte and a lumped anode surface-concentration state) can represent the anode-potential response that
matters here: a step up, a step down, a rest and a smooth taper, at 0 °C and 10 °C. Report its open-loop error against
the DFN on the nominal cell. If it cannot follow a sharp cut or a rest to within the observer's open-loop error on
the nominal cell, stop and document this.

## 1. Three separate partitions

| Partition | Parameter cases | Temperatures | Used for |
|---|---|---|---|
| **Training** | the 20 `C.TRAIN` combinations | 0, 10 °C | fitting predictor parameters only (open-loop steps, rests, smooth tapers; optional training-cell rollouts) |
| **Calibration / development** | the 7 `C.HELD_OUT` combinations and the 8 v1.0 final-family combinations (both already inspected, so neither can serve as a blind test) | 0, 5, 10 °C | choosing settings, calibrating the margin (15 mV + p99 optimistic error while truth < 50 mV, out-of-fold by parameter case), applying the gate below |
| **Untouched final test** | 8 new interior combinations: hard-carbon diffusivity ×{0.7, 1.45} × anode reaction rate ×{0.6, 1.7} × electrolyte conductivity ×{0.78, 1.22} | 2.5 and 7.5 °C (never used) | one evaluation of the frozen candidate and the frozen baseline; no tuning afterwards |

None of the final-test levels or temperatures appear anywhere in v1.0 or v1.0.1. The final-test cases must not be
simulated, even with another controller, until the freeze in step 4.

## 2. Baseline

The calibrated observer from the estimator study (`ObserverEstimator`, lag and margin recalibrated on the training
partition with the same rule), run under the **same controller rules** as the candidate: 2 s decisions, the same 69
candidate currents up to 3.4C, 4.3 V limit, the same stopping rules (90 % true SOC, 5 min stall, 2.5 h limit).

## 3. Go/no-go gate (fixed now, applied on the calibration/development partition)

All five must hold. If any fails, the experiment stops there and the limitation is documented.

| # | Criterion | Threshold |
|---|---|---|
| G1 | Open-loop p99 optimistic one-step error while truth < 50 mV | ≤ 0.5 × the observer's, on the same sessions |
| G2 | Persistence: longest run of consecutive decisions with optimistic error > 5 mV | ≤ the observer's |
| G3 | Runtime per decision (all 69 candidates, one CPU core) | ≤ 50 ms, so it fits well inside the 2 s interval |
| G4 | Closed loop at its calibrated margin: runs below 0 mV; runs below 15 mV | 0; ≤ the observer's count |
| G5 | Closed loop: completed runs; charging time on cases both complete | ≥ the observer's; median per-case time ratio (candidate / observer) ≤ 0.85 |

## 4. Freeze, then one final closed-loop evaluation

If the gate passes: write a new, separately named manifest `freeze_manifest_predictor.json` (SHA-256 of the
predictor parameters, calibration, scripts, final cases and stopping rules). Then run the final test once, candidate
and baseline under identical controller rules, and report for each:

- **true modelled constraint violations:** runs below 15 mV and below 0 mV, worst modelled anode, maximum estimated
  cumulative time below 0 mV (two-second endpoint samples);
- **charging completion:** completed N/M, with the incomplete runs' statuses;
- **charging time:** median among completed runs, and the paired per-case comparison;
- prediction accuracy (max across per-run p99 optimistic errors), error persistence and runtime as secondary results.

Prediction accuracy, persistence and runtime alone cannot show that charging improves. The closed-loop results decide.

## Time box

Two weeks part-time. If the feasibility check or the gate fails, or the time box runs out, stop and write up what was
learned.
