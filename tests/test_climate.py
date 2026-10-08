"""A3 climate + DEM: planted-case tests for every transform between the raw grid and climate_h3."""

import numpy as np
import pandas as pd
import pytest

from rtl.climate.downscale import bilinear_weights, fit_lapse_rate, interpolate, lapse_shift
from rtl.climate.stats import degree_days_per_year, summarise, to_kwh_m2_day, to_local_days
from rtl.ingest.dem import snap_bounds, tiles_for_bbox
from rtl.ingest.openmeteo import call_weight, grid_points
from rtl.schemas import climate_h3
from rtl.settings import INTERIM_DIR


# ------------------------------------------------------------------ ingest helpers
def test_dem_tiles_for_rwanda_use_south_west_corner_names():
    tiles = tiles_for_bbox([28.77, -2.88, 30.93, -1.01])
    assert len(tiles) == 6
    assert "Copernicus_DSM_COG_10_S02_00_E029_00_DEM" in tiles  # covers lat -2..-1, lon 29..30
    assert "Copernicus_DSM_COG_10_S03_00_E030_00_DEM" in tiles
    assert not any("S01" in t for t in tiles)  # bbox stops below -1


def test_snap_bounds_lands_on_source_pixel_grid():
    res, ox, oy = 1 / 3600, 27.999861111, -0.999861111
    b = snap_bounds((28.77, -2.88, 30.93, -1.01), ox, oy, res)
    for v, o in zip(b, (ox, oy, ox, oy), strict=True):
        assert abs((v - o) / res - round((v - o) / res)) < 1e-6
    assert b[0] <= 28.77 and b[1] <= -2.88 and b[2] >= 30.93 and b[3] >= -1.01


def test_grid_points_enclose_bbox_plus_one_ring():
    pts = grid_points([28.80, -2.85, 30.90, -1.04], step=0.1, margin=1)
    lats, lons = sorted({p[0] for p in pts}), sorted({p[1] for p in pts})
    assert lons[0] == 28.7 and lons[-1] == 31.0 and lats[0] == -3.0 and lats[-1] == -0.9
    assert len(pts) == len(lats) * len(lons) == 22 * 24


def test_call_weight_matches_open_meteo_documented_example():
    assert call_weight(10, 3650, 1) == pytest.approx(260.7, abs=0.05)
    assert call_weight(1, 2, 1) == pytest.approx(0.1)  # minimum 14 days


# ------------------------------------------------------------------ time zone + statistics
def test_utc_to_cat_shift_puts_local_hour_h_at_utc_h_minus_2():
    times = pd.date_range("2018-12-31", "2019-01-03 23:00", freq="h", tz="UTC")
    t = times.hour.to_numpy().astype(float)  # value = UTC hour
    days_arr, days = to_local_days(t, times, "2019-01-01", "2019-01-03")
    assert days_arr.shape == (3, 24)
    np.testing.assert_array_equal(days_arr[0], (np.arange(24) - 2) % 24)
    # and the first local hour of 2019-01-01 is UTC 2018-12-31 22:00
    t2 = np.arange(len(times), dtype=float)
    arr2, _ = to_local_days(t2, times, "2019-01-01", "2019-01-03")
    assert times[int(arr2[0, 0])] == pd.Timestamp("2018-12-31 22:00", tz="UTC")


def test_to_local_days_rejects_incomplete_coverage():
    times = pd.date_range("2019-01-01", "2019-01-02 23:00", freq="h", tz="UTC")  # no 22:00-23:00 of Dec 31
    with pytest.raises(ValueError):
        to_local_days(np.zeros(len(times)), times, "2019-01-01", "2019-01-02")


def test_degree_days_on_planted_series():
    days = pd.date_range("2019-01-01", "2020-12-31", freq="D")
    daily = np.full(len(days), 20.0)
    daily[(days.year == 2019) & (days.month == 3)] = 26.0  # 31 days, 2 K above 24
    # 2019: 31*2 = 62 above 24; 2020: 0  -> mean 31 per year
    assert degree_days_per_year(daily, days, 24.0) == pytest.approx(31.0)
    # base 18: every day contributes 2 K, March 2019 8 K -> 2019: 334*2 + 31*8 = 916, 2020: 366*2 = 732
    assert degree_days_per_year(daily, days, 18.0) == pytest.approx((916 + 732) / 2)


def test_summarise_daily_max_p95_and_typical_day():
    days = pd.date_range("2019-01-01", "2019-04-10", freq="D")  # 100 days
    hours = np.arange(24)
    profile = 15 + 5 * np.sin((hours - 8) / 24 * 2 * np.pi)  # peak at 14 h
    t = np.tile(profile, (len(days), 1)) + np.arange(len(days))[:, None] * 0.1  # warming 0.1 K/day
    s = summarise(t, days)
    np.testing.assert_allclose(s["typical_day"], profile + 0.1 * 49.5)
    assert np.argmax(s["typical_day"]) == 14
    assert s["tmax_p95_c"] == pytest.approx(np.percentile(profile.max() + 0.1 * np.arange(100), 95))
    assert s["t2m_mean_c"] == pytest.approx(profile.mean() + 4.95)


def test_ghi_units():
    assert to_kwh_m2_day(np.array([18.0]), "MJ/m^2/day")[0] == pytest.approx(5.0)
    assert to_kwh_m2_day(np.array([5.0]), "kW-hr/m^2/day")[0] == 5.0
    with pytest.raises(ValueError):
        to_kwh_m2_day(np.array([1.0]), "W/m^2")


# ------------------------------------------------------------------ downscaling
def test_bilinear_reproduces_a_plane_and_clamps_outside():
    glat, glon = np.array([-3.0, -2.9, -2.8]), np.array([29.0, 29.1, 29.2, 29.3])
    la, lo = np.meshgrid(glat, glon, indexing="ij")
    field = (3 + 2 * la - 0.5 * lo).ravel()
    plat, plon = np.array([-2.95, -2.81, -2.9]), np.array([29.05, 29.27, 29.3])
    idx, w = bilinear_weights(plat, plon, glat, glon)
    np.testing.assert_allclose(w.sum(axis=1), 1)
    np.testing.assert_allclose(interpolate(field, idx, w), 3 + 2 * plat - 0.5 * plon, atol=1e-12)
    idx, w = bilinear_weights(np.array([-3.5]), np.array([29.15]), glat, glon)  # south of the grid
    assert interpolate(field, idx, w)[0] == pytest.approx(3 + 2 * -3.0 - 0.5 * 29.15)


def test_interpolate_series_keeps_trailing_axes():
    field = np.arange(6 * 5, dtype=float).reshape(6, 5)
    idx, w = np.array([[0, 1, 2, 3]]), np.array([[0.25, 0.25, 0.25, 0.25]])
    np.testing.assert_allclose(interpolate(field, idx, w)[0], field[:4].mean(axis=0))


def test_lapse_rate_fit_recovers_planted_slope():
    rng = np.random.default_rng(0)
    z = rng.uniform(900, 3000, 300)
    t = 30 - 0.0055 * z + rng.normal(0, 0.05, z.size)
    gamma, intercept, r2 = fit_lapse_rate(z, t)
    assert gamma == pytest.approx(-0.0055, rel=0.02) and intercept == pytest.approx(30, abs=0.2) and r2 > 0.99


def test_lapse_correction_sign_higher_is_colder():
    gamma = -0.0065
    assert lapse_shift(gamma, np.array([1800.0]), np.array([1500.0]))[0] == pytest.approx(-1.95)
    assert lapse_shift(gamma, np.array([1300.0]), np.array([1500.0]))[0] == pytest.approx(1.3)


# ------------------------------------------------------------------ produced table (if built)
@pytest.fixture(scope="module")
def table():
    path = INTERIM_DIR / "climate_h3.parquet"
    if not path.exists():
        pytest.skip("climate_h3 not built: run `pixi run climate-h3`")
    return pd.read_parquet(path)


def test_table_matches_contract_and_is_internally_consistent(table):
    climate_h3.validate(table, lazy=True)
    hourly = table[[f"t2m_h{h:02d}_c" for h in range(24)]].to_numpy()
    # the typical day averages back to the annual mean (both are means over the same hours)
    np.testing.assert_allclose(hourly.mean(axis=1), table.t2m_mean_c, atol=1e-6)
    # daytime peak in local time falls in the afternoon, minimum around dawn (wrong offset would move these)
    assert 12 <= np.median(hourly.argmax(axis=1)) <= 16
    assert 4 <= np.median(hourly.argmin(axis=1)) <= 7
    assert (table.cdd18_per_year >= table.cdd24_per_year).all()
    # colder with elevation across cells
    assert np.corrcoef(table.elevation_m, table.t2m_mean_c)[0, 1] < -0.8
