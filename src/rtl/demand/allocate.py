"""B2b: allocate census counts of uses to individual buildings, rarest first (rules in config/allocation.yaml).

    pixi run allocate

Inputs: the classifier's probabilities (rtl.demand.use_model), tags, the Establishment Census targets prepared by
rtl.demand.productive_use and rtl.demand.services, census retail and household counts per district.
Every district's assigned counts equal its census targets unless it runs out of eligible buildings (reported as a
shortfall). Each building gets at most one establishment and a whole number of households; a shop may share a
building with a household. Nothing is left as an area amount.

Outputs (data/processed/):
  building_use.parquet        bldg_id, district, use, establishment, households, source, score
  building_profiles.parquet   bldg_id, profile_id, weight (the demand weights every later step reads)
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


def load_inputs(con) -> pd.DataFrame:
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
    df = context_flags(df, map_cfg)  # adds urban / rich (the household profile split)
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
    out["households"] = hh.set_index("district").households_total
    return {k: v.round().astype(int) for k, v in out.items()}


def allocate(df: pd.DataFrame, cfg: dict, tgt: dict[str, pd.Series], factors: pd.Series) -> tuple[pd.DataFrame,
                                                                                                    pd.DataFrame]:
    pcal = calibrated(df, factors)
    pu_cfg = load_yaml("productive_use.yaml")
    crit = pu.criteria(df)
    est = pd.Series(pd.NA, index=df.index, dtype="object")      # establishment profile
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

    def place(item: str, profile: str, pool: pd.Series, score: pd.Series, cls: str) -> None:
        tagged = (est == profile).groupby(df.district).sum()
        need = (tgt[item].reindex(tagged.index, fill_value=0) - tagged).clip(lower=0)
        sp = cfg["spacing"][profile]
        pick = take_spaced(df, pool & est.isna() & use.isna(), score, need, df[sp["cell"]], sp["cap"])
        est[pick], use[pick], source[pick] = profile, cls, "census"
        got = pick.groupby(df.district).sum()
        for d in need.index:
            rows.append({"district": d, "item": item, "target": int(tgt[item].get(d, 0)), "tagged": int(tagged[d]),
                         "assigned": int(got.get(d, 0)), "shortfall": int(max(0, need[d] - got.get(d, 0)))})

    # 1. productive use: mills rural and roadside, town trades urban; best viability x not-ancillary first
    for prof in cfg["order"]["productive_use"]:
        w = pu_cfg["candidate_score"]["town" if prof in pu.TOWN_TRADES else prof]
        score = pu.candidate_score(crit, w) / 100 * not_anc
        pool = df.pu_eligible.astype(bool) & (df.setting == ("rural" if prof == "grain_mill" else "urban"))
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
    # facility tags choose the institutional profile
    fac = df.facility_type.fillna("")
    est[(use == "institutional") & fac.str.startswith("education")] = "school"
    est[(use == "institutional") & fac.str.startswith("health")] = "health_post"
    # 5. households: tagged homes first, then the most likely homes; shops may host one (shop-house)
    home_ok = (use.isna() | (est == "kiosk_shop")) & (df.area_m2 >= cfg["eligibility"]["household_min_area_m2"])
    hscore = pcal.residential + pcal.mixed_shop_house + (t == "residential").astype(float)
    hh = fill_households(df, home_ok, hscore, tgt["households"])
    tagged_hh = pd.Series(0, index=tgt["households"].index)
    got = hh.groupby(df.district).sum()
    for d in tgt["households"].index:
        rows.append({"district": d, "item": "households", "target": int(tgt["households"][d]),
                     "tagged": int(tagged_hh[d]), "assigned": int(got.get(d, 0)),
                     "shortfall": int(max(0, tgt["households"][d] - got.get(d, 0)))})
    home = hh > 0
    use[home & (est == "kiosk_shop")] = "mixed_shop_house"
    use[home & use.isna()] = "residential"
    source[home & source.isna()] = "census"
    # 6. the rest
    use[use.isna()], source[source.isna()] = "ancillary", "remainder"
    out = df[["bldg_id", "district", "sector", "h3_r7"]].assign(
        use=use.astype(str), establishment=est, households=hh, source=source.astype(str),
        household_profile=[HOUSEHOLD_PROFILES[(bool(u), bool(r))] for u, r in zip(df.urban, df.rich, strict=True)])
    return out, pd.DataFrame(rows)


def weights(out: pd.DataFrame) -> pd.DataFrame:
    h = out[out.households > 0]
    e = out[out.establishment.notna()]
    return pd.concat([
        pd.DataFrame({"bldg_id": h.bldg_id, "profile_id": h.household_profile, "weight": h.households.astype(float)}),
        pd.DataFrame({"bldg_id": e.bldg_id, "profile_id": e.establishment.astype(str), "weight": 1.0})],
        ignore_index=True)


def main() -> None:
    cfg = load_yaml("allocation.yaml")
    con = duckdb.connect()
    con.sql("INSTALL h3 FROM community; LOAD h3; SET memory_limit='8GB'")
    df = load_inputs(con)
    factors = calibration(df)
    tgt = targets(cfg)
    out, rows = allocate(df, cfg, tgt, factors)
    out.drop(columns="household_profile").to_parquet(PROCESSED_DIR / "building_use.parquet", index=False)
    w = weights(out)
    w.to_parquet(PROCESSED_DIR / "building_profiles.parquet", index=False)
    rows.to_csv(TARGETS_PATH, index=False)
    summ = rows.groupby("item")[["target", "tagged", "assigned", "shortfall"]].sum()
    print("calibration factors:", factors.round(3).to_dict())
    print("LLM rates by setting:", setting_rates(df)[list(cfg["uncensused"])].round(4).to_dict())
    print(summ.to_string())
    print("national use mix:", out.use.value_counts(normalize=True).round(4).to_dict())
    print(f"{w.bldg_id.nunique():,} buildings carry demand; {len(w):,} building-profile weights")


if __name__ == "__main__":
    main()
