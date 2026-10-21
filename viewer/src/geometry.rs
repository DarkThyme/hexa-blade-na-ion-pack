//! Procedural meshes: rounded hexagonal cells with caps and terminals, the lofted glass car body, and the turbo colormap.

use bevy::asset::RenderAssetUsages;
use bevy::prelude::*;
use bevy::render::mesh::{Indices, PrimitiveTopology};

/// Contract coordinates (x forward, y lateral, z up) -> Bevy (y up).
pub fn to_bevy(p: [f32; 3]) -> Vec3 {
    Vec3::new(p[0], p[2], -p[1])
}

/// Cross-section ring: (position, outward normal) in the prism's (u, v) plane, counter-clockwise.
pub type Ring = Vec<(Vec2, Vec2)>;

/// Regular hexagon with rounded vertical edges. `phase` = angle of the first vertex.
pub fn hex_ring(across_flats: f32, shrink: f32, corner_frac: f32, phase: f32) -> Ring {
    let r = across_flats / 3f32.sqrt() * shrink;
    let rc = r * corner_frac;
    let dc = r - rc / 60f32.to_radians().sin();
    let m = 3;
    let mut ring = Vec::with_capacity(6 * (m + 1));
    for k in 0..6 {
        let th = phase + k as f32 * std::f32::consts::FRAC_PI_3;
        let c = Vec2::new(th.cos(), th.sin()) * dc;
        for j in 0..=m {
            let phi = th - 30f32.to_radians() + j as f32 * 60f32.to_radians() / m as f32;
            let n = Vec2::new(phi.cos(), phi.sin());
            ring.push((c + n * rc, n));
        }
    }
    ring
}

pub fn circle_ring(radius: f32, segments: usize) -> Ring {
    (0..segments)
        .map(|k| {
            let a = k as f32 / segments as f32 * std::f32::consts::TAU;
            let n = Vec2::new(a.cos(), a.sin());
            (n * radius, n)
        })
        .collect()
}

/// How a prism's local (u, v, w) frame maps into Bevy space (prism runs along w).
#[derive(Clone, Copy)]
pub enum Axis {
    /// contract y (across the car) -> Bevy Z; hexagon vertices point up/down
    Lateral,
    /// contract z (upright cell) -> Bevy Y
    Vertical,
}

impl Axis {
    pub fn from_contract(axis: &str) -> Self {
        if axis == "y" { Axis::Lateral } else { Axis::Vertical }
    }
    pub fn hex_phase(self) -> f32 {
        match self {
            Axis::Lateral => 90f32.to_radians(),
            Axis::Vertical => 0.0,
        }
    }
    fn map(self, u: f32, v: f32, w: f32) -> Vec3 {
        match self {
            Axis::Lateral => Vec3::new(u, v, w),
            Axis::Vertical => Vec3::new(u, w, -v), // proper rotation, keeps winding
        }
    }
}

/// Mesh builder that merges several primitives into one mesh (one draw per material).
#[derive(Default)]
pub struct MeshBuf {
    pos: Vec<[f32; 3]>,
    nrm: Vec<[f32; 3]>,
    idx: Vec<u32>,
}

impl MeshBuf {
    fn strip(&mut self, a: &[(Vec3, Vec3)], b: &[(Vec3, Vec3)]) {
        let base = self.pos.len() as u32;
        let n = a.len() as u32;
        for (p, q) in a.iter().chain(b) {
            self.pos.push(p.to_array());
            self.nrm.push(q.to_array());
        }
        for i in 0..n {
            let j = (i + 1) % n;
            self.idx.extend_from_slice(&[base + i, base + j, base + n + j, base + i, base + n + j, base + n + i]);
        }
    }

    fn cap(&mut self, ring: &[(Vec3, Vec3)], centre: Vec3, normal: Vec3, flip: bool) {
        let base = self.pos.len() as u32;
        self.pos.push(centre.to_array());
        self.nrm.push(normal.to_array());
        for (p, _) in ring {
            self.pos.push(p.to_array());
            self.nrm.push(normal.to_array());
        }
        let n = ring.len() as u32;
        for i in 0..n {
            let (a, b) = (base + 1 + i, base + 1 + (i + 1) % n);
            if flip { self.idx.extend_from_slice(&[base, b, a]) } else { self.idx.extend_from_slice(&[base, a, b]) }
        }
    }

    /// Prism of `ring` from w0 to w1 (w0 < w1), offset by (cu, cv), with optional chamfers at each end.
    #[allow(clippy::too_many_arguments)]
    pub fn prism(&mut self, ring: &Ring, axis: Axis, offset: Vec3, w0: f32, w1: f32, bevel0: f32, bevel1: f32) {
        let pt = |p: Vec2, w: f32| axis.map(p.x, p.y, w) + offset;
        let dir = |n: Vec2, nw: f32| axis.map(n.x, n.y, nw).normalize();
        let make = |inset: f32, w: f32, nw: f32| -> Vec<(Vec3, Vec3)> {
            ring.iter().map(|(p, n)| (pt(*p - *n * inset, w), dir(*n, nw))).collect()
        };
        let (s0, s1) = (w0 + bevel0, w1 - bevel1);
        self.strip(&make(0.0, s0, 0.0), &make(0.0, s1, 0.0));
        if bevel0 > 0.0 {
            self.strip(&make(bevel0, w0, -1.0), &make(0.0, s0, -1.0));
        }
        if bevel1 > 0.0 {
            self.strip(&make(0.0, s1, 1.0), &make(bevel1, w1, 1.0));
        }
        self.cap(&make(bevel0, w0, 0.0), pt(Vec2::ZERO, w0), dir(Vec2::ZERO, -1.0), true);
        self.cap(&make(bevel1, w1, 0.0), pt(Vec2::ZERO, w1), dir(Vec2::ZERO, 1.0), false);
    }

    pub fn build(self) -> Mesh {
        Mesh::new(PrimitiveTopology::TriangleList, RenderAssetUsages::default())
            .with_inserted_attribute(Mesh::ATTRIBUTE_POSITION, self.pos)
            .with_inserted_attribute(Mesh::ATTRIBUTE_NORMAL, self.nrm)
            .with_inserted_indices(Indices::U32(self.idx))
    }
}

/// Meshes for one cell, all centred on the cell: the coloured sleeve, the two aluminium end caps
/// and the terminal posts.
pub struct CellMeshes {
    pub sleeve: Mesh,
    pub caps: Mesh,
    pub terminals: Mesh,
    /// distance from the cell centre to the outer face of the terminal, along the cell axis
    pub terminal_reach: f32,
}

fn cap_length(length: f32) -> f32 {
    (length * 0.07).clamp(0.008, 0.03)
}

/// Keep the part of a convex polygon below the horizontal line v = level (Sutherland-Hodgman).
fn clip_below(poly: &[Vec2], level: f32) -> Vec<Vec2> {
    let mut out = Vec::new();
    for i in 0..poly.len() {
        let (a, b) = (poly[i], poly[(i + 1) % poly.len()]);
        let (ia, ib) = (a.y <= level, b.y <= level);
        if ia {
            out.push(a);
        }
        if ia != ib {
            out.push(a + (b - a) * ((level - a.y) / (b.y - a.y)));
        }
    }
    out
}

/// Sharp-cornered ring from a counter-clockwise polygon (each edge gets its own flat normal).
fn polygon_ring(poly: &[Vec2]) -> Ring {
    let mut ring = Vec::with_capacity(poly.len() * 2);
    for i in 0..poly.len() {
        let (a, b) = (poly[i], poly[(i + 1) % poly.len()]);
        let d = b - a;
        if d.length_squared() < 1e-12 {
            continue;
        }
        let n = Vec2::new(d.y, -d.x).normalize();
        ring.push((a, n));
        ring.push((b, n));
    }
    ring
}

/// Liquid inside a cell filled to fraction `f` (0..1) of its interior, with a flat surface that rises
/// with gravity: up the axis for an upright cell, across the hexagon for a blade lying on its side.
pub fn liquid_mesh(across_flats: f32, length: f32, axis: Axis, f: f32) -> Option<Mesh> {
    if f <= 0.004 {
        return None;
    }
    let f = f.min(1.0);
    let h = length / 2.0;
    let r = across_flats / 3f32.sqrt() * 0.9;
    let ph = axis.hex_phase();
    let hex: Vec<Vec2> = (0..6)
        .map(|k| {
            let a = ph + k as f32 * std::f32::consts::FRAC_PI_3;
            Vec2::new(a.cos(), a.sin()) * r
        })
        .collect();
    let w0 = -h + cap_length(length) + 0.002;
    let mut buf = MeshBuf::default();
    match axis {
        Axis::Lateral => {
            // hexagon vertices point up/down (Bevy y = v): the water line cuts across it
            let poly = clip_below(&hex, -r + 2.0 * r * f);
            if poly.len() < 3 {
                return None;
            }
            buf.prism(&polygon_ring(&poly), axis, Vec3::ZERO, w0, -w0, 0.0, 0.0);
        }
        Axis::Vertical => {
            let top = h - 0.004;
            buf.prism(&polygon_ring(&hex), axis, Vec3::ZERO, w0, w0 + (top - w0) * f, 0.0, 0.0);
        }
    }
    Some(buf.build())
}

pub fn cell_meshes(across_flats: f32, length: f32, axis: Axis) -> CellMeshes {
    let h = length / 2.0;
    let cap_len = cap_length(length);
    let ph = axis.hex_phase();
    let bevel = across_flats * 0.05;
    let cap_ring = hex_ring(across_flats, 0.935, 0.26, ph);
    let post = circle_ring(across_flats * 0.16, 16);
    let t_len = (length * 0.012).clamp(0.003, 0.012);
    let (mut sleeve, mut caps, mut terminals) = (MeshBuf::default(), MeshBuf::default(), MeshBuf::default());
    let sleeve_ring = hex_ring(across_flats, 0.955, 0.22, ph);
    caps.prism(&cap_ring, axis, Vec3::ZERO, -h, -h + cap_len + 0.001, bevel, 0.0);
    let top = match axis {
        // blade lying across the car: metal end caps and a terminal at both ends
        Axis::Lateral => {
            sleeve.prism(&sleeve_ring, axis, Vec3::ZERO, -h + cap_len, h - cap_len, 0.0, 0.0);
            caps.prism(&cap_ring, axis, Vec3::ZERO, h - cap_len - 0.001, h, 0.0, bevel);
            terminals.prism(&post, axis, Vec3::ZERO, -h - t_len, -h + 0.001, t_len * 0.3, 0.0);
            h
        }
        // upright cell seen from above: the coloured sleeve runs to the top, the top cap is a small
        // disc, so a ring of heat colour stays visible around it
        Axis::Vertical => {
            sleeve.prism(&sleeve_ring, axis, Vec3::ZERO, -h + cap_len, h, 0.0, across_flats * 0.06);
            let disc = 0.0035;
            caps.prism(&hex_ring(across_flats, 0.62, 0.3, ph), axis, Vec3::ZERO, h - 0.001, h + disc, 0.0, disc * 0.5);
            h + disc
        }
    };
    terminals.prism(&post, axis, Vec3::ZERO, top - 0.001, top + t_len, 0.0, t_len * 0.3);
    CellMeshes { sleeve: sleeve.build(), caps: caps.build(), terminals: terminals.build(), terminal_reach: top + t_len }
}

fn smooth(x: f32) -> f32 {
    let x = x.clamp(0.0, 1.0);
    x * x * (3.0 - 2.0 * x)
}

/// Piecewise-smooth interpolation through (u, value) control points.
fn profile(u: f32, pts: &[(f32, f32)]) -> f32 {
    if u <= pts[0].0 {
        return pts[0].1;
    }
    for w in pts.windows(2) {
        let ((u0, a), (u1, b)) = (w[0], w[1]);
        if u <= u1 {
            return a + (b - a) * smooth((u - u0) / (u1 - u0));
        }
    }
    pts[pts.len() - 1].1
}

pub struct BodyShape {
    pub length: f32,
    pub width: f32,
    pub height: f32,
}

impl BodyShape {
    /// (bottom, top, half-width) at normalised position u (0 = front bumper, 1 = tail).
    pub fn section(&self, u: f32) -> (f32, f32, f32) {
        let hs = self.height / 1.62;
        let top = profile(u, &[(0.0, 0.52), (0.06, 0.70), (0.30, 0.92), (0.47, 1.50), (0.58, 1.62),
                               (0.78, 1.58), (0.92, 1.18), (1.0, 0.98)]) * hs;
        let bottom = profile(u, &[(0.0, 0.36), (0.08, 0.20), (0.92, 0.20), (1.0, 0.40)]) * hs;
        let hw = profile(u, &[(0.0, 0.70), (0.10, 0.94), (0.85, 0.97), (1.0, 0.80)]) * self.width / 2.0;
        (bottom, top, hw)
    }

    /// Point on the superellipse cross-section at angle t (0 = right side, pi/2 = top).
    pub fn point(&self, u: f32, t: f32) -> Vec3 {
        let (bottom, top, hw) = self.section(u);
        let belt = bottom + (top - bottom) * 0.55;
        let (c, s) = (t.cos(), t.sin());
        let n = 3.2;
        let y_side = c.signum() * c.abs().powf(2.0 / n);
        let z_up = s.signum() * s.abs().powf(2.0 / n);
        let mid = (top + bottom) / 2.0;
        let z = mid + z_up * (top - bottom) / 2.0;
        // tumblehome: the glasshouse narrows above the belt line
        let taper = 1.0 - 0.30 * smooth((z - belt) / (top - belt + 1e-3));
        let x = -self.length / 2.0 + u * self.length;
        Vec3::new(x, z, -(y_side * hw * taper))
    }
}

/// Lofted car body mesh with smooth normals (front = -x).
pub fn car_body(shape: &BodyShape, stations: usize, segments: usize) -> Mesh {
    let mut pos = Vec::with_capacity((stations + 1) * segments);
    for i in 0..=stations {
        let u = i as f32 / stations as f32;
        for j in 0..segments {
            let t = j as f32 / segments as f32 * std::f32::consts::TAU;
            pos.push(shape.point(u, t));
        }
    }
    let mut idx: Vec<u32> = Vec::new();
    for i in 0..stations as u32 {
        for j in 0..segments as u32 {
            let a = i * segments as u32 + j;
            let b = i * segments as u32 + (j + 1) % segments as u32;
            let c = a + segments as u32;
            let d = b + segments as u32;
            idx.extend_from_slice(&[a, c, b, b, c, d]);
        }
    }
    // end caps
    for (ring, flip) in [(0u32, true), (stations as u32, false)] {
        let centre = pos[(ring as usize) * segments..(ring as usize + 1) * segments].iter().copied().sum::<Vec3>()
            / segments as f32;
        let ci = pos.len() as u32;
        pos.push(centre);
        for j in 0..segments as u32 {
            let a = ring * segments as u32 + j;
            let b = ring * segments as u32 + (j + 1) % segments as u32;
            if flip { idx.extend_from_slice(&[ci, a, b]) } else { idx.extend_from_slice(&[ci, b, a]) }
        }
    }
    // smooth normals
    let mut nrm = vec![Vec3::ZERO; pos.len()];
    for t in idx.chunks(3) {
        let (a, b, c) = (pos[t[0] as usize], pos[t[1] as usize], pos[t[2] as usize]);
        let n = (b - a).cross(c - a);
        for &k in t {
            nrm[k as usize] += n;
        }
    }
    let centre_line = |p: Vec3| Vec3::new(p.x, shape.height * 0.6, 0.0);
    let nrm: Vec<[f32; 3]> = nrm.iter().zip(&pos).map(|(n, p)| {
        let n = n.normalize_or_zero();
        // orient outward
        (if n.dot(*p - centre_line(*p)) < 0.0 { -n } else { n }).to_array()
    }).collect();
    let pos: Vec<[f32; 3]> = pos.iter().map(|p| p.to_array()).collect();
    Mesh::new(PrimitiveTopology::TriangleList, RenderAssetUsages::default())
        .with_inserted_attribute(Mesh::ATTRIBUTE_POSITION, pos)
        .with_inserted_attribute(Mesh::ATTRIBUTE_NORMAL, nrm)
        .with_inserted_indices(Indices::U32(idx))
}

/// Heat / SOC colour for t in [0, 1]: turbo without its near-black ends.
pub fn heat(t: f32) -> Color {
    turbo(0.07 + 0.88 * t.clamp(0.0, 1.0))
}

/// Google "turbo" colormap, polynomial approximation (Mikhailov 2019). t in [0, 1].
pub fn turbo(t: f32) -> Color {
    let t = t.clamp(0.0, 1.0);
    let r = 0.13572138 + t * (4.61539260 + t * (-42.66032258 + t * (132.13108234 + t * (-152.94239396 + t * 59.28637943))));
    let g = 0.09140261 + t * (2.19418839 + t * (4.84296658 + t * (-14.18503333 + t * (4.27729857 + t * 2.82956604))));
    let b = 0.10667330 + t * (12.64194608 + t * (-60.58204836 + t * (110.36276771 + t * (-89.90310912 + t * 27.34824973))));
    Color::srgb(r.clamp(0.0, 1.0), g.clamp(0.0, 1.0), b.clamp(0.0, 1.0))
}
