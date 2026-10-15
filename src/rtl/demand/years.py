"""B5y: building demand by year, back-cast to 2016 and projected to 2030 (rules in config/years.yaml).

    pixi run demand-years

Back-cast, building by building:
- presence: a building is present in year y if the presence model detected it (>= threshold) in y or earlier.
  Detection improves over time, so each district's stock is pinned to a census-scaled index
  I(d, y) = exp(-g_d (2023 - y)), where g_d is the district's log-growth of detected demand, rescaled so the
  national rate equals census household growth 2012-2022. Never-detected buildings (mostly small ones) get the
  presence share that makes the district total equal I(d, y); if they all count and it is still short, later-
  detected buildings get a share too.
- connection: households' P(connected) shifts on the logit scale per setting, linearly from 0 in 2024 to the
  shift that reproduces EICV5's 2017 share; establishments keep p = 1.
Projection 2024-2030: each district's 2023 demand grows at g_d (a district multiplier on every building; new
buildings cannot be placed), connection held at 2024.

Outputs: data/processed/demand_by_year.csv (district x year), building_demand_by_year.parquet (bldg_id,
expected_kwh_<year> for the back-cast years; potential = expected with p = 1 is in building_demand_preview).
"""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from rtl.demand.calibrate import logit, shift_to_mean
from rtl.settings import INTERIM_DIR, PROCESSED_DIR, load_yaml

OUT = PROCESSED_DIR / "demand_by_year.csv"
OUT_B = PROCESSED_DIR / "building_demand_by_year.parquet"


def first_detected(years: list[int], thr: float) -> pd.DataFrame:
    case = " ".join(f"WHEN presence_{y} >= {thr} THEN {y}" for y in years)
    return duckdb.sql(f"""SELECT bldg_id, CASE {case} END AS first_y
                          FROM '{INTERIM_DIR}/building_height.parquet'""").df()


def slope(years: np.ndarray, values: np.ndarray) -> float:
    """OLS slope of log(values) on years."""
    return float(np.polyfit(years, np.log(np.clip(values, 1e-9, None)), 1)[0])


def main() -> None:
    cfg = load_yaml("years.yaml")
    base, (y0, y1) = cfg["base_year"], cfg["backcast"]
    years = list(range(y0, y1 + 1))
    lib = pd.read_parquet(PROCESSED_DIR / "profile_library.parquet").set_index("profile_id").daily_kwh_mean
    bp = pd.read_parquet(PROCESSED_DIR / "building_profiles.parquet")
    use = pd.read_parquet(PROCESSED_DIR / "building_use.parquet", columns=["bldg_id", "district", "setting"])
    bp = bp.merge(use, on="bldg_id")
    bp["kwh"] = bp.weight * bp.profile_id.map(lib) * 365
    hh = bp.profile_id.str.startswith("household_").to_numpy()

    # connection by year: logit shift per setting, 0 in 2024, EICV5 share in 2017, linear in between
    g = cfg["grid_history"]
    shift_hist = {s: shift_to_mean(bp.p_grid[hh & (bp.setting == s)].to_numpy(),
                                   bp.units[hh & (bp.setting == s)].to_numpy(), share)
                  for s, share in g["share"].items()}
    z = logit(bp.p_grid.to_numpy())

    def p_in(year: int) -> np.ndarray:
        t = max(0, g["base_year"] - year) / (g["base_year"] - g["year"])  # 0 in 2024, 1 in 2017, extrapolated before
        s = bp.setting.map(shift_hist).to_numpy() * t
        return np.where(hh, 1 / (1 + np.exp(-(z + s))), 1.0)

    # per-building demand (all profiles summed) at each year's connection
    exp_y = {y: (bp.kwh * p_in(y)).groupby(bp.bldg_id).sum() for y in years}
    b = pd.DataFrame(exp_y).rename(columns=lambda y: f"expected_kwh_{y}")
    pot = bp.groupby("bldg_id").kwh.sum()
    b = b.join(use.set_index("bldg_id").district).join(pot.rename("potential_kwh"))
    b = b.join(first_detected(years, cfg["presence_threshold"]).set_index("bldg_id").first_y.astype(float))

    # growth: detected demand per district, rescaled to census household growth
    c = cfg["census_growth"]
    g_census = np.log(c["households_2022"] / c["households_2012"]) / 10
    det = b[b.first_y.notna()]
    total = det.groupby("district").potential_kwh.sum()
    raw = {y: det[det.first_y <= y].groupby("district").potential_kwh.sum() / total for y in years}
    raw = pd.DataFrame(raw)
    nat = np.array([det[det.first_y <= y].potential_kwh.sum() / det.potential_kwh.sum() for y in years])
    k = g_census / slope(np.array(years), nat)
    g_d = raw.apply(lambda r: slope(np.array(years), r.to_numpy()), axis=1) * k

    # presence weights per building and year, pinned to I(d, y)
    rows = []
    for y in years:
        idx = np.exp(-b.district.map(g_d) * (base - y))
        for d, grp in b.groupby("district"):
            e = grp[f"expected_kwh_{y}"]
            target = float(idx[grp.index].iloc[0] * e.sum())
            seen = (grp.first_y <= y).to_numpy()
            never = grp.first_y.isna().to_numpy()
            later = ~seen & ~never
            r_never = np.clip((target - e[seen].sum()) / max(e[never].sum(), 1e-9), 0, 1)
            r_later = np.clip((target - e[seen].sum() - e[never].sum()) / max(e[later].sum(), 1e-9), 0, 1)
            w = np.where(seen, 1.0, np.where(never, r_never, r_later)) if y < base else np.ones(len(grp))
            b.loc[grp.index, f"expected_kwh_{y}"] = (e * w).astype("float32")
            pot_y = float((grp.potential_kwh * w).sum())
            rows.append({"district": d, "year": y, "kind": "base" if y == base else "backcast",
                         "potential_gwh": pot_y / 1e6, "expected_gwh": float((e * w).sum()) / 1e6,
                         "stock_index": float(idx[grp.index].iloc[0]), "growth_rate": float(g_d[d]),
                         "presence_weight_undetected": float(r_never)})
    for y in range(base + 1, cfg["project_to"] + 1):
        for d, grp in b.groupby("district"):
            f = float(np.exp(g_d[d] * (y - base)))
            rows.append({"district": d, "year": y, "kind": "projection",
                         "potential_gwh": float(grp.potential_kwh.sum()) * f / 1e6,
                         "expected_gwh": float(grp[f"expected_kwh_{min(base, y)}"].sum()) * f / 1e6,
                         "stock_index": f, "growth_rate": float(g_d[d]), "presence_weight_undetected": np.nan})
    out = pd.DataFrame(rows)
    out.round(4).to_csv(OUT, index=False)
    b.drop(columns=["district", "potential_kwh", "first_y"]).reset_index().to_parquet(OUT_B, index=False)
    nat_y = out.groupby("year")[["potential_gwh", "expected_gwh"]].sum().round(0)
    print(f"growth: presence {slope(np.array(years), nat):.3f}/yr nationally, census {g_census:.3f}/yr -> x{k:.2f}; "
          f"districts {g_d.min():.3f} to {g_d.max():.3f}/yr; EICV5 logit shift {shift_hist}")
    print(nat_y.to_string())


if __name__ == "__main__":
    main()
