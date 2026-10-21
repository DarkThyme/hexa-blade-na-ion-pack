//! Studio scene: lighting, orbit camera, glass car body, wheels and the battery pack.

use bevy::core_pipeline::bloom::Bloom;
use bevy::core_pipeline::tonemapping::Tonemapping;
use bevy::input::mouse::{AccumulatedMouseMotion, AccumulatedMouseScroll};
use bevy::pbr::{CascadeShadowConfigBuilder, NotShadowCaster};
use bevy::prelude::*;
use bevy_egui::EguiContexts;

use crate::data::Pack;
use crate::geometry::{car_body, cell_meshes, heat, liquid_mesh, to_bevy, Axis, BodyShape};
use crate::state::{Playback, ViewState};

pub const PALETTE_SIZE: usize = 64;
/// Number of pre-built liquid meshes (fill levels) per pack.
pub const FILL_LEVELS: usize = 48;
const SHELL: usize = usize::MAX - 1;
const SELECTED: usize = usize::MAX;

#[derive(Resource)]
pub struct Palette {
    pub heat: Vec<Handle<StandardMaterial>>,
    pub selected: Handle<StandardMaterial>,
    /// clear glass shell for the charge (water-fill) view
    pub shell: Handle<StandardMaterial>,
    pub liquid: Handle<StandardMaterial>,
}

/// Liquid meshes for the current pack, index = fill level (None = empty).
#[derive(Resource, Default)]
pub struct FillMeshes(pub Vec<Option<Handle<Mesh>>>);

#[derive(Component)]
pub struct Cell {
    pub index: usize,
    pub colour: usize,
    /// child entity holding the green liquid
    pub liquid: Entity,
    pub level: usize,
}

/// Pack part that moves in the exploded view: rests at `home`, moves by `explode` when exploded.
#[derive(Component)]
pub struct Movable {
    pub home: Vec3,
    pub explode: Vec3,
}

#[derive(Component)]
pub struct PackPart; // pack, wheels and drivetrain (despawned on architecture switch)

#[derive(Component)]
pub struct CarBody;

#[derive(Resource, Default)]
pub struct BuiltPack(pub Option<String>);

#[derive(Resource)]
pub struct Orbit {
    pub target: Vec3,
    pub yaw: f32,
    pub pitch: f32,
    pub dist: f32,
    pub goal: Option<(Vec3, f32, f32, f32)>,
}

impl Default for Orbit {
    fn default() -> Self {
        let (t, y, p, d) = preset("Overview");
        Self { target: t, yaw: y, pitch: p, dist: d, goal: None }
    }
}

pub fn preset(name: &str) -> (Vec3, f32, f32, f32) {
    match name {
        "Honeycomb" => (Vec3::new(-0.75, 0.25, -0.62), -0.45, 0.22, 0.9),
        "Top" => (Vec3::new(0.0, 0.2, 0.0), 0.0, 1.35, 4.2),
        "Side" => (Vec3::new(0.0, 0.55, 0.0), std::f32::consts::FRAC_PI_2, 0.08, 6.2),
        "Pack" => (Vec3::new(-0.1, 0.25, 0.0), -0.75, 0.55, 3.0),
        "Drive unit" => (Vec3::new(-1.45, 0.38, 0.0), -0.95, 0.42, 1.7),
        _ => (Vec3::new(0.0, 0.45, 0.0), -0.85, 0.42, 6.4),
    }
}

pub fn setup_scene(
    mut commands: Commands,
    mut meshes: ResMut<Assets<Mesh>>,
    mut materials: ResMut<Assets<StandardMaterial>>,
    mut images: ResMut<Assets<Image>>,
) {
    let bg = Color::srgb(0.018, 0.022, 0.032);
    commands.insert_resource(ClearColor(bg));
    commands.insert_resource(AmbientLight { color: Color::srgb(0.6, 0.7, 1.0), brightness: 40.0, ..default() });
    commands.spawn((
        Camera3d::default(),
        Camera { hdr: true, ..default() },
        Tonemapping::TonyMcMapface,
        Bloom { intensity: 0.12, ..Bloom::NATURAL },
        crate::studio::environment(&mut images),
        DistanceFog { color: bg, falloff: FogFalloff::Linear { start: 9.0, end: 28.0 }, ..default() },
        Transform::from_xyz(-4.0, 3.0, 4.0).looking_at(Vec3::ZERO, Vec3::Y),
    ));
    commands.spawn((
        DirectionalLight { illuminance: 7_000.0, shadows_enabled: true, ..default() },
        CascadeShadowConfigBuilder { maximum_distance: 12.0, first_cascade_far_bound: 4.0, ..default() }.build(),
        Transform::from_xyz(-3.0, 8.0, 4.0).looking_at(Vec3::ZERO, Vec3::Y),
    ));
    for (x, z, c) in [(-4.0, -3.0, Color::srgb(0.45, 0.7, 1.0)), (4.0, 3.0, Color::srgb(1.0, 0.8, 0.6))] {
        commands.spawn((PointLight { intensity: 350_000.0, range: 30.0, color: c, ..default() },
                        Transform::from_xyz(x, 3.5, z)));
    }
    // floor: dark, slightly glossy studio plane (reflects the soft boxes)
    commands.spawn((
        Mesh3d(meshes.add(Plane3d::default().mesh().size(400.0, 400.0))),
        MeshMaterial3d(materials.add(StandardMaterial {
            base_color: Color::srgb(0.024, 0.026, 0.033),
            perceptual_roughness: 0.42,
            reflectance: 0.22,
            ..default()
        })),
        Transform::from_xyz(0.0, -0.001, 0.0),
    ));
    // cell sleeves: clear-coated heat colours; only the hot end of the scale glows under bloom
    let palette: Vec<_> = (0..PALETTE_SIZE).map(|i| {
        let t = i as f32 / (PALETTE_SIZE - 1) as f32;
        let c = heat(t);
        materials.add(StandardMaterial {
            base_color: c,
            emissive: LinearRgba::from(c) * (0.03 + 1.1 * t.powi(4)),
            perceptual_roughness: 0.42,
            metallic: 0.0,
            reflectance: 0.4,
            clearcoat: 1.0,
            clearcoat_perceptual_roughness: 0.12,
            ..default()
        })
    }).collect();
    let selected = materials.add(StandardMaterial {
        base_color: Color::srgba(1.0, 1.0, 1.0, 0.55),
        emissive: LinearRgba::rgb(1.6, 2.6, 3.6),
        alpha_mode: AlphaMode::Blend,
        ..default()
    });
    let shell = materials.add(StandardMaterial {
        base_color: Color::srgba(0.75, 0.88, 1.0, 0.07),
        perceptual_roughness: 0.04,
        reflectance: 0.3,
        alpha_mode: AlphaMode::Blend,
        ..default()
    });
    let liquid = materials.add(StandardMaterial {
        base_color: Color::srgb(0.06, 0.95, 0.28),
        emissive: LinearRgba::rgb(0.06, 1.1, 0.22),
        perceptual_roughness: 0.12,
        reflectance: 0.5,
        clearcoat: 1.0,
        clearcoat_perceptual_roughness: 0.05,
        ..default()
    });
    commands.insert_resource(Palette { heat: palette, selected, shell, liquid });
}

pub fn spawn_car(commands: &mut Commands, meshes: &mut Assets<Mesh>, materials: &mut Assets<StandardMaterial>, pack: &Pack) {
    let v = &pack.vehicle;
    let shape = BodyShape { length: v.body_length_m, width: v.body_width_m, height: v.body_height_m };
    let glass = materials.add(StandardMaterial {
        base_color: Color::srgba(0.75, 0.85, 1.0, 0.05),
        emissive: LinearRgba::rgb(0.01, 0.015, 0.025),
        perceptual_roughness: 0.05,
        metallic: 0.0,
        reflectance: 0.9,
        alpha_mode: AlphaMode::Blend,
        double_sided: true,
        cull_mode: None,
        ..default()
    });
    commands.spawn((Mesh3d(meshes.add(car_body(&shape, 120, 64))), MeshMaterial3d(glass), Transform::default(),
                    CarBody, NotShadowCaster));
    let tyre = materials.add(StandardMaterial { base_color: Color::srgb(0.03, 0.03, 0.035), perceptual_roughness: 0.8, ..default() });
    let rim = materials.add(StandardMaterial { base_color: Color::srgb(0.75, 0.78, 0.82), metallic: 1.0, perceptual_roughness: 0.22, ..default() });
    let r = v.wheel_radius_m;
    for sx in [-1.0f32, 1.0] {
        for sy in [-1.0f32, 1.0] {
            let centre = Vec3::new(sx * v.wheelbase_m / 2.0, r, sy * v.track_m / 2.0);
            commands.spawn((
                Mesh3d(meshes.add(Torus { minor_radius: r * 0.28, major_radius: r * 0.72 })),
                MeshMaterial3d(tyre.clone()),
                Transform::from_translation(centre).with_rotation(Quat::from_rotation_x(std::f32::consts::FRAC_PI_2)),
                PackPart,
            ));
            commands.spawn((
                Mesh3d(meshes.add(Cylinder { radius: r * 0.55, half_height: 0.10 })),
                MeshMaterial3d(rim.clone()),
                Transform::from_translation(centre).with_rotation(Quat::from_rotation_x(std::f32::consts::FRAC_PI_2)),
                PackPart,
            ));
        }
    }
    spawn_drivetrain(commands, meshes, materials, pack);
}

/// Two electric drive units (front + rear axle): finned motor, gearbox, inverter on top, half-shafts with
/// CV boots to the wheels, brake discs with calipers, and orange HV cables from the pack.
fn spawn_drivetrain(commands: &mut Commands, meshes: &mut Assets<Mesh>, materials: &mut Assets<StandardMaterial>, pack: &Pack) {
    let v = &pack.vehicle;
    let r = v.wheel_radius_m;
    let [l, w, _] = pack.dimensions_m;
    let floor = v.pack_floor_z_m;
    let cast = metal(materials, (0.60, 0.62, 0.66), 1.0, 0.5);
    let housing = metal(materials, (0.16, 0.17, 0.19), 0.7, 0.42);
    let steel = metal(materials, (0.55, 0.56, 0.58), 1.0, 0.3);
    let disc = metal(materials, (0.42, 0.42, 0.44), 1.0, 0.55);
    let rubber = materials.add(StandardMaterial { base_color: Color::srgb(0.04, 0.04, 0.045), perceptual_roughness: 0.9, ..default() });
    let caliper = materials.add(StandardMaterial { base_color: Color::srgb(0.75, 0.08, 0.06), perceptual_roughness: 0.35, ..default() });
    let orange = materials.add(StandardMaterial { base_color: Color::srgb(1.0, 0.40, 0.04), perceptual_roughness: 0.5, ..default() });
    let mut put = |commands: &mut Commands, mesh: Mesh, mat: &Handle<StandardMaterial>, tf: Transform| {
        commands.spawn((Mesh3d(meshes.add(mesh)), MeshMaterial3d(mat.clone()), tf, PackPart));
    };
    let tz = v.track_m / 2.0;
    for sx in [-1.0f32, 1.0] {
        let x = sx * v.wheelbase_m / 2.0;
        let y = r;
        // motor: finned cylinder across the car, offset to one side like a real coaxial EDU
        put(commands, Cylinder { radius: 0.125, half_height: 0.16 }.into(), &cast, along_z(Vec3::new(x, y, 0.10)));
        for k in 0..6 {
            put(commands, Cylinder { radius: 0.134, half_height: 0.005 }.into(), &cast,
                along_z(Vec3::new(x, y, -0.04 + k as f32 * 0.055)));
        }
        put(commands, Cylinder { radius: 0.105, half_height: 0.016 }.into(), &housing, along_z(Vec3::new(x, y, 0.275)));
        // gearbox and inverter
        put(commands, Cuboid::new(0.30, 0.30, 0.14).into(), &housing, Transform::from_xyz(x, y - 0.01, -0.13));
        put(commands, Cuboid::new(0.24, 0.07, 0.34).into(), &housing, Transform::from_xyz(x, y + 0.165, 0.06));
        put(commands, Cuboid::new(0.06, 0.035, 0.09).into(), &orange, Transform::from_xyz(x - sx * 0.07, y + 0.215, -0.06));
        // half-shafts with CV boots, brake discs and calipers
        for (z0, z1) in [(-0.20f32, -(tz - 0.10)), (0.285, tz - 0.10)] {
            let (a, b) = (z0.min(z1), z0.max(z1));
            put(commands, Cylinder { radius: 0.02, half_height: (b - a) / 2.0 }.into(), &steel, along_z(Vec3::new(x, y, (a + b) / 2.0)));
            for zb in [z0 + 0.035 * z0.signum(), z1 - 0.04 * z1.signum()] {
                put(commands, Cylinder { radius: 0.042, half_height: 0.035 }.into(), &rubber, along_z(Vec3::new(x, y, zb)));
            }
        }
        for sz in [-1.0f32, 1.0] {
            put(commands, Cylinder { radius: r * 0.46, half_height: 0.012 }.into(), &disc, along_z(Vec3::new(x, y, sz * (tz - 0.12))));
            put(commands, Cuboid::new(0.07, 0.12, 0.06).into(), &caliper,
                Transform::from_xyz(x + sx * r * 0.28, y + r * 0.3, sz * (tz - 0.12)));
        }
        // HV cables: pack outlet -> riser -> inverter
        let pack_end = sx * (l / 2.0 + 0.06);
        let riser_x = x - sx * 0.19;
        let top = y + 0.215;
        for dz in [-0.05f32, 0.05] {
            let z = -w * 0.22 + dz;
            let cable_y = floor + 0.05;
            let run = (riser_x - pack_end).abs();
            put(commands, Cylinder { radius: 0.013, half_height: run / 2.0 }.into(), &orange,
                along_x(Vec3::new((riser_x + pack_end) / 2.0, cable_y, z)));
            put(commands, Sphere::new(0.016).into(), &orange, Transform::from_xyz(riser_x, cable_y, z));
            put(commands, Cylinder { radius: 0.013, half_height: (top - cable_y) / 2.0 }.into(), &orange,
                Transform::from_xyz(riser_x, (top + cable_y) / 2.0, z));
            put(commands, Sphere::new(0.016).into(), &orange, Transform::from_xyz(riser_x, top, z));
            let zi = -0.06;
            put(commands, Cylinder { radius: 0.013, half_height: (zi - z).abs() / 2.0 + 0.01 }.into(), &orange,
                along_z(Vec3::new(riser_x, top, (zi + z) / 2.0)));
        }
    }
}

fn metal(materials: &mut Assets<StandardMaterial>, rgb: (f32, f32, f32), metallic: f32, rough: f32) -> Handle<StandardMaterial> {
    materials.add(StandardMaterial {
        base_color: Color::srgb(rgb.0, rgb.1, rgb.2),
        metallic,
        perceptual_roughness: rough,
        ..default()
    })
}

fn glow(materials: &mut Assets<StandardMaterial>, rgb: (f32, f32, f32), emissive: LinearRgba) -> Handle<StandardMaterial> {
    materials.add(StandardMaterial {
        base_color: Color::srgb(rgb.0, rgb.1, rgb.2),
        emissive,
        perceptual_roughness: 0.4,
        ..default()
    })
}

fn part(commands: &mut Commands, mesh: Handle<Mesh>, mat: &Handle<StandardMaterial>, at: Transform, explode: Vec3) -> Entity {
    commands
        .spawn((Mesh3d(mesh), MeshMaterial3d(mat.clone()), at, PackPart, Movable { home: at.translation, explode }))
        .id()
}

/// Cylinder along Bevy X / Z (the default primitive runs along Y).
fn along_x(at: Vec3) -> Transform {
    Transform::from_translation(at).with_rotation(Quat::from_rotation_z(std::f32::consts::FRAC_PI_2))
}
fn along_z(at: Vec3) -> Transform {
    Transform::from_translation(at).with_rotation(Quat::from_rotation_x(std::f32::consts::FRAC_PI_2))
}

pub fn spawn_pack(
    commands: &mut Commands,
    meshes: &mut Assets<Mesh>,
    materials: &mut Assets<StandardMaterial>,
    palette: &Palette,
    pack: &Pack,
) {
    let floor = pack.vehicle.pack_floor_z_m;
    let lift = Vec3::new(0.0, floor, 0.0);
    let [l, w, h] = pack.dimensions_m;
    let af = pack.cell_shape.across_flats_m;
    let axis = Axis::from_contract(&pack.cell_shape.axis);
    let blade = matches!(axis, Axis::Lateral);
    let at = Transform::from_translation;

    let alu = metal(materials, (0.78, 0.80, 0.83), 1.0, 0.34);
    let cap_mat = metal(materials, (0.88, 0.89, 0.91), 1.0, 0.2);
    let post_mat = metal(materials, (0.93, 0.93, 0.95), 1.0, 0.14);
    let copper = metal(materials, (0.96, 0.6, 0.38), 1.0, 0.26);
    let nickel = metal(materials, (0.90, 0.90, 0.88), 1.0, 0.22);
    let anodized = metal(materials, (0.11, 0.12, 0.14), 0.6, 0.48);
    let steel = metal(materials, (0.32, 0.33, 0.36), 1.0, 0.42);
    let cold_plate = metal(materials, (0.60, 0.67, 0.76), 1.0, 0.36);
    let channel = glow(materials, (0.1, 0.5, 1.0), LinearRgba::rgb(0.12, 0.7, 2.4));
    let hv_orange = materials.add(StandardMaterial { base_color: Color::srgb(1.0, 0.40, 0.04), perceptual_roughness: 0.5, ..default() });
    let hv_label = glow(materials, (1.0, 0.45, 0.05), LinearRgba::rgb(2.2, 0.6, 0.04));
    let coolant_in = metal(materials, (0.12, 0.38, 0.95), 0.3, 0.3);
    let coolant_out = metal(materials, (0.95, 0.32, 0.12), 0.3, 0.3);

    // ---------------------------------------------------------------- enclosure
    let rail_t = 0.028;
    let rail_h = h * 0.5;
    part(commands, meshes.add(Cuboid::new(l + 2.0 * rail_t, 0.012, w + 2.0 * rail_t)), &anodized,
         at(lift - Vec3::Y * 0.006), Vec3::ZERO);
    for s in [-1.0f32, 1.0] {
        part(commands, meshes.add(Cuboid::new(l + 2.0 * rail_t, rail_h, rail_t)), &alu,
             at(lift + Vec3::new(0.0, rail_h / 2.0, s * (w / 2.0 + rail_t / 2.0))), Vec3::ZERO);
        part(commands, meshes.add(Cuboid::new(rail_t, rail_h, w)), &alu,
             at(lift + Vec3::new(s * (l / 2.0 + rail_t / 2.0), rail_h / 2.0, 0.0)), Vec3::ZERO);
        // body mounting tabs with bolts along the sills
        for k in 0..6 {
            let x = -l / 2.0 + 0.18 + k as f32 * (l - 0.36) / 5.0;
            let z = s * (w / 2.0 + rail_t + 0.028);
            part(commands, meshes.add(Cuboid::new(0.075, 0.012, 0.056)), &alu, at(lift + Vec3::new(x, 0.006, z)), Vec3::ZERO);
            part(commands, meshes.add(Cylinder { radius: 0.011, half_height: 0.006 }), &steel,
                 at(lift + Vec3::new(x, 0.018, z)), Vec3::ZERO);
        }
    }

    // ---------------------------------------------------------------- exploded-view stacking (blade)
    // plates and cell layers sorted bottom-up; each item lifts by its rank so the stack opens evenly
    let mut levels: Vec<f32> = pack.cells.iter().map(|c| (c[2] * 1000.0).round()).collect();
    levels.sort_by(|a, b| a.partial_cmp(b).unwrap());
    levels.dedup();
    let mut stack: Vec<f32> = levels.iter().map(|z| z / 1000.0).chain(pack.cooling.iter().map(|c| c.center[2])).collect();
    stack.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let rank_lift = |z: f32| {
        let r = stack.iter().position(|s| (s - z).abs() < 5e-4).unwrap_or(0);
        Vec3::Y * r as f32 * 0.11
    };

    // ---------------------------------------------------------------- cooling plates (+ coolant channels)
    let mut plate_levels: Vec<f32> = pack.cooling.iter().map(|c| c.center[2]).collect();
    plate_levels.sort_by(|a, b| a.partial_cmp(b).unwrap());
    for c in &pack.cooling {
        let level = plate_levels.iter().position(|z| (z - c.center[2]).abs() < 1e-6).unwrap_or(0);
        let (mut sx, sy, sz) = (c.size[0], c.size[2], c.size[1]);
        let mut centre = to_bevy(c.center) + lift;
        // cutaway: the top plate only covers the front of the pack so the cells stay visible
        if level + 1 == plate_levels.len() && plate_levels.len() > 1 {
            centre.x -= sx * (1.0 - 0.24) / 2.0;
            sx *= 0.24;
        }
        let up = if blade { rank_lift(c.center[2]) } else { Vec3::ZERO };
        let e = part(commands, meshes.add(Cuboid::new(sx, sy, sz)), &cold_plate, at(centre), up);
        let strip = meshes.add(Cuboid::new(sx * 0.95, 0.0014, 0.014));
        commands.entity(e).with_children(|p| {
            for k in 0..9 {
                let z = -sz / 2.0 + (k as f32 + 0.5) * sz / 9.0;
                for y in [sy / 2.0 + 0.0007, -sy / 2.0 - 0.0007] {
                    p.spawn((Mesh3d(strip.clone()), MeshMaterial3d(channel.clone()), at(Vec3::new(0.0, y, z)), NotShadowCaster));
                }
            }
        });
    }

    // ---------------------------------------------------------------- cells: sleeve + caps + terminals + liquid
    let (len_m, axis_s) = (pack.cell_shape.length_m, axis);
    let cm = cell_meshes(af, len_m, axis_s);
    let reach = cm.terminal_reach;
    let (sleeve, caps, posts) = (meshes.add(cm.sleeve), meshes.add(cm.caps), meshes.add(cm.terminals));
    let fill: Vec<Option<Handle<Mesh>>> = (0..=FILL_LEVELS)
        .map(|k| liquid_mesh(af, len_m, axis_s, k as f32 / FILL_LEVELS as f32).map(|m| meshes.add(m)))
        .collect();
    let layer_of = |c: &[f32; 3]| levels.iter().position(|z| *z == (c[2] * 1000.0).round()).unwrap_or(0);
    let layer_lift = |layer: usize| rank_lift(levels[layer] / 1000.0);
    let module_shift = |m: usize| {
        pack.modules.get(m).map(|b| {
            let c = to_bevy(b.center);
            Vec3::new(c.x * 0.35, 0.12, c.z * 0.45)
        }).unwrap_or(Vec3::ZERO)
    };
    for (i, c) in pack.cells.iter().enumerate() {
        let home = to_bevy(*c) + lift;
        let shift = if blade { layer_lift(layer_of(c)) } else { module_shift(pack.cell_module.get(i).copied().unwrap_or(0)) };
        let cell = commands
            .spawn((Mesh3d(sleeve.clone()), MeshMaterial3d(palette.shell.clone()), at(home), Movable { home, explode: shift }, PackPart))
            .id();
        let liquid = commands
            .spawn((Mesh3d(fill.last().cloned().flatten().unwrap_or_default()), MeshMaterial3d(palette.liquid.clone()),
                    Transform::default(), Visibility::Hidden, NotShadowCaster, ChildOf(cell)))
            .id();
        commands.spawn((Mesh3d(caps.clone()), MeshMaterial3d(cap_mat.clone()), Transform::default(), ChildOf(cell)));
        commands.spawn((Mesh3d(posts.clone()), MeshMaterial3d(post_mat.clone()), Transform::default(), NotShadowCaster, ChildOf(cell)));
        commands.entity(cell).insert(Cell { index: i, colour: SHELL, liquid, level: usize::MAX });
    }
    commands.insert_resource(FillMeshes(fill));

    // ---------------------------------------------------------------- interconnects
    if blade {
        // copper links: between the blades of a lane (bridging the spacer), and lane to lane at the outer
        // ends, alternating pairs on each side (the zig-zag series string)
        for layer in 0..levels.len() {
            let mut lanes: std::collections::BTreeMap<i32, Vec<Vec3>> = Default::default();
            for c in pack.cells.iter().filter(|c| layer_of(c) == layer) {
                let p = to_bevy(*c) + lift;
                lanes.entry((p.x * 2000.0).round() as i32).or_default().push(p);
            }
            let lanes: Vec<Vec<Vec3>> = lanes.into_values().map(|mut v| {
                v.sort_by(|a, b| a.z.partial_cmp(&b.z).unwrap());
                v
            }).collect();
            let shift = layer_lift(layer);
            for lane in &lanes {
                for w in lane.windows(2) {
                    let (a, b) = (w[0].z + reach, w[1].z - reach);
                    part(commands, meshes.add(Cuboid::new(af * 0.42, af * 0.36, (b - a).abs() + 0.004)), &copper,
                         at(Vec3::new(w[0].x, w[0].y, (a + b) / 2.0)), shift);
                }
            }
            for (side, start) in [(1.0f32, 0usize), (-1.0, 1)] {
                let mut i = start;
                while i + 1 < lanes.len() {
                    let (a, b) = (&lanes[i], &lanes[i + 1]);
                    let end = |l: &Vec<Vec3>| if side > 0.0 { l.last().unwrap().z } else { l[0].z };
                    let z = side * (end(a).abs().max(end(b).abs()) + reach + 0.003);
                    let (pa, pb) = (a[0], b[0]);
                    part(commands, meshes.add(Cuboid::new((pb.x - pa.x).abs() + af * 0.5, af * 0.42, 0.005)), &copper,
                         at(Vec3::new((pa.x + pb.x) / 2.0, (pa.y + pb.y) / 2.0, z)), shift);
                    i += 2;
                }
            }
        }
    } else {
        // nickel strips over each row of a module, housing walls, end plates and HV connectors
        let mut rows: std::collections::BTreeMap<(usize, i32), (f32, f32, f32, f32)> = Default::default();
        for (i, c) in pack.cells.iter().enumerate() {
            let m = pack.cell_module.get(i).copied().unwrap_or(0);
            let p = to_bevy(*c) + lift;
            let e = rows.entry((m, (p.z * 2000.0).round() as i32)).or_insert((f32::MAX, f32::MIN, p.y, p.z));
            e.0 = e.0.min(p.x);
            e.1 = e.1.max(p.x);
        }
        for (&(m, _), &(x0, x1, y, z)) in &rows {
            part(commands, meshes.add(Cuboid::new(x1 - x0 + af * 0.4, 0.0012, af * 0.13)), &nickel,
                 at(Vec3::new((x0 + x1) / 2.0, y + reach + 0.0008, z)), module_shift(m));
        }
        for (m, b) in pack.modules.iter().enumerate() {
            let c = to_bevy(b.center) + lift;
            let (sx, sy, sz) = (b.size[0], b.size[2], b.size[1]);
            let shift = module_shift(m);
            let base = c.y - sy / 2.0;
            for s in [-1.0f32, 1.0] {
                // low side rails so the fill level stays visible from the side
                part(commands, meshes.add(Cuboid::new(sx, sy * 0.22, 0.005)), &anodized,
                     at(Vec3::new(c.x, base + sy * 0.11, c.z + s * (sz / 2.0 - 0.0025))), shift);
                part(commands, meshes.add(Cuboid::new(0.012, sy * 0.92, sz)), &steel,
                     at(Vec3::new(c.x + s * (sx / 2.0 - 0.006), base + sy * 0.46, c.z)), shift);
            }
            part(commands, meshes.add(Cuboid::new(0.03, 0.022, 0.06)), &hv_orange,
                 at(Vec3::new(c.x + sx / 2.0 - 0.02, base + sy * 0.92 + 0.011, c.z)), shift);
        }
        // cross-members between module columns and rows
        let uniq = |mut v: Vec<f32>| {
            v.sort_by(|a, b| a.partial_cmp(b).unwrap());
            v.dedup_by(|a, b| (*a - *b).abs() < 0.01);
            v
        };
        let xs = uniq(pack.modules.iter().map(|b| to_bevy(b.center).x).collect());
        let zs = uniq(pack.modules.iter().map(|b| to_bevy(b.center).z).collect());
        for p in xs.windows(2) {
            part(commands, meshes.add(Cuboid::new(0.012, h * 0.45, w)), &alu,
                 at(lift + Vec3::new((p[0] + p[1]) / 2.0, h * 0.225, 0.0)), Vec3::ZERO);
        }
        for p in zs.windows(2) {
            part(commands, meshes.add(Cuboid::new(l, h * 0.45, 0.012)), &alu,
                 at(lift + Vec3::new(0.0, h * 0.225, (p[0] + p[1]) / 2.0)), Vec3::ZERO);
        }
    }

    // ---------------------------------------------------------------- front: coolant manifold + HV junction box
    let xf = -(l / 2.0 + rail_t);
    let pipe = 0.016;
    for (y, mat) in [(0.05, &coolant_in), (0.11, &coolant_out)] {
        let half = w * 0.2;
        let zc = w * 0.25;
        part(commands, meshes.add(Cylinder { radius: pipe, half_height: half }), mat,
             along_z(lift + Vec3::new(xf - 0.03, y, zc)), Vec3::ZERO);
        part(commands, meshes.add(Sphere::new(pipe * 1.15)), mat, at(lift + Vec3::new(xf - 0.03, y, zc + half)), Vec3::ZERO);
        // feed pipes run forward to the front-end radiator, between the drive unit and the brake
        part(commands, meshes.add(Sphere::new(pipe * 1.15)), mat, at(lift + Vec3::new(xf - 0.03, y, 0.40)), Vec3::ZERO);
        part(commands, meshes.add(Cylinder { radius: pipe, half_height: 0.22 }), mat,
             along_x(lift + Vec3::new(xf - 0.03 - 0.22, y, 0.40)), Vec3::ZERO);
        // stubs into the pack wall
        for k in 0..3 {
            part(commands, meshes.add(Cylinder { radius: pipe * 0.6, half_height: 0.016 }), mat,
                 along_x(lift + Vec3::new(xf - 0.014, y, zc - half * 0.7 + k as f32 * half * 0.7)), Vec3::ZERO);
        }
    }
    let jb = Vec3::new(0.12, 0.11, 0.34);
    let jb_at = lift + Vec3::new(xf - jb.x / 2.0 - 0.005, jb.y / 2.0 + 0.004, -w * 0.22);
    part(commands, meshes.add(Cuboid::new(jb.x, jb.y, jb.z)), &anodized, at(jb_at), Vec3::ZERO);
    part(commands, meshes.add(Cuboid::new(jb.x * 0.7, 0.002, jb.z * 0.6)), &hv_label,
         at(jb_at + Vec3::Y * (jb.y / 2.0 + 0.001)), Vec3::ZERO);
    for dz in [-0.08f32, 0.08] {
        part(commands, meshes.add(Cylinder { radius: 0.022, half_height: 0.012 }), &steel,
             along_x(jb_at + Vec3::new(-jb.x / 2.0 - 0.012, 0.01, dz)), Vec3::ZERO);
    }
    // rear HV outlet for the rear drive unit
    part(commands, meshes.add(Cuboid::new(0.05, 0.06, 0.2)), &hv_orange,
         at(lift + Vec3::new(l / 2.0 + rail_t + 0.025, 0.05, -w * 0.22)), Vec3::ZERO);
}

/// Click (without dragging) selects the nearest cell under the cursor: ray vs. each cell's bounding box.
pub fn click_select(
    buttons: Res<ButtonInput<MouseButton>>,
    windows: Query<&Window>,
    cams: Query<(&Camera, &GlobalTransform), With<Camera3d>>,
    cells: Query<(&Cell, &Transform)>,
    store: Res<crate::data::Store>,
    mut contexts: EguiContexts,
    mut view: ResMut<ViewState>,
    mut press: Local<Option<Vec2>>,
) {
    let Ok(window) = windows.single() else { return };
    let Some(cursor) = window.cursor_position() else { return };
    if buttons.just_pressed(MouseButton::Left) {
        // press and release can land in the same frame for a quick click, so don't return here
        *press = if contexts.ctx_mut().is_pointer_over_area() { None } else { Some(cursor) };
    }
    if !buttons.just_released(MouseButton::Left) {
        return;
    }
    let Some(start) = press.take() else { return };
    if start.distance(cursor) > 4.0 {
        return; // that was an orbit drag
    }
    let Ok((cam, cam_tf)) = cams.single() else { return };
    let Ok(ray) = cam.viewport_to_world(cam_tf, cursor) else { return };
    let Some(pack) = store.packs.get(&view.arch) else { return };
    let r = pack.cell_shape.across_flats_m / 3f32.sqrt();
    let h = pack.cell_shape.length_m / 2.0;
    let half = if pack.cell_shape.axis == "y" { Vec3::new(r, r, h) } else { Vec3::new(r, h, r) };
    let (o, d) = (ray.origin, *ray.direction);
    let mut best: Option<(f32, usize)> = None;
    for (cell, tf) in &cells {
        let (lo, hi) = (tf.translation - half, tf.translation + half);
        let (mut t0, mut t1) = (0.0f32, f32::INFINITY);
        for k in 0..3 {
            if d[k].abs() < 1e-9 {
                if o[k] < lo[k] || o[k] > hi[k] { t0 = f32::INFINITY; break; }
                continue;
            }
            let (a, b) = ((lo[k] - o[k]) / d[k], (hi[k] - o[k]) / d[k]);
            t0 = t0.max(a.min(b));
            t1 = t1.min(a.max(b));
        }
        if t0 <= t1 && best.is_none_or(|(bt, _)| t0 < bt) {
            best = Some((t0, cell.index));
        }
    }
    view.selected = match best {
        Some((_, i)) if view.selected != Some(i) => Some(i),
        _ => None,
    };
}

/// Exploded view: blades (with their busbars and cold plates) lift by layer, modules spread apart.
pub fn explode(view: Res<ViewState>, time: Res<Time>, mut parts: Query<(&Movable, &mut Transform)>) {
    let target = if view.exploded { 1.0 } else { 0.0 };
    let k = (time.delta_secs() * 5.0).min(1.0);
    for (m, mut tf) in &mut parts {
        if m.explode == Vec3::ZERO {
            continue;
        }
        let goal = m.home + m.explode * target;
        if tf.translation.distance_squared(goal) > 1e-10 {
            tf.translation = tf.translation.lerp(goal, k);
        }
    }
}

pub fn orbit_camera(
    mut orbit: ResMut<Orbit>,
    motion: Res<AccumulatedMouseMotion>,
    scroll: Res<AccumulatedMouseScroll>,
    buttons: Res<ButtonInput<MouseButton>>,
    mut contexts: EguiContexts,
    time: Res<Time>,
    mut cam: Query<&mut Transform, With<Camera3d>>,
) {
    let over_ui = contexts.ctx_mut().wants_pointer_input() || contexts.ctx_mut().is_pointer_over_area();
    if !over_ui {
        if buttons.pressed(MouseButton::Left) && motion.delta != Vec2::ZERO {
            orbit.yaw -= motion.delta.x * 0.006;
            orbit.pitch = (orbit.pitch + motion.delta.y * 0.006).clamp(-0.05, 1.5);
            orbit.goal = None;
        }
        if buttons.pressed(MouseButton::Right) && motion.delta != Vec2::ZERO {
            let right = Vec3::new(orbit.yaw.cos(), 0.0, -orbit.yaw.sin());
            let d = orbit.dist * 0.0015;
            orbit.target += -right * motion.delta.x * d + Vec3::Y * motion.delta.y * d;
            orbit.goal = None;
        }
        if scroll.delta.y != 0.0 {
            orbit.dist = (orbit.dist * (1.0 - scroll.delta.y * 0.1)).clamp(0.3, 14.0);
            orbit.goal = None;
        }
    }
    if let Some((t, y, p, d)) = orbit.goal {
        let k = (time.delta_secs() * 3.0).min(1.0);
        orbit.target = orbit.target.lerp(t, k);
        orbit.yaw += (y - orbit.yaw) * k;
        orbit.pitch += (p - orbit.pitch) * k;
        orbit.dist += (d - orbit.dist) * k;
        if (orbit.dist - d).abs() < 1e-3 && (orbit.yaw - y).abs() < 1e-3 {
            orbit.goal = None;
        }
    }
    if let Ok(mut tf) = cam.single_mut() {
        let dir = Vec3::new(orbit.pitch.cos() * orbit.yaw.sin(), orbit.pitch.sin(), orbit.pitch.cos() * orbit.yaw.cos());
        tf.translation = orbit.target + dir * orbit.dist;
        tf.look_at(orbit.target, Vec3::Y);
    }
}

/// Charge view: clear shells with green liquid at each cell's SOC (level meshes swapped per cell).
/// Temperature view: solid heat-coloured sleeves.
#[allow(clippy::too_many_arguments)]
pub fn paint_cells(
    pb: Res<Playback>,
    view: Res<ViewState>,
    store: Res<crate::data::Store>,
    palette: Res<Palette>,
    fill: Option<Res<FillMeshes>>,
    mut cells: Query<(&mut Cell, &mut MeshMaterial3d<StandardMaterial>)>,
    mut liquids: Query<(&mut Mesh3d, &mut Visibility)>,
) {
    let Some(sc) = pb.scenario.as_ref().and_then(|id| store.scenarios.get(id)) else { return };
    let Some(fill) = fill else { return };
    let n = sc.doc.frames.count;
    if n == 0 {
        return;
    }
    let f = pb.frame.clamp(0.0, (n - 1) as f32);
    let (i0, i1) = (f.floor() as usize, (f.ceil() as usize).min(n - 1));
    let a = f - i0 as f32;
    let (lo, hi) = view.colour_range(sc);
    for (mut cell, mut mat) in &mut cells {
        let i = cell.index;
        if i >= sc.n_cells {
            continue;
        }
        let (want, level) = if view.colour_by_soc {
            let soc = sc.soc[i0][i] * (1.0 - a) + sc.soc[i1][i] * a;
            let level = (soc.clamp(0.0, 1.0) * FILL_LEVELS as f32).round() as usize;
            (if view.selected == Some(i) { SELECTED } else { SHELL }, level)
        } else {
            let v = sc.temp[i0][i] * (1.0 - a) + sc.temp[i1][i] * a;
            let idx = (((v - lo) / (hi - lo)).clamp(0.0, 1.0) * (PALETTE_SIZE - 1) as f32).round() as usize;
            (if view.selected == Some(i) { SELECTED } else { idx }, 0)
        };
        if want != cell.colour {
            cell.colour = want;
            mat.0 = match want {
                SELECTED => palette.selected.clone(),
                SHELL => palette.shell.clone(),
                k => palette.heat[k].clone(),
            };
        }
        if level != cell.level {
            cell.level = level;
            if let Ok((mut mesh, mut vis)) = liquids.get_mut(cell.liquid) {
                match fill.0.get(level).cloned().flatten() {
                    Some(h) => {
                        mesh.0 = h;
                        *vis = Visibility::Inherited;
                    }
                    None => *vis = Visibility::Hidden,
                }
            }
        }
    }
}

/// Faint feature lines on the glass car body and a studio floor grid.
pub fn draw_outlines(mut gizmos: Gizmos, view: Res<ViewState>, store: Res<crate::data::Store>) {
    gizmos.grid(
        Isometry3d::new(Vec3::Y * 0.0005, Quat::from_rotation_x(std::f32::consts::FRAC_PI_2)),
        UVec2::new(28, 28),
        Vec2::splat(0.5),
        Color::srgba(0.45, 0.65, 1.0, 0.045),
    );
    let Some(pack) = store.packs.get(&view.arch) else { return };
    if view.show_body {
        let v = &pack.vehicle;
        let shape = BodyShape { length: v.body_length_m, width: v.body_width_m, height: v.body_height_m };
        let faint = Color::srgba(0.75, 0.88, 1.0, 0.13);
        for &t in &[0.0f32, 0.55, 1.1, 1.57, 2.04, 2.59, 3.14159] {
            let pts: Vec<Vec3> = (0..=80).map(|i| shape.point(i as f32 / 80.0, t)).collect();
            gizmos.linestrip(pts, faint);
        }
        for &u in &[0.04f32, 0.3, 0.47, 0.58, 0.78, 0.96] {
            let pts: Vec<Vec3> = (0..=64).map(|j| shape.point(u, j as f32 / 64.0 * std::f32::consts::TAU)).collect();
            gizmos.linestrip(pts, faint);
        }
    }
}

pub fn toggle_body(view: Res<ViewState>, mut q: Query<&mut Visibility, With<CarBody>>) {
    if view.is_changed() {
        for mut v in &mut q {
            *v = if view.show_body { Visibility::Visible } else { Visibility::Hidden };
        }
    }
}
