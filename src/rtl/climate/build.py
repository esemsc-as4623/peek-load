"""A3: build data/interim/climate_h3.parquet (one row per H3 res-7 cell that contains a building).

Steps
  1. load the raw ERA5-Land grid (Open-Meteo, UTC) into arrays (node, hour) + each node's model elevation
  2. shift to local time (CAT = UTC+2) and cut into whole local days 2019-01-01 .. 2024-12-31
  3. estimate the lapse rate gamma by regressing node mean temperature on node elevation
  4. target cells = res-7 parents of every building's h3_r9; cell elevation = mean of the 30 m DEM over the cell
  5. per cell: bilinear-interpolate the daily mean / daily max / typical-day series and the grid elevation
     to the cell centroid, add gamma * (DEM elevation - interpolated grid elevation), *then* compute
     degree-days and p95 (thresholds are non-linear, so the correction must come before them)
  6. GHI: multi-year mean of NASA POWER daily ALLSKY_SFC_SW_DWN, bilinear from its 1 deg grid (no correction)
  7. write, plus diagnostics (lapse rate, NASA POWER cross-check, town sanity checks) and quick-look maps
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import geopandas as gpd
import h3
import numpy as np
import pandas as pd
import shapely
from exactextract import exact_extract

from rtl.climate.downscale import bilinear_weights, fit_lapse_rate, interpolate, lapse_shift
from rtl.climate.stats import CAT_OFFSET_H, degree_days_per_year, summarise, to_kwh_m2_day, to_local_days
from rtl.ingest.power import TOWNS
from rtl.locks import heavy_job
from rtl.manifest import read_manifest
from rtl.schemas import climate_h3
from rtl.settings import INTERIM_DIR, REPO_ROOT, REPORTS_DIR

FIRST_DAY, LAST_DAY = "2019-01-01", "2024-12-31"
YEARS = "2019-2024"
DEM = INTERIM_DIR / "dem_rwa_30m.tif"
BUILDINGS = INTERIM_DIR / "buildings_base.parquet"
OUT = INTERIM_DIR / "climate_h3.parquet"
DIAG = INTERIM_DIR / "climate_h3_diagnostics.json"
FIG_DIR = REPORTS_DIR / "figures"
HOURLY = [f"t2m_h{h:02d}_c" for h in range(24)]


def raw_files(source_id: str, pattern: str) -> list[Path]:
    """Latest-version raw files of a source, as recorded in the manifest (provenance, not a directory glob)."""
    entries = [e for e in read_manifest().values() if e["source_id"] == source_id and pattern in e["path"]]
    if not entries:
        raise FileNotFoundError(f"no {source_id} files in the manifest")
    latest = max(e["path"].split("/")[3] for e in entries)
    return sorted(REPO_ROOT / e["path"] for e in entries if e["path"].split("/")[3] == latest)


# ------------------------------------------------------------------------------------------- grid
def load_grid() -> dict:
    """ERA5-Land nodes as a regular (lat, lon) grid: t[node, hour] in UTC, flattened row-major."""
    frames = [pd.read_parquet(p) for p in raw_files("openmeteo", "t2m_era5land_batch_")]
    df = pd.concat(frames, ignore_index=True)
    # returned node coordinates must be the requested 0.1 deg nodes (cell_selection=nearest)
    assert (df.lat - df.req_lat).abs().max() < 0.01 and (df.lon - df.req_lon).abs().max() < 0.01
    times = pd.DatetimeIndex(np.sort(df.time_utc.unique()))
    lats, lons = np.sort(df.req_lat.unique()), np.sort(df.req_lon.unique())
    wide = df.pivot(index=["req_lat", "req_lon"], columns="time_utc", values="t2m_c")
    full = pd.MultiIndex.from_product([lats, lons], names=["req_lat", "req_lon"])
    wide = wide.reindex(index=full, columns=times)
    if wide.isna().any().any():
        raise ValueError(f"grid incomplete: {int(wide.isna().any(axis=1).sum())} nodes with missing hours")
    elev = df.groupby(["req_lat", "req_lon"]).elevation_m.first().reindex(full).to_numpy()
    return {"lat": lats, "lon": lons, "t": wide.to_numpy(np.float32), "times": times, "elev": elev}


# ------------------------------------------------------------------------------------------- cells
def target_cells(buildings: Path) -> pd.DataFrame:
    """Res-7 parents of all building h3_r9 values, with building counts (used only for diagnostics)."""
    con = duckdb.connect()
    con.sql("SET memory_limit='3GB'; SET threads=2;")
    r9 = con.sql(f"SELECT h3_r9, count(*) AS n FROM read_parquet('{buildings}') GROUP BY 1").df()
    r9["h3_r7"] = [h3.cell_to_parent(c, 7) for c in r9.h3_r9]
    cells = r9.groupby("h3_r7", as_index=False).n.sum().rename(columns={"n": "n_buildings"})
    latlng = np.array([h3.cell_to_latlng(c) for c in cells.h3_r7])
    cells["lat"], cells["lon"] = latlng[:, 0], latlng[:, 1]
    return cells


def cell_polygons(cells: pd.DataFrame) -> gpd.GeoDataFrame:
    polys = [shapely.Polygon([(lng, lat) for lat, lng in h3.cell_to_boundary(c)]) for c in cells.h3_r7]
    return gpd.GeoDataFrame(cells, geometry=polys, crs="EPSG:4326")


def dem_mean(gdf: gpd.GeoDataFrame) -> np.ndarray:
    res = exact_extract(str(DEM), gdf[["h3_r7", "geometry"]], "mean", include_cols=["h3_r7"], output="pandas")
    return res.set_index("h3_r7").reindex(gdf.h3_r7)["mean"].to_numpy(float)


# ------------------------------------------------------------------------------------------- GHI
def load_ghi() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Multi-year mean daily GHI (kWh/m2/day) on the NASA POWER 1 deg grid: (lat, lon, values[lat, lon])."""
    vals: dict[tuple[float, float], list[float]] = {}
    for p in raw_files("nasa_power", "ghi_daily_"):
        d = json.loads(p.read_text())
        units = d["parameters"]["ALLSKY_SFC_SW_DWN"]["units"]
        fill = d["header"]["fill_value"]
        for f in d["features"]:
            lon, lat = f["geometry"]["coordinates"][:2]
            v = np.array(list(f["properties"]["parameter"]["ALLSKY_SFC_SW_DWN"].values()), float)
            vals.setdefault((lat, lon), []).extend(to_kwh_m2_day(v[v != fill], units))
    lats, lons = sorted({k[0] for k in vals}), sorted({k[1] for k in vals})
    grid = np.array([[np.mean(vals[(la, lo)]) for lo in lons] for la in lats])
    return np.array(lats), np.array(lons), grid


# ------------------------------------------------------------------------------------------- main
def build(buildings: Path, out: Path) -> dict:
    g = load_grid()
    t_days, days = to_local_days(g["t"], g["times"], FIRST_DAY, LAST_DAY)
    node = summarise(t_days, days)
    gamma, intercept, r2 = fit_lapse_rate(g["elev"], node["t2m_mean_c"])
    gamma_hour = [fit_lapse_rate(g["elev"], node["typical_day"][:, h])[0] * 1000 for h in range(24)]
    gamma_tmax = fit_lapse_rate(g["elev"], node["tmax_p95_c"])[0] * 1000

    cells = target_cells(buildings)
    gdf = cell_polygons(cells)
    elev = dem_mean(gdf)
    idx, w = bilinear_weights(cells.lat.to_numpy(), cells.lon.to_numpy(), g["lat"], g["lon"])
    grid_elev = interpolate(g["elev"], idx, w)
    no_dem = np.isnan(elev)
    elev = np.where(no_dem, grid_elev, elev)  # outside the DEM extent: keep the grid elevation (no shift)
    shift = lapse_shift(gamma, elev, grid_elev)

    # interpolate the daily and typical-day series, apply the shift, then compute the statistics
    daily_mean = interpolate(t_days.mean(axis=-1), idx, w) + shift[:, None]
    daily_max = interpolate(t_days.max(axis=-1), idx, w) + shift[:, None]
    typical = interpolate(node["typical_day"], idx, w) + shift[:, None]
    glat, glon, ghi_grid = load_ghi()
    gidx, gw = bilinear_weights(cells.lat.to_numpy(), cells.lon.to_numpy(), glat, glon)

    df = pd.DataFrame({
        "h3_r7": cells.h3_r7,
        "elevation_m": elev,
        "t2m_mean_c": daily_mean.mean(axis=1),
        "tmax_p95_c": np.percentile(daily_max, 95, axis=1),
        "cdd18_per_year": degree_days_per_year(daily_mean, days, 18.0),
        "cdd22_per_year": degree_days_per_year(daily_mean, days, 22.0),
        "cdd24_per_year": degree_days_per_year(daily_mean, days, 24.0),
        "ghi_kwh_m2_day": interpolate(ghi_grid.ravel(), gidx, gw),
        "years": YEARS,
        "source": (f"t2m: ERA5-Land hourly 0.1deg via Open-Meteo archive API (raw grid, no API downscaling), "
                   f"bilinear to cell centroid + lapse rate {gamma * 1000:.2f} K/km (fit on grid) to Copernicus "
                   f"GLO-30 cell-mean elevation; local time UTC+{CAT_OFFSET_H}; "
                   "GHI: NASA POWER daily ALLSKY_SFC_SW_DWN (CERES SYN1deg 1deg), bilinear"),
        **{c: typical[:, h] for h, c in enumerate(HOURLY)},
    })
    from rtl.derive import cooling_class  # thresholds live in config/params.yaml
    df["cooling_class"] = cooling_class(df["tmax_p95_c"])
    climate_h3.validate(df, lazy=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)

    diag = {
        "rows": len(df), "grid_nodes": int(g["elev"].size), "days": len(days),
        "lapse_rate_k_per_km": round(gamma * 1000, 3), "lapse_intercept_c": round(intercept, 2),
        "lapse_r2": round(r2, 3), "lapse_rate_tmax_p95_k_per_km": round(gamma_tmax, 3),
        "lapse_rate_by_local_hour_k_per_km": [round(x, 2) for x in gamma_hour],
        "cells_without_dem": int(no_dem.sum()),
        "abs_shift_c": {"median": round(float(np.median(np.abs(shift))), 2),
                        "p95": round(float(np.percentile(np.abs(shift), 95)), 2)},
        "towns": town_checks(df, cells, g, t_days, gamma),
        "building_weighted": building_weighted(df, cells.n_buildings.to_numpy()),
    }
    return diag | {"_df": df, "_gdf": gdf}


def town_checks(df: pd.DataFrame, cells: pd.DataFrame, g: dict, t_days: np.ndarray, gamma: float) -> dict:
    """Climate at each reference town's res-7 cell, and NASA POWER (MERRA-2) vs ERA5-Land at the town point."""
    power = {p.stem.removeprefix("t2m_hourly_"): p for p in raw_files("nasa_power", "t2m_hourly_")}
    out = {}
    for town, (lat, lon) in TOWNS.items():
        row = df[df.h3_r7 == h3.latlng_to_cell(lat, lon, 7)]
        res = {"cell_present": len(row) == 1}
        if len(row) == 1:
            r = row.iloc[0]
            res |= {k: round(float(r[k]), 2) for k in
                    ["elevation_m", "t2m_mean_c", "tmax_p95_c", "cdd18_per_year", "cdd22_per_year", "cdd24_per_year",
                     "ghi_kwh_m2_day"]}
        # cross-check on raw series (both in UTC hours, point-interpolated, no elevation correction)
        idx, w = bilinear_weights(np.array([lat]), np.array([lon]), g["lat"], g["lon"])
        era = interpolate(g["t"], idx, w)[0]
        d = json.loads(power[town].read_text())
        p = np.array(list(d["properties"]["parameter"]["T2M"].values()), float)
        p_times = pd.to_datetime(list(d["properties"]["parameter"]["T2M"].keys()), format="%Y%m%d%H", utc=True)
        era_s = pd.Series(era, index=g["times"]).reindex(p_times)
        ok = (p != d["header"]["fill_value"]) & era_s.notna().to_numpy()
        res["crosscheck_power_vs_era5land"] = {
            "power_elev_m": d["geometry"]["coordinates"][2],
            "era5land_grid_elev_m": round(float(interpolate(g["elev"], idx, w)[0]), 0),
            "power_mean_c": round(float(p[ok].mean()), 2), "era5land_mean_c": round(float(era_s[ok].mean()), 2),
            "hourly_corr": round(float(np.corrcoef(p[ok], era_s[ok])[0, 1]), 3),
            # compare after moving POWER to the ERA5-Land grid elevation with the same lapse rate
            "bias_power_minus_era5land_elev_adjusted_c": round(float(
                (p[ok].mean() + gamma * (interpolate(g["elev"], idx, w)[0] - d["geometry"]["coordinates"][2]))
                - era_s[ok].mean()), 2),
        }
        out[town] = res
    return out


def building_weighted(df: pd.DataFrame, n: np.ndarray) -> dict:
    share = lambda m: round(float(n[m].sum() / n.sum()), 4)  # noqa: E731
    return {
        "buildings": int(n.sum()),
        "cells_cdd24_gt_0": int((df.cdd24_per_year > 0).sum()),
        "cells_cdd24_ge_10": int((df.cdd24_per_year >= 10).sum()),
        "cells_cdd24_ge_50": int((df.cdd24_per_year >= 50).sum()),
        "building_share_cdd24_gt_0": share(df.cdd24_per_year.to_numpy() > 0),
        "building_share_cdd24_ge_10": share(df.cdd24_per_year.to_numpy() >= 10),
        "building_share_cdd24_ge_50": share(df.cdd24_per_year.to_numpy() >= 50),
        "building_share_tmax_p95_ge_30": share(df.tmax_p95_c.to_numpy() >= 30),
        "building_weighted_t2m_mean_c": round(float(np.average(df.t2m_mean_c, weights=n)), 2),
        "building_weighted_cdd18": round(float(np.average(df.cdd18_per_year, weights=n)), 1),
    }


def quicklook(gdf: gpd.GeoDataFrame, df: pd.DataFrame, prefix: str = "A3") -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    gdf = gdf[["h3_r7", "geometry"]].merge(df, on="h3_r7")
    paths = []
    for col, label, cmap in [("t2m_mean_c", "Mean 2 m temperature 2019-2024 (°C)", "Oranges"),
                             ("cdd24_per_year", "Cooling degree-days, base 24 °C (K·day/yr)", "Reds")]:
        fig, ax = plt.subplots(figsize=(7, 6.5))
        gdf.plot(column=col, cmap=cmap, linewidth=0, ax=ax, legend=True,
                 legend_kwds={"label": label, "shrink": 0.7})
        ax.set_title(f"{label}\nH3 res-7 cells with buildings (n={len(gdf):,})", fontsize=10)
        ax.set_axis_off()
        path = FIG_DIR / f"{prefix}_{col}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, dpi=130, bbox_inches="tight")
        plt.close(fig)
        paths.append(path)
    return paths


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--buildings", type=Path, default=BUILDINGS)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    national = args.out == OUT
    if national:
        with heavy_job("A3-climate-h3"):  # waits for the national buildings rewrite to release the lock
            diag = build(args.buildings, args.out)
    else:
        diag = build(args.buildings, args.out)
    df, gdf = diag.pop("_df"), diag.pop("_gdf")
    if national:
        diag["figures"] = [str(p.relative_to(REPO_ROOT)) for p in quicklook(gdf, df)]
        DIAG.write_text(json.dumps(diag, indent=2) + "\n")
    print(json.dumps(diag, indent=2))
    print(df.describe().T[["mean", "min", "max"]].round(2).to_string())


if __name__ == "__main__":
    main()
