# Hexa-Blade Charging Lab — 3D viewer (Rust + Bevy → WebAssembly)

Interactive showcase of the sodium-ion pack simulation. A glass car body sits over the pack, every cell is coloured live by temperature or state of charge, and you can switch architectures, chargers, conditions and BMS strategies.

It only reads `../web_data/` (schema `hexablade.v1`, see `../docs/viewer_data_contract.md`), produced by:

```bash
cd ..
.venv/Scripts/python -m ev_pack.export
```

## Build & run in the browser

One-time setup (Rust via rustup, then):

```bash
rustup target add wasm32-unknown-unknown
cargo install --locked trunk
```

Develop (hot reload, http://127.0.0.1:8090):

```bash
trunk serve --port 8090
```

Release build. Produces a static site in `dist/` (wasm + index.html + web_data/) that any static host can serve, e.g. GitHub Pages, Cloudflare Pages or Netlify:

```bash
trunk build --release
```

`index.html` copies `../web_data` into `dist/web_data` (`data-trunk rel="copy-dir"`).

## Native run (desktop window, reads ../web_data from disk)

```bash
cargo run --release
```

## Controls

- Left-drag: orbit. Right-drag: pan. Wheel: zoom. Click a cell: inspect it.
- Camera presets: Overview, Pack, Honeycomb, Top, Side.
- Exploded view: blades lift by layer, modules spread apart.
- The right panel shows live readouts, including **what the BMS is limited by**, and compares both BMS strategies (time to 80 / 85 / 90%).

## Stack

Bevy 0.16 (PBR, HDR + bloom, mesh picking), bevy_egui 0.34 + egui_plot 0.32, and ehttp for loading. Cells are recoloured by swapping one of 64 shared palette materials, so the GPU batches them and recolouring costs no texture or buffer uploads.
