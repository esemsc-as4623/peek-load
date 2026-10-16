"""Dashboard data bundle: static, versioned JSON/GeoJSON files the frontend reads (no server, no database).

    pixi run dashboard-data              # full bundle  -> data/dashboard/v1/ (gitignored; publish to static hosting)
    pixi run dashboard-data --sample     # small sample -> dashboard/sample_data/v1/ (committed, same schema)

The GeoParquet tables stay the source of truth; this is a read-only projection of them, sized for a browser. The
contract (files, fields, units, joins, client-side formulas) is dashboard/DATA_CONTRACT.md. Demand numbers are
computed here from building_profiles x profile_library, with the same formulas the frontend uses for selections.
Restricted survey microdata never enters the bundle: only its published aggregates (profiles, checks).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import shutil
import subprocess

import duckdb
import numpy as np
import pandas as pd

from rtl.archetypes import Archetype
from rtl.demand import productive_use as pu
from rtl.demand.mapping import LABELER
from rtl.settings import DATA_DIR, INTERIM_DIR, PROCESSED_DIR, REPO_ROOT, REPORTS_DIR, load_yaml

FULL_OUT = DATA_DIR / "dashboard"
SAMPLE_OUT = REPO_ROOT / "dashboard" / "sample_data"
HOURS = range(24)
EVENING, DAYTIME, NIGHT = range(18, 23), range(8, 17), range(0, 5)
FIGURES = ["B1_profiles.png", "B1_household_check.png", "A3_cooling_check.png", "A3_cooling_class.png",
           "A3_cdd22_per_year.png", "A3_t2m_mean_c.png", "labels_confusion.png", "A1_height_vs_osm.png",
           "A1_growth.png", "eda01_density_h3r7.png", "C6_national_demand.png", "C7_demand_by_year.png",
           "C8_uncertainty.png", "C9_benchmarks.png"]


# ------------------------------------------------------------------------------------------------ helpers
def clean(x):
    """JSON-safe: NaN/inf -> None, numpy -> python, recursively."""
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [clean(v) for v in x]
    if x is pd.NA or x is pd.NaT:
        return None
    if isinstance(x, np.generic):
        x = x.item()
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


def rnd(x, n: int = 1):
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) or pd.isna(x) else round(float(x), n)


def write_json(path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean(obj), separators=(",", ":"), ensure_ascii=False))


def write_geojson(path, geoms: list[str], props: list[dict]) -> None:
    """FeatureCollection from GeoJSON geometry strings (already precision-reduced) and property dicts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        f.write('{"type":"FeatureCollection","features":[')
        for i, (g, p) in enumerate(zip(geoms, props, strict=True)):
            f.write(("," if i else "") + '{"type":"Feature","geometry":' + g + ',"properties":'
                    + json.dumps(clean(p), separators=(",", ":"), ensure_ascii=False) + "}")
        f.write("]}")


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.sql("LOAD spatial; LOAD h3; SET memory_limit='6GB';")
    return con


# ------------------------------------------------------------------------------------------------ demand maths
def library() -> pd.DataFrame:
    return pd.read_parquet(PROCESSED_DIR / "profile_library.parquet").set_index("profile_id")


def curves(lib: pd.DataFrame, kind: str = "") -> np.ndarray:
    """(n_profiles, 24) per-unit hourly mean W; kind '' (all days), 'weekday_' or 'weekend_'."""
    return lib[[f"w_{kind}h{h:02d}" for h in HOURS]].to_numpy(float)


def characteristics(hourly: np.ndarray, weekday: np.ndarray, weekend: np.ndarray) -> dict:
    """Load Profile Characteristics of one 24-h curve (W). Same formulas as DATA_CONTRACT.md section 5."""
    total = hourly.sum()
    if total <= 0:
        return {"peak_w": 0.0, "peak_hour": None, "load_factor": None, "evening_share": None, "daytime_share": None,
                "night_base_w": 0.0, "weekday_weekend_ratio": None, "shape": "none"}
    peak_h = int(hourly.argmax())
    lf = hourly.mean() / hourly.max()
    ev = hourly[list(EVENING)].sum() / total
    day = hourly[list(DAYTIME)].sum() / total
    shape = ("evening peak" if ev >= 0.4 and peak_h in range(17, 23) else "daytime" if day >= 0.5
             else "flat" if lf >= 0.6 else "mixed")
    return {"peak_w": rnd(hourly.max()), "peak_hour": peak_h, "load_factor": rnd(lf, 2), "evening_share": rnd(ev, 2),
            "daytime_share": rnd(day, 2), "night_base_w": rnd(hourly[list(NIGHT)].mean()),
            "weekday_weekend_ratio": rnd(weekday.sum() / weekend.sum(), 2) if weekend.sum() > 0 else None,
            "shape": shape}


def building_demand(weights: pd.DataFrame, lib: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, np.ndarray]]:
    """Per-building demand from profile weights (bldg_id, profile_id, weight) x per-unit library values."""
    w = weights.pivot_table(index="bldg_id", columns="profile_id", values="weight", aggfunc="sum").reindex(
        columns=lib.index, fill_value=0.0).fillna(0.0)
    W = w.to_numpy(float)
    load = {k: W @ curves(lib, k) for k in ("", "weekday_", "weekend_")}
    daily = W @ lib.daily_kwh_mean.to_numpy(float)
    rows = []
    for i, b in enumerate(w.index):
        c = characteristics(load[""][i], load["weekday_"][i], load["weekend_"][i])
        rows.append({"bldg_id": b, "annual_kwh": rnd(daily[i] * 365),
                     "annual_kwh_p10": rnd((W[i] @ lib.daily_kwh_p10.to_numpy(float)) * 365),
                     "annual_kwh_p90": rnd((W[i] @ lib.daily_kwh_p90.to_numpy(float)) * 365), **c})
    return pd.DataFrame(rows).set_index("bldg_id"), {k: v for k, v in load.items()} | {"_index": w.index}


def pu_scores(df: pd.DataFrame, pu_cfg: dict, dash_cfg: dict) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Productive-use candidate criteria (0-1), score (0-100, weights by the building's group) and category, for
    eligible buildings; others get null (config/dashboard.yaml productive_use, config/productive_use.yaml)."""
    c = pu.criteria(df)
    score = pd.Series(np.nan, index=df.index)
    for g, w in pu_cfg["candidate_score"].items():
        m = df.pu_group == g
        score[m] = pu.candidate_score(c[m], w)
    score[~df.pu_eligible.astype(bool)] = np.nan
    cats = dash_cfg["categories"]
    cat = pd.cut(score, bins=[cats["low"][0], cats["medium"][0], cats["high"][0], 100.0001],
                 labels=["low", "medium", "high"], right=False, include_lowest=True)
    return c, score.round(1), cat.astype(object).where(score.notna(), None)


# ------------------------------------------------------------------------------------------------ deep dives
def deep_dive(con, dd: dict, classes: list[str], bbox: list[float] | None) -> pd.DataFrame:
    I, P = INTERIM_DIR, PROCESSED_DIR  # noqa: E741, N806 (short names keep the SQL readable)
    pu_cfg, map_cfg = load_yaml("productive_use.yaml"), load_yaml("profile_mapping.yaml")
    where = f"x.adm2_name = '{dd['district']}' AND x.adm3_name = '{dd['sector']}'"
    if bbox:
        where += f" AND b.lon BETWEEN {bbox[0]} AND {bbox[2]} AND b.lat BETWEEN {bbox[1]} AND {bbox[3]}"
    df = con.sql(f"""
        SELECT b.bldg_id, b.source, b.source_confidence, b.area_m2, b.perimeter_m, b.compactness, b.orientation_deg,
               b.lon, b.lat, b.h3_r9, b.qa_flags, ST_AsGeoJSON(ST_ReducePrecision(b.geometry, 0.000001)) AS geom,
               h.height_m, h.est_floors, h.gfa_m2, h.first_seen_year,
               {", ".join(f"h.presence_{y}" for y in range(2016, 2024))},
               x.adm3_name, x.adm4_name, x.adm5_name, x.osm_building, x.osm_amenity, x.osm_shop, x.overture_category,
               x.facility_type, x.dist_road_major_m, x.dist_road_any_m, x.dist_powerline_m, x.poi_count_50m,
               x.poi_count_100m, x.poi_count_250m, x.ghsl_class, x.ghsl_nres_share, x.pop_density_per_km2, x.rwi,
               x.elevation_m, x.h3_r7, k.cooling_class, k.tmax_p95_c, k.cdd22_per_year, k.t2m_mean_c,
               l.label, l.confidence, l.probs_json, c.cropland_share,
               {pu.setting_sql(map_cfg)} AS setting, {pu.eligible_sql(pu_cfg, s="b")} AS pu_eligible
        FROM '{I}/buildings_base.parquet' b
        JOIN '{I}/building_context.parquet' x USING (bldg_id)
        LEFT JOIN '{I}/building_height.parquet' h USING (bldg_id)
        LEFT JOIN '{I}/climate_h3.parquet' k USING (h3_r7)
        LEFT JOIN (SELECT * FROM '{P}/labels.parquet' WHERE labeler = '{LABELER}') l USING (bldg_id)
        LEFT JOIN '{I}/cropland_h3r8.parquet' c ON c.h3_r8 = h3_cell_to_parent(b.h3_r9, 8)
        WHERE {where} ORDER BY b.bldg_id""").df().set_index("bldg_id")
    df["pu_group"] = df.setting.map(pu.group_of)
    pj = df.probs_json.apply(lambda s: json.loads(s) if isinstance(s, str) else None)
    df["probs"] = pj.apply(lambda j: [round(float(j.get(c, 0)), 3) for c in classes] if j else None)
    df["cues"] = pj.apply(lambda j: j.get("_evidence") if j else None)
    return df.drop(columns="probs_json")


def establishment_summary(dd: dict, lib: pd.DataFrame, df: pd.DataFrame, kind: str) -> dict:
    """Productive use (kind "pu") or services ("svc") in a deep-dive sector: the census figure reported for the
    whole district, and the establishments placed on buildings in the sector (tagged or census-allocated). Every
    establishment sits on a building; their demand is already in the building totals."""
    census = pd.read_csv(PROCESSED_DIR / f"{kind}_census_districts.csv")
    col = "pu_census" if kind == "pu" else "census"
    profiles = set(census.profile_id)
    e = df[df.establishment.isin(profiles)]
    units = e.establishment.value_counts()
    d = census[census.district == dd["district"]]
    return {
        "reported": {"district": dd["district"], "establishments_census": rnd(d[col].sum(), 0),
                     "by_profile": d.groupby("profile_id")[col].sum().round(0).to_dict(),
                     "source": "NISR Establishment Census 2023 (district level)"},
        "building_sum": {"buildings": int(len(e)), "tagged": int((e.use_source == "tag").sum()),
                         "census_allocated": int((e.use_source == "census").sum()),
                         "units_by_profile": units.to_dict(),
                         "annual_mwh": rnd(sum(units.get(p, 0) * lib.at[p, "daily_kwh_mean"] for p in units.index)
                                           * 365 / 1000, 1)},
        "note": ("Every census establishment is placed on a building: tagged ones first, then the best candidates "
                 "(rtl.demand.allocate). Their demand is part of the building totals."),
    }


def export_deep_dive(con, out, dd_id: str, dd: dict, cfg: dict, lib: pd.DataFrame, wtop: pd.DataFrame,
                     bbox=None) -> dict:
    classes = [c["id"] for c in cfg["classes"]]
    df = deep_dive(con, dd, classes, bbox)
    weights = con.sql(f"SELECT * FROM '{PROCESSED_DIR}/building_profiles.parquet' WHERE bldg_id IN "
                      f"(SELECT unnest(?::VARCHAR[]))", params=[list(df.index)]).df()
    dem, load = building_demand(weights, lib)
    df = df.join(dem)
    df["modelled"] = df.index.isin(dem.index)
    # "top class only" mode is kept for the schema: uses are now allocated discretely, so both modes are the same
    weights_top = wtop[wtop.bldg_id.isin(df.index)]
    dem_top, _ = building_demand(weights_top, lib) if len(weights_top) else (pd.DataFrame(), None)
    top = dem_top.reindex(df.index)[["annual_kwh", "peak_w", "peak_hour"]] if len(dem_top) else pd.DataFrame(
        index=df.index, columns=["annual_kwh", "peak_w", "peak_hour"])
    top.loc[df.modelled & top.annual_kwh.isna(), ["annual_kwh", "peak_w"]] = 0.0
    df = df.join(top.add_suffix("_top"))
    pu_cfg = load_yaml("productive_use.yaml")
    comp, df["pu_score"], df["pu_category"] = pu_scores(df, pu_cfg, cfg["productive_use"])
    bu = con.sql(f"SELECT bldg_id, use, establishment, est_weight, households, upper, source AS use_source FROM "
                 f"'{PROCESSED_DIR}/building_use.parquet' WHERE bldg_id IN (SELECT unnest(?::VARCHAR[]))",
                 params=[list(df.index)]).df().set_index("bldg_id")
    df = df.join(bu)
    gc = con.sql(f"SELECT bldg_id, p_connected FROM '{PROCESSED_DIR}/grid_connection.parquet' WHERE bldg_id IN "
                 "(SELECT unnest(?::VARCHAR[]))", params=[list(df.index)]).df().set_index("bldg_id")
    df["p_connected"] = gc.p_connected.reindex(df.index)
    # expected demand (x P(connected) raked to EICV7; rtl.demand.calibrate) and use agreement across runs (B6)
    pv = con.sql(f"SELECT bldg_id, expected_annual_kwh FROM '{PROCESSED_DIR}/building_demand_preview.parquet' "
                 "WHERE bldg_id IN (SELECT unnest(?::VARCHAR[]))", params=[list(df.index)]).df().set_index("bldg_id")
    df["annual_kwh_expected"] = pv.expected_annual_kwh.reindex(df.index)
    df.loc[df.modelled & df.annual_kwh_expected.isna(), "annual_kwh_expected"] = 0.0
    by = PROCESSED_DIR / "building_demand_by_year.parquet"
    yr = (con.sql(f"SELECT * FROM '{by}' WHERE bldg_id IN (SELECT unnest(?::VARCHAR[]))", params=[list(df.index)])
          .df().set_index("bldg_id") if by.exists() else pd.DataFrame())
    ag = PROCESSED_DIR / "uncertainty" / "building_agreement.parquet"
    df["use_agreement"] = (con.sql(f"SELECT bldg_id, use_agreement FROM '{ag}' WHERE bldg_id IN "
                                   "(SELECT unnest(?::VARCHAR[]))", params=[list(df.index)]).df()
                           .set_index("bldg_id").use_agreement.reindex(df.index) if ag.exists() else np.nan)

    map_cols = ["label", "confidence", "modelled", "annual_kwh", "peak_w", "peak_hour", "annual_kwh_top",
                "peak_w_top", "cooling_class", "pu_score", "pu_category", "use", "establishment", "households",
                "use_source",
                "p_connected", "annual_kwh_expected", "use_agreement",
                "height_m", "est_floors", "first_seen_year", "source", "area_m2"]
    props = []
    for b, r in df[map_cols].iterrows():
        p = {"bldg_id": b, **{k: r[k] for k in map_cols}, "h3_r9": df.at[b, "h3_r9"]}
        p["confidence"], p["area_m2"], p["height_m"] = rnd(p["confidence"], 2), rnd(p["area_m2"]), rnd(p["height_m"])
        p["peak_hour"] = None if pd.isna(p["peak_hour"]) else int(p["peak_hour"])
        p["annual_kwh_top"], p["peak_w_top"] = rnd(p["annual_kwh_top"]), rnd(p["peak_w_top"])
        p["p_connected"] = rnd(p["p_connected"], 2)
        p["annual_kwh_expected"], p["use_agreement"] = rnd(p["annual_kwh_expected"]), rnd(p["use_agreement"], 2)
        props.append(p)
    write_geojson(out / "deep_dives" / dd_id / "buildings.geojson", list(df.geom), props)

    wmap = weights.groupby("bldg_id").apply(lambda g: dict(zip(g.profile_id, g.weight.round(6), strict=True)),
                                            include_groups=False)
    wmap_top = (weights_top.groupby("bldg_id").apply(lambda g: dict(zip(g.profile_id, g.weight.round(6), strict=True)),
                                                     include_groups=False) if len(weights_top) else pd.Series())
    details = {}
    for b, r in df.iterrows():
        details[b] = {
            "lon": rnd(r.lon, 6), "lat": rnd(r.lat, 6), "h3_r7": r.h3_r7, "h3_r9": r.h3_r9,
            "admin": {"sector": r.adm3_name, "cell": r.adm4_name, "village": r.adm5_name},
            "footprint": {"source": r.source, "source_confidence": rnd(r.source_confidence, 2),
                          "area_m2": rnd(r.area_m2),
                          "perimeter_m": rnd(r.perimeter_m), "compactness": rnd(r.compactness, 2),
                          "orientation_deg": rnd(r.orientation_deg, 0),
                          "qa_flags": [f for f in str(r.qa_flags or "").split(";") if f]},
            "height": {"height_m": rnd(r.height_m), "est_floors": r.est_floors, "gfa_m2": rnd(r.gfa_m2),
                       "first_seen_year": r.first_seen_year,
                       "presence": [rnd(r[f"presence_{y}"], 2) for y in range(2016, 2024)]},
            "use": {"label": r.label, "confidence": rnd(r.confidence, 2), "probs": r.probs, "cues": r.cues,
                    "tags": {k: r[k] for k in ("osm_building", "osm_amenity", "osm_shop", "overture_category",
                                               "facility_type") if isinstance(r[k], str)}},
            "allocation": {"use": r.use, "establishment": r.establishment if isinstance(r.establishment, str) else None,
                           "households": None if pd.isna(r.households) else int(r.households), "source": r.use_source,
                           "establishment_share": rnd(r.est_weight, 4),
                           "upper_wealth": None if pd.isna(r.households) or not r.households else bool(r.upper),
                           "use_agreement": rnd(r.use_agreement, 2)},
            "expected": None if not r.modelled else {
                "annual_kwh": rnd(r.annual_kwh_expected),
                "annual_kwh_by_year": ({c[-4:]: rnd(v) for c, v in yr.loc[b].items()} if b in yr.index else None)},
            "profiles": wmap.get(b),
            "profiles_top_class": wmap_top.get(b, {} if r.modelled else None),
            "demand": None if not r.modelled else {k: (int(r[k]) if k == "peak_hour" else r[k]) for k in (
                "annual_kwh", "annual_kwh_p10", "annual_kwh_p90", "peak_w", "peak_hour", "load_factor",
                "evening_share", "daytime_share", "night_base_w", "weekday_weekend_ratio", "shape")},
            "demand_top_class": None if not r.modelled else {"annual_kwh": rnd(r.annual_kwh_top),
                                                              "peak_w": rnd(r.peak_w_top),
                                                              "peak_hour": None if pd.isna(r.peak_hour_top)
                                                              else int(r.peak_hour_top)},
            "climate": {"cooling_class": r.cooling_class, "tmax_p95_c": rnd(r.tmax_p95_c),
                        "cdd22_per_year": rnd(r.cdd22_per_year),
                        "t2m_mean_c": rnd(r.t2m_mean_c)},
            "pu": {"eligible": bool(r.pu_eligible), "group": r.pu_group, "score": r.pu_score,
                   "category": r.pu_category,
                   "criteria": {k: rnd(v, 2) for k, v in comp.loc[b].items()}},
            "context": {"dist_road_major_m": rnd(r.dist_road_major_m, 0), "dist_road_any_m": rnd(r.dist_road_any_m, 0),
                        "dist_powerline_m": rnd(r.dist_powerline_m, 0), "poi_count_50m": r.poi_count_50m,
                        "poi_count_100m": r.poi_count_100m, "poi_count_250m": r.poi_count_250m,
                        "pop_density_per_km2": rnd(r.pop_density_per_km2, 0), "rwi": rnd(r.rwi, 3),
                        "ghsl_class": r.ghsl_class, "ghsl_nres_share": rnd(r.ghsl_nres_share, 2),
                        "elevation_m": rnd(r.elevation_m, 0), "setting": r.setting,
                        "p_grid_connected": rnd(r.p_connected, 3),
                        "cropland_share": rnd(r.cropland_share, 2)}}
    shards: dict[str, dict] = {}
    for b, d in details.items():
        shards.setdefault(d["h3_r9"], {})[b] = d
    for h, chunk in shards.items():
        write_json(out / "deep_dives" / dd_id / "details" / f"{h}.json", chunk)
    write_json(out / "deep_dives" / dd_id / "details" / "index.json", {h: len(c) for h, c in sorted(shards.items())})

    # compact weights for client-side aggregation: one array per building in profile order
    order = list(lib.index)

    def arrays(wt: pd.DataFrame) -> dict:
        if not len(wt):
            return {}
        m = wt.pivot_table(index="bldg_id", columns="profile_id", values="weight", aggfunc="sum").reindex(
            columns=order, fill_value=0.0).fillna(0.0).round(6)
        return {b: [float(v) for v in row] for b, row in zip(m.index, m.to_numpy(), strict=True)}

    pu_order = list(cfg["productive_use"]["criteria"])
    pu_rows = {b: [rnd(comp.at[b, k], 3) for k in pu_order] for b in df.index[df.pu_eligible.astype(bool)]}
    write_json(out / "deep_dives" / dd_id / "weights.json", {
        "profile_order": order, "probabilities": arrays(weights), "top_class": arrays(weights_top),
        "pu_criteria_order": pu_order, "pu_criteria": pu_rows,
        "pu_group": {b: g for b, g in df.pu_group[df.pu_eligible.astype(bool)].items()}})

    # area summary: curves summed over modelled buildings, split by profile group
    groups = {pid: cfg["profiles"][pid]["group"] for pid in lib.index}
    w = weights.pivot_table(index="bldg_id", columns="profile_id", values="weight", aggfunc="sum").reindex(
        columns=lib.index, fill_value=0.0).fillna(0.0)
    units = w.sum(axis=0)  # expected units per profile in the area
    by_group = {g["id"]: (units[[p for p in lib.index if groups[p] == g["id"]]].to_numpy()
                          @ curves(lib)[[groups[p] == g["id"] for p in lib.index]]) for g in cfg["profile_groups"]}
    total = {k: units.to_numpy() @ curves(lib, k) for k in ("", "weekday_", "weekend_")}
    units_top = weights_top.groupby("profile_id").weight.sum().reindex(lib.index, fill_value=0.0)
    total_top = {k: units_top.to_numpy() @ curves(lib, k) for k in ("", "weekday_", "weekend_")}
    ch = characteristics(total[""], total["weekday_"], total["weekend_"])
    mod = df[df.modelled]
    labelled = mod[mod.probs.map(lambda p: isinstance(p, list))]  # tagged trades can be modelled without a label
    summary = {
        "id": dd_id, **dd, "n_buildings": len(df), "n_modelled": int(df.modelled.sum()),
        "bbox": [rnd(df.lon.min(), 5), rnd(df.lat.min(), 5), rnd(df.lon.max(), 5), rnd(df.lat.max(), 5)],
        "class_mix_top_label": mod.label.value_counts().reindex(classes, fill_value=0).to_dict(),
        "class_mix_expected": (dict(zip(classes, np.nansum(np.vstack(labelled.probs.to_numpy()), axis=0).round(1),
                                        strict=True)) if len(labelled) else {}),
        "cooling_mix": df.cooling_class.value_counts().to_dict(),
        "pu_mix": df.pu_category.value_counts().to_dict(),
        "use_mix": df.use.value_counts().to_dict(), "households": int(df.households.fillna(0).sum()),
        "productive_use": establishment_summary(dd, lib, df, "pu"),
        "services": establishment_summary(dd, lib, df, "svc"),
        "units_by_profile": units.round(1).to_dict(),
        "metrics": {"annual_mwh": rnd(mod.annual_kwh.sum() / 1000, 1),
                    "annual_mwh_expected": rnd(mod.annual_kwh_expected.sum() / 1000, 1),
                    "by_year_whole_sector": sector_years(con, dd),
                    "uncertainty": sector_band(dd),
                    "annual_mwh_p10": rnd(mod.annual_kwh_p10.sum() / 1000, 1),
                    "annual_mwh_p90": rnd(mod.annual_kwh_p90.sum() / 1000, 1),
                    "area_peak_kw": rnd(total[""].max() / 1000, 1), "area_peak_hour": int(total[""].argmax()),
                    "sum_of_building_peaks_kw": rnd(mod.peak_w.sum() / 1000, 1),
                    "median_building_annual_kwh": rnd(mod.annual_kwh.median()),
                    "p90_building_annual_kwh": rnd(mod.annual_kwh.quantile(0.9)),
                    "load_profile": ch},
        "metrics_top_class": {"annual_mwh": rnd(units_top.to_numpy() @ lib.daily_kwh_mean.to_numpy() * 365 / 1000, 1),
                              "area_peak_kw": rnd(total_top[""].max() / 1000, 1),
                              "area_peak_hour": int(total_top[""].argmax()),
                              "load_profile": characteristics(total_top[""], total_top["weekday_"],
                                                              total_top["weekend_"])},
        "units_by_profile_top_class": units_top.round(1).to_dict(),
        "hourly_kw_top_class": {"all": [rnd(v / 1000, 2) for v in total_top[""]]},
        "hourly_kw": {"all": [rnd(v / 1000, 2) for v in total[""]],
                      "weekday": [rnd(v / 1000, 2) for v in total["weekday_"]],
                      "weekend": [rnd(v / 1000, 2) for v in total["weekend_"]],
                      "by_group": {g: [rnd(v / 1000, 2) for v in arr] for g, arr in by_group.items()}},
        "note": "annual_mwh is potential demand (every building connected); annual_mwh_expected multiplies households "
                "by P(connected) raked to EICV7 2024. Household energy is scaled to REG means by region.",
    }
    write_json(out / "deep_dives" / dd_id / "summary.json", summary)
    return summary


def sector_band(dd: dict) -> dict | None:
    """The deep-dive sector's potential-energy band from rtl.demand.sensitivity (MWh/yr), if it has run."""
    f = PROCESSED_DIR / "uncertainty" / "sector_bands.csv"
    if not f.exists():
        return None
    b = pd.read_csv(f)
    r = b[(b.sector == dd.get("sector")) & (b.district == dd.get("district"))]
    if r.empty:
        return None
    r = r.iloc[0]
    return {k: rnd(r[f"{k}_mwh"], 1) for k in ("alloc_p10", "alloc_p90", "profile_p10", "profile_p90",
                                                "combined_p10", "combined_p90", "base")}



def district_extras(con) -> pd.DataFrame:
    """Per district: the constraints and calibration the allocation imposes, growth and night lights (adm2_name)."""
    P, I = PROCESSED_DIR, INTERIM_DIR  # noqa: E741, N806
    u = con.sql(f"""SELECT district AS adm2_name,
            sum(households * (setting = 'urban')::INT) / sum(households) AS households_urban_share,
            sum(households * upper::INT) / nullif(sum(households), 0) AS households_upper_share,
            sum(est_weight) FILTER (WHERE establishment = 'school') AS schools,
            sum(est_weight) FILTER (WHERE establishment IN ('health_post', 'health_centre', 'hospital'))
                AS health_facilities,
            sum(est_weight) FILTER (WHERE use = 'religious') AS places_of_worship
        FROM '{P}/building_use.parquet' GROUP BY 1""").df()
    g = con.sql(f"""SELECT u.district AS adm2_name, sum(w.units * w.p_grid) / sum(w.units) AS p_grid_households
        FROM '{P}/building_profiles.parquet' w JOIN '{P}/building_use.parquet' u USING (bldg_id)
        WHERE w.profile_id LIKE 'household_%' GROUP BY 1""").df()
    out = u.merge(g, on="adm2_name", how="left")
    eicv = pd.read_csv(P / "validation_anchors" / "grid_lighting_by_district_2024.csv")
    out = out.merge(eicv[["district", "grid_main_lighting_share"]].rename(
        columns={"district": "adm2_name", "grid_main_lighting_share": "eicv7_grid_share_2024"}), how="left")
    yrs = P / "demand_by_year.csv"
    if yrs.exists():
        y = pd.read_csv(yrs)
        w = y.pivot_table(index="district", columns="year", values="expected_gwh")
        out = out.merge(pd.DataFrame({"adm2_name": w.index, "growth_rate": y.groupby("district").growth_rate.first()
                                      .reindex(w.index).to_numpy(), "expected_gwh_2016": w[2016].to_numpy(),
                                      "expected_gwh_2030": w[2030].to_numpy()}), how="left")
    ntl = I / "ntl_h3r8.parquet"
    if ntl.exists():
        n = con.sql(f"""WITH c AS (SELECT DISTINCT h3_cell_to_parent(b.h3_r9, 8) AS h3_r8, x.adm2_name
                        FROM '{I}/buildings_base.parquet' b JOIN '{I}/building_context.parquet' x USING (bldg_id))
            SELECT c.adm2_name, avg(radiance) FILTER (WHERE year = 2016) AS ntl_radiance_2016,
                   avg(radiance) FILTER (WHERE year = 2024) AS ntl_radiance_2024,
                   avg(lit_share) FILTER (WHERE year = 2024) AS ntl_lit_share_2024
            FROM '{ntl}' n JOIN c USING (h3_r8) WHERE c.adm2_name IS NOT NULL GROUP BY 1""").df()
        out = out.merge(n, on="adm2_name", how="left")
    return out


def hex_ntl(con) -> pd.DataFrame:
    """Night lights per H3 r7 cell: mean radiance of its r8 children in 2016 and 2024, lit share in 2024."""
    ntl = INTERIM_DIR / "ntl_h3r8.parquet"
    if not ntl.exists():
        return pd.DataFrame(columns=["h3_r7"])
    return con.sql(f"""SELECT h3_cell_to_parent(h3_string_to_h3(h3_r8), 7) AS c,
            avg(radiance) FILTER (WHERE year = 2016) AS ntl_radiance_2016,
            avg(radiance) FILTER (WHERE year = 2024) AS ntl_radiance_2024,
            avg(lit_share) FILTER (WHERE year = 2024) AS ntl_lit_share_2024
        FROM '{ntl}' GROUP BY 1""").df().assign(h3_r7=lambda d: d.c.map(lambda v: f"{v:x}")).drop(columns="c")


def sector_years(con, dd: dict) -> list[dict] | None:
    """Potential and expected GWh per year for a deep-dive sector: back-cast per building, projection by the
    district's growth rate (rtl.demand.years)."""
    P = PROCESSED_DIR  # noqa: N806
    f = P / "building_demand_by_year.parquet"
    if not f.exists():
        return None
    cols = [c for c in con.sql(f"SELECT * FROM '{f}' LIMIT 0").columns if c.startswith("expected_kwh_")]
    sums = ", ".join(f"sum(y.{c}) / 1e6 AS {c}" for c in cols)
    r = con.sql(f"""SELECT {sums}, sum(p.annual_kwh) / 1e6 AS pot FROM '{f}' y
                    JOIN '{P}/building_use.parquet' u USING (bldg_id)
                    JOIN '{P}/building_demand_preview.parquet' p USING (bldg_id)
                    WHERE u.district = ? AND u.sector = ?""", params=[dd["district"], dd["sector"]]).df().iloc[0]
    d = pd.read_csv(P / "demand_by_year.csv")
    d = d[d.district == dd["district"]].set_index("year")
    base = int(d[d.kind == "base"].index[0])
    rows = []
    for yr, k in d.kind.items():
        if yr <= base:
            exp = float(r[f"expected_kwh_{yr}"])
            pot = float(r.pot) * float(d.at[yr, "potential_gwh"] / d.at[base, "potential_gwh"])
        else:
            f_ = float(d.at[yr, "stock_index"])
            exp, pot = float(r[f"expected_kwh_{base}"]) * f_, float(r.pot) * f_
        rows.append({"year": int(yr), "kind": k, "potential_gwh": rnd(pot, 3), "expected_gwh": rnd(exp, 3)})
    return rows


# ------------------------------------------------------------------------------------------------ national/admin
PU_FIELD_KINDS = {"pu_census_reported": "reported", "svc_census_reported": "reported",
                  "households_census_reported": "reported", "pu_buildings": "building_sum",
                  "svc_buildings": "building_sum", "households": "building_sum", "annual_mwh_modelled": "building_sum"}


def pu_by_area(lib: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Allocated uses per H3 r7 cell, sector (district, sector) and district: productive-use and service buildings,
    households (sums over buildings); districts add the census figures they reproduce (reported)."""
    u = pd.read_parquet(PROCESSED_DIR / "building_use.parquet")
    pu_p = set(pd.read_csv(PROCESSED_DIR / "pu_census_districts.csv").profile_id)
    svc_p = set(pd.read_csv(PROCESSED_DIR / "svc_census_districts.csv").profile_id)
    u = u.assign(pu=u.establishment.isin(pu_p).astype(int), svc=u.establishment.isin(svc_p).astype(int))
    pc = pd.read_csv(PROCESSED_DIR / "pu_census_districts.csv").groupby("district").pu_census.sum()
    sc = pd.read_csv(PROCESSED_DIR / "svc_census_districts.csv").groupby("district").census.sum()
    hh = pd.read_csv(PROCESSED_DIR / "validation_anchors" / "households_by_district_2022.csv").set_index(
        "district").households_total
    out = {}
    for level, keys in [("h3_r7", ["h3_r7"]), ("sector", ["district", "sector"]), ("district", ["district"])]:
        g = u.groupby(keys).agg(pu_buildings=("pu", "sum"), svc_buildings=("svc", "sum"),
                                households=("households", "sum"))
        if level == "district":
            g = g.join(pc.rename("pu_census_reported")).join(sc.rename("svc_census_reported")).join(
                hh.rename("households_census_reported"))
        out[level] = g.reset_index()
    return out


def national(con, out, lib: pd.DataFrame) -> None:
    I, P = INTERIM_DIR, PROCESSED_DIR  # noqa: E741, N806 (short names keep the SQL readable)
    pua = pu_by_area(lib)
    daily = ", ".join(f"('{pid}', {v})" for pid, v in lib.daily_kwh_mean.items())
    con.sql(f"""CREATE OR REPLACE TEMP TABLE modelled AS
        SELECT w.bldg_id, sum(w.weight * d.kwh) * 365 AS annual_kwh, sum(w.weight * w.p_grid * d.kwh) * 365 AS exp_kwh
        FROM '{P}/building_profiles.parquet' w JOIN (VALUES {daily}) d(pid, kwh) ON d.pid = w.profile_id GROUP BY 1""")
    base = f"""SELECT x.bldg_id, x.h3_r7, x.adm2_name, x.adm3_name, b.area_m2, h.height_m, x.pop_density_per_km2,
                      x.rwi, x.dist_powerline_m, x.ghsl_class, k.cooling_class, m.annual_kwh, m.exp_kwh
               FROM '{I}/building_context.parquet' x JOIN '{I}/buildings_base.parquet' b USING (bldg_id)
               LEFT JOIN '{I}/building_height.parquet' h USING (bldg_id)
               LEFT JOIN '{I}/climate_h3.parquet' k USING (h3_r7) LEFT JOIN modelled m USING (bldg_id)"""
    aggs = """count(*) AS n_buildings, sum(area_m2) AS footprint_m2, avg(height_m) AS mean_height_m,
              avg(pop_density_per_km2) AS pop_density_mean, avg(rwi) AS rwi_mean,
              median(dist_powerline_m) AS dist_powerline_m_median, mode(ghsl_class) AS ghsl_class_mode,
              count(annual_kwh) AS n_modelled, sum(annual_kwh) / 1000 AS annual_mwh_modelled,
              sum(exp_kwh) / 1000 AS annual_mwh_expected,
              avg((cooling_class = 'low')::INT) AS share_low, avg((cooling_class = 'medium')::INT) AS share_medium,
              avg((cooling_class = 'high')::INT) AS share_high"""
    hexes = con.sql(f"""WITH a AS (SELECT h3_r7, mode(adm2_name) AS district, {aggs} FROM ({base}) GROUP BY 1)
        SELECT k.*, a.* EXCLUDE (h3_r7),
               ST_AsGeoJSON(ST_ReducePrecision(ST_GeomFromText(h3_cell_to_boundary_wkt(k.h3_r7)), 0.00001)) AS geom
        FROM '{I}/climate_h3.parquet' k LEFT JOIN a USING (h3_r7) ORDER BY k.h3_r7""").df()
    keep = ["h3_r7", "district", "n_buildings", "footprint_m2", "mean_height_m", "pop_density_mean", "rwi_mean",
            "dist_powerline_m_median", "ghsl_class_mode", "elevation_m", "t2m_mean_c", "tmax_p95_c", "cdd18_per_year",
            "cdd22_per_year", "cdd24_per_year", "ghi_kwh_m2_day", "rh_mean_pct", "precip_mm_per_year", "cooling_class",
            "n_modelled", "annual_mwh_modelled", "annual_mwh_expected"]
    hexes = hexes.merge(pua["h3_r7"], on="h3_r7", how="left")
    gc = PROCESSED_DIR / "grid_connection.parquet"
    if gc.exists():  # mean predicted P(grid connection) over buildings >= 20 m2 (rtl.demand.grid_connection)
        hexes = hexes.merge(con.sql(f"""SELECT g.h3_r7, avg(g.p_connected) AS p_connected_mean FROM '{gc}' g
            JOIN '{INTERIM_DIR}/buildings_base.parquet' b USING (bldg_id) WHERE b.area_m2 >= 20 GROUP BY 1""").df(),
                            on="h3_r7", how="left")
    hexes = hexes.merge(hex_ntl(con), on="h3_r7", how="left")
    keep += ["pu_buildings", "svc_buildings", "households", "p_connected_mean", "annual_mwh_expected",
             "ntl_radiance_2016", "ntl_radiance_2024", "ntl_lit_share_2024"]
    keep = list(dict.fromkeys(keep))
    keep = [k for k in keep if k in hexes.columns]
    props = [{k: (rnd(r[k], 2) if isinstance(r[k], float) else r[k]) for k in keep} for _, r in hexes.iterrows()]
    for p in props:
        p["n_buildings"] = int(p["n_buildings"] or 0)
        p["n_modelled"] = int(p["n_modelled"] or 0)
        p["modelled_share"] = rnd(p["n_modelled"] / p["n_buildings"], 3) if p["n_buildings"] else 0.0
    write_geojson(out / "national" / "h3_r7.geojson", list(hexes.geom), props)
    years = PROCESSED_DIR / "demand_by_year.csv"
    if years.exists():  # back-cast 2016-2023 and projection to 2030 per district (rtl.demand.years)
        y = pd.read_csv(years)
        write_json(out / "national" / "demand_by_year.json", {
            "note": "GWh/yr per district. kind: backcast (building presence, census-scaled growth), base (2023), "
                    "projection (district growth, connection held at 2024).",
            "rows": [{k: (rnd(v, 3) if isinstance(v, float) else v) for k, v in r.items()}
                     for r in y[["district", "year", "kind", "potential_gwh", "expected_gwh", "growth_rate"]]
                     .to_dict("records")]})
    write_json(out / "national" / "climate_hourly.json",
               {r.h3_r7: [rnd(r[f"t2m_h{h:02d}_c"], 1) for h in HOURS] for _, r in hexes.iterrows()})
    if "t2m_m01_c" in hexes.columns:  # Copernicus build only
        write_json(out / "national" / "climate_monthly.json",
                   {r.h3_r7: {"t2m_c": [rnd(r[f"t2m_m{m:02d}_c"], 1) for m in range(1, 13)],
                              "precip_mm": [rnd(r[f"precip_m{m:02d}_mm"], 0) for m in range(1, 13)]}
                    for _, r in hexes.iterrows()})

    for level, keys in [("districts", ["adm1_name", "adm2_name"]),
                        ("sectors", ["adm1_name", "adm2_name", "adm3_name"])]:
        k = ", ".join(keys)
        tol = 0.001 if level == "districts" else 0.0005
        adm = con.sql(f"""
            WITH g AS (SELECT {k}, ST_AsGeoJSON(ST_ReducePrecision(ST_SimplifyPreserveTopology(ST_Union_Agg(geometry),
                              {tol}), 0.00001)) AS geom FROM '{I}/layers/admin_villages.parquet' GROUP BY ALL),
                 a AS (SELECT {', '.join(keys[1:])}, {aggs} FROM ({base}) GROUP BY ALL)
            SELECT g.*, a.* EXCLUDE ({', '.join(keys[1:])}) FROM g LEFT JOIN a USING ({', '.join(keys[1:])})
            ORDER BY {k}""").df()
        if level == "districts":
            adm = adm.merge(pua["district"].rename(columns={"district": "adm2_name"}), on="adm2_name", how="left")
            adm = adm.merge(district_extras(con), on="adm2_name", how="left")
        else:
            adm = adm.merge(pua["sector"].rename(columns={"district": "adm2_name", "sector": "adm3_name"}),
                            on=["adm2_name", "adm3_name"], how="left")
        cols = [c for c in adm.columns if c != "geom"]
        props = [{c: (rnd(r[c], 3) if isinstance(r[c], float) else r[c]) for c in cols} for _, r in adm.iterrows()]
        for p in props:
            p["n_buildings"], p["n_modelled"] = int(p["n_buildings"] or 0), int(p["n_modelled"] or 0)
        write_geojson(out / "admin" / f"{level}.geojson", list(adm.geom), props)


# ------------------------------------------------------------------------------------------------ profiles, evidence
def provenance(v: dict) -> dict:
    ids = v.get("evidence_ids") or []
    kind = ("survey" if any(i.startswith(("mtf:", "eicv5:", "dhs:")) for i in ids)
            else "measured" if any(i.startswith("anchor:") for i in ids)
            else "evidence" if ids and v.get("derivation") else "assumption")
    return {"type": kind, "ids": ids, "derivation": v.get("derivation"), "rationale": v.get("assumption")}


def profiles(out, cfg: dict, lib: pd.DataFrame) -> set[str]:
    src = pd.read_csv(PROCESSED_DIR / "archetypes" / "archetype_sources.csv").set_index("archetype_id")
    cited: set[str] = set()
    items = []
    derived = load_yaml("archetypes.yaml").get("derived", {})
    for pid, meta in cfg["profiles"].items():
        base_id = pid
        while base_id in derived and derived[base_id]["base"] != base_id:  # scaled copy: appliances of its base
            base_id = derived[base_id]["base"]
        arch = Archetype.load(PROCESSED_DIR / "archetypes" / f"{base_id}.json")
        r = lib.loc[pid]
        apps = []
        for a in arch.appliances:
            d = a.model_dump()
            fields = {"ownership_share": d["ownership_share"], "number": d["number"], "power_w": d["power"],
                      "minutes_per_day": d["func_time"], "windows_min": d["windows"]}
            for v in fields.values():
                cited.update(v.get("evidence_ids") or [])
            apps.append({"name": a.name, **{k: v["value"] for k, v in fields.items()},
                         "hours_per_day": rnd(a.func_time.value / 60, 2),
                         "wh_per_day_all_on": rnd(a.ownership_share.value * a.number.value * a.power.value
                                                  * a.func_time.value / 60, 1),
                         "ramp": {k: d[k]["value"] for k in ("func_cycle", "time_fraction_random_variability",
                                                             "random_var_w", "occasional_use", "wd_we_type")},
                         "provenance": {k: provenance(v) for k, v in fields.items()}})
        is_derived = pid in derived
        items.append({"profile_id": pid, "name": meta["name"], "group": meta["group"],
                      "status": (lib.at[pid, "status"] if is_derived else arch.status),
                      "description": " ".join(derived[pid]["why"].split()) + f" Appliances shown are {base_id}'s; "
                      "energy and curve are scaled." if is_derived else arch.description,
                      "population": arch.population, "year": arch.year,
                      "daily_kwh": rnd(r.daily_kwh_mean, 5), "daily_kwh_p10": rnd(r.daily_kwh_p10, 5),
                      "daily_kwh_p90": rnd(r.daily_kwh_p90, 5), "annual_kwh": rnd(r.daily_kwh_mean * 365, 1),
                      "coincident_peak_w_p50": rnd(r.coincident_peak_w_p50), "coincident_peak_w_p90":
                      rnd(r.coincident_peak_w_p90), "simulated_units": int(r.n_households),
                      "simulated_days": int(r.days),
                      "hourly_w": {"all": [rnd(v, 3) for v in curves(lib.loc[[pid]])[0]],
                                   "weekday": [rnd(v, 3) for v in curves(lib.loc[[pid]], "weekday_")[0]],
                                   "weekend": [rnd(v, 3) for v in curves(lib.loc[[pid]], "weekend_")[0]]},
                      "characteristics": characteristics(curves(lib.loc[[pid]])[0],
                                                         curves(lib.loc[[pid]], "weekday_")[0],
                                                         curves(lib.loc[[pid]], "weekend_")[0]),
                      "share_from_data": rnd(src.loc[base_id, "share_from_data"], 2), "appliances": apps})
    write_json(out / "profiles" / "profiles.json", {"profiles": items, "groups": cfg["profile_groups"],
                                                    "simulation": "RAMP 0.5.2 (rampdemand), 200 units x 28 days, "
                                                                  "per-unit means; seed fixed"})
    return cited


def evidence_index(out, cited: set[str]) -> None:
    """Every id an archetype cites -> what it points at (document quote, MTF aggregate row, measured anchor)."""
    from rtl.demand.review import load as load_evidence

    ev = load_evidence().set_index("evidence_id")
    idx = {}
    for i in sorted(cited):
        if i in ev.index:
            r = ev.loc[i]
            idx[i] = {"type": "evidence", "doc_id": r.doc_id, "page": r.page, "quote": r.quote, "value": r.value,
                      "unit": r.unit, "appliance": r.appliance, "family": r.family}
        elif i.startswith("mtf:"):
            idx[i] = {"type": "survey", "source": "World Bank MTF Rwanda 2022 (weighted aggregate)",
                      "table": "validation_anchors/mtf_*.csv", "row": i}
        elif i.startswith("anchor:"):
            doc, page, param = i.split(":")[1:4]
            idx[i] = {"type": "measured", "doc_id": doc, "page": int(page.lstrip("p")), "parameter": param}
        else:
            idx[i] = {"type": "other", "row": i}
    write_json(out / "profiles" / "evidence_index.json", idx)


# ------------------------------------------------------------------------------- validation, sources, workflow
def validation(out) -> None:
    from rtl.demand.checks import establishment_rows, household_rows

    lib = pd.read_parquet(PROCESSED_DIR / "profile_library.parquet")
    tiers, anchors = household_rows(lib)
    est = establishment_rows(lib)
    rec = json.loads((REPORTS_DIR / "labels" / "recommendation.json").read_text())
    flags = pd.read_csv(REPORTS_DIR / "review" / "validation_flags.csv")
    write_json(out / "validation" / "checks.json", {
        "household_tiers": tiers.round(2).to_dict("records"), "household_anchors": anchors.round(2).to_dict("records"),
        "establishments": est.round(1).to_dict("records"),
        "labels": {"labeler": rec["recommended"], "macro_f1": rnd(rec["macro_f1"], 3), "n_gold": rec["n_gold"],
                   "usd_per_1k_buildings": rnd(rec["usd_per_1k"], 2), "agreement_range": "about 39-43%",
                   "figure": "figures/labels_confusion.png"},
        "evidence": {"values_checked": int(len(pd.read_csv(REPORTS_DIR / "review" / "evidence_review.csv"))),
                     "values_flagged": int(flags.evidence_id.nunique()), "extracted_parameters": 3076,
                     "quotes_verified_share": 0.81},
        "verdict_rule": "ok within 1.5x of an anchor or inside its p25-p75 range; check within 3x; fail beyond",
        **constraint_checks()})


def constraint_checks() -> dict:
    """What the allocation and calibration impose, and how much the answer moves (sensitivity, uncertainty)."""
    P = PROCESSED_DIR  # noqa: N806
    out: dict = {}
    t = pd.read_csv(P / "allocation_targets.csv")
    g = t.groupby("item")[["target", "tagged", "assigned", "shortfall"]].sum().reset_index()
    out["allocation_targets"] = {"note": "national sums of district targets: census (households, pu:, svc:, retail:), "
                                         "registries (registry:), LLM rates (model:)",
                                 "rows": g.to_dict("records")}
    cal = P / "calibration.csv"
    if cal.exists():
        c = pd.read_csv(cal)
        d = c[c.level == "district"]
        out["calibration"] = {
            "grid": {"note": "households' P(connected) shifted per district (logit) to EICV7 2024 grid lighting",
                     "rows": [{"district": r.key, "eicv7_share": rnd(r.target, 3), "model_share": rnd(r.before, 3),
                               "logit_shift": rnd(r.factor, 2)} for r in d.itertuples()]},
            "household_energy": [{"scope": r.key, "item": r.item, "target_kwh_month": rnd(r.target, 2),
                                  "before_kwh_month": rnd(r.before, 2),
                                  "factor": None if pd.isna(r.factor) else rnd(r.factor, 3)}
                                 for r in c[c.level != "district"].itertuples()]}
    caps = REPORTS_DIR / "demand" / "cap_sensitivity.csv"
    if caps.exists():
        out["cap_sensitivity"] = {"note": "spacing caps x0.5 / base / x2 / x4 / none; district totals never change",
                                  "rows": pd.read_csv(caps).round(3).to_dict("records")}
    bm = P / "benchmarks.csv"
    if bm.exists():  # rtl.demand.benchmarks: the model against REG / RURA sales, customers and MoH facility counts
        out["benchmarks"] = {"note": "model / official; scopes differ (see each note)",
                             "rows": pd.read_csv(bm).round(3).replace({np.nan: None}).to_dict("records")}
    b = P / "uncertainty" / "sector_bands.csv"
    if b.exists():
        bands = pd.read_csv(b)
        nat = bands.drop(columns=["district", "sector"]).sum() / 1000
        out["uncertainty"] = {"note": "potential GWh/yr; allocation: 10 runs with class probabilities x "
                                      "exp(0.5 N(0,1)); profile: RAMP p10/p90 day; combined adds relative "
                                      "half-widths in quadrature",
                              "national_gwh": {k.removesuffix("_mwh"): rnd(v, 1) for k, v in nat.items()},
                              "sector_band_width_median": rnd(float(((bands.combined_p90_mwh - bands.combined_p10_mwh)
                                                                     / bands.base_mwh).median()), 3)}
    return out


def sources(out, cfg: dict) -> None:
    from rtl.manifest import read_manifest

    man = pd.DataFrame(read_manifest().values())
    s = load_yaml("sources.yaml")
    srcs = s["sources"] if isinstance(s, dict) and "sources" in s else s
    srcs = srcs if isinstance(srcs, list) else [dict(id=k, **v) for k, v in srcs.items()]
    rows = []
    for x in srcs:
        m = man[man.source_id == x["id"]] if "source_id" in man else man.iloc[0:0]
        rows.append({"id": x["id"], "name": x.get("name") or x.get("title"), "owner": x.get("owner"),
                     "role": x.get("role"), "access": x.get("access"), "licence": x.get("licence"),
                     "status": x.get("status"),
                     "url": (x.get("docs") or x.get("url")) if x.get("access") == "open" else None,
                     "files": len(m), "megabytes": rnd(m["bytes"].sum() / 1e6, 1) if "bytes" in m and len(m) else 0,
                     "citations": [cfg["citations"][c] for c in cfg["source_citations"].get(x["id"], [])]})
    docs = [{"doc_id": d["doc_id"], "title": d["title"], "publisher": d["publisher"], "year": d.get("year"),
             "url": d["url"], "licence": d.get("licence"), "status": d["status"], "topics": d.get("topics")}
            for d in load_yaml("evidence_docs.yaml")["documents"]]
    write_json(out / "sources" / "sources.json", {
        "datasets": rows, "evidence_documents": docs, "citations": cfg["citations"],
        "restricted_notice": ("Derived from restricted survey microdata (DHS 2025, World Bank MTF 2022, NISR EICV5) "
                              "under the providers' terms; only weighted aggregates are shown and cells with fewer "
                              "than 10 respondents are suppressed.")})


def count_rows(con, file: str | None):
    if not file:
        return None
    p = REPO_ROOT / file
    if p.is_dir():
        return len([f for f in p.iterdir() if f.suffix in (".json", ".csv") and not f.name.endswith(".schema.json")])
    return con.sql(f"SELECT count(*) FROM '{p}'").fetchone()[0] if p.exists() else None


def workflow(con, out, cfg: dict, src_status: dict) -> None:
    wf = cfg["workflow"]
    nodes = []
    for n in wf["nodes"]:
        n = dict(n)
        n.setdefault("status", "built")  # per-node status lives in config/dashboard.yaml
        if n["kind"] == "source":
            n["status"] = {"verified": "built"}.get(src_status.get(n.get("source")), "built")
            n["citations"] = [cfg["citations"][c] for c in cfg["source_citations"].get(n.get("source"), [])]
        if n["kind"] == "table":
            n["records"] = count_rows(con, n.get("file"))
        nodes.append(n)
    edges = [{"from": a, "to": b} for a, b in wf["edges"]]
    write_json(out / "workflow" / "workflow.json", {"lanes": wf["lanes"], "nodes": nodes, "edges": edges})
    shape = {"source": ('["', '"]'), "process": ('["', '"]'), "table": ('[("', '")]'), "gate": ('{{"', '"}}'),
             "output": ('(["', '"])')}
    lines = ["flowchart LR"]
    for lane in wf["lanes"]:
        lines.append(f'  subgraph {lane["id"]}["{lane["label"]}"]')
        for n in (n for n in nodes if n["lane"] == lane["id"]):
            o, c = shape[n["kind"]]
            label = n["label"].replace('"', "'") + (" (restricted)" if n.get("restricted") else "")
            lines.append(f"    {n['id']}{o}{label}{c}")
        lines.append("  end")
    lines += [f"  {e['from']} --> {e['to']}" for e in edges]
    (out / "workflow" / "workflow.mmd").write_text("\n".join(lines) + "\n")


def national_demand() -> dict:
    """Headline national totals from the processed outputs."""
    P = PROCESSED_DIR  # noqa: N806
    pv = pd.read_parquet(P / "building_demand_preview.parquet", columns=["annual_kwh", "expected_annual_kwh"])
    u = pd.read_parquet(P / "building_use.parquet", columns=["households", "establishment", "est_weight"])
    out = {"buildings_with_demand": int(len(pv)), "potential_gwh": rnd(pv.annual_kwh.sum() / 1e6, 1),
           "expected_gwh": rnd(pv.expected_annual_kwh.sum() / 1e6, 1), "households": int(u.households.sum()),
           "establishments_by_profile": {k: rnd(v, 1) for k, v in
                                         u.groupby("establishment").est_weight.sum().sort_values(ascending=False).items()}}
    y = P / "demand_by_year.csv"
    if y.exists():
        g = pd.read_csv(y).groupby("year")[["potential_gwh", "expected_gwh"]].sum()
        out["by_year"] = [{"year": int(k), "potential_gwh": rnd(r.potential_gwh, 1),
                           "expected_gwh": rnd(r.expected_gwh, 1)} for k, r in g.iterrows()]
    return out


def meta(out, cfg: dict, summaries: dict) -> None:
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True,
                            text=True).stdout.strip()
    aoi = load_yaml("aoi.yaml")
    write_json(out / "meta.json", {
        "title": "Rooftops to Load Curves: building-level electricity demand for Rwanda",
        "version": cfg["version"], "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "git_commit": commit, "crs": "EPSG:4326 (lon, lat); metrics computed in EPSG:32735",
        "time": "local time, UTC+2; hours 0-23 are hour-starting bins",
        "nulls": "null means not observed or not modelled, never zero",
        "units": {"power": "W (buildings), kW (areas)", "energy": "kWh/yr (buildings), MWh/yr (areas)",
                  "temperature": "degC", "cdd": "degC-days per year", "ghi": "kWh/m2/day", "distance": "m"},
        "national": {"buildings": 6434247, "buildings_with_height": 4603981, "climate_cells_h3_r7": 4034,
                     "fixture_bbox": aoi["fixture"]["bbox"], **national_demand()},
        "classes": cfg["classes"], "cooling": cfg["cooling"], "profile_groups": cfg["profile_groups"],
        "profiles": cfg["profiles"], "metrics": cfg["metrics"], "status": cfg["status"],
        "productive_use": cfg["productive_use"] | {
            "weights": load_yaml("productive_use.yaml")["candidate_score"],
            "groups": {"town": list(pu.TOWN_TRADES), "grain_mill": ["grain_mill"],
                       "workshop_rural": ["workshop_rural"]},
            "trade_mix": load_yaml("productive_use.yaml")["trade_mix"],
            "field_kinds": PU_FIELD_KINDS},
        "deep_dives": [{k: v for k, v in s.items() if k in ("id", "district", "sector", "description", "aoi_key",
                                                              "n_buildings", "n_modelled", "bbox")}
                       for s in summaries.values()],
        "attribution": ("© OpenStreetMap contributors · Google Open Buildings · Microsoft Building Footprints · "
                        "Overture Maps Foundation · GHSL (EC JRC) · WorldPop · Meta Data for Good · Copernicus · "
                        "NASA POWER · Gridfinder"),
        "licence_notes": ("Meta RWI is CC-BY-NC-4.0: the bundle is for non-commercial use unless RWI fields are "
                          "dropped. Footprints are ODbL-1.0 (share-alike for derived databases).")})


def bundle_manifest(out, cfg: dict) -> None:
    files = []
    for p in sorted(out.rglob("*")):
        if p.is_file() and p.name != "manifest.json":
            data = p.read_bytes()
            rec = None
            if p.suffix == ".geojson":
                rec = data.count(b'"type":"Feature",')
            files.append({"path": str(p.relative_to(out)), "bytes": len(data),
                          "sha256": hashlib.sha256(data).hexdigest(), "features": rec})
    write_json(out / "manifest.json", {"version": cfg["version"], "files": files,
                                       "total_bytes": sum(f["bytes"] for f in files)})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sample", action="store_true", help="small committed sample (one box per deep dive)")
    args = ap.parse_args()
    cfg = load_yaml("dashboard.yaml")
    out = (SAMPLE_OUT if args.sample else FULL_OUT) / cfg["version"]
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    con = connect()
    lib = library()
    wtop = pd.read_parquet(PROCESSED_DIR / "building_profiles.parquet")  # allocation is discrete: same weights
    national(con, out, lib)
    summaries = {}
    for dd_id, dd in cfg["deep_dives"].items():
        summaries[dd_id] = export_deep_dive(con, out, dd_id, dd, cfg, lib, wtop,
                                            bbox=cfg["sample"]["bboxes"][dd_id] if args.sample else None)
    evidence_index(out, profiles(out, cfg, lib))
    validation(out)
    sources(out, cfg)
    s = load_yaml("sources.yaml")
    srcs = s["sources"] if isinstance(s, dict) and "sources" in s else s
    srcs = srcs if isinstance(srcs, list) else [dict(id=k, **v) for k, v in srcs.items()]
    workflow(con, out, cfg, {x["id"]: x.get("status") for x in srcs})
    meta(out, cfg, summaries)
    for f in FIGURES:
        if (REPORTS_DIR / "figures" / f).exists():
            (out / "figures").mkdir(exist_ok=True)
            shutil.copy2(REPORTS_DIR / "figures" / f, out / "figures" / f)
    bundle_manifest(out, cfg)
    man = json.loads((out / "manifest.json").read_text())
    print(f"{len(man['files'])} files, {man['total_bytes'] / 1e6:.1f} MB -> {out}")
    for f in man["files"]:
        if "/details/" not in f["path"]:
            extra = f"  ({f['features']:,} features)" if f["features"] else ""
            print(f"  {f['bytes'] / 1e6:7.2f} MB  {f['path']}{extra}")
    shards = pd.DataFrame([f for f in man["files"] if "/details/" in f["path"]])
    if len(shards):
        shards["dir"] = shards.path.str.rsplit("/", n=1).str[0]
        for d, g in shards.groupby("dir"):
            print(f"  {g.bytes.sum() / 1e6:7.2f} MB  {d}/ ({len(g)} shards, max {g.bytes.max() / 1e3:.0f} kB)")


if __name__ == "__main__":
    main()
