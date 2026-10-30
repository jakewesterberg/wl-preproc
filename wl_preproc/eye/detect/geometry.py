"""True visual angle between gaze positions given in per-axis degrees.

**Gaze here is in per-axis degrees**: a point `(X, Y)` cm from straight ahead
at distance `D` is `(atan(X/D), atan(Y/D))`, the convention wl-xcon draws in
(`viewport.center`) and our calibration fits to. Distances taken in those
coordinates shrink off centre -- a 2-degree step up from (20, 0) is 1.879
degrees of visual angle -- so a size, speed or direction that means what it
says is measured between DIRECTIONS (design spec
`2026-10-30-true-angle-saccades-design.md` section 2).

These are wl-xcon's definitions to the formula (its `geometry.separation_deg`,
`polar_deg` and `viewport.local_true_angle`, at its `main` `642ce31`), so its
online detector and eye records match ours to the sample. Nothing else in this
repository computes them.
"""

from __future__ import annotations

import numpy as np

#: The two settings of the shared `eye_detection` paramset key `geometry`
#: (design spec section 3).
TRUE_ANGLE = "true-angle"
PER_AXIS = "per-axis"
GEOMETRIES = (TRUE_ANGLE, PER_AXIS)
#: What a default paramset registers.
DEFAULT_GEOMETRY = TRUE_ANGLE
#: What a paramset WITHOUT the key means: every one written before the key
#: existed was detected per axis, so a detector's own params default to it.
UNSTATED_GEOMETRY = PER_AXIS


def directions(xy_deg: np.ndarray) -> np.ndarray:
    """Unit directions, `(..., 2)` per-axis degrees to `(..., 3)`.

    `normalize(tan x, tan y, 1)`, computed as wl-xcon's `separation_deg`
    computes it, `(sin x cos y, cos x sin y, cos x cos y)`, normalized: the
    same direction for every position a screen shows, without `tan`'s blow-up
    near 90 degrees. The third component is positive for every such position,
    so no direction is ever along the x axis, where the local frame below
    would have no rightward axis. NaN in, NaN out."""
    rad = np.radians(np.asarray(xy_deg, dtype=float))
    x, y = rad[..., 0], rad[..., 1]
    d = np.stack([np.sin(x) * np.cos(y), np.cos(x) * np.sin(y), np.cos(x) * np.cos(y)], axis=-1)
    return d / np.linalg.norm(d, axis=-1, keepdims=True)


def true_angle_deg(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """The angle between directions, `atan2(|u x v|, u . v)` in degrees:
    exact at small angles, where `acos` of a dot product loses its digits."""
    return np.degrees(np.arctan2(np.linalg.norm(np.cross(u, v), axis=-1), np.sum(u * v, axis=-1)))


def wrap_360_deg(angle_deg) -> np.ndarray:
    """An angle in `[0, 360)` (amendment 1, "0 to 360 in both repos"): mod
    360, and 0 where that rounds to 360, as a tiny negative angle does
    (-1e-15 mod 360 is 360.0 in float64). Adding 0.0 turns a -0.0 into 0.0,
    which a JSON file would otherwise write with its sign. NaN stays NaN."""
    wrapped = np.mod(np.asarray(angle_deg, dtype=float), 360.0)
    return np.where(wrapped >= 360.0, 0.0, wrapped) + 0.0


def eccentricity_polar_deg(xy_deg: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Each position's eccentricity, `atan(sqrt(tan^2 x + tan^2 y))`, and
    polar angle, `atan2(tan y, tan x)` counter-clockwise from rightward in
    `[0, 360)`. **The polar angle is NaN at zero eccentricity** ("Empty at
    center"): `atan2(0, 0)` would say rightward. Computed from the direction,
    whose first two components have the signs and ratio of `tan x` and
    `tan y`."""
    d = directions(xy_deg)
    across, up, ahead = d[..., 0], d[..., 1], d[..., 2]
    eccentricity = np.degrees(np.arctan2(np.hypot(across, up), ahead))
    polar = wrap_360_deg(np.degrees(np.arctan2(up, across)))
    return eccentricity, np.where(eccentricity == 0.0, np.nan, polar)


def local_coordinates_deg(v: np.ndarray, c: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Directions `v`'s azimuthal-equidistant coordinates about `c`, degrees
    along `c`'s own rightward and upward axes (wl-xcon's
    `viewport.local_true_angle`, the same formula, broadcast so that each `v`
    may have its own `c`): `ex = normalize(x - (x.c)c)`, `ey = c x ex`, and
    `v` at `angle(c, v)` from the origin in the direction of its tangent
    `t = v - (v.c)c`. A distance from `c` is exact and a direction about it
    true; at the centre `ex` is rightward and `ey` upward. A tangent of zero
    length is the origin. `v` and `c` are `(..., 3)`, broadcast together."""
    c = np.asarray(c, dtype=float)
    ex = np.array([1.0, 0.0, 0.0]) - c[..., 0:1] * c
    ex = ex / np.linalg.norm(ex, axis=-1, keepdims=True)
    ey = np.cross(c, ex)
    cos_t = np.clip(np.sum(v * c, axis=-1), -1.0, 1.0)
    t = v - cos_t[..., None] * c
    tn = np.linalg.norm(t, axis=-1)
    theta = np.degrees(np.arctan2(tn, cos_t))
    safe = np.where(tn > 0, tn, 1.0)
    return theta * np.sum(t * ex, axis=-1) / safe, theta * np.sum(t * ey, axis=-1) / safe
