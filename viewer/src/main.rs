//! Hexa-Blade Charging Lab — Bevy viewer for the sodium-ion pack simulation.
//!
//! Reads `web_data/` (schema hexablade.v1, see docs/viewer_data_contract.md) and shows the pack in a
//! glass car body with every cell coloured by temperature or state of charge during charging.

mod data;
mod geometry;
mod scene;
mod state;
mod studio;
mod ui;

use bevy::prelude::*;
use bevy_egui::{EguiContextPass, EguiPlugin};

fn main() {
    App::new()
        .add_plugins(DefaultPlugins.set(WindowPlugin {
            primary_window: Some(Window {
                title: "Hexa-Blade Charging Lab".into(),
                canvas: Some("#bevy".into()),
                fit_canvas_to_parent: true,
                prevent_default_event_handling: true,
                ..default()
            }),
            ..default()
        }))
        .add_plugins(EguiPlugin { enable_multipass_for_primary_context: true })
        .init_resource::<data::Inbox>()
        .init_resource::<data::Store>()
        .init_resource::<state::ViewState>()
        .init_resource::<state::Playback>()
        .init_resource::<scene::BuiltPack>()
        .init_resource::<scene::Orbit>()
        .add_systems(Startup, scene::setup_scene)
        .add_systems(
            Update,
            (
                data::receive,
                state::request_data,
                state::rebuild_pack,
                state::advance,
                scene::paint_cells,
                scene::explode,
                scene::draw_outlines,
                scene::toggle_body,
            )
                .chain(),
        )
        .add_systems(EguiContextPass, (ui::style, ui::ui, scene::orbit_camera, scene::click_select).chain())
        .run();
}
