"""
Two pack architectures for the 110 kWh (gross) sodium-ion pack, both ~800 V class.

  blade   204 hexagonal blades 64 mm across (204s1p) lying across the car, cell-to-pack: 2 layers of
          34 lanes, 3 blades end to end per lane. Corrugated cold plates at the bottom, between the two
          layers and on top: every blade touches two plates.
  module  800 upright hexagonal cells 58 mm across (200s4p) in 20 modules of 10s4p on a bottom cold
          plate, like cylindrical-cell modules but with zero-gap hexagonal packing.

The blade count can't drop much below 200: each blade is its own series cell (1p), and 204 x 4.3 V =
877 V stays under the 920 V limit of 800 V-class chargers.

Coordinates follow docs/viewer_data_contract.md: x forward (front = -x), y lateral, z up, origin at the
pack centre on the pack floor. Thermal parameters are lumped engineering estimates (documented inline).
"""

from dataclasses import dataclass, field
from math import sqrt

import numpy as np

SPECIFIC_ENERGY_WH_KG = 150.0   # power-optimised Na-ion cell (CATL Naxtra energy cell: 175 Wh/kg)
CELL_CP_J_KGK = 1000.0
GROSS_KWH = 110.0
N_SERIES = 200            # default / module; the blade pack is 204s


@dataclass
class Architecture:
    id: str
    name: str
    n_parallel: int
    n_series: int
    cell_capacity_ah: float
    cell_shape: dict
    pos: np.ndarray               # (n, 3) cell centres [m]
    group: np.ndarray             # (n,) series-group index 0..n_series-1
    module: np.ndarray            # (n,) module index
    neighbors: np.ndarray         # (m, 2)
    g_neighbor: float | np.ndarray  # W/K between touching cells (scalar or one per neighbour pair)
    g_plate: np.ndarray           # (n,) W/K from cell to its cold plate (0 if none)
    coolant_bin: np.ndarray       # (n,) index of the coolant segment under/over the cell (front -> rear)
    n_bins: int
    modules: list = field(default_factory=list)   # [{"center", "size"}]
    plates: list = field(default_factory=list)    # [{"type", "center", "size"}]
    dims: tuple = (0.0, 0.0, 0.0)
    notes: str = ""

    @property
    def n_cells(self) -> int:
        return len(self.pos)

    @property
    def cell_energy_wh(self) -> float:
        return GROSS_KWH * 1000 / self.n_cells

    @property
    def cell_mass_kg(self) -> float:
        return self.cell_energy_wh / SPECIFIC_ENERGY_WH_KG

    @property
    def cell_heat_capacity(self) -> float:
        return self.cell_mass_kg * CELL_CP_J_KGK


def _bins(x: np.ndarray, n_bins: int) -> np.ndarray:
    edges = np.linspace(x.min() - 1e-9, x.max() + 1e-9, n_bins + 1)
    return np.clip(np.digitize(x, edges) - 1, 0, n_bins - 1)


def blade(columns: int = 34, rows: int = 2, segments: int = 3, across_flats: float = 0.064,
          lane_length: float = 1.40, gap: float = 0.001, seg_gap: float = 0.004, plate: float = 0.010) -> Architecture:
    p = across_flats + gap
    row_pitch = p * sqrt(3) / 2
    circ = p / sqrt(3)
    seg_len = (lane_length - (segments - 1) * seg_gap) / segments
    z0 = 0.018  # top of bottom cold plate
    mid = rows // 2
    pos, row_of, col_of, seg_of = [], [], [], []
    for r in range(rows):
        lift = plate if r >= mid else 0.0
        for c in range(columns):
            for k in range(segments):
                y = -lane_length / 2 + seg_len / 2 + k * (seg_len + seg_gap)
                pos.append((c * p + (p / 2 if r % 2 else 0.0), y, z0 + circ + r * row_pitch + lift))
                row_of.append(r)
                col_of.append(c)
                seg_of.append(k)
    pos = np.array(pos)
    pos[:, 0] -= (pos[:, 0].min() + pos[:, 0].max()) / 2
    row_of, col_of, seg_of = np.array(row_of), np.array(col_of), np.array(seg_of)
    # series string snakes along each lane (alternating direction), then lane to lane
    snake = np.where((col_of * rows + row_of) % 2 == 0, seg_of, segments - 1 - seg_of)
    order = np.lexsort((snake, row_of, col_of))
    group = np.empty(len(pos), dtype=int)
    group[order] = np.arange(len(pos))

    idx = lambda r, c, k: (r * columns + c) * segments + k  # noqa: E731
    side, end = [], []
    for r in range(rows):
        for c in range(columns):
            for k in range(segments):
                if c + 1 < columns:
                    side.append((idx(r, c, k), idx(r, c + 1, k)))
                if k + 1 < segments:
                    end.append((idx(r, c, k), idx(r, c, k + 1)))
                if r + 1 < rows and r + 1 != mid:  # a cold plate separates layers mid-1 and mid
                    for ca in ((c - 1, c) if r % 2 == 0 else (c, c + 1)):
                        if 0 <= ca < columns:
                            side.append((idx(r, c, k), idx(r + 1, ca, k)))
    # Conductances scale with blade length (1.5 / 3.0 W/K for the old 1.40 m blades): the radial path grows
    # with the cell size, but so does the contact face, so per metre of blade they stay the same.
    # End-to-end neighbours only touch through an insulating spacer: small.
    frac = seg_len / 1.40
    g_side, g_end = 1.5 * frac, 0.10
    plates_touched = ((row_of == 0).astype(float) + (row_of == mid - 1) + (row_of == mid) + (row_of == rows - 1))
    L = (columns + 0.5) * p + 0.10
    W = lane_length + 0.10
    top = pos[:, 2].max() + circ
    H = top + plate + 0.02
    plates = [
        {"type": "plate", "center": [0, 0, z0 - 0.006], "size": [L - 0.06, lane_length, 0.012]},
        {"type": "plate", "center": [0, 0, z0 + circ + (mid - 0.5) * row_pitch + plate / 2],
         "size": [L - 0.06, lane_length, plate]},
        {"type": "plate", "center": [0, 0, top + plate / 2], "size": [L - 0.06, lane_length, plate]},
    ]
    return Architecture(
        id="blade", name="Hex-Blade (cell-to-pack)", n_parallel=1, n_series=len(pos),
        cell_capacity_ah=0.0,
        cell_shape={"type": "hex_prism", "across_flats_m": across_flats, "length_m": seg_len, "axis": "y"},
        pos=pos, group=group, module=np.zeros(len(pos), dtype=int),
        # one conductance per pair: side contacts and end-to-end contacts differ
        neighbors=np.array(side + end), g_neighbor=np.r_[np.full(len(side), g_side), np.full(len(end), g_end)],
        # a hexagon face on a corrugated plate per touched plate, through ~32 mm of cell + TIM
        g_plate=3.0 * frac * plates_touched,
        coolant_bin=_bins(pos[:, 0], 25), n_bins=25,
        modules=[{"center": [0, 0, H / 2], "size": [L, W, H]}], plates=plates, dims=(L, W, H),
        notes=f"{len(pos)} blades ({columns} lanes x {rows} layers x {segments} per lane); "
              "every blade touches two cold plates.",
    )


def module(modules_x: int = 4, modules_y: int = 5, rows: int = 10, per_row: int = 4,
           across_flats: float = 0.058, height: float = 0.175, gap: float = 0.001,
           wall: float = 0.010) -> Architecture:
    p = across_flats + gap
    row_pitch = p * sqrt(3) / 2
    mod_len = (rows - 1) * row_pitch + 2 * p / sqrt(3) + 2 * wall      # along x
    mod_wid = (per_row + 0.5) * p + 2 * wall                          # along y
    z_plate_top = 0.020
    pos, group, mod_of, mods = [], [], [], []
    m = 0
    for mx in range(modules_x):
        for my in range(modules_y):
            ox = (mx - (modules_x - 1) / 2) * (mod_len + 0.012)
            oy = (my - (modules_y - 1) / 2) * (mod_wid + 0.012)
            mods.append({"center": [ox, oy, z_plate_top + height / 2], "size": [mod_len, mod_wid, height + 0.01]})
            for r in range(rows):
                for k in range(per_row):
                    x = ox - mod_len / 2 + wall + p / sqrt(3) + r * row_pitch
                    y = oy - mod_wid / 2 + wall + p / 2 + k * p + (p / 2 if r % 2 else 0.0)
                    pos.append((x, y, z_plate_top + height / 2))
                    group.append(m * rows + r)       # each row of per_row cells is one parallel group
                    mod_of.append(m)
            m += 1
    pos = np.array(pos)
    mod_of = np.array(mod_of)
    # neighbours: touching hexagons inside the same module (distance == pitch)
    pairs = []
    for mi in range(m):
        ids = np.flatnonzero(mod_of == mi)
        xy = pos[ids, :2]
        d = np.hypot(xy[:, None, 0] - xy[None, :, 0], xy[:, None, 1] - xy[None, :, 1])
        a, b = np.nonzero(np.triu(np.abs(d - p) < 1e-6))
        pairs.extend(zip(ids[a], ids[b]))
    L = modules_x * (mod_len + 0.012) + 0.08
    W = modules_y * (mod_wid + 0.012) + 0.08
    H = z_plate_top + height + 0.035
    return Architecture(
        id="module", name="Hex-Cell Modules", n_parallel=per_row, n_series=modules_x * modules_y * rows,
        cell_capacity_ah=0.0,
        cell_shape={"type": "hex_prism", "across_flats_m": across_flats, "length_m": height, "axis": "z"},
        pos=pos, group=np.array(group), module=mod_of,
        neighbors=np.array(pairs),
        # face-to-face through two cans + adhesive, limited by radial conductivity of the winding
        g_neighbor=0.15,
        # bottom face on the plate: axial conduction (~25 W/mK) over half the height + TIM;
        # scales with the face area (0.64 W/K at 46 mm across flats)
        g_plate=np.full(len(pos), 0.64 * (across_flats / 0.046) ** 2),
        coolant_bin=_bins(pos[:, 0], 25), n_bins=25,
        modules=mods,
        plates=[{"type": "plate", "center": [0, 0, z_plate_top - 0.008], "size": [L - 0.04, W - 0.04, 0.016]}],
        dims=(L, W, H),
        notes=f"{m} modules of {rows}s{per_row}p upright hex cells on a full-width bottom cold plate.",
    )


ARCHITECTURES = {"blade": blade, "module": module}


def n_series(arch_id: str) -> int:
    return ARCHITECTURES[arch_id]().n_series


def build(arch_id: str, cell_capacity_total_ah: float) -> Architecture:
    """cell_capacity_total_ah = capacity of one series group (all parallel cells together)."""
    a = ARCHITECTURES[arch_id]()
    a.cell_capacity_ah = cell_capacity_total_ah / a.n_parallel
    assert len(np.unique(a.group)) == a.n_series, (arch_id, len(np.unique(a.group)))
    return a
