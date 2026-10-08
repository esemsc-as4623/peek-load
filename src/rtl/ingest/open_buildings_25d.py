"""A1: Google Open Buildings 2.5D Temporal -> per-year derived rasters for Rwanda.

Google publishes the dataset as public GeoTIFFs on GCS, listed per country-year in a zip of URL files on HDX
(recorded in the manifest as the raw index). For Rwanda each year is 260 tiles of 12.5 km x 12.5 km in UTM 35S,
0.5 m pixels, three float32 bands (fractional_count, height, presence), nodata -99: ~41 GB per year, ~326 GB
for 2016-2023. We use one band per year plus the 2023 height, and Google states the *effective* resolution is
~4 m, so most of those bytes carry no information we need.

Two modes (config/sources.yaml -> google_25d_temporal.mode):
- `remote` (default): read the needed band straight from the public COG-style tiles via GDAL /vsicurl/ range
  requests, asking for a coarser grid. GDAL then serves the read from the tile's internal overviews (2x, 4x, 7x,
  13x, ...), so only ~1/16 (2 m) or ~1/49 (4 m, from the 3.5 m overview) of a band's bytes cross the network.
- `full`: download whole tiles through rtl.manifest.fetch into data/raw/ (immutable, sha256 in the manifest),
  then derive the same rasters from the local files. Same outputs, ~41 GB of disk per year.

Outputs: data/interim/google_25d_temporal/<tag>/<layer>_<res>m_<year>/tile_*.tif + one VRT mosaic per UTM
zone + a provenance JSON (tile URLs, GCS md5, Last-Modified, mode, resolution).
"""

from __future__ import annotations

import argparse
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path

import httpx
import rasterio
from osgeo import gdal
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.warp import transform_bounds
from rasterio.windows import Window
from rasterio.windows import from_bounds as window_from_bounds

from rtl.locks import heavy_job
from rtl.manifest import fetch, read_manifest
from rtl.settings import INTERIM_DIR, REPO_ROOT, sources

gdal.UseExceptions()

SOURCE_ID = "google_25d_temporal"
YEARS = list(range(2016, 2024))
BANDS = {"fractional_count": 1, "height": 2, "presence": 3}  # band order as in the GeoTIFF descriptions
NODATA = -99.0
DERIVED_DIR = INTERIM_DIR / SOURCE_ID
# GDAL settings for many small range requests against GCS: don't list "directories", retry transient errors.
GDAL_ENV = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
                GDAL_HTTP_MAX_RETRY="5", GDAL_HTTP_RETRY_DELAY="2", GDAL_HTTP_MULTIRANGE="YES", VSI_CACHE="TRUE")


def cfg() -> dict:
    return sources()[SOURCE_ID]


def index_zip() -> Path:
    """The HDX zip of per-country-year URL lists (downloaded once through the manifest)."""
    entries = [e for e in read_manifest().values() if e["source_id"] == SOURCE_ID and e["path"].endswith(".zip")]
    if not entries:
        return fetch(SOURCE_ID, url=cfg()["index_url"])
    return REPO_ROOT / max(entries, key=lambda e: e["version"])["path"]


def tile_urls(year: int, iso3: str = "RWA") -> list[str]:
    with zipfile.ZipFile(index_zip()) as z:
        text = z.read(f"urls/{iso3}_{year}.txt").decode()
    return [u.strip() for u in text.splitlines() if u.strip()]


def layer_dir(layer: str, year: int, res_m: float, tag: str = "national") -> Path:
    return DERIVED_DIR / tag / f"{layer}_{res_m:g}m_{year}"


def derive_tile(src: str | Path, band: int, res_m: float, dest: Path, bbox_lonlat: list[float] | None = None) -> bool:
    """Write one band of one tile resampled to `res_m` (nearest). Returns False if the tile misses the bbox.

    Nearest (not average) keeps values that actually occur in the product; asking GDAL for a coarser grid makes it
    read the closest internal overview instead of the 0.5 m data, which is what keeps remote reads cheap.
    """
    with rasterio.open(src) as ds:
        left, bottom, right, top = ds.bounds
        window = Window(0, 0, ds.width, ds.height)
        if bbox_lonlat:  # dev runs: crop to the bbox, in tile pixels
            x0, y0, x1, y1 = transform_bounds("EPSG:4326", ds.crs, *bbox_lonlat)
            left, bottom, right, top = max(left, x0), max(bottom, y0), min(right, x1), min(top, y1)
            if left >= right or bottom >= top:
                return False
            window = window_from_bounds(left, bottom, right, top, ds.transform).round_offsets().round_lengths()
            left, bottom, right, top = ds.window_bounds(window)
        w, h = max(1, round((right - left) / res_m)), max(1, round((top - bottom) / res_m))
        arr = ds.read(band, window=window, out_shape=(h, w), resampling=Resampling.nearest)
        profile = dict(driver="GTiff", width=w, height=h, count=1, dtype="float32", crs=ds.crs, nodata=NODATA,
                       transform=from_bounds(left, bottom, right, top, w, h), compress="deflate", predictor=3,
                       tiled=True, blockxsize=256, blockysize=256)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.stem + ".part.tif")
    with rasterio.open(tmp, "w", **profile) as out:
        out.write(arr, 1)
    tmp.rename(dest)
    return True


def remote_meta(url: str) -> dict:
    """GCS md5 + Last-Modified: identifies the exact remote object we read, without downloading it."""
    r = httpx.head(url, timeout=60)
    hashes = dict(h.split("=", 1) for v in r.headers.get_list("x-goog-hash") for h in v.split(","))
    return {"url": url, "bytes": int(r.headers.get("content-length", 0)), "md5_b64": hashes.get("md5"),
            "last_modified": r.headers.get("last-modified")}


def derive_layer(layer: str, year: int, res_m: float, mode: str = "remote", tag: str = "national",
                 bbox_lonlat: list[float] | None = None, workers: int = 8) -> Path:
    """Derive one layer-year for all Rwanda tiles; returns the tile directory. Idempotent per tile."""
    out_dir = layer_dir(layer, year, res_m, tag)
    band = BANDS[layer]

    def one(url: str) -> dict | None:
        dest = out_dir / url.rsplit("/", 1)[-1]
        meta = remote_meta(url) if mode == "remote" else {"url": url}
        if not dest.exists():
            if mode == "full":
                local = fetch(SOURCE_ID, url=url, filename=f"{year}/{dest.name}")
                meta["raw_path"] = str(local.relative_to(REPO_ROOT))
                src = local
            else:
                src = f"/vsicurl/{url}"
            with rasterio.Env(**GDAL_ENV):
                if not derive_tile(src, band, res_m, dest, bbox_lonlat):
                    return None
        return meta

    with ThreadPoolExecutor(workers) as pool:
        metas = [m for m in pool.map(one, tile_urls(year)) if m]
    vrts = build_vrts(out_dir)
    (out_dir.parent / f"{out_dir.name}.json").write_text(json.dumps({
        "source_id": SOURCE_ID, "layer": layer, "band": band, "year": year, "res_m": res_m, "mode": mode,
        "resampling": "nearest (served from internal overviews)", "bbox_lonlat": bbox_lonlat,
        "derived_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "vrts": {k: str(v.relative_to(REPO_ROOT)) for k, v in vrts.items()}, "n_tiles": len(metas), "tiles": metas,
    }, indent=1) + "\n")
    print(f"[{SOURCE_ID}] {layer} {year} @ {res_m:g} m: {len(metas)} tiles -> {out_dir.relative_to(REPO_ROOT)} "
          f"(VRT per CRS: {sorted(vrts)})")
    return out_dir


def build_vrts(out_dir: Path) -> dict[int, Path]:
    """One VRT mosaic per CRS. Rwanda straddles UTM 35S/36S at 30 deg E and Google keeps each tile in its local
    zone (161 tiles in 35S, 99 in 36S); a single VRT would silently drop the minority zone."""
    by_crs: dict[int, list[str]] = {}
    for tif in sorted(out_dir.glob("tile_*.tif")):
        if ".part" in tif.name:
            continue
        with rasterio.open(tif) as ds:
            by_crs.setdefault(ds.crs.to_epsg(), []).append(str(tif))
    vrts = {}
    for epsg, tiles in by_crs.items():
        vrts[epsg] = out_dir.parent / f"{out_dir.name}_{epsg}.vrt"  # not with_suffix: "0.5m" contains a dot
        gdal.BuildVRT(str(vrts[epsg]), tiles).FlushCache()
    return vrts


def plan() -> list[tuple[str, int, float]]:
    """The layer-years the height table needs: 2023 height (fine) + presence every year (coarse)."""
    c = cfg()
    return [("height", 2023, c["height_res_m"])] + [("presence", y, c["presence_res_m"]) for y in YEARS]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bbox", type=float, nargs=4, metavar=("MINX", "MINY", "MAXX", "MAXY"))
    ap.add_argument("--tag", default="national")
    ap.add_argument("--mode", choices=["remote", "full"], default=None)
    args = ap.parse_args()
    mode = args.mode or cfg().get("mode", "remote")
    with heavy_job("A1-25d-ingest") if args.tag == "national" else nullcontext():
        for layer, year, res in plan():
            derive_layer(layer, year, res, mode=mode, tag=args.tag, bbox_lonlat=args.bbox)


if __name__ == "__main__":
    main()
