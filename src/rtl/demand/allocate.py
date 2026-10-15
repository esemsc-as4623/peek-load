"""B2b: allocate census counts of uses to individual buildings, rarest first (rules in config/allocation.yaml).

    pixi run allocate

Inputs: the classifier's probabilities (rtl.demand.use_model), tags, facility points (schools, health), the
Establishment Census targets prepared by rtl.demand.productive_use and rtl.demand.services, census retail and
household counts per district (urban and rural), MINEDUC school counts, a national count of places of worship and
DHS 2025 wealth shares per district x setting.
Every district's assigned counts equal its census targets unless it runs out of eligible buildings (reported as a
shortfall). Each building gets at most one establishment and a whole number of households; a shop may share a
building with a household. Nothing is left as an area amount.

Outputs (data/processed/):
  building_use.parquet        bldg_id, district, setting, use, establishment, est_weight, households, upper, source
  building_profiles.parquet   bldg_id, profile_id, units (households, or the building's share of an establishment);
                              rtl.demand.calibrate adds the energy weight, p_grid and the REG scale
  allocation_targets.csv      district x item: target, tagged, assigned, shortfall
"""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from rtl.demand import productive_use as pu
from rtl.demand.mapping import LABELER, context_flags
from rtl.settings import INTERIM_DIR, PROCESSED_DIR, load_yaml

USE_PATH = PROCESSED_DIR / "building_use.parquet"
TARGETS_PATH = PROCESSED_DIR / "allocation_targets.csv"
CLASSES = ["residential", "mixed_shop_house", "commercial", "institutional", "religious", "productive_use",
           "industrial_warehouse", "ancillary"]
HOUSEHOLD_PROFILES = {(True, True): "household_urban_upper", (True, False): "household_urban_lower",
                      (False, True): "household_rural_upper", (False, False): "household_rural_lower"}


def calibration(probs: pd.DataFrame) -> pd.Series:
    """Per-class factors that rescale model B's class shares to the LLM's mix on the deep dives (all buildings
    labelled there, so a representative sample; the tag-trained shares over-represent institutions)."""
    lab = duckdb.sql(f"""SELECT bldg_id, label FROM '{PROCESSED_DIR}/labels.parquet'
                         WHERE labeler = '{LABELER}' AND label <> 'unknown'""").df()
    d = probs[probs.bldg_id.isin(lab.bldg_id)]
    want = lab.label.value_counts(normalize=True).reindex(CLASSES, fill_value=0)
    have = d[[f"p_{c}" for c in CLASSES]].mean().set_axis(CLASSES)
    return (want / have).replace([np.inf, np.nan], 0).clip(upper=50)


def setting_rates(df: pd.DataFrame) -> pd.DataFrame:
    """Share of buildings in each class per setting (urban / rural) among the LLM-labelled deep-dive buildings."""
    lab = duckdb.sql(f"""SELECT bldg_id, label FROM '{PROCESSED_DIR}/labels.parquet'
                         WHERE labeler = '{LABELER}'""").df()
    m = lab.merge(df[["bldg_id", "setting"]], on="bldg_id")
    return pd.crosstab(m.setting, m.label, normalize="index").reindex(columns=CLASSES, fill_value=0.0)


def calibrated(probs: pd.DataFrame, factors: pd.Series) -> pd.DataFrame:
    p = probs[[f"p_{c}" for c in CLASSES]].to_numpy() * factors.to_numpy()
    p = p / p.sum(1, keepdims=True).clip(min=1e-9)
    return pd.DataFrame(p, columns=CLASSES, index=probs.index)


def take_top(df: pd.DataFrame, pool: pd.Series, score: pd.Series, need: pd.Series) -> pd.Series:
    """Boolean mask: within each district, the `need[district]` eligible buildings with the highest score."""
    s = score.where(pool)
    rank = s.groupby(df.district).rank(ascending=False, method="first")
    return pool & (rank <= df.district.map(need).fillna(0))


def take_spaced(df: pd.DataFrame, pool: pd.Series, score: pd.Series, need: pd.Series, cell: pd.Series,
                cap: int, max_doublings: int = 8) -> pd.Series:
    """take_top, but at most `cap` picks per spatial cell (a mill serves a catchment; bars cluster, but only so much).
    A district that can't meet its need under the cap gets the cap doubled, and only that district, until it can."""
    in_cell = score.where(pool).groupby(cell).rank(ascending=False, method="first")
    pick = pd.Series(False, index=df.index)
    todo = need[need > 0]
    for k in range(max_doublings + 1):
        if todo.empty:
            break
        mine = df.district.isin(todo.index)
        p = take_top(df, pool & mine & (in_cell <= cap * 2 ** k), score, todo)
        got = p.groupby(df.district).sum().reindex(todo.index, fill_value=0)
        done = got[got >= todo].index if k < max_doublings else todo.index
        pick |= p & df.district.isin(done)
        todo = todo.drop(done)
    return pick


def fill_households(df: pd.DataFrame, pool: pd.Series, score: pd.Series, need: pd.Series) -> pd.Series:
    """Households per building: the most likely homes first, up to one household per floor, until the district's
    census count is reached (the last building may take fewer)."""
    cap = df.est_floors.fillna(1).clip(lower=1, upper=10).where(pool, 0)
    order = df.assign(_s=score.where(pool, -1.0), _cap=cap).sort_values(["district", "_s"], ascending=[True, False])
    before = order.groupby("district")._cap.cumsum() - order._cap
    left = (order.district.map(need).fillna(0) - before).clip(lower=0)
    return np.minimum(order._cap, left).reindex(df.index).astype(int)


def facility_sites(con, cfg: dict) -> pd.DataFrame:
    """bldg_id -> nearest school / health facility (facility_id, profile, dist_m, tagged). A building tagged with a
    facility type joins the nearest facility of that type within `tagged_max_m`; any other building joins the
    nearest facility within `radius_m` of its group (education, health), and decides later if it is eligible."""
    f = cfg["facilities"]
    prof = ", ".join(f"('{k}', '{v}')" for k, v in f["profiles"].items())
    rad = ", ".join(f"('{k}', {v})" for k, v in f["radius_m"].items())
    return con.sql(f"""
        WITH fp(facility_type, profile) AS (VALUES {prof}), rad(grp, r) AS (VALUES {rad}),
        f AS (SELECT facility_id, facility_type, profile, split_part(facility_type, ':', 1) AS grp,
                     ST_Y(ST_Centroid(geometry)) AS flat, ST_X(ST_Centroid(geometry)) AS flon
              FROM '{INTERIM_DIR}/layers/facilities.parquet' JOIN fp USING (facility_type)),
        fc AS (SELECT *, unnest(h3_grid_disk(h3_latlng_to_cell_string(flat, flon, 9), 6)) AS h3_r9 FROM f),
        m AS (SELECT b.bldg_id, fc.facility_id, fc.profile, fc.facility_type, x.facility_type AS own_type,
                     111320 * sqrt(pow(b.lat - flat, 2) + pow((b.lon - flon) * cos(radians(flat)), 2)) AS dist_m,
                     fc.grp
              FROM '{INTERIM_DIR}/buildings_base.parquet' b JOIN fc USING (h3_r9)
              JOIN '{INTERIM_DIR}/building_context.parquet' x USING (bldg_id))
        SELECT bldg_id, arg_min(facility_id, dist_m) AS facility_id, arg_min(profile, dist_m) AS fac_profile,
               min(dist_m) AS fac_dist_m, bool_or(own_type = facility_type) AS fac_tagged
        FROM m JOIN rad USING (grp)
        WHERE (own_type = facility_type AND dist_m <= {f["tagged_max_m"]}) OR (own_type IS NULL AND dist_m <= r)
        GROUP BY 1""").df()


def census_setting(df: pd.DataFrame, slots: pd.Series, urban_share: pd.Series) -> pd.Series:
    """'urban' / 'rural' per building: within each district the densest buildings are urban until they hold the
    census share of urban households (slots = expected households a building can hold)."""
    o = df.assign(_slots=slots, _d=df.pop_density_per_km2.fillna(0)).sort_values(["district", "_d"],
                                                                                 ascending=[True, False])
    cum = o.groupby("district")._slots.cumsum() - o._slots
    tot = o.groupby("district")._slots.transform("sum")
    urban = cum < o.district.map(urban_share).fillna(0) * tot
    return np.where(urban.reindex(df.index), "urban", "rural")


def wealth_upper(df: pd.DataFrame, hh: pd.Series, setting: pd.Series, target: pd.DataFrame) -> pd.Series:
    """Upper (Ubudehe 3-4-like) household buildings: within each district x setting, the highest-RWI homes until
    they hold the DHS-based target share of that cell's households."""
    key = df.district + "|" + setting
    t = target.assign(key=target.district + "|" + target.setting).set_index("key").target_upper_share
    o = pd.DataFrame({"key": key, "hh": hh, "rwi": df.rwi.fillna(df.rwi.median()), "a": df.area_m2})
    o = o[o.hh > 0].sort_values(["key", "rwi", "a"], ascending=[True, False, False])
    cum = o.groupby("key").hh.cumsum() - o.hh
    upper = cum < o.key.map(t).fillna(0.5) * o.groupby("key").hh.transform("sum")
    return upper.reindex(df.index, fill_value=False)


def load_inputs(con, cfg: dict) -> pd.DataFrame:
    I, P = INTERIM_DIR, PROCESSED_DIR  # noqa: E741, N806
    map_cfg, pu_cfg = load_yaml("profile_mapping.yaml"), load_yaml("productive_use.yaml")
    df = con.sql(f"""
        SELECT b.bldg_id, b.area_m2, b.h3_r9, h3_cell_to_parent(b.h3_r9, 8) AS h3_r8,
               x.adm2_name AS district, x.adm3_name AS sector, x.h3_r7, h.est_floors,
               x.facility_type, x.pop_density_per_km2, x.ghsl_class, x.rwi, x.poi_count_250m, x.dist_road_major_m,
               x.dist_road_any_m, x.dist_powerline_m, c.cropland_share,
               {pu.setting_sql(map_cfg)} AS setting, {pu.eligible_sql(pu_cfg, s="b", label="NULL")} AS pu_eligible
        FROM '{I}/buildings_base.parquet' b JOIN '{I}/building_context.parquet' x USING (bldg_id)
        LEFT JOIN '{I}/building_height.parquet' h USING (bldg_id)
        LEFT JOIN '{I}/cropland_h3r8.parquet' c ON c.h3_r8 = h3_cell_to_parent(b.h3_r9, 8)
        WHERE x.adm2_name IS NOT NULL""").df()
    probs = pd.read_parquet(P / "use_probs.parquet")
    df = df.merge(probs, on="bldg_id", how="left")
    df = context_flags(df, map_cfg)  # adds urban / rich (superseded by the census setting and the DHS split)
    df = df.merge(facility_sites(con, cfg), on="bldg_id", how="left")
    return df


def targets(cfg: dict) -> dict[str, pd.Series]:
    """Census targets per district for every censused item."""
    pu_cen = pd.read_csv(PROCESSED_DIR / "pu_census_districts.csv")
    svc_cen = pd.read_csv(PROCESSED_DIR / "svc_census_districts.csv")
    out = {f"pu:{p}": g.groupby("district").pu_census.sum() for p, g in pu_cen.groupby("profile_id")}
    out |= {f"svc:{p}": g.groupby("district").census.sum() for p, g in svc_cen.groupby("profile_id")}
    retail = pu.census_counts(load_yaml("productive_use.yaml"), cfg["census"]["retail_section"])
    out["retail:kiosk_shop"] = retail.groupby("district").pu_census.sum()
    hh = pd.read_csv(PROCESSED_DIR / cfg["census"]["households"])
    hh = hh.set_index("district")
    out["households"] = hh.households_total
    out["households_urban"], out["households_rural"] = hh.households_urban, hh.households_rural
    sch = pd.read_csv(PROCESSED_DIR / cfg["registries"]["schools"]).set_index("district")
    out["registry:school"] = sch.schools
    w = cfg["registries"]["worship"]
    out["registry:church"] = hh.households_total / hh.households_total.sum() * w["national"]
    return {k: v.round().astype(int) for k, v in out.items()}


def allocate(df: pd.DataFrame, cfg: dict, tgt: dict[str, pd.Series], factors: pd.Series, noise: float = 0.0,
             seed: int = 0, cap_mult: float | None = 1.0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """`noise` > 0 multiplies every class probability by exp(noise x N(0, 1)) (uncertainty runs); `cap_mult` scales
    the spacing caps (None: no caps), for the sensitivity report."""
    pcal = calibrated(df, factors)
    if noise > 0:
        rng = np.random.default_rng(seed)
        pcal = pcal * np.exp(noise * rng.standard_normal(pcal.shape))
        pcal = pcal.div(pcal.sum(axis=1), axis=0)
    # A8: census setting per district (urban households share), used by every later step
    floors = df.est_floors.fillna(1).clip(lower=1, upper=10)
    home_size = df.area_m2 >= cfg["eligibility"]["household_min_area_m2"]
    slots = (pcal.residential + pcal.mixed_shop_house) * floors * home_size
    hh_cen = tgt["households_urban"] / tgt["households"].replace(0, np.nan)
    df = df.assign(setting=census_setting(df, slots, hh_cen))
    df["urban"] = df.setting == "urban"
    pu_cfg = load_yaml("productive_use.yaml")
    crit = pu.criteria(df)
    est = pd.Series(pd.NA, index=df.index, dtype="object")      # establishment profile
    est_w = pd.Series(1.0, index=df.index)                        # the building's share of its establishment
    use = pd.Series(pd.NA, index=df.index, dtype="object")
    source = pd.Series(pd.NA, index=df.index, dtype="object")
    rows = []

    # 0. tags: located trades and services, then tag classes
    pu_loc = pd.read_parquet(PROCESSED_DIR / "pu_located.parquet").set_index("bldg_id").profile_id
    svc_loc = pd.read_parquet(PROCESSED_DIR / "svc_located.parquet").set_index("bldg_id").profile_id
    t = df.tag_label
    # a facility / worship / warehouse / ancillary tag on the building itself beats a trade or service point matched
    # within 30 m (a hospital canteen, a church guesthouse); that establishment is then placed by the census step
    own = t.isin(["institutional", "religious", "industrial_warehouse", "ancillary"])
    tagged_est = df.bldg_id.map(pu_loc).fillna(df.bldg_id.map(svc_loc)).where(~own)
    est[tagged_est.notna()] = tagged_est[tagged_est.notna()]
    use[df.bldg_id.isin(pu_loc.index) & ~own] = "productive_use"
    use[df.bldg_id.isin(svc_loc.index) & ~own & use.isna()] = "commercial"
    for cls in ("institutional", "religious", "industrial_warehouse", "ancillary"):
        use[(t == cls) & use.isna()] = cls
    shop_tag = (t == "commercial") & est.isna()
    est[shop_tag], use[shop_tag] = "kiosk_shop", "commercial"
    source[use.notna()] = "tag"
    big = df.area_m2 >= cfg["eligibility"]["establishment_min_area_m2"]
    not_anc = 1 - pcal.ancillary

    est[(use == "religious") & est.isna()] = cfg["registries"]["worship"]["profile"]  # counts towards worship

    # 0b. facility compounds: tagged facility buildings, plus institution-like buildings close to a facility
    fp = df.fac_profile
    tagged_fac = df.fac_tagged.fillna(False).astype(bool) & fp.notna()
    near = (fp.notna() & ~tagged_fac & use.isna() & est.isna() & (fp != "kiosk_shop")
            & (pcal.institutional >= pcal.residential) & (df.area_m2 >= cfg["facilities"]["min_area_m2"]))
    pharm = tagged_fac & (fp == "kiosk_shop") & est.isna()
    est[pharm], use[pharm], source[pharm] = "kiosk_shop", "commercial", "tag"
    member = (tagged_fac | near) & (fp != "kiosk_shop") & est.isna()
    est[member], use[member] = fp[member], "institutional"
    source[member & near] = "facility"
    source[member & ~near] = "tag"
    n = member.groupby(df.facility_id.where(member)).transform("sum")
    est_w[member] = 1.0 / n[member]

    def place(item: str, profile: str, pool: pd.Series, score: pd.Series, cls: str) -> None:
        tagged = est_w.where(est == profile, 0.0).groupby(df.district).sum().round().astype(int)
        tagged = tagged.reindex(tgt[item].index.union(tagged.index), fill_value=0)
        need = (tgt[item].reindex(tagged.index, fill_value=0) - tagged).clip(lower=0)
        sp = cfg["spacing"][profile]
        cap = 10**9 if cap_mult is None else max(1, round(sp["cap"] * cap_mult))
        pick = take_spaced(df, pool & est.isna() & use.isna(), score, need, df[sp["cell"]], cap)
        est[pick], use[pick], source[pick] = profile, cls, "registry" if item.startswith("registry") else "census"
        got = pick.groupby(df.district).sum()
        for d in need.index:
            rows.append({"district": d, "item": item, "target": int(tgt[item].get(d, 0)),
                         "tagged": int(tagged.get(d, 0)),
                         "assigned": int(got.get(d, 0)), "shortfall": int(max(0, need[d] - got.get(d, 0)))})

    # 1. productive use: mills rural and roadside, town trades urban; best viability x not-ancillary first
    for prof in cfg["order"]["productive_use"]:
        w = pu_cfg["candidate_score"]["town" if prof in pu.TOWN_TRADES else prof]
        score = pu.candidate_score(crit, w) / 100 * not_anc
        rural = prof in pu_cfg["trade_mix"]["rural"]
        pool = df.pu_eligible.astype(bool) & (df.setting == ("rural" if rural else "urban"))
        if prof in pu_cfg["eligibility"]["roadside_m"]:
            pool &= df.dist_road_any_m.fillna(1e9) <= pu_cfg["eligibility"]["roadside_m"][prof]
        place(f"pu:{prof}", prof, pool, score, "productive_use")
    # 2. services, rarest first: P(commercial or shop-house) x customers nearby
    for prof in cfg["order"]["services"]:
        place(f"svc:{prof}", prof, big, (pcal.commercial + pcal.mixed_shop_house) * (0.5 + crit.demand_density),
              "commercial")
    # 3. retail shops
    place("retail:kiosk_shop", "kiosk_shop", big, pcal.commercial + pcal.mixed_shop_house, "commercial")
    # 4. uncensused classes: the LLM's rate per setting in the deep dives (a fully labelled, representative sample)
    #    x the district's urban and rural buildings, minus tagged ones; the classifier picks the buildings
    inst_ok = big & (df.area_m2 >= cfg["registries"]["school_min_area_m2"])
    place("registry:school", "school", inst_ok, pcal.institutional, "institutional")
    place("registry:church", cfg["registries"]["worship"]["profile"], big, pcal.religious, "religious")
    rates = setting_rates(df)
    for cls, prof in cfg["uncensused"].items():
        free = use.isna() & est.isna()
        tagged = (use == cls).groupby(df.district).sum()
        expected = (df.setting.map(rates[cls]).groupby(df.district).sum() - tagged.reindex(
            df.district.unique(), fill_value=0)).clip(lower=0).round().astype(int)
        pick = take_top(df, free & big, pcal[cls], expected)
        use[pick], source[pick] = cls, "model"
        est[(use == cls) & est.isna()] = prof
        got = pick.groupby(df.district).sum()
        for d in expected.index:
            rows.append({"district": d, "item": f"model:{cls}", "target": int(expected[d] + tagged.get(d, 0)),
                         "tagged": int(tagged.get(d, 0)), "assigned": int(got.get(d, 0)),
                         "shortfall": int(max(0, expected[d] - got.get(d, 0)))})
    # tagged facilities beyond reach of any facility point keep a profile by their type
    fac = df.facility_type.fillna("")
    est[(use == "institutional") & est.isna() & fac.str.startswith("education")] = "school"
    est[(use == "institutional") & est.isna() & fac.str.startswith("health")] = "health_post"
    est[(use == "religious") & est.isna()] = cfg["registries"]["worship"]["profile"]
    # 5. households: tagged homes first, then the most likely homes; shops may host one (shop-house)
    home_ok = (use.isna() | (est == "kiosk_shop")) & (df.area_m2 >= cfg["eligibility"]["household_min_area_m2"])
    hscore = pcal.residential + pcal.mixed_shop_house + (t == "residential").astype(float)
    hh = (fill_households(df, home_ok & df.urban, hscore, tgt["households_urban"])
          + fill_households(df, home_ok & ~df.urban, hscore, tgt["households_rural"]))
    short = (tgt["households"] - hh.groupby(df.district).sum()).clip(lower=0)  # a pool ran out: use the other one
    hh += fill_households(df, home_ok & (hh == 0), hscore, short)
    for item in ("households", "households_urban", "households_rural"):
        sel = {"households": df.urban | True, "households_urban": df.urban, "households_rural": ~df.urban}[item]
        got = hh.where(sel, 0).groupby(df.district).sum()
        for d in tgt[item].index:
            rows.append({"district": d, "item": item, "target": int(tgt[item][d]), "tagged": 0,
                         "assigned": int(got.get(d, 0)), "shortfall": int(max(0, tgt[item][d] - got.get(d, 0)))})
    wealth = pd.read_csv(PROCESSED_DIR / cfg["wealth"])
    upper = wealth_upper(df, hh, df.setting, wealth)
    home = hh > 0
    use[home & (est == "kiosk_shop")] = "mixed_shop_house"
    use[home & use.isna()] = "residential"
    source[home & source.isna()] = "census"
    # rural variants of profiles (P1: rural bars are lighter than town bars)
    for prof, rural_prof in cfg.get("rural_profiles", {}).items():
        est[(est == prof) & (df.setting == "rural")] = rural_prof
    # 6. the rest
    use[use.isna()], source[source.isna()] = "ancillary", "remainder"
    est_w[est.isna()] = np.nan
    out = df[["bldg_id", "district", "sector", "h3_r7", "setting"]].assign(
        use=use.astype(str), establishment=est, est_weight=est_w, households=hh, upper=upper, source=source.astype(str),
        household_profile=[HOUSEHOLD_PROFILES[(bool(u), bool(r))] for u, r in zip(df.urban, upper, strict=True)])
    return out, pd.DataFrame(rows)


def weights(out: pd.DataFrame) -> pd.DataFrame:
    h = out[out.households > 0]
    e = out[out.establishment.notna()]
    return pd.concat([
        pd.DataFrame({"bldg_id": h.bldg_id, "profile_id": h.household_profile, "units": h.households.astype(float)}),
        pd.DataFrame({"bldg_id": e.bldg_id, "profile_id": e.establishment.astype(str), "units": e.est_weight})],
        ignore_index=True)


def main() -> None:
    cfg = load_yaml("allocation.yaml")
    con = duckdb.connect()
    con.sql("INSTALL h3 FROM community; LOAD h3; INSTALL spatial; LOAD spatial; SET memory_limit='6GB'")
    df = load_inputs(con, cfg)
    factors = calibration(df)
    tgt = targets(cfg)
    out, rows = allocate(df, cfg, tgt, factors)
    out.drop(columns="household_profile").to_parquet(PROCESSED_DIR / "building_use.parquet", index=False)
    w = weights(out)
    w["weight"] = w.units  # energy weight; rtl.demand.calibrate rescales households to REG consumption
    w.to_parquet(PROCESSED_DIR / "building_profiles.parquet", index=False)
    rows.to_csv(TARGETS_PATH, index=False)
    summ = rows.groupby("item")[["target", "tagged", "assigned", "shortfall"]].sum()
    print("calibration factors:", factors.round(3).to_dict())
    print("urban share of households:", round(out[out.setting == "urban"].households.sum() / out.households.sum(), 3))
    print(summ.to_string())
    print("national use mix:", out.use.value_counts(normalize=True).round(4).to_dict())
    print(f"{w.bldg_id.nunique():,} buildings carry demand; {len(w):,} building-profile weights")


if __name__ == "__main__":
    main()
