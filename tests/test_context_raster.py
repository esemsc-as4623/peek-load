"""A2r: tests that would fail if a raster transform, the quadkey maths or a unit conversion were wrong."""

import math

import numpy as np
import pandas as pd
import pytest
from affine import Affine

from rtl.ingest import ghsl, rwi, worldpop
from rtl.join.raster import Grid, decode_msz, disk_offsets, nres_share, rowcol, sample
from rtl.schemas import CONTEXT_RASTER_COLS, context_raster
from rtl.settings import FIXTURES_DIR

A_WGS84 = 6378137.0


def _grid_4326(value_at=(3, 7), value=42.0):
    """10 x 10 grid of 0.001 deg pixels with top-left corner at (30.0, -1.0) and one planted value."""
    arr = np.zeros((10, 10), dtype="float32")
    arr[value_at] = value
    return Grid(arr, Affine(0.001, 0, 30.0, 0, -0.001, -1.0), "EPSG:4326", -9999.0)


# --------------------------------------------------------------------------- pixel lookup
def test_planted_pixel_centre_returns_planted_value():
    g = _grid_4326()
    lon, lat = 30.0 + 0.001 * 7.5, -1.0 - 0.001 * 3.5  # centre of row 3, col 7
    assert sample(g, np.array([lon]), np.array([lat]))[0] == 42.0


def test_half_pixel_offset_is_not_applied():
    # Points just inside the planted pixel's top-left corner hit it; just outside hit the neighbours.
    # A round()-instead-of-floor() bug (or a +0.5 shift) breaks one of these.
    g = _grid_4326()
    x0, y0 = 30.0 + 0.001 * 7, -1.0 - 0.001 * 3
    eps = 1e-7
    assert sample(g, np.array([x0 + eps]), np.array([y0 - eps]))[0] == 42.0
    assert sample(g, np.array([x0 - eps]), np.array([y0 - eps]))[0] == 0.0  # west neighbour
    assert sample(g, np.array([x0 + eps]), np.array([y0 + eps]))[0] == 0.0  # north neighbour
    r, c = rowcol(g.transform, np.array([x0 + 0.00099]), np.array([y0 - 0.00099]))
    assert (r[0], c[0]) == (3, 7)  # far corner of the pixel still in the pixel


def test_outside_raster_and_nodata_are_nan():
    g = _grid_4326()
    g.arr[0, 0] = -9999.0
    out = sample(g, np.array([29.0, 30.0005]), np.array([-1.0005, -1.0005]))
    assert np.isnan(out).all()


def test_mollweide_projection_of_known_point():
    # On the equator Mollweide reduces to x = (2*sqrt(2)/pi) * a * lon_rad, y = 0 (PROJ uses a as radius).
    lon = 29.5
    x_expected = 2 * math.sqrt(2) / math.pi * A_WGS84 * math.radians(lon)
    arr = np.zeros((4, 4), dtype="uint8")
    arr[1, 2] = 7
    # 10 m grid whose pixel (1, 2) contains (x_expected, -5)
    left, top = math.floor(x_expected / 10) * 10 - 20, 10.0
    g = Grid(arr, Affine(10, 0, left, 0, -10, top), ghsl.MOLLWEIDE, 255)
    assert sample(g, np.array([lon]), np.array([-5 / (math.sqrt(2) * A_WGS84) * 4 / math.pi * 180 / math.pi]))[0] == 7


def test_ghsl_tiles_for_rwanda():
    # Tile R10_C22 starts at x = 2,959,000 (from the tile's own geotransform); Rwanda straddles it.
    assert ghsl.tiles_for_bounds(2_884_000, -353_000, 3_097_000, -128_000) == ["R10_C21", "R10_C22"]
    assert ghsl.tiles_for_bounds(2_960_000, -10, 2_960_010, -5) == ["R10_C22"]


# --------------------------------------------------------------------------- neighbourhood share
def test_disk_offsets_cover_the_ground_area_of_the_circle():
    dr, dc = disk_offsets(50, 11.1, 9.0)  # Mollweide pixel at Rwanda: 11.1 m wide, 9.0 m tall
    area = len(dr) * 11.1 * 9.0
    assert abs(area - math.pi * 50**2) / (math.pi * 50**2) < 0.1
    assert dr.max() == 5 and dc.max() == 4  # 50/9.0 -> 5 rows, 50/11.1 -> 4 cols


def test_nres_share_planted_pattern():
    arr = np.zeros((41, 41), dtype="uint8")
    arr[18:23, 18:23] = ghsl.FUN_RES  # 25 RES pixels around the centre
    arr[20, 18:22] = ghsl.FUN_NRES  # 4 of them NRES
    arr[0, 0] = ghsl.FUN_NRES  # far outside 50 m: must not count
    x0, y0 = 800_000.0, 9_780_000.0  # UTM 35S grid in Kigali
    g = Grid(arr, Affine(10, 0, x0, 0, -10, y0), "EPSG:32735", 255)
    lon, lat = _utm_to_lonlat(x0 + np.array([205.0, 305.0]), y0 - np.array([205.0, 305.0]))
    share = nres_share(g, lon, lat, radius_m=50)
    assert share[0] == pytest.approx(4 / 25)
    assert np.isnan(share[1])  # no built pixel within 50 m -> not observed, not 0


def _utm_to_lonlat(x, y):
    from pyproj import Transformer

    return Transformer.from_crs("EPSG:32735", "EPSG:4326", always_xy=True).transform(x, y)


def test_msz_decoding_matches_official_legend():
    out = decode_msz(np.array([0, 1, 5, 11, 15, 21, 25, 255, np.nan]))
    assert list(out) == ["outside_msz", "open_low_veg", "open_road", "res_le3m", "res_gt30m", "nres_le3m",
                         "nres_gt30m", None, None]


# --------------------------------------------------------------------------- population density units
def test_worldpop_cell_area_matches_authalic_sphere():
    # Independent formula: area = R^2 * dlon * (sin(lat_top) - sin(lat_bottom)), authalic radius.
    d = 3 / 3600
    got = worldpop.cell_area_km2(np.array([0.0, -2.0]), d, d)
    r = 6371007.2
    exp = [r**2 * math.radians(d) * (math.sin(math.radians(t)) - math.sin(math.radians(t - d))) / 1e6
           for t in (0.0, -2.0)]
    np.testing.assert_allclose(got, exp, rtol=5e-3)
    assert 0.0084 < got[1] < 0.0086  # a "100 m" WorldPop cell is not 0.01 km2


# --------------------------------------------------------------------------- quadkeys
def test_quadkey_matches_bing_documentation_example():
    # "Bing Maps Tile System": tile (3, 5) at level 3 has quadkey "213".
    assert rwi.tile_to_quadkey(3, 5, 3) == "213"
    assert rwi.quadkey_to_tile("213") == (3, 5, 3)


def test_tile_xy_known_values():
    assert [int(v[0]) for v in rwi.tile_xy(np.array([0.1]), np.array([-0.1]), 1)] == [1, 1]  # SE quadrant
    x, y = rwi.tile_xy(np.array([-179.99]), np.array([85.0511]), 14)  # top-left of the world
    assert (x[0], y[0]) == (0, 0)
    # Meta RWI Rwanda row 1 publishes its tile centre at (-1.6587038, 30.0036621); recompute that centre.
    x, y = rwi.tile_xy(np.array([30.0036621]), np.array([-1.6587038]))
    lon, lat = rwi.tile_centre(x, y)
    assert abs(lon[0] - 30.0036621) < 1e-6 and abs(lat[0] - -1.6587038) < 1e-6
    assert (int(x[0]), int(y[0])) == (9557, 8267)


def test_rwi_join_and_null_outside_tiles():
    table = pd.DataFrame({"tile_x": [9557], "tile_y": [8267], "rwi": [-0.378]})
    got = rwi.rwi_at(np.array([30.0036621, 30.5]), np.array([-1.6587038, -2.5]), table)
    assert got[0] == -0.378 and np.isnan(got[1])


# --------------------------------------------------------------------------- end to end on the fixture
def test_fixture_end_to_end_matches_contract():
    if not all(p.exists() for p in [*ghsl.CLIPPED.values(), worldpop.DENSITY_TIF]):
        pytest.skip("clipped rasters missing: run `pixi run fetch-rasters`")
    from rtl.join.raster import build

    b = pd.read_parquet(FIXTURES_DIR / "buildings_base_fixture.parquet", columns=["bldg_id", "lon", "lat"])
    out = build(b)
    assert list(out.columns) == CONTEXT_RASTER_COLS
    assert len(out) == len(b) and out.bldg_id.tolist() == b.bldg_id.tolist()
    context_raster.validate(out, lazy=True)
    assert out.pop_density_per_km2.median() > 5_000  # Nyamirambo is dense urban Kigali
