"""A2r: download GHS-BUILT-C R2023A tiles covering Rwanda and clip them to the country bbox.

GHSL distributes 10 m products as 1000 km x 1000 km tiles in World Mollweide (ESRI:54009). We download only
the tiles that intersect Rwanda (two of them) and cut a Rwanda-sized window out of their mosaic.

We deliberately keep the native Mollweide grid instead of reprojecting: these are *categorical* rasters, and
nearest-neighbour resampling onto another 10 m grid would duplicate or drop pixels. Mollweide is equal-area,
so pixel counts (used for `ghsl_nres_share`) stay proportional to ground area. Points are projected to
Mollweide at sampling time instead (see rtl.join.raster).

Two products from the same release:
- MSZ: Morphological Settlement Zone classes (legend below) -> `ghsl_class`
- FUN: functional use of every built-up pixel (0 non-built, 1 RES, 2 NRES) -> `ghsl_nres_share`
"""

from __future__ import annotations

import math

import numpy as np
import rasterio
from osgeo import gdal
from pyproj import Transformer
from rasterio.windows import from_bounds

from rtl.manifest import fetch
from rtl.settings import INTERIM_DIR, aoi, sources

MOLLWEIDE = "ESRI:54009"
# GHSL tile grid (from the tiles' own geotransforms: R10_C21 starts at x=1,959,000, y=0).
TILE_X0, TILE_Y0, TILE_SIZE = -18_041_000.0, 9_000_000.0, 1_000_000.0
PIXEL_M = 10.0
CLIP_BUFFER_M = 1_000.0  # so the 50 m neighbourhood of a border building is never cut off

RASTER_DIR = INTERIM_DIR / "rasters"
CLIPPED = {p: RASTER_DIR / f"ghsl_built_c_{p.lower()}_rwa_54009_10m.tif" for p in ("MSZ", "FUN")}

# Official MSZ legend (GHSL dataset sheet ghs_buC2023, "Legend"), decoded to short labels.
# res/nres = residential / non-residential built space; the suffix is the building-height class.
MSZ_LEGEND = {
    # 0 is not in the legend but fills ~93% of Rwanda: the complement of the settlement zone. It is an
    # observation ("GHSL sees no settlement here"), so it gets a label rather than null.
    0: "outside_msz",
    1: "open_low_veg",  # open spaces, low vegetation, NDVI <= 0.3
    2: "open_medium_veg",  # 0.3 < NDVI <= 0.5
    3: "open_high_veg",  # NDVI > 0.5
    4: "open_water",  # LAND < 0.5
    5: "open_road",  # OSM highways
    11: "res_le3m",
    12: "res_3_6m",
    13: "res_6_15m",
    14: "res_15_30m",
    15: "res_gt30m",
    21: "nres_le3m",
    22: "nres_3_6m",
    23: "nres_6_15m",
    24: "nres_15_30m",
    25: "nres_gt30m",
}
MSZ_NODATA = 255  # outside any settlement zone (or no data): left null
FUN_RES, FUN_NRES, FUN_NODATA = 1, 2, 255


def bbox_mollweide(bbox_lonlat: list[float]) -> tuple[float, float, float, float]:
    """Envelope of a lon/lat bbox in Mollweide. Densify the edges: in Mollweide straight lon/lat edges curve."""
    x0, y0, x1, y1 = bbox_lonlat
    t = np.linspace(0, 1, 101)
    lon = np.r_[x0 + (x1 - x0) * t, np.full(101, x1), x0 + (x1 - x0) * t, np.full(101, x0)]
    lat = np.r_[np.full(101, y0), y0 + (y1 - y0) * t, np.full(101, y1), y0 + (y1 - y0) * t]
    x, y = Transformer.from_crs("EPSG:4326", MOLLWEIDE, always_xy=True).transform(lon, lat)
    return float(x.min()), float(y.min()), float(x.max()), float(y.max())


def tiles_for_bounds(xmin: float, ymin: float, xmax: float, ymax: float) -> list[str]:
    """GHSL tile ids ("R<row>_C<col>", 1-based, rows counted down from the top) intersecting Mollweide bounds."""
    cols = range(math.floor((xmin - TILE_X0) / TILE_SIZE) + 1, math.floor((xmax - TILE_X0) / TILE_SIZE) + 2)
    rows = range(math.floor((TILE_Y0 - ymax) / TILE_SIZE) + 1, math.floor((TILE_Y0 - ymin) / TILE_SIZE) + 2)
    return [f"R{r}_C{c}" for r in rows for c in cols]


def clip_bounds() -> tuple[float, float, float, float]:
    """Rwanda bbox in Mollweide plus a buffer, snapped outward to the 10 m pixel grid."""
    x0, y0, x1, y1 = bbox_mollweide(aoi()["country"]["bbox"])
    b = CLIP_BUFFER_M
    snap_lo = lambda v: math.floor(v / PIXEL_M) * PIXEL_M  # noqa: E731
    snap_hi = lambda v: math.ceil(v / PIXEL_M) * PIXEL_M  # noqa: E731
    return snap_lo(x0 - b), snap_lo(y0 - b), snap_hi(x1 + b), snap_hi(y1 + b)


def clip_product(product: str, zips: list) -> None:
    """Mosaic the product's tiles with a VRT (no copy) and write the Rwanda window as a tiled GeoTIFF."""
    tifs = [f"/vsizip/{z}/{z.stem}.tif" for z in zips]
    RASTER_DIR.mkdir(parents=True, exist_ok=True)
    vrt = str(RASTER_DIR / f"_ghsl_{product}.vrt")
    gdal.BuildVRT(vrt, tifs)
    with rasterio.open(vrt) as src:
        win = from_bounds(*clip_bounds(), transform=src.transform).round_offsets().round_lengths()
        arr = src.read(1, window=win, boundless=True, fill_value=255)
        profile = {
            "driver": "GTiff", "dtype": "uint8", "count": 1, "crs": src.crs, "nodata": 255,
            "width": arr.shape[1], "height": arr.shape[0], "transform": src.window_transform(win),
            "tiled": True, "blockxsize": 512, "blockysize": 512, "compress": "deflate",
        }
    with rasterio.open(CLIPPED[product], "w", **profile) as dst:
        dst.write(arr, 1)
    (RASTER_DIR / f"_ghsl_{product}.vrt").unlink()
    print(f"[ghsl] {product}: {arr.shape} -> {CLIPPED[product]}  values={np.unique(arr).tolist()}")


def main() -> None:
    tiles = tiles_for_bounds(*clip_bounds())
    template = sources()["ghsl_built_c"]["url"]
    for product in ("MSZ", "FUN"):
        zips = [fetch("ghsl_built_c", template.format(product=product, tile=t)) for t in tiles]
        clip_product(product, zips)


if __name__ == "__main__":
    main()
