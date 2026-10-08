"""Checks on the committed fixture, including an independent re-computation of DuckDB's metrics."""

import numpy as np
import pytest
import shapely

geopandas = pytest.importorskip("geopandas")

from rtl.schemas import QA_FLAGS, buildings_base  # noqa: E402
from rtl.settings import FIXTURES_DIR  # noqa: E402

FIXTURE = FIXTURES_DIR / "buildings_base_fixture.parquet"


@pytest.fixture(scope="module")
def gdf():
    if not FIXTURE.exists():
        pytest.skip("fixture missing: run `pixi run make-fixture`")
    return geopandas.read_parquet(FIXTURE)


def test_fixture_matches_contract(gdf):
    buildings_base.validate(gdf, lazy=True)


def test_fixture_size_is_reasonable(gdf):
    assert 1500 <= len(gdf) <= 3000


def test_area_and_perimeter_match_independent_projection(gdf):
    # DuckDB computed these via ST_Transform; recompute with pyproj/shapely and compare.
    utm = gdf.to_crs("EPSG:32735")
    np.testing.assert_allclose(gdf.area_m2, utm.area, rtol=1e-6)
    np.testing.assert_allclose(gdf.perimeter_m, utm.length, rtol=1e-6)


def test_centroids_inside_footprints_or_close(gdf):
    pts = shapely.points(gdf.lon, gdf.lat)
    d = shapely.distance(pts, gdf.geometry.values)
    assert (d < 1e-4).mean() > 0.99  # centroid of a concave footprint can fall just outside


def test_flags_are_known(gdf):
    used = {f for v in gdf.qa_flags for f in v.split(",") if f}
    assert used <= set(QA_FLAGS)
