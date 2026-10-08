"""Grid -> point: bilinear interpolation on a regular lat/lon grid plus an elevation (lapse-rate) correction.

Rwanda's temperature is driven mainly by elevation (900-4,500 m), and a 9 km ERA5-Land cell averages
over hills and valleys. So we (1) interpolate both temperature and the grid's own model elevation to the
point, then (2) shift temperature by gamma * (point elevation - interpolated grid elevation), with gamma
estimated from the grid itself (negative: higher = colder).
"""

from __future__ import annotations

import numpy as np


def bilinear_weights(lat: np.ndarray, lon: np.ndarray, grid_lat: np.ndarray,
                     grid_lon: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Indices into the flattened (lat, lon) grid and weights, each shaped (n_points, 4).

    grid_lat / grid_lon must be ascending. Points outside the grid are clamped to its edge (nearest-edge
    values, no extrapolation), which only matters for a few border cells.
    """
    fy = np.interp(lat, grid_lat, np.arange(len(grid_lat)))  # fractional row index, clamped at the edges
    fx = np.interp(lon, grid_lon, np.arange(len(grid_lon)))
    y0 = np.minimum(np.floor(fy).astype(int), len(grid_lat) - 2)
    x0 = np.minimum(np.floor(fx).astype(int), len(grid_lon) - 2)
    wy, wx = fy - y0, fx - x0
    nx = len(grid_lon)
    idx = np.stack([y0 * nx + x0, y0 * nx + x0 + 1, (y0 + 1) * nx + x0, (y0 + 1) * nx + x0 + 1], axis=1)
    w = np.stack([(1 - wy) * (1 - wx), (1 - wy) * wx, wy * (1 - wx), wy * wx], axis=1)
    return idx, w


def interpolate(field: np.ndarray, idx: np.ndarray, w: np.ndarray) -> np.ndarray:
    """field: (n_nodes, ...) flattened grid values -> (n_points, ...) interpolated values."""
    extra = (1,) * (field.ndim - 1)
    return (field[idx] * w.reshape(*w.shape, *extra)).sum(axis=1)


def fit_lapse_rate(elevation_m: np.ndarray, t_c: np.ndarray) -> tuple[float, float, float]:
    """OLS of temperature on elevation: returns (slope K/m, intercept degC, r2)."""
    slope, intercept = np.polyfit(elevation_m, t_c, 1)
    resid = t_c - (slope * elevation_m + intercept)
    r2 = 1 - resid.var() / t_c.var()
    return float(slope), float(intercept), float(r2)


def lapse_shift(gamma_k_per_m: float, target_elev_m: np.ndarray, grid_elev_m: np.ndarray) -> np.ndarray:
    """Additive temperature correction: positive when the target is *lower* than the grid (gamma < 0)."""
    return gamma_k_per_m * (target_elev_m - grid_elev_m)
