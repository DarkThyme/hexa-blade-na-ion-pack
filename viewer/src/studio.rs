//! Procedural studio environment map (no asset files): a dark room with soft-box lights, baked into
//! a mip-mapped HDR cubemap so metal and clear-coated surfaces get believable reflections.
//! Rougher mips are the same analytic studio with wider light falloff (cheap stand-in for GGX filtering).

use bevy::asset::RenderAssetUsages;
use bevy::image::{ImageFilterMode, ImageSampler, ImageSamplerDescriptor};
use bevy::prelude::*;
use bevy::render::render_resource::{Extent3d, TextureDimension, TextureFormat, TextureViewDescriptor, TextureViewDimension};

/// Soft boxes: (direction, angular radius [rad], linear radiance)
fn lights() -> [(Vec3, f32, Vec3); 5] {
    [
        (Vec3::new(0.0, 1.0, 0.0), 0.55, Vec3::new(2.4, 2.45, 2.6)),            // overhead box
        (Vec3::new(-0.65, 0.55, 0.55).normalize(), 0.28, Vec3::new(4.0, 3.8, 3.5)), // warm key
        (Vec3::new(0.7, 0.35, -0.65).normalize(), 0.22, Vec3::new(1.4, 2.0, 3.2)),  // cool rim
        (Vec3::new(-0.9, 0.12, -0.45).normalize(), 0.16, Vec3::new(1.6, 1.6, 1.7)), // low strip
        (Vec3::new(0.85, 0.2, 0.5).normalize(), 0.14, Vec3::new(1.2, 1.25, 1.3)),   // fill
    ]
}

fn smoothstep(e0: f32, e1: f32, x: f32) -> f32 {
    let t = ((x - e0) / (e1 - e0)).clamp(0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

/// Radiance seen in world direction `d`, blurred by roughly `sigma` radians.
fn radiance(d: Vec3, sigma: f32) -> Vec3 {
    let horizon = Vec3::new(0.030, 0.034, 0.045);
    let top = Vec3::new(0.085, 0.10, 0.135);
    let ground = Vec3::new(0.010, 0.011, 0.013);
    let soft = 0.06 + sigma * 0.6;
    let sky = horizon.lerp(top, smoothstep(0.0, 1.0, d.y));
    let mut c = ground.lerp(sky, smoothstep(-soft, soft, d.y));
    for (dir, r, col) in lights() {
        let a = d.dot(dir).clamp(-1.0, 1.0).acos();
        let e = 0.015 + sigma;
        let w = 1.0 - smoothstep(r - e, r + e, a);
        c += col * w * (r * r / (r * r + sigma * sigma));
    }
    c
}

fn f16(x: f32) -> u16 {
    let b = x.max(0.0).to_bits();
    let exp = ((b >> 23) & 0xff) as i32 - 127 + 15;
    if exp <= 0 {
        return 0;
    }
    if exp >= 31 {
        return 0x7bff;
    }
    ((exp as u32) << 10 | ((b & 0x7f_ffff) >> 13)) as u16
}

/// Texel direction on a cube face (wgpu face order +X -X +Y -Y +Z -Z), converted to world space
/// (Bevy samples cubemaps with z negated).
fn face_dir(face: usize, sc: f32, tc: f32) -> Vec3 {
    let d = match face {
        0 => Vec3::new(1.0, -tc, -sc),
        1 => Vec3::new(-1.0, -tc, sc),
        2 => Vec3::new(sc, 1.0, tc),
        3 => Vec3::new(sc, -1.0, -tc),
        4 => Vec3::new(sc, -tc, 1.0),
        _ => Vec3::new(-sc, -tc, -1.0),
    }
    .normalize();
    Vec3::new(d.x, d.y, -d.z)
}

fn cubemap(size: u32, sigmas: &[f32]) -> Image {
    let levels = sigmas.len() as u32;
    let mut data: Vec<u8> = Vec::new();
    for face in 0..6 {
        for (mip, &sigma) in sigmas.iter().enumerate() {
            let n = (size >> mip).max(1);
            for t in 0..n {
                for s in 0..n {
                    let sc = 2.0 * (s as f32 + 0.5) / n as f32 - 1.0;
                    let tc = 2.0 * (t as f32 + 0.5) / n as f32 - 1.0;
                    let c = radiance(face_dir(face, sc, tc), sigma);
                    for v in [c.x, c.y, c.z, 1.0] {
                        data.extend_from_slice(&f16(v).to_le_bytes());
                    }
                }
            }
        }
    }
    let ext = Extent3d { width: size, height: size, depth_or_array_layers: 6 };
    let mip0 = (size * size * 6 * 8) as usize;
    let mut img = Image::new(ext, TextureDimension::D2, vec![0; mip0], TextureFormat::Rgba16Float,
                             RenderAssetUsages::RENDER_WORLD);
    img.data = Some(data);
    img.texture_descriptor.mip_level_count = levels;
    img.texture_view_descriptor = Some(TextureViewDescriptor { dimension: Some(TextureViewDimension::Cube), ..default() });
    img.sampler = ImageSampler::Descriptor(ImageSamplerDescriptor {
        mag_filter: ImageFilterMode::Linear,
        min_filter: ImageFilterMode::Linear,
        mipmap_filter: ImageFilterMode::Linear,
        ..default()
    });
    img
}

/// Environment light for the camera: specular cubemap with a full mip chain plus a blurry diffuse map.
pub fn environment(images: &mut Assets<Image>) -> EnvironmentMapLight {
    let size = 128u32;
    let levels = size.trailing_zeros() + 1; // 128 -> 1 px
    // Bevy maps perceptual roughness linearly onto the mip chain; GGX lobe width ~ roughness^2
    let sigmas: Vec<f32> = (0..levels).map(|k| {
        let r = k as f32 / (levels - 1) as f32;
        1.2 * r * r
    }).collect();
    let specular = images.add(cubemap(size, &sigmas));
    let diffuse = images.add(cubemap(16, &[1.4]));
    EnvironmentMapLight { diffuse_map: diffuse, specular_map: specular, intensity: 700.0, ..default() }
}
