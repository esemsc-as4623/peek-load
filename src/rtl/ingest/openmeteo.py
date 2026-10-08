"""A3: hourly 2 m temperature 2019-2024 on the ERA5-Land 0.1 deg grid via the Open-Meteo Historical API.

Why Open-Meteo: it serves ERA5-Land (Copernicus, ~9 km) without the CDS login, and accepts many
coordinates per call. Terms (checked 2026-10-08, open-meteo.com/en/terms): free tier is for
non-commercial use (public research counts), data under CC-BY-4.0; ERA5-Land itself is under the
Copernicus licence (attribution: "Contains modified Copernicus Climate Change Service information").

Request choices
- `models=era5_land`, `cell_selection=nearest`: return the grid node nearest each requested point, so a
  regular 0.1 deg request grid maps 1:1 onto ERA5-Land nodes (the default `land` may jump to a neighbour).
- `elevation=nan`: switch OFF Open-Meteo's own lapse-rate downscaling to a 90 m DEM. We want the raw
  grid value and the grid's model elevation (returned as `elevation`), and do the elevation correction
  ourselves in climate/ with a lapse rate estimated from this same grid.
- Timestamps stay in UTC; the shift to Central Africa Time happens (and is tested) in climate/.
  We start one UTC day early so that local day 2019-01-01 00:00 CAT (= 2018-12-31 22:00 UTC) is complete.

Rate limits: the free tier allows 600 / 5,000 / 10,000 weighted calls per minute / hour / day, where
weight = n_locations * (n_days / 14) * (n_variables / 10). Six years of one variable is ~15.7 calls per
location, so ~530 grid nodes cost ~8,300 calls: we pace batches to stay under HOURLY_BUDGET and the run
takes ~2 h. Each batch is saved as its own parquet file, so an interrupted run resumes where it stopped.

Raw output: data/raw/openmeteo/<version>/t2m_era5land_batch_NNN.parquet, long format
(req_lat, req_lon, lat, lon, elevation_m, time_utc, t2m_c), each file recorded in the manifest.
"""

from __future__ import annotations

import argparse
import math
import time
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx
import numpy as np
import pandas as pd

from rtl.manifest import read_manifest, record
from rtl.settings import RAW_DIR, REPO_ROOT, aoi

SOURCE_ID = "openmeteo"
API = "https://archive-api.open-meteo.com/v1/archive"
START_UTC, END_UTC = "2018-12-31", "2024-12-31"
STEP_DEG = 0.1  # ERA5-Land native grid spacing
MARGIN_CELLS = 1
BATCH = 16  # locations per request; 16 * 15.7 = ~250 weighted calls, well under the 600/minute limit
HOURLY_BUDGET = 4500  # weighted calls per hour we allow ourselves (limit: 5,000)
USER_AGENT = "rooftops-to-load-curves/0.1 (OSEAS26 hackathon, non-commercial research)"


class DailyLimitReached(RuntimeError):
    """The free tier's daily budget is used up: re-run tomorrow with the same --version to resume."""


def grid_points(bbox: list[float], step: float = STEP_DEG, margin: int = MARGIN_CELLS) -> list[tuple[float, float]]:
    """(lat, lon) grid nodes enclosing bbox = [min_lon, min_lat, max_lon, max_lat] plus `margin` extra rings.

    Works in integer multiples of `step` so 28.8 stays 28.8 (no float drift from repeated addition).
    """
    x0, y0, x1, y1 = bbox
    ix = range(math.floor(round(x0 / step, 6)) - margin, math.ceil(round(x1 / step, 6)) + margin + 1)
    iy = range(math.floor(round(y0 / step, 6)) - margin, math.ceil(round(y1 / step, 6)) + margin + 1)
    return [(round(j * step, 4), round(i * step, 4)) for j in iy for i in ix]


def call_weight(n_locations: int, n_days: int, n_variables: int = 1) -> float:
    """Open-Meteo's documented weighting (minimum 14 days per location)."""
    return n_locations * max(n_days, 14) / 14 * n_variables / 10


def n_days(start: str = START_UTC, end: str = END_UTC) -> int:
    return (pd.Timestamp(end) - pd.Timestamp(start)).days + 1


def request_params(points: list[tuple[float, float]]) -> dict:
    return {
        "latitude": ",".join(f"{lat:.4f}" for lat, _ in points),
        "longitude": ",".join(f"{lon:.4f}" for _, lon in points),
        "elevation": ",".join("nan" for _ in points),
        "start_date": START_UTC,
        "end_date": END_UTC,
        "hourly": "temperature_2m",
        "models": "era5_land",
        "cell_selection": "nearest",
        "timeformat": "unixtime",
        "timezone": "GMT",
    }


def to_frame(points: list[tuple[float, float]], payload: list[dict] | dict) -> pd.DataFrame:
    """Flatten the API's per-location JSON into one long table (results come back in request order)."""
    results = payload if isinstance(payload, list) else [payload]
    if len(results) != len(points):
        raise ValueError(f"asked for {len(points)} locations, got {len(results)}")
    frames = []
    for (req_lat, req_lon), r in zip(points, results, strict=True):
        if r.get("utc_offset_seconds", 0) != 0:
            raise ValueError("expected UTC timestamps")
        h = r["hourly"]
        frames.append(pd.DataFrame({
            "req_lat": req_lat, "req_lon": req_lon,
            "lat": r["latitude"], "lon": r["longitude"], "elevation_m": r["elevation"],
            "time_utc": pd.to_datetime(np.asarray(h["time"], dtype="int64"), unit="s", utc=True),
            "t2m_c": np.asarray(h["temperature_2m"], dtype="float32"),  # JSON null -> NaN
        }))
    return pd.concat(frames, ignore_index=True)


def get_with_retries(client: httpx.Client, params: dict, max_tries: int = 6) -> list[dict] | dict:
    """GET with backoff. 429s are classified by the API's `reason` text (minutely / hourly / daily limit)."""
    for attempt in range(max_tries):
        try:
            r = client.get(API, params=params)
        except httpx.HTTPError as err:  # network hiccup: back off and retry
            wait = 30 * 2**attempt
            print(f"  network error {err!r}; retry in {wait}s")
            time.sleep(wait)
            continue
        if r.status_code == 200:
            return r.json()
        reason = r.json().get("reason", r.text) if "json" in r.headers.get("content-type", "") else r.text
        if r.status_code == 429 and "daily" in reason.lower():
            raise DailyLimitReached(reason)
        if r.status_code == 429:
            wait = 65 if "minut" in reason.lower() else 15 * 60
            print(f"  429 ({reason}); sleeping {wait}s")
            time.sleep(wait)
            continue
        if r.status_code >= 500:
            wait = 30 * 2**attempt
            print(f"  HTTP {r.status_code}; retry in {wait}s")
            time.sleep(wait)
            continue
        raise RuntimeError(f"HTTP {r.status_code}: {reason}")  # 4xx other than 429 = our request is wrong
    raise RuntimeError(f"gave up after {max_tries} tries")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--version", default=datetime.now(UTC).strftime("%Y-%m-%d"),
                    help="raw sub-directory; pass an earlier date to resume an interrupted download")
    ap.add_argument("--max-batches", type=int, default=None, help="stop after N new batches (testing)")
    args = ap.parse_args()

    points = grid_points(aoi()["country"]["bbox"])
    batches = [points[i:i + BATCH] for i in range(0, len(points), BATCH)]
    per_batch = call_weight(BATCH, n_days())
    interval = per_batch / HOURLY_BUDGET * 3600
    print(f"{len(points)} grid nodes in {len(batches)} batches; ~{call_weight(len(points), n_days()):.0f} "
          f"weighted calls; one batch every {interval:.0f}s")

    out_dir = RAW_DIR / SOURCE_ID / args.version
    out_dir.mkdir(parents=True, exist_ok=True)
    known = read_manifest()
    done = 0
    next_t = time.monotonic()
    with httpx.Client(timeout=300, headers={"User-Agent": USER_AGENT}) as client:
        for k, batch in enumerate(batches):
            path = out_dir / f"t2m_era5land_batch_{k:03d}.parquet"
            if path.exists() and str(path.relative_to(REPO_ROOT)) in known:
                continue
            time.sleep(max(0.0, next_t - time.monotonic()))
            params = request_params(batch)
            t0 = time.time()
            df = to_frame(batch, get_with_retries(client, params))
            next_t = time.monotonic() + interval
            df.to_parquet(path, index=False, compression="zstd")
            record(SOURCE_ID, path, f"{API}?{urlencode(params)}",
                   {"version": args.version, "grid_step_deg": STEP_DEG, "n_locations": len(batch)})
            print(f"batch {k + 1}/{len(batches)}: {len(df):,} rows, {df.t2m_c.isna().mean():.1%} NaN, "
                  f"{time.time() - t0:.0f}s -> {path.name}", flush=True)
            done += 1
            if args.max_batches and done >= args.max_batches:
                break
    print("done")


if __name__ == "__main__":
    main()
