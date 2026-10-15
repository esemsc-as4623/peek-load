"""A2r: NASA VIIRS Black Marble annual night lights (VNP46A4, Collection 2) for Rwanda, per H3 r8 cell and year.

    pixi run fetch-blackmarble                 # 2016-2024 (the years with building presence data, plus 2024)
    pixi run fetch-blackmarble --years 2023    # one year

Needs a NASA Earthdata token in .env as EARTHDATA_TOKEN=<token> (https://urs.earthdata.nasa.gov > Generate Token).
Rwanda spans two Black Marble tiles, h20v09 (west of 30 E) and h21v09 (10 x 10 degrees, 15 arc-second pixels,
~74 MB each per year). Granules are found in NASA's CMR catalogue and downloaded from Earthdata Cloud (the LAADS
archive URLs redirect token requests to a browser login). Each year's "AllAngle_Composite_Snow_Free" radiance
(nW/cm2/sr; scale and fill read from the file: Collection 2 is float, fill -999.9) is cut to the country bbox and
averaged per H3 r8 cell. Output: data/interim/ntl_h3r8.parquet (h3_r8, year, radiance, lit_share).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import h5py
import httpx
import numpy as np
import pandas as pd

from rtl.manifest import record
from rtl.settings import INTERIM_DIR, RAW_DIR, aoi, settings

CMR = "https://cmr.earthdata.nasa.gov/search/granules.json"
TILES = ("h20v09", "h21v09")
FIELD = "HDFEOS/GRIDS/VIIRS_Grid_DNB_2d/Data Fields/AllAngle_Composite_Snow_Free"
OUT = INTERIM_DIR / "ntl_h3r8.parquet"
PIX = 10.0 / 2400  # 15 arc-seconds


def tile_origin(tile: str) -> tuple[float, float]:
    """(west, north) corner of a Black Marble 10-degree tile: h counts east from 180 W, v counts south from 90 N."""
    h, v = int(tile[1:3]), int(tile[4:6])
    return -180 + 10 * h, 90 - 10 * v


def granules(year: int) -> dict[str, str]:
    """{tile: Earthdata Cloud URL} of the year's VNP46A4 Collection 2 granules over Rwanda."""
    x0, y0, x1, y1 = aoi()["country"]["bbox"]
    r = httpx.get(CMR, params={"short_name": "VNP46A4", "version": "2", "page_size": 20,
                               "temporal": f"{year}-01-01T00:00:00Z,{year}-01-01T23:59:59Z",
                               "bounding_box": f"{x0},{y0},{x1},{y1}"}, timeout=60)
    r.raise_for_status()
    out = {}
    for g in r.json()["feed"]["entry"]:
        for link in g["links"]:
            href = link["href"]
            tile = next((t for t in TILES if f".{t}." in href), None)
            if tile and href.startswith("https://data.") and href.endswith(".h5"):
                out[tile] = href
    if set(out) != set(TILES):
        raise SystemExit(f"{year}: CMR lists tiles {sorted(out)}, expected {TILES}")
    return out


def download(url: str, year: int, token: str) -> Path:
    dest = RAW_DIR / "blackmarble" / str(year) / url.rsplit("/", 1)[1]
    if dest.exists() and dest.read_bytes()[:4] == b"\x89HDF":
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    with httpx.stream("GET", url, headers={"Authorization": f"Bearer {token}"}, follow_redirects=True,
                      timeout=600) as r:
        if r.status_code in (401, 403):
            raise SystemExit("Earthdata refused the token: check EARTHDATA_TOKEN in .env (tokens expire after 60 days)")
        r.raise_for_status()
        with open(tmp, "wb") as fh:
            for block in r.iter_bytes(1 << 20):
                fh.write(block)
    if tmp.read_bytes()[:4] != b"\x89HDF":  # a login page instead of data
        tmp.unlink()
        raise SystemExit(f"{url} did not return HDF5 (Earthdata login page?): check EARTHDATA_TOKEN in .env")
    tmp.rename(dest)
    record("viirs_black_marble", dest, url, {"year": year})
    return dest


def radiance_points(path: Path, tile: str) -> pd.DataFrame:
    """Pixel centres and radiance of one tile inside the Rwanda bbox (fill values dropped)."""
    x0, y0, x1, y1 = aoi()["country"]["bbox"]
    west, north = tile_origin(tile)
    c0, c1 = max(0, int((x0 - west) / PIX)), min(2400, int(np.ceil((x1 - west) / PIX)))
    r0, r1 = max(0, int((north - y1) / PIX)), min(2400, int(np.ceil((north - y0) / PIX)))
    with h5py.File(path, "r") as f:
        d = f[FIELD]
        a = d[r0:r1, c0:c1].astype("float64")
        fill, scale = float(d.attrs["_FillValue"][0]), float(d.attrs.get("scale_factor", [1.0])[0])
    rows, cols = np.nonzero((a != fill) & (a >= 0))
    return pd.DataFrame({"lon": west + (c0 + cols + 0.5) * PIX, "lat": north - (r0 + rows + 0.5) * PIX,
                         "rad": a[rows, cols] * scale})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--years", type=int, nargs="*", default=list(range(2016, 2025)))
    args = ap.parse_args()
    tok = settings().earthdata_token
    if tok is None:
        raise SystemExit("Add EARTHDATA_TOKEN=<your token> to .env (it is gitignored), then rerun.")
    con = duckdb.connect()
    con.sql("INSTALL h3 FROM community; LOAD h3")
    parts = []
    for y in args.years:
        token = tok.get_secret_value().strip()
        pts = pd.concat([radiance_points(download(url, y, token), t) for t, url in granules(y).items()])
        con.register("pts", pts)
        parts.append(con.sql(f"""SELECT h3_latlng_to_cell_string(lat, lon, 8) AS h3_r8, {y} AS year,
                                        avg(rad)::FLOAT AS radiance, avg((rad > 0.5)::INT)::FLOAT AS lit_share
                                 FROM pts GROUP BY 1""").df())
        print(f"{y}: {len(pts):,} pixels, mean radiance {pts.rad.mean():.2f} nW/cm2/sr")
    out = pd.concat(parts, ignore_index=True)
    if OUT.exists():  # keep years not re-run
        old = pd.read_parquet(OUT)
        out = pd.concat([old[~old.year.isin(args.years)], out], ignore_index=True)
    out.to_parquet(OUT, index=False)
    print(f"{out.h3_r8.nunique():,} H3 r8 cells x {out.year.nunique()} years -> {OUT}")


if __name__ == "__main__":
    main()
