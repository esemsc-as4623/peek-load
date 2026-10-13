"""B3s: services demand (bars/restaurants, guesthouses, salons), located on tagged buildings plus an area amount.

    pixi run svc-demand

Same pattern as productive use (rtl.demand.productive_use), rules in config/services.yaml:
1. Census: establishments of ISIC sections I (accommodation, food) and S (other services) per district and setting,
   split into profiles by assumed shares.
2. Located: buildings with a bar/restaurant/hotel/salon tag (OSM, Overture) get that profile.
3. Unlocated: census count - located, spread over eligible buildings in proportion to population density, then
   summed per H3 r7 cell, sector and district. Reported per area only, never on a building.

Outputs (data/processed/): svc_located.parquet, svc_unlocated_areas.parquet, svc_census_districts.csv
"""

from __future__ import annotations

import pandas as pd

from rtl.demand import productive_use as pu
from rtl.settings import PROCESSED_DIR, load_yaml


def census(pu_cfg: dict, cfg: dict) -> pd.DataFrame:
    """(district, setting, profile_id, census): establishments per profile, from the section counts x split."""
    rows = []
    for section, name in cfg["census"]["sections"].items():
        c = pu.census_counts(pu_cfg, name)
        for pid, share in cfg["split"][section].items():
            rows.append(c.assign(profile_id=pid, census=c.pu_census * share, section=section))
    return pd.concat(rows, ignore_index=True)[["district", "setting", "section", "profile_id", "enterprises",
                                               "pu_share", "census"]]


def allocate_areas(con, cfg: dict, cen: pd.DataFrame, loc: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    loc_n = loc.groupby(["district", "setting", "profile_id"]).size()
    per_building, district_rows = [], []
    wcol = cfg["allocation"]["weight"]
    for (district, setting), g in cen.groupby(["district", "setting"]):
        df = con.sql(f"SELECT bldg_id, h3_r7, sector, coalesce({wcol}, 0) AS w FROM b WHERE eligible AND district = ? "
                     "AND setting = ? AND bldg_id NOT IN (SELECT bldg_id FROM taken)",
                     params=[district, setting]).df()
        for r in g.itertuples(index=False):
            n_loc = int(loc_n.get((district, setting, r.profile_id), 0))
            n_unloc = max(0.0, r.census - n_loc)
            district_rows.append({"district": district, "setting": setting, "profile_id": r.profile_id,
                                  "census": round(r.census, 1), "located": n_loc, "unlocated": round(n_unloc, 1),
                                  "eligible_buildings": len(df),
                                  "not_allocated": round(n_unloc, 1) if not len(df) else 0.0})
            if not len(df) or n_unloc <= 0:
                continue
            w = df.w.to_numpy(float) + 1.0  # +1 keeps buildings with no WorldPop value eligible
            per_building.append(df[["h3_r7", "sector"]].assign(units=n_unloc * w / w.sum(), profile_id=r.profile_id,
                                                                district=district))
    alloc = pd.concat(per_building, ignore_index=True)
    areas = pd.concat([
        alloc.groupby(["h3_r7", "district", "profile_id"]).units.sum().reset_index()
             .rename(columns={"h3_r7": "area_id"}).assign(level="h3_r7"),
        alloc.groupby(["sector", "district", "profile_id"]).units.sum().reset_index()
             .rename(columns={"sector": "area_id"}).assign(level="sector"),
        alloc.groupby(["district", "profile_id"]).units.sum().reset_index()
             .assign(area_id=lambda d: d.district, level="district")], ignore_index=True)
    return areas[["level", "area_id", "district", "profile_id", "units"]], pd.DataFrame(district_rows)


def main() -> None:
    cfg, pu_cfg = load_yaml("services.yaml"), load_yaml("productive_use.yaml")
    map_cfg = load_yaml("profile_mapping.yaml")
    cen = census(pu_cfg, cfg)
    con = pu.connect()
    pu.building_table(con, map_cfg, pu_cfg)
    loc = pu.located(con, cfg)
    pu_loc = pd.read_parquet(PROCESSED_DIR / "pu_located.parquet")
    loc = loc[~loc.bldg_id.isin(pu_loc.bldg_id)]  # a trade tag wins over a service tag
    con.register("taken", pd.concat([loc[["bldg_id"]], pu_loc[["bldg_id"]]]))
    areas, districts = allocate_areas(con, cfg, cen, loc)
    loc.to_parquet(PROCESSED_DIR / "svc_located.parquet", index=False)
    areas.assign(units=areas.units.round(4)).to_parquet(PROCESSED_DIR / "svc_unlocated_areas.parquet", index=False)
    districts.to_csv(PROCESSED_DIR / "svc_census_districts.csv", index=False)
    tot = districts.groupby("profile_id")[["census", "located", "unlocated", "not_allocated"]].sum().round(0)
    print(f"located {len(loc)} service buildings\n{tot.to_string()}")


if __name__ == "__main__":
    main()
