"""A2v: Rwanda admin boundaries (province -> village) from geoBoundaries gbOpen ADM0-5.

Why geoBoundaries: it is the only openly licensed source reaching village level (ADM5, 14,815 units); OCHA COD-AB
stops at 1,000 of 2,148 cells. geoBoundaries files carry only `shapeName` per level, no parent names, and the levels
come from different vintages (ADM1 2020, ADM2-5 2012), so the hierarchy is rebuilt spatially: each village takes the
cell/sector/district/province that contains its point-on-surface. Buildings are then matched to a village only, which
keeps the five names consistent for every building.

Outputs
  data/interim/layers/admin_villages.parquet  one row per village with adm1..adm5 names + geometry
  data/interim/aoi_deep_dives.parquet         the three deep-dive sector polygons from config/aoi.yaml
"""

from __future__ import annotations

import geopandas as gpd
import pandas as pd

from rtl.manifest import fetch
from rtl.settings import INTERIM_DIR, aoi, sources

LAYERS = INTERIM_DIR / "layers"
LEVELS = ["ADM0", "ADM1", "ADM2", "ADM3", "ADM4", "ADM5"]
EXPECTED = {"ADM0": 1, "ADM1": 5, "ADM2": 30, "ADM3": 416, "ADM4": 2148, "ADM5": 14815}  # source counts
VILLAGES = LAYERS / "admin_villages.parquet"
DEEP_DIVES = INTERIM_DIR / "aoi_deep_dives.parquet"
METRIC = aoi()["country"]["metric_crs"]


def download() -> dict[str, gpd.GeoDataFrame]:
    url = sources()["admin_boundaries"]["url"]
    out = {}
    for lvl in LEVELS:
        gdf = gpd.read_file(fetch("admin_boundaries", url=url.format(level=lvl)))
        assert len(gdf) == EXPECTED[lvl], f"{lvl}: {len(gdf)} units, expected {EXPECTED[lvl]}"
        out[lvl] = gdf.to_crs("EPSG:4326")
    return out


def hierarchy(levels: dict[str, gpd.GeoDataFrame]) -> gpd.GeoDataFrame:
    """Villages with parent names. A village's point-on-surface is always inside it, so the parent that contains
    that point is the parent the village (mostly) lies in, even where the 2012/2020 boundaries disagree slightly."""
    v = levels["ADM5"][["shapeName", "shapeID", "geometry"]].rename(columns={"shapeName": "adm5_name"})
    pts = gpd.GeoDataFrame(v[["shapeID"]], geometry=v.representative_point(), crs=v.crs)
    for i in (4, 3, 2, 1):
        parent = levels[f"ADM{i}"][["shapeName", "geometry"]].rename(columns={"shapeName": f"adm{i}_name"})
        hit = gpd.sjoin(pts, parent, predicate="within", how="left").drop_duplicates("shapeID")
        if hit[f"adm{i}_name"].isna().any():  # point in a gap of the parent layer: take the nearest parent
            miss = hit[f"adm{i}_name"].isna()
            near = gpd.sjoin_nearest(pts[miss.values].to_crs(METRIC), parent.to_crs(METRIC), how="left")
            hit.loc[miss, f"adm{i}_name"] = near.drop_duplicates("shapeID")[f"adm{i}_name"].values
        v[f"adm{i}_name"] = hit[f"adm{i}_name"].values
    cols = ["adm1_name", "adm2_name", "adm3_name", "adm4_name", "adm5_name"]
    return v.rename(columns={"shapeID": "adm5_id"})[["adm5_id", *cols, "geometry"]]


def deep_dives(levels: dict[str, gpd.GeoDataFrame], villages: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Sector polygons for the deep-dive AOIs. A sector name alone is ambiguous, so the district must match too."""
    sectors = levels["ADM3"][["shapeName", "geometry"]].rename(columns={"shapeName": "adm3_name"})
    # district of each sector = district of most of its villages
    s2d = villages.groupby("adm3_name")["adm2_name"].agg(lambda s: s.mode().iloc[0])
    sectors["adm2_name"] = sectors["adm3_name"].map(s2d)
    rows = []
    for key, dd in aoi()["deep_dives"].items():
        hit = sectors[(sectors.adm3_name.str.lower() == dd["sector"].lower())
                      & (sectors.adm2_name.str.lower() == dd["district"].lower())]
        if len(hit) != 1:
            raise ValueError(f"deep dive {key}: {dd['sector']}/{dd['district']} matched {len(hit)} sectors")
        rows.append(hit.assign(deep_dive=key))
    out = gpd.GeoDataFrame(pd.concat(rows), crs="EPSG:4326")
    out["area_km2"] = out.to_crs(METRIC).area / 1e6
    return out[["deep_dive", "adm2_name", "adm3_name", "area_km2", "geometry"]].reset_index(drop=True)


def country_polygon() -> gpd.GeoDataFrame:
    """ADM0 outline (used to clip national layers such as Gridfinder)."""
    return gpd.read_file(fetch("admin_boundaries", url=sources()["admin_boundaries"]["url"].format(level="ADM0")))


def main() -> None:
    levels = download()
    LAYERS.mkdir(parents=True, exist_ok=True)
    v = hierarchy(levels)
    v.to_parquet(VILLAGES)
    dd = deep_dives(levels, v)
    dd.to_parquet(DEEP_DIVES)
    print({lvl: len(g) for lvl, g in levels.items()})
    print("unique names per level:", {c: v[c].nunique() for c in v.columns if c.endswith("_name")})
    print(dd.drop(columns="geometry").to_string())


if __name__ == "__main__":
    main()
