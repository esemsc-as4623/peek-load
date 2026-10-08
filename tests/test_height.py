"""A1 height transforms: each test fails if the transform it covers were wrong."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pyproj
import rasterio
import shapely
from rasterio.transform import from_origin

from rtl.conform.height import (
    est_floors,
    first_seen_year,
    footprint_median,
    gfa_m2,
    sample_points,
    tile_epsg,
    weighted_median,
)
from rtl.conform.height_report import parse_osm_number
from rtl.ingest.open_buildings_25d import NODATA, derive_tile

YEARS = list(range(2016, 2024))
nan = np.nan


def test_first_seen_year_handles_flicker_and_censoring():
    p = np.array([
        [0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9],  # present from the start -> 2016 ("2016 or earlier")
        [0.1, 0.1, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6],  # built 2018
        [0.1, 0.8, 0.1, 0.1, 0.7, 0.8, 0.9, 0.9],  # one-year flicker in 2017 ignored -> 2020
        [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],  # never detected -> null
        [0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.2],  # gone in 2023 -> null (not stable to the end)
        [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.6],  # appears only in 2023 -> 2023
        [nan, nan, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6],  # no data counts as absent -> 2018
        [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5],  # exactly at threshold counts as present
    ])
    got = first_seen_year(p, YEARS, threshold=0.5).tolist()
    assert got == [2016, 2018, 2020, pd.NA, pd.NA, 2023, 2018, 2016]


def test_first_seen_year_threshold_sensitivity():
    p = np.array([[0.2, 0.4, 0.6, 0.8, 0.8, 0.8, 0.8, 0.8]])
    assert [first_seen_year(p, YEARS, t).iloc[0] for t in (0.3, 0.5, 0.7)] == [2017, 2018, 2019]


def test_floors_and_gfa_arithmetic():
    h = pd.Series([0.5, 2.9, 4.4, 4.5, 7.5, 10.4, 200.0, nan])
    floors = est_floors(h)
    # 0.5/3 -> 0 -> clipped to 1; 4.5/3 = 1.5 -> 2; 7.5/3 = 2.5 -> 3 (half-up); 200 m capped at 30; NaN stays NA
    assert floors.tolist() == [1, 1, 1, 2, 3, 3, 30, pd.NA]
    gfa = gfa_m2(pd.Series([10.0] * 8), floors)
    assert gfa.iloc[:7].tolist() == [10.0, 10.0, 10.0, 20.0, 30.0, 30.0, 300.0]
    assert np.isnan(gfa.iloc[7])


def _write(path, arr, x0=500_000.0, y0=9_800_000.0, res=1.0, crs="EPSG:32735"):
    with rasterio.open(path, "w", driver="GTiff", width=arr.shape[1], height=arr.shape[0], count=1,
                       dtype="float32", crs=crs, transform=from_origin(x0, y0, res, res),
                       nodata=NODATA) as ds:
        ds.write(arr.astype("float32"), 1)
    return path


def test_footprint_median_on_planted_raster(tmp_path):
    # 20x20 m raster: a 10x10 m block of a 6 m building, with a 9 m column (the neighbour) on its east edge
    arr = np.zeros((20, 20))
    arr[5:15, 5:15] = 6.0
    arr[5:15, 14] = 9.0
    tif = _write(tmp_path / "h.tif", arr)
    x0, y0 = 500_000.0, 9_800_000.0
    exact = shapely.box(x0 + 5, y0 - 15, x0 + 15, y0 - 5)  # exactly the block: 90 px at 6 m, 10 px at 9 m
    shifted = shapely.box(x0 + 3, y0 - 15, x0 + 13, y0 - 5)  # misregistered 2 m west: 20 px of ground
    res = footprint_median(tif, pd.Series(["a", "b"]), np.array([exact, shifted])).set_index("bldg_id")
    assert res.loc["a", "fp_median"] == 6.0  # mean would be 6.3
    assert res.loc["b", "fp_median"] == 6.0  # mean would be 4.8: the median resists edge ground pixels
    assert res.loc["a", "fp_pixels"] == 100.0


def test_weighted_median_groups_and_weights():
    vals = [np.array([9.0, 6.0, 0.0]), np.array([]), np.array([1.0, 2.0]), np.array([5.0])]
    wts = [np.array([0.1, 0.6, 0.3]), np.array([]), np.array([0.5, 0.5]), np.array([0.2])]
    got = weighted_median(vals, wts)
    assert got[0] == 6.0 and np.isnan(got[1]) and got[2] == 1.0 and got[3] == 5.0


def test_sample_points_picks_containing_pixel_across_tiles_and_utm_zones(tmp_path):
    a = _write(tmp_path / "a.tif", np.arange(16).reshape(4, 4))  # x 500000..500004 (UTM 35S)
    b = _write(tmp_path / "b.tif", np.full((4, 4), 7.0), x0=500_004.0)  # adjacent tile to the east
    c = _write(tmp_path / "c.tif", np.full((4, 4), NODATA), x0=500_008.0)  # nodata tile
    d = _write(tmp_path / "d.tif", np.full((4, 4), 3.0), x0=200_000.0, crs="EPSG:32736")  # a tile in UTM 36S
    x = np.array([500_000.5, 500_003.9, 500_005.0, 500_009.0, 499_000.0])
    y = np.array([9_799_999.5, 9_799_996.1, 9_799_998.0, 9_799_998.0, 9_799_998.0])
    lon, lat = pyproj.Transformer.from_crs("EPSG:32735", "EPSG:4326", always_xy=True).transform(x, y)
    to_ll36 = pyproj.Transformer.from_crs("EPSG:32736", "EPSG:4326", always_xy=True)
    lon36, lat36 = to_ll36.transform(200_001.0, 9_799_998.0)
    got = sample_points([a, b, c, d], np.r_[lon, lon36], np.r_[lat, lat36])
    assert got[:3].tolist() == [0.0, 15.0, 7.0]
    assert np.isnan(got[3]) and np.isnan(got[4])
    assert got[5] == 3.0  # read in the tile's own zone, not forced into 35S
    assert tile_epsg([a, d], np.r_[lon[:1], lon36], np.r_[lat[:1], lat36]).tolist() == [32735, 32736]


def test_derive_tile_decimates_and_crops(tmp_path):
    # 0.5 m source with 4x4-pixel (2 m) constant blocks: nearest decimation to 2 m must recover each block value
    blocks = np.arange(64, dtype=float).reshape(8, 8)
    src = _write(tmp_path / "src.tif", np.kron(blocks, np.ones((4, 4))), res=0.5)
    out = tmp_path / "out" / "tile.tif"
    assert derive_tile(src, 1, 2.0, out)
    with rasterio.open(out) as ds:
        assert ds.res == (2.0, 2.0) and ds.shape == (8, 8)
        np.testing.assert_array_equal(ds.read(1), blocks)
    # a bbox far away from the tile writes nothing
    assert not derive_tile(src, 1, 2.0, tmp_path / "none.tif", bbox_lonlat=[10.0, 10.0, 10.1, 10.1])


def test_parse_osm_number():
    assert parse_osm_number("3") == 3.0
    assert parse_osm_number("12.5 m") == 12.5
    assert parse_osm_number("2;3") == 2.0
    assert parse_osm_number("4,5") == 4.5
    assert np.isnan(parse_osm_number("tall")) and np.isnan(parse_osm_number(None))
