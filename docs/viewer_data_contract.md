# Viewer data contract — `hexablade.v1`

The Python simulation (`ev_pack/`) writes scenario files; the Rust/Bevy web viewer (`viewer/`) only reads them.
Neither side imports the other. All units SI unless the field name says otherwise.

## Files

```
web_data/
  manifest.json                 # index of architectures + scenarios
  <arch>/pack.json              # geometry of one pack architecture
  <arch>/<scenario_id>.json     # one charging session: summary + time series
  <arch>/<scenario_id>.frames.bin  # per-cell animation frames (binary)
```

`<arch>` is `blade` (long hexagonal blades) or `module` (short upright hexagonal cells in modules).

## manifest.json

```json
{
  "schema": "hexablade.v1",
  "generated": "2026-10-20T12:00:00Z",
  "architectures": [
    {"id": "blade", "name": "Hex-Blade (cell-to-pack)", "pack": "blade/pack.json"},
    {"id": "module", "name": "Hex-Cell Modules", "pack": "module/pack.json"}
  ],
  "chargers": ["7 kW AC", "15 kW AC", "50 kW DC", "125 kW DC", "250 kW DC", "350 kW DC"],
  "scenarios": [
    {"id": "350kW_25C_pre_10-90", "arch": "blade", "charger": "350 kW DC", "ambient_c": 25,
     "precondition": true, "strategy": "optimized", "start_soc": 0.10, "target_soc": 0.90,
     "file": "blade/350kW_25C_pre_10-90.json", "frames": "blade/350kW_25C_pre_10-90.frames.bin"}
  ]
}
```

`strategy` is `"standard"` (conventional BMS map) or `"optimized"` (anode-potential-aware protocol).
Scenario `id`s are unique only **within** one architecture (the same id exists under `blade/` and `module/`).
Readers must key scenarios by `file`, which is unique.
SOC values are **displayed (usable-window) SOC**, 0–1.

## pack.json

Coordinate frame: **x = vehicle forward (front is −x), y = left/right, z = up**, origin at pack centre on the pack floor. Metres.

```json
{
  "arch": "module",
  "spec": {"Energy (gross)": "110.0 kWh", "Usable": "103.4 kWh", "...": "..."},
  "dimensions_m": [2.47, 1.50, 0.16],
  "cell_shape": {"type": "hex_prism", "across_flats_m": 0.046, "length_m": 0.095,
                 "axis": "z"},
  "cells": [[x, y, z], ...],          // centre of each cell, index = cell id
  "cell_module": [0, 0, 0, 1, ...],   // module index per cell (all 0 for cell-to-pack)
  "modules": [{"center": [x, y, z], "size": [lx, ly, lz]}],
  "cooling": [{"type": "plate", "center": [x, y, z], "size": [lx, ly, lz]}],
  "coolant": {"inlet": [x, y, z], "outlet": [x, y, z]},
  "vehicle": {"wheelbase_m": 2.95, "track_m": 1.66, "wheel_radius_m": 0.36,
              "body_length_m": 4.85, "body_width_m": 1.95, "body_height_m": 1.65,
              "pack_floor_z_m": 0.16}
}
```

`axis` is the prism's long axis: `"y"` for blades lying across the car, `"z"` for upright cells.
Hexagon orientation:
- `axis "y"` (blades): cross-section in the x–z plane, one pair of flats perpendicular to x
  (vertices point up/down, so stacked layers nest).
- `axis "z"` (upright cells): cross-section in the x–y plane, one pair of flats perpendicular to y
  (cells in a row along y share flat faces; alternate rows are offset by half a pitch in y).

`cells[i]` is the prism centre (half-way along its long axis).

## <scenario_id>.json

```json
{
  "id": "350kW_25C_pre_10-90",
  "summary": {"time_min": 19.4, "energy_to_pack_kwh": 82.1, "peak_power_kw": 341.0,
              "avg_power_kw": 254.0, "peak_temp_c": 44.2, "max_temp_spread_c": 4.1,
              "min_anode_mv": 15.2, "main_limiter": "Na-plating guard",
              "time_to_soc_min": {"0.5": 6.1, "0.8": 13.9, "0.85": 16.2, "0.9": 19.4}},
  "series": {                         // equal length arrays, one sample per step (downsampled to <= 2000)
    "time_min": [], "power_kw": [], "current_a": [], "c_rate": [], "pack_v": [],
    "soc": [], "temp_max": [], "temp_min": [], "temp_mean": [],
    "coolant_in": [], "coolant_out": [], "anode_min_mv": [], "limiter": []
  },
  "limiters": ["Charger power", "Charger current", "Cell C-rate cap", "Na-plating guard",
               "Voltage limit (CV)", "Thermal derate"],
  "frames": {"count": 240, "time_min": [], "series_index": [],
             "temp_offset_c": -20.0, "temp_step_c": 0.25,
             "soc_step": 0.005}
}
```

`limiter` holds strings from `limiters`.

## <scenario_id>.frames.bin

Little-endian, no header. `count` frames, each frame is:

```
u8[n_cells]  temperature: temp_c = temp_offset_c + value * temp_step_c   (−20 … 43.75 °C)
u8[n_cells]  soc:         soc    = value * soc_step                      (0 … 1.275)
```

So the total size is `count * n_cells * 2` bytes. `n_cells = len(pack.cells)`.

Values outside the range are clamped. If a scenario runs hotter than 43.75 °C, the exporter sets `temp_offset_c` so the range covers it, keeping `temp_step_c`.
