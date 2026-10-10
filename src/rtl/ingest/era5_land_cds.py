"""A3: ERA5-Land for Rwanda straight from the Copernicus Climate Data Store (CDS), under the Copernicus licence.

Two products (accept each one's licence on its CDS page first, under the Download tab, "Terms of use"):

1. "ERA5-Land hourly data from 1950 to present" (reanalysis-era5-land), REQUIRED, 2019-2025:
   the same 0.1 deg hourly grid we use now via Open-Meteo. Hourly because RAMP needs time-of-day (typical day,
   hot hours, fan windows); gridded because every H3 cell is interpolated + elevation-corrected.
   Variables and why:
     2m_temperature                     degree-days, tmax_p95, typical day, hot hours
     2m_dewpoint_temperature            humidity -> heat index (hot + humid drives fan use more than hot + dry)
     surface_solar_radiation_downwards  hourly GHI at 0.1 deg (replaces NASA POWER 1 deg): PV, solar pumps
     total_precipitation                rainy/dry seasons: irrigation-pumping demand, seasonal load shape
     10m_u/v_component_of_wind          wind speed: apparent temperature, natural ventilation (cheap extra)
   NOTE: in ERA5-Land hourly files, ssrd and tp are ACCUMULATED from 00 UTC; de-accumulate (difference
   consecutive hours, reset at 01 UTC) before use.

2. "ERA5-Land post-processed daily statistics from 1950 to present" (derived-era5-land-daily-statistics), OPTIONAL
   (`--baseline`): daily mean and max 2 m temperature in Rwandan days (UTC+02:00), 1991-2020, for a 30-year
   WMO-style climate normal, so cooling classes and CDD aren't based on six years only. Before the first run,
   compare the request keys below with the CDS page's "Show API request" snippet; CDS occasionally renames them.

Credentials live on THIS machine (no local download + transfer): ~/.cdsapirc, chmod 600, containing
    url: https://cds.climate.copernicus.eu/api
    key: <PERSONAL-ACCESS-TOKEN>

    pixi run fetch-era5-cds              # hourly, 84 monthly requests (resumable; CDS queues them)
    pixi run fetch-era5-cds --baseline   # daily statistics 1991-2020 (one request per year and statistic)
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

from rtl.manifest import record
from rtl.settings import RAW_DIR, aoi

HOURLY_YEARS = range(2019, 2026)  # 2025 is complete in ERA5-Land; 2026 is not
BASELINE_YEARS = range(1991, 2021)
MARGIN_DEG = 0.1  # one ERA5-Land cell around the country, for interpolation at the border
HOURLY_VARIABLES = [
    "2m_temperature", "2m_dewpoint_temperature", "surface_solar_radiation_downwards", "total_precipitation",
    "10m_u_component_of_wind", "10m_v_component_of_wind",
]


def area() -> list[float]:
    x0, y0, x1, y1 = aoi()["country"]["bbox"]
    return [y1 + MARGIN_DEG, x0 - MARGIN_DEG, y0 - MARGIN_DEG, x1 + MARGIN_DEG]  # N, W, S, E


def fetch(client, dataset: str, request: dict, target, source_id: str, extra: dict) -> None:
    if target.exists() and target.stat().st_size > 0:
        return
    client.retrieve(dataset, request, str(target))
    record(source_id, target, f"cds:{dataset}", extra)
    print(f"{target.name}: {target.stat().st_size / 1e6:.1f} MB", flush=True)


def hourly(client, out_dir) -> None:
    for year in HOURLY_YEARS:
        for month in range(1, 13):
            req = {"variable": HOURLY_VARIABLES, "year": str(year), "month": f"{month:02d}",
                   "day": [f"{d:02d}" for d in range(1, 32)], "time": [f"{h:02d}:00" for h in range(24)],
                   "area": area(), "data_format": "netcdf", "download_format": "unarchived"}
            fetch(client, "reanalysis-era5-land", req, out_dir / f"era5land_hourly_{year}{month:02d}.nc",
                  "era5_land_cds", {"year": year, "month": month, "variables": HOURLY_VARIABLES})


def baseline(client, out_dir) -> None:
    for year in BASELINE_YEARS:
        for stat in ("daily_mean", "daily_maximum"):
            req = {"variable": ["2m_temperature"], "year": str(year), "month": [f"{m:02d}" for m in range(1, 13)],
                   "day": [f"{d:02d}" for d in range(1, 32)], "daily_statistic": stat,
                   "time_zone": "utc+02:00", "frequency": "1_hourly", "area": area()}
            fetch(client, "derived-era5-land-daily-statistics", req, out_dir / f"era5land_{stat}_{year}.nc",
                  "era5_land_cds", {"year": year, "statistic": stat})


def main() -> None:
    import cdsapi

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--baseline", action="store_true", help="daily statistics 1991-2020 instead of hourly")
    args = ap.parse_args()
    out_dir = RAW_DIR / "era5_land_cds" / datetime.now(UTC).strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    client = cdsapi.Client()
    (baseline if args.baseline else hourly)(client, out_dir)


if __name__ == "__main__":
    main()
