"""
Design specification for the "Hexa-Blade" 110 kWh sodium-ion pack.

Architecture follows 800 V-class platforms (Porsche Taycan J1, Hyundai E-GMP):
cell-to-pack, liquid cold plates, ~730 V nominal. Cells are long hexagonal-prism
"blades" (BYD Blade-style, spanning the pack width) stacked in a honeycomb.

Everything here is a design assumption you can change; derived values are computed.
"""

from dataclasses import dataclass, field
from math import sqrt

import numpy as np

from . import chemistry


@dataclass(frozen=True)
class CellSpec:
    capacity_ah: float = 150.0
    v_max: float = 4.20                # charge cut-off per cell
    v_min: float = 2.30
    across_flats_m: float = 0.046      # hexagon width, flat to flat
    length_m: float = 1.40             # blade length (spans pack width)
    specific_energy_wh_kg: float = 140.0
    heat_capacity_j_kgk: float = 1000.0
    # Electrical (ECM) at 25 C
    r0_mohm: float = 0.30              # ohmic + charge-transfer DC resistance
    r1_mohm: float = 0.10              # diffusion RC branch
    tau1_s: float = 30.0
    r_anode_mohm: float = 0.13         # share of resistance that polarizes the anode
    ea_r0_j_mol: float = 20_000        # Arrhenius activation energies
    ea_anode_j_mol: float = 40_000
    max_c_rate: float = 4.0            # absolute cell current limit
    plating_margin_v: float = 0.015    # keep anode >= +15 mV vs Na/Na+
    t_derate_start_c: float = 45.0     # BMS thermal derating window
    t_cutoff_c: float = 55.0

    @property
    def nominal_v(self) -> float:
        return float(np.mean(chemistry.table()["ocv_v"]))

    @property
    def energy_wh(self) -> float:
        return self.capacity_ah * self.nominal_v

    @property
    def mass_kg(self) -> float:
        return self.energy_wh / self.specific_energy_wh_kg

    @property
    def volume_l(self) -> float:
        return sqrt(3) / 2 * self.across_flats_m**2 * self.length_m * 1000

    @property
    def side_m(self) -> float:
        return self.across_flats_m / sqrt(3)


@dataclass(frozen=True)
class PackSpec:
    cell: CellSpec = field(default_factory=CellSpec)
    rows: int = 4                      # honeycomb layers (vertical)
    columns: int = 50                  # cells along the vehicle length
    gap_m: float = 0.001               # adhesive / thermal-interface layer
    # Thermal management
    g_cell_cell_w_k: float = 1.5       # conductance between touching cells
    g_cell_plate_w_k: float = 3.0      # top & bottom rows to corrugated cold plates
    g_cell_ambient_w_k: float = 0.02
    coolant_flow_kg_s: float = 0.5     # ~30 L/min water-glycol
    coolant_cp_j_kgk: float = 3500.0
    chiller_max_w: float = 20_000.0
    coolant_setpoint_c: float = 25.0
    cell_to_pack_mass_ratio: float = 0.80

    @property
    def n_cells(self) -> int:
        return self.rows * self.columns

    @property
    def series(self) -> int:
        return self.n_cells  # 200s1p

    @property
    def energy_kwh(self) -> float:
        return self.n_cells * self.cell.energy_wh / 1000

    @property
    def nominal_v(self) -> float:
        return self.series * self.cell.nominal_v

    @property
    def max_v(self) -> float:
        return self.series * self.cell.v_max

    @property
    def mass_kg(self) -> float:
        return self.n_cells * self.cell.mass_kg / self.cell_to_pack_mass_ratio

    @property
    def pitch_m(self) -> float:
        return self.cell.across_flats_m + self.gap_m

    @property
    def dimensions_m(self) -> tuple[float, float, float]:
        """Length (x), width (y), height (z) of the cell block plus enclosure allowance."""
        p = self.pitch_m
        length = (self.columns + 0.5) * p + 0.10
        width = self.cell.length_m + 0.10
        height = (2 / sqrt(3) + (self.rows - 1) * sqrt(3) / 2) * p + 0.035
        return length, width, height


@dataclass(frozen=True)
class ChargerSpec:
    name: str
    kind: str              # "AC" or "DC"
    power_kw: float
    max_current_a: float   # DC side limit (for AC: unlimited, OBC power-limited)
    max_voltage_v: float
    efficiency: float      # grid -> pack
    note: str = ""


CHARGERS = [
    ChargerSpec("7 kW AC", "AC", 7.0, 1e9, 1e9, 0.93, "Home wallbox, single-phase, on-board charger"),
    ChargerSpec("15 kW AC", "AC", 15.0, 1e9, 1e9, 0.94, "Three-phase AC, 15 kW on-board charger"),
    ChargerSpec("50 kW DC", "DC", 50.0, 125.0, 500.0, 0.95,
                "Legacy 400 V station: vehicle boosts to 800 V via inverter (E-GMP style)"),
    ChargerSpec("125 kW DC", "DC", 125.0, 200.0, 920.0, 0.97, "Mid-power highway CCS"),
    ChargerSpec("250 kW DC", "DC", 250.0, 375.0, 920.0, 0.975, "High-power CCS"),
    ChargerSpec("350 kW DC", "DC", 350.0, 500.0, 920.0, 0.975, "Ultra-fast 800 V CCS (HPC)"),
]

DEFAULT_PACK = PackSpec()


def spec_sheet(pack: PackSpec = DEFAULT_PACK) -> dict:
    c = pack.cell
    L, W, H = pack.dimensions_m
    return {
        "Chemistry": "Sodium-ion — NVPF cathode / hard-carbon anode",
        "Cell format": f"Hexagonal-prism blade, {c.across_flats_m * 1000:.0f} mm across flats × {c.length_m * 1000:.0f} mm",
        "Cell": f"{c.capacity_ah:.0f} Ah, {c.nominal_v:.2f} V nominal, {c.energy_wh:.0f} Wh, {c.mass_kg:.2f} kg",
        "Configuration": f"{pack.series}s1p cell-to-pack, {pack.rows}-layer honeycomb × {pack.columns} columns",
        "Energy": f"{pack.energy_kwh:.1f} kWh",
        "Voltage": f"{pack.nominal_v:.0f} V nominal, {pack.max_v:.0f} V max (800 V class)",
        "Pack size": f"{L:.2f} × {W:.2f} × {H:.2f} m",
        "Pack mass (est.)": f"{pack.mass_kg:.0f} kg",
        "Cooling": f"Corrugated top & bottom cold plates, {pack.coolant_flow_kg_s * 60:.0f} L/min, {pack.chiller_max_w / 1000:.0f} kW chiller",
    }
