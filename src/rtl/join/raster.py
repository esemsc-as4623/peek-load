"""A2r: sample raster context layers at every building centroid -> data/interim/context_raster.parquet.

Method: load each (Rwanda-clipped) raster into memory once, project all centroids into the raster's CRS, turn
coordinates into pixel indices with the inverse affine transform, and index the array with numpy. No per-point
Python loop, so 6.4M buildings take seconds per layer.

Columns (contract `context_raster` in rtl.schemas):
- ghsl_class:          decoded GHS-BUILT-C MSZ class of the pixel under the centroid
- ghsl_nres_share:     NRES / (RES + NRES) among GHS-BUILT-C FUN built pixels within 50 m of the centroid;
                       null when there is no built pixel in that disk (0/0 is "not observed", not zero)
- pop_density_per_km2: WorldPop R2025A 2023, people per km2 of the pixel under the centroid
- rwi:                 Meta Relative Wealth Index of the zoom-14 tile containing the centroid
- elevation_m:         A3's Copernicus DEM (data/interim/dem_rwa_30m.tif) under the centroid
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import rasterio
from affine import Affine
from pyproj import Proj, Transformer

from rtl.ingest import ghsl, rwi, worldpop
from rtl.locks import heavy_job
from rtl.manifest import read_manifest
from rtl.schemas import CONTEXT_RASTER_COLS
from rtl.settings import INTERIM_DIR

NRES_RADIUS_M = 50.0  # justified in reports/status/A2r.md
DEM_TIF = INTERIM_DIR / "dem_rwa_30m.tif"
OUT = INTERIM_DIR / "context_raster.parquet"


@dataclass
class Grid:
    """A raster held in memory: values, pixel->CRS affine transform, CRS, nodata."""

    arr: np.ndarray
    transform: Affine
    crs: str
    nodata: float | None

    @classmethod
    def load(cls, path: Path) -> Grid:
        with rasterio.open(path) as src:
            return cls(src.read(1), src.transform, src.crs.to_string(), src.nodata)


def rowcol(transform: Affine, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Pixel (row, col) containing each point. floor, not round: a pixel spans [i, i+1) in pixel space,
    and its centre is at i + 0.5. Rounding would shift every lookup by half a pixel."""
    col, row = ~transform @ (np.asarray(x, dtype="float64"), np.asarray(y, dtype="float64"))
    return np.floor(row).astype("int64"), np.floor(col).astype("int64")


def to_crs(lon: np.ndarray, lat: np.ndarray, crs: str) -> tuple[np.ndarray, np.ndarray]:
    if crs in ("EPSG:4326", "OGC:CRS84"):
        return np.asarray(lon, dtype="float64"), np.asarray(lat, dtype="float64")
    return Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform(lon, lat)


def sample(grid: Grid, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    """Value of the pixel under each lon/lat point as float64; NaN outside the raster or on nodata."""
    row, col = rowcol(grid.transform, *to_crs(lon, lat, grid.crs))
    h, w = grid.arr.shape
    inside = (row >= 0) & (row < h) & (col >= 0) & (col < w)
    out = np.full(len(row), np.nan)
    out[inside] = grid.arr[row[inside], col[inside]]
    if grid.nodata is not None and not np.isnan(grid.nodata):
        out[out == grid.nodata] = np.nan
    return out


def disk_offsets(radius_m: float, px_w_m: float, px_h_m: float) -> tuple[np.ndarray, np.ndarray]:
    """(drow, dcol) of pixels whose centre lies within `radius_m` on the ground of the centre pixel's centre.

    px_w_m / px_h_m are the *ground* width / height of one pixel. In Mollweide at Rwanda a 10 m pixel is about
    11.1 m wide and 9.0 m tall (equal-area, but stretched), so the disk is an ellipse in pixel space.
    """
    nr, nc = int(np.ceil(radius_m / px_h_m)), int(np.ceil(radius_m / px_w_m))
    dr, dc = np.mgrid[-nr : nr + 1, -nc : nc + 1]
    keep = (dr * px_h_m) ** 2 + (dc * px_w_m) ** 2 <= radius_m**2
    return dr[keep], dc[keep]


def ground_pixel_size(grid: Grid, lon: float, lat: float) -> tuple[float, float]:
    """Ground width and height (m) of one pixel at lon/lat, from the projection's local scale factors."""
    f = Proj(grid.crs).get_factors(lon, lat)
    return grid.transform.a / f.parallel_scale, -grid.transform.e / f.meridional_scale


def nres_share(fun: Grid, lon: np.ndarray, lat: np.ndarray, radius_m: float = NRES_RADIUS_M) -> np.ndarray:
    """Share of built pixels (FUN 1 = RES, 2 = NRES) within radius_m that are NRES; NaN if none are built."""
    row, col = rowcol(fun.transform, *to_crs(lon, lat, fun.crs))
    dr, dc = disk_offsets(radius_m, *ground_pixel_size(fun, float(np.mean(lon)), float(np.mean(lat))))
    h, w = fun.arr.shape
    inside = (row + dr.min() >= 0) & (row + dr.max() < h) & (col + dc.min() >= 0) & (col + dc.max() < w)
    r, c = row[inside], col[inside]
    built = np.zeros(len(r), dtype="int32")
    nres = np.zeros(len(r), dtype="int32")
    for a, b in zip(dr, dc, strict=True):  # ~80 offsets, each vectorised over all points
        v = fun.arr[r + a, c + b]
        built += (v == ghsl.FUN_RES) | (v == ghsl.FUN_NRES)
        nres += v == ghsl.FUN_NRES
    out = np.full(len(row), np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        out[inside] = np.where(built > 0, nres / built, np.nan)
    return out


def decode_msz(codes: np.ndarray) -> np.ndarray:
    """MSZ pixel codes (float, NaN = no data) -> label strings (None where null or undocumented)."""
    labels = np.full(256, None, dtype=object)
    for code, label in ghsl.MSZ_LEGEND.items():
        labels[code] = label
    out = np.full(len(codes), None, dtype=object)
    ok = ~np.isnan(codes)
    out[ok] = labels[codes[ok].astype("int64")]
    return out


def rwi_table() -> pd.DataFrame:
    path = max((e for e in read_manifest().values() if e["source_id"] == "meta_rwi"), key=lambda e: e["path"])
    return rwi.load_rwi(path["path"])


def build(b: pd.DataFrame, dem: Path | None = DEM_TIF) -> pd.DataFrame:
    """Context columns for a frame with bldg_id, lon, lat. Loads one raster at a time to bound memory."""
    lon, lat = b["lon"].to_numpy(), b["lat"].to_numpy()
    out = pd.DataFrame({"bldg_id": b["bldg_id"].to_numpy()})

    fun = Grid.load(ghsl.CLIPPED["FUN"])
    out["ghsl_nres_share"] = nres_share(fun, lon, lat)
    del fun
    msz = Grid.load(ghsl.CLIPPED["MSZ"])
    out["ghsl_class"] = decode_msz(sample(msz, lon, lat))
    del msz
    out["pop_density_per_km2"] = sample(Grid.load(worldpop.DENSITY_TIF), lon, lat)
    out["rwi"] = rwi.rwi_at(lon, lat, rwi_table())
    out["elevation_m"] = sample(Grid.load(dem), lon, lat) if dem is not None and dem.exists() else np.nan
    return out[CONTEXT_RASTER_COLS]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--buildings", type=Path, default=INTERIM_DIR / "buildings_base.parquet")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    t0 = time.time()
    with heavy_job("A2r-sample"):
        b = duckdb.sql(f"SELECT bldg_id, lon, lat FROM read_parquet('{args.buildings}')").df()
        out = build(b)
        # reconcile: exactly one row per input building, same ids
        assert len(out) == len(b) and out.bldg_id.is_unique and (out.bldg_id.to_numpy() == b.bldg_id.to_numpy()).all()
        out.to_parquet(args.out, index=False, compression="zstd")
    stats = {"rows": len(out), "seconds": round(time.time() - t0, 1),
             "null_share": out.drop(columns="bldg_id").isna().mean().round(4).to_dict()}
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
