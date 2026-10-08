"""A1: evidence for building_height: OSM height validation, regional distributions, growth curve, figures.

OSM `building:levels` / `height` tags are an independent check (mapped by people, not by the 2.5D model).
A footprint is matched to an OSM polygon when its centroid lies inside it (VIDA already contains many OSM
footprints, so this is usually the same building). OSM is read-only here: A2v owns OSM processing.
"""

from __future__ import annotations

import json
import re

import duckdb
import matplotlib
import numpy as np
import pandas as pd
import pyogrio

from rtl.conform.buildings import OUT as BASE
from rtl.conform.height import OUT, STOREY_M
from rtl.manifest import read_manifest
from rtl.settings import REPO_ROOT, REPORTS_DIR

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# Rough deep-dive envelopes until A2v delivers admin polygons (aoi.yaml names the sectors).
REGIONS = {
    "kigali_city": [29.98, -2.06, 30.26, -1.88],
    "musanze_town": [29.58, -1.53, 29.66, -1.47],
    "kirehe_district": [30.40, -2.45, 30.90, -2.05],
}
BLUE, ORANGE, AQUA, INK, MUTED = "#2a78d6", "#eb6834", "#1baf7a", "#0b0b0b", "#52514e"
FIG_DIR = REPORTS_DIR / "figures"


def parse_osm_number(v: str | None) -> float:
    """First number in an OSM tag value ("12.5 m" -> 12.5, "2;3" -> 2, "4,5" -> 4.5); NaN if none."""
    if v is None:
        return np.nan
    m = re.search(r"\d+(?:[.,]\d+)?", str(v))
    return float(m.group().replace(",", ".")) if m else np.nan


def osm_tagged_buildings() -> pd.DataFrame:
    entries = [e for e in read_manifest().values() if e["source_id"] == "osm_geofabrik"]
    pbf = REPO_ROOT / max(entries, key=lambda e: e["version"])["path"]
    df = pyogrio.read_dataframe(pbf, layer="multipolygons", columns=["osm_id", "osm_way_id", "building", "other_tags"],
                                where="building IS NOT NULL AND (other_tags LIKE '%building:levels%' "
                                      "OR other_tags LIKE '%\"height\"%')")
    tag = lambda k: df["other_tags"].str.extract(rf'"{re.escape(k)}"=>"([^"]*)"')[0]  # noqa: E731
    df["osm_levels"] = tag("building:levels").map(parse_osm_number)
    df["osm_height_m"] = tag("height").map(parse_osm_number)
    df = df[(df["osm_levels"].between(1, 40)) | (df["osm_height_m"].between(2, 150))]
    return pd.DataFrame({"osm_levels": df["osm_levels"], "osm_height_m": df["osm_height_m"],
                         "wkb": df.geometry.to_wkb()})


def agreement(est: pd.Series, ref: pd.Series) -> dict:
    d = (est - ref).dropna()
    return {"n": int(len(d)), "bias": round(float(d.mean()), 2), "mae": round(float(d.abs().mean()), 2),
            "median_abs_err": round(float(d.abs().median()), 2),
            "pearson_r": round(float(np.corrcoef(est[d.index], ref[d.index])[0, 1]), 3) if len(d) > 2 else None}


def osm_validation(con: duckdb.DuckDBPyConnection) -> tuple[dict, pd.DataFrame]:
    osm = osm_tagged_buildings()
    con.register("osm_df", osm)
    m = con.sql(f"""
        WITH o AS (SELECT row_number() OVER () AS oid, osm_levels, osm_height_m, ST_GeomFromWKB(wkb) AS g
                   FROM osm_df),
             b AS (SELECT b.bldg_id, b.area_m2, ST_Point(b.lon, b.lat) AS p, h.height_m, h.est_floors
                   FROM read_parquet('{BASE}') b JOIN read_parquet('{OUT}') h USING (bldg_id))
        SELECT o.oid, o.osm_levels, o.osm_height_m, b.height_m, b.est_floors, b.area_m2
        FROM o JOIN b ON ST_Contains(o.g, b.p)
        -- several footprints can sit in one OSM polygon: compare the largest
        QUALIFY row_number() OVER (PARTITION BY o.oid ORDER BY b.area_m2 DESC) = 1
    """).df()
    lv = m.dropna(subset=["osm_levels"])
    floors_ok = lv.dropna(subset=["est_floors"])
    stats = {
        "osm_tagged_polygons": len(osm), "matched": len(m),
        "matched_with_25d_height": int(m["height_m"].notna().sum()),
        "floors_vs_osm_levels": agreement(floors_ok["est_floors"].astype(float), floors_ok["osm_levels"]),
        "floors_exact_share": round(float((floors_ok["est_floors"] == floors_ok["osm_levels"]).mean()), 3),
        "floors_within_1_share": round(float(((floors_ok["est_floors"] - floors_ok["osm_levels"]).abs() <= 1)
                                             .mean()), 3),
        "height_vs_osm_height_tag": agreement(m["height_m"], m["osm_height_m"]),
        "height_vs_osm_levels_x_storey": agreement(lv["height_m"], lv["osm_levels"] * STOREY_M),
        "implied_storey_m_median": round(float((lv["height_m"] / lv["osm_levels"]).median()), 2),
        "osm_levels_distribution": {int(k): int(v) for k, v in lv["osm_levels"].round().value_counts()
                                    .sort_index().items()},
    }
    return stats, m


def regional(con: duckdb.DuckDBPyConnection) -> dict:
    out = {}
    for name, (x0, y0, x1, y1) in REGIONS.items():
        r = con.sql(f"""
            SELECT count(*) AS n, avg((height_m IS NOT NULL)::int) AS coverage,
                   quantile_cont(height_m, [0.1, 0.5, 0.9, 0.99]) AS q,
                   avg((est_floors >= 2)::int) FILTER (WHERE est_floors IS NOT NULL) AS share_multistorey,
                   avg((first_seen_year > 2016)::int) FILTER (WHERE first_seen_year IS NOT NULL) AS new_17,
                   avg((first_seen_year IS NULL)::int) AS share_no_first_seen
            FROM read_parquet('{BASE}') b JOIN read_parquet('{OUT}') h USING (bldg_id)
            WHERE lon BETWEEN {x0} AND {x1} AND lat BETWEEN {y0} AND {y1}""").fetchone()
        out[name] = {"bbox": [x0, y0, x1, y1], "n": r[0], "height_coverage": round(r[1], 3),
                     "height_q10_q50_q90_q99": [round(v, 1) for v in r[2]], "share_multistorey": round(r[3], 3),
                     "share_first_seen_after_2016": round(r[4], 3), "share_no_first_seen": round(r[5], 3)}
    return out


def _style(ax):
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.grid(axis="y", color="#e6e5e0", linewidth=0.8)
    ax.set_axisbelow(True)


def plot_growth(conform_stats: dict) -> None:
    years = [str(y) for y in range(2016, 2024)]
    fs = conform_stats["first_seen_counts"]
    fig, ax = plt.subplots(figsize=(8, 4.2))
    base = [fs["thr_0.5"].get(y, 0) / 1e3 for y in years]
    ax.bar(years, base, color=BLUE, width=0.6, label="threshold 0.5 (used)")
    for thr, c, mk in (("0.3", ORANGE, "o"), ("0.7", AQUA, "s")):
        ax.plot(years, [fs[f"thr_{thr}"].get(y, 0) / 1e3 for y in years], color=c, lw=2, marker=mk, ms=8,
                label=f"threshold {thr}")
    ax.set_yscale("log")
    ax.set_ylabel("buildings (thousands, log scale)", color=MUTED)
    ax.set_title("Rwanda buildings by first year stably detected (2016 = 2016 or earlier)", color=INK,
                 fontsize=11, loc="left")
    _style(ax)
    ax.legend(frameon=False, fontsize=9)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "A1_growth.png", dpi=150)
    plt.close(fig)


def plot_osm(m: pd.DataFrame) -> None:
    fig, (a, b) = plt.subplots(1, 2, figsize=(10, 4.4))
    lv = m.dropna(subset=["osm_levels", "height_m"])
    jitter = np.random.default_rng(0).uniform(-0.15, 0.15, len(lv))
    a.scatter(lv["osm_levels"] + jitter, lv["height_m"], s=10, color=BLUE, alpha=0.35, linewidths=0)
    xs = np.arange(1, max(2, lv["osm_levels"].max()) + 1)
    a.plot(xs, xs * STOREY_M, color=MUTED, lw=1.5, ls="--", label=f"{STOREY_M:g} m per storey")
    a.set_xlabel("OSM building:levels", color=MUTED)
    a.set_ylabel("2.5D height 2023, footprint median (m)", color=MUTED)
    a.set_title(f"vs OSM levels (n={len(lv):,})", color=INK, fontsize=10, loc="left")
    a.legend(frameon=False, fontsize=9)
    ht = m.dropna(subset=["osm_height_m", "height_m"])
    b.scatter(ht["osm_height_m"], ht["height_m"], s=12, color=BLUE, alpha=0.5, linewidths=0)
    lim = max(10, float(np.nanmax(ht[["osm_height_m", "height_m"]].to_numpy(), initial=10)))
    b.plot([0, lim], [0, lim], color=MUTED, lw=1.5, ls="--", label="1:1")
    b.set_xlabel("OSM height tag (m)", color=MUTED)
    b.set_title(f"vs OSM height (n={len(ht):,})", color=INK, fontsize=10, loc="left")
    b.legend(frameon=False, fontsize=9)
    for ax in (a, b):
        _style(ax)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "A1_height_vs_osm.png", dpi=150)
    plt.close(fig)


def main() -> None:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.sql("LOAD spatial; SET memory_limit='6GB';")
    conform_stats = json.loads((REPORTS_DIR / "qa" / "conform_height.json").read_text())
    osm_stats, matched = osm_validation(con)
    report = {"osm_validation": osm_stats, "regions": regional(con),
              "growth_thr_0.5": conform_stats["first_seen_counts"]["thr_0.5"]}
    plot_growth(conform_stats)
    plot_osm(matched)
    (REPORTS_DIR / "qa" / "height_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
