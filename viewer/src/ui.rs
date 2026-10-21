//! egui overlay. Wide screens: setup panel (left), live readouts (right), playback + charts (bottom).
//! Narrow screens (phones, split panes): a KPI strip on top and a tabbed bottom sheet, so the 3D view stays visible.

use bevy::prelude::*;
use bevy_egui::{egui, EguiContexts};
use egui::{Color32, CornerRadius, Margin, RichText, Stroke};
use egui_plot::{Legend, Line, Plot, PlotPoints, VLine};

use crate::data::{time_to_soc, Scenario, Store};
use crate::geometry::heat;
use crate::scene::{preset, Orbit};
use crate::state::{Playback, ViewState, CONDITIONS, STRATEGIES};

const ACCENT: Color32 = Color32::from_rgb(56, 189, 248);
const DIM: Color32 = Color32::from_rgb(140, 152, 172);
const FAINT: Color32 = Color32::from_rgb(92, 104, 124);
const TEXT: Color32 = Color32::from_rgb(232, 238, 246);
const CARD: Color32 = Color32::from_rgb(19, 25, 38);
const PANEL: Color32 = Color32::from_rgba_premultiplied(9, 12, 20, 242);
const GOOD: Color32 = Color32::from_rgb(74, 222, 128);
const WARN: Color32 = Color32::from_rgb(251, 146, 60);
const HOT: Color32 = Color32::from_rgb(244, 63, 94);

fn c32(c: Color) -> Color32 {
    let s = c.to_srgba();
    Color32::from_rgb((s.red * 255.0) as u8, (s.green * 255.0) as u8, (s.blue * 255.0) as u8)
}

fn fmt_min(m: f32) -> String {
    if m >= 60.0 { format!("{} h {:02} min", (m / 60.0) as u32, (m % 60.0).round() as u32) } else { format!("{m:.1} min") }
}

pub fn style(mut contexts: EguiContexts, mut done: Local<bool>) {
    if *done {
        return;
    }
    let ctx = contexts.ctx_mut();
    let mut style = (*ctx.style()).clone();
    let v = &mut style.visuals;
    *v = egui::Visuals::dark();
    v.panel_fill = PANEL;
    v.window_fill = PANEL;
    v.extreme_bg_color = Color32::from_rgb(12, 16, 26);
    v.faint_bg_color = Color32::from_rgb(16, 21, 32);
    v.window_corner_radius = CornerRadius::same(12);
    v.window_stroke = Stroke::new(1.0_f32, Color32::from_white_alpha(14));
    v.window_shadow = egui::epaint::Shadow { offset: [0, 6], blur: 24, spread: 0, color: Color32::from_black_alpha(110) };
    v.selection.bg_fill = Color32::from_rgb(10, 105, 140);
    v.selection.stroke = Stroke::new(1.0_f32, Color32::from_rgb(125, 211, 252));
    v.hyperlink_color = ACCENT;
    for (w, fill) in [
        (&mut v.widgets.inactive, Color32::from_rgb(25, 32, 47)),
        (&mut v.widgets.hovered, Color32::from_rgb(35, 45, 66)),
        (&mut v.widgets.active, Color32::from_rgb(14, 116, 150)),
        (&mut v.widgets.open, Color32::from_rgb(30, 38, 56)),
    ] {
        w.corner_radius = CornerRadius::same(7);
        w.weak_bg_fill = fill;
        w.bg_fill = fill;
        w.bg_stroke = Stroke::NONE;
    }
    v.widgets.inactive.fg_stroke = Stroke::new(1.0_f32, Color32::from_rgb(196, 206, 222));
    v.widgets.hovered.fg_stroke = Stroke::new(1.0_f32, Color32::WHITE);
    v.widgets.noninteractive.bg_stroke = Stroke::new(1.0_f32, Color32::from_white_alpha(12));
    v.widgets.noninteractive.fg_stroke = Stroke::new(1.0_f32, Color32::from_rgb(200, 210, 224));
    let s = &mut style.spacing;
    s.item_spacing = egui::vec2(6.0, 6.0);
    s.button_padding = egui::vec2(10.0, 5.0);
    s.window_margin = Margin::same(12);
    s.interact_size.y = 26.0;
    use egui::{FontFamily::Proportional, FontId, TextStyle};
    style.text_styles = [
        (TextStyle::Heading, FontId::new(20.0, Proportional)),
        (TextStyle::Body, FontId::new(13.5, Proportional)),
        (TextStyle::Button, FontId::new(13.5, Proportional)),
        (TextStyle::Small, FontId::new(11.0, Proportional)),
        (TextStyle::Monospace, FontId::new(13.0, egui::FontFamily::Monospace)),
    ]
    .into();
    ctx.set_style(style);
    *done = true;
}

// ------------------------------------------------------------------ small widgets

fn section(ui: &mut egui::Ui, title: &str) {
    ui.add_space(10.0);
    ui.label(RichText::new(title).size(10.5).strong().color(FAINT).extra_letter_spacing(1.2));
    ui.add_space(2.0);
}

fn chip(ui: &mut egui::Ui, selected: bool, label: &str, width: f32) -> bool {
    let text = RichText::new(label).color(if selected { Color32::WHITE } else { Color32::from_rgb(190, 200, 216) });
    ui.add(egui::Button::new(text).selected(selected).min_size(egui::vec2(width, 28.0))).clicked()
}

/// Row of equal-width chips; returns the index clicked.
fn chip_row(ui: &mut egui::Ui, labels: &[&str], selected: impl Fn(usize) -> bool) -> Option<usize> {
    let n = labels.len() as f32;
    let w = ((ui.available_width() - (n - 1.0) * ui.spacing().item_spacing.x) / n).floor();
    let mut hit = None;
    ui.horizontal(|ui| {
        for (k, l) in labels.iter().enumerate() {
            if chip(ui, selected(k), l, w) {
                hit = Some(k);
            }
        }
    });
    hit
}

fn card<R>(ui: &mut egui::Ui, add: impl FnOnce(&mut egui::Ui) -> R) -> R {
    egui::Frame::new().fill(CARD).corner_radius(CornerRadius::same(10)).inner_margin(Margin::symmetric(10, 8))
        .show(ui, add).inner
}

/// Label above a big value with a small unit.
fn metric(ui: &mut egui::Ui, label: &str, value: &str, unit: &str, colour: Color32, size: f32) {
    ui.vertical(|ui| {
        ui.spacing_mut().item_spacing.y = 1.0;
        ui.label(RichText::new(label).size(10.5).color(DIM));
        ui.horizontal(|ui| {
            ui.spacing_mut().item_spacing.x = 3.0;
            ui.label(RichText::new(value).size(size).strong().color(colour));
            if !unit.is_empty() {
                ui.label(RichText::new(unit).size(size * 0.55).color(DIM));
            }
        });
    });
}

fn pill(ui: &mut egui::Ui, text: &str, colour: Color32) {
    egui::Frame::new()
        .fill(colour.gamma_multiply(0.18))
        .stroke(Stroke::new(1.0_f32, colour.gamma_multiply(0.6)))
        .corner_radius(CornerRadius::same(255))
        .inner_margin(Margin::symmetric(10, 3))
        .show(ui, |ui| {
            ui.label(RichText::new(text).size(12.0).strong().color(colour));
        });
}

fn limiter_colour(lim: &str) -> Color32 {
    match lim {
        "Na-plating guard" => WARN,
        "Thermal derate" => HOT,
        "Voltage limit (CV)" => Color32::from_rgb(45, 212, 191),
        _ => ACCENT,
    }
}

/// Charge view legend: an empty glass cell next to a filling one.
fn fill_legend(ui: &mut egui::Ui) {
    let (rect, _) = ui.allocate_exact_size(egui::vec2(ui.available_width(), 10.0), egui::Sense::hover());
    let painter = ui.painter_at(rect);
    painter.rect_filled(rect, CornerRadius::same(3), Color32::from_rgba_unmultiplied(170, 210, 255, 28));
    let full = egui::Rect::from_min_max(rect.min, egui::pos2(rect.left() + rect.width() * 0.62, rect.bottom()));
    painter.rect_filled(full, CornerRadius::same(3), Color32::from_rgb(41, 220, 98));
    ui.painter().rect_stroke(rect, CornerRadius::same(3), Stroke::new(1.0_f32, Color32::from_white_alpha(40)), egui::StrokeKind::Inside);
    ui.horizontal(|ui| {
        ui.label(RichText::new("hollow = empty").size(10.5).color(DIM));
        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
            ui.label(RichText::new("green level = cell SOC").size(10.5).color(DIM));
        });
    });
}

fn colour_legend(ui: &mut egui::Ui, lo: &str, hi: &str) {
    let (rect, _) = ui.allocate_exact_size(egui::vec2(ui.available_width(), 10.0), egui::Sense::hover());
    let n = 64;
    let painter = ui.painter_at(rect);
    for i in 0..n {
        let x0 = rect.left() + rect.width() * i as f32 / n as f32;
        let x1 = rect.left() + rect.width() * (i + 1) as f32 / n as f32;
        painter.rect_filled(egui::Rect::from_min_max(egui::pos2(x0, rect.top()), egui::pos2(x1 + 0.6, rect.bottom())),
                            0.0, c32(heat(i as f32 / (n - 1) as f32)));
    }
    ui.painter().rect_stroke(rect, CornerRadius::same(3), Stroke::new(1.0_f32, Color32::from_black_alpha(120)), egui::StrokeKind::Inside);
    ui.horizontal(|ui| {
        ui.label(RichText::new(lo).size(10.5).color(DIM));
        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
            ui.label(RichText::new(hi).size(10.5).color(DIM));
        });
    });
}

// ------------------------------------------------------------------ sections

fn setup_section(ui: &mut egui::Ui, view: &mut ViewState, store: &Store) {
    let Some(m) = store.manifest.clone() else {
        ui.label(RichText::new("Loading…").color(ACCENT));
        return;
    };
    section(ui, "PACK ARCHITECTURE");
    let names: Vec<&str> = m.architectures.iter().map(|a| a.name.as_str()).collect();
    if let Some(k) = chip_row(ui, &names, |k| view.arch == m.architectures[k].id) {
        if view.arch != m.architectures[k].id {
            view.arch = m.architectures[k].id.clone();
            view.selected = None;
        }
    }
    section(ui, "CHARGER");
    for row in m.chargers.chunks(3) {
        let labels: Vec<&str> = row.iter().map(|s| s.as_str()).collect();
        if let Some(k) = chip_row(ui, &labels, |k| view.charger == row[k]) {
            view.charger = row[k].clone();
        }
    }
    section(ui, "CONDITIONS");
    let w = ui.available_width();
    for (k, (_, _, label)) in CONDITIONS.iter().enumerate() {
        if chip(ui, view.condition == k, label, w) {
            view.condition = k;
        }
    }
    section(ui, "BMS STRATEGY");
    let labels: Vec<&str> = STRATEGIES.iter().map(|s| s.1).collect();
    if let Some(k) = chip_row(ui, &labels, |k| view.strategy == STRATEGIES[k].0) {
        view.strategy = STRATEGIES[k].0.into();
    }
}

fn view_section(ui: &mut egui::Ui, view: &mut ViewState, orbit: &mut Orbit, store: &Store, sc: Option<&Scenario>) {
    section(ui, "SHOW IN CELLS");
    if let Some(k) = chip_row(ui, &["Charge (fill)", "Temperature"], |k| view.colour_by_soc == (k == 0)) {
        view.colour_by_soc = k == 0;
    }
    if let Some(sc) = sc {
        ui.add_space(2.0);
        if view.colour_by_soc {
            fill_legend(ui);
        } else {
            let (lo, hi) = view.colour_range(sc);
            colour_legend(ui, &format!("{lo:.0} °C"), &format!("{hi:.0} °C"));
        }
    }
    section(ui, "VIEW");
    let mut exploded = view.exploded;
    let mut body = view.show_body;
    match chip_row(ui, &["Exploded", "Car body"], |k| if k == 0 { exploded } else { body }) {
        Some(0) => exploded = !exploded,
        Some(_) => body = !body,
        None => {}
    }
    if exploded != view.exploded {
        view.exploded = exploded;
    }
    if body != view.show_body {
        view.show_body = body;
    }
    ui.add_space(2.0);
    let cams = ["Overview", "Pack", "Honeycomb", "Top", "Side", "Drive unit"];
    if let Some(k) = chip_row(ui, &cams[..3], |_| false) {
        orbit.goal = Some(preset(cams[k]));
    }
    if let Some(k) = chip_row(ui, &cams[3..], |_| false) {
        orbit.goal = Some(preset(cams[3 + k]));
    }
    ui.label(RichText::new("Drag to orbit · right-drag to pan · scroll to zoom · click a cell").size(10.5).color(FAINT));

    if let Some(p) = store.packs.get(&view.arch) {
        ui.add_space(6.0);
        egui::CollapsingHeader::new(RichText::new("Pack specification").color(DIM)).default_open(false).show(ui, |ui| {
            egui::Grid::new("spec").num_columns(2).spacing([10.0, 4.0]).show(ui, |ui| {
                for (k, v) in &p.spec {
                    ui.label(RichText::new(k).size(11.0).color(DIM));
                    ui.label(RichText::new(v.as_str().unwrap_or("")).size(12.0));
                    ui.end_row();
                }
            });
        });
    }
    for e in store.errors.iter().take(4) {
        ui.colored_label(Color32::LIGHT_RED, e);
    }
}

fn live_section(ui: &mut egui::Ui, view: &ViewState, pb: &Playback, store: &Store, sc: &Scenario) {
    let s = &sc.doc.series;
    let k = pb.series_index(sc);
    let lim = s.limiter.get(k).cloned().unwrap_or_default();
    ui.horizontal(|ui| {
        ui.label(RichText::new("LIMITED BY").size(10.5).strong().color(FAINT).extra_letter_spacing(1.2));
        pill(ui, &lim, limiter_colour(&lim));
    });
    ui.add_space(6.0);
    card(ui, |ui| {
        ui.set_width(ui.available_width());
        metric(ui, "STATE OF CHARGE", &format!("{:.1}", s.soc[k] * 100.0), "%", Color32::WHITE, 30.0);
        ui.add(egui::ProgressBar::new(s.soc[k]).desired_height(6.0).fill(GOOD).corner_radius(CornerRadius::same(3)));
    });
    ui.add_space(4.0);
    let anode = s.anode_min_mv[k];
    let anode_col = if anode < 20.0 { WARN } else { TEXT };
    let cells: [(&str, String, &str, Color32); 8] = [
        ("POWER", format!("{:.0}", s.power_kw[k]), "kW", ACCENT),
        ("CURRENT", format!("{:.0}", s.current_a[k]), &"A", TEXT),
        ("C-RATE", format!("{:.2}", s.c_rate[k]), "C", TEXT),
        ("PACK VOLTAGE", format!("{:.0}", s.pack_v[k]), "V", TEXT),
        ("HOTTEST CELL", format!("{:.1}", s.temp_max[k]), "°C", if s.temp_max[k] > 45.0 { HOT } else { TEXT }),
        ("CELL SPREAD ΔT", format!("{:.1}", s.temp_max[k] - s.temp_min[k]), "°C", TEXT),
        ("COOLANT IN / OUT", format!("{:.0} / {:.0}", s.coolant_in[k], s.coolant_out[k]), "°C", TEXT),
        ("ANODE MARGIN", format!("{anode:.0}"), "mV", anode_col),
    ];
    let w = ((ui.available_width() - 6.0) / 2.0).floor();
    egui::Grid::new("live").num_columns(2).spacing([6.0, 6.0]).show(ui, |ui| {
        for (i, (label, value, unit, col)) in cells.iter().enumerate() {
            card(ui, |ui| {
                ui.set_width(w - 20.0);
                metric(ui, label, value, unit, *col, 18.0);
            });
            if i % 2 == 1 {
                ui.end_row();
            }
        }
    });
    if let Some(i) = view.selected {
        let f = (pb.frame.round() as usize).min(sc.doc.frames.count - 1);
        if i < sc.n_cells {
            ui.add_space(4.0);
            card(ui, |ui| {
                ui.set_width(ui.available_width());
                ui.label(RichText::new(format!("CELL #{}", i + 1)).size(10.5).strong().color(ACCENT));
                ui.label(RichText::new(format!("{:.1} °C  ·  SOC {:.1} %", sc.temp[f][i], sc.soc[f][i] * 100.0)).size(15.0).color(TEXT));
            });
        }
    }

    section(ui, "10-90 % SESSION");
    card(ui, |ui| {
        ui.set_width(ui.available_width());
        if let Some(t90) = sc.time_to_soc("0.9") {
            ui.label(RichText::new(fmt_min(t90)).size(30.0).strong().color(ACCENT));
        }
        ui.label(RichText::new(format!(
            "peak {:.0} kW  ·  hottest {:.1} °C  ·  aging index {:.2}",
            sc.summary_f32("peak_power_kw").unwrap_or(0.0),
            sc.summary_f32("peak_temp_c").unwrap_or(0.0),
            sc.summary_f32("sei_aging_h25").unwrap_or(0.0)
        )).size(11.0).color(DIM));
    });

    section(ui, "STRATEGY COMPARISON · MINUTES");
    let (amb, pre) = view.condition();
    card(ui, |ui| {
        ui.set_width(ui.available_width());
        egui::Grid::new("cmp").num_columns(4).spacing([14.0, 6.0]).show(ui, |ui| {
            for h in ["", "to 80 %", "to 85 %", "to 90 %"] {
                ui.label(RichText::new(h).size(10.5).color(FAINT));
            }
            ui.end_row();
            for (id, label) in STRATEGIES {
                let sum = store.find(&view.arch, &view.charger, amb, pre, id).and_then(|e| store.summaries.get(&e.file));
                let on = id == view.strategy;
                let col = if on { Color32::WHITE } else { DIM };
                ui.label(RichText::new(if on { format!("● {label}") } else { format!("  {label}") }).size(12.0).color(if on { ACCENT } else { DIM }));
                for t in ["0.8", "0.85", "0.9"] {
                    let v = sum.and_then(|s| time_to_soc(s, t)).map(|v| format!("{v:.1}")).unwrap_or("…".into());
                    ui.label(RichText::new(v).size(13.5).strong().color(col));
                }
                ui.end_row();
            }
        });
    });
}

fn playback_bar(ui: &mut egui::Ui, pb: &mut Playback, sc: &Scenario) {
    let last = sc.doc.frames.count.saturating_sub(1) as f32;
    let s = &sc.doc.series;
    ui.horizontal(|ui| {
        let play = if pb.playing { "⏸" } else { "▶" };
        if ui.add(egui::Button::new(RichText::new(play).size(15.0)).min_size(egui::vec2(34.0, 28.0))).clicked() {
            if !pb.playing && pb.frame >= last {
                pb.frame = 0.0;
            }
            pb.playing = !pb.playing;
        }
        if ui.add(egui::Button::new("⏮").min_size(egui::vec2(30.0, 28.0))).clicked() {
            pb.frame = 0.0;
        }
        let total = s.time_min.last().copied().unwrap_or(0.0);
        let now = s.time_min[pb.series_index(sc)];
        let label = format!("{now:.1} / {total:.1} min");
        ui.spacing_mut().slider_width = (ui.available_width() - 250.0).max(40.0);
        let mut f = pb.frame;
        if ui.add(egui::Slider::new(&mut f, 0.0..=last).show_value(false)).changed() {
            pb.frame = f;
            pb.playing = false;
        }
        ui.label(RichText::new(label).size(12.0).color(DIM).monospace());
        for sp in [1.0, 2.0, 4.0] {
            if ui.add(egui::Button::new(format!("{sp}×")).selected(pb.speed == sp).min_size(egui::vec2(30.0, 24.0))).clicked() {
                pb.speed = sp;
            }
        }
    });
}

fn charts(ui: &mut egui::Ui, pb: &Playback, sc: &Scenario, stacked: bool, height: f32) {
    let s = &sc.doc.series;
    let t_now = s.time_min[pb.series_index(sc)] as f64;
    let pts = |ys: &Vec<f32>, scale: f32| -> PlotPoints {
        s.time_min.iter().zip(ys).map(|(t, y)| [*t as f64, (*y * scale) as f64]).collect()
    };
    let now = || VLine::new("now", t_now).color(Color32::from_white_alpha(160));
    let power = |ui: &mut egui::Ui| {
        Plot::new("power").height(height).legend(Legend::default()).allow_scroll(false).allow_drag(false).allow_zoom(false)
            .x_axis_label("min").y_axis_label("kW").show(ui, |p| {
                p.line(Line::new("Power kW", pts(&s.power_kw, 1.0)).color(ACCENT).fill(0.0_f32).width(2.0_f32));
                p.line(Line::new("SOC × 3.5", pts(&s.soc, 350.0)).color(GOOD).width(1.5_f32));
                p.vline(now());
            });
    };
    let temps = |ui: &mut egui::Ui| {
        Plot::new("temps").height(height).legend(Legend::default()).allow_scroll(false).allow_drag(false).allow_zoom(false)
            .x_axis_label("min").y_axis_label("°C").show(ui, |p| {
                p.line(Line::new("Hottest", pts(&s.temp_max, 1.0)).color(HOT).width(2.0_f32));
                p.line(Line::new("Mean", pts(&s.temp_mean, 1.0)).color(Color32::from_rgb(250, 204, 21)).width(1.5_f32));
                p.line(Line::new("Coldest", pts(&s.temp_min, 1.0)).color(ACCENT).width(1.5_f32));
                p.line(Line::new("Coolant out", pts(&s.coolant_out, 1.0)).color(DIM).width(1.0_f32));
                p.vline(now());
            });
    };
    if stacked {
        power(ui);
        temps(ui);
    } else {
        ui.columns(2, |cols| {
            power(&mut cols[0]);
            temps(&mut cols[1]);
        });
    }
}

fn title(ui: &mut egui::Ui) {
    ui.horizontal(|ui| {
        ui.label(RichText::new("HEXA-BLADE").size(20.0).strong().color(Color32::WHITE).extra_letter_spacing(1.5));
        ui.label(RichText::new("CHARGING LAB").size(11.0).strong().color(ACCENT).extra_letter_spacing(1.2));
    });
    ui.label(RichText::new("110 kWh sodium-ion pack · 800 V class").size(12.0).color(DIM));
}

// ------------------------------------------------------------------ layout

#[derive(Default, Clone, Copy, PartialEq)]
pub enum Tab {
    #[default]
    Hidden,
    Setup,
    Live,
    Charts,
}

#[allow(clippy::too_many_arguments)]
pub fn ui(
    mut contexts: EguiContexts,
    mut view: ResMut<ViewState>,
    mut pb: ResMut<Playback>,
    mut orbit: ResMut<Orbit>,
    store: Res<Store>,
    mut tab: Local<Tab>,
) {
    let ctx = contexts.ctx_mut();
    let sc = pb.scenario.as_ref().and_then(|id| store.scenarios.get(id));
    let screen = ctx.screen_rect();

    if screen.width() >= 1000.0 {
        egui::SidePanel::left("setup").resizable(false).exact_width(300.0).show(ctx, |ui| {
            egui::ScrollArea::vertical().show(ui, |ui| {
                ui.add_space(10.0);
                title(ui);
                setup_section(ui, &mut view, &store);
                view_section(ui, &mut view, &mut orbit, &store, sc);
                ui.add_space(10.0);
            });
        });
        egui::SidePanel::right("live").resizable(false).exact_width(310.0).show(ctx, |ui| {
            egui::ScrollArea::vertical().show(ui, |ui| {
                ui.add_space(10.0);
                match sc {
                    Some(sc) => live_section(ui, &view, &pb, &store, sc),
                    None => { ui.label(RichText::new("Loading simulation…").color(ACCENT)); }
                }
                ui.add_space(10.0);
            });
        });
        if let Some(sc) = sc {
            egui::TopBottomPanel::bottom("charts").resizable(false).exact_height(236.0)
                .frame(egui::Frame::new().fill(PANEL).inner_margin(Margin::symmetric(12, 8)))
                .show(ctx, |ui| {
                    playback_bar(ui, &mut pb, sc);
                    ui.add_space(4.0);
                    charts(ui, &pb, sc, false, 180.0);
                });
        }
        return;
    }

    // ---------------- narrow: KPI strip + bottom sheet
    egui::Area::new("kpis".into()).anchor(egui::Align2::LEFT_TOP, [8.0, 8.0]).show(ctx, |ui| {
        egui::Frame::new().fill(PANEL).corner_radius(CornerRadius::same(12)).inner_margin(Margin::symmetric(12, 8))
            .stroke(Stroke::new(1.0_f32, Color32::from_white_alpha(14)))
            .show(ui, |ui| {
                ui.set_max_width(screen.width() - 40.0);
                title(ui);
                if let Some(sc) = sc {
                    let s = &sc.doc.series;
                    let k = pb.series_index(sc);
                    ui.add_space(4.0);
                    ui.horizontal_wrapped(|ui| {
                        ui.spacing_mut().item_spacing.x = 16.0;
                        metric(ui, "SOC", &format!("{:.0}", s.soc[k] * 100.0), "%", Color32::WHITE, 20.0);
                        metric(ui, "POWER", &format!("{:.0}", s.power_kw[k]), "kW", ACCENT, 20.0);
                        metric(ui, "HOTTEST", &format!("{:.1}", s.temp_max[k]), "°C", TEXT, 20.0);
                        if let Some(t90) = sc.time_to_soc("0.9") {
                            metric(ui, "10-90 %", &format!("{t90:.1}"), "min", ACCENT, 20.0);
                        }
                    });
                    let lim = s.limiter.get(k).cloned().unwrap_or_default();
                    pill(ui, &lim, limiter_colour(&lim));
                }
            });
    });

    // bars (playback + tabs) plus the open tab's content; egui panels don't grow on their own
    let bars = if sc.is_some() { 88.0 } else { 52.0 };
    let sheet = if *tab == Tab::Hidden { 0.0 } else { (screen.height() * 0.48).min(460.0) };
    egui::TopBottomPanel::bottom("sheet").resizable(false).exact_height(bars + sheet)
        .frame(egui::Frame::new().fill(PANEL).inner_margin(Margin::symmetric(10, 8))
            .stroke(Stroke::new(1.0_f32, Color32::from_white_alpha(14))))
        .show(ctx, |ui| {
            if let Some(sc) = sc {
                playback_bar(ui, &mut pb, sc);
            }
            let tabs = [(Tab::Setup, "Setup"), (Tab::Live, "Live"), (Tab::Charts, "Charts")];
            let labels: Vec<&str> = tabs.iter().map(|t| t.1).collect();
            if let Some(k) = chip_row(ui, &labels, |k| *tab == tabs[k].0) {
                *tab = if *tab == tabs[k].0 { Tab::Hidden } else { tabs[k].0 };
            }
            if *tab == Tab::Hidden {
                return;
            }
            egui::ScrollArea::vertical().auto_shrink([false, false]).show(ui, |ui| {
                match *tab {
                    Tab::Setup => {
                        setup_section(ui, &mut view, &store);
                        view_section(ui, &mut view, &mut orbit, &store, sc);
                    }
                    Tab::Live => match sc {
                        Some(sc) => live_section(ui, &view, &pb, &store, sc),
                        None => { ui.label(RichText::new("Loading simulation…").color(ACCENT)); }
                    },
                    Tab::Charts => {
                        if let Some(sc) = sc {
                            charts(ui, &pb, sc, true, 150.0);
                        }
                    }
                    Tab::Hidden => {}
                }
                ui.add_space(6.0);
            });
        });
}
