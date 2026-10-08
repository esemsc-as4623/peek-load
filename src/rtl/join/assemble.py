"""Lead: join the two partial context tables into `building_context`.

A2v (vector) and A2r (raster) each write one partial table keyed by bldg_id. This step joins them,
checks both cover exactly the buildings in buildings_base, and validates the result. Keeping the join
in one place means neither workstream can silently drop or duplicate buildings.
"""

from __future__ import annotations

import duckdb

from rtl.conform.buildings import OUT as BASE
from rtl.locks import heavy_job
from rtl.schemas import CONTEXT_RASTER_COLS, CONTEXT_VECTOR_COLS
from rtl.settings import INTERIM_DIR

VECTOR = INTERIM_DIR / "context_vector.parquet"
RASTER = INTERIM_DIR / "context_raster.parquet"
OUT = INTERIM_DIR / "building_context.parquet"


def main() -> None:
    con = duckdb.connect()
    con.sql("SET memory_limit='6GB'")
    n_base = con.sql(f"SELECT count(*) FROM read_parquet('{BASE}')").fetchone()[0]
    for name, path in [("context_vector", VECTOR), ("context_raster", RASTER)]:
        n, n_join = con.sql(f"""
            SELECT count(*), count(b.bldg_id) FROM read_parquet('{path}') p
            LEFT JOIN (SELECT bldg_id FROM read_parquet('{BASE}')) b USING (bldg_id)""").fetchone()
        if not (n == n_join == n_base):
            raise SystemExit(f"{name}: {n:,} rows, {n_join:,} match buildings_base, expected {n_base:,}")

    raster_only = [c for c in CONTEXT_RASTER_COLS if c != "bldg_id"]
    with heavy_job("assemble-context"):
        con.sql(f"""
            COPY (SELECT {", ".join("v." + c for c in CONTEXT_VECTOR_COLS)}, {", ".join("r." + c for c in raster_only)}
                  FROM read_parquet('{VECTOR}') v JOIN read_parquet('{RASTER}') r USING (bldg_id))
            TO '{OUT}' (FORMAT parquet, COMPRESSION zstd)""")
    print(f"building_context: {n_base:,} rows -> {OUT}  (run `pixi run qa building_context`)")


if __name__ == "__main__":
    main()
