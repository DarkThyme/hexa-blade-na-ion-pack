# HEXA-BLADE: fast-charging a sodium-ion 110 kWh, 800 V EV pack (simulation study)

**Live 3D viewer:** https://darkthyme.github.io/hexa-blade-na-ion-pack/ (runs in the browser, no install) ·
**Project report:** [docs/Hexa-Blade_Project_Report.pdf](docs/Hexa-Blade_Project_Report.pdf) ([.docx](docs/Hexa-Blade_Project_Report.docx))

HEXA-BLADE asks how fast a sodium-ion EV pack could charge from 10 to 80–90 % without the anode dropping below a
modelled plating-risk criterion, and how much of that speed survives once the battery management system (BMS) has to
work from imperfect measurements. Everything here is **simulation only**: the reference model is PyBaMM's sodium-ion
Doyle–Fuller–Newman (DFN) model, a higher-fidelity simulation reference, not experimental truth.

What is in the repository:

| Part | Where | What it does |
|---|---|---|
| Cell physics | `ev_pack/na_cell.py`, `ev_pack/charge_maps.py` | Sodium-ion DFN cell (Chayambuka 2022 NVPF / hard-carbon parameters, scaled to a 147 Ah EV cell) and the DFN-derived charge maps |
| Pack simulator | `ev_pack/pack_sim.py`, `ev_pack/architectures.py` | Two 110 kWh pack architectures (hexagonal *blade* cell-to-pack and *module*), per-cell electro-thermal model, cold plates, two BMS strategies |
| Analyses | `analysis/*/REPORT.md` | Real-fleet calibration, benchmark against real 800 V cars, a 336-session test matrix, Shapley ablation, model card, imperfect-information stress test, measurement-only estimator study |
| 3D viewer | `viewer/` (Rust + Bevy → WebAssembly) | Glass car body over the pack; every cell fills with green liquid as its SOC rises, or is coloured by temperature |
| Charging Lab | `app.py` (Python, NiceGUI) | Local interactive app: charger, ambient, preconditioning, live charts |
| Report | `docs/` | Word / PDF write-up with every key value and the references; rebuilt by `docs/report_builder/` |

Headline results (details and caveats in the phase sections below):
- **350 kW, 25 °C, 10 → 90 %:** about 21 min with a standard BMS (in line with the best real 800 V cars) and
  **15.3 min with an anode-aware BMS** that holds a 15 mV modelled anode margin. The 15.3 min is an
  *ideal-information simulation reference*, not demonstrated production-BMS performance.
- **Where the gain comes from (Phase 9):** 81–95 % from the smaller plating margin (40 → 15 mV), the rest from warmer preconditioning.
- **With imperfect information (Phases 11–12):** errors in the anode estimate eat most of the gain in cold, plating-limited charging.
  No measurement-only estimator tested here held the 15 mV target on unseen cells while keeping the speed-up; the verdict is "not yet".

## Quick start

Tested with Python 3.12 on Windows (paths below use `.venv/Scripts/`; on Linux / macOS use `.venv/bin/`).

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m pytest -q                 # unit and regression tests
.venv/Scripts/python app.py                       # Charging Lab at http://localhost:8080
.venv/Scripts/python -m ev_pack.export            # scenario data for the 3D viewer -> web_data/
```

The 3D viewer build is described in [viewer/README.md](viewer/README.md); the public copy is rebuilt and deployed to
GitHub Pages by `.github/workflows/pages.yml` on every push to `main`.

**Not in the repository:**
- *Third-party datasets* (see [Data sources and licences](#data-sources-and-licences)). Download them into `external_data/` to rerun Phases 5 and 5b.
- *Raw run logs and recorded sessions* (`*.jsonl`, the large `results.json` files, `analysis/estimator/data*/`). They are
  attached to the [v1.0 release](https://github.com/DarkThyme/hexa-blade-na-ion-pack/releases/tag/v1.0) as
  `hexa-blade-run-logs-v1.0.zip`; unzip it in the repository root to rebuild the reports without rerunning the simulations,
  or rerun them with the commands in each phase (all runners are resumable).
- *Generated output:* `web_data/` and the viewer build (`viewer/dist/`).

## Development log

The project grew in phases. Phases 1–2 are the starting point on a lithium-ion cell (LG M50 21700, PyBaMM `Chen2020`
parameter set); from Phase 3 on everything is the sodium-ion pack.

## Phase 1 — Baseline CC-CV (done)

```bash
.venv/Scripts/python phase1_baseline.py                       # SPMe model, 0.5/1/2/3C
.venv/Scripts/python phase1_baseline.py --model DFN --c-rates 1 2   # full physics model
.venv/Scripts/python -m pytest -q                              # physics sanity tests
```

Outputs in `results/`:
- `phase1_cccv_comparison.png` — current, voltage, temperature, SOC vs time
- `phase1_summary.csv` — time to 80%, peak temperature, limit violations per C-rate
- `cccv_<rate>C.csv` — raw time series

### Baseline results (SPMe, 25 °C ambient, natural-convection cooling)

| CC rate | 10→80% time | Temp at 80% | Peak temp | Over 45 °C? |
|---|---|---|---|---|
| 0.5C | 84.0 min | 29.0 °C | 29.2 °C | no |
| 1.0C | 43.2 min | 36.3 °C | 37.6 °C | no |
| 2.0C | 26.5 min | 47.3 °C | 60.3 °C | **yes** |
| 2.5C | 25.6 min | 53.1 °C | 73.4 °C | **yes** |
| 3.0C | 25.3 min | 53.6 °C | 73.9 °C | **yes** |

Takeaways:
- Going past ~2C buys almost nothing: the cell hits 4.2 V within ~1 min, so CV mode clamps current to ~10 A anyway — just with a hotter spike.
- 1C is safe but slow; 2C is fast but overheats. **The optimum lies between, with a shaped (non-constant) current profile** — that's Phase 2.
- SOC is measured against the 5 Ah nominal capacity; the model cell holds slightly more, so "full" reads ~1.01.
- At high C-rates the solver can fail partway; the script retries with stricter solvers and flags `completed=False` if all fail.

### Concepts used
- **CC-CV**: constant current until the voltage hits 4.2 V, then hold the voltage while the current tapers off.
- **C-rate**: 1C = the current that fully charges the cell in ~1 hour (5 A for this cell). 2C = 10 A.
- **SPMe / DFN**: electrochemical models. DFN (Doyle-Fuller-Newman) is the full physics model; SPMe is a faster approximation.
- **Lumped thermal**: the cell is treated as a single temperature, heated by I²R + reaction heat and cooled to ambient air.

## Phase 2 — Optimized multi-step charging (done)

```bash
.venv/Scripts/python phase2_optimize.py                 # 5 SOC bins, 100 trials (~10 min)
.venv/Scripts/python phase2_optimize.py --n-steps 6 --n-trials 150
```

The 10→80% window is split into equal SOC bins, each with its own constant current (4.2 V cap, CV fallback). Optuna (Bayesian TPE) searches the currents to minimize time-to-80% under two limits:
- **Thermal**: peak cell temperature ≤ 45 °C
- **Lithium plating**: anode potential vs Li/Li⁺ at the separator ≥ 0 V. Below 0 V, metallic lithium can deposit on the graphite — the main cause of fast-charge damage.

| | Profile (C per SOC bin) | 10→80% | Peak temp | Min anode potential |
|---|---|---|---|---|
| Best safe CC-CV | 0.80 flat | 52.7 min | 33.9 °C | +2.2 mV |
| **Optimized** | 1.43 / 1.41 / 1.39 / 1.03 / 0.92 | **35.8 min** | 44.0 °C | +0.8 mV |

**32% faster (−16.9 min) with both limits respected.** Takeaways:
- **Plating, not heat, is the binding limit** for this cell at 25 °C: plain CC-CV plates above 0.8C.
- Plating risk peaks around 65–75% SOC (graphite's final lithiation stage), so the optimum is "fast early, step down before ~52% SOC" — the profile rides just above 0 V.
- Outputs: `results/phase2_optimized_vs_cccv.png`, `results/phase2_optimization_history.png`, `results/phase2_best_profile.json`, `results/phase2_trials.csv`.

## Phase 3 — Hexa-Blade 110 kWh sodium-ion EV pack + 3D Charging Lab

```bash
.venv/Scripts/python app.py          # open http://localhost:8080
.venv/Scripts/python -m ev_pack.simulate   # all six chargers in the terminal
```

**Pack design** (`ev_pack/spec.py`, all assumptions editable):

| | |
|---|---|
| Chemistry | Sodium-ion, NVPF cathode / hard-carbon anode (OCV data: Chayambuka 2022 via PyBaMM) |
| Cells | 200 hexagonal-prism blades, 46 mm across flats × 1.40 m, 150 Ah, 3.67 V, 551 Wh |
| Layout | 200s1p cell-to-pack, 4-layer honeycomb × 50 columns, blades span the pack width (Blade-style) |
| Electrical | 110 kWh, 735 V nominal / 840 V max (800 V class, like Taycan / E-GMP) |
| Size / mass | 2.47 × 1.50 × 0.21 m, ~984 kg (Na-ion ≈140 Wh/kg → SUV floor) |
| Cooling | Corrugated top + bottom cold plates, 30 L/min, 20 kW chiller, 7 kW heater |

**Simulation** (`ev_pack/simulate.py`): every cell has its own equivalent-circuit model (OCV + R0 + RC, Arrhenius temperature dependence) and thermal node, conducting to its honeycomb neighbours and the cold plates. Coolant warms front → rear. The BMS picks the lowest of: charger power, charger current, cell C-rate cap, **Na-plating guard** (anode ≥ +15 mV vs Na), voltage limit (CV), thermal derate.

Results at 25 °C, preconditioned, 10 → 80%:

| Charger | Time | Peak power | Hottest cell |
|---|---|---|---|
| 7 kW AC | 11 h 53 min | 6.5 kW | 25.0 °C |
| 15 kW AC | 5 h 30 min | 14.1 kW | 25.2 °C |
| 50 kW DC (400 V station, boosted) | 1 h 38 min | 47.5 kW | 26.4 °C |
| 125 kW DC | 39 min | 121 kW | 29.9 °C |
| 250 kW DC | 20 min | 244 kW | 35.2 °C |
| 350 kW DC | **15 min** | 341 kW | 38.3 °C |

At −10 °C without preconditioning, the 350 kW session slows to 25.5 min because the sodium-plating guard limits the current. Preconditioning brings it back to 15 min.

**Charging Lab UI** (`app.py`, pure Python via NiceGUI): an interactive 3D pack with every cell colored live by temperature or SOC, camera presets, an exploded view and a vehicle context. You can choose a charger, start/stop SOC, ambient temperature and preconditioning. It also shows live stats with the active BMS limit, power/SOC and thermal charts, a comparison of all six chargers with charge curves, and pack-design and physics explainer tabs.

## Phase 4 — Physics-based Na-ion model, two architectures, charging past 80%

```bash
.venv/Scripts/python -m ev_pack.na_cell        # DFN cell sanity check at several C-rates
.venv/Scripts/python -m ev_pack.charge_maps    # rebuild DFN charge maps (~4 min)
.venv/Scripts/python phase3_tradeoff.py        # pack temperature vs speed vs aging
.venv/Scripts/python -m ev_pack.export         # 72 scenarios -> web_data/ for the 3D viewer
```

**Cell model (`ev_pack/na_cell.py`).** PyBaMM's sodium-ion DFN with the Chayambuka 2022 NVPF / hard-carbon
thermodynamics, scaled from a 3 mAh lab coin cell to a 150 Ah EV cell. The changes, all documented knobs in `CellDesign`:
- Reaction kinetics ×50, cathode diffusion ×10, hard-carbon diffusion ×40, particle 2.5 µm. The lab cell reaches only ~30% at 1C. Kinetics were the main bottleneck.
- Thinner, power-optimised electrodes (48 / 51 µm instead of 64 / 68).
- Arrhenius temperature dependence added to diffusion, kinetics and electrolyte (the base set has none). Activation energies are within published Na-ion ranges.
- **4.3 V cut-off**: NVPF's second plateau sits at ~4.1–4.2 V, so a 4.2 V limit forces CV from ~55% SOC.

Calibration check (plating onset with a 15 mV margin), which behaves like a commercial fast-charge cell:

| | 2C | 3C | 4C |
|---|---|---|---|
| 25 °C | 68% SOC | 62% | 55% |
| 45 °C | none | none | 72% |
| 0 °C | 43% | 35% | 28% |

**Charge maps (`ev_pack/charge_maps.py`).** 96 DFN runs (8 temperatures × 12 C-rates) give overpotential and
anode-vs-Na potential as functions of (T, C-rate, SOC). The pack simulator interpolates these for every cell at every step.
This is how real BMS charge maps are derived.

**Architectures (`ev_pack/architectures.py`).** Both are 110 kWh gross / ~102 kWh usable (3% / 4% buffers, like the Taycan's 105 / 97 kWh), ~800 V class, and every cell touches a cold plate. The cells were enlarged in October 2026 (46 mm → 64 / 58 mm) so charge is easier to see in the viewer:
- **blade**: 204 hexagonal blades (64 mm × 0.46 m, 147 Ah), 204s1p: 2 layers × 34 lanes × 3 blades end to end, cold plates bottom / middle / top so every blade touches two. 2.34 × 1.50 × 0.19 m. The blade count can't drop much below 200, because each blade is its own series cell (204 × 4.3 V = 877 V, under the 920 V charger limit).
- **module**: 800 upright hexagonal cells (58 mm × 175 mm, 37 Ah), 20 modules of 10s4p on a bottom cold plate. Parallel cells share current by conductance, so warm cells work harder. 2.32 × 1.57 × 0.23 m.

**BMS strategies (`ev_pack/pack_sim.py`).**
- *standard*: a typical modern EV. 40 mV plating margin, preconditioned to 30 °C.
- *optimized*: a model-based anode observer with a 15 mV margin, preconditioned to 35 °C.

The plating margin is enforced exactly on every cell, every step. An SEI aging index (equivalent hours at 25 °C) makes the cost of heat visible.

**Result (350 kW, 25 °C; calibrated model, see Phase 5):**

| Pack | BMS | 10→80% | 10→85% | 10→90% | Hottest cell | Aging index |
|---|---|---|---|---|---|---|
| Blade | Standard | 16.0 min | 18.7 min | 21.4 min | 41.8 °C | 0.94 |
| Blade | **Anode-aware, 35 °C** | **13.2 min** | **14.2 min** | **15.3 min** | 47.4 °C | **0.86** |
| Module | Standard | 18.3 min | 21.7 min | 26.5 min | 41.6 °C | 1.01 |
| Module | **Anode-aware, 35 °C** | **13.3 min** | **14.4 min** | **15.4 min** | 47.3 °C | **0.80** |

Effect of the larger cells (vs the 46 mm design): the anode-aware times are unchanged, and cells run about 1 °C hotter (the heat path to the plate is longer). The standard-BMS module pack got much faster (33.6 → 26.5 min to 90%): with 4 parallel cells per group instead of 7, current sharing is more even, so the coldest cell holds back less of its group.

90% arrives about 2 minutes after 80% instead of 5–8. The charger becomes the limit, not the battery, and aging is *lower* because the session is shorter. With the standard BMS the module pack suffers more than the blade pack: parallel cells share current unevenly, and the coldest cell sets the plating limit for its whole group.
`phase3_tradeoff.py` shows that preconditioning beyond ~35 °C buys seconds but costs 30–75% more aging. Most of the gain comes from the anode-aware BMS's smaller plating margin, not from heat (Phase 9 measures the split).

Caveats: the maps come from constant-current DFN runs, so the history effects of varying current are approximated (with a 15 s relaxation lag). Thermal conductances and activation energies are engineering estimates.

## Phase 5 — Real-world calibration (EVBattery, 1.28 M charging snippets)

See [analysis/evbattery/REPORT.md](analysis/evbattery/REPORT.md). The dataset's pickle files are read by a restricted loader that allows only numpy data types and was tested to refuse code execution. The real fleet's behaviour led to three calibrations: tab, weld and busbar resistance (pack ≈ 72 mΩ), ±25% cold-plate contact variation (cell ΔT now 1.5–4 °C, against a real 3.3–3.6 °C), and 1% initial SOC imbalance.

Key real-world facts:
- Fleet fast charging tapers at **median 77% SOC**.
- Cars later flagged for plating or fire showed **~3× the cell-voltage spread above 80% SOC** (p90 127 mV vs 45 mV).
- Na-ion's flat OCV near full charge hides that spread in cell voltage, so a Na-ion BMS needs model-based imbalance detection.

## Phase 5b — Benchmark vs real 800 V cars

See [analysis/realcars/REPORT.md](analysis/realcars/REPORT.md). The real curves are digitized Fastned / InsideEVs charging curves from Morin et al. 2025 (Figshare, **CC BY 4.0**). They cover the Taycan, e-tron GT, Ioniq 5 / 6, EV6 and Macan Electric.

- **Standard BMS ≈ real cars.** On a 300 kW charger the blade pack takes 16.8 min for 10→80% and 21.7 min for 10→90%. The best real 800 V cars take 15.2–17.2 and 20.9–29 min.
- **Anode-aware BMS:** 16.8 min for 10→90%, about 4 min faster than any real car. The result holds even with 30× slower hard-carbon plateau diffusion. The Phase 9 ablation shows 81–95% of its advantage comes from the 15 mV plating margin and the rest from a warmer pack (except in a 45 °C hot-soak at 350 kW, where relaxed thermal limits also matter); a production BMS would have to hold that margin across aging, sensor error and cell spread. Treat it as a best case; the real-world-validated figure is about 22 min.

## Phase 6 — 3D showcase (Rust + Bevy → WebAssembly)

[viewer/README.md](viewer/README.md). The viewer is a static web app that reads `web_data/` (contract: [docs/viewer_data_contract.md](docs/viewer_data_contract.md)). It shows a glass car body with both drive units (one motor per axle) and both pack architectures. In the default **Charge (fill)** view every cell is a hollow glass shell that fills with green liquid as its own SOC rises; the **Temperature** view colours cells by heat. You can switch charger, conditions and BMS strategy. It also gives live readouts (including what limits the current), charts, and the strategy comparison.

## Phase 8 — Full test matrix (336 sessions)

See [analysis/matrix/REPORT.md](analysis/matrix/REPORT.md). Every pack × charger (7 kW AC to 350 kW DC) × ambient (−20 to 45 °C) × preconditioned or not × BMS strategy, 10 → 90 %. All 336 sessions pass all 10 checks (plating margin, temperature cutoff, charger power / current / voltage, energy balance, numerical sanity), and 664 cross-run comparisons are consistent. The matrix found one real bug: cold-soaked packs briefly drew 3–7 % more than the charger's rating, because the power limit used the previous step's voltage. That is now fixed and covered by a regression test.

```bash
.venv/Scripts/python analysis/matrix/run_matrix.py --workers 8   # ~4 min, resumable
.venv/Scripts/python analysis/matrix/report.py
```

The whole project, written up for a reviewer with all key values and references: [docs/Hexa-Blade_Project_Report.docx](docs/Hexa-Blade_Project_Report.docx) (PDF copy alongside).

## Phase 9 — Ablation: what makes the anode-aware BMS faster

See [analysis/ablation/REPORT.md](analysis/ablation/REPORT.md). The two strategies differ in four setting groups (plating margin, preconditioning / coolant temperature, thermal limits, C-rate cap), so a full 2⁴ factorial (16 configurations × 20 cases = 320 runs: both packs, 250 / 350 kW, 25 °C preconditioned, 0 / −20 °C cold-soaked, 35 / 45 °C hot-soaked) splits the speed-up with Shapley attribution. In 18 of 20 cases:
- **Plating margin 40 → 15 mV: 81–95% of the gap.**
- **Warmer preconditioning / coolant (35 / 33 °C): the remaining 5–19%.**
- **Thermal limits and the 4C → 5C cap: 0.** The 4C cap never binds (peak 3.4C). Crossing the 45 °C derate start isn't the same as being limited by it: at 25 °C preconditioned the pack only passes 45 °C after ~64% SOC, when the charger's power limit already holds current near 2.7C, below what the standard derate allows; in cold-soaked cases the plating guard holds the current.

**Exception: 350 kW, hot-soaked at 45 °C.** The standard BMS spends 59% (blade) / 42% (module) of the session derating, so the relaxed thermal limits explain 51% / 25% of the gain (about 2.9 min) and the 5C cap 13% / 7% (the derated limit scales with the C-rate cap); the margin explains 36% / 62%. The relaxed limits let cells reach 53 °C instead of 50 °C. At 250 kW, 45 °C, thermal limits again contribute 0.

So a production version could keep the standard thermal limits and current cap and give up about 3 minutes on hot days at 350 kW; the open question is still whether a 15 mV margin is achievable.

```bash
.venv/Scripts/python analysis/ablation/run_ablation.py --workers 8   # ~6 min, resumable
.venv/Scripts/python analysis/ablation/report.py
```

## Phase 10 — Model card: do the lookup maps match the full DFN?

See [analysis/validation/MODEL_CARD.md](analysis/validation/MODEL_CARD.md). One cell is driven with identical current profiles in the map model the pack simulator uses and in the full PyBaMM sodium DFN (model-to-model verification, not experimental validation):
- **Smooth BMS tapers (both strategies, 25–45 °C): close.** The map is at most 1.5 mV optimistic for anode-aware sessions and 5.5 mV for standard ones, so a cell held at the 15 mV margin sits at about 13.5–15 mV by the DFN.
- **Sharp current cuts, rests and pulses: the map is optimistic by up to 50–60 mV at 25 °C** (more at 0 °C), because it reads the anode potential instantly while the real particles keep the previous current's concentration gradient. Controllers that pulse, pause or step sharply must be checked with full DFN runs.
- Beyond the SOC each constant-current map run reached, the map holds the anode flat (optimistic). The pack BMS never goes there (its voltage limit forbids it), but any new controller using the maps must keep that guard.

**How to quote the headline:** 15.3 min is an *ideal-information simulation reference* (direct access to the modelled anode potential, no estimation error or delay), not demonstrated production-BMS performance.

```bash
.venv/Scripts/python analysis/validation/dynamic_check.py --workers 6   # ~1 min
.venv/Scripts/python analysis/validation/report.py
```

## Phase 11 — Stress test: imperfect information

See [analysis/robustness/REPORT.md](analysis/robustness/REPORT.md). `simulate(..., info=InfoErrors(...))` splits the BMS into a **controller** that sees only biased / noisy / delayed estimates (no true-anode clamp), a **plant** on the true state, and an **evaluator** that measures violations on the true state. Defaults are unchanged (regression tests). 664 runs: both packs, 250 / 350 kW, 25 °C preconditioned and 0 °C cold-soaked, 14 assumed error scenarios, controller margins 15–40 mV, plus the **standard BMS facing the same errors** (776 runs). Thresholds: below 15 mV = margin violation; below 0 mV = the model's plating-risk criterion (not evidence of deposited sodium). The combinations are *assumed* moderate / harsh, not measured error distributions.

| Condition | Result |
|---|---|
| Warm, charger-limited | The tested errors have limited effect: the plating guard rarely controls the current. |
| Cold-soaked, plating-limited | Errors directly reduce the true margin; compensating removes most of the gain from the smaller margin. |
| Single assumed errors | A 20–30 mV margin protects 15 mV, and the anode-aware controller stays several minutes faster than the standard BMS facing the same error. |
| Assumed moderate combination | Same errors, same 40 mV setting: anode-aware still 1.1–2.3 min faster, but both end slightly below 15 mV (14.4 vs 14.3 mV worst), so this is same-setting, not protection-matched. The difference is plausibly from its temperature settings (not tested under errors). |
| Assumed harsh combination | Neither controller holds 15 mV at 40 mV (both cross 0 mV briefly); the fallback margin (66 mV) holds it but the charge stalls before 80%: constraint-respecting, task-incomplete. |

- The fallback rule (margin = 15 mV + sum of the declared errors' equivalents) was **empirically successful on the tested combinations**, not a general guarantee.
- The reduced cell model's own error is profile-dependent: ~1.5 mV pointwise for smooth tapers, 4–10 mV around jumpy currents, 50–60 mV after sharp cuts; session minima moved ≤ 1.4 mV in the DFN replays.

What this establishes: a controller–plant–evaluator benchmark that quantifies how estimation uncertainty changes charging performance across charger-limited and plating-limited conditions. Next bounded experiment: can a measurement-only estimator (model-based observer vs small history-based ML estimator) recover performance in cold, plating-limited conditions while holding the declared modelled protection target, judged on held-out sessions?

```bash
.venv/Scripts/python analysis/robustness/run_robustness.py --workers 8   # ~7 min, resumable
.venv/Scripts/python analysis/robustness/dfn_recheck.py
.venv/Scripts/python analysis/robustness/report.py
```

## Phase 12 — Measurement-only anode-potential estimators

See [analysis/estimator/REPORT.md](analysis/estimator/REPORT.md). Question: can an estimator that sees only voltage, current, temperature and the declared initial SOC recover charging performance in cold, plating-limited conditions while holding the modelled 15 mV target? Ground truth is the full DFN (a higher-fidelity simulation reference, not experiment) on cells that differ from the nominal design; 20 training, 7 development-holdout, and a frozen final family of 8 unseen interior parameter cases at 5 °C and 0 °C.
- **Open-loop accuracy did not predict closed-loop safety.** The network (v1) was 10 mV p99 accurate on recorded sessions but 163 mV optimistic in closed loop, letting the modelled anode stay below the plating-risk criterion for up to 24 min.
- **Two causes, isolated:** a decision-horizon mismatch in the training data (fix A: step-aligned data) and training coverage (fix B: DAgger-inspired closed-loop data aggregation on training cells only).
- **Frozen final family:** B cut the worst crossing from −120 to −8 mV and the longest from 24 to 0.7 min and completed every run, but 8 of 16 runs still crossed 0 mV briefly; its training-calibrated margin underestimated the tail on unseen cells (5 vs 23 mV p99). Only the calibrated observer held both 15 mV and 0 mV, at a 139 min median. **Verdict: not yet**: the protection–speed trade-off is set by how well the estimator's error tail transfers to unseen cells.
- v1, A and B and every rollout are preserved; the final family was simulated only after `freeze_manifest.json` hashed every model, calibration and script (verified unchanged at report time).

## Roadmap
- Model predictive control (MPC) or RL that adjusts current live from predicted temperature and anode potential.
- Aging model beyond the SEI index (plating-free cycle-life validation), and model-based imbalance detection for the flat Na-ion OCV.
- Optional: ESP32 + INA226 + NTC hardware rig on a single protected cell.

## Data sources and licences

| Dataset | Used for | Licence | Get it |
|---|---|---|---|
| EVBattery (He et al. 2022), 1.28 M charging snippets | Phase 5 real-fleet calibration | CC BY-NC-SA (non-commercial) | Links in arXiv:2201.12358 → `external_data/evbattery/` |
| Digitized EV fast-charging profiles (Morin et al. 2025) | Phase 5b benchmark against real 800 V cars | CC BY 4.0 | https://doi.org/10.6084/m9.figshare.30570653.v1 → `external_data/figshare-fastcharge/` |
| PyBaMM `Chayambuka2022` and `Chen2020` parameter sets | Cell models | BSD-3-Clause (PyBaMM) | installed with `pybamm` |

Only aggregate statistics derived from EVBattery are committed (`analysis/evbattery/`); they carry the dataset's
CC BY-NC-SA terms. The EVBattery pickles are read by `analysis/evbattery/safe_load.py`, a restricted unpickler that
accepts only numpy data types.

## Citations

If you use this project, please cite it as:

> Dariya, N. (2026). *HEXA-BLADE: fast-charging a sodium-ion 110 kWh, 800 V EV pack (simulation study)*, v1.0.
> https://github.com/DarkThyme/hexa-blade-na-ion-pack

Models, data and methods this work builds on:

1. Sulzer, V., Marquis, S. G., Timms, R., Robinson, M., Chapman, S. J. (2021). Python Battery Mathematical Modelling (PyBaMM). *Journal of Open Research Software*, 9(1), 14. https://doi.org/10.5334/jors.309
2. Doyle, M., Fuller, T. F., Newman, J. (1993). Modeling of galvanostatic charge and discharge of the lithium/polymer/insertion cell. *Journal of The Electrochemical Society*, 140(6), 1526. https://doi.org/10.1149/1.2221597
3. Chayambuka, K., Jiang, M., Mulder, G., Danilov, D. L., Notten, P. H. L. (2022). Physics-based modeling of sodium-ion batteries part I: Experimental parameter determination. *Electrochimica Acta*, 404, 139726. https://doi.org/10.1016/j.electacta.2021.139726
4. Chayambuka, K., Mulder, G., Danilov, D. L., Notten, P. H. L. (2022). Physics-based modeling of sodium-ion batteries part II. Model and validation. *Electrochimica Acta*, 404, 139764. https://doi.org/10.1016/j.electacta.2021.139764
5. Chen, C.-H., Brosa Planella, F., O'Regan, K., Gastol, D., Widanage, W. D., Kendrick, E. (2020). Development of experimental techniques for parameterization of multi-scale lithium-ion battery models. *Journal of The Electrochemical Society*, 167, 080534. https://doi.org/10.1149/1945-7111/ab9050
6. Waldmann, T., Hogg, B.-I., Wohlfahrt-Mehrens, M. (2018). Li plating as unwanted side reaction in commercial Li-ion cells: a review. *Journal of Power Sources*, 384, 107–124. https://doi.org/10.1016/j.jpowsour.2018.02.063
7. He, H., Zhang, J., Wang, Y., et al. (2022, rev. 2023). EVBattery: A large-scale electric vehicle dataset for battery health and capacity estimation. arXiv:2201.12358. https://arxiv.org/abs/2201.12358
8. Morin, H., Chatra, A., Whitacre, J. F., Michalek, J. (2025). Digitized electric vehicle fast-charging profiles by model (power vs SOC/SOE, CSV format), Version 1. Figshare. https://doi.org/10.6084/m9.figshare.30570653.v1
9. Akiba, T., Sano, S., Yanase, T., Ohta, T., Koyama, M. (2019). Optuna: A next-generation hyperparameter optimization framework. *Proc. 25th ACM SIGKDD*, 2623–2631. https://doi.org/10.1145/3292500.3330701
10. Shapley, L. S. (1953). A value for n-person games. In *Contributions to the Theory of Games II*, 307–317. Princeton University Press. (Phase 9 attribution.)
11. Ross, S., Gordon, G., Bagnell, D. (2011). A reduction of imitation learning and structured prediction to no-regret online learning. *Proc. AISTATS*, PMLR 15, 627–635. (The DAgger idea behind the Phase 12 closed-loop data aggregation.)
12. Pedregosa, F., et al. (2011). Scikit-learn: Machine learning in Python. *Journal of Machine Learning Research*, 12, 2825–2830. (Phase 12 estimator network.)

Software: [Bevy](https://bevyengine.org) 0.16 and [egui](https://github.com/emilk/egui) for the viewer,
[NiceGUI](https://nicegui.io) for the Charging Lab, NumPy, SciPy, pandas and matplotlib. The full reference list,
including the vehicle specification sources, is in section 12 of the [project report](docs/Hexa-Blade_Project_Report.pdf).

## Licence

Code and documentation: [MIT](LICENSE), © 2026 Naman Dariya. Third-party data keep their own licences (table above).
