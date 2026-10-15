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


def allocation_checks(con: duckdb.DuckDBPyConnection) -> list[tuple[str, bool, str]]:
    """Census-constrained allocation (rtl.demand.allocate) and the use classifier (rtl.demand.use_model)."""
    from rtl.demand.allocate import TARGETS_PATH, USE_PATH

    P, I = PROCESSED_DIR, INTERIM_DIR  # noqa: E741, N806
    out = []
    t = pd.read_csv(TARGETS_PATH)
    est = t[~t.item.str.startswith("households")]
    met = est.tagged.clip(upper=est.target) + est.assigned + est.shortfall
    out.append(("allocation: every district's establishments = census target (tagged + assigned + shortfall)",
                bool(((met == est.target) | (est.tagged > est.target)).all()),
                f"{len(est)} district x item rows; shortfall {int(est.shortfall.sum()):,}; tags above target in "
                f"{int((est.tagged > est.target).sum())} rows"))
    for item in ("households", "households_urban", "households_rural"):
        hh = t[t.item == item]
        out.append((f"allocation: {item.replace('_', ' ')} per district = 2022 census (or shortfall reported)",
                    bool(len(hh) == 30 and (hh.assigned + hh.shortfall == hh.target).all()),
                    f"{int(hh.assigned.sum()):,} of {int(hh.target.sum()):,}; shortfall {int(hh.shortfall.sum()):,}"))
    n_use, n_ids, n_bldg = con.sql(f"SELECT count(*), count(DISTINCT bldg_id), (SELECT count(*) FROM "
                                   f"'{I}/building_context.parquet' WHERE adm2_name IS NOT NULL) "
                                   f"FROM '{USE_PATH}'").fetchone()
    out.append(("allocation: one use per building, every building with a district",
                n_use == n_ids == n_bldg, f"{n_use:,} rows, {n_ids:,} ids, {n_bldg:,} buildings"))
    bad_w = con.sql(f"""WITH w AS (SELECT bldg_id, sum(units) w FROM '{P}/building_profiles.parquet' GROUP BY 1)
        SELECT count(*) FROM '{USE_PATH}' u FULL JOIN w USING (bldg_id)
        WHERE abs(coalesce(w.w, 0) - (coalesce(u.households, 0) + coalesce(u.est_weight, 0))) > 1e-9"""
                   ).fetchone()[0]
    out.append(("allocation: profile units = households + the building's share of its establishment", bad_w == 0,
                f"{bad_w} mismatches"))
    comp = con.sql(f"""SELECT count(*) FROM (SELECT establishment, sum(est_weight) s, count(*) n FROM '{USE_PATH}'
                       WHERE establishment IS NOT NULL GROUP BY 1) WHERE abs(s - round(s)) > 1e-6""").fetchone()[0]
    out.append(("allocation: facility compounds add up to whole facilities (shares sum to integers per kind)",
                comp == 0, f"{comp} kinds with a fractional total"))
    wt = con.sql(f"""SELECT setting, sum(households * upper::INT) / sum(households) FROM '{USE_PATH}'
                     WHERE households > 0 GROUP BY 1""").fetchall()
    dhs = pd.read_csv(P / "dhs_wealth_district.csv")
    hhw = con.sql(f"SELECT district, setting, sum(households) h FROM '{USE_PATH}' GROUP BY ALL").df()
    tgt_w = dhs.merge(hhw, on=["district", "setting"])
    want = {s: float(np.average(g.target_upper_share, weights=g.h)) for s, g in tgt_w.groupby("setting")}
    out.append(("allocation: upper-wealth household share per setting matches the DHS-based targets (+-0.02)",
                all(abs(v - want[s]) <= 0.02 for s, v in wt),
                "; ".join(f"{s} {v:.3f} vs {want[s]:.3f}" for s, v in sorted(wt))))
    cal = P / "calibration.csv"
    if cal.exists():
        c = pd.read_csv(cal)
        bp = con.sql(f"""SELECT u.district, sum(w.units * w.p_grid) / sum(w.units) AS p
                         FROM '{P}/building_profiles.parquet' w JOIN '{USE_PATH}' u USING (bldg_id)
                         WHERE w.profile_id LIKE 'household_%' GROUP BY 1""").df().set_index("district").p
        d = c[c.level == "district"].set_index("key").target
        gap = float((bp.reindex(d.index) - d).abs().max())
        out.append(("calibration: households' mean P(connected) = EICV7 2024 grid share in every district (+-0.005)",
                    gap <= 0.005, f"largest gap {gap:.4f}"))
    fid = con.sql(f"""SELECT count(*) FILTER (WHERE p.tag_label IN ('institutional', 'religious',
                             'industrial_warehouse', 'ancillary') AND u.use <> p.tag_label
                             AND NOT (x.facility_type = 'health:pharmacy' AND u.establishment = 'kiosk_shop')),
                             count(*) FILTER (WHERE p.tag_label IS NOT NULL)
                      FROM '{USE_PATH}' u JOIN '{P}/use_probs.parquet' p USING (bldg_id)
                      JOIN '{I}/building_context.parquet' x USING (bldg_id)""").fetchone()
    out.append(("allocation: tagged institutions, worship, stores and ancillary keep their tag (pharmacies are shops)",
                fid[0] == 0, f"{fid[0]} changed of {fid[1]:,} tagged buildings"))
    loaded = con.sql(f"SELECT avg((households > 0 OR establishment IS NOT NULL)::INT) FROM '{USE_PATH}'").fetchone()[0]
    out.append(("allocation: share of buildings carrying demand is plausible (40-75%)", 0.40 <= loaded <= 0.75,
                f"{loaded:.1%} (census: 0.52 households per building)"))
    from rtl.settings import load_yaml

    con.sql("INSTALL h3 FROM community; LOAD h3")
    sp = load_yaml("allocation.yaml")["spacing"]
    over, tot = 0, 0
    for prof, c in sp.items():
        cell = "h3_cell_to_parent(b.h3_r9, 8)" if c["cell"] == "h3_r8" else "b.h3_r9"
        o, t2 = con.sql(f"""WITH k AS (SELECT {cell} AS cell, count(*) n FROM '{USE_PATH}' u
                JOIN '{I}/buildings_base.parquet' b USING (bldg_id)
                WHERE u.establishment = '{prof}' AND u.source IN ('census', 'registry') GROUP BY 1)
                SELECT coalesce(sum(greatest(n - {c["cap"]}, 0)), 0), coalesce(sum(n), 0) FROM k""").fetchone()
        over, tot = over + o, tot + t2
    out.append(("allocation: establishments respect the spacing caps (relaxed caps used for <= 10%)",
                tot > 0 and over / tot <= 0.10, f"{int(over):,} of {int(tot):,} census-placed establishments sit above "
                f"their cell's base cap ({over / max(tot, 1):.1%})"))
    sc = pd.read_csv(P / "use_model" / "scores.csv")
    b = sc[(sc.model == "B tags+llm")].set_index("test")
    auc_tags = b.at["tags (held-out districts)", "macro_auc"]
    auc_anc = b.at["LLM labels (held-out sector)", "auc_ancillary"]
    out.append(("use classifier B ranks held-out uses (macro AUC >= 0.70 on tags; ancillary AUC >= 0.70 by sector)",
                bool(auc_tags >= 0.70 and auc_anc >= 0.70), f"{auc_tags:.3f}; {auc_anc:.3f}"))
    return out


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
        "services (config/services.yaml)": set(load_yaml("services.yaml")["located"]["rules"]),
        "productive use (config/productive_use.yaml)": set(load_yaml("productive_use.yaml")["located"]["rules"])
        | {t for mix in load_yaml("productive_use.yaml")["trade_mix"].values() if isinstance(mix, dict) for t in mix},
        "profile_library": set(pd.read_parquet(PROCESSED_DIR / "profile_library.parquet").profile_id),
        "building_profiles": {r[0] for r in con.sql(
            f"SELECT DISTINCT profile_id FROM read_parquet('{PROCESSED_DIR / 'building_profiles.parquet'}')"
        ).fetchall()},
    }
    derived = set(recipes.get("derived", {}))  # scaled copies of built profiles (health centre, hospital)
    sets["recipes (config/archetypes.yaml)"] |= derived
    sets["archetype JSON (+ derived copies)"] = sets.pop("archetype JSON") | derived
    ref = sets["archetype JSON (+ derived copies)"]
    lines = ["# QA: demand", "", f"- checked: {datetime.now(UTC):%Y-%m-%d %H:%M} UTC", f"- archetypes: {len(ref)}", "",
             "| id set | ids | missing vs archetype JSON | extra |", "|---|---|---|---|"]
    ok = True
    for name, ids in sets.items():
        missing, extra = sorted(ref - ids), sorted(ids - ref)
        # the mapping and the weights need not use every archetype, but must not name an unknown one
        partial_ok = name in ("mapping (config/profile_mapping.yaml)", "building_profiles",
                              "productive use (config/productive_use.yaml)", "services (config/services.yaml)")
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
    checks += allocation_checks(con)
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
