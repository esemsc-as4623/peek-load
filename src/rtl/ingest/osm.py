"""A2v: download the Geofabrik OSM extract for Rwanda and split it into the vector layers the context join uses.

GDAL's OSM driver assembles nodes/ways/relations into points, lines and (multi)polygons; `osmconf.ini` next to this
file lists the tags kept. Each layer is written as GeoParquet (EPSG:4326) to data/interim/layers/:

  osm_buildings.parquet  polygons with a building=* tag (+ amenity/shop/... on the same feature)
  osm_pois.parquet       "points of interest" = places of activity: tagged nodes plus the point-on-surface of tagged
                         areas (buildings and compounds), so a school is one POI however it was mapped. Street
                         furniture (parking, toilets, benches, viewpoints...) is not a POI.
  osm_compounds.parquet  non-building areas with amenity/healthcare/... (school grounds, hospital compounds)
  osm_roads.parquet      highways with road_class = major | minor
  osm_powerlines.parquet power=line/minor_line/cable
  osm_landuse.parquet    landuse=* polygons
"""

from __future__ import annotations

import os
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio

from rtl.manifest import fetch, read_manifest
from rtl.settings import INTERIM_DIR, REPO_ROOT

LAYERS = INTERIM_DIR / "layers"
OSMCONF = Path(__file__).with_name("osmconf.ini")

POI_KEYS = ["amenity", "shop", "craft", "office", "tourism", "healthcare", "industrial"]
POI_MAN_MADE = {"water_works", "water_tower", "works", "pumping_station", "wastewater_plant"}
NOT_POI = {"parking", "parking_space", "parking_entrance", "bicycle_parking", "motorcycle_parking", "bench",
           "waste_basket", "waste_disposal", "recycling", "toilets", "viewpoint", "information", "fixme", "yes",
           "shelter", "clock", "vending_machine", "telephone", "post_box"}
TAG_COLS = ["name", "building", "building_levels", *POI_KEYS, "religion", "man_made", "leisure"]

# Roads: "major" = the classified network; "minor" = other motor-vehicle ways incl. tracks (rural access).
# Footways, paths, steps, cycleways and proposed/under-construction ways are not roads for accessibility.
MAJOR = {"motorway", "trunk", "primary", "secondary", "tertiary"}
MAJOR |= {f"{h}_link" for h in MAJOR}
MINOR = {"unclassified", "residential", "living_street", "service", "track", "road", "busway"}
POWER = {"line", "minor_line", "cable"}


def raw_pbf() -> Path:
    entries = [e for e in read_manifest().values() if e["source_id"] == "osm_geofabrik"]
    if not entries:
        return fetch("osm_geofabrik")
    return REPO_ROOT / max(entries, key=lambda e: e["version"])["path"]


def read_layer(pbf: Path, layer: str) -> gpd.GeoDataFrame:
    os.environ["OSM_CONFIG_FILE"] = str(OSMCONF)
    # A handful of broken OSM ways (rings with < 3 nodes) can't be built; they become empty and are dropped in main().
    gdf = pyogrio.read_dataframe(pbf, layer=layer, on_invalid="ignore")
    gdf = gdf.set_crs("EPSG:4326", allow_override=True)
    if layer == "multipolygons":  # self-intersecting areas would break overlap computations later
        gdf["geometry"] = gdf.geometry.make_valid()
    return gdf


def _has_any(df: pd.DataFrame, keys: list[str]) -> pd.Series:
    return df[keys].notna().any(axis=1)


def _poi_mask(df: pd.DataFrame) -> pd.Series:
    return _has_any(df, POI_KEYS) | df["man_made"].isin(POI_MAN_MADE)


def split(points: gpd.GeoDataFrame, lines: gpd.GeoDataFrame, polys: gpd.GeoDataFrame) -> dict[str, gpd.GeoDataFrame]:
    """Pure function (testable without a PBF): GDAL layers -> our named layers."""
    polys = polys.copy()
    if "osm_way_id" in polys:  # GDAL puts ways' ids in osm_way_id and relations' in osm_id
        polys["osm_id"] = polys["osm_way_id"].fillna(polys["osm_id"]).astype(str)
        polys["osm_type"] = polys["osm_way_id"].notna().map({True: "way", False: "relation"})
    else:
        polys["osm_type"] = "way"
    points = points.assign(osm_id=points["osm_id"].astype(str), osm_type="node")
    for c in TAG_COLS:
        for df in (points, polys):
            if c not in df:
                df[c] = None

    is_bldg = polys["building"].notna()
    buildings = polys[is_bldg][["osm_id", "osm_type", *TAG_COLS, "geometry"]]
    compounds = polys[~is_bldg & _poi_mask(polys)][["osm_id", "osm_type", *TAG_COLS, "geometry"]]
    poi_nodes = points[_poi_mask(points)][["osm_id", "osm_type", *TAG_COLS, "geometry"]]
    areas = polys[_poi_mask(polys)][["osm_id", "osm_type", *TAG_COLS, "geometry"]]
    poi_areas = areas.assign(geometry=areas.representative_point())
    pois = gpd.GeoDataFrame(pd.concat([poi_nodes, poi_areas], ignore_index=True), crs="EPSG:4326")
    # poi_key = first POI key present (in POI_KEYS order), poi_value = its value: "amenity"/"restaurant"
    pois["poi_key"] = pois[POI_KEYS].notna().idxmax(axis=1).where(_has_any(pois, POI_KEYS), "man_made")
    pois["poi_value"] = pois[[*POI_KEYS, "man_made"]].bfill(axis=1).iloc[:, 0]
    pois = pois[~pois["poi_value"].isin(NOT_POI)]

    hw = lines[lines["highway"].isin(MAJOR | MINOR)]
    roads = hw.assign(road_class=hw["highway"].isin(MAJOR).map({True: "major", False: "minor"}))
    roads = roads[["osm_id", "name", "highway", "road_class", "geometry"]]
    power = lines[lines["power"].isin(POWER)][["osm_id", "power", "voltage", "geometry"]]
    landuse = polys[polys["landuse"].notna()][["osm_id", "osm_type", "name", "landuse", "geometry"]]
    return {"osm_buildings": buildings, "osm_pois": pois, "osm_compounds": compounds, "osm_roads": roads,
            "osm_powerlines": power, "osm_landuse": landuse}


def main() -> None:
    pbf = raw_pbf()
    layers = split(read_layer(pbf, "points"), read_layer(pbf, "lines"), read_layer(pbf, "multipolygons"))
    LAYERS.mkdir(parents=True, exist_ok=True)
    for name, gdf in layers.items():
        ok = gdf.geometry.notna() & ~gdf.geometry.is_empty
        gdf[ok].reset_index(drop=True).to_parquet(LAYERS / f"{name}.parquet")
        print(f"{name}: {ok.sum():,} written, {(~ok).sum()} dropped (unbuildable geometry)")


if __name__ == "__main__":
    main()
