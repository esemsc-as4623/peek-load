"""A3: NASA POWER (no key) for two jobs Open-Meteo doesn't cover.

1. Cross-check: hourly T2M 2019-2024 (UTC, MERRA-2 ~0.5 x 0.625 deg) at four reference towns, compared with
   the ERA5-Land grid in climate/ (an independent reanalysis, so agreement is evidence, not tautology).
2. Solar: daily ALLSKY_SFC_SW_DWN (CERES SYN1deg, native 1 deg) on the regional endpoint. The regional API
   allows at most 366 days per call, so one call per year. We ask for lat -4..0 / lon 28..32 so the 1-deg
   cell centres (x.5) enclose the whole country and bilinear interpolation never extrapolates.

Raw JSON responses are saved untouched in data/raw/nasa_power/<date>/ and recorded in the manifest.
Licence: NASA open data, no restrictions; acknowledgement requested ("These data were obtained from the
NASA Langley Research Center (LaRC) POWER Project funded through the NASA Earth Science/Applied Science Program").
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx

from rtl.manifest import record
from rtl.settings import RAW_DIR

SOURCE_ID = "nasa_power"
API = "https://power.larc.nasa.gov/api/temporal"
YEARS = range(2019, 2025)
TOWNS = {  # (lat, lon)
    "kigali": (-1.95, 30.06),
    "musanze": (-1.50, 29.63),
    "kirehe_nasho": (-2.20, 30.65),
    "rusizi": (-2.48, 28.90),
}
GHI_REGION = {"latitude-min": -4.0, "latitude-max": 0.0, "longitude-min": 28.0, "longitude-max": 32.0}


def get_json(client: httpx.Client, url: str, tries: int = 4) -> dict:
    for attempt in range(tries):
        try:
            r = client.get(url)
            if r.status_code == 200:
                return r.json()
            print(f"  HTTP {r.status_code}: {r.text[:200]}")
        except httpx.HTTPError as err:
            print(f"  network error {err!r}")
        time.sleep(20 * 2**attempt)
    raise RuntimeError(f"failed: {url}")


def save(client: httpx.Client, url: str, name: str, out_dir) -> None:
    path = out_dir / name
    if not path.exists():
        payload = get_json(client, url)
        if "messages" in payload and not payload.get("properties") and not payload.get("features"):
            raise RuntimeError(f"{name}: {payload['messages']}")
        path.write_text(json.dumps(payload))
        record(SOURCE_ID, path, url)
        time.sleep(2)  # polite pacing
    print(f"ok {name}")


def main() -> None:
    out_dir = RAW_DIR / SOURCE_ID / datetime.now(UTC).strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=300) as client:
        for town, (lat, lon) in TOWNS.items():
            q = dict(parameters="T2M", community="RE", latitude=lat, longitude=lon, start="20190101",
                     end="20241231", format="JSON", **{"time-standard": "UTC"})
            save(client, f"{API}/hourly/point?{urlencode(q)}", f"t2m_hourly_{town}.json", out_dir)
        for year in YEARS:
            q = dict(parameters="ALLSKY_SFC_SW_DWN", community="RE", start=f"{year}0101", end=f"{year}1231",
                     format="JSON", **GHI_REGION)
            save(client, f"{API}/daily/regional?{urlencode(q)}", f"ghi_daily_{year}.json", out_dir)


if __name__ == "__main__":
    main()
