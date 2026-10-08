"""QA gate: validate every produced table against its contract and write a markdown report.

    pixi run qa                 # all tables that exist
    pixi run qa buildings_base  # one table

Large tables: pandera validates a random sample; uniqueness, row counts and null rates are checked on the
full table in DuckDB. Exit code 1 if any check fails, so downstream pixi tasks stop.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import geopandas as gpd
import pandas as pd
import pandera.errors as pe

from rtl.schemas import SCHEMAS
from rtl.settings import INTERIM_DIR, PROCESSED_DIR, REPORTS_DIR

SAMPLE_ROWS = 200_000

# table name -> (path, primary key or None)
TABLES: dict[str, tuple[Path, str | None]] = {
    "buildings_base": (INTERIM_DIR / "buildings_base.parquet", "bldg_id"),
    "building_height": (INTERIM_DIR / "building_height.parquet", "bldg_id"),
    "context_vector": (INTERIM_DIR / "context_vector.parquet", "bldg_id"),
    "context_raster": (INTERIM_DIR / "context_raster.parquet", "bldg_id"),
    "building_context": (INTERIM_DIR / "building_context.parquet", "bldg_id"),
    "climate_h3": (INTERIM_DIR / "climate_h3.parquet", "h3_r7"),
    "evidence": (PROCESSED_DIR / "evidence.parquet", "evidence_id"),
    "labels": (PROCESSED_DIR / "labels.parquet", None),
}


def load_sample(con: duckdb.DuckDBPyConnection, path: Path, n_rows: int) -> pd.DataFrame:
    if n_rows <= SAMPLE_ROWS:
        return gpd.read_parquet(path) if _is_geo(con, path) else pd.read_parquet(path)
    if _is_geo(con, path):
        # Random sample via DuckDB, written to a temp GeoParquet so geometry and CRS round-trip exactly.
        tmp = path.with_suffix(".qa_sample.parquet")
        con.sql(f"COPY (SELECT * FROM read_parquet('{path}') USING SAMPLE {SAMPLE_ROWS} ROWS (reservoir, 42)) "
                f"TO '{tmp}' (FORMAT parquet)")
        df = gpd.read_parquet(tmp)
        tmp.unlink()
        return df
    return con.sql(f"SELECT * FROM read_parquet('{path}') USING SAMPLE {SAMPLE_ROWS} ROWS (reservoir, 42)").df()


def _is_geo(con: duckdb.DuckDBPyConnection, path: Path) -> bool:
    cols = [r[0] for r in con.sql(f"DESCRIBE SELECT * FROM read_parquet('{path}')").fetchall()]
    return "geometry" in cols


def check_table(con: duckdb.DuckDBPyConnection, name: str) -> tuple[bool, str]:
    path, key = TABLES[name]
    lines = [f"# QA: {name}", "", f"- file: `{path.name}`", f"- checked: {datetime.now(UTC):%Y-%m-%d %H:%M} UTC"]
    ok = True
    n = con.sql(f"SELECT count(*) FROM read_parquet('{path}')").fetchone()[0]
    lines.append(f"- rows: {n:,}")

    if key:
        dup = con.sql(f"SELECT count(*) - count(DISTINCT {key}) FROM read_parquet('{path}')").fetchone()[0]
        lines.append(f"- duplicate `{key}`: {dup}")
        ok &= dup == 0

    sample = load_sample(con, path, n)
    scope = "all rows" if len(sample) == n else f"random sample of {len(sample):,}"
    try:
        SCHEMAS[name].validate(sample, lazy=True)
        lines.append(f"- schema: PASS ({scope})")
    except pe.SchemaErrors as err:
        ok = False
        lines.append(f"- schema: FAIL ({scope})")
        lines += ["", "```", err.failure_cases.head(30).to_string(), "```"]

    cols = [c for c in sample.columns if c != "geometry"]
    nulls = con.sql("SELECT " + ", ".join(f"avg(({c} IS NULL)::int) AS \"{c}\"" for c in cols)
                    + f" FROM read_parquet('{path}')").df().T[0]
    lines += ["", "| column | null share |", "|---|---|"]
    lines += [f"| {c} | {v:.1%} |" for c, v in nulls.items()]

    if name == "buildings_base":
        flags = con.sql(f"""
            SELECT flag, count(*) AS n FROM (SELECT unnest(string_split(qa_flags, ',')) AS flag
                                             FROM read_parquet('{path}') WHERE qa_flags <> '')
            GROUP BY 1 ORDER BY 2 DESC""").fetchall()
        lines += ["", "| qa flag | buildings | share |", "|---|---|---|"]
        lines += [f"| {f} | {c:,} | {c / n:.2%} |" for f, c in flags]

    lines.insert(1, f"**{'PASS' if ok else 'FAIL'}**")
    return ok, "\n".join(lines) + "\n"


def main(names: list[str]) -> int:
    con = duckdb.connect()
    con.sql("LOAD spatial;")
    names = names or [t for t, (p, _) in TABLES.items() if p.exists()]
    out_dir = REPORTS_DIR / "qa"
    out_dir.mkdir(parents=True, exist_ok=True)
    all_ok = True
    for name in names:
        ok, report = check_table(con, name)
        (out_dir / f"{name}.md").write_text(report)
        print(f"{name}: {'PASS' if ok else 'FAIL'} -> reports/qa/{name}.md")
        all_ok &= ok
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
