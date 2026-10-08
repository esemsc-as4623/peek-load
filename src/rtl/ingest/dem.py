"""A3: Copernicus GLO-30 DEM tiles for Rwanda -> one clipped mosaic `data/interim/dem_rwa_30m.tif`.

The public AWS bucket stores 1x1 degree Cloud-Optimised GeoTIFFs named after their south-west corner
(`..._S02_00_E029_00_DEM` covers lat -2..-1, lon 29..30). We download the tiles touching the country bbox
(plus a small margin, so H3 cells and buildings on the border still get an elevation), record each in the
manifest, and mosaic them *without resampling*: the output grid is snapped to the tiles' own pixel grid,
so every output pixel is an exact copy of a source pixel.

Consumers: A2r samples building elevations from this file; climate/ uses H3-cell means for the
lapse-rate correction.
"""

from __future__ import annotations

import math
from pathlib import Path

import rasterio
from rasterio.enums import Resampling
from rasterio.merge import merge

from rtl.manifest import fetch
from rtl.settings import INTERIM_DIR, aoi, sources

SOURCE_ID = "copernicus_dem"
OUT = INTERIM_DIR / "dem_rwa_30m.tif"
# ~3.3 km: larger than an H3 res-7 cell edge (~1.4 km) so border cells are fully covered, but small enough
# that the clip stays inside the S02/S03 tile row (Rwanda's northern tip is at about -1.05).
MARGIN_DEG = 0.03
NODATA = -32767.0


def tile_name(lat_sw: int, lon_sw: int) -> str:
    """Bucket tile name for the tile whose south-west corner is (lat_sw, lon_sw), e.g. (-2, 29) -> S02/E029."""
    ns = f"{'N' if lat_sw >= 0 else 'S'}{abs(lat_sw):02d}"
    ew = f"{'E' if lon_sw >= 0 else 'W'}{abs(lon_sw):03d}"
    return f"Copernicus_DSM_COG_10_{ns}_00_{ew}_00_DEM"


def tiles_for_bbox(bbox: list[float]) -> list[str]:
    """All tile names intersecting bbox = [min_lon, min_lat, max_lon, max_lat].

    A tile with SW corner (lat, lon) spans [lat, lat+1) x [lon, lon+1), so we need floor(min)..ceil(max)-1.
    """
    x0, y0, x1, y1 = bbox
    return [tile_name(lat, lon)
            for lat in range(math.floor(y0), math.ceil(y1))
            for lon in range(math.floor(x0), math.ceil(x1))]


def snap_bounds(bounds: tuple[float, float, float, float], origin_x: float, origin_y: float,
                res: float) -> tuple[float, float, float, float]:
    """Expand bounds outward onto the source pixel grid (origin + k*res), so merging copies pixels 1:1."""
    left, bottom, right, top = bounds
    return (origin_x + math.floor((left - origin_x) / res) * res,
            origin_y + math.floor((bottom - origin_y) / res) * res,
            origin_x + math.ceil((right - origin_x) / res) * res,
            origin_y + math.ceil((top - origin_y) / res) * res)


def download(bbox: list[float]) -> list[Path]:
    url_tpl = sources()[SOURCE_ID]["url"]
    paths = []
    for name in tiles_for_bbox(bbox):
        paths.append(fetch(SOURCE_ID, url=url_tpl.format(tile=name), filename=f"{name}.tif"))
    return paths


def mosaic(tiles: list[Path], bbox: list[float], out: Path = OUT) -> dict:
    with rasterio.open(tiles[0]) as first:
        res = first.res[0]
        origin_x, origin_y = first.transform.c, first.transform.f
    bounds = snap_bounds(tuple(bbox), origin_x, origin_y, res)
    data, transform = merge([str(p) for p in tiles], bounds=bounds, res=res, nodata=NODATA,
                            resampling=Resampling.nearest)
    profile = dict(driver="GTiff", dtype="float32", count=1, crs="EPSG:4326", transform=transform,
                   width=data.shape[2], height=data.shape[1], nodata=NODATA, tiled=True, blockxsize=512,
                   blockysize=512, compress="deflate", predictor=3, bigtiff="IF_SAFER")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part.tif")
    with rasterio.open(tmp, "w", **profile) as dst:
        dst.write(data)
        dst.update_tags(source="Copernicus DEM GLO-30 Public (DSM, EGM2008 heights)",
                        tiles=",".join(p.stem for p in tiles))
        dst.build_overviews([2, 4, 8, 16, 32], Resampling.average)  # quick-look plots read these
    tmp.rename(out)
    valid = data[data != NODATA]
    return {"path": str(out), "shape": data.shape[1:], "bounds": bounds, "res_deg": res,
            "min_m": float(valid.min()), "max_m": float(valid.max()), "nodata_share": 1 - valid.size / data[0].size}


def main() -> None:
    x0, y0, x1, y1 = aoi()["country"]["bbox"]
    bbox = [x0 - MARGIN_DEG, y0 - MARGIN_DEG, x1 + MARGIN_DEG, y1 + MARGIN_DEG]
    stats = mosaic(download(bbox), bbox)
    print(stats)


if __name__ == "__main__":
    main()
