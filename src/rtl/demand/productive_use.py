"""B3: productive-use (PU) census targets and tagged buildings, for the census-constrained allocation.

    pixi run pu-demand

Rules and assumptions live in config/productive_use.yaml. Steps:
1. Census: manufacturing establishments per district and setting (NISR Establishment Census 2023 tables), split
   into trades by setting (mills rural, tailors / welders / carpenters in towns).
2. Located: buildings carrying a trade tag (OSM / Overture) get that trade's RAMP profile.
The rest of each district's count is placed on individual buildings by rtl.demand.allocate (best candidates by the
viability criteria below); nothing stays as an area amount.

Outputs (data/processed/):
  pu_located.parquet            bldg_id, profile_id, tag, h3_r7, district, sector, setting
  pu_census_districts.csv       published district table: census count and tagged buildings by trade and setting
"""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from rtl.demand.mapping import LABELER
from rtl.settings import DATA_DIR, INTERIM_DIR, PROCESSED_DIR, load_yaml

RESTRICTED_DIR = DATA_DIR / "restricted"
CRITERIA = ["market_access", "demand_density", "grid_proximity", "ability_to_pay", "agri_context", "building_size"]
TOWN_TRADES = ("tailoring", "welding_workshop", "carpentry")
M_PER_DEG = 111_320.0  # metres per degree near the equator; Rwanda is at 1-3 S


# ------------------------------------------------------------------------------------------------ 1. census
def _rows(xlsx, sheet: str) -> list[list]:
    raw = pd.read_excel(xlsx, sheet_name=sheet, header=None)
    return [[None if (isinstance(v, float) and np.isnan(v)) else v for v in r] for r in raw.itertuples(index=False)]


def census_counts(cfg: dict, activity: str | None = None) -> pd.DataFrame:
    """(district, setting, enterprises, pu_share, pu_census): establishments of one ISIC section (default the
    config's, manufacturing) per district and setting."""
    c = cfg["census"]
    xlsx = RESTRICTED_DIR / c["file"]
    activity = activity or c["activity"]
    act = next(r for r in _rows(xlsx, c["establishments_by_activity"]) if str(r[0]).strip() == activity)
    tot = next(r for r in _rows(xlsx, c["establishments_by_activity"]) if str(r[0]).strip() == "Total")
    share = {"urban": act[4] / tot[4], "rural": act[9] / tot[9]}  # 2023 columns of the urban and rural blocks
    rows = []
    for r in _rows(xlsx, c["enterprises_by_district"]):
        name = str(r[0] or "").strip()
        if not name or not isinstance(r[5], int | float) or "Province" in name or name in ("Kigali City", "Rwanda"):
            continue
        for setting, v in (("urban", r[5]), ("rural", r[6])):
            rows.append({"district": name, "setting": setting, "enterprises": int(v), "pu_share": share[setting],
                         "pu_census": v * share[setting]})
    out = pd.DataFrame(rows)
    if out.district.nunique() != 30:
        raise ValueError(f"expected 30 districts in the census table, got {out.district.nunique()}")
    return out


# ------------------------------------------------------------------------------------------------ buildings
def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.sql("INSTALL h3 FROM community; LOAD h3; LOAD spatial")
    return con


def setting_sql(map_cfg: dict, x: str = "x") -> str:
    """SQL for 'urban' / 'rural' with the mapping's rule (context columns under alias `x`)."""
    u = map_cfg["context"]["urban"]
    return (f"CASE WHEN coalesce({x}.pop_density_per_km2, 0) >= {u['pop_density_per_km2_min']} OR {x}.ghsl_class IN "
            f"({', '.join(repr(c) for c in u['ghsl_classes'])}) THEN 'urban' ELSE 'rural' END")


def eligible_sql(cfg: dict, s: str = "s", x: str = "x", label: str = "l.label") -> str:
    """SQL for whether a building can host an unlocated PU establishment (config eligibility)."""
    e = cfg["eligibility"]
    flags = " OR ".join(f"contains(coalesce({s}.qa_flags, ''), '{f}')" for f in e["exclude_qa_flags"]) or "false"
    tops = ", ".join(repr(t) for t in e["exclude_top_labels"])
    return (f"({s}.area_m2 >= {e['min_area_m2']} AND NOT ({flags})"
            f" AND NOT ({label} IS NOT NULL AND {label} IN ({tops}))"
            f" AND NOT ({str(e['exclude_facilities']).lower()} AND {x}.facility_type IS NOT NULL))")


def group_of(setting: str) -> str:
    """Score group for a building's setting: towns host the town trades, rural areas the mills."""
    return "town" if setting == "urban" else "grain_mill"


def building_table(con, map_cfg: dict, cfg: dict) -> None:
    """TEMP TABLE b: one row per building with setting, criteria inputs and eligibility."""
    I, P = INTERIM_DIR, PROCESSED_DIR  # noqa: E741, N806 (short names keep the SQL readable)
    con.sql(f"""CREATE OR REPLACE TEMP TABLE b AS
        SELECT s.bldg_id, s.lon, s.lat, s.area_m2, x.h3_r7, x.adm2_name AS district, x.adm3_name AS sector,
               {setting_sql(map_cfg)} AS setting,
               x.poi_count_250m, x.dist_road_major_m, x.dist_road_any_m, x.pop_density_per_km2, x.dist_powerline_m,
               x.rwi, c.cropland_share, {eligible_sql(cfg)} AS eligible
        FROM '{I}/buildings_base.parquet' s JOIN '{I}/building_context.parquet' x USING (bldg_id)
        LEFT JOIN '{I}/cropland_h3r8.parquet' c ON c.h3_r8 = h3_cell_to_parent(s.h3_r9, 8)
        LEFT JOIN (SELECT bldg_id, label FROM '{P}/labels.parquet' WHERE labeler = '{LABELER}') l USING (bldg_id)
        WHERE x.adm2_name IS NOT NULL""")


def criteria(df: pd.DataFrame) -> pd.DataFrame:
    """Candidate criteria, each scaled 0-1 (the first four are the dashboard's viability components)."""
    c = pd.DataFrame(index=df.index)
    c["market_access"] = (0.5 * np.minimum(df.poi_count_250m.fillna(0) / 10, 1)
                          + 0.5 * (1 - np.minimum(df.dist_road_major_m.fillna(2000) / 2000, 1)))
    c["demand_density"] = np.minimum(np.log10(1 + df.pop_density_per_km2.fillna(0)) / np.log10(1 + 15000), 1)
    c["grid_proximity"] = 1 - np.minimum(df.dist_powerline_m.fillna(5000) / 5000, 1)
    c["ability_to_pay"] = np.clip((df.rwi + 0.415) / (1.248 + 0.415), 0, 1).fillna(0.5)
    c["agri_context"] = df.cropland_share.fillna(0).clip(0, 1)
    c["building_size"] = np.clip(np.log10(df.area_m2.clip(lower=20) / 20) / np.log10(500 / 20), 0, 1)
    return c


def candidate_score(c: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    w = {k: v for k, v in weights.items() if v}
    return 100 * sum(c[k] * v for k, v in w.items()) / sum(w.values())


# ------------------------------------------------------------------------------------------------ 2. located
def located(con, cfg: dict) -> pd.DataFrame:
    """Buildings with a trade tag: tags on the building itself, else POIs inside or within max_m of it.
    `cfg["located"]` holds `rules` {profile_id: {osm: {key: [values]}, overture: [categories]}} and `max_m`."""
    I = INTERIM_DIR  # noqa: E741, N806
    rules = cfg["located"]["rules"]
    osm_cases, ov_cases = [], []
    for pid, r in rules.items():
        for key, vals in r.get("osm", {}).items():
            osm_cases.append(f"WHEN {key} IN ({', '.join(repr(v) for v in vals)}) THEN '{pid}'")
        if r.get("overture"):
            ov_cases.append(f"WHEN category IN ({', '.join(repr(v) for v in r['overture'])}) THEN '{pid}'")
    pts = con.sql(f"""
        SELECT * FROM (
          SELECT CASE {' '.join(osm_cases)} END AS profile_id,
                 'osm:' || coalesce(craft, shop, industrial, man_made) AS tag,
                 ST_X(ST_Centroid(geometry)) AS lon, ST_Y(ST_Centroid(geometry)) AS lat
          FROM '{I}/layers/osm_pois.parquet'
          UNION ALL
          SELECT CASE {' '.join(ov_cases)} END, 'overture:' || category,
                 ST_X(ST_Centroid(geometry)), ST_Y(ST_Centroid(geometry))
          FROM '{I}/layers/overture_places.parquet') WHERE profile_id IS NOT NULL""").df()
    con.register("pts", pts)
    max_deg = cfg["located"]["max_m"] / M_PER_DEG
    hits = con.sql(f"""
        WITH p AS (SELECT row_number() OVER (ORDER BY profile_id, tag, lon, lat) AS pid, *, ST_Point(lon, lat) AS g
                   FROM pts),
             near AS (SELECT p.pid, p.profile_id, p.tag, s.bldg_id, ST_Distance(s.geometry, p.g) AS d
                      FROM p JOIN '{I}/buildings_base.parquet' s
                        ON s.lon BETWEEN p.lon - {max_deg * 3} AND p.lon + {max_deg * 3}
                       AND s.lat BETWEEN p.lat - {max_deg * 3} AND p.lat + {max_deg * 3}
                      WHERE ST_Distance(s.geometry, p.g) <= {max_deg})
        SELECT bldg_id, profile_id, tag, d * {M_PER_DEG} AS dist_m FROM near
        QUALIFY row_number() OVER (PARTITION BY pid ORDER BY d, bldg_id) = 1""").df()
    # one trade per building: the closest tag wins
    hits = hits.sort_values(["dist_m", "bldg_id", "profile_id", "tag"]).drop_duplicates("bldg_id")
    con.register("hits", hits)
    return con.sql("""SELECT h.bldg_id, h.profile_id, h.tag, round(h.dist_m, 1) AS dist_m, b.h3_r7, b.district,
                             b.sector, b.setting FROM hits h JOIN b USING (bldg_id)""").df()


def district_targets(census: pd.DataFrame, loc: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """(district, setting, profile_id, pu_census, located): census establishments by trade (trade_mix by setting)."""
    loc_n = loc.groupby(["district", "setting", "profile_id"]).size()
    rows = [{"district": r.district, "setting": r.setting, "profile_id": pid,
             "pu_census": round(r.pu_census * share, 1),
             "located": int(loc_n.get((r.district, r.setting, pid), 0))}
            for r in census.itertuples(index=False) for pid, share in cfg["trade_mix"][r.setting].items()]
    return pd.DataFrame(rows)


def main() -> None:
    cfg, map_cfg = load_yaml("productive_use.yaml"), load_yaml("profile_mapping.yaml")
    census = census_counts(cfg)
    con = connect()
    building_table(con, map_cfg, cfg)
    loc = located(con, cfg)
    districts = district_targets(census, loc, cfg)
    loc.to_parquet(PROCESSED_DIR / "pu_located.parquet", index=False)
    districts.to_csv(PROCESSED_DIR / "pu_census_districts.csv", index=False)
    print(f"census PU establishments {census.pu_census.sum():,.0f} "
          f"(urban share {census.pu_share[census.setting == 'urban'].iloc[0]:.1%}, "
          f"rural {census.pu_share[census.setting == 'rural'].iloc[0]:.1%}); located {len(loc)}\n"
          f"{districts.groupby('profile_id')[['pu_census', 'located']].sum().round(0).to_string()}")


if __name__ == "__main__":
    main()
