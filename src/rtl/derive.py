"""Cheap derived columns, re-computable from config/params.yaml without re-sampling rasters or calling an API.

    pixi run derive              # cooling class, storey calibration, floors, GFA, first-seen year
    pixi run derive --presence   # also (re)sample presence_2016..2023 from the cached local 2.5D tiles (minutes)

- cooling_class (climate_h3): relative "fans plausible on the hottest days" proxy from tmax_p95_c.
- storey_m: if params say `auto`, calibrated on half of the OSM buildings with building:levels and validated on
  the other half; est_floors and gfa_m2 are recomputed from the stored height_m.
- first_seen_year: re-derived from the stored presence columns at params.height.presence_threshold.
"""

from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from rtl.settings import INTERIM_DIR, REPORTS_DIR, params

HEIGHT = INTERIM_DIR / "building_height.parquet"
CLIMATE = INTERIM_DIR / "climate_h3.parquet"
PRESENCE_YEARS = list(range(2016, 2024))


def cooling_class(tmax_p95_c: pd.Series) -> pd.Series:
    c = params()["cooling"]
    return pd.Series(np.select([tmax_p95_c < c["low_below_c"], tmax_p95_c >= c["high_from_c"]],
                               ["low", "high"], default="medium"), index=tmax_p95_c.index)


STOREY_GRID = np.arange(2.5, 6.01, 0.1)


def calibrate_storey(m: pd.DataFrame, grid: np.ndarray = STOREY_GRID) -> dict:
    """Pick the storey height on even OSM ids, report it on odd ids (held out).

    The objective is the mean of per-group MAEs over OSM levels {1, 2, 3+}. Plain MAE would be dominated by the
    ~90% single-storey buildings and reward calling everything one storey, which erases real multi-storey stock.
    """
    from rtl.conform.height import est_floors

    m = m.dropna(subset=["osm_levels", "height_m"]).assign(grp=lambda d: d.osm_levels.clip(upper=3).round())
    fit, test = m[m.oid % 2 == 0], m[m.oid % 2 == 1]

    def err(df, s):
        e = (est_floors(df.height_m, s).astype(float) - df.osm_levels).abs()
        return float(e.groupby(df.grp).mean().mean())

    best = float(min(grid, key=lambda s: err(fit, s)))
    f = est_floors(test.height_m, best).astype(float)
    return {"storey_m": round(best, 2), "objective": "mean of per-group MAE, OSM levels 1/2/3+",
            "n_fit": len(fit), "n_test": len(test),
            "test_group_mae": round(err(test, best), 3), "test_group_mae_at_3m": round(err(test, 3.0), 3),
            "test_mae_by_levels": {str(int(k)): round(float(v), 3)
                                   for k, v in (f - test.osm_levels).abs().groupby(test.grp).mean().items()},
            "test_exact_share": round(float((f == test.osm_levels).mean()), 3),
            "n_test_by_levels": {str(int(k)): int(v) for k, v in test.grp.value_counts().sort_index().items()}}


def sample_presence() -> pd.DataFrame:
    """Centroid presence for every year from the already-downloaded local tiles (no network)."""
    import duckdb

    from rtl.conform.buildings import OUT as BASE
    from rtl.conform.height import sample_points, tiles_of
    from rtl.ingest.open_buildings_25d import cfg

    pts = duckdb.sql(f"SELECT bldg_id, lon, lat FROM read_parquet('{BASE}')").df()
    res = cfg()["presence_res_m"]
    for y in PRESENCE_YEARS:
        v = sample_points(tiles_of("presence", y, res, "national")[0], pts.lon.to_numpy(), pts.lat.to_numpy())
        pts[f"presence_{y}"] = np.clip(v, 0, 1).astype("float64")  # NaN (no tile / nodata) stays NaN
    return pts.drop(columns=["lon", "lat"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--presence", action="store_true", help="re-sample presence columns from local tiles")
    args = ap.parse_args()
    report: dict = {}

    # climate: cooling class
    clim = pd.read_parquet(CLIMATE)
    clim["cooling_class"] = cooling_class(clim["tmax_p95_c"])
    clim.to_parquet(CLIMATE, index=False)
    report["cooling_class_cells"] = clim.cooling_class.value_counts().to_dict()

    # height: presence, storey calibration, floors, GFA, first seen
    import duckdb

    from rtl.conform.buildings import OUT as BASE
    from rtl.conform.height import est_floors, first_seen_year, gfa_m2
    from rtl.conform.height_report import osm_validation
    from rtl.locks import heavy_job

    with heavy_job("derive"):
        h = pd.read_parquet(HEIGHT)
        if args.presence or "presence_2023" not in h.columns:
            h = h.drop(columns=[c for c in h.columns if c.startswith("presence_")]).merge(
                sample_presence(), on="bldg_id", how="left", validate="1:1")
        storey = params()["height"]["storey_m"]
        if storey == "auto":
            con = duckdb.connect()
            con.sql("LOAD spatial;")
            _, m = osm_validation(con)
            report["storey_calibration"] = calibrate_storey(m)
            storey = report["storey_calibration"]["storey_m"]
        area = duckdb.sql(f"SELECT bldg_id, area_m2 FROM read_parquet('{BASE}')").df()
        h = h.drop(columns=["area_m2"], errors="ignore").merge(area, on="bldg_id", how="left", validate="1:1")
        h["est_floors"] = est_floors(h["height_m"], storey)
        h["gfa_m2"] = gfa_m2(h["area_m2"], h["est_floors"])
        thr = float(params()["height"]["presence_threshold"])
        h["first_seen_year"] = first_seen_year(h[[f"presence_{y}" for y in PRESENCE_YEARS]].to_numpy(),
                                               PRESENCE_YEARS, thr).set_axis(h.index)
        h = h.drop(columns=["area_m2"])
        pres = [f"presence_{y}" for y in PRESENCE_YEARS]
        h[pres] = h[pres].astype("float64")  # contract dtype; tiles are float32
        h.to_parquet(HEIGHT, index=False)
    report.update({"storey_m_used": storey, "presence_threshold": thr,
                   "floors_counts": {str(k): int(v)
                                     for k, v in h.est_floors.value_counts().sort_index().head(6).items()},
                   "first_seen_nonnull_share": round(float(h.first_seen_year.notna().mean()), 4)})
    out = REPORTS_DIR / "qa" / "derive.json"
    out.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
