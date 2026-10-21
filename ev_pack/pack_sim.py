"""
Pack charging simulation v2 — every cell driven by the physics-based (DFN) charge maps.

Per cell and step:
  * overpotential eta(T, C, SOC) and anode potential vs Na(T, C, SOC) from ev_pack/data/na_cell_maps.npz
    (eta lags its map value with a short time constant, standing in for diffusion relaxation)
  * heat q = I * eta; conduction to touching cells and to its cold plate; coolant warms front -> rear
  * parallel cells share the group current by conductance (warmer cells take more current)
The BMS picks the string current as the lowest of: charger power, charger current, cell C-rate cap,
Na-plating guard (anode >= margin vs Na at every cell), voltage limit (CV), thermal derate.

Two BMS strategies are compared:
  standard   typical modern EV: 40 mV plating margin, preconditioned / held near 30 C
  optimized  ideal-information control (reads the modelled anode potential directly): 15 mV margin, preconditioned to 35 C (the sweet spot found by
             phase3_tradeoff.py: hotter adds aging but almost no speed once the charger is the limit)
"""

from dataclasses import dataclass, field

import numpy as np
from scipy.interpolate import RegularGridInterpolator

from . import charge_maps
from .architectures import GROSS_KWH, N_SERIES, Architecture, build, n_series
from .spec import CHARGERS, ChargerSpec

R_GAS = 8.314
SOC_LO, SOC_HI = 0.03, 0.96          # usable window inside the electrode window (bottom / top buffer)
EA_SEI = 55_000.0                     # SEI growth activation energy (SEI exposure index only)
ETA_TAU_S = 15.0
# Calibrated against EVBattery fleet data (analysis/evbattery): resistances outside the electrochemistry
# (tabs, welds, busbars, fuse, contactors) and cell-to-cell non-uniformity.
R_TAB_MOHM_AH = 0.12 * 150.0     # cell tab/weld resistance 0.12 mOhm for a 150 Ah cell, scales 1/capacity
R_BUS_MOHM = 8.0                 # pack-level busbars, fuse, contactors (heat leaves via the enclosure)
PLATE_CONTACT_SPREAD = 0.25      # log-normal sigma of cell-to-cold-plate contact quality (TIM thickness)
SOC_SPREAD = 0.01                # initial cell SOC imbalance (1 sigma)

LIMITERS = ["Charger power", "Charger current", "Cell C-rate cap", "Na-plating guard",
            "Voltage limit (CV)", "Thermal derate"]


@dataclass(frozen=True)
class Strategy:
    id: str
    name: str
    plating_margin_v: float
    precondition_c: float
    coolant_setpoint_c: float
    derate_start_c: float
    cutoff_c: float
    max_c_rate: float


STRATEGIES = {
    "standard": Strategy("standard", "Standard BMS map", 0.040, 30.0, 30.0, 45.0, 55.0, 4.0),
    "optimized": Strategy("optimized", "Anode-aware BMS + 35 C preconditioning", 0.015, 35.0, 33.0, 50.0, 58.0, 5.0),
}


@dataclass(frozen=True)
class InfoErrors:
    """What the BMS controller does NOT know. Passing one to simulate() splits three roles:

    controller  decides the current from declared measurements and estimates only (temperature readings with
                bias, an SOC estimate, an anode-potential estimate from its own nominal map model + bias + noise),
                optionally delayed. It runs the same limits and bisection as the ideal BMS, but on estimates:
                there is NO clamp on the true anode potential.
    plant       evolves on its own true state. `r_mismatch` makes the real cells polarise more (or less) than
                the controller's model: true anode = OCP - (1 + r) * (OCP - anode_model), true overpotential
                and heat scale by (1 + r).
    evaluator   records the true anode potential, the true cell voltage and estimate-minus-truth errors; it
                never corrects the controller.

    Signs: positive anode_bias / temp_bias / r_mismatch and negative soc_bias are the dangerous directions
    (the controller believes it is further from plating than it is). All ranges are assumed stress values.
    """
    anode_bias_mv: float = 0.0
    anode_noise_mv: float = 0.0      # 1 sigma, independent per cell and step
    temp_bias_c: float = 0.0
    soc_bias: float = 0.0            # estimated - true SOC (fraction of capacity)
    r_mismatch: float = 0.0
    delay_steps: int = 0
    seed: int = 11


# ---------------------------------------------------------------------------------------- cell maps

class CellMaps:
    C_FINE = np.linspace(0.0, 6.0, 241)

    def __init__(self, path=None):
        m = charge_maps.load(path) if path else charge_maps.load()
        self.v_max = float(m["v_max"])
        T, C, S = m["T_grid"], m["C_grid"], m["soc_grid"]
        ocv = self._fill_soc(m["ocv"])
        an_ocp = self._fill_soc(m["anode_ocp"])
        volt = m["voltage"].copy()
        anode = m["anode"].copy()
        # beyond the SOC a CC run reached, the cell would be above the voltage limit
        volt = np.where(np.isnan(volt), self.v_max + 0.15, volt)
        for i in range(len(T)):
            for j in range(len(C)):
                anode[i, j] = self._fill_soc(anode[i, j])
        # prepend C = 0 (rest): voltage = OCV, anode = OCP
        C = np.concatenate([[0.0], C])
        volt = np.concatenate([np.broadcast_to(ocv, (len(T), 1, len(S))), volt], axis=1)
        anode = np.concatenate([np.broadcast_to(an_ocp, (len(T), 1, len(S))), anode], axis=1)
        eta = volt - ocv[None, None, :]
        self.T, self.C, self.S = T, C, S
        self.ocv_table, self.an_table = ocv, an_ocp
        self._eta = RegularGridInterpolator((T, C, S), eta, bounds_error=False, fill_value=None)
        self._anode = RegularGridInterpolator((T, C, S), anode, bounds_error=False, fill_value=None)
        # fine-C tables -> max C for a plating margin / for the voltage limit, on (T, SOC)
        self._eta_fine = np.stack([[np.interp(self.C_FINE, C, eta[i, :, k]) for k in range(len(S))]
                                   for i in range(len(T))])           # (T, S, Cf)
        self._an_fine = np.stack([[np.interp(self.C_FINE, C, anode[i, :, k]) for k in range(len(S))]
                                  for i in range(len(T))])
        cv = self._max_c(self._eta_fine + ocv[None, :, None] <= self.v_max)
        self._cv = RegularGridInterpolator((T, S), cv, bounds_error=False, fill_value=None)
        self._plating = {}

    @staticmethod
    def _fill_soc(a: np.ndarray) -> np.ndarray:
        a = a.copy()
        ok = np.flatnonzero(~np.isnan(a))
        if ok.size == 0:
            return np.zeros_like(a)
        a[: ok[0]] = a[ok[0]]
        a[ok[-1] + 1:] = a[ok[-1]]
        return a

    def _max_c(self, allowed: np.ndarray) -> np.ndarray:
        # allowed: (T, S, Cf) bool, monotone-ish in C. Largest allowed C before the first violation.
        first_bad = np.argmax(~allowed, axis=2)
        all_ok = allowed.all(axis=2)
        idx = np.where(all_ok, len(self.C_FINE) - 1, np.maximum(first_bad - 1, 0))
        return self.C_FINE[idx]

    def plating_cmax(self, margin_v: float) -> RegularGridInterpolator:
        if margin_v not in self._plating:
            cmax = self._max_c(self._an_fine >= margin_v)
            self._plating[margin_v] = RegularGridInterpolator((self.T, self.S), cmax, bounds_error=False,
                                                              fill_value=None)
        return self._plating[margin_v]

    def _pts2(self, temp, soc):
        return np.column_stack([np.clip(temp, self.T[0], self.T[-1]), np.clip(soc, 0, 1)])

    def _pts3(self, temp, c, soc):
        return np.column_stack([np.clip(temp, self.T[0], self.T[-1]), np.clip(c, 0, self.C[-1]), np.clip(soc, 0, 1)])

    def ocv(self, soc):
        return np.interp(soc, self.S, self.ocv_table)

    def eta(self, temp, c, soc):
        return self._eta(self._pts3(temp, c, soc))

    def anode(self, temp, c, soc):
        return self._anode(self._pts3(temp, c, soc))

    def cv_cmax(self, temp, soc):
        return self._cv(self._pts2(temp, soc))


_MAPS: dict = {}


def maps(path=None) -> CellMaps:
    """Charge maps for the default cell, or for a variant map file (sensitivity studies)."""
    key = str(path) if path else "default"
    if key not in _MAPS:
        _MAPS[key] = CellMaps(path)
    return _MAPS[key]


def nominal_group_ah(series: int = N_SERIES) -> float:
    """Series-group capacity (Ah) so that the pack holds GROSS_KWH over the electrode window."""
    m = maps()
    v_avg = float(np.trapezoid(m.ocv_table, m.S))
    return GROSS_KWH * 1000 / (series * v_avg)


def build_nominal(arch_id: str) -> Architecture:
    return build(arch_id, nominal_group_ah(n_series(arch_id)))


def to_display(soc_true):
    return (np.asarray(soc_true) - SOC_LO) / (SOC_HI - SOC_LO)


def to_true(soc_disp):
    return SOC_LO + np.asarray(soc_disp) * (SOC_HI - SOC_LO)


# ---------------------------------------------------------------------------------------- simulation

@dataclass
class Result:
    arch: Architecture
    charger: ChargerSpec
    strategy: Strategy
    settings: dict
    series: dict
    frame_time_min: np.ndarray
    frame_index: np.ndarray
    frame_temp_c: np.ndarray
    frame_soc: np.ndarray
    summary: dict = field(default_factory=dict)


def simulate(arch: str | Architecture = "blade", charger: str | ChargerSpec = "350 kW DC",
             strategy: str | Strategy = "optimized", start_soc: float = 0.10, target_soc: float = 0.90,
             ambient_c: float = 25.0, precondition: bool = True, seed: int = 7, n_frames: int = 240,
             chiller_max_w: float = 25_000.0, heater_max_w: float = 7_000.0,
             coolant_flow_kg_s: float = 0.5, max_hours: float = 30.0, maps_path=None,
             info: InfoErrors | None = None) -> Result:
    m = maps(maps_path)
    a = arch if isinstance(arch, Architecture) else build_nominal(arch)
    ch = charger if isinstance(charger, ChargerSpec) else next(c for c in CHARGERS if c.name == charger)
    st = strategy if isinstance(strategy, Strategy) else STRATEGIES[strategy]
    n = a.n_cells
    rng = np.random.default_rng(seed)

    cap = a.cell_capacity_ah * (1 + rng.normal(0, 0.01, n))
    r_spread = 1 + rng.normal(0, 0.03, n)                 # resistance manufacturing spread
    soc = np.clip(to_true(start_soc) + rng.normal(0, SOC_SPREAD, n), 0, 1)
    g_plate = a.g_plate * rng.lognormal(0.0, PLATE_CONTACT_SPREAD, n)
    r_tab = R_TAB_MOHM_AH / a.cell_capacity_ah * 1e-3 * r_spread
    temp = np.full(n, st.precondition_c if precondition else ambient_c, dtype=float)
    eta_dyn = np.zeros(n)
    heat_cap = a.cell_heat_capacity
    mcp = coolant_flow_kg_s * 3500.0
    nb_a, nb_b = a.neighbors[:, 0], a.neighbors[:, 1]
    groups = a.group
    n_groups = a.n_series
    group_size = np.bincount(groups, minlength=n_groups)
    plating_tbl = m.plating_cmax(st.plating_margin_v)
    t_in = st.coolant_setpoint_c
    coolant_return = temp.mean()

    dt = 2.0 if ch.kind == "DC" else 20.0
    p_max_w = ch.power_kw * 1000 * ch.efficiency
    boost = ch.kind == "DC" and ch.max_voltage_v < 800
    v_pack_prev = float(np.bincount(groups, weights=m.ocv(soc), minlength=n_groups).sum() / 1) / a.n_parallel
    sei = 0.0

    rec = {k: [] for k in ("time_min", "power_kw", "current_a", "c_rate", "pack_v", "soc", "soc_true",
                           "temp_max", "temp_min", "temp_mean", "coolant_in", "coolant_out", "heat_kw",
                           "anode_min_mv", "vspread_mv", "limiter")}
    temps, socs = [], []
    t, low_s = 0.0, 0.0
    target_true = to_true(target_soc)
    eta_scale = 1.0
    if info is not None:
        eta_scale = 1.0 + info.r_mismatch
        rng_i = np.random.default_rng(info.seed)
        plating_tbl_m = m.plating_cmax(round(st.plating_margin_v - info.anode_bias_mv * 1e-3, 6))
        history = []                      # (temp, soc) seen by the controller, oldest first
        for k in ("anode_true_min_mv", "anode_est_min_mv", "est_err_max_mv", "est_err_at_min_mv", "v_cell_max"):
            rec[k] = []

        def plant_anode(T, c, s_):
            ocp = m.anode(T, np.zeros_like(c), s_)
            return ocp - (1 + info.r_mismatch) * (ocp - m.anode(T, c, s_))
    while t < max_hours * 3600:
        # --- current sharing inside each parallel group (by conductance ~ Arrhenius on temperature)
        g = np.exp(20_000.0 / R_GAS * (1 / 298.15 - 1 / (temp + 273.15))) / r_spread
        share = g / np.bincount(groups, weights=g, minlength=n_groups)[groups]

        if info is None:
            # --- BMS limits on the string current
            c_plating = plating_tbl(np.column_stack([np.clip(temp, m.T[0], m.T[-1]), np.clip(soc, 0, 1)]))
            c_cv = m.cv_cmax(temp, soc)
            limits = {
                "Charger power": p_max_w / max(v_pack_prev, 1.0),
                "Charger current": ch.max_current_a if ch.kind == "DC" and not boost else np.inf,
                "Cell C-rate cap": float(np.min(st.max_c_rate * cap / share)),
                "Na-plating guard": float(np.min(np.maximum(c_plating, 0) * cap / share)),
                "Voltage limit (CV)": float(np.min(np.maximum(c_cv, 0) * cap / share)),
            }
            tmax = temp.max()
            derate = np.clip((st.cutoff_c - tmax) / (st.cutoff_c - st.derate_start_c), 0, 1)
            limits["Thermal derate"] = derate * float(np.min(st.max_c_rate * cap / share)) if derate < 1 else np.inf
            limiter = min(limits, key=limits.get)
            i_string = max(0.0, float(limits[limiter]))

            # --- cells. The tables above are approximate; enforce the plating margin exactly on every cell
            # (bisection on the string current) so no cell is ever charged below the margin.
            i_cell = i_string * share
            c_cell = i_cell / cap
            anode = m.anode(temp, c_cell, soc)
            ocv_now = m.ocv(soc)

            def violation(i):  # which hard limit (if any) a string current breaks on any cell
                c = i * share / cap
                if m.anode(temp, c, soc).min() < st.plating_margin_v:
                    return "Na-plating guard"
                if (ocv_now + m.eta(temp, c, soc) + i * share * r_tab).max() > m.v_max:
                    return "Voltage limit (CV)"
                return None

            broken = violation(i_string) if i_string > 0 else None
            if broken:
                lo, hi = 0.0, i_string
                for _ in range(12):
                    mid = (lo + hi) / 2
                    if violation(mid) is None:
                        lo = mid
                    else:
                        hi = mid
                i_string, limiter = lo, broken
                i_cell = i_string * share
                c_cell = i_cell / cap
                anode = m.anode(temp, c_cell, soc)
        else:
            # --- controller: estimates only (delayed state, biased sensors, noisy anode estimate)
            history.append((temp.copy(), soc.copy()))
            if len(history) > info.delay_steps + 1:
                history.pop(0)
            temp_seen, soc_seen = history[0]
            temp_m = temp_seen + info.temp_bias_c
            soc_m = np.clip(soc_seen + info.soc_bias, 0, 1)
            g_m = np.exp(20_000.0 / R_GAS * (1 / 298.15 - 1 / (temp_m + 273.15))) / r_spread
            share_m = g_m / np.bincount(groups, weights=g_m, minlength=n_groups)[groups]
            noise = rng_i.normal(0.0, info.anode_noise_mv, n) * 1e-3 if info.anode_noise_mv > 0 else 0.0
            offset = info.anode_bias_mv * 1e-3 + noise

            def est_anode(i):
                return m.anode(temp_m, i * share_m / cap, soc_m) + offset

            # the controller's table shares its estimate's bias: est = model + bias >= margin <=> model >= margin - bias
            c_plating = plating_tbl_m(np.column_stack([np.clip(temp_m, m.T[0], m.T[-1]), soc_m]))
            c_cv = m.cv_cmax(temp_m, soc_m)
            limits = {
                "Charger power": p_max_w / max(v_pack_prev, 1.0),
                "Charger current": ch.max_current_a if ch.kind == "DC" and not boost else np.inf,
                "Cell C-rate cap": float(np.min(st.max_c_rate * cap / share_m)),
                "Na-plating guard": float(np.min(np.maximum(c_plating, 0) * cap / share_m)),
                "Voltage limit (CV)": float(np.min(np.maximum(c_cv, 0) * cap / share_m)),
            }
            derate = np.clip((st.cutoff_c - temp_m.max()) / (st.cutoff_c - st.derate_start_c), 0, 1)
            limits["Thermal derate"] = derate * float(np.min(st.max_c_rate * cap / share_m)) if derate < 1 else np.inf
            limiter = min(limits, key=limits.get)
            i_string = max(0.0, float(limits[limiter]))
            ocv_m = m.ocv(soc_m)

            def violation(i):  # judged on the controller's estimates only
                if est_anode(i).min() < st.plating_margin_v:
                    return "Na-plating guard"
                if (ocv_m + m.eta(temp_m, i * share_m / cap, soc_m) + i * share_m * r_tab).max() > m.v_max:
                    return "Voltage limit (CV)"
                return None

            broken = violation(i_string) if i_string > 0 else None
            if broken:
                lo, hi = 0.0, i_string
                for _ in range(12):
                    mid = (lo + hi) / 2
                    if violation(mid) is None:
                        lo = mid
                    else:
                        hi = mid
                i_string, limiter = lo, broken
            g = np.exp(20_000.0 / R_GAS * (1 / 298.15 - 1 / (temp + 273.15))) / r_spread
            share = g / np.bincount(groups, weights=g, minlength=n_groups)[groups]   # true current split
            ocv_now = m.ocv(soc)
        eta_prev = eta_dyn.copy()

        def solve(i_s):
            i_c = i_s * share
            e = eta_prev + (m.eta(temp, i_c / cap, soc) * eta_scale - eta_prev) * min(1.0, dt / ETA_TAU_S)
            v_c = ocv_now + e + i_c * r_tab
            v_g = np.bincount(groups, weights=v_c, minlength=n_groups) / group_size
            return i_c, e, v_c, v_g, float(v_g.sum()) + i_s * R_BUS_MOHM * 1e-3

        i_cell, eta_dyn, v_cell, v_group, pack_v = solve(i_string)
        # The power limit above used the previous step's voltage. In a cold pack the voltage climbs quickly,
        # so re-solve until the charger's power rating holds (the full-matrix test caught a 3-7 % overshoot).
        for _ in range(4):
            if pack_v * i_string <= p_max_w * 1.0005:
                break
            i_string, limiter = p_max_w / pack_v, "Charger power"
            i_cell, eta_dyn, v_cell, v_group, pack_v = solve(i_string)
        c_cell = i_cell / cap
        if info is None:
            anode = m.anode(temp, c_cell, soc)
        else:
            anode = plant_anode(temp, c_cell, soc)                       # evaluator: true state
            est = est_anode(i_string)                                     # what the controller believed
            err = est - anode
            k_min = int(np.argmin(anode))
            rec["anode_true_min_mv"].append(float(anode.min()) * 1000)
            rec["anode_est_min_mv"].append(float(est.min()) * 1000)
            rec["est_err_max_mv"].append(float(err.max()) * 1000)
            rec["est_err_at_min_mv"].append(float(err[k_min]) * 1000)
            rec["v_cell_max"].append(float(v_cell.max()))
        v_pack_prev = pack_v
        heat = i_cell * np.maximum(eta_dyn, 0.0) + i_cell**2 * r_tab
        soc = np.minimum(soc + i_cell * dt / 3600 / cap, 1.0)

        # --- thermal network
        q = heat.copy()
        flow = a.g_neighbor * (temp[nb_a] - temp[nb_b])
        np.subtract.at(q, nb_a, flow)
        np.add.at(q, nb_b, flow)
        bin_gt = np.bincount(a.coolant_bin, weights=g_plate * temp, minlength=a.n_bins)
        bin_g = np.bincount(a.coolant_bin, weights=g_plate, minlength=a.n_bins)
        t_cool = np.empty(a.n_bins)
        tc = t_in
        for b in range(a.n_bins):
            t_cool[b] = tc
            tc += (bin_gt[b] - bin_g[b] * tc) / mcp
        coolant_return = tc
        q -= g_plate * (temp - t_cool[a.coolant_bin])
        q -= 0.01 * (temp - ambient_c)
        temp = temp + dt * q / heat_cap
        t_in = float(np.clip(st.coolant_setpoint_c, coolant_return - chiller_max_w / mcp,
                             coolant_return + heater_max_w / mcp))
        sei += dt / 3600 * float(np.mean(np.exp(EA_SEI / R_GAS * (1 / 298.15 - 1 / (temp + 273.15)))))

        # --- record
        p_pack = pack_v * i_string
        for k, v in (("time_min", t / 60), ("power_kw", p_pack / 1000), ("current_a", i_string),
                     ("c_rate", i_string / (a.cell_capacity_ah * a.n_parallel)), ("pack_v", pack_v),
                     ("soc", float(to_display(soc.mean()))), ("soc_true", float(soc.mean())),
                     ("temp_max", float(temp.max())), ("temp_min", float(temp.min())),
                     ("temp_mean", float(temp.mean())), ("coolant_in", t_in), ("coolant_out", coolant_return),
                     ("heat_kw", (float(heat.sum()) + i_string**2 * R_BUS_MOHM * 1e-3) / 1000),
                     ("anode_min_mv", float(anode.min()) * 1000),
                     ("vspread_mv", float(v_group.max() - v_group.min()) * 1000),
                     ("limiter", limiter)):
            rec[k].append(v)
        temps.append(temp.astype(np.float32))
        socs.append(to_display(soc).astype(np.float32))

        t += dt
        low_s = low_s + dt if c_cell.mean() < 0.02 else 0.0
        if soc.mean() >= target_true or low_s > 300:
            break

    series = {k: (np.array(v) if k != "limiter" else v) for k, v in rec.items()}
    steps = len(series["time_min"])
    fidx = np.unique(np.linspace(0, steps - 1, min(n_frames, steps)).round().astype(int))
    res = Result(a, ch, st, dict(start_soc=start_soc, target_soc=target_soc, ambient_c=ambient_c,
                                 precondition=precondition), series,
                 series["time_min"][fidx], fidx, np.array(temps)[fidx], np.array(socs)[fidx])
    res.summary = _summary(res, dt, sei)
    if info is not None:
        res.summary["info"] = _info_summary(series, dt, st, m.v_max)
    return res


def _info_summary(s: dict, dt: float, st: Strategy, v_max: float) -> dict:
    """Evaluator metrics on the TRUE state (simulation stress test, not a real-cell safety proof)."""
    a = np.asarray(s["anode_true_min_mv"])
    err_min = np.asarray(s["est_err_at_min_mv"])
    below0, below_m, below15 = a < 0.0, a < st.plating_margin_v * 1000 - 0.05, a < 14.95
    return {
        "true_min_anode_mv": float(a.min()),
        "est_min_anode_mv": float(np.min(s["anode_est_min_mv"])),
        "plating_steps": int(below0.sum()), "plating_s": float(below0.sum() * dt),
        "plating_depth_mv": float(max(0.0, -a.min())),
        "below_margin_s": float(below_m.sum() * dt), "below_margin_depth_mv": float(max(0.0, st.plating_margin_v * 1000 - a.min())),
        "below_15mv_s": float(below15.sum() * dt), "below_15mv_depth_mv": float(max(0.0, 15.0 - a.min())),
        "danger_err_max_mv": float(np.max(s["est_err_max_mv"])),
        "danger_err_at_min_cell_max_mv": float(err_min.max()),
        "danger_err_frac": float(np.mean(err_min > 2.0)),
        # the dangerous direction where it matters: while the true anode is within 50 mV of plating
        "danger_err_near_limit_mv": float(err_min[a < 50.0].max()) if (a < 50.0).any() else 0.0,
        "danger_err_near_limit_s": float(np.sum((a < 50.0) & (err_min > 2.0)) * dt),
        "overvoltage_s": float(np.sum(np.asarray(s["v_cell_max"]) > v_max + 1e-3) * dt),
        "max_cell_v": float(np.max(s["v_cell_max"])),
    }


def _summary(res: Result, dt: float, sei_h: float) -> dict:
    s = res.series
    t, soc = s["time_min"], s["soc"]
    share = {k: s["limiter"].count(k) / len(s["limiter"]) for k in LIMITERS}
    tts = {}
    for target in (0.5, 0.8, 0.85, 0.9, 0.95, 1.0):
        hit = np.flatnonzero(soc >= target - 1e-9)
        if hit.size and target >= res.settings["start_soc"]:
            tts[f"{target:g}"] = round(float(t[hit[0]]), 2)
    return {
        "arch": res.arch.id, "charger": res.charger.name, "strategy": res.strategy.id,
        "time_min": float(t[-1]),
        "time_to_soc_min": tts,
        "energy_to_pack_kwh": float(np.sum(s["power_kw"]) * dt / 3600),
        "peak_power_kw": float(s["power_kw"].max()),
        "avg_power_kw": float(s["power_kw"].mean()),
        "peak_c_rate": float(s["c_rate"].max()),
        "peak_temp_c": float(s["temp_max"].max()),
        "max_temp_spread_c": float((s["temp_max"] - s["temp_min"]).max()),
        "min_anode_mv": float(s["anode_min_mv"].min()),
        "final_soc": float(soc[-1]),
        "main_limiter": max(share, key=share.get),
        "limiter_share": share,
        "sei_aging_h25": round(sei_h, 3),
    }
