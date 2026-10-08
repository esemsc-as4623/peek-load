"""A2v: facility registry layer: health (healthsites.io), education and places of worship (OSM).

Health: healthsites.io (HDX export, ODbL) is the declared registry. In Rwanda many health centres and health posts
are tagged amenity=hospital, so the subtype comes from the *name* first ("... Health Centre" -> health_centre). A bare
hospital tag on a facility whose name says nothing (often just a place name) becomes `health:unspecified`: we don't
claim "hospital" without evidence. Education and worship come from the OSM layers built by rtl.ingest.osm.

Output data/interim/layers/facilities.parquet: facility_id, facility_type ("health:health_centre",
"education:school", "worship:christian", ...), name, source, geometry (points or areas, EPSG:4326).
"""

from __future__ import annotations

import re

import geopandas as gpd
import pandas as pd

from rtl.ingest.osm import LAYERS
from rtl.manifest import fetch

OUT = LAYERS / "facilities.parquet"

# Name patterns (English / French / Kinyarwanda) checked in order; first hit wins.
HEALTH_NAME = [
    ("health_post", r"health post|poste? de sant|\bhp\b"),
    ("health_centre", r"health cent|cent(re|er) de sant|\bcs\b|ikigo nderabuzima"),
    ("hospital", r"hospital|h[oô]pital|\bchu\b|\bchuk\b|\bkfh\b"),
    ("dispensary", r"dispensar"),
    ("pharmacy", r"pharmac"),
    ("clinic", r"clinic|clinique|polyclin"),
]
EDUCATION = {"school", "kindergarten", "college", "university"}


def health_subtype(name: str | None, amenity: str | None, healthcare: str | None) -> str:
    n = (name or "").lower()
    for sub, pat in HEALTH_NAME:
        if re.search(pat, n):
            return sub
    tag = amenity or healthcare or "other"
    return {"doctors": "clinic", "drugstore": "pharmacy", "hospital": "unspecified"}.get(tag, tag)


def health() -> gpd.GeoDataFrame:
    hs = gpd.read_file(fetch("health_facilities", filename="healthsites_rwanda.geojson")).to_crs("EPSG:4326")
    hs = hs.replace({"": None})
    sub = [health_subtype(n, a, h) for n, a, h in zip(hs["name"], hs["amenity"], hs["healthcare"], strict=True)]
    return gpd.GeoDataFrame({
        "facility_id": "healthsites:" + hs["osm_type"].astype(str) + "/" + hs["osm_id"].astype(str),
        "facility_type": ["health:" + s for s in sub],
        "name": hs["name"],
        "source": "healthsites",
        "geometry": hs.geometry,
    }, crs="EPSG:4326")


def osm_education_worship() -> gpd.GeoDataFrame:
    """Nodes from osm_pois; areas from osm_buildings and osm_compounds (school grounds etc. keep their polygon)."""
    nodes = gpd.read_parquet(LAYERS / "osm_pois.parquet").query("osm_type == 'node'")
    areas = pd.concat([gpd.read_parquet(LAYERS / "osm_buildings.parquet"),
                       gpd.read_parquet(LAYERS / "osm_compounds.parquet")], ignore_index=True)
    df = gpd.GeoDataFrame(pd.concat([nodes, areas], ignore_index=True), crs="EPSG:4326")
    edu = df["amenity"].isin(EDUCATION)
    wor = df["amenity"].eq("place_of_worship")
    df = df[edu | wor].copy()
    df["facility_type"] = ("education:" + df["amenity"]).where(df["amenity"].isin(EDUCATION),
                                                               "worship:" + df["religion"].fillna("unknown"))
    df["facility_id"] = "osm:" + df["osm_type"] + "/" + df["osm_id"].astype(str)
    return df.assign(source="osm")[["facility_id", "facility_type", "name", "source", "geometry"]]


def main() -> None:
    fac = gpd.GeoDataFrame(pd.concat([health(), osm_education_worship()], ignore_index=True), crs="EPSG:4326")
    fac = fac.drop_duplicates("facility_id").reset_index(drop=True)
    fac.to_parquet(OUT)
    kind = fac.geom_type.map(lambda t: "point" if t == "Point" else "area")
    print(pd.crosstab(fac["facility_type"], kind).to_string())
    print(f"facilities: {len(fac):,} -> {OUT}")


if __name__ == "__main__":
    main()
