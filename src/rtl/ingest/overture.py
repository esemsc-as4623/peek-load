"""A2v: Overture Maps places (POIs) for Rwanda, read straight from the public S3 bucket with DuckDB.

Only row groups whose bbox overlaps Rwanda's envelope are fetched (Overture files carry a `bbox` struct for exactly
this). The raw extract is kept under data/raw/overture_places/<release>/ and recorded in the manifest with the
release id; the layer keeps the fields the join and context cards need.

Category: since 2026 releases Overture replaced `categories` with `taxonomy` (primary + hierarchy) and a coarse
`basic_category`; `taxonomy.primary` is the most specific label and becomes `overture_category`.
"""

from __future__ import annotations

import duckdb

from rtl.manifest import record
from rtl.settings import INTERIM_DIR, RAW_DIR, aoi, sources

LAYERS = INTERIM_DIR / "layers"
OUT = LAYERS / "overture_places.parquet"


def extract(release: str) -> None:
    src = sources()["overture_places"]
    url = src["url"].format(release=release)
    x0, y0, x1, y1 = aoi()["country"]["bbox"]
    raw = RAW_DIR / "overture_places" / release / "places_rwa_bbox.parquet"
    raw.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.sql("INSTALL httpfs; LOAD httpfs; LOAD spatial; SET s3_region='us-west-2'; SET threads=2;")
    con.sql(f"""
        COPY (SELECT * FROM read_parquet('{url}', hive_partitioning=1)
              WHERE bbox.xmin <= {x1} AND bbox.xmax >= {x0} AND bbox.ymin <= {y1} AND bbox.ymax >= {y0})
        TO '{raw}' (FORMAT parquet, COMPRESSION zstd)
    """)
    record("overture_places", raw, url, {"version": release, "release": release, "bbox": [x0, y0, x1, y1]})

    LAYERS.mkdir(parents=True, exist_ok=True)
    con.sql(f"""
        COPY (SELECT id AS overture_id,
                     names.primary AS name,
                     taxonomy.primary AS category,
                     basic_category,
                     array_to_string(taxonomy.hierarchy, '>') AS category_hierarchy,
                     confidence,
                     operating_status,
                     sources[1].dataset AS source_dataset,
                     geometry
              FROM read_parquet('{raw}'))
        TO '{OUT}' (FORMAT parquet, COMPRESSION zstd)
    """)
    n, ncat = con.sql(f"SELECT count(*), count(category) FROM read_parquet('{OUT}')").fetchone()
    print(f"overture_places {release}: {n:,} places ({ncat:,} with category) -> {OUT}")


if __name__ == "__main__":
    extract(sources()["overture_places"]["release"])
