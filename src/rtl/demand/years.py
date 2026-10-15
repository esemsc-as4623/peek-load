"""B5y: building demand by year, back-cast to 2016 and projected to 2030 (rules in config/years.yaml).

    pixi run demand-years

Back-cast, building by building:
- presence: a building is present in year y if the presence model detected it (>= threshold) in y or earlier.
  Detection improves over time, so each district's stock is pinned to a census-scaled index
  I(d, y) = exp(-g_d (2023 - y)), where g_d is the district's log-growth of detected demand, rescaled so the
  national rate equals census household growth 2012-2022. Never-detected buildings (mostly small ones) get the
  presence share that makes the district total equal I(d, y); if they all count and it is still short, later-
  detected buildings get a share too.
- connection: households' P(connected) shifts on the logit scale per district, linearly in time between the
  shifts that reproduce EICV4 (2014) and EICV5 (2017) district grid-lighting shares and 0 in 2024 (EICV7, already
  raked); establishments keep p = 1.
Projection 2024-2030: each district's 2023 demand grows at g_d (a district multiplier on every building; new
buildings cannot be placed), connection held at 2024.

Outputs: data/processed/demand_by_year.csv (district x year; potential, expected, households' expected GWh and
connected households, for checks against REG sales and customers), building_demand_by_year.parquet (bldg_id,
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

    # connection by year: logit shift per district reproducing each survey's district share, 0 in 2024
    g = cfg["grid_history"]
    hist = pd.read_csv(PROCESSED_DIR / g["file"]).pivot_table(index="district", columns="year", values="grid_share")
    shifts = pd.DataFrame({a: {d: shift_to_mean(bp.p_grid[hh & (bp.district == d)].to_numpy(),
                                                bp.units[hh & (bp.district == d)].to_numpy(), float(hist.at[d, a]))
                               for d in hist.index} for a in g["anchors"]})
    shifts[g["base_year"]] = 0.0
    z = logit(bp.p_grid.to_numpy())

    def p_in(year: int) -> np.ndarray:
        cols = sorted(shifts.columns)
        s_d = shifts.apply(lambda r: float(np.interp(year, cols, r[cols].to_numpy())), axis=1)
        s = bp.district.map(s_d).to_numpy()
        return np.where(hh, 1 / (1 + np.exp(-(z + s))), 1.0)

    # per-building demand (all profiles summed) at each year's connection
    exp_y, hh_y, conn_y = {}, {}, {}
    for y in years:
        p = p_in(y)
        exp_y[y] = (bp.kwh * p).groupby(bp.bldg_id).sum()
        hh_y[y] = (bp.kwh * p * hh).groupby(bp.bldg_id).sum()      # households' part (REG "residential")
        conn_y[y] = (bp.units * p * hh).groupby(bp.bldg_id).sum()  # connected households
    b = pd.DataFrame(exp_y).rename(columns=lambda y: f"expected_kwh_{y}")
    side = {y: pd.DataFrame({"hh": hh_y[y], "conn": conn_y[y]}).reindex(b.index) for y in years}
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
            sd = side[y].loc[grp.index]
            rows.append({"district": d, "year": y, "kind": "base" if y == base else "backcast",
                         "potential_gwh": pot_y / 1e6, "expected_gwh": float((e * w).sum()) / 1e6,
                         "expected_household_gwh": float((sd.hh * w).sum()) / 1e6,
                         "connected_households": float((sd.conn * w).sum()),
                         "stock_index": float(idx[grp.index].iloc[0]), "growth_rate": float(g_d[d]),
                         "presence_weight_undetected": float(r_never)})
    for y in range(base + 1, cfg["project_to"] + 1):
        for d, grp in b.groupby("district"):
            f = float(np.exp(g_d[d] * (y - base)))
            sd = side[base].loc[grp.index]
            rows.append({"district": d, "year": y, "kind": "projection",
                         "potential_gwh": float(grp.potential_kwh.sum()) * f / 1e6,
                         "expected_gwh": float(grp[f"expected_kwh_{min(base, y)}"].sum()) * f / 1e6,
                         "expected_household_gwh": float(sd.hh.sum()) * f / 1e6,
                         "connected_households": float(sd.conn.sum()) * f,
                         "stock_index": f, "growth_rate": float(g_d[d]), "presence_weight_undetected": np.nan})
    out = pd.DataFrame(rows)
    out.round(4).to_csv(OUT, index=False)
    b.drop(columns=["district", "potential_kwh", "first_y"]).reset_index().to_parquet(OUT_B, index=False)
    nat_y = out.groupby("year")[["potential_gwh", "expected_gwh", "expected_household_gwh",
                                 "connected_households"]].sum().round(0)
    print(f"growth: presence {slope(np.array(years), nat):.3f}/yr nationally, census {g_census:.3f}/yr -> x{k:.2f}; "
          f"districts {g_d.min():.3f} to {g_d.max():.3f}/yr; logit shifts 2014 {shifts[2014].min():.2f} to "
          f"{shifts[2014].max():.2f}, 2017 {shifts[2017].min():.2f} to {shifts[2017].max():.2f}")
    print(nat_y.to_string())


if __name__ == "__main__":
    main()
