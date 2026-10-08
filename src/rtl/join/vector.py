"""A2v: join vector context (admin, OSM, Overture, facilities, roads, power) to every building.

Output: data/interim/context_vector.parquet with exactly `schemas.CONTEXT_VECTOR_COLS`, one row per bldg_id.

Why shapely and not DuckDB: most steps are nearest-neighbour questions ("nearest road", "nearest building to this
POI"), which DuckDB spatial can't answer without a quadratic join; shapely's STRtree can (`query_nearest`,
`predicate="dwithin"`). Buildings are streamed in chunks (buildings_base is sorted by h3_r9, so a chunk is a compact
area) and every distance is computed in metres in UTM 35S (EPSG:32735), never in degrees.

Matching rules (all distances are from the footprint polygon edge; 0 if inside):
- admin: the village whose polygon contains the footprint centroid; the village carries all five names
  (rtl.ingest.admin builds the hierarchy), so a building's names are always mutually consistent.
- osm_match = "polygon": an OSM building polygon whose overlap covers >= 50% of the smaller of the two shapes
  (best overlap wins). Else "point": an OSM POI node assigned to this footprint (see next). Else "none".
  When a polygon matches but carries no amenity/shop, amenity/shop are taken from an assigned node, if any.
- POI assignment (OSM nodes, Overture places, facility points): each POI goes to ONE building, the one containing it,
  else the nearest within 15 m. In dense blocks a 15 m radius touches many roofs; without this a single shop node
  would tag all of them. A building keeps its nearest assigned POI of each kind.
- facility_type: an assigned facility point, else the smallest facility area (building or compound, e.g. school
  grounds) containing the centroid.
- poi_count_*: OSM POIs (rtl.ingest.osm definition) within 50/100/250 m of the footprint. Overture is not added:
  most Overture places duplicate OSM ones and would be double counted.
- dist_powerline_m: nearest OSM power line or Gridfinder predicted MV line.
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Iterator
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import h3
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import shapely
from shapely import STRtree

from rtl.ingest.osm import LAYERS
from rtl.locks import heavy_job
from rtl.schemas import CONTEXT_VECTOR_COLS
from rtl.settings import INTERIM_DIR, aoi

METRIC = aoi()["country"]["metric_crs"]
BASE = INTERIM_DIR / "buildings_base.parquet"
OUT = INTERIM_DIR / "context_vector.parquet"
CHUNK = 200_000
MATCH_M = 15.0
MIN_OVERLAP = 0.5
RADII = (50, 100, 250)
ADM = ["adm1_name", "adm2_name", "adm3_name", "adm4_name", "adm5_name"]


def _utm(gdf: gpd.GeoDataFrame) -> np.ndarray:
    return gdf.geometry.to_crs(METRIC).values


@dataclass
class Layer:
    """A national layer projected to UTM, with its attribute table and a spatial index."""

    attrs: pd.DataFrame
    geoms: np.ndarray
    tree: STRtree

    @classmethod
    def of(cls, gdf: gpd.GeoDataFrame) -> Layer:
        gdf = gdf.reset_index(drop=True)
        geoms = _utm(gdf) if len(gdf) else np.array([], dtype=object)
        return cls(pd.DataFrame(gdf.drop(columns="geometry")), geoms, STRtree(geoms))


@dataclass
class Layers:
    villages: Layer
    osm_buildings: Layer
    osm_nodes: Layer  # OSM POIs mapped as nodes (for the point match)
    osm_pois: Layer  # all OSM POIs (for counts)
    overture: Layer
    fac_points: Layer
    fac_areas: Layer
    roads_major: Layer
    roads_any: Layer
    power: Layer

    @classmethod
    def from_frames(cls, villages, osm_buildings, osm_pois, overture, facilities, roads, osm_power, gridfinder):
        is_pt = facilities.geom_type == "Point"
        fac_areas = facilities[~is_pt].copy()
        fac_areas["area_m2"] = fac_areas.to_crs(METRIC).area
        power = pd.concat([osm_power[["geometry"]], gridfinder[["geometry"]]], ignore_index=True)
        return cls(
            villages=Layer.of(villages[[*ADM, "geometry"]]),
            osm_buildings=Layer.of(osm_buildings[["osm_id", "building", "amenity", "shop", "geometry"]]),
            osm_nodes=Layer.of(osm_pois[osm_pois["osm_type"] == "node"][
                ["osm_id", "building", "amenity", "shop", "geometry"]]),
            osm_pois=Layer.of(osm_pois[["osm_id", "geometry"]]),
            overture=Layer.of(overture[["overture_id", "category", "confidence", "geometry"]]),
            fac_points=Layer.of(facilities[is_pt][["facility_id", "facility_type", "geometry"]]),
            fac_areas=Layer.of(fac_areas[["facility_id", "facility_type", "area_m2", "geometry"]]),
            roads_major=Layer.of(roads[roads["road_class"] == "major"][["geometry"]]),
            roads_any=Layer.of(roads[["geometry"]]),
            power=Layer.of(gpd.GeoDataFrame(power, crs="EPSG:4326")),
        )


def load_layers(d: Path = LAYERS) -> Layers:
    r = gpd.read_parquet
    return Layers.from_frames(
        villages=r(d / "admin_villages.parquet"), osm_buildings=r(d / "osm_buildings.parquet"),
        osm_pois=r(d / "osm_pois.parquet"), overture=r(d / "overture_places.parquet"),
        facilities=r(d / "facilities.parquet"), roads=r(d / "osm_roads.parquet"),
        osm_power=r(d / "osm_powerlines.parquet"), gridfinder=r(d / "gridfinder_mv.parquet"))


# --------------------------------------------------------------------------- per-chunk steps
def admin_names(cen: np.ndarray, v: Layer) -> pd.DataFrame:
    ib, iv = v.tree.query(cen, predicate="within")
    first = pd.Series(iv, index=ib).groupby(level=0).min()  # a centroid on a shared edge: lowest index, stable
    out = pd.DataFrame(index=range(len(cen)), columns=ADM, dtype=object)
    out.loc[first.index, ADM] = v.attrs.loc[first.values, ADM].values
    return out


def polygon_match(fp: np.ndarray, osm: Layer) -> pd.DataFrame:
    """Best-overlapping OSM building per footprint, if overlap >= 50% of the smaller shape."""
    ib, io = osm.tree.query(fp, predicate="intersects")
    inter = shapely.area(shapely.intersection(fp[ib], osm.geoms[io]))
    frac = inter / np.minimum(shapely.area(fp[ib]), shapely.area(osm.geoms[io]))
    m = pd.DataFrame({"i": ib, "o": io, "frac": frac})
    m = m[m["frac"] >= MIN_OVERLAP].sort_values(["i", "frac", "o"], ascending=[True, False, True])
    m = m.drop_duplicates("i")
    tags = osm.attrs.loc[m["o"].values, ["building", "amenity", "shop"]].set_axis(m["i"].values)
    return tags.add_prefix("poly_")


def candidates(fp: np.ndarray, ids: np.ndarray, pts: Layer, kind: str) -> pd.DataFrame:
    """Every (POI, footprint) pair within 15 m, with the distance. Resolved to one building per POI after all chunks,
    so a POI near a chunk border still goes to its truly nearest building."""
    if not len(pts.geoms):
        return pd.DataFrame(columns=["kind", "poi", "bldg_id", "dist"])
    ip, ib = STRtree(fp).query(pts.geoms, predicate="dwithin", distance=MATCH_M)
    d = shapely.distance(pts.geoms[ip], fp[ib])
    return pd.DataFrame({"kind": kind, "poi": ip, "bldg_id": ids[ib], "dist": d})


def facility_area(cen: np.ndarray, fa: Layer) -> pd.Series:
    ib, ia = fa.tree.query(cen, predicate="within")
    m = pd.DataFrame({"i": ib, "area": fa.attrs["area_m2"].values[ia], "a": ia}).sort_values(["i", "area", "a"])
    m = m.drop_duplicates("i")
    return pd.Series(fa.attrs["facility_type"].values[m["a"]], index=m["i"].values)


def nearest_dist(fp: np.ndarray, lines: Layer) -> np.ndarray:
    if not len(lines.geoms):
        return np.full(len(fp), np.nan)
    (ib, _), d = lines.tree.query_nearest(fp, return_distance=True, all_matches=False)
    out = np.full(len(fp), np.nan)
    out[ib] = d
    return out


def poi_counts(fp: np.ndarray, pois: Layer) -> dict[str, np.ndarray]:
    ib, ip = pois.tree.query(fp, predicate="dwithin", distance=max(RADII))
    d = shapely.distance(fp[ib], pois.geoms[ip])
    return {f"poi_count_{r}m": np.bincount(ib[d <= r], minlength=len(fp)).astype("int64") for r in RADII}


def chunk_context(b: gpd.GeoDataFrame, L: Layers) -> tuple[pd.DataFrame, pd.DataFrame]:
    fp = _utm(b)
    cen = shapely.centroid(fp)
    ids = b["bldg_id"].values
    df = pd.DataFrame({"bldg_id": ids})
    df[ADM] = admin_names(cen, L.villages).values
    df = df.join(polygon_match(fp, L.osm_buildings))
    df["facility_area"] = facility_area(cen, L.fac_areas)
    df["dist_road_major_m"] = nearest_dist(fp, L.roads_major)
    df["dist_road_any_m"] = nearest_dist(fp, L.roads_any)
    df["dist_powerline_m"] = nearest_dist(fp, L.power)
    for k, v in poi_counts(fp, L.osm_pois).items():
        df[k] = v
    df["h3_r7"] = [h3.cell_to_parent(c, 7) for c in b["h3_r9"]]
    cand = pd.concat([candidates(fp, ids, L.osm_nodes, "osm"), candidates(fp, ids, L.overture, "overture"),
                      candidates(fp, ids, L.fac_points, "facility")], ignore_index=True)
    return df, cand


# --------------------------------------------------------------------------- after all chunks
def assign(cand: pd.DataFrame, kind: str) -> pd.DataFrame:
    """POI -> its single nearest building; then building -> its nearest assigned POI. Ties: lowest id/index."""
    c = cand[cand["kind"] == kind].sort_values(["poi", "dist", "bldg_id"]).drop_duplicates("poi")
    return c.sort_values(["bldg_id", "dist", "poi"]).drop_duplicates("bldg_id").set_index("bldg_id")["poi"]


def finalize(df: pd.DataFrame, cand: pd.DataFrame, L: Layers) -> pd.DataFrame:
    df = df.set_index("bldg_id")
    node, ov, fpt = (layer.attrs.loc[a.values].set_axis(a.index) for layer, a in [
        (L.osm_nodes, assign(cand, "osm")), (L.overture, assign(cand, "overture")),
        (L.fac_points, assign(cand, "facility"))])

    poly = df["poly_building"].notna()
    has_node = df.index.isin(node.index)
    df["osm_match"] = np.select([poly, has_node], ["polygon", "point"], "none")
    for t in ["building", "amenity", "shop"]:
        df[f"osm_{t}"] = df[f"poly_{t}"].combine_first(node[t].reindex(df.index))
    df["overture_category"] = ov["category"].reindex(df.index)
    df["overture_confidence"] = ov["confidence"].reindex(df.index).astype(float)
    df["facility_type"] = fpt["facility_type"].reindex(df.index).combine_first(df["facility_area"])
    out = df.reset_index()[CONTEXT_VECTOR_COLS]
    obj = [c for c in out.columns if out[c].dtype == object or str(out[c].dtype).startswith("str")]
    out[obj] = out[obj].astype(object).where(out[obj].notna(), None)
    return out


def join(chunks: Iterator[gpd.GeoDataFrame], L: Layers) -> pd.DataFrame:
    parts, cands = [], []
    for i, b in enumerate(chunks):
        t0 = time.time()
        df, cand = chunk_context(b, L)
        parts.append(df)
        cands.append(cand)
        print(f"  chunk {i}: {len(b):,} buildings in {time.time() - t0:.0f}s", flush=True)
    return finalize(pd.concat(parts, ignore_index=True), pd.concat(cands, ignore_index=True), L)


def read_chunks(path: Path, size: int = CHUNK) -> Iterator[gpd.GeoDataFrame]:
    f = pq.ParquetFile(path)
    for batch in f.iter_batches(batch_size=size, columns=["bldg_id", "h3_r9", "geometry"]):
        t = batch.to_pandas()
        yield gpd.GeoDataFrame(t[["bldg_id", "h3_r9"]], geometry=shapely.from_wkb(t["geometry"]), crs="EPSG:4326")


# --------------------------------------------------------------------------- coverage audit
# "Informative" = evidence a labeller can use beyond "there is a building here": a polygon match whose only tag is
# building=yes says nothing about use, so it doesn't count. These shares are the weak-label bias audit input.
INFORMATIVE = """(coalesce(osm_building, 'yes') <> 'yes' OR osm_amenity IS NOT NULL OR osm_shop IS NOT NULL
                  OR osm_match = 'point' OR overture_category IS NOT NULL OR facility_type IS NOT NULL)"""
SHARES = f"""count(*) AS n,
    avg((osm_match = 'polygon')::int) AS osm_polygon, avg((osm_match = 'point')::int) AS osm_point,
    avg((coalesce(osm_building, 'yes') <> 'yes' OR osm_amenity IS NOT NULL OR osm_shop IS NOT NULL)::int)
        AS osm_use_tag,
    avg((overture_category IS NOT NULL)::int) AS overture, avg((facility_type IS NOT NULL)::int) AS facility,
    avg({INFORMATIVE}::int) AS any_informative"""


def report(cv: Path = OUT, base: Path = BASE) -> dict:
    import duckdb
    import matplotlib.pyplot as plt

    from rtl.settings import REPORTS_DIR
    con = duckdb.connect()
    con.sql(f"CREATE VIEW cv AS SELECT * FROM read_parquet('{cv}')")
    con.sql(f"CREATE VIEW j AS SELECT cv.*, b.source FROM cv JOIN read_parquet('{base}') b USING (bldg_id)")
    dd = [(d["district"], d["sector"]) for d in aoi()["deep_dives"].values()]
    in_dd = " OR ".join(f"(adm2_name = '{d}' AND adm3_name = '{s}')" for d, s in dd)
    q = lambda sql: con.sql(sql).df()  # noqa: E731
    out = {
        "national": q(f"SELECT {SHARES} FROM cv").round(4).to_dict("records")[0],
        "deep_dives": q(f"SELECT adm2_name, adm3_name, {SHARES} FROM cv WHERE {in_dd} GROUP BY 1, 2").round(4)
        .to_dict("records"),
        "by_footprint_source": q(f"SELECT source, {SHARES} FROM j GROUP BY 1 ORDER BY 1").round(4).to_dict("records"),
        "distance_quantiles_m": {
            c: dict(zip(["p10", "p50", "p90", "p99", "max"], [round(x) for x in con.sql(
                f"SELECT quantile_cont({c}, [.1, .5, .9, .99]) || [max({c})] FROM cv").fetchone()[0]], strict=True))
            for c in ["dist_road_major_m", "dist_road_any_m", "dist_powerline_m"]},
        "poi_count_share_gt0": q("""SELECT avg((poi_count_50m>0)::int) c50, avg((poi_count_100m>0)::int) c100,
            avg((poi_count_250m>0)::int) c250 FROM cv""").round(4).to_dict("records")[0],
        "admin_missing": int(con.sql("SELECT count(*) FROM cv WHERE adm5_name IS NULL").fetchone()[0]),
        "top_overture": q("SELECT overture_category, count(*) n FROM cv WHERE overture_category IS NOT NULL "
                          "GROUP BY 1 ORDER BY 2 DESC LIMIT 10").to_dict("records"),
        "top_facility": q("SELECT facility_type, count(*) n FROM cv WHERE facility_type IS NOT NULL "
                          "GROUP BY 1 ORDER BY 2 DESC LIMIT 12").to_dict("records"),
    }
    by_d = q(f"SELECT adm2_name, {SHARES} FROM cv WHERE adm2_name IS NOT NULL GROUP BY 1 ORDER BY any_informative")
    out["by_district"] = by_d.round(4).to_dict("records")

    # quick-look: one series (share of buildings with informative context), districts sorted
    fig, ax = plt.subplots(figsize=(7, 8))
    ax.barh(by_d["adm2_name"], by_d["any_informative"] * 100, color="#2a78d6", height=0.7)
    for y, v in enumerate(by_d["any_informative"] * 100):
        ax.text(v, y, f" {v:.2f}%", va="center", fontsize=8, color="#52514e")
    ax.set_xlabel("% of buildings with informative OSM / Overture / facility context")
    ax.set_title("Weak-label evidence coverage by district", loc="left", fontsize=11)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="x", color="#e4e3df", linewidth=0.6)
    ax.set_axisbelow(True)
    ax.tick_params(labelsize=8)
    fig.tight_layout()
    (REPORTS_DIR / "figures").mkdir(parents=True, exist_ok=True)
    fig.savefig(REPORTS_DIR / "figures" / "A2v_match_coverage.png", dpi=150)
    (REPORTS_DIR / "status" / "A2v_coverage.json").write_text(json.dumps(out, indent=2, default=float) + "\n")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--buildings", type=Path, default=BASE)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--report", action="store_true", help="only write the coverage audit + figure for --out")
    args = ap.parse_args()
    if args.report:
        print(json.dumps(report(args.out, args.buildings), indent=2, default=float))
        return
    t0 = time.time()
    national = args.buildings == BASE
    with heavy_job("A2v-join") if national else nullcontext():  # fixture runs don't need the machine lock
        L = load_layers()
        print(f"layers loaded in {time.time() - t0:.0f}s", flush=True)
        out = join(read_chunks(args.buildings), L)
    n_in = pq.ParquetFile(args.buildings).metadata.num_rows
    assert len(out) == n_in == out["bldg_id"].nunique(), f"row reconciliation failed: {len(out)} vs {n_in}"
    out.to_parquet(args.out, index=False)
    stats = {"rows": len(out), "seconds": round(time.time() - t0),
             "osm_match": out["osm_match"].value_counts().to_dict(),
             "admin_missing": int(out["adm5_name"].isna().sum())}
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
