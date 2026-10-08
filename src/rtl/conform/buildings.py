"""A1: conform raw VIDA footprints into the `buildings_base` contract.

Steps (all in DuckDB, which spills to disk instead of exhausting RAM on 6.4M polygons):
  1. read the raw GeoParquet, make geometries valid, compute metrics in UTM 35S
  2. stable id = sha256(source + exact geometry), drop exact duplicates (logged)
  3. flag footprints that overlap a footprint from a *different* source with IoU > 0.5, and the smaller of two
     same-source footprints when more than half of it lies inside the other (nested duplicate detections;
     adjacent buildings that merely share a wall overlap by < 1 m2 and are not flagged)
  4. write data/interim/buildings_base.parquet (GeoParquet, EPSG:4326)

Nothing is dropped except exact duplicates: tiny / low-confidence / overlapping footprints are *flagged*,
so downstream steps decide what to exclude and every exclusion is visible.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import duckdb

from rtl.locks import heavy_job
from rtl.manifest import read_manifest
from rtl.settings import INTERIM_DIR, REPORTS_DIR, aoi

TINY_M2 = 6.0  # below this a footprint is almost certainly a sliver or latrine-sized artefact
LOW_CONF = 0.70  # Google V3 confidence; the dataset's own floor is 0.65
OVERLAP_IOU = 0.5
NESTED_FRAC = 0.5  # share of the smaller footprint covered by a same-source neighbour

OUT = INTERIM_DIR / "buildings_base.parquet"


def raw_vida_path() -> str:
    entries = [e for e in read_manifest().values() if e["source_id"] == "vida_buildings"]
    if not entries:
        raise FileNotFoundError("vida_buildings not in manifest: run `pixi run fetch-vida`")
    return max(entries, key=lambda e: e["version"])["path"]


def connect() -> duckdb.DuckDBPyConnection:
    tmp = INTERIM_DIR / "duckdb_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.sql(f"SET memory_limit='9GB'; SET temp_directory='{tmp}'; SET preserve_insertion_order=false;")
    con.sql("LOAD spatial; INSTALL h3 FROM community; LOAD h3;")
    return con


def build(con: duckdb.DuckDBPyConnection, raw_path: str, out: Path = OUT,
          bbox: list[float] | None = None) -> dict:
    """Build buildings_base. `bbox` (min_lon, min_lat, max_lon, max_lat) restricts to a sub-area for dev runs."""
    crs = aoi()["country"]["metric_crs"]
    stats: dict = {"raw_path": raw_path, "bbox": bbox}
    where = ""
    if bbox:
        x0, y0, x1, y1 = bbox
        where = (f"WHERE bbox.xmin >= {x0} AND bbox.xmax <= {x1} "
                 f"AND bbox.ymin >= {y0} AND bbox.ymax <= {y1}")

    # 1. metrics --------------------------------------------------------------------------------
    con.sql(f"""
        CREATE OR REPLACE TABLE b AS
        WITH src AS (
            SELECT bf_source AS source,
                   CASE WHEN confidence >= 0 THEN confidence END AS source_confidence,
                   CASE WHEN ST_IsValid(geometry) THEN geometry ELSE ST_MakeValid(geometry) END AS geom,
                   NOT ST_IsValid(geometry) AS was_invalid
            FROM read_parquet('{raw_path}')
            {where}
        ),
        m AS (
            SELECT *, ST_Transform(geom, 'EPSG:4326', '{crs}', always_xy := true) AS g_utm FROM src
        ),
        r AS (
            SELECT *, ST_ExteriorRing(ST_MinimumRotatedRectangle(g_utm)) AS ring FROM m
        )
        SELECT
            'RWA-' || left(sha256(source || ':' || ST_AsHEXWKB(geom)), 16)       AS bldg_id,
            source,
            source_confidence,
            ST_Area(g_utm)                                                         AS area_m2,
            ST_Perimeter(g_utm)                                                    AS perimeter_m,
            4 * pi() * ST_Area(g_utm) / pow(ST_Perimeter(g_utm), 2)                AS compactness,
            -- bearing of the long side of the minimum rotated rectangle, folded to [0, 180)
            CASE WHEN ring IS NOT NULL THEN
                degrees(CASE WHEN ST_Distance(ST_PointN(ring, 1), ST_PointN(ring, 2))
                                  >= ST_Distance(ST_PointN(ring, 2), ST_PointN(ring, 3))
                             THEN ST_Azimuth(ST_PointN(ring, 1), ST_PointN(ring, 2))
                             ELSE ST_Azimuth(ST_PointN(ring, 2), ST_PointN(ring, 3)) END) % 180
            END                                                                    AS orientation_deg,
            ST_NPoints(ST_ExteriorRing(geom)) - 1                                  AS n_vertices,
            ST_X(ST_Centroid(geom))                                                AS lon,
            ST_Y(ST_Centroid(geom))                                                AS lat,
            h3_latlng_to_cell_string(ST_Y(ST_Centroid(geom)), ST_X(ST_Centroid(geom)), 9) AS h3_r9,
            was_invalid,
            geom
        FROM r
    """)
    stats["rows_raw"] = con.sql("SELECT count(*) FROM b").fetchone()[0]

    # 2. exact duplicates (same source + identical geometry -> same id) -------------------------
    dups = con.sql("SELECT count(*) - count(DISTINCT bldg_id) FROM b").fetchone()[0]
    if dups:
        con.sql("CREATE OR REPLACE TABLE b AS SELECT * FROM b QUALIFY row_number() OVER (PARTITION BY bldg_id) = 1")
    stats["exact_duplicates_dropped"] = dups

    # 3. overlaps: ONE spatial join over every intersecting pair (each unordered pair once), then derive
    #    both flags from it. Note: putting `a.source = c.source` in the join condition makes DuckDB choose a
    #    hash join on source (quadratic within Google's 5.8M footprints); an inequality keeps the R-tree
    #    SPATIAL_JOIN. IoU and coverage are area ratios, so computing them in degrees is fine at this scale.
    con.sql("""
        CREATE OR REPLACE TABLE pairs AS
        SELECT a.bldg_id AS id_a, c.bldg_id AS id_b, a.source AS src_a, c.source AS src_b,
               ST_Area(a.geom) AS area_a, ST_Area(c.geom) AS area_b,
               ST_Area(ST_Intersection(a.geom, c.geom)) AS inter
        FROM b AS a JOIN b AS c
          ON ST_Intersects(a.geom, c.geom) AND a.bldg_id < c.bldg_id
    """)
    con.sql("""
        CREATE OR REPLACE TABLE xsrc_pairs AS
        SELECT id_a, id_b, least(src_a, src_b) AS src_a, greatest(src_a, src_b) AS src_b,
               inter / (area_a + area_b - inter) AS iou
        FROM pairs WHERE src_a <> src_b
    """)
    con.sql(f"""
        CREATE OR REPLACE TABLE overlap_ids AS
        SELECT id_a AS id FROM xsrc_pairs WHERE iou > {OVERLAP_IOU}
        UNION SELECT id_b FROM xsrc_pairs WHERE iou > {OVERLAP_IOU}
    """)
    con.sql(f"""
        CREATE OR REPLACE TABLE nested_ids AS
        SELECT DISTINCT CASE WHEN area_a <= area_b THEN id_a ELSE id_b END AS id
        FROM pairs
        WHERE src_a = src_b AND inter / least(area_a, area_b) > {NESTED_FRAC}
    """)
    stats["intersecting_pairs_same_source"] = con.sql("SELECT count(*) FROM pairs WHERE src_a = src_b").fetchone()[0]
    stats["nested_same_source"] = con.sql("SELECT count(*) FROM nested_ids").fetchone()[0]
    stats["overlap_pairs_any"] = con.sql("SELECT count(*) FROM xsrc_pairs").fetchone()[0]
    stats["overlap_pairs_iou_gt_0_5"] = con.sql(
        f"SELECT count(*) FROM xsrc_pairs WHERE iou > {OVERLAP_IOU}").fetchone()[0]
    stats["overlap_pairs_by_source"] = {
        f"{a}|{b}": n for a, b, n in con.sql(
            f"SELECT src_a, src_b, count(*) FROM xsrc_pairs WHERE iou > {OVERLAP_IOU} GROUP BY 1, 2").fetchall()
    }

    # 4. flags + write --------------------------------------------------------------------------
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    con.sql(f"""
        COPY (
            SELECT bldg_id, source, source_confidence, area_m2, perimeter_m, compactness, orientation_deg,
                   n_vertices::BIGINT AS n_vertices, lon, lat, h3_r9,
                   concat_ws(',',
                       CASE WHEN was_invalid THEN 'invalid_fixed' END,
                       CASE WHEN source_confidence < {LOW_CONF} THEN 'low_confidence' END,
                       CASE WHEN n.id IS NOT NULL THEN 'nested_same_source' END,
                       CASE WHEN o.id IS NOT NULL THEN 'overlaps_other_source' END,
                       CASE WHEN area_m2 < {TINY_M2} THEN 'tiny' END) AS qa_flags,
                   geom AS geometry
            FROM b LEFT JOIN overlap_ids o ON b.bldg_id = o.id
                   LEFT JOIN nested_ids n ON b.bldg_id = n.id
            ORDER BY h3_r9
        ) TO '{out}' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 100000)
    """)
    stats["rows_out"] = con.sql(f"SELECT count(*) FROM read_parquet('{out}')").fetchone()[0]
    stats["flag_counts"] = dict(con.sql(f"""
        SELECT flag, count(*) FROM (SELECT unnest(string_split(qa_flags, ',')) AS flag
                                    FROM read_parquet('{out}') WHERE qa_flags <> '') GROUP BY 1 ORDER BY 1
    """).fetchall())
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bbox", type=float, nargs=4, metavar=("MINX", "MINY", "MAXX", "MAXY"))
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    t0 = time.time()
    with heavy_job("conform-buildings"):
        stats = build(connect(), raw_vida_path(), out=args.out, bbox=args.bbox)
    stats["seconds"] = round(time.time() - t0, 1)
    assert stats["rows_out"] == stats["rows_raw"] - stats["exact_duplicates_dropped"], "row reconciliation failed"
    if args.out == OUT:  # only the national run writes the QA record
        (REPORTS_DIR / "qa").mkdir(parents=True, exist_ok=True)
        (REPORTS_DIR / "qa" / "conform_buildings.json").write_text(json.dumps(stats, indent=2) + "\n")
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
