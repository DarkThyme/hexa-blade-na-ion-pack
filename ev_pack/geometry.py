"""
Honeycomb layout of the hexagonal blades.

Coordinates: x = vehicle length (coolant flows +x), y = vehicle width (blade axis),
z = up. Hexagons are "pointy-top": flat sides face left/right, so each row nests
into the half-pitch-shifted row below it.
"""

from dataclasses import dataclass
from math import sqrt

import numpy as np

from .spec import PackSpec


@dataclass(frozen=True)
class Layout:
    row: np.ndarray        # (n,) layer index, 0 = bottom
    col: np.ndarray        # (n,) column index along x
    x: np.ndarray          # (n,) cell centre [m], pack centred on origin
    z: np.ndarray          # (n,) cell centre height above pack floor [m]
    neighbors: np.ndarray  # (m, 2) index pairs of touching cells
    cooled: np.ndarray     # (n,) bool, touches a cold plate (bottom or top layer)


def build_layout(pack: PackSpec) -> Layout:
    p = pack.pitch_m
    row_pitch = p * sqrt(3) / 2
    idx = lambda r, c: r * pack.columns + c  # noqa: E731

    rows, cols, xs, zs = [], [], [], []
    for r in range(pack.rows):
        for c in range(pack.columns):
            rows.append(r)
            cols.append(c)
            xs.append(c * p + (p / 2 if r % 2 else 0.0))
            zs.append(p / sqrt(3) + r * row_pitch)
    x = np.array(xs)
    x -= (x.min() + x.max()) / 2

    pairs = []
    for r in range(pack.rows):
        for c in range(pack.columns):
            if c + 1 < pack.columns:
                pairs.append((idx(r, c), idx(r, c + 1)))
            if r + 1 < pack.rows:
                # the row above is shifted +half pitch if this row is even
                above = (c - 1, c) if r % 2 == 0 else (c, c + 1)
                for ca in above:
                    if 0 <= ca < pack.columns:
                        pairs.append((idx(r, c), idx(r + 1, ca)))

    row = np.array(rows)
    return Layout(
        row=row,
        col=np.array(cols),
        x=x,
        z=np.array(zs),
        neighbors=np.array(pairs),
        cooled=(row == 0) | (row == pack.rows - 1),
    )
