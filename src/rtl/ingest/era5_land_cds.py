"""A3: ERA5-Land hourly 2 m temperature for Rwanda straight from the Copernicus Climate Data Store (CDS).

Same data as the Open-Meteo extract we use now, but obtained from the source under the Copernicus licence (free,
including commercial use, with attribution), which removes the Open-Meteo free-tier non-commercial restriction.

Needs a CDS account and API token on THIS machine (no local download + transfer needed):
  1. register at https://cds.climate.copernicus.eu, then accept the licence on the
     "ERA5-Land hourly data from 1950 to present" dataset page (Download tab, "Terms of use");
  2. copy your Personal Access Token from your CDS profile into ~/.cdsapirc (chmod 600):
         url: https://cds.climate.copernicus.eu/api
         key: <PERSONAL-ACCESS-TOKEN>

    pixi run fetch-era5-cds            # one request per month 2019-2024 (72 requests; resumable; queued by CDS)
"""

from __future__ import annotations

from datetime import UTC, datetime

from rtl.manifest import record
from rtl.settings import RAW_DIR, aoi

YEARS = range(2019, 2025)
MARGIN_DEG = 0.1  # one ERA5-Land cell around the country, for interpolation at the border


def main() -> None:
    import cdsapi

    x0, y0, x1, y1 = aoi()["country"]["bbox"]
    area = [y1 + MARGIN_DEG, x0 - MARGIN_DEG, y0 - MARGIN_DEG, x1 + MARGIN_DEG]  # N, W, S, E
    out_dir = RAW_DIR / "era5_land_cds" / datetime.now(UTC).strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    client = cdsapi.Client()
    for year in YEARS:
        for month in range(1, 13):
            target = out_dir / f"era5land_t2m_{year}{month:02d}.nc"
            if target.exists() and target.stat().st_size > 0:
                continue
            request = {
                "variable": ["2m_temperature"],
                "year": str(year), "month": f"{month:02d}",
                "day": [f"{d:02d}" for d in range(1, 32)],
                "time": [f"{h:02d}:00" for h in range(24)],
                "area": area,
                "data_format": "netcdf", "download_format": "unarchived",
            }
            client.retrieve("reanalysis-era5-land", request, str(target))
            record("era5_land_cds", target, "cds:reanalysis-era5-land", {"year": year, "month": month})
            print(f"{target.name}: {target.stat().st_size / 1e6:.1f} MB", flush=True)


if __name__ == "__main__":
    main()
