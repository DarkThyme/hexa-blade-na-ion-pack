"""
Sodium-ion cell chemistry: NVPF cathode (Na3V2(PO4)2F3) / hard-carbon anode.

Open-circuit curves come from the Chayambuka 2022 dataset that ships with PyBaMM
(measured electrode potentials, not made up). From them we build, as functions of
cell state of charge:

  * OCV(SOC)            - cell open-circuit voltage
  * U_anode(SOC)        - hard-carbon potential vs Na/Na+. Charging pulls it down;
                          below 0 V metallic sodium can plate (same failure mode as
                          lithium plating in Phase 2), so the BMS limits current by it.

The table is cached to ev_pack/data/na_ion_ocv.json so the web app does not need
PyBaMM at runtime.
"""

import json
from functools import lru_cache
from pathlib import Path

import numpy as np

CACHE = Path(__file__).parent / "data" / "na_ion_ocv.json"

# From the Chayambuka2022 parameter set (fully charged state and electrode loadings)
C_MAX_N, C_MAX_P = 14540.0, 15320.0  # mol/m3
X_N_FULL, X_P_FULL = 13520 / C_MAX_N, 3320 / C_MAX_P  # stoichiometries at 100% SOC
CAP_N = C_MAX_N * 0.489 * 64e-6  # relative electrode capacities (c_max * eps * thickness)
CAP_P = C_MAX_P * 0.55 * 68e-6
V_MIN_CELL = 2.0  # lower cut-off; defines 0% SOC


def _pybamm_csv(name: str) -> tuple[np.ndarray, np.ndarray]:
    import pybamm  # only needed when (re)building the cache

    path = Path(pybamm.__file__).parent / "input" / "parameters" / "sodium_ion" / "data" / name
    data = np.loadtxt(path, delimiter=",", skiprows=1)
    order = np.argsort(data[:, 0])
    return data[order, 0], data[order, 1]


def build_table(n: int = 201) -> dict:
    xn_data, un_data = _pybamm_csv("U_n.csv")
    xp_data, up_data = _pybamm_csv("U_p.csv")

    def ocv_at(depth):
        """depth = sodium moved from anode back to cathode, in anode-stoichiometry units."""
        xn = X_N_FULL - depth
        xp = X_P_FULL + depth * CAP_N / CAP_P
        un = np.interp(xn, xn_data, un_data)
        up = np.interp(xp, xp_data, up_data)
        return up - un, un

    # Find the depth of discharge where OCV hits the cut-off (or an electrode runs out)
    max_depth = min(X_N_FULL - xn_data[0], (xp_data[-1] - X_P_FULL) * CAP_P / CAP_N)
    depths = np.linspace(0, max_depth, 4000)
    ocv, _ = ocv_at(depths)
    below = np.flatnonzero(ocv <= V_MIN_CELL)
    d0 = depths[below[0]] if below.size else max_depth

    soc = np.linspace(0, 1, n)
    ocv, un = ocv_at(d0 * (1 - soc))
    return {"soc": soc.tolist(), "ocv_v": ocv.tolist(), "anode_v": un.tolist(),
            "source": "PyBaMM Chayambuka2022 (NVPF / hard carbon)"}


@lru_cache(maxsize=1)
def table() -> dict:
    if not CACHE.exists():
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(build_table()))
    t = json.loads(CACHE.read_text())
    return {k: np.asarray(v) if isinstance(v, list) else v for k, v in t.items()}


def ocv(soc):
    t = table()
    return np.interp(soc, t["soc"], t["ocv_v"])


def anode_potential(soc):
    t = table()
    return np.interp(soc, t["soc"], t["anode_v"])


if __name__ == "__main__":
    t = table()
    for s in (0, 0.1, 0.2, 0.5, 0.8, 0.9, 1.0):
        print(f"SOC {s:4.0%}: OCV {ocv(s):.3f} V, anode {anode_potential(s) * 1000:6.1f} mV vs Na")
    print("mean OCV (nominal):", round(float(np.mean(t["ocv_v"])), 3))
