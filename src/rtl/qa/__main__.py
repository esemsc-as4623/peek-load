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
import numpy as np
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


def _mapping_ids(rules: dict) -> set[str]:
    return {pid for rs in rules["classes"].values() for r in rs for pid in r["profiles"] if not pid.startswith("@")}


def check_demand(con: duckdb.DuckDBPyConnection) -> tuple[bool, str]:
    """Phase-2 consistency: one set of profile ids across recipes, mapping, archetype JSON, library, weights and
    dashboard config; sane weights and library values; archetype status matching data/gold/approvals.yaml."""
    import json

    import yaml

    from rtl.settings import GOLD_DIR, load_yaml

    arch_dir = PROCESSED_DIR / "archetypes"
    recipes = load_yaml("archetypes.yaml")
    sets = {
        "recipes (config/archetypes.yaml)": set(recipes["household"]["archetypes"]) | set(recipes["establishments"]),
        "archetype JSON": {f.stem for f in arch_dir.glob("*.json") if not f.name.endswith(".schema.json")},
        "mapping (config/profile_mapping.yaml)": _mapping_ids(load_yaml("profile_mapping.yaml")),
        "dashboard (config/dashboard.yaml)": set(load_yaml("dashboard.yaml")["profiles"]),
        "productive use (config/productive_use.yaml)": set(load_yaml("productive_use.yaml")["located"]["rules"])
        | {t for mix in load_yaml("productive_use.yaml")["trade_mix"].values() if isinstance(mix, dict) for t in mix},
        "profile_library": set(pd.read_parquet(PROCESSED_DIR / "profile_library.parquet").profile_id),
        "building_profiles": {r[0] for r in con.sql(
            f"SELECT DISTINCT profile_id FROM read_parquet('{PROCESSED_DIR / 'building_profiles.parquet'}')"
        ).fetchall()},
    }
    ref = sets["archetype JSON"]
    lines = ["# QA: demand", "", f"- checked: {datetime.now(UTC):%Y-%m-%d %H:%M} UTC", f"- archetypes: {len(ref)}", "",
             "| id set | ids | missing vs archetype JSON | extra |", "|---|---|---|---|"]
    ok = True
    for name, ids in sets.items():
        missing, extra = sorted(ref - ids), sorted(ids - ref)
        # the mapping and the weights need not use every archetype, but must not name an unknown one
        partial_ok = name in ("mapping (config/profile_mapping.yaml)", "building_profiles",
                              "productive use (config/productive_use.yaml)")
        bad = bool(extra) or (bool(missing) and not partial_ok)
        ok &= not bad
        lines.append(f"| {name} | {len(ids)} | {', '.join(missing) or '-'} | {', '.join(extra) or '-'} |")
    w = con.sql(f"SELECT count(*) FILTER (WHERE weight IS NULL OR weight < 0 OR isnan(weight)), count(*) "
                f"FROM read_parquet('{PROCESSED_DIR / 'building_profiles.parquet'}')").fetchone()
    lib = pd.read_parquet(PROCESSED_DIR / "profile_library.parquet")
    approved = (yaml.safe_load((GOLD_DIR / "approvals.yaml").read_text()).get("archetypes", {}).get("decision")
                == "approved")
    statuses = {json.loads(f.read_text())["status"] for f in arch_dir.glob("*.json")
                if not f.name.endswith(".schema.json")}
    want = {"human_reviewed"} if approved else {"draft"}
    checks = [("weights non-negative and present", w[0] == 0, f"{w[0]} bad of {w[1]:,}"),
              ("library daily kWh > 0", bool((lib.daily_kwh_mean > 0).all()), f"{len(lib)} profiles"),
              ("archetype status matches approvals", statuses == want, f"{sorted(statuses)} vs {sorted(want)}")]
    # productive use: census establishments are either located, unlocated (spread over areas) or reported as
    # not allocated; the three area levels hold the same total
    dist = pd.read_csv(PROCESSED_DIR / "pu_census_districts.csv")
    areas = pd.read_parquet(PROCESSED_DIR / "pu_unlocated_areas.parquet")
    lv = areas.groupby("level").units.sum()
    unloc = float((dist.unlocated - dist.not_allocated).sum())
    checks.append(("productive use: area levels add up to the allocated census amount",
                   bool(np.allclose(lv.to_numpy(), unloc, rtol=1e-4))
                   and set(lv.index) == {"h3_r7", "sector", "district"},
                   f"{lv.round(0).to_dict()} vs {unloc:,.0f}"))
    gc = PROCESSED_DIR / "grid_connection.parquet"
    if gc.exists():
        g = con.sql(f"SELECT count(*), count(*) FILTER (WHERE p_connected BETWEEN 0 AND 1) FROM '{gc}'").fetchone()
        checks.append(("grid model: one probability in [0, 1] per building", g[0] == g[1] and g[0] > 6_000_000,
                       f"{g[1]:,} of {g[0]:,}"))
    lines += ["", "| check | result | detail |", "|---|---|---|"]
    for label, passed, detail in checks:
        ok &= passed
        lines.append(f"| {label} | {'PASS' if passed else 'FAIL'} | {detail} |")
    lines.insert(1, f"**{'PASS' if ok else 'FAIL'}**")
    return ok, "\n".join(lines) + "\n"


def main(names: list[str]) -> int:
    con = duckdb.connect()
    con.sql("LOAD spatial;")
    names = names or [t for t, (p, _) in TABLES.items() if p.exists()] + (
        ["demand"] if (PROCESSED_DIR / "building_profiles.parquet").exists() else [])
    out_dir = REPORTS_DIR / "qa"
    out_dir.mkdir(parents=True, exist_ok=True)
    all_ok = True
    for name in names:
        ok, report = check_demand(con) if name == "demand" else check_table(con, name)
        (out_dir / f"{name}.md").write_text(report)
        print(f"{name}: {'PASS' if ok else 'FAIL'} -> reports/qa/{name}.md")
        all_ok &= ok
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
