"""Cut the committed development fixture (~2k buildings in Nyamirambo, Kigali) from buildings_base.

Every workstream develops and tests against this file first, so it is small enough to commit.
"""

from __future__ import annotations

import duckdb

from rtl.conform.buildings import OUT as BASE
from rtl.settings import FIXTURES_DIR, aoi

FIXTURE = FIXTURES_DIR / "buildings_base_fixture.parquet"


def main() -> None:
    x0, y0, x1, y1 = aoi()["fixture"]["bbox"]
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.sql("LOAD spatial;")
    con.sql(f"""
        COPY (SELECT * FROM read_parquet('{BASE}')
              WHERE lon BETWEEN {x0} AND {x1} AND lat BETWEEN {y0} AND {y1}
              ORDER BY bldg_id)
        TO '{FIXTURE}' (FORMAT parquet, COMPRESSION zstd)
    """)
    n = con.sql(f"SELECT count(*) FROM read_parquet('{FIXTURE}')").fetchone()[0]
    print(f"fixture: {n} buildings -> {FIXTURE}")


if __name__ == "__main__":
    main()
