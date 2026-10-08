"""A2r: download WorldPop Global2 (R2025A) constrained population for Rwanda and convert it to density.

The file holds *people per pixel* on a 3 arc-second lon/lat grid (EPSG:4326). That grid is called "100 m",
but at Rwanda's latitude a cell is ~92.6 m x 92.1 m = ~0.0085 km2, not 0.01 km2: dividing by 0.01 would
understate density by ~15%. So we divide each row by its true (ellipsoidal) cell area instead.
"""

from __future__ import annotations

import numpy as np
import rasterio
from pyproj import Geod

from rtl.manifest import fetch
from rtl.settings import INTERIM_DIR

DENSITY_TIF = INTERIM_DIR / "rasters" / "worldpop_rwa_2023_per_km2.tif"
_GEOD = Geod(ellps="WGS84")


def cell_area_km2(lat_top: np.ndarray, dlon: float, dlat: float) -> np.ndarray:
    """Geodesic area (km2) of a lon/lat cell `dlon` wide whose top edge is at `lat_top` and `dlat` tall."""
    out = np.empty(len(lat_top))
    for i, top in enumerate(lat_top):  # one call per raster row (~7k rows): cheap
        lons = [0.0, dlon, dlon, 0.0]
        lats = [top, top, top - dlat, top - dlat]
        out[i] = abs(_GEOD.polygon_area_perimeter(lons, lats)[0]) / 1e6
    return out


def to_density(src_path, dst_path=DENSITY_TIF) -> None:
    """People per pixel -> people per km2, row by row. Nodata (outside the settlement mask) stays nodata."""
    with rasterio.open(src_path) as src:
        assert src.crs.to_epsg() == 4326, src.crs
        pop = src.read(1, masked=True).astype("float64")
        t = src.transform
        lat_top = t.f + t.e * np.arange(src.height)  # t.e < 0 (north-up)
        area = cell_area_km2(lat_top, t.a, -t.e)
        dens = (pop / area[:, None]).astype("float32").filled(np.nan)
        profile = src.profile | {"dtype": "float32", "nodata": np.nan, "compress": "deflate", "tiled": True,
                                 "blockxsize": 256, "blockysize": 256}
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(dst_path, "w", **profile) as dst:
        dst.write(dens, 1)
    print(f"[worldpop] total pop {pop.sum():,.0f}; cell area {area.min():.5f}-{area.max():.5f} km2 -> {dst_path}")


def main() -> None:
    to_density(fetch("worldpop"))


if __name__ == "__main__":
    main()
