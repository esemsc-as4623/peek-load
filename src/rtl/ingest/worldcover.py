"""A2r: ESA WorldCover 2021 cropland share around every place in Rwanda.

    pixi run worldcover

Two 3 x 3 degree tiles (10 m, EPSG:4326) cover the country. Each is cut to the country bbox and reduced to a
~100 m grid holding the cropland fraction (class 40) of its 10 x 10 pixels. That grid is then averaged per H3
resolution-8 cell (~0.7 km2) and smoothed over the cell and its 6 neighbours (~1.5 km across), which is the
"agricultural context" a mill or a pump would serve.
Outputs: data/interim/rasters/worldcover_cropland_100m.tif, data/interim/cropland_h3r8.parquet (h3_r8, cropland_share).
"""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd
import rasterio
from rasterio.windows import from_bounds

from rtl.manifest import fetch
from rtl.settings import INTERIM_DIR, aoi, sources

TILES = ["S03E027", "S03E030"]  # SW corners; Rwanda is 28.86-30.90 E, 2.84-1.05 S
CROPLAND = 40
BLOCK = 10  # 10 x 10 pixels of 10 m -> ~100 m
TIF = INTERIM_DIR / "rasters" / "worldcover_cropland_100m.tif"
OUT = INTERIM_DIR / "cropland_h3r8.parquet"


def block_fraction(cls: np.ndarray, value: int = CROPLAND, block: int = BLOCK) -> np.ndarray:
    """Share of pixels equal to `value` in each block x block window (edges trimmed to whole blocks)."""
    h, w = (cls.shape[0] // block) * block, (cls.shape[1] // block) * block
    hit = (cls[:h, :w] == value).reshape(h // block, block, w // block, block)
    return hit.mean(axis=(1, 3), dtype="float32")


def cropland_grid() -> tuple[np.ndarray, rasterio.Affine]:
    x0, y0, x1, y1 = aoi()["country"]["bbox"]
    url = sources()["esa_worldcover"]["url"]
    with rasterio.open(fetch("esa_worldcover", url.format(tile=TILES[0]))) as src:
        res = src.res[0]
    # one 100 m grid over the bbox, snapped to the 10 m pixel grid so the two tiles line up
    gx0, gy1 = np.floor(x0 / res) * res, np.ceil(y1 / res) * res
    step = res * BLOCK
    nx, ny = int(np.ceil((x1 - gx0) / step)), int(np.ceil((gy1 - y0) / step))
    grid = np.full((ny, nx), np.nan, dtype="float32")
    for tile in TILES:
        with rasterio.open(fetch("esa_worldcover", url.format(tile=tile))) as src:
            tb = src.bounds
            bx0, bx1 = max(gx0, tb.left), min(gx0 + nx * step, tb.right)
            by0, by1 = max(gy1 - ny * step, tb.bottom), min(gy1, tb.top)
            if bx0 >= bx1 or by0 >= by1:
                continue
            win = from_bounds(bx0, by0, bx1, by1, src.transform).round_offsets().round_lengths()
            frac = block_fraction(src.read(1, window=win))
            c0, r0 = round((bx0 - gx0) / step), round((gy1 - by1) / step)
            grid[r0:r0 + frac.shape[0], c0:c0 + frac.shape[1]] = frac[:ny - r0, :nx - c0]
    return grid, rasterio.Affine(step, 0, gx0, 0, -step, gy1)


def main() -> None:
    grid, transform = cropland_grid()
    TIF.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(TIF, "w", driver="GTiff", width=grid.shape[1], height=grid.shape[0], count=1,
                       dtype="float32", crs="EPSG:4326", transform=transform, nodata=np.nan,
                       compress="deflate") as dst:
        dst.write(grid, 1)
    rows, cols = np.nonzero(~np.isnan(grid))
    lon, lat = rasterio.transform.xy(transform, rows, cols)
    pts = pd.DataFrame({"lon": lon, "lat": lat, "v": grid[rows, cols]})
    con = duckdb.connect()
    con.sql("INSTALL h3 FROM community; LOAD h3")
    con.register("pts", pts)
    out = con.sql("""
        WITH c AS (SELECT h3_latlng_to_cell_string(lat, lon, 8) AS h, avg(v) AS v FROM pts GROUP BY 1),
             k AS (SELECT c.h, unnest(h3_grid_disk(c.h, 1)) AS n FROM c)
        SELECT k.h AS h3_r8, avg(c.v)::FLOAT AS cropland_share FROM k JOIN c ON c.h = k.n GROUP BY 1""").df()
    out.to_parquet(OUT, index=False)
    print(f"[worldcover] 100 m grid {grid.shape}, cropland {np.nanmean(grid):.1%} of the bbox; "
          f"{len(out):,} H3 r8 cells (median share {out.cropland_share.median():.2f}) -> {OUT}")


if __name__ == "__main__":
    main()
