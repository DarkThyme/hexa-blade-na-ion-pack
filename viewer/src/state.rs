//! Selection / playback state and the systems that keep the scene in sync with it.

use bevy::prelude::*;

use crate::data::{Inbox, Scenario, Store};
use crate::scene::{spawn_car, spawn_pack, BuiltPack, CarBody, PackPart, Palette};

pub const CONDITIONS: [(f32, bool, &str); 3] = [
    (25.0, true, "25 °C, preconditioned"),
    (-10.0, false, "−10 °C, cold-soaked"),
    (-10.0, true, "−10 °C, preconditioned"),
];
pub const STRATEGIES: [(&str, &str); 2] = [("standard", "Standard BMS"), ("optimized", "Anode-aware BMS")];

#[derive(Resource)]
pub struct ViewState {
    pub arch: String,
    pub charger: String,
    pub condition: usize,
    pub strategy: String,
    pub colour_by_soc: bool,
    pub exploded: bool,
    pub show_body: bool,
    pub selected: Option<usize>,
}

impl Default for ViewState {
    fn default() -> Self {
        Self {
            arch: "blade".into(),
            charger: "350 kW DC".into(),
            condition: 0,
            strategy: "optimized".into(),
            colour_by_soc: true,
            exploded: false,
            show_body: true,
            selected: None,
        }
    }
}

impl ViewState {
    /// Temperature colour range auto-scales to the whole session (min 6 C span); SOC is 0..1.
    pub fn colour_range(&self, sc: &Scenario) -> (f32, f32) {
        if self.colour_by_soc {
            return (0.0, 1.0);
        }
        let s = &sc.doc.series;
        let lo = s.temp_min.iter().cloned().fold(f32::INFINITY, f32::min).floor();
        let hi = s.temp_max.iter().cloned().fold(f32::NEG_INFINITY, f32::max).ceil();
        (lo, hi.max(lo + 6.0))
    }

    pub fn condition(&self) -> (f32, bool) {
        let (a, p, _) = CONDITIONS[self.condition];
        (a, p)
    }
}

#[derive(Resource)]
pub struct Playback {
    pub scenario: Option<String>,
    pub frame: f32,
    pub playing: bool,
    pub speed: f32,
}

impl Default for Playback {
    fn default() -> Self {
        Self { scenario: None, frame: 0.0, playing: true, speed: 1.0 }
    }
}

impl Playback {
    /// Index into the scenario series for the current frame.
    pub fn series_index(&self, sc: &Scenario) -> usize {
        let f = (self.frame.round() as usize).min(sc.doc.frames.count.saturating_sub(1));
        sc.doc.frames.series_index.get(f).copied().unwrap_or(0).min(sc.doc.series.time_min.len().saturating_sub(1))
    }
}

/// Request whatever the current selection needs (manifest -> pack -> scenario + counterpart summaries).
pub fn request_data(view: Res<ViewState>, inbox: Res<Inbox>, mut store: ResMut<Store>, mut pb: ResMut<Playback>) {
    let Some(manifest) = store.manifest.clone() else {
        if store.requested.insert("manifest".into()) {
            inbox.load_manifest();
        }
        return;
    };
    let key = format!("pack:{}", view.arch);
    if !store.packs.contains_key(&view.arch) {
        if let Some(a) = manifest.architectures.iter().find(|a| a.id == view.arch) {
            if store.requested.insert(key) {
                inbox.load_pack(&a.id, &a.pack);
            }
        }
        return;
    }
    let (amb, pre) = view.condition();
    if let Some(e) = store.find(&view.arch, &view.charger, amb, pre, &view.strategy) {
        if !store.scenarios.contains_key(&e.file) && store.requested.insert(e.file.clone()) {
            inbox.load_scenario(&e);
        }
        if pb.scenario.as_deref() != Some(e.file.as_str()) && store.scenarios.contains_key(&e.file) {
            pb.scenario = Some(e.file.clone());
            pb.frame = 0.0;
            pb.playing = true;
        }
    }
    // summaries of every scenario with the same conditions (for the comparison table)
    for s in manifest.scenarios.iter().filter(|s| s.arch == view.arch && (s.ambient_c - amb).abs() < 0.5 && s.precondition == pre) {
        if !store.summaries.contains_key(&s.file) && store.requested.insert(format!("sum:{}", s.file)) {
            inbox.load_summary(s);
        }
    }
}

/// Rebuild the pack (and car) when the architecture changes and its data is available.
#[allow(clippy::too_many_arguments)]
pub fn rebuild_pack(
    mut commands: Commands,
    view: Res<ViewState>,
    store: Res<Store>,
    mut built: ResMut<BuiltPack>,
    mut meshes: ResMut<Assets<Mesh>>,
    mut materials: ResMut<Assets<StandardMaterial>>,
    palette: Res<Palette>,
    old: Query<Entity, Or<(With<PackPart>, With<CarBody>)>>,
    mut pb: ResMut<Playback>,
) {
    if built.0.as_deref() == Some(view.arch.as_str()) {
        return;
    }
    let Some(pack) = store.packs.get(&view.arch) else { return };
    for e in &old {
        commands.entity(e).despawn();
    }
    spawn_car(&mut commands, &mut meshes, &mut materials, pack);
    spawn_pack(&mut commands, &mut meshes, &mut materials, &palette, pack);
    built.0 = Some(view.arch.clone());
    pb.scenario = None; // force re-selection for the new architecture
}

pub fn advance(time: Res<Time>, mut pb: ResMut<Playback>, store: Res<Store>) {
    let Some(sc) = pb.scenario.as_ref().and_then(|id| store.scenarios.get(id)) else { return };
    let last = sc.doc.frames.count.saturating_sub(1) as f32;
    if pb.playing {
        // a full session plays in ~24 s at 1x
        pb.frame += time.delta_secs() * 10.0 * pb.speed;
        if pb.frame >= last {
            pb.frame = last;
            pb.playing = false;
        }
    }
}
