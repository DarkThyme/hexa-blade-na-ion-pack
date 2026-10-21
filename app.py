"""
Hexa-Blade Charging Lab - interactive 3D showcase of the 110 kWh sodium-ion pack.

Pure Python: NiceGUI serves the page; its 3D scene (three.js) and charts (ECharts)
are driven entirely from this file.

Run:
    .venv/Scripts/python app.py      ->  http://localhost:8080
"""

from functools import lru_cache

import numpy as np
from matplotlib import colormaps
from matplotlib.colors import to_hex
from nicegui import run, ui

from ev_pack.geometry import build_layout
from ev_pack.simulate import LIMITERS, simulate
from ev_pack.spec import CHARGERS, DEFAULT_PACK, spec_sheet

PACK = DEFAULT_PACK
LAYOUT = build_layout(PACK)
CELL = PACK.cell
LENGTH, WIDTH, HEIGHT = PACK.dimensions_m
FLOOR_Z = 0.03  # top of bottom cold plate
EXPLODE_GAP = 0.07

PALETTE = [to_hex(c) for c in colormaps["turbo"](np.linspace(0.08, 0.95, 64))]
TEMP_RANGE = (10.0, 55.0)
SOC_RANGE = (0.0, 1.0)

LIMITER_COLOR = {
    "Charger power": "blue-6",
    "Charger current": "indigo-5",
    "Cell C-rate cap": "purple-5",
    "Na-plating guard": "orange-8",
    "Voltage limit (CV)": "teal-6",
    "Thermal derate": "red-6",
}
CHARGER_NAMES = [c.name for c in CHARGERS]
SERIES_COLORS = ["#94a3b8", "#22d3ee", "#a3e635", "#facc15", "#fb923c", "#f43f5e"]

AXIS = {"axisLine": {"lineStyle": {"color": "#64748b"}}, "splitLine": {"lineStyle": {"color": "#1e293b"}},
        "axisLabel": {"color": "#94a3b8"}, "nameTextStyle": {"color": "#94a3b8"}}


@lru_cache(maxsize=128)
def cached_sim(charger: str, start: int, target: int, ambient: int, precondition: bool):
    return simulate(charger, start / 100, target / 100, float(ambient), precondition)


def colors_for(values: np.ndarray, lo: float, hi: float) -> list[str]:
    idx = np.clip((values - lo) / (hi - lo) * (len(PALETTE) - 1), 0, len(PALETTE) - 1).astype(int)
    return [PALETTE[i] for i in idx]


def fmt_duration(minutes: float) -> str:
    h, m = divmod(int(round(minutes)), 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


def downsample(n: int, k: int = 500) -> np.ndarray:
    return np.unique(np.linspace(0, n - 1, min(n, k)).round().astype(int))


# ------------------------------------------------------------------ chart options


def session_chart(res) -> dict:
    s = res.series
    i = downsample(len(s["time_min"]))
    t = s["time_min"][i].round(2)
    return {
        "backgroundColor": "transparent",
        "animation": False,
        "tooltip": {"trigger": "axis"},
        "legend": {"textStyle": {"color": "#cbd5e1"}, "top": 0},
        "grid": {"left": 55, "right": 55, "top": 36, "bottom": 36},
        "xAxis": {"type": "value", "name": "min", "max": float(t[-1]), **AXIS},
        "yAxis": [{"type": "value", "name": "kW", **AXIS},
                  {"type": "value", "name": "SOC %", "min": 0, "max": 100, **AXIS, "splitLine": {"show": False}}],
        "series": [
            {"name": "Power to pack", "type": "line", "showSymbol": False, "color": "#22d3ee",
             "areaStyle": {"opacity": 0.12}, "data": np.c_[t, s["power_kw"][i].round(1)].tolist(),
             "markLine": {"symbol": "none", "silent": True, "data": [{"xAxis": 0}],
                          "lineStyle": {"color": "#f8fafc", "type": "solid"}, "label": {"show": False}}},
            {"name": "SOC", "type": "line", "showSymbol": False, "yAxisIndex": 1, "color": "#a3e635",
             "data": np.c_[t, (s["soc_mean"][i] * 100).round(1)].tolist()},
        ],
    }


def thermal_chart(res) -> dict:
    s = res.series
    i = downsample(len(s["time_min"]))
    t = s["time_min"][i].round(2)
    line = lambda name, key, color, dash="solid": {  # noqa: E731
        "name": name, "type": "line", "showSymbol": False, "color": color,
        "lineStyle": {"type": dash}, "data": np.c_[t, s[key][i].round(2)].tolist()}
    hottest = line("Hottest cell", "temp_max", "#f43f5e")
    hottest["markLine"] = {"symbol": "none", "silent": True, "data": [{"xAxis": 0}],
                           "lineStyle": {"color": "#f8fafc"}, "label": {"show": False}}
    return {
        "backgroundColor": "transparent",
        "animation": False,
        "tooltip": {"trigger": "axis"},
        "legend": {"textStyle": {"color": "#cbd5e1"}, "top": 0},
        "grid": {"left": 55, "right": 25, "top": 36, "bottom": 36},
        "xAxis": {"type": "value", "name": "min", "max": float(t[-1]), **AXIS},
        "yAxis": {"type": "value", "name": "°C", "scale": True, **AXIS},
        "series": [hottest, line("Average cell", "temp_mean", "#facc15"), line("Coldest cell", "temp_min", "#38bdf8"),
                   line("Coolant in", "coolant_in", "#64748b", "dashed"),
                   line("Coolant out", "coolant_out", "#94a3b8", "dashed")],
    }


def compare_charts(results: dict) -> tuple[dict, dict]:
    names = list(results)
    times = [round(results[n].summary["time_min"], 1) for n in names]
    bars = {
        "backgroundColor": "transparent",
        "tooltip": {"trigger": "axis"},
        "grid": {"left": 90, "right": 70, "top": 10, "bottom": 30},
        "xAxis": {"type": "log", "name": "min", **AXIS},
        "yAxis": {"type": "category", "data": names, "inverse": True, **AXIS},
        "series": [{"type": "bar", "data": [{"value": v, "itemStyle": {"color": c}} for v, c in zip(times, SERIES_COLORS)],
                    "label": {"show": True, "position": "right", "color": "#e2e8f0",
                              "formatter": "{c} min"}}],
    }
    curves = {
        "backgroundColor": "transparent",
        "tooltip": {"trigger": "axis"},
        "legend": {"textStyle": {"color": "#cbd5e1"}, "top": 0},
        "grid": {"left": 55, "right": 25, "top": 40, "bottom": 36},
        "xAxis": {"type": "value", "name": "SOC %", **AXIS},
        "yAxis": {"type": "value", "name": "kW", **AXIS},
        "series": [],
    }
    for n, color in zip(names, SERIES_COLORS):
        s = results[n].series
        i = downsample(len(s["soc_mean"]), 300)
        curves["series"].append({"name": n, "type": "line", "showSymbol": False, "color": color,
                                 "data": np.c_[(s["soc_mean"][i] * 100).round(2), s["power_kw"][i].round(1)].tolist()})
    return bars, curves


# ------------------------------------------------------------------ page


@ui.page("/")
def index():
    ui.dark_mode(True)
    ui.add_css("""
        body { background: #070b16; }
        .stat-label { font-size: 0.72rem; letter-spacing: .06em; text-transform: uppercase; color: #94a3b8; }
        .stat-value { font-size: 1.35rem; font-weight: 600; color: #f1f5f9; font-variant-numeric: tabular-nums; }
        .card { background: #0f172a; border: 1px solid #1e293b; border-radius: 14px; }
        .legend-bar { height: 10px; border-radius: 5px; }
    """)

    state = {"trange": TEMP_RANGE, "res": None, "frame": 0, "playing": False, "speed": 1, "mode": "Temperature", "exploded": False,
             "selected": None}

    # ---------------- header
    with ui.row().classes("w-full items-end justify-between px-6 pt-5 pb-2"):
        with ui.column().classes("gap-0"):
            ui.label("HEXA-BLADE  ·  Charging Lab").classes("text-2xl font-bold tracking-wide text-slate-100")
            ui.label(f"{PACK.energy_kwh:.0f} kWh sodium-ion · {PACK.series}s hexagonal-blade cell-to-pack · "
                     f"{PACK.nominal_v:.0f} V (800 V class)").classes("text-slate-400")
        ui.label("Simulated: electrochemistry · thermal network · BMS limits").classes("text-xs text-slate-500")

    with ui.row().classes("w-full px-6 gap-5 no-wrap items-start max-lg:flex-wrap"):
        # ================= left: 3D + playback
        with ui.column().classes("grow gap-3 min-w-0").style("flex: 2 1 640px"):
            with ui.card().classes("card w-full p-0 overflow-hidden relative"):
                scene = ui.scene(height=560, grid=False, background_color="#0b1222",
                                 on_click=lambda e: on_cell_click(e)).classes("w-full")
                with scene:
                    # vehicle context (ghost)
                    wheelbase, track = 2.95, 1.66
                    vehicle = []
                    for sx in (-1, 1):
                        for sy in (-1, 1):
                            vehicle.append(scene.cylinder(0.36, 0.36, 0.26, 32)
                                           .move(sx * wheelbase / 2, sy * track / 2, 0.36 - 0.12)
                                           .material("#334155", 0.55))
                    for sy in (-1, 1):  # chassis rails
                        vehicle.append(scene.box(4.2, 0.06, 0.08).move(0, sy * (WIDTH / 2 + 0.06), 0.12)
                                       .material("#334155", 0.6))
                    # enclosure
                    tray = scene.box(LENGTH, WIDTH, 0.02).move(0, 0, 0.0).material("#475569")
                    shell = scene.box(LENGTH, WIDTH, HEIGHT, wireframe=True).move(0, 0, HEIGHT / 2 - 0.01) \
                        .material("#64748b", 0.5)
                    bottom_plate = scene.box(LENGTH - 0.05, WIDTH - 0.04, 0.012).move(0, 0, FLOOR_Z - 0.006) \
                        .material("#1d4ed8", 0.9)
                    top_z = FLOOR_Z + LAYOUT.z.max() + CELL.side_m
                    top_plate = scene.box(LENGTH - 0.05, WIDTH - 0.04, 0.012).move(0, 0, top_z + 0.006) \
                        .material("#60a5fa", 0.10)
                    # cells
                    cell_objs = []
                    for i in range(PACK.n_cells):
                        obj = scene.cylinder(CELL.side_m * 0.985, CELL.side_m * 0.985, CELL.length_m, 6) \
                            .move(float(LAYOUT.x[i]), 0, float(FLOOR_Z + LAYOUT.z[i])).material(PALETTE[10])
                        cell_objs.append(obj)
                    obj_index = {o.id: i for i, o in enumerate(cell_objs)}
                    # coolant ports
                    for sy, label in ((-0.35, "coolant in"), (0.35, "coolant out")):
                        scene.box(0.18, 0.05, 0.05).move(-LENGTH / 2 - 0.09, sy, FLOOR_Z).material("#38bdf8")
                    scene.text("Coolant in ▸", "color:#7dd3fc;font-size:12px").move(-LENGTH / 2 - 0.25, -0.35, 0.1)
                    scene.text("front", "color:#64748b;font-size:11px").move(-LENGTH / 2 - 0.3, 0, -0.05)
                VIEWS = {
                    "Overview": dict(x=-2.3, y=-2.6, z=1.5, look_at_x=0.2, look_at_y=0.15, look_at_z=0.0),
                    "Honeycomb": dict(x=-0.95, y=-1.45, z=0.42, look_at_x=-0.55, look_at_y=-0.7, look_at_z=0.08),
                    "Top": dict(x=0.0, y=-0.9, z=3.2, look_at_x=0.0, look_at_y=0.0, look_at_z=0.0),
                    "Side": dict(x=0.0, y=-3.6, z=0.35, look_at_x=0.0, look_at_y=0.0, look_at_z=0.1),
                }
                scene.move_camera(**VIEWS["Overview"], duration=0)
                with ui.row().classes("absolute top-3 left-3 gap-1 z-10"):
                    for view in VIEWS:
                        ui.button(view, on_click=lambda v=view: scene.move_camera(**VIEWS[v], duration=1.2)).props("dense no-caps unelevated color=grey-10 text-color=grey-4 size=sm")

            # legend + playback
            with ui.card().classes("card w-full px-4 py-3"):
                with ui.row().classes("w-full items-center gap-4 no-wrap"):
                    play_btn = ui.button(icon="play_arrow", on_click=lambda: toggle_play()).props("round color=cyan-7")
                    ui.button(icon="replay", on_click=lambda: seek(0)).props("round flat color=grey-5")
                    timeline = ui.slider(min=0, max=1, value=0, on_change=lambda e: seek(int(e.value), from_slider=True)) \
                        .classes("grow").props("color=cyan-5")
                    clock = ui.label("0 min").classes("text-slate-200 w-28 text-right font-mono")
                    ui.toggle({1: "1×", 2: "2×", 4: "4×"}, value=1,
                              on_change=lambda e: state.update(speed=e.value)).props("dense color=grey-9 toggle-color=cyan-8")
                with ui.row().classes("w-full items-center gap-3 no-wrap mt-1"):
                    legend_lo = ui.label().classes("text-xs text-slate-400 w-14")
                    ui.element("div").classes("legend-bar grow").style(
                        f"background: linear-gradient(90deg, {', '.join(PALETTE[::7])})")
                    legend_hi = ui.label().classes("text-xs text-slate-400 w-14 text-right")
                    legend_title = ui.label().classes("text-xs text-slate-300 w-40 text-right")
                    ui.toggle(["Temperature", "SOC"], value="Temperature",
                              on_change=lambda e: set_mode(e.value)).props("dense color=grey-9 toggle-color=cyan-8")
                    ui.switch("Exploded", on_change=lambda e: set_exploded(e.value)).props("dense color=cyan-6")
                    ui.switch("Vehicle", value=True,
                              on_change=lambda e: [v.visible(e.value) for v in vehicle]).props("dense color=cyan-6")

        # ================= right: controls + live stats
        with ui.column().classes("gap-3").style("flex: 1 1 340px; max-width: 460px"):
            with ui.card().classes("card w-full p-4 gap-3"):
                ui.label("Charger").classes("stat-label")
                charger_toggle = ui.toggle(CHARGER_NAMES, value="350 kW DC", on_change=lambda: rerun()) \
                    .props("spread no-caps color=grey-9 toggle-color=cyan-8").classes("w-full flex-wrap")
                charger_note = ui.label().classes("text-xs text-slate-400 -mt-1")
                with ui.row().classes("w-full gap-6 no-wrap"):
                    with ui.column().classes("grow gap-0"):
                        ui.label("Start SOC").classes("stat-label mb-7")
                        start_sl = ui.slider(min=0, max=90, step=5, value=10, on_change=lambda: rerun()) \
                            .props("label-always color=cyan-6")
                    with ui.column().classes("grow gap-0"):
                        ui.label("Stop at SOC").classes("stat-label mb-7")
                        target_sl = ui.slider(min=20, max=100, step=5, value=80, on_change=lambda: rerun()) \
                            .props("label-always color=cyan-6")
                with ui.row().classes("w-full gap-6 no-wrap items-center"):
                    with ui.column().classes("grow gap-0"):
                        ui.label("Ambient °C").classes("stat-label mb-7")
                        ambient_sl = ui.slider(min=-20, max=45, step=5, value=25, on_change=lambda: rerun()) \
                            .props("label-always color=cyan-6")
                    precond_sw = ui.switch("Battery preconditioning", value=True, on_change=lambda: rerun()) \
                        .props("color=cyan-6").classes("text-slate-300")

            with ui.card().classes("card w-full p-4"):
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label("Live").classes("stat-label")
                    limiter_badge = ui.badge("—").props("color=blue-6").classes("text-sm px-2 py-1")
                stats = {}
                with ui.grid(columns=2).classes("w-full gap-x-6 gap-y-3 mt-2"):
                    for key, label in (("soc", "State of charge"), ("power", "Power to pack"),
                                       ("current", "Current"), ("voltage", "Pack voltage"),
                                       ("tmax", "Hottest cell"), ("spread", "Cell ΔT"),
                                       ("coolant", "Coolant in → out"), ("energy", "Energy added")):
                        with ui.column().classes("gap-0"):
                            ui.label(label).classes("stat-label")
                            stats[key] = ui.label("—").classes("stat-value")
                soc_bar = ui.linear_progress(value=0, show_value=False).props("color=lime-5 track-color=grey-9 rounded size=8px").classes("mt-3")

            with ui.card().classes("card w-full p-4"):
                ui.label("Session result").classes("stat-label")
                result_main = ui.label("—").classes("text-3xl font-bold text-cyan-300")
                result_sub = ui.label().classes("text-sm text-slate-400")
                cell_info = ui.label("Click a cell in the 3D view to inspect it.").classes("text-xs text-slate-500 mt-2")

    # ================= bottom tabs
    with ui.card().classes("card mx-6 my-5 p-0").style("width: calc(100% - 3rem)"):
        with ui.tabs().props("dense align=left active-color=cyan-4 indicator-color=cyan-4").classes("text-slate-300") as tabs:
            t_session = ui.tab("Charging session")
            t_compare = ui.tab("Compare all chargers")
            t_spec = ui.tab("Pack design")
            t_physics = ui.tab("How it works")
        with ui.tab_panels(tabs, value=t_session).classes("w-full bg-transparent"):
            with ui.tab_panel(t_session):
                with ui.row().classes("w-full no-wrap gap-4 max-lg:flex-wrap"):
                    power_chart = ui.echart(session_chart(cached_sim("350 kW DC", 10, 80, 25, True))) \
                        .classes("grow").style("height: 300px; min-width: 300px")
                    temp_chart = ui.echart(thermal_chart(cached_sim("350 kW DC", 10, 80, 25, True))) \
                        .classes("grow").style("height: 300px; min-width: 300px")
            with ui.tab_panel(t_compare):
                compare_note = ui.label().classes("text-slate-400 text-sm mb-2")
                with ui.row().classes("w-full no-wrap gap-4 max-lg:flex-wrap"):
                    bars_chart = ui.echart({}).classes("grow").style("height: 320px; min-width: 300px")
                    curves_chart = ui.echart({}).classes("grow").style("height: 320px; min-width: 300px")
                compare_table = ui.table(columns=[
                    {"name": "charger", "label": "Charger", "field": "charger", "align": "left"},
                    {"name": "time", "label": "Time", "field": "time"},
                    {"name": "peak", "label": "Peak kW", "field": "peak"},
                    {"name": "avg", "label": "Avg kW", "field": "avg"},
                    {"name": "crate", "label": "Peak C-rate", "field": "crate"},
                    {"name": "tmax", "label": "Hottest cell", "field": "tmax"},
                    {"name": "grid", "label": "Grid energy", "field": "grid"},
                    {"name": "limiter", "label": "Main limit", "field": "limiter", "align": "left"},
                ], rows=[]).props("dark flat dense").classes("w-full mt-2 bg-transparent")
            with ui.tab_panel(t_spec):
                with ui.grid(columns=2).classes("gap-x-10 gap-y-2 max-w-4xl"):
                    for k, v in spec_sheet(PACK).items():
                        ui.label(k).classes("stat-label self-center")
                        ui.label(v).classes("text-slate-200")
                ui.markdown(DESIGN_NOTES).classes("text-slate-300 mt-4 max-w-4xl")
            with ui.tab_panel(t_physics):
                ui.markdown(PHYSICS_NOTES).classes("text-slate-300 max-w-4xl")

    # ---------------- behaviour

    def cell_positions(exploded: bool):
        lift = LAYOUT.row * EXPLODE_GAP if exploded else np.zeros(PACK.n_cells)
        return FLOOR_Z + LAYOUT.z + lift

    def set_exploded(on: bool):
        state["exploded"] = on
        z = cell_positions(on)
        for i, obj in enumerate(cell_objs):
            obj.move(float(LAYOUT.x[i]), 0, float(z[i]))
        extra = (PACK.rows - 1) * EXPLODE_GAP if on else 0
        top_plate.move(0, 0, top_z + 0.006 + extra + (0.05 if on else 0))
        shell.visible(not on)

    def set_mode(mode: str):
        state["mode"] = mode
        if mode == "Temperature":
            lo, hi = state["trange"]
            legend_lo.text, legend_hi.text, legend_title.text = f"{lo:.0f} °C", f"{hi:.0f} °C", "Cell temperature"
        else:
            legend_lo.text, legend_hi.text, legend_title.text = "0 %", "100 %", "Cell state of charge"
        paint()

    def paint():
        res, f = state["res"], state["frame"]
        if res is None:
            return
        if state["mode"] == "Temperature":
            cols = colors_for(res.frame_temp_c[f], *state["trange"])
        else:
            cols = colors_for(res.frame_soc[f], *SOC_RANGE)
        sel = state["selected"]
        for i, (obj, c) in enumerate(zip(cell_objs, cols)):
            obj.material("#ffffff" if i == sel else c)

    def update_stats():
        res, f = state["res"], state["frame"]
        s = res.series
        k = int(res.frame_index[f])
        stats["soc"].text = f"{s['soc_mean'][k] * 100:.1f} %"
        stats["power"].text = f"{s['power_kw'][k]:.1f} kW"
        stats["current"].text = f"{s['current_a'][k]:.0f} A · {s['c_rate'][k]:.2f}C"
        stats["voltage"].text = f"{s['pack_v'][k]:.0f} V"
        stats["tmax"].text = f"{s['temp_max'][k]:.1f} °C"
        stats["spread"].text = f"{s['temp_max'][k] - s['temp_min'][k]:.1f} °C"
        stats["coolant"].text = f"{s['coolant_in'][k]:.0f} → {s['coolant_out'][k]:.0f} °C"
        dt_h = (s["time_min"][1] - s["time_min"][0]) / 60 if len(s["time_min"]) > 1 else 0
        stats["energy"].text = f"{np.sum(s['power_kw'][:k + 1]) * dt_h:.1f} kWh"
        lim = s["limiter"][k]
        limiter_badge.text = f"Limited by: {lim}"
        limiter_badge.props(f"color={LIMITER_COLOR[lim]}")
        soc_bar.value = float(s["soc_mean"][k])
        clock.text = fmt_duration(float(res.frame_time_min[f]))
        t = float(res.frame_time_min[f])
        for chart in (power_chart, temp_chart):
            chart.run_chart_method("setOption", {"series": [{"markLine": {"data": [{"xAxis": t}]}}]})
        if state["selected"] is not None:
            show_cell(state["selected"])

    def seek(frame: int, from_slider: bool = False):
        res = state["res"]
        if res is None:
            return
        state["frame"] = int(np.clip(frame, 0, len(res.frame_time_min) - 1))
        if not from_slider:
            timeline.set_value(state["frame"])
        paint()
        update_stats()

    def tick():
        res = state["res"]
        if not state["playing"] or res is None:
            return
        last = len(res.frame_time_min) - 1
        if state["frame"] >= last:
            toggle_play(False)
            return
        seek(min(last, state["frame"] + state["speed"]))

    def toggle_play(on: bool | None = None):
        res = state["res"]
        state["playing"] = (not state["playing"]) if on is None else on
        if state["playing"] and res is not None and state["frame"] >= len(res.frame_time_min) - 1:
            seek(0)
        play_btn.props(f"icon={'pause' if state['playing'] else 'play_arrow'}")

    def on_cell_click(e):
        for hit in e.hits:
            if hit.object_id in obj_index:
                state["selected"] = obj_index[hit.object_id]
                paint()
                show_cell(state["selected"])
                return

    def show_cell(i: int):
        res, f = state["res"], state["frame"]
        if res is None:
            return
        cell_info.text = (f"Cell #{i + 1} — layer {LAYOUT.row[i] + 1} of {PACK.rows}, column {LAYOUT.col[i] + 1} "
                          f"(front→rear): {res.frame_temp_c[f][i]:.1f} °C, SOC {res.frame_soc[f][i] * 100:.1f} %"
                          f"{' · touches a cold plate' if LAYOUT.cooled[i] else ' · inner layer'}")
        cell_info.classes(replace="text-xs text-cyan-300 mt-2")

    async def rerun():
        args = (charger_toggle.value, int(start_sl.value), int(target_sl.value), int(ambient_sl.value),
                bool(precond_sw.value))
        if args[1] >= args[2]:
            result_main.text, result_sub.text = "—", "Stop SOC must be above start SOC."
            return
        charger = next(c for c in CHARGERS if c.name == args[0])
        charger_note.text = charger.note
        result_sub.text = "simulating…"
        res = await run.io_bound(cached_sim, *args)
        if res is None:  # client disconnected or app stopping
            return
        state["res"] = res
        lo = float(np.floor(res.frame_temp_c.min()))
        state["trange"] = (lo, max(lo + 6.0, float(np.ceil(res.frame_temp_c.max()))))
        set_mode(state["mode"])
        sm = res.summary
        result_main.text = fmt_duration(sm["time_min"])
        result_sub.text = (f"{args[1]}% → {args[2]}% on {charger.name} · {sm['energy_to_pack_kwh']:.1f} kWh into the pack · "
                           f"peak {sm['peak_power_kw']:.0f} kW · hottest cell {sm['peak_temp_c']:.1f} °C")
        timeline.props(f"max={len(res.frame_time_min) - 1}")
        timeline._props["max"] = len(res.frame_time_min) - 1
        power_chart.options.clear()
        power_chart.options.update(session_chart(res))
        power_chart.update()
        temp_chart.options.clear()
        temp_chart.options.update(thermal_chart(res))
        temp_chart.update()
        seek(0)
        toggle_play(True)
        await refresh_compare(args[1:])

    async def refresh_compare(cond):
        start, target, ambient, pre = cond
        compare_note.text = (f"Same conditions for every charger: {start}% → {target}% SOC, {ambient} °C ambient, "
                             f"preconditioning {'on' if pre else 'off'}.")
        results = {}
        for name in CHARGER_NAMES:
            r = await run.io_bound(cached_sim, name, start, target, ambient, pre)
            if r is None:
                return
            results[name] = r
        bars, curves = compare_charts(results)
        for chart, opts in ((bars_chart, bars), (curves_chart, curves)):
            chart.options.clear()
            chart.options.update(opts)
            chart.update()
        compare_table.rows = [{
            "charger": n, "time": fmt_duration(r.summary["time_min"]),
            "peak": f"{r.summary['peak_power_kw']:.0f}", "avg": f"{r.summary['avg_power_kw']:.0f}",
            "crate": f"{r.summary['peak_c_rate']:.2f}C", "tmax": f"{r.summary['peak_temp_c']:.1f} °C",
            "grid": f"{r.summary['energy_from_grid_kwh']:.1f} kWh", "limiter": r.summary["main_limiter"],
        } for n, r in results.items()]
        compare_table.update()

    set_mode("Temperature")
    ui.timer(0.1, tick)
    ui.timer(0.05, rerun, once=True)


DESIGN_NOTES = """
**Why hexagonal blades?** A long hexagonal prism tiles space with zero gaps (a honeycomb), so the pack has
no dead volume, and every cell shares a flat face with up to six neighbours. Spanning the full pack width like a
BYD Blade lets the cells themselves act as cross-members, carrying load in a *cell-to-pack* structure with no modules.

**Why sodium-ion?** No lithium, cobalt or nickel; sodium is abundant and cheap. Hard-carbon anodes take fast
charge well and keep performing in the cold, and cells can be shipped at 0 V. The trade-off is energy density:
about 140 Wh/kg here, so a 110 kWh pack weighs roughly a tonne. That suits an SUV floor (E-GMP class) rather than a low sports sedan.

**800 V class.** 200 cells in series give about 735 V nominal and 840 V at full charge, like the Porsche Taycan
and Hyundai E-GMP. High voltage means lower current for the same power, which means less heat in cables and cells.
On a legacy 400 V / 50 kW station the car boosts the voltage through its motor inverter, as E-GMP does.
"""

PHYSICS_NOTES = """
Each of the 200 cells is simulated individually, with small manufacturing differences in capacity and resistance.

- **Voltage** comes from the open-circuit curve of the NVPF / hard-carbon chemistry (measured data from
  Chayambuka et al. 2022, via PyBaMM), plus resistive and diffusion losses that grow when the cell is cold.
- **Heat** comes from current flowing through those resistances (I²R). It spreads to touching neighbours and
  out through corrugated cold plates on the top and bottom layers. Coolant warms as it flows front → rear,
  so rear cells run warmer and the **inner layers run hottest**: switch to *Exploded* view to see it.
- **The BMS** chooses the charging current every second as the *lowest* of these limits (shown live as
  "Limited by"):
    - **Charger power / current**: what the station can deliver. At low SOC the pack voltage is low, so a
      500 A station cannot reach its full 350 kW.
    - **Na-plating guard**: during charging the hard-carbon anode potential falls. Below 0 V vs sodium, metallic sodium
      plates on it and permanently damages the cell. The BMS keeps a +15 mV margin. Cold cells
      polarise more, so in winter this limit dominates. Try −10 °C with preconditioning off.
    - **Voltage limit (CV)**: near full charge the cell reaches 4.2 V and the current tapers.
    - **Thermal derate**: above 45 °C the current is cut, reaching zero at 55 °C.

This is a concept-level engineering model meant to make the trade-offs visible. It is not a validated product design.
"""


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(title="Hexa-Blade Charging Lab", port=8080, reload=False, show=False, favicon="🔋")
