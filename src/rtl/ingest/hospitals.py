"""P5: curate the healthsites.io "hospital" points into the Ministry of Health's hospitals (rules in
config/health_facilities.yaml).

    pixi run hospitals

1. Units of a hospital (laboratory, office, dialysis centre, ...) join the nearest hospital within `merge_m`;
   specialist facilities (eye, dental, maternity, ...) are clinics.
2. Points with the same normalised name, or closer than `merge_m`, or listed as aliases, are one hospital.
3. Public and referral hospitals (named district, provincial, referral, teaching, ...) are always hospitals; the
   other "X Hospital" points fill the places left up to MoH's 2023 count, largest building compound first; the rest
   are clinics.
Output: data/processed/registries/health_facilities.csv (facility_id, name, role, canonical_id, compound_m2,
public), one row per healthsites "hospital" point.
"""

from __future__ import annotations

import re

import duckdb
import numpy as np
import pandas as pd

from rtl.settings import INTERIM_DIR, PROCESSED_DIR, load_yaml

OUT = PROCESSED_DIR / "registries" / "health_facilities.csv"
STOP = ["district", "hospital", "referral", "provincial", "level two teaching", "teaching", "national", "rwanda",
        "ihospital", "neuropsychiatric", "hopital"]


def key(name: str) -> str:
    n = re.sub(r"\(.*?\)|[^a-z ]", " ", name.lower())
    for w in STOP:
        n = n.replace(w, " ")
    return " ".join(n.split())


def metres(a: pd.Series, b: pd.DataFrame) -> np.ndarray:
    return 111320 * np.hypot(b.lat - a.lat, (b.lon - a.lon) * np.cos(np.radians(a.lat)))


def compound_area(con, pts: pd.DataFrame, radius: float) -> pd.Series:
    con.register("pts", pts[["facility_id", "lat", "lon"]])
    return con.sql(f"""
        WITH c AS (SELECT facility_id, lat AS flat, lon AS flon,
                          unnest(h3_grid_disk(h3_latlng_to_cell_string(lat, lon, 9), 2)) AS h3_r9 FROM pts)
        SELECT facility_id, sum(b.area_m2) AS m2 FROM c JOIN '{INTERIM_DIR}/buildings_base.parquet' b USING (h3_r9)
        WHERE 111320 * sqrt(pow(b.lat - flat, 2) + pow((b.lon - flon) * cos(radians(flat)), 2)) <= {radius}
        GROUP BY 1""").df().set_index("facility_id").m2


def curate(f: pd.DataFrame, cfg: dict, compound: pd.Series) -> pd.DataFrame:
    """Planted-case-testable core: f has facility_id, name, lat, lon; compound maps facility_id -> m2."""
    h = cfg["hospitals"]
    low = f.name.fillna("").str.lower()
    f = f.assign(role=np.where(low.str.contains(h["unit_patterns"]), "unit",
                               np.where(low.str.contains(h["clinic_patterns"]), "clinic", "hospital")))
    f.loc[low.str.strip().isin(["hospital", ""]), "role"] = "clinic"   # unnamed points: not a named hospital
    hosp = f[f.role == "hospital"].copy()
    hosp["key"] = hosp.name.map(key)
    first = {n: grp[0] for grp in h["aliases"] for n in grp}
    hosp["key"] = [key(first[n]) if n in first else k for n, k in zip(hosp.name, hosp.key, strict=True)]
    parent = {i: i for i in hosp.index}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    aliased = hosp.name.isin(first)
    for i in hosp.index:
        d = metres(hosp.loc[i], hosp)
        same = hosp.key == hosp.at[i, "key"]
        near = (d < h["merge_m"]) | (same & (d < 20_000)) | (same & aliased & aliased.loc[i])
        for j in hosp.index[near]:
            parent[find(i)] = find(j)
    hosp["group"] = [find(i) for i in hosp.index]
    hosp["compound_m2"] = hosp.facility_id.map(compound).fillna(0)
    hosp["public"] = ~hosp.name.str.lower().str.contains(h["private_patterns"])
    g = hosp.groupby("group").agg(public=("public", "all"), m2=("compound_m2", "max"))
    target = cfg["counts_2023"]["hospitals"]
    keep = set(g[g.public].index)
    rest = g[~g.public].sort_values("m2", ascending=False)
    keep |= set(rest.index[:max(0, target - len(keep))])

    def canonical(grp):  # an alias group's first name, else the point with the largest compound
        m = hosp[hosp.group == grp]
        firsts = m[m.name.isin([a[0] for a in h["aliases"]])]
        return (firsts if len(firsts) else m.sort_values("compound_m2")).facility_id.iloc[-1]

    canon = {grp: canonical(grp) for grp in g.index}
    hosp["role"] = np.where(hosp.group.isin(keep), "hospital", "clinic")
    hosp["canonical_id"] = np.where(hosp.role == "hospital", hosp.group.map(canon), hosp.facility_id)
    f = f.merge(hosp[["facility_id", "canonical_id", "compound_m2", "public"]], on="facility_id", how="left")
    f.loc[f.role != "hospital", "role"] = f.role  # units / clinics as classified above
    f.loc[f.facility_id.isin(hosp.facility_id[hosp.role == "clinic"]), "role"] = "clinic"
    # units join the nearest kept hospital within merge_m, else become clinics
    kept = f[f.role == "hospital"]
    for i in f.index[f.role == "unit"]:
        d = metres(f.loc[i], kept)
        if len(kept) and d.min() <= h["merge_m"]:
            f.at[i, "canonical_id"] = kept.canonical_id.iloc[int(np.argmin(d.to_numpy()))]
        else:
            f.at[i, "role"] = "clinic"
    f["canonical_id"] = f.canonical_id.fillna(f.facility_id)
    return f


def main() -> None:
    cfg = load_yaml("health_facilities.yaml")
    con = duckdb.connect()
    con.sql("INSTALL h3 FROM community; LOAD h3; LOAD spatial")
    f = con.sql(f"""SELECT facility_id, name, ST_Y(ST_Centroid(geometry)) AS lat, ST_X(ST_Centroid(geometry)) AS lon
                    FROM '{INTERIM_DIR}/layers/facilities.parquet' WHERE facility_type = 'health:hospital'""").df()
    out = curate(f, cfg, compound_area(con, f, cfg["hospitals"]["compound_m"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out[["facility_id", "name", "role", "canonical_id", "compound_m2", "public"]].to_csv(OUT, index=False)
    n = out[out.role == "hospital"].canonical_id.nunique()
    pub = int(out[out.role == "hospital"].groupby("canonical_id").public.all().sum())
    print(f"{len(out)} points -> {n} hospitals ({pub} public, {n - pub} private), {int((out.role == 'clinic').sum())} "
          f"clinics, {int((out.role == 'unit').sum())} units of a hospital; MoH 2023: {cfg['counts_2023']['hospitals']}"
          f" (15 referral, 3 provincial, 34 district, 11 private)")


if __name__ == "__main__":
    main()
