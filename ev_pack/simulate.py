"""
Pack-level charging simulation.

Per cell (200 of them, with small manufacturing variation):
  * electrical: OCV(SOC) + R0 + one RC branch (equivalent-circuit model),
    resistances follow Arrhenius temperature dependence
  * thermal: lumped node with Joule heat, conduction to touching neighbours,
    top/bottom layers cooled by cold plates
Pack:
  * coolant runs front -> rear and warms up along the pack; a chiller (and a
    heater for preconditioning) holds the inlet near the set-point within its power
  * the BMS requests the lowest of: charger power, charger current, cell C-rate cap,
    sodium-plating guard (anode >= +15 mV vs Na), voltage cut-off (CV), thermal derate
"""

from dataclasses import dataclass, field

import numpy as np

from . import chemistry
from .geometry import Layout, build_layout
from .spec import CHARGERS, DEFAULT_PACK, ChargerSpec, PackSpec

R_GAS = 8.314
T_REF_K = 298.15

LIMITERS = [
    "Charger power",
    "Charger current",
    "Cell C-rate cap",
    "Na-plating guard",
    "Voltage limit (CV)",
    "Thermal derate",
]


@dataclass
class SimResult:
    charger: ChargerSpec
    settings: dict
    series: dict            # 1-D arrays over every time step
    frame_time_min: np.ndarray
    frame_temp_c: np.ndarray  # (frames, cells)
    frame_soc: np.ndarray     # (frames, cells)
    frame_index: np.ndarray   # step index of each frame (into series)
    summary: dict = field(default_factory=dict)


def charger_by_name(name: str) -> ChargerSpec:
    return next(c for c in CHARGERS if c.name == name)


def simulate(
    charger: ChargerSpec | str,
    start_soc: float = 0.10,
    target_soc: float = 0.80,
    ambient_c: float = 25.0,
    precondition: bool = True,
    pack: PackSpec = DEFAULT_PACK,
    seed: int = 7,
    n_frames: int = 240,
    heater_max_w: float = 7000.0,
) -> SimResult:
    if isinstance(charger, str):
        charger = charger_by_name(charger)
    cell = pack.cell
    lay: Layout = build_layout(pack)
    n = pack.n_cells
    rng = np.random.default_rng(seed)

    # Manufacturing spread
    cap_ah = cell.capacity_ah * (1 + rng.normal(0, 0.01, n))
    r_factor = 1 + rng.normal(0, 0.03, n)
    soc = np.clip(start_soc + rng.normal(0, 0.004, n), 0, 1)

    setpoint = pack.coolant_setpoint_c
    temp = np.full(n, setpoint if precondition else ambient_c, dtype=float)
    v_rc = np.zeros(n)
    heat_cap = cell.mass_kg * cell.heat_capacity_j_kgk  # J/K per cell
    mcp = pack.coolant_flow_kg_s * pack.coolant_cp_j_kgk
    a, b = lay.neighbors[:, 0], lay.neighbors[:, 1]
    cols = lay.col
    plate_contacts = lay.cooled.astype(float)  # bottom and top rows touch one plate each
    coolant_return = temp.mean()
    t_in = setpoint

    dt = 1.0 if charger.kind == "DC" else 10.0
    p_max_w = charger.power_kw * 1000 * charger.efficiency
    boost = charger.kind == "DC" and charger.max_voltage_v < pack.max_v  # legacy 400 V station

    keys = ["time_min", "power_kw", "grid_kw", "current_a", "c_rate", "pack_v", "soc_mean", "soc_min",
            "soc_max", "temp_max", "temp_min", "temp_mean", "coolant_in", "coolant_out", "heat_kw",
            "anode_min_mv", "limiter"]
    rec = {k: [] for k in keys}
    temps, socs = [], []

    t = 0.0
    low_current_s = 0.0
    max_time = 30 * 3600
    while t < max_time:
        tk = temp + 273.15
        arr = lambda ea: np.exp(ea / R_GAS * (1 / tk - 1 / T_REF_K))  # noqa: E731
        r0 = cell.r0_mohm * 1e-3 * r_factor * arr(cell.ea_r0_j_mol)
        r1 = cell.r1_mohm * 1e-3 * r_factor * arr(cell.ea_r0_j_mol)
        r_an = cell.r_anode_mohm * 1e-3 * r_factor * arr(cell.ea_anode_j_mol)
        ocv = chemistry.ocv(soc)
        u_an = chemistry.anode_potential(soc)

        # --- BMS current request: the tightest limit wins
        e_pack = float(np.sum(ocv + v_rc))
        r_pack = float(np.sum(r0))
        i_power = (-e_pack + np.sqrt(e_pack**2 + 4 * r_pack * p_max_w)) / (2 * r_pack)
        limits = {
            "Charger power": i_power,
            "Charger current": charger.max_current_a if charger.kind == "DC" and not boost else np.inf,
            "Cell C-rate cap": cell.max_c_rate * cell.capacity_ah,
            "Na-plating guard": max(0.0, float(np.min((u_an - cell.plating_margin_v) / r_an))),
            "Voltage limit (CV)": max(0.0, float(np.min((cell.v_max - ocv - v_rc) / r0))),
        }
        derate = np.clip((cell.t_cutoff_c - temp.max()) / (cell.t_cutoff_c - cell.t_derate_start_c), 0, 1)
        limits["Thermal derate"] = derate * cell.max_c_rate * cell.capacity_ah if derate < 1 else np.inf
        limiter = min(limits, key=limits.get)
        current = max(0.0, float(limits[limiter]))

        # --- electrical update
        v_cell = ocv + v_rc + current * r0
        pack_v = float(v_cell.sum())
        heat = current**2 * r0 + current * v_rc  # W per cell
        v_rc += dt * (current * r1 - v_rc) / cell.tau1_s
        soc = np.minimum(soc + current * dt / 3600 / cap_ah, 1.0)

        # --- thermal update
        q = heat.copy()
        flow = pack.g_cell_cell_w_k * (temp[a] - temp[b])
        np.subtract.at(q, a, flow)
        np.add.at(q, b, flow)
        col_heat = np.bincount(cols, weights=plate_contacts * pack.g_cell_plate_w_k * temp, minlength=pack.columns)
        col_g = np.bincount(cols, weights=plate_contacts * pack.g_cell_plate_w_k, minlength=pack.columns)
        # coolant temperature entering each column (marches front -> rear), quasi-steady
        t_cool = np.empty(pack.columns)
        tc = t_in
        for c in range(pack.columns):
            t_cool[c] = tc
            tc += (col_heat[c] - col_g[c] * tc) / mcp
        coolant_return = tc
        q -= plate_contacts * pack.g_cell_plate_w_k * (temp - t_cool[cols])
        q -= pack.g_cell_ambient_w_k * (temp - ambient_c)
        temp = temp + dt * q / heat_cap
        # chiller / heater sets the next inlet temperature within their power
        t_in = float(np.clip(setpoint, coolant_return - pack.chiller_max_w / mcp,
                             coolant_return + (heater_max_w if precondition else 0.0) / mcp))

        # --- record
        p_pack = pack_v * current
        for k, v in (("time_min", t / 60), ("power_kw", p_pack / 1000),
                     ("grid_kw", p_pack / charger.efficiency / 1000), ("current_a", current),
                     ("c_rate", current / cell.capacity_ah), ("pack_v", pack_v),
                     ("soc_mean", soc.mean()), ("soc_min", soc.min()), ("soc_max", soc.max()),
                     ("temp_max", temp.max()), ("temp_min", temp.min()), ("temp_mean", temp.mean()),
                     ("coolant_in", t_in), ("coolant_out", coolant_return), ("heat_kw", heat.sum() / 1000),
                     ("anode_min_mv", float(np.min(u_an - current * r_an)) * 1000), ("limiter", limiter)):
            rec[k].append(v)
        temps.append(temp.copy())
        socs.append(soc.copy())

        t += dt
        low_current_s = low_current_s + dt if current < 0.02 * cell.capacity_ah else 0.0
        if soc.mean() >= target_soc or low_current_s > 300:
            break

    series = {k: (np.array(v) if k != "limiter" else v) for k, v in rec.items()}
    steps = len(series["time_min"])
    frame_index = np.unique(np.linspace(0, steps - 1, min(n_frames, steps)).round().astype(int))

    res = SimResult(
        charger=charger,
        settings=dict(start_soc=start_soc, target_soc=target_soc, ambient_c=ambient_c, precondition=precondition),
        series=series,
        frame_time_min=series["time_min"][frame_index],
        frame_temp_c=np.array(temps)[frame_index],
        frame_soc=np.array(socs)[frame_index],
        frame_index=frame_index,
    )
    res.summary = summarize(res, dt)
    return res


def summarize(res: SimResult, dt: float) -> dict:
    s = res.series
    energy_kwh = float(np.sum(s["power_kw"]) * dt / 3600)
    limiter_share = {name: s["limiter"].count(name) / len(s["limiter"]) for name in LIMITERS}
    return {
        "charger": res.charger.name,
        "time_min": float(s["time_min"][-1]),
        "energy_to_pack_kwh": energy_kwh,
        "energy_from_grid_kwh": energy_kwh / res.charger.efficiency,
        "peak_power_kw": float(np.max(s["power_kw"])),
        "avg_power_kw": float(np.mean(s["power_kw"])),
        "peak_c_rate": float(np.max(s["c_rate"])),
        "peak_temp_c": float(np.max(s["temp_max"])),
        "max_temp_spread_c": float(np.max(s["temp_max"] - s["temp_min"])),
        "min_anode_mv": float(np.min(s["anode_min_mv"])),
        "final_soc": float(s["soc_mean"][-1]),
        "main_limiter": max(limiter_share, key=limiter_share.get),
        "limiter_share": limiter_share,
    }


if __name__ == "__main__":
    import time

    for ch in CHARGERS:
        t0 = time.time()
        r = simulate(ch)
        sm = r.summary
        print(f"{ch.name:>10}: 10->80% {sm['time_min']:7.1f} min | peak {sm['peak_power_kw']:6.1f} kW "
              f"({sm['peak_c_rate']:.2f}C) | avg {sm['avg_power_kw']:6.1f} kW | Tmax {sm['peak_temp_c']:5.1f} C "
              f"| spread {sm['max_temp_spread_c']:4.1f} C | anode min {sm['min_anode_mv']:5.1f} mV "
              f"| limiter: {sm['main_limiter']} | {time.time() - t0:.1f}s")
