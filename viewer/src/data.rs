//! Data model for the `hexablade.v1` contract (docs/viewer_data_contract.md) and async loading
//! that works both in the browser (HTTP fetch) and natively (files next to the binary).

use bevy::prelude::*;
use serde::Deserialize;
use std::collections::HashMap;
use std::sync::{Arc, Mutex};

#[derive(Deserialize, Clone, Debug)]
#[allow(dead_code)]
pub struct Manifest {
    pub schema: String,
    pub architectures: Vec<ArchEntry>,
    pub chargers: Vec<String>,
    pub scenarios: Vec<ScenarioEntry>,
}

#[derive(Deserialize, Clone, Debug)]
pub struct ArchEntry {
    pub id: String,
    pub name: String,
    pub pack: String,
}

#[derive(Deserialize, Clone, Debug)]
pub struct ScenarioEntry {
    pub id: String,
    pub arch: String,
    pub charger: String,
    pub ambient_c: f32,
    pub precondition: bool,
    pub strategy: String,
    pub file: String,
    pub frames: String,
}

#[derive(Deserialize, Clone, Debug)]
pub struct CellShape {
    pub across_flats_m: f32,
    pub length_m: f32,
    pub axis: String,
}

#[derive(Deserialize, Clone, Debug)]
pub struct BoxSpec {
    pub center: [f32; 3],
    pub size: [f32; 3],
}

#[derive(Deserialize, Clone, Debug)]
pub struct Vehicle {
    pub wheelbase_m: f32,
    pub track_m: f32,
    pub wheel_radius_m: f32,
    pub body_length_m: f32,
    pub body_width_m: f32,
    pub body_height_m: f32,
    pub pack_floor_z_m: f32,
}

#[derive(Deserialize, Clone, Debug)]
#[allow(dead_code)]
pub struct Pack {
    pub arch: String,
    pub spec: serde_json::Map<String, serde_json::Value>,
    pub dimensions_m: [f32; 3],
    pub cell_shape: CellShape,
    pub cells: Vec<[f32; 3]>,
    pub cell_module: Vec<usize>,
    pub modules: Vec<BoxSpec>,
    pub cooling: Vec<BoxSpec>,
    pub vehicle: Vehicle,
}

#[derive(Deserialize, Clone, Debug, Default)]
pub struct Series {
    pub time_min: Vec<f32>,
    pub power_kw: Vec<f32>,
    pub current_a: Vec<f32>,
    pub c_rate: Vec<f32>,
    pub pack_v: Vec<f32>,
    pub soc: Vec<f32>,
    pub temp_max: Vec<f32>,
    pub temp_min: Vec<f32>,
    pub temp_mean: Vec<f32>,
    pub coolant_in: Vec<f32>,
    pub coolant_out: Vec<f32>,
    pub anode_min_mv: Vec<f32>,
    pub limiter: Vec<String>,
}

#[derive(Deserialize, Clone, Debug)]
#[allow(dead_code)]
pub struct FramesMeta {
    pub count: usize,
    pub time_min: Vec<f32>,
    pub series_index: Vec<usize>,
    pub temp_offset_c: f32,
    pub temp_step_c: f32,
    pub soc_step: f32,
}

#[derive(Deserialize, Clone, Debug)]
pub struct ScenarioDoc {
    pub id: String,
    pub summary: serde_json::Value,
    pub series: Series,
    pub frames: FramesMeta,
}

/// A fully loaded scenario: JSON plus decoded per-cell frames.
#[derive(Clone)]
pub struct Scenario {
    pub doc: ScenarioDoc,
    pub n_cells: usize,
    /// frames[f][cell] in degrees C / SOC 0..1
    pub temp: Vec<Vec<f32>>,
    pub soc: Vec<Vec<f32>>,
}

impl Scenario {
    pub fn decode(doc: ScenarioDoc, raw: &[u8], n_cells: usize) -> Result<Self, String> {
        let m = &doc.frames;
        let need = m.count * n_cells * 2;
        if raw.len() != need {
            return Err(format!("{}: frames.bin is {} bytes, expected {}", doc.id, raw.len(), need));
        }
        let mut temp = Vec::with_capacity(m.count);
        let mut soc = Vec::with_capacity(m.count);
        for f in 0..m.count {
            let base = f * n_cells * 2;
            temp.push(raw[base..base + n_cells].iter().map(|&v| m.temp_offset_c + v as f32 * m.temp_step_c).collect());
            soc.push(raw[base + n_cells..base + 2 * n_cells].iter().map(|&v| v as f32 * m.soc_step).collect());
        }
        Ok(Self { doc, n_cells, temp, soc })
    }

    pub fn summary_f32(&self, key: &str) -> Option<f32> {
        self.doc.summary.get(key).and_then(|v| v.as_f64()).map(|v| v as f32)
    }

    pub fn time_to_soc(&self, target: &str) -> Option<f32> {
        time_to_soc(&self.doc.summary, target)
    }
}

pub fn time_to_soc(summary: &serde_json::Value, target: &str) -> Option<f32> {
    summary.get("time_to_soc_min")?.get(target)?.as_f64().map(|v| v as f32)
}

// ------------------------------------------------------------------------------------------ loading

pub enum Loaded {
    Manifest(Result<Manifest, String>),
    Pack(String, Result<Pack, String>),
    ScenarioJson(String, Result<ScenarioDoc, String>),
    ScenarioFrames(String, Result<Vec<u8>, String>),
    Summary(String, Result<serde_json::Value, String>),
}

#[derive(Resource, Clone, Default)]
pub struct Inbox(pub Arc<Mutex<Vec<Loaded>>>);

/// Base location of web_data: relative URL in the browser, ../web_data natively.
pub fn base_url() -> String {
    if cfg!(target_arch = "wasm32") { "web_data".to_string() } else { "../web_data".to_string() }
}

fn fetch_bytes(path: String, done: impl 'static + Send + FnOnce(Result<Vec<u8>, String>)) {
    #[cfg(target_arch = "wasm32")]
    {
        ehttp::fetch(ehttp::Request::get(path.clone()), move |r| {
            done(match r {
                Ok(resp) if resp.ok => Ok(resp.bytes),
                Ok(resp) => Err(format!("{path}: HTTP {}", resp.status)),
                Err(e) => Err(format!("{path}: {e}")),
            })
        });
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        done(std::fs::read(&path).map_err(|e| format!("{path}: {e}")));
    }
}

fn parse<T: for<'de> Deserialize<'de>>(r: Result<Vec<u8>, String>) -> Result<T, String> {
    r.and_then(|b| serde_json::from_slice(&b).map_err(|e| e.to_string()))
}

impl Inbox {
    fn push(&self, l: Loaded) {
        self.0.lock().unwrap().push(l);
    }

    pub fn load_manifest(&self) {
        let me = self.clone();
        fetch_bytes(format!("{}/manifest.json", base_url()), move |r| me.push(Loaded::Manifest(parse(r))));
    }

    pub fn load_pack(&self, arch: &str, path: &str) {
        let (me, a) = (self.clone(), arch.to_string());
        fetch_bytes(format!("{}/{path}", base_url()), move |r| me.push(Loaded::Pack(a, parse(r))));
    }

    pub fn load_scenario(&self, e: &ScenarioEntry) {
        let (me, id) = (self.clone(), e.file.clone()); // key by file: ids repeat across architectures
        fetch_bytes(format!("{}/{}", base_url(), e.file), move |r| me.push(Loaded::ScenarioJson(id, parse(r))));
        let (me, id) = (self.clone(), e.file.clone()); // key by file: ids repeat across architectures
        fetch_bytes(format!("{}/{}", base_url(), e.frames), move |r| me.push(Loaded::ScenarioFrames(id, r)));
    }

    /// Only the JSON (for comparing summaries without downloading frames).
    pub fn load_summary(&self, e: &ScenarioEntry) {
        let (me, id) = (self.clone(), e.file.clone()); // key by file: ids repeat across architectures
        fetch_bytes(format!("{}/{}", base_url(), e.file), move |r| {
            me.push(Loaded::Summary(id, parse::<serde_json::Value>(r).map(|v| v["summary"].clone())))
        });
    }

    pub fn drain(&self) -> Vec<Loaded> {
        std::mem::take(&mut *self.0.lock().unwrap())
    }
}

/// Everything loaded so far.
#[derive(Resource, Default)]
pub struct Store {
    pub manifest: Option<Manifest>,
    pub packs: HashMap<String, Pack>,
    pub scenarios: HashMap<String, Scenario>,
    pub pending_json: HashMap<String, ScenarioDoc>,
    pub pending_frames: HashMap<String, Vec<u8>>,
    pub summaries: HashMap<String, serde_json::Value>,
    pub requested: std::collections::HashSet<String>,
    pub errors: Vec<String>,
}

impl Store {
    pub fn find(&self, arch: &str, charger: &str, ambient: f32, pre: bool, strategy: &str) -> Option<ScenarioEntry> {
        self.manifest.as_ref()?.scenarios.iter().find(|s| {
            s.arch == arch && s.charger == charger && (s.ambient_c - ambient).abs() < 0.5
                && s.precondition == pre && s.strategy == strategy
        }).cloned()
    }
}

pub fn receive(inbox: Res<Inbox>, mut store: ResMut<Store>) {
    for item in inbox.drain() {
        match item {
            Loaded::Manifest(Ok(m)) => store.manifest = Some(m),
            Loaded::Pack(a, Ok(p)) => { store.packs.insert(a, p); }
            Loaded::ScenarioJson(id, Ok(d)) => { store.pending_json.insert(id, d); }
            Loaded::ScenarioFrames(id, Ok(b)) => { store.pending_frames.insert(id, b); }
            Loaded::Summary(id, Ok(s)) => { store.summaries.insert(id, s); }
            Loaded::Manifest(Err(e)) | Loaded::Pack(_, Err(e)) | Loaded::ScenarioJson(_, Err(e))
            | Loaded::ScenarioFrames(_, Err(e)) | Loaded::Summary(_, Err(e)) => store.errors.push(e),
        }
    }
    // assemble scenarios once both halves have arrived
    let ready: Vec<String> = store.pending_json.keys().filter(|k| store.pending_frames.contains_key(*k)).cloned().collect();
    for id in ready {
        let doc = store.pending_json.remove(&id).unwrap();
        let raw = store.pending_frames.remove(&id).unwrap();
        let arch = store.manifest.as_ref()
            .and_then(|m| m.scenarios.iter().find(|s| s.file == id).map(|s| s.arch.clone()));
        let Some(n) = arch.and_then(|a| store.packs.get(&a).map(|p| p.cells.len())) else {
            store.errors.push(format!("{id}: pack not loaded"));
            continue;
        };
        let summary = doc.summary.clone();
        match Scenario::decode(doc, &raw, n) {
            Ok(s) => {
                store.summaries.insert(id.clone(), summary);
                store.scenarios.insert(id, s);
            }
            Err(e) => store.errors.push(e),
        }
    }
}
