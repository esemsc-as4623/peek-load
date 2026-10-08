"""Planted-case tests for rtl.join.vector: every geometry is built in UTM metres, so a distance accidentally
computed in degrees, a POI smeared over several roofs, or a wrong admin polygon all fail loudly."""

from __future__ import annotations

import geopandas as gpd
import h3
import pytest
from shapely.geometry import LineString, Point, box

from rtl.join.vector import Layers, join
from rtl.schemas import context_vector

UTM = "EPSG:32735"
X0, Y0 = 785_000.0, 9_784_000.0  # central Kigali in UTM 35S


def g(geoms: list, **cols) -> gpd.GeoDataFrame:
    """GeoDataFrame from UTM geometries (offsets from X0, Y0), stored in EPSG:4326 like the real layers."""
    return gpd.GeoDataFrame(cols, geometry=geoms, crs=UTM).to_crs("EPSG:4326")


def sq(x: float, y: float, s: float = 10) -> object:
    return box(X0 + x, Y0 + y, X0 + x + s, Y0 + y + s)


def pt(x: float, y: float) -> Point:
    return Point(X0 + x, Y0 + y)


def buildings() -> gpd.GeoDataFrame:
    # A: 10x10 m at origin. B: 10 m east of A. C: 2 km west, in the other village. D: holds a small OSM building.
    geoms = [sq(0, 0), sq(20, 0), sq(-2000, 0), sq(500, 500)]
    b = g(geoms, bldg_id=[f"RWA-{i:016x}" for i in range(1, 5)])
    c = b.geometry.to_crs(UTM).centroid.to_crs("EPSG:4326")
    b["h3_r9"] = [h3.latlng_to_cell(p.y, p.x, 9) for p in c]
    return b


def layers(**override) -> Layers:
    villages = g([box(X0 - 1000, Y0 - 1000, X0 + 1000, Y0 + 1000), box(X0 - 3000, Y0 - 1000, X0 - 1000, Y0 + 1000)],
                 adm1_name=["P", "P"], adm2_name=["D", "D"], adm3_name=["S1", "S2"], adm4_name=["C1", "C2"],
                 adm5_name=["east_village", "west_village"])
    osm_buildings = g([box(X0 + 0, Y0 + 0, X0 + 10, Y0 + 6),  # covers 60% of A
                       box(X0 + 500, Y0 + 500, X0 + 503, Y0 + 510)],  # 30% of D, but 100% of itself
                      osm_id=["w1", "w2"], building=["yes", "commercial"], amenity=[None, "bank"], shop=[None, None])
    # n1: kiosk node 4 m east of A and 6 m west of B -> must go to A only.
    # n2..n5: 30, 80, 200, 400 m north of A's top edge, for the radius counts.
    osm_pois = g([pt(14, 5), pt(5, 40), pt(5, 90), pt(5, 210), pt(5, 410)],
                 osm_id=["n1", "n2", "n3", "n4", "n5"], osm_type=["node"] * 5,
                 building=[None] * 5, amenity=[None, "cafe", None, None, None], shop=["kiosk", None, None, None, None])
    # o1 inside B (15 m from A); o2 12 m north of C; o3 20 m south of C (too far)
    overture = g([pt(25, 5), pt(-1995, 22), pt(-1995, -20)], overture_id=["o1", "o2", "o3"],
                 category=["hair_salon", "church", "bar"], confidence=[0.9, 0.4, 0.8])
    facilities = g([box(X0 - 2010, Y0 - 10, X0 - 1980, Y0 + 20), pt(505, 505)],
                   facility_id=["f1", "f2"], facility_type=["education:school", "health:health_post"])
    roads = g([LineString([(X0 + 110, Y0 - 500), (X0 + 110, Y0 + 500)]),  # 100 m east of A's edge (x=10)
               LineString([(X0 - 300, Y0 - 500), (X0 - 300, Y0 + 500)])],  # 300 m west of A's edge (x=0)
              road_class=["minor", "major"])
    osm_power = g([LineString([(X0 - 500, Y0 + 60), (X0 + 500, Y0 + 60)])])  # 50 m north of A's top (y=10)
    gridfinder = g([LineString([(X0 - 500, Y0 - 1000), (X0 + 500, Y0 - 1000)])])
    frames = dict(villages=villages, osm_buildings=osm_buildings, osm_pois=osm_pois, overture=overture,
                  facilities=facilities, roads=roads, osm_power=osm_power, gridfinder=gridfinder)
    return Layers.from_frames(**{**frames, **override})


@pytest.fixture(scope="module")
def cv():
    return join(iter([buildings()]), layers()).set_index("bldg_id")


A, B, C, D = (f"RWA-{i:016x}" for i in range(1, 5))


def test_contract_and_rows(cv):
    assert len(cv) == 4
    context_vector.validate(cv.reset_index())


def test_distances_are_metres(cv):
    assert cv.loc[A, "dist_road_any_m"] == pytest.approx(100, abs=0.5)
    assert cv.loc[A, "dist_road_major_m"] == pytest.approx(300, abs=0.5)
    assert cv.loc[A, "dist_powerline_m"] == pytest.approx(50, abs=0.5)  # OSM line wins over Gridfinder at 1 km


def test_poi_counts_per_radius(cv):
    # A's POIs by edge distance: n1 4 m, n2 30 m, n3 80 m, n4 200 m, n5 400 m
    assert cv.loc[A, ["poi_count_50m", "poi_count_100m", "poi_count_250m"]].tolist() == [2, 3, 4]


def test_admin_point_in_polygon(cv):
    assert cv.loc[A, "adm5_name"] == "east_village" and cv.loc[A, "adm3_name"] == "S1"
    assert cv.loc[C, "adm5_name"] == "west_village" and cv.loc[C, "adm4_name"] == "C2"


def test_polygon_match_needs_half_of_smaller_shape(cv):
    assert cv.loc[A, "osm_match"] == "polygon" and cv.loc[A, "osm_building"] == "yes"
    # w2 is fully inside D, so it covers 100% of the *smaller* shape -> polygon match, with its tags
    assert cv.loc[D, "osm_match"] == "polygon" and cv.loc[D, "osm_amenity"] == "bank"


def test_polygon_below_half_does_not_match():
    # same size as A, shifted 6 m east: overlap is 40% of both shapes
    lay = layers(osm_buildings=g([box(X0 + 6, Y0 + 0, X0 + 16, Y0 + 10)], osm_id=["w9"], building=["yes"],
                                 amenity=[None], shop=[None]))
    out = join(iter([buildings()]), lay).set_index("bldg_id")
    assert out.loc[A, "osm_building"] is None
    assert out.loc[A, "osm_match"] == "point"  # the kiosk node n1 is still assigned to A


def test_poi_goes_to_one_nearest_building(cv):
    # n1 is 4 m from A and 6 m from B: A gets the shop (filled under its tag-less polygon), B gets nothing
    assert cv.loc[A, "osm_shop"] == "kiosk"
    assert cv.loc[B, "osm_match"] == "none" and cv.loc[B, "osm_shop"] is None


def test_overture_within_15m(cv):
    assert cv.loc[B, "overture_category"] == "hair_salon"  # o1 is 5 m from B, 15 m from A
    assert cv.loc[B, "overture_confidence"] == pytest.approx(0.9)
    assert cv.loc[C, "overture_category"] == "church"  # o2 at 12 m; o3 at 20 m is ignored
    assert pd_isna(cv.loc[A, "overture_category"])  # o1 sits inside B, so it is B's, not A's


def test_facilities(cv):
    assert cv.loc[C, "facility_type"] == "education:school"  # centroid inside the school compound
    assert cv.loc[D, "facility_type"] == "health:health_post"  # point inside the footprint
    assert cv.loc[A, "facility_type"] is None


def test_h3_parent(cv):
    b = buildings().set_index("bldg_id")
    assert cv.loc[A, "h3_r7"] == h3.cell_to_parent(b.loc[A, "h3_r9"], 7)


def pd_isna(v) -> bool:
    return v is None or v != v
