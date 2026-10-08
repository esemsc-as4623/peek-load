"""A1: per-building height, floors, gross floor area and first-seen year -> `building_height` contract.

Inputs: buildings_base (footprints) + rasters derived by rtl.ingest.open_buildings_25d (2023 height at 2 m,
presence 2016-2023 at ~8 m, UTM 35S).

Choices (justified in reports/status/A1.md):
- height_m = coverage-weighted MEDIAN of 2023 height pixels under the footprint (exactextract cells). Footprints and the
  4 m-effective product are not perfectly co-registered, so edge pixels often fall on ground (0 m) or on a taller
  neighbour; the median ignores both as long as most of the footprint sits on its own building, the mean does not.
  Footprints covering < 1 pixel (< 4 m2) or no valid pixel fall back to the pixel under the centroid.
  A result < MIN_HEIGHT_M means the model saw no structure there: stored as null ("not observed"), never 0.
- first_seen_year = earliest year whose presence at the CENTROID is >= threshold and stays >= threshold in every
  later year (so a single noisy year can neither create nor delay a building). The centroid, not the footprint
  max: at 8 m a footprint max picks up neighbouring roofs in dense areas and would date buildings too early.
  2016 means "2016 or earlier" (first year of the series). Not present through 2023 -> null.
- est_floors = max(1, round(height_m / STOREY_M)); gfa_m2 = area_m2 * est_floors.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import duckdb
import geopandas as gpd
import numpy as np
import pandas as pd
import pyproj
import rasterio
import shapely
from exactextract import exact_extract

from rtl.conform.buildings import OUT as BASE
from rtl.ingest.open_buildings_25d import NODATA, YEARS, build_vrts, cfg, layer_dir
from rtl.locks import heavy_job
from rtl.settings import FIXTURES_DIR, INTERIM_DIR, REPORTS_DIR

STOREY_M = 3.0  # typical floor-to-floor height of Rwandan masonry buildings; see status file
MIN_HEIGHT_M = 1.0  # below this the 2.5D model saw no structure
PRESENCE_THRESHOLD = 0.5
SENSITIVITY_THRESHOLDS = (0.3, 0.5, 0.7)
MAX_FLOORS = 30  # contract bound
CHUNK = 250_000  # footprints per exactextract call (keeps memory modest)

OUT = INTERIM_DIR / "building_height.parquet"
FIXTURE = FIXTURES_DIR / "buildings_base_fixture.parquet"


# --------------------------------------------------------------------------- pure transforms (unit-tested)
def first_seen_year(presence: np.ndarray, years: list[int], threshold: float = PRESENCE_THRESHOLD) -> pd.Series:
    """presence: (n_buildings, n_years) in year order, NaN = no data. Returns Int64 years (NA = never stable)."""
    above = np.nan_to_num(presence, nan=-1.0) >= threshold
    # stays[i, t] is True when the building is above threshold in year t AND every later year
    stays = np.logical_and.accumulate(above[:, ::-1], axis=1)[:, ::-1]
    first = np.asarray(years)[stays.argmax(axis=1)]
    return pd.Series(first, dtype="Int64").where(stays[:, -1])


def est_floors(height_m: pd.Series, storey_m: float = STOREY_M) -> pd.Series:
    """max(1, round(h / storey)) with round-half-up; np.round rounds half to even (7.5 m / 3 = 2.5 -> 2)."""
    floors = np.floor(height_m.astype(float) / storey_m + 0.5).clip(1, MAX_FLOORS)
    return pd.Series(floors, index=height_m.index).astype("Int64")


def gfa_m2(area_m2: pd.Series, floors: pd.Series) -> pd.Series:
    return (area_m2 * floors.astype("Float64")).astype(float)


# --------------------------------------------------------------------------- raster sampling
def _points_in_tiles(tiles: list[Path], lon: np.ndarray, lat: np.ndarray):
    """Yield (open tile, point indices, rows, cols) for the points inside each tile (first tile wins).

    Points are projected into each tile's own CRS: Rwanda straddles UTM 35S/36S and Google keeps every tile in its
    local zone, so one fixed metric CRS would silently miss ~40% of the tiles.
    """
    projected: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    taken = np.zeros(len(lon), dtype=bool)
    for tile in tiles:
        with rasterio.open(tile) as ds:
            key = ds.crs.to_string()
            if key not in projected:
                projected[key] = pyproj.Transformer.from_crs("EPSG:4326", ds.crs, always_xy=True).transform(lon, lat)
            x, y = projected[key]
            left, bottom, right, top = ds.bounds
            idx = np.flatnonzero((x >= left) & (x < right) & (y > bottom) & (y <= top) & ~taken)
            if not idx.size:
                continue
            taken[idx] = True
            col = np.minimum(((x[idx] - left) / ds.res[0]).astype(int), ds.width - 1)
            row = np.minimum(((top - y[idx]) / ds.res[1]).astype(int), ds.height - 1)
            yield ds, idx, row, col


def sample_points(tiles: list[Path], lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    """Value of the pixel containing each lon/lat point; NaN outside every tile or nodata.

    Per-tile array indexing instead of rasterio.sample: one read per tile instead of one call per point.
    """
    out = np.full(len(lon), np.nan, dtype="float32")
    for ds, idx, row, col in _points_in_tiles(tiles, lon, lat):
        vals = ds.read(1)[row, col]
        out[idx] = np.where(vals == NODATA, np.nan, vals)
    return out


def tile_epsg(tiles: list[Path], lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    """EPSG code of the tile containing each point (0 = outside all tiles)."""
    out = np.zeros(len(lon), dtype=int)
    for ds, idx, _, _ in _points_in_tiles(tiles, lon, lat):
        out[idx] = ds.crs.to_epsg()
    return out


def weighted_median(values: list[np.ndarray], weights: list[np.ndarray]) -> np.ndarray:
    """Lower weighted median per group: smallest value whose cumulative weight reaches half the group's total.

    Written out (vectorised over all groups) because exactextract's own `median` interpolates between *distinct*
    values, so 90 pixels at 6 m + 10 pixels at 9 m gives 7.5 m instead of 6 m.
    """
    n = len(values)
    lens = np.fromiter((len(v) for v in values), dtype=int, count=n)
    out = np.full(n, np.nan)
    if not lens.sum():
        return out
    grp = np.repeat(np.arange(n), lens)
    vals, w = np.concatenate(values).astype(float), np.concatenate(weights).astype(float)
    order = np.lexsort((vals, grp))
    vals, w, grp = vals[order], w[order], grp[order]
    totals = np.bincount(grp, w, minlength=n)
    within = np.cumsum(w) - np.repeat(np.r_[0.0, np.cumsum(totals)[:-1]], lens)  # cumulative weight inside group
    hit = np.flatnonzero(within >= 0.5 * totals[grp] - 1e-9)
    g, first = np.unique(grp[hit], return_index=True)
    out[g] = vals[hit[first]]
    return out


def footprint_median(raster: Path, ids: pd.Series, geoms: np.ndarray, crs: str = "EPSG:32735") -> pd.DataFrame:
    """Coverage-weighted median and covered pixel count per footprint (geometries already in the raster CRS)."""
    gdf = gpd.GeoDataFrame({"bldg_id": ids.values}, geometry=geoms, crs=crs)
    res = exact_extract(str(raster), gdf, ["values", "coverage"], include_cols=["bldg_id"], output="pandas")
    return pd.DataFrame({"bldg_id": res["bldg_id"],
                         "fp_median": weighted_median(list(res["values"]), list(res["coverage"])),
                         "fp_pixels": [float(np.sum(c)) for c in res["coverage"]]})


def tiles_of(layer: str, year: int, res_m: float, tag: str) -> tuple[list[Path], dict[int, Path]]:
    d = layer_dir(layer, year, res_m, tag)
    tiles = sorted(p for p in d.glob("tile_*.tif") if ".part" not in p.name)
    if not tiles:
        raise FileNotFoundError(f"{d} empty: run `pixi run fetch-25d` (tag={tag}) first")
    return tiles, build_vrts(d)


def _project(geoms: np.ndarray, epsg: int) -> np.ndarray:
    tr = pyproj.Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    return shapely.transform(geoms, lambda xy: np.column_stack(tr.transform(*xy.T)))


# --------------------------------------------------------------------------- pipeline
def build(base: Path, out: Path, tag: str) -> dict:
    c = cfg()
    con = duckdb.connect()
    con.sql("LOAD spatial;")
    pts = con.sql(f"SELECT bldg_id, area_m2, lon, lat FROM read_parquet('{base}')").df()
    lon, lat = pts["lon"].to_numpy(), pts["lat"].to_numpy()

    # presence time series at the centroid, one column per year
    presence = np.column_stack([sample_points(tiles_of("presence", yr, c["presence_res_m"], tag)[0], lon, lat)
                                for yr in YEARS])

    # 2023 height: footprint median in the UTM zone of the tile under the centroid; centroid pixel as fallback
    h_tiles, h_vrts = tiles_of("height", 2023, c["height_res_m"], tag)
    centroid_h = sample_points(h_tiles, lon, lat)
    epsg_of = pd.Series(tile_epsg(h_tiles, lon, lat), index=pts["bldg_id"])
    parts = []
    reader = con.execute(f"SELECT bldg_id, ST_AsWKB(geometry) AS wkb FROM read_parquet('{base}')"
                         ).fetch_record_batch(CHUNK)
    for batch in reader:
        b = batch.to_pandas()
        b["epsg"] = epsg_of.loc[b["bldg_id"]].to_numpy()
        for epsg, g in b[b["epsg"] > 0].groupby("epsg"):
            geoms = _project(shapely.from_wkb(g["wkb"].map(bytes)), epsg)
            parts.append(footprint_median(h_vrts[epsg], g["bldg_id"], geoms, f"EPSG:{epsg}"))
    fp = pd.concat(parts, ignore_index=True)
    df = pts.merge(fp, on="bldg_id", how="left", validate="1:1")
    df["centroid_h"] = centroid_h  # pts order is preserved by the left merge

    use_fp = df["fp_pixels"].fillna(0).ge(1) & df["fp_median"].notna()
    raw_h = np.where(use_fp, df["fp_median"], df["centroid_h"])
    observed = pd.Series(raw_h).ge(MIN_HEIGHT_M)  # NaN compares False -> unobserved
    res = f"{c['height_res_m']:g}m"
    height = pd.Series(np.clip(raw_h, 0, 80)).where(observed)
    floors = est_floors(height)
    table = pd.DataFrame({
        "bldg_id": df["bldg_id"],
        "height_m": height.astype(float),
        "height_source": pd.Series(np.where(use_fp, f"google_25d_temporal:2023:footprint_median_{res}",
                                            f"google_25d_temporal:2023:centroid_{res}")).where(observed),
        "first_seen_year": first_seen_year(presence, YEARS),
        "est_floors": floors,
        "gfa_m2": gfa_m2(df["area_m2"], floors),
    })
    out.parent.mkdir(parents=True, exist_ok=True)
    table.to_parquet(out, index=False)

    n_base = len(pts)
    stats = {
        "base": str(base), "rows_base": n_base, "rows_out": len(table), "tag": tag,
        "height_res_m": c["height_res_m"], "presence_res_m": c["presence_res_m"], "mode": c.get("mode"),
        "height_coverage": float(table["height_m"].notna().mean()),
        "height_from_centroid_fallback": int((~use_fp & observed).sum()),
        "height_quantiles": table["height_m"].quantile([0.1, 0.25, 0.5, 0.75, 0.9, 0.99]).round(2).to_dict(),
        "floors_counts": {int(k): int(v) for k, v in table["est_floors"].value_counts().sort_index().items()},
        "first_seen_counts": {f"thr_{t}": {str(k): int(v) for k, v in first_seen_year(presence, YEARS, t)
                                           .value_counts(dropna=False).sort_index().items()}
                              for t in SENSITIVITY_THRESHOLDS},
        "height_tiles_by_epsg": {int(k): int(v) for k, v in epsg_of.value_counts().items()},
        "presence_nan_share_by_year": dict(zip(map(str, YEARS), np.isnan(presence).mean(axis=0).round(4).tolist(),
                                               strict=True)),
    }
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fixture", action="store_true", help="run on the committed fixture with --tag fixture rasters")
    args = ap.parse_args()
    t0 = time.time()
    if args.fixture:
        stats = build(FIXTURE, INTERIM_DIR / "building_height_fixture.parquet", tag="fixture")
    else:
        with heavy_job("A1-height"):
            stats = build(BASE, OUT, tag="national")
    stats["seconds"] = round(time.time() - t0, 1)
    assert stats["rows_out"] == stats["rows_base"], "row reconciliation with buildings_base failed"
    name = "conform_height_fixture.json" if args.fixture else "conform_height.json"
    (REPORTS_DIR / "qa").mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "qa" / name).write_text(json.dumps(stats, indent=2) + "\n")
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
