"""B7: the public site's data files, packed from the dashboard bundle.

    pixi run site-data          # data/dashboard/v1 + dashboard/sample_data/v1 -> data/dashboard/app_v1

The site (app/) reads a handful of compact JSON files instead of the bundle's 900: one per page family, with short
keys and the map geometry pre-projected to SVG paths, so the browser does no projection or GeoJSON parsing.

- national.json: H3 r7 cells and districts as SVG paths (a 0.01 degree margin around Rwanda, 1000 units wide),
  their values, cells with no buildings (`hexes_empty`), the deep-dive markers and the national climate medians
- site.json: the bundle's meta (sample bundle), checks, sources, workflow, national demand by year and the
  whole-sub-district summaries
- subdistrict_<id>.json: the sample box of buildings of each deep dive as SVG paths (a 0.0002 degree margin),
  their values, weights, productive-use criteria and details
- profiles.json: the RAMP profile library with the evidence each number cites
- figures/*.png: report figures (config/dashboard.yaml `site.figures`)

The format reproduces the files the design canvas used, so app/src/pages read them unchanged.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import shutil
import statistics
from pathlib import Path

import duckdb
import numpy as np

from rtl.settings import DATA_DIR, RAW_DIR, REPO_ROOT, REPORTS_DIR, load_yaml

FULL = DATA_DIR / "dashboard" / "v1"
SAMPLE = REPO_ROOT / "dashboard" / "sample_data" / "v1"
OUT = DATA_DIR / "dashboard" / "app_v1"
M_PER_DEG = 111320.0


def read(path: Path):
    return json.loads(path.read_text())


def write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))


def r(x, n: int):
    return None if x is None else round(x, n)


def rings(geom: dict) -> list:
    return geom["coordinates"] if geom["type"] == "Polygon" else [ring for poly in geom["coordinates"] for ring in poly]


class Frame:
    """Equirectangular projection of a lon/lat box (plus a margin) onto an SVG frame 1000 units wide."""

    def __init__(self, coords: np.ndarray, margin_deg: float):
        self.x0, self.y0 = coords.min(0) - margin_deg
        x1, self.y1 = coords.max(0) + margin_deg
        cos = math.cos(math.radians((self.y0 + self.y1) / 2))
        self.metres = (x1 - self.x0) * M_PER_DEG * cos  # frame width in metres
        self.kx = 1000 / (x1 - self.x0)
        self.ky = self.kx / cos
        self.height = round((self.y1 - self.y0) * self.ky, 1)

    def xy(self, lon: float, lat: float) -> tuple[float, float]:
        return (lon - self.x0) * self.kx, (self.y1 - lat) * self.ky

    def path(self, rs: list) -> str:
        return "".join("M" + "L".join("{:.1f} {:.1f}".format(*self.xy(lo, la)) for lo, la in ring) + "Z" for ring in rs)


# ------------------------------------------------------------------------------------------------ national
def land_area_km2() -> dict[str, float]:
    f = glob.glob(str(RAW_DIR / "admin_boundaries" / "*" / "geoBoundaries-RWA-ADM2.geojson"))[0]
    con = duckdb.connect()
    con.sql("LOAD spatial")
    rows = con.sql(f"""SELECT shapeName, ST_Area(ST_Transform(geom, 'EPSG:4326', 'EPSG:32735', always_xy := true))
                       / 1e6 FROM st_read('{f}')""").fetchall()
    return {name: round(a, 1) for name, a in rows}


def empty_cells(have: set[str]) -> list[str]:
    """H3 r7 cells that touch Rwanda but hold no mapped building (lakes, parks): drawn grey, not left as holes.
    Candidates are the cells within 3 rings of a cell with buildings; those whose hexagon intersects the border
    are kept."""
    f = glob.glob(str(RAW_DIR / "admin_boundaries" / "*" / "geoBoundaries-RWA-ADM0.geojson"))[0]
    con = duckdb.connect()
    con.sql("LOAD spatial; LOAD h3")
    con.execute("CREATE TABLE have AS SELECT UNNEST(?::VARCHAR[]) AS h", [sorted(have)])
    cells = con.sql(f"""
        WITH cand AS (SELECT DISTINCT UNNEST(h3_grid_disk(h, 3)) AS h FROM have),
             rw AS (SELECT geom FROM st_read('{f}'))
        SELECT cand.h FROM cand, rw
        WHERE cand.h NOT IN (SELECT h FROM have)
          AND ST_Intersects(rw.geom, ST_GeomFromText(h3_cell_to_boundary_wkt(cand.h)))""").fetchall()
    return sorted(c[0] for c in cells)


def cell_ring(cell: str) -> list:
    con = duckdb.connect()
    con.sql("LOAD h3")
    wkt = con.sql(f"SELECT h3_cell_to_boundary_wkt('{cell}')").fetchone()[0]
    pts = [tuple(map(float, p.split())) for p in wkt.split("((")[1].split("))")[0].split(", ")]
    return [list(p) for p in pts]


HEX = {  # site key -> (bundle property, decimals or None to copy)
    "district": ("district", None), "n": ("n_buildings", None), "cool": ("cooling_class", None),
    "t2m": ("t2m_mean_c", 1), "tmax": ("tmax_p95_c", 1), "cdd22": ("cdd22_per_year", 1),
    "ghi": ("ghi_kwh_m2_day", None), "rwi": ("rwi_mean", None), "pop": ("pop_density_mean", 0),
    "grid": ("dist_powerline_m_median", 0), "ghsl": ("ghsl_class_mode", None), "elev": ("elevation_m", 0),
    "nmod": ("n_modelled", None), "mwh": ("annual_mwh_modelled", 1), "mwhE": ("annual_mwh_expected", 1),
}
HEX_TAIL = {"rh": ("rh_mean_pct", 1), "rain": ("precip_mm_per_year", 0)}
HEX_END = {"puB": ("pu_buildings", None), "svB": ("svc_buildings", None), "hh": ("households", None),
           "pg": ("p_connected_mean", None), "ntl16": ("ntl_radiance_2016", None),
           "ntl24": ("ntl_radiance_2024", None), "lit24": ("ntl_lit_share_2024", None)}

DISTRICT = {  # site key -> (bundle property, decimals or None, scale)
    "n": ("n_buildings", None, 1), "footprint_km2": ("footprint_m2", 2, 1e-6), "pop": ("pop_density_mean", 0, 1),
    "rwi": ("rwi_mean", 2, 1), "grid": ("dist_powerline_m_median", 0, 1), "low": ("share_low", None, 1),
    "medium": ("share_medium", None, 1), "high": ("share_high", None, 1), "nmod": ("n_modelled", None, 1),
    "mwh": ("annual_mwh_modelled", 1, 1), "puR": ("pu_census_reported", 0, 1), "puU": (None, None, 1),
    "puM": (None, None, 1), "puB": ("pu_buildings", None, 1), "svR": ("svc_census_reported", 0, 1),
    "svB": ("svc_buildings", None, 1), "hh": ("households", None, 1), "hhR": ("households_census_reported", None, 1),
    "mwhE": ("annual_mwh_expected", 1, 1), "urb": ("households_urban_share", None, 1),
    "upper": ("households_upper_share", None, 1), "schools": ("schools", 0, 1), "health": ("health_facilities", 0, 1),
    "worship": ("places_of_worship", 0, 1), "pgH": ("p_grid_households", None, 1),
    "eicv7": ("eicv7_grid_share_2024", None, 1), "growth": ("growth_rate", 1, 100),
    "e16": ("expected_gwh_2016", 2, 1), "e30": ("expected_gwh_2030", 2, 1), "ntl16": ("ntl_radiance_2016", 2, 1),
    "ntl24": ("ntl_radiance_2024", 2, 1),
}


def pick(props: dict, spec: dict) -> dict:
    out = {}
    for k, (src, nd, *scale) in spec.items():
        v = props.get(src) if src else None
        if v is not None and scale and scale[0] != 1:
            v = v * scale[0]
        out[k] = r(v, nd) if (nd is not None and isinstance(v, int | float)) else v
    return out


def national(years: list[dict], with_empty: bool = True) -> dict:
    dg = read(FULL / "admin" / "districts.geojson")
    frame = Frame(np.array([c for f in dg["features"] for ring in rings(f["geometry"]) for c in ring]), 0.01)
    hourly = read(FULL / "national" / "climate_hourly.json")
    monthly = read(FULL / "national" / "climate_monthly.json")
    hexes = []
    for f in read(FULL / "national" / "h3_r7.geojson")["features"]:
        p, h = f["properties"], f["properties"]["h3_r7"]
        m = monthly.get(h, {})
        hexes.append({"h": h, "d": frame.path(rings(f["geometry"]))} | pick(p, HEX) | {"t": hourly.get(h)}
                     | pick(p, HEX_TAIL) | {"tm": m.get("t2m_c"), "pm": m.get("precip_mm")} | pick(p, HEX_END))
    land = land_area_km2()
    districts = []
    for f in dg["features"]:
        p = f["properties"]
        pts = np.array([c for ring in rings(f["geometry"]) for c in ring])
        cx, cy = frame.xy(pts[:, 0].mean(), pts[:, 1].mean())
        districts.append({"name": p["adm2_name"], "province": p["adm1_name"], "d": frame.path(rings(f["geometry"])),
                          "cx": round(cx, 1), "cy": round(cy, 1)} | pick(p, DISTRICT)
                         | {"land_km2": land.get(p["adm2_name"])})
    deep = []
    for dd_id in load_yaml("dashboard.yaml")["deep_dives"]:
        s = read(FULL / "deep_dives" / dd_id / "summary.json")
        x, y = frame.xy((s["bbox"][0] + s["bbox"][2]) / 2, (s["bbox"][1] + s["bbox"][3]) / 2)
        deep.append({"id": dd_id, "sector": s["sector"], "district": s["district"], "x": round(x, 1), "y": round(y, 1)})
    med = lambda vals: round(statistics.median(vals), 1)  # noqa: E731
    out = {"viewBox": [0, 0, 1000, frame.height], "hexes": hexes, "districts": districts, "deep_dives": deep,
           "median_hourly": [med([v[i] for v in hourly.values()]) for i in range(24)],
           "median_monthly_t2m": [med([v["t2m_c"][i] for v in monthly.values()]) for i in range(12)],
           "median_monthly_precip": [round(statistics.median([v["precip_mm"][i] for v in monthly.values()]), 0)
                                     for i in range(12)],
           "years": years, "hexes_empty": []}
    if with_empty:
        out["hexes_empty"] = [{"h": c, "d": frame.path([cell_ring(c)]), "empty": True}
                              for c in empty_cells({h["h"] for h in hexes})]
    return out


# ------------------------------------------------------------------------------------------------ deep dives
BUILDING = {"id": "bldg_id", "label": "label", "conf": "confidence", "mod": "modelled", "kwh": "annual_kwh",
            "pk": "peak_w", "ph": "peak_hour", "kwhT": "annual_kwh_top", "pkT": "peak_w_top", "cool": "cooling_class",
            "pu": "pu_score", "puc": "pu_category", "h": "height_m", "fl": "est_floors", "yr": "first_seen_year",
            "src": "source", "area": "area_m2", "use": "use", "est": "establishment", "hh": "households",
            "usrc": "use_source", "pg": "p_connected", "kwhE": "annual_kwh_expected", "agr": "use_agreement"}


def subdistrict(dd_id: str) -> dict:
    base = SAMPLE / "deep_dives" / dd_id
    feats = read(base / "buildings.geojson")["features"]
    frame = Frame(np.array([c for f in feats for c in f["geometry"]["coordinates"][0]]), 0.0002)
    mpu = frame.metres / 1000
    buildings = []
    for f in feats:
        p, ring = f["properties"], np.array(f["geometry"]["coordinates"][0])
        b = {k: p.get(v) for k, v in BUILDING.items()}
        b["d"] = frame.path([ring.tolist()])
        x, y = frame.xy(ring[:, 0], ring[:, 1])
        b["cx"], b["cy"] = round(float(x.mean()), 1), round(float(y.mean()), 1)
        buildings.append({k: b[k] for k in ["id", "d", *list(BUILDING)[1:], "cx", "cy"]})
    ids = {b["id"] for b in buildings}
    details = {}
    for f in sorted((base / "details").glob("*.json")):
        details |= {k: v for k, v in read(f).items() if k in ids}
    w = read(base / "weights.json")
    sample = read(base / "summary.json")
    full = read(FULL / "deep_dives" / dd_id / "summary.json")
    return {"id": dd_id, "sector": sample["sector"], "district": sample["district"],
            "description": sample["description"], "viewBox": [0, 0, 1000, frame.height],
            "metres_per_unit": round(mpu, 4), "buildings": buildings, "order": w["profile_order"],
            "w": w["probabilities"], "wT": w["top_class"], "puOrder": w["pu_criteria_order"], "puc": w["pu_criteria"],
            "puGroup": w["pu_group"], "details": details, "summary_full": full, "summary_sample": sample}


# ------------------------------------------------------------------------------------------------ site, profiles
def national_years() -> list[dict]:
    rows = read(FULL / "national" / "demand_by_year.json")["rows"]
    by = {}
    for row in rows:
        y = by.setdefault(row["year"], {"year": row["year"], "kind": row["kind"], "pot": 0.0, "exp": 0.0})
        y["pot"] += row["potential_gwh"]
        y["exp"] += row["expected_gwh"]
    return [{**y, "pot": round(y["pot"], 1), "exp": round(y["exp"], 1)} for _, y in sorted(by.items())]


def site(years: list[dict]) -> dict:
    summaries = {}
    for dd_id in load_yaml("dashboard.yaml")["deep_dives"]:
        s = read(FULL / "deep_dives" / dd_id / "summary.json")
        summaries[dd_id] = {k: v for k, v in s.items() if not k.startswith("units_by_profile")}  # in subdistrict_*
    return {"meta": read(SAMPLE / "meta.json"), "checks": read(FULL / "validation" / "checks.json"),
            "sources": read(FULL / "sources" / "sources.json"), "workflow": read(FULL / "workflow" / "workflow.json"),
            "years": years, "full_summaries": summaries}


def profiles() -> dict:
    return read(FULL / "profiles" / "profiles.json") | {"evidence": read(FULL / "profiles" / "evidence_index.json")}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--no-empty-cells", action="store_true", help="skip the slow H3 coverage of empty cells")
    a = ap.parse_args()
    years = national_years()
    files = {"national.json": national(years, not a.no_empty_cells), "site.json": site(years),
             "profiles.json": profiles()}
    for dd_id in load_yaml("dashboard.yaml")["deep_dives"]:
        files[f"subdistrict_{dd_id}.json"] = subdistrict(dd_id)
    for name, obj in files.items():
        write(a.out / name, obj)
    for name, src in load_yaml("dashboard.yaml")["site"]["figures"].items():
        (a.out / "figures").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPORTS_DIR / "figures" / src, a.out / "figures" / name)
    manifest = {}
    for p in sorted(a.out.rglob("*")):
        if p.is_file() and not p.name.startswith("_"):
            manifest[str(p.relative_to(a.out))] = {"bytes": p.stat().st_size,
                                                   "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
    (REPO_ROOT / "app" / "data-files.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"{len(manifest)} site data files -> {a.out}")


if __name__ == "__main__":
    main()
