"""Build the final dataset release (config/release.yaml) for Hugging Face.

    pixi run release-build            # -> data/release/<version>/

Layout:
  README.md                      dataset card (YAML metadata for the Hub + documentation)
  data/buildings/<district>.parquet  (30 districts, plus unassigned.parquet: 833 footprints outside the district
                                 boundaries)
                                 GeoParquet, one row per footprint (6.43M): footprint, height, context, use and
                                 allocation, the five challenge metrics (potential and expected), yearly expected
                                 kWh 2016-2023, grid probability, use agreement
  data/building_profiles.parquet bldg_id x profile_id: units, energy weight, REG scale, P(connected)
  data/profiles.parquet          the RAMP profile library: hourly curves (all days, weekday, weekend), daily kWh
                                 p10/mean/p90, coincident peaks, status
  data/districts.parquet, data/sectors.parquet  area totals (GeoParquet)
  data/demand_by_year.parquet    district x year potential / expected GWh, 2016-2030
  data/benchmarks.parquet        the model against REG / RURA / MoH figures
  docs/                          datasheet, data sources, status report, data contract
  CITATION.cff, LICENSE.md, SHA256SUMS

Restricted microdata, DHS cluster locations and Meta RWI values are never included.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from scipy import sparse

from rtl.demand import productive_use as pu
from rtl.settings import INTERIM_DIR, PROCESSED_DIR, REPO_ROOT, REPORTS_DIR, load_yaml

HOURS = [f"w_h{h:02d}" for h in range(24)]
EVENING, DAYTIME, NIGHT = range(18, 24), range(8, 18), range(0, 6)


def curve_metrics(bp: pd.DataFrame, lib: pd.DataFrame) -> pd.DataFrame:
    """Per building: the five metrics' curve-based parts (potential: every unit connected), via one sparse product
    (buildings x profiles) @ (profiles x 24 h). Same formulas as the dashboard (DATA_CONTRACT.md section 5)."""
    pid = {p: i for i, p in enumerate(lib.index)}
    b_ids, b_idx = np.unique(bp.bldg_id.to_numpy(), return_inverse=True)
    w = sparse.csr_matrix((bp.weight.to_numpy(float), (b_idx, bp.profile_id.map(pid).to_numpy())),
                          shape=(len(b_ids), len(pid)))
    curve = np.asarray(w @ lib[HOURS].to_numpy(float), dtype=np.float32)          # W, 24 h
    wd = np.asarray(w @ lib[[f"w_weekday_h{h:02d}" for h in range(24)]].to_numpy(float))
    we = np.asarray(w @ lib[[f"w_weekend_h{h:02d}" for h in range(24)]].to_numpy(float))
    total = curve.sum(1)
    peak = curve.max(1)
    with np.errstate(divide="ignore", invalid="ignore"):
        lf = np.where(peak > 0, curve.mean(1) / peak, np.nan)
        ev = np.where(total > 0, curve[:, list(EVENING)].sum(1) / total, np.nan)
        day = np.where(total > 0, curve[:, list(DAYTIME)].sum(1) / total, np.nan)
        wwr = np.where(we.sum(1) > 0, wd.sum(1) / we.sum(1), np.nan)
    peak_h = curve.argmax(1)
    shape = np.select([(ev >= 0.4) & (peak_h >= 17) & (peak_h <= 22), day >= 0.5, lf >= 0.6],
                      ["evening peak", "daytime", "flat"], "mixed")
    out = pd.DataFrame({
        "bldg_id": b_ids,
        "annual_kwh_p10": np.asarray(w @ lib.daily_kwh_p10.to_numpy(float)).ravel() * 365,
        "annual_kwh_p90": np.asarray(w @ lib.daily_kwh_p90.to_numpy(float)).ravel() * 365,
        "peak_w": peak, "peak_hour": np.where(peak > 0, peak_h, -1).astype("int8"),
        "load_factor": lf.astype("float32"), "evening_share": ev.astype("float32"),
        "daytime_share": day.astype("float32"), "night_base_w": curve[:, list(NIGHT)].mean(1),
        "weekday_weekend_ratio": wwr.astype("float32"), "load_shape": np.where(total > 0, shape, "none")})
    out["peak_hour"] = out.peak_hour.where(out.peak_hour >= 0)
    return out


def pu_viability(con) -> pd.DataFrame:
    """Productive-use viability score (0-100) and category for eligible buildings (rural: mill weights; urban:
    town weights), as on the dashboard. RWI enters the ability-to-pay criterion but is not released."""
    pu_cfg, dash = load_yaml("productive_use.yaml"), load_yaml("dashboard.yaml")
    df = con.sql(f"""SELECT b.bldg_id, b.area_m2, x.poi_count_250m, x.dist_road_major_m, x.pop_density_per_km2,
                            x.dist_powerline_m, x.rwi, c.cropland_share, u.setting,
                            {pu.eligible_sql(pu_cfg, s="b", label="NULL")} AS pu_eligible
                     FROM '{INTERIM_DIR}/buildings_base.parquet' b
                     JOIN '{INTERIM_DIR}/building_context.parquet' x USING (bldg_id)
                     JOIN '{PROCESSED_DIR}/building_use.parquet' u USING (bldg_id)
                     LEFT JOIN '{INTERIM_DIR}/cropland_h3r8.parquet' c ON c.h3_r8 = h3_cell_to_parent(b.h3_r9, 8)
                     """).df()
    c = pu.criteria(df)
    score = pd.Series(np.nan, index=df.index)
    for setting, group in (("urban", "town"), ("rural", "grain_mill")):
        m = df.setting == setting
        score[m] = pu.candidate_score(c[m], pu_cfg["candidate_score"][group])
    score[~df.pu_eligible.astype(bool)] = np.nan
    cats = dash["productive_use"]["categories"]
    cat = pd.cut(score, bins=[cats["low"][0], cats["medium"][0], cats["high"][0], 100.0001],
                 labels=["low", "medium", "high"], right=False, include_lowest=True).astype(object)
    return pd.DataFrame({"bldg_id": df.bldg_id, "pu_viability_score": score.round(1).astype("float32"),
                         "pu_viability": cat.where(score.notna(), None)})


def write_geo(t, path: Path) -> None:
    """Write an Arrow table with a WKB `geometry` column as GeoParquet 1.1 (CRS: OGC:CRS84, lon/lat on WGS84)."""
    from pyproj import CRS

    geo = json.dumps({"version": "1.1.0", "primary_column": "geometry", "columns": {"geometry": {
        "encoding": "WKB", "geometry_types": ["Polygon", "MultiPolygon"], "crs": CRS("OGC:CRS84").to_json_dict()}}})
    t = t.replace_schema_metadata({**(t.schema.metadata or {}), b"geo": geo.encode()})
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(t, path, compression="zstd")


def buildings(con, out: Path, cfg: dict, lib: pd.DataFrame) -> int:
    P, I = PROCESSED_DIR, INTERIM_DIR  # noqa: E741, N806
    bp = pd.read_parquet(P / "building_profiles.parquet")
    con.register("curve", curve_metrics(bp, lib))
    con.register("puv", pu_viability(con))
    years = [c for c in con.sql(f"SELECT * FROM '{P}/building_demand_by_year.parquet' LIMIT 0").columns
             if c.startswith("expected_kwh_")]
    ycols = ", ".join(f"y.{c}::FLOAT AS {c}" for c in years) if cfg["yearly_columns"] else ""
    con.sql(f"""CREATE OR REPLACE TEMP TABLE rel AS SELECT
            b.bldg_id, x.adm1_name AS province, x.adm2_name AS district, x.adm3_name AS sector, x.adm4_name AS cell,
            x.adm5_name AS village, b.lon, b.lat, b.h3_r9, x.h3_r7,
            b.source AS footprint_source, b.source_confidence, b.area_m2::FLOAT AS area_m2, b.qa_flags,
            h.height_m::FLOAT AS height_m, h.est_floors, h.gfa_m2::FLOAT AS gfa_m2, h.first_seen_year,
            x.facility_type, x.dist_road_any_m::FLOAT AS dist_road_any_m,
            x.dist_powerline_m::FLOAT AS dist_mv_line_m, x.pop_density_per_km2::FLOAT AS pop_density_per_km2,
            x.ghsl_class, x.elevation_m::FLOAT AS elevation_m,
            u.setting, u.use, u.source AS use_source, u.establishment, u.est_weight::FLOAT AS establishment_share,
            u.households, CASE WHEN u.households > 0 THEN u.upper END AS upper_wealth,
            a.use_agreement,
            p.annual_kwh::FLOAT AS annual_kwh_potential, p.expected_annual_kwh::FLOAT AS annual_kwh_expected,
            cv.annual_kwh_p10::FLOAT AS annual_kwh_p10, cv.annual_kwh_p90::FLOAT AS annual_kwh_p90,
            cv.peak_w::FLOAT AS peak_w, cv.peak_hour, cv.load_factor, cv.evening_share, cv.daytime_share,
            cv.night_base_w::FLOAT AS night_base_w, cv.weekday_weekend_ratio, cv.load_shape,
            k.cooling_class, k.tmax_p95_c::FLOAT AS tmax_p95_c, k.cdd22_per_year::FLOAT AS cdd22_per_year,
            puv.pu_viability_score, puv.pu_viability,
            g.p_connected::FLOAT AS p_connected_model, gh.p_grid_households::FLOAT AS p_connected_calibrated
            {', ' + ycols if ycols else ''},
            ST_AsWKB(b.geometry) AS geometry
        FROM '{I}/buildings_base.parquet' b
        JOIN '{I}/building_context.parquet' x USING (bldg_id)
        LEFT JOIN '{I}/building_height.parquet' h USING (bldg_id)
        LEFT JOIN '{I}/climate_h3.parquet' k ON k.h3_r7 = x.h3_r7
        LEFT JOIN '{P}/building_use.parquet' u USING (bldg_id)
        LEFT JOIN '{P}/uncertainty/building_agreement.parquet' a USING (bldg_id)
        LEFT JOIN '{P}/building_demand_preview.parquet' p USING (bldg_id)
        LEFT JOIN curve cv USING (bldg_id)
        LEFT JOIN puv USING (bldg_id)
        LEFT JOIN '{P}/grid_connection.parquet' g USING (bldg_id)
        LEFT JOIN (SELECT bldg_id, sum(units * p_grid) / sum(units) AS p_grid_households
                   FROM '{P}/building_profiles.parquet' WHERE profile_id LIKE 'household_%'
                   GROUP BY 1) gh USING (bldg_id)
        LEFT JOIN '{P}/building_demand_by_year.parquet' y USING (bldg_id)""")
    n = con.sql("SELECT count(*) FROM rel").fetchone()[0]
    for (d,) in con.sql("SELECT DISTINCT coalesce(district, 'unassigned') FROM rel ORDER BY 1").fetchall():
        t = con.execute("SELECT * FROM rel WHERE coalesce(district, 'unassigned') = ? ORDER BY h3_r9",
                        [d]).to_arrow_table()
        write_geo(t, out / "data" / "buildings" / f"{d.lower().replace(' ', '_')}.parquet")
    return n


def tables(con, out: Path, lib: pd.DataFrame) -> None:
    P, I = PROCESSED_DIR, INTERIM_DIR  # noqa: E741, N806
    d = out / "data"
    con.sql(f"COPY (SELECT bldg_id, profile_id, units::FLOAT AS units, weight::FLOAT AS weight, scale::FLOAT AS scale,"
            f" p_grid::FLOAT AS p_grid FROM '{P}/building_profiles.parquet') TO '{d}/building_profiles.parquet' "
            "(FORMAT parquet, COMPRESSION zstd)")
    lib.reset_index().to_parquet(d / "profiles.parquet", index=False)
    for name, src in (("demand_by_year", P / "demand_by_year.csv"), ("benchmarks", P / "benchmarks.csv")):
        pd.read_csv(src).to_parquet(d / f"{name}.parquet", index=False)
    for level, cols in (("districts", ("province", "district")), ("sectors", ("province", "district", "sector"))):
        adm = dict(zip(("province", "district", "sector"), ("adm1_name", "adm2_name", "adm3_name"), strict=True))
        keys = ", ".join(f"{adm[c]} AS {c}" for c in cols)
        names = ", ".join(cols)
        t = con.sql(f"""
            WITH g AS (SELECT {keys}, ST_AsWKB(ST_Union_Agg(geometry)) AS geometry
                       FROM '{I}/layers/admin_villages.parquet' GROUP BY ALL),
                 a AS (SELECT {names}, count(*) AS buildings, sum(households) AS households,
                              sum(annual_kwh_potential) / 1e6 AS potential_gwh,
                              sum(annual_kwh_expected) / 1e6 AS expected_gwh,
                              sum(establishment_share) FILTER (WHERE use = 'productive_use') AS productive_use
                       FROM rel GROUP BY ALL)
            SELECT {names}, a.* EXCLUDE ({names}), g.geometry FROM g LEFT JOIN a USING ({names})
            ORDER BY {names}""").to_arrow_table()
        write_geo(t, d / f"{level}.parquet")


def docs(out: Path, cfg: dict, n: int, pot: float, exp: float) -> None:
    (out / "docs").mkdir(exist_ok=True)
    for f in ("docs/datasheet.md", "docs/data_sources.md", "status_report.md", "dashboard/DATA_CONTRACT.md",
              "docs/codebook.md", "docs/ai_use.md"):
        shutil.copy2(REPO_ROOT / f, out / "docs" / Path(f).name)
    shutil.copy2(REPORTS_DIR / "demand" / "benchmarks.md", out / "docs" / "benchmarks.md")
    card = (REPO_ROOT / "src" / "rtl" / "release" / "dataset_card.md").read_text()
    nat = pd.read_parquet(out / "data" / "demand_by_year.parquet").groupby("year")[["potential_gwh",
                                                                                     "expected_gwh"]].sum()
    card = card.format(version=cfg["version"], title=cfg["title"], pretty_name=cfg["pretty_name"], n_buildings=n,
                       potential=pot, expected=exp,
                       exp2016=nat.at[2016, "expected_gwh"], exp2030=nat.at[2030, "expected_gwh"],
                       licence_notes=" ".join(cfg["licence"]["notes"].split()))
    (out / "README.md").write_text(card)
    (out / "CITATION.cff").write_text(
        "cff-version: 1.2.0\nmessage: If you use this dataset, please cite it and its underlying sources.\n"
        f"title: \"{cfg['title']}\"\nversion: {cfg['version']}\ntype: dataset\n"
        "authors:\n  - name: \"[TEAM NAME]\"\n"
        "repository-code: https://github.com/esemsc-as4623/peek-load\nlicense: ODbL-1.0\n")
    (out / "LICENSE.md").write_text(
        "# Licence\n\nThis database is made available under the Open Database License (ODbL-1.0): "
        "https://opendatacommons.org/licenses/odbl/1-0/. Any rights in individual contents of the database are "
        "licensed under the Database Contents License: https://opendatacommons.org/licenses/dbcl/1-0/.\n\n"
        + " ".join(cfg["licence"]["notes"].split()) + "\n")


def checksums(out: Path) -> None:
    lines = []
    for f in sorted(p for p in out.rglob("*") if p.is_file() and p.name != "SHA256SUMS"):
        h = hashlib.sha256()
        with open(f, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
        lines.append(f"{h.hexdigest()}  {f.relative_to(out)}")
    (out / "SHA256SUMS").write_text("\n".join(lines) + "\n")


def main() -> None:
    cfg = load_yaml("release.yaml")
    out = REPO_ROOT / cfg["out_dir"] / cfg["version"]
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    con = duckdb.connect()
    con.sql("INSTALL h3 FROM community; LOAD h3; INSTALL spatial; LOAD spatial; SET memory_limit='8GB'")
    lib = pd.read_parquet(PROCESSED_DIR / "profile_library.parquet").set_index("profile_id")
    n = buildings(con, out, cfg, lib)
    tables(con, out, lib)
    cols = con.sql("SELECT * FROM rel LIMIT 0").columns
    leaked = [c for c in cfg["licence"]["excluded_columns"] if c in cols]
    if leaked:
        raise ValueError(f"excluded columns in the release: {leaked}")
    pot, exp = con.sql("SELECT sum(annual_kwh_potential) / 1e6, sum(annual_kwh_expected) / 1e6 FROM rel").fetchone()
    docs(out, cfg, n, pot, exp)
    checksums(out)
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file()) / 1e6
    print(f"{n:,} buildings, {len(list(out.rglob('*.parquet')))} parquet files, {size:,.0f} MB -> {out}")


if __name__ == "__main__":
    main()
