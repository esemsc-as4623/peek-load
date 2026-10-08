"""A2r: Meta Relative Wealth Index for Rwanda (HDX CSV) and the Bing quadkey maths to join it to points.

LICENCE: CC-BY-NC-4.0 (non-commercial). Any output carrying `rwi` inherits the NC term.

The CSV has one row per Bing Maps tile at zoom 14 (~2.4 km). The Rwanda file carries only each tile's centre
(latitude, longitude), not the quadkey, so we derive every tile's key from its centre. To find the RWI of a
building we compute the tile containing its centroid the same way (Web Mercator tile maths, as documented in
"Bing Maps Tile System", Microsoft) and join on integer tile (x, y). Points in tiles absent from the CSV
(Meta only publishes populated tiles) get null.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from rtl.manifest import fetch

ZOOM = 14
MAX_LAT = 85.05112878  # Web Mercator is undefined at the poles; Bing clips latitudes to this


def tile_xy(lon: np.ndarray, lat: np.ndarray, zoom: int = ZOOM) -> tuple[np.ndarray, np.ndarray]:
    """Tile column/row (x to the east, y to the south, origin at the top-left of the world) for lon/lat."""
    lat = np.clip(np.asarray(lat, dtype="float64"), -MAX_LAT, MAX_LAT)
    lon = np.asarray(lon, dtype="float64")
    n = 2**zoom
    sin_lat = np.sin(np.radians(lat))
    fx = (lon + 180.0) / 360.0  # 0..1 across the map
    fy = 0.5 - np.log((1 + sin_lat) / (1 - sin_lat)) / (4 * np.pi)  # Mercator y, 0 at top, 1 at bottom
    x = np.clip(np.floor(fx * n), 0, n - 1).astype("int64")
    y = np.clip(np.floor(fy * n), 0, n - 1).astype("int64")
    return x, y


def tile_to_quadkey(x: int, y: int, zoom: int = ZOOM) -> str:
    """Interleave the bits of x and y, most significant level first: digit = xbit + 2*ybit."""
    digits = []
    for level in range(zoom, 0, -1):
        mask = 1 << (level - 1)
        digits.append(str((1 if x & mask else 0) + (2 if y & mask else 0)))
    return "".join(digits)


def quadkey_to_tile(quadkey: str) -> tuple[int, int, int]:
    """Inverse of tile_to_quadkey: (x, y, zoom)."""
    x = y = 0
    zoom = len(quadkey)
    for i, d in enumerate(quadkey):
        mask = 1 << (zoom - i - 1)
        d = int(d)
        if d & 1:
            x |= mask
        if d & 2:
            y |= mask
    return x, y, zoom


def tile_centre(x: np.ndarray, y: np.ndarray, zoom: int = ZOOM) -> tuple[np.ndarray, np.ndarray]:
    """Lon/lat of the centre of tile (x, y): the inverse Mercator of (x + 0.5, y + 0.5)."""
    n = 2**zoom
    lon = (np.asarray(x) + 0.5) / n * 360.0 - 180.0
    lat = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * (np.asarray(y) + 0.5) / n))))
    return lon, lat


def load_rwi(path) -> pd.DataFrame:
    """RWI CSV -> DataFrame with integer tile_x, tile_y, quadkey (zoom 14) and rwi.

    Checks that each published point really is a tile centre (so the tile assignment is unambiguous) and that
    no two rows fall in the same tile.
    """
    df = pd.read_csv(path)
    df["tile_x"], df["tile_y"] = tile_xy(df.longitude, df.latitude)
    clon, clat = tile_centre(df.tile_x, df.tile_y)
    tile_deg = 360.0 / 2**ZOOM
    assert (np.abs(clon - df.longitude) < 0.01 * tile_deg).all(), "CSV points are not tile centres"
    assert (np.abs(clat - df.latitude) < 0.01 * tile_deg).all(), "CSV points are not tile centres"
    assert not df.duplicated(["tile_x", "tile_y"]).any(), "two RWI rows in one tile"
    df["quadkey"] = [tile_to_quadkey(x, y) for x, y in zip(df.tile_x, df.tile_y, strict=True)]
    return df


def rwi_at(lon: np.ndarray, lat: np.ndarray, rwi: pd.DataFrame) -> np.ndarray:
    """RWI of the zoom-14 tile containing each point; NaN where Meta has no value."""
    x, y = tile_xy(lon, lat)
    key = pd.MultiIndex.from_arrays([x, y])
    lookup = rwi.set_index(["tile_x", "tile_y"])["rwi"]
    return lookup.reindex(key).to_numpy(dtype="float64")


def main() -> None:
    df = load_rwi(fetch("meta_rwi"))  # load_rwi asserts the tile-centre round trip on the real data
    print(f"[rwi] {len(df):,} zoom-14 tiles; rwi {df.rwi.min():.2f}..{df.rwi.max():.2f}; e.g. {df.quadkey[0]}")


if __name__ == "__main__":
    main()
