"""
Physics-based sodium-ion cell (PyBaMM DFN), scaled from a lab coin cell to a commercial EV cell.

Base: Chayambuka et al. 2022 (NVPF / hard carbon), shipped with PyBaMM. That parameter set is a
3 mAh lab cell with very slow hard-carbon transport (it reaches only ~30% capacity at 1C), and it has
no temperature dependence. We keep its measured thermodynamics (OCPs, concentrations, electrode
balance) and change transport/kinetics to commercial-cell values:

  * hard-carbon diffusivity and particle size  -> fast-charge-capable anode
  * Arrhenius temperature dependence on solid diffusion, reaction kinetics and
    electrolyte transport (activation energies within published ranges for Na-ion)
  * electrode area scaled so the cell is CELL_AH

Every change is a named, documented knob in CellDesign so it can be calibrated or questioned.
"""

from dataclasses import asdict, dataclass

import numpy as np
import pybamm

R_GAS = 8.314
T_REF = 298.15
# NVPF's second plateau sits at ~4.1-4.2 V, so a 4.2 V cut-off leaves almost no headroom above ~55% SOC.
# Commercial NVPF / hard-carbon cells charge to ~4.3 V (assumption, documented in the README).
V_MAX = 4.3


@dataclass(frozen=True)
class CellDesign:
    capacity_ah: float = 150.0
    # transport / kinetics scaling vs the Chayambuka2022 lab cell
    d_n_scale: float = 40.0          # hard-carbon solid diffusivity multiplier
    d_p_scale: float = 10.0          # NVPF solid diffusivity multiplier
    k_n_scale: float = 50.0          # anode reaction-rate multiplier
    k_p_scale: float = 50.0
    # Hard-carbon "plateau" (pore-filling, anode < ~0.1 V vs Na): GITT studies report Na diffusion 1-2 orders of
    # magnitude slower than in the sloping region. Chayambuka's data only dips ~6x there, so this optional penalty
    # multiplies D_n by plateau_d_factor above plateau_sto_start (smooth step). 1.0 = base data (optimistic).
    plateau_d_factor: float = 1.0
    plateau_sto_start: float = 0.55
    plateau_sto_width: float = 0.04
    pos_porosity: float = 0.23       # cathode porosity (lab value; power cells run ~0.30)
    sigma_e_scale: float = 1.0       # electrolyte conductivity multiplier
    r_n_m: float = 2.5e-6            # hard-carbon particle radius (lab: 3.48 um)
    neg_thickness_m: float = 48e-6   # power-optimised electrodes (lab cell: 64 / 68 um)
    pos_thickness_m: float = 51e-6
    # Arrhenius activation energies [J/mol] (assumptions, literature ranges)
    ea_d_n: float = 35_000.0
    ea_d_p: float = 30_000.0
    ea_k_n: float = 55_000.0
    ea_k_p: float = 45_000.0
    ea_electrolyte: float = 17_000.0


def _arrhenius(ea: float, T):
    return pybamm.exp(ea / R_GAS * (1 / T_REF - 1 / T))


def parameter_values(design: CellDesign = CellDesign(), temperature_c: float = 25.0) -> pybamm.ParameterValues:
    p = pybamm.ParameterValues("Chayambuka2022")
    base = {k: p[k] for k in (
        "Negative particle diffusivity [m2.s-1]", "Positive particle diffusivity [m2.s-1]",
        "Negative electrode exchange-current density [A.m-2]",
        "Positive electrode exchange-current density [A.m-2]",
        "Electrolyte conductivity [S.m-1]", "Electrolyte diffusivity [m2.s-1]")}
    d = design

    def d_n(sto, T):
        plateau = 1 - (1 - d.plateau_d_factor) * 0.5 * (1 + pybamm.tanh((sto - d.plateau_sto_start) / d.plateau_sto_width))
        return d.d_n_scale * base["Negative particle diffusivity [m2.s-1]"](sto, T) * _arrhenius(d.ea_d_n, T) * plateau

    def d_p(sto, T):
        return d.d_p_scale * base["Positive particle diffusivity [m2.s-1]"](sto, T) * _arrhenius(d.ea_d_p, T)

    def j0_n(c_e, c_s, c_max, T):
        return d.k_n_scale * base["Negative electrode exchange-current density [A.m-2]"](c_e, c_s, c_max, T) \
            * _arrhenius(d.ea_k_n, T)

    def j0_p(c_e, c_s, c_max, T):
        return d.k_p_scale * base["Positive electrode exchange-current density [A.m-2]"](c_e, c_s, c_max, T) \
            * _arrhenius(d.ea_k_p, T)

    def sigma_e(c_e, T):
        return d.sigma_e_scale * base["Electrolyte conductivity [S.m-1]"](c_e, T) * _arrhenius(d.ea_electrolyte, T)

    def d_e(c_e, T):
        return base["Electrolyte diffusivity [m2.s-1]"](c_e, T) * _arrhenius(d.ea_electrolyte, T)

    t_k = temperature_c + 273.15
    p.update({
        "Negative particle diffusivity [m2.s-1]": d_n,
        "Positive particle diffusivity [m2.s-1]": d_p,
        "Negative electrode exchange-current density [A.m-2]": j0_n,
        "Positive electrode exchange-current density [A.m-2]": j0_p,
        "Electrolyte conductivity [S.m-1]": sigma_e,
        "Electrolyte diffusivity [m2.s-1]": d_e,
        "Negative particle radius [m]": d.r_n_m,
        "Negative electrode thickness [m]": d.neg_thickness_m,
        "Positive electrode thickness [m]": d.pos_thickness_m,
        "Positive electrode porosity": d.pos_porosity,
        "Initial temperature [K]": t_k,
        "Ambient temperature [K]": t_k,
    })
    # Scale electrode area so the theoretical capacity window equals capacity_ah
    lab_area = p["Electrode height [m]"] * p["Electrode width [m]"]
    lab_cap = theoretical_capacity_ah(p)
    p.update({
        "Electrode width [m]": p["Electrode width [m]"] * d.capacity_ah / lab_cap,
        "Nominal cell capacity [A.h]": d.capacity_ah,
        "Current function [A]": d.capacity_ah,
    })
    assert p["Electrode height [m]"] * p["Electrode width [m]"] > lab_area
    return p


def theoretical_capacity_ah(p: pybamm.ParameterValues) -> float:
    """Capacity between 100% SOC (initial concentrations) and the anode running empty."""
    F = 96485.0
    area = p["Electrode height [m]"] * p["Electrode width [m]"]
    eps, L = p["Negative electrode active material volume fraction"], p["Negative electrode thickness [m]"]
    c_max, c0 = p["Maximum concentration in negative electrode [mol.m-3]"], \
        p["Initial concentration in negative electrode [mol.m-3]"]
    # usable anode stoichiometry ~ from full (c0) to ~0
    return float(F * area * eps * L * c0 / 3600)


ANODE_SEP_VAR = "anode potential at separator [V]"


def _model() -> pybamm.BaseModel:
    model = pybamm.sodium_ion.BasicDFN()
    phi_s_n = model.variables["Negative electrode potential [V]"]
    phi_e_n = model.variables["Negative electrolyte potential [V]"]
    # electrode-electrolyte potential difference at the anode/separator boundary = anode vs a Na reference there
    model.variables[ANODE_SEP_VAR] = pybamm.boundary_value(phi_s_n - phi_e_n, "right")
    return model


def charge_cc(c_rate: float, temperature_c: float, design: CellDesign = CellDesign(),
              start_soc: float = 0.0, v_max: float = V_MAX) -> dict:
    """Constant-current charge until v_max. Returns arrays vs charged SOC (true electrode window).

    `complete` is False if the solver gave up before reaching v_max (results are then partial).
    """
    p = parameter_values(design, temperature_c)
    exp = pybamm.Experiment([f"Charge at {c_rate}C until {v_max} V"],
                            period=f"{max(2.0, 20.0 / c_rate):.1f} seconds")
    sol = None
    for make_solver in (lambda: None,
                        lambda: pybamm.IDAKLUSolver(rtol=1e-6, atol=1e-8),
                        lambda: pybamm.IDAKLUSolver(rtol=1e-7, atol=1e-9, options={"max_num_steps": 200000})):
        sim = pybamm.Simulation(_model(), parameter_values=p, experiment=exp, solver=make_solver(),
                                var_pts={"x_n": 16, "x_s": 8, "x_p": 16, "r_n": 16, "r_p": 12})
        try:
            pybamm.set_logging_level("CRITICAL")
            sol = sim.solve(initial_soc=start_soc)
        except pybamm.SolverError:
            continue
        finally:
            pybamm.set_logging_level("WARNING")
        if sol["Voltage [V]"].entries[-1] >= v_max - 0.005:
            break
    if sol is None:
        raise RuntimeError(f"DFN failed at {c_rate}C, {temperature_c} C")
    q = -sol["Discharge capacity [A.h]"].entries
    return {
        "complete": bool(sol["Voltage [V]"].entries[-1] >= v_max - 0.005),
        "soc": start_soc + q / design.capacity_ah,
        "voltage": sol["Voltage [V]"].entries,
        "anode_v": sol[ANODE_SEP_VAR].entries,
        "time_s": sol["Time [s]"].entries,
    }


def summary(design: CellDesign = CellDesign()) -> dict:
    return asdict(design)


if __name__ == "__main__":
    import time
    import warnings

    warnings.simplefilter("ignore")
    for T in (25.0, 0.0):
        for c in (0.1, 1.0, 2.0, 3.0, 4.0):
            t0 = time.time()
            r = charge_cc(c, T)
            neg = np.flatnonzero(r["anode_v"] < 0)
            onset = f"{r['soc'][neg[0]]:.2f}" if neg.size else "none"
            print(f"T={T:>4}C {c:>3}C: reaches SOC {r['soc'][-1]:.3f} at 4.2 V, "
                  f"min anode {r['anode_v'].min() * 1000:6.1f} mV, plating onset SOC {onset}  ({time.time() - t0:.1f}s)")
