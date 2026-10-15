"""B3s: services (bars/restaurants, guesthouses, salons) census targets and tagged buildings, for the allocation.

    pixi run svc-demand

Same pattern as productive use (rtl.demand.productive_use), rules in config/services.yaml:
1. Census: establishments of ISIC sections I (accommodation, food) and S (other services) per district and setting,
   split into profiles by assumed shares.
2. Located: buildings with a bar/restaurant/hotel/salon tag (OSM, Overture) get that profile.
The rest is placed on individual buildings by rtl.demand.allocate.

Outputs (data/processed/): svc_located.parquet, svc_census_districts.csv
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


def main() -> None:
    cfg, pu_cfg = load_yaml("services.yaml"), load_yaml("productive_use.yaml")
    map_cfg = load_yaml("profile_mapping.yaml")
    cen = census(pu_cfg, cfg)
    con = pu.connect()
    pu.building_table(con, map_cfg, pu_cfg)
    loc = pu.located(con, cfg)
    pu_loc = pd.read_parquet(PROCESSED_DIR / "pu_located.parquet")
    loc = loc[~loc.bldg_id.isin(pu_loc.bldg_id)]  # a trade tag wins over a service tag
    loc_n = loc.groupby(["district", "setting", "profile_id"]).size()
    districts = cen.assign(census=cen.census.round(1), located=[int(loc_n.get((r.district, r.setting, r.profile_id), 0))
                                                                for r in cen.itertuples(index=False)])
    loc.to_parquet(PROCESSED_DIR / "svc_located.parquet", index=False)
    districts[["district", "setting", "section", "profile_id", "census", "located"]].to_csv(
        PROCESSED_DIR / "svc_census_districts.csv", index=False)
    print(f"located {len(loc)} service buildings\n"
          f"{districts.groupby('profile_id')[['census', 'located']].sum().round(0).to_string()}")


if __name__ == "__main__":
    main()
