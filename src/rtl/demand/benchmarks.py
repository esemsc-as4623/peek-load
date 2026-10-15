"""B4v: the model against official REG, RURA and Ministry of Health figures (after demand-years).

    pixi run demand-benchmarks

These figures are not used to fit anything except where noted (REG's mean consumption per customer is the household
energy constraint), so the comparison is an independent check. Each row: what REG / NISR report, what the model
gives for the same scope, and the ratio. Scopes differ (REG customers are meters, not households; non-residential
includes businesses we don't model), so the notes matter more than a pass/fail.

Outputs: reports/demand/benchmarks.md, data/processed/benchmarks.csv
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from rtl.settings import PROCESSED_DIR, REPORTS_DIR

A = PROCESSED_DIR / "validation_anchors"
OUT = PROCESSED_DIR / "benchmarks.csv"
REPORT = REPORTS_DIR / "demand" / "benchmarks.md"
KIGALI = ("Nyarugenge", "Gasabo", "Kicukiro")
RESIDENTIAL_SHARE_OF_CUSTOMERS = 0.88  # Mugyenyi et al. 2025 (REG billing 2013-2020): 88% of customers residential
HEALTH = ("health_post", "health_centre", "hospital")


def profile_energy() -> pd.DataFrame:
    """Annual GWh per profile, potential and expected (base year), and the national coincident peak curve (MW)."""
    lib = pd.read_parquet(PROCESSED_DIR / "profile_library.parquet").set_index("profile_id")
    bp = pd.read_parquet(PROCESSED_DIR / "building_profiles.parquet")
    g = bp.assign(exp=bp.weight * bp.p_grid).groupby("profile_id")[["weight", "exp", "units"]].sum()
    g["potential_gwh"] = g.weight * lib.daily_kwh_mean.reindex(g.index) * 365 / 1e6
    g["expected_gwh"] = g.exp * lib.daily_kwh_mean.reindex(g.index) * 365 / 1e6
    hours = [f"w_h{h:02d}" for h in range(24)]
    g.attrs["peak_curve_mw"] = (lib.loc[g.index, hours].mul(g.exp, axis=0).sum() / 1e6).to_numpy()
    return g


def main() -> None:
    sales = pd.read_csv(A / "reg_sales_by_category.csv")
    yr = sales[sales.period_type == "year"].pivot_table(index="category", columns="period", values="kwh") / 1e6
    q = sales[(sales.period_type == "quarter") & sales.period.isin(["2023Q3", "2023Q4", "2024Q1", "2024Q2"])]
    last4 = q.groupby("category").kwh.sum() / 1e6
    cust = pd.read_csv(A / "reg_customers_by_branch.csv")
    fac = pd.read_csv(A / "health_facilities_by_type.csv")
    fac23 = fac[fac.year == 2023].set_index("facility_type").facilities
    years = pd.read_csv(PROCESSED_DIR / "demand_by_year.csv")
    nat = years.groupby("year")[["expected_household_gwh", "connected_households", "expected_gwh"]].sum()
    g = profile_energy()
    use = pd.read_parquet(PROCESSED_DIR / "building_use.parquet", columns=["district", "establishment", "est_weight"])
    n_est = use.groupby("establishment").est_weight.sum()

    rows = []

    def add(item, official, model, unit, source, note):
        rows.append({"item": item, "official": official, "model": model,
                     "ratio": model / official if official else np.nan, "unit": unit, "source": source, "note": note})

    for y in (2021, 2022, 2023):
        add(f"Residential electricity {y}", float(yr.at["residential", str(y)]),
            float(nat.at[y, "expected_household_gwh"]), "GWh/yr", "NISR Yearbook 2024, Table 7.4 (REG)",
            "households' expected demand in that year (back-cast); REG's mean per customer is the household "
            "constraint, so the level is not independent, but the year-to-year change is")
    add("Residential electricity, Q3 2023 - Q2 2024", float(last4["residential"]),
        float(g.loc[g.index.str.startswith("household_"), "expected_gwh"].sum()), "GWh/yr", "RURA Q2 2024, Table 3",
        "base-year expected household demand")
    for y in (2017, 2020, 2023):
        reg = float(cust[(cust.branch == "Rwanda") & (cust.year == y)].customers.iloc[0])
        add(f"Residential customers {y}", reg * RESIDENTIAL_SHARE_OF_CUSTOMERS, float(nat.at[min(y, 2023),
                                                                                             "connected_households"]),
            "households vs meters", "NISR Yearbook 2024, Table 7.2 (REG) x 0.88 residential",
            "model counts households lighting with grid power (EICV); several households can share a meter")
    hlth = g.loc[g.index.isin(HEALTH)]
    add("Health facilities electricity", float(yr.at["health facilities", "2023"]), float(hlth.expected_gwh.sum()),
        "GWh/yr", "NISR Yearbook 2024, Table 7.4 (REG); RURA Q2 2024 gives 23.6 over the last four quarters",
        "health-facility tariff only; private clinics may buy as non-residential")
    for t, n in hlth.expected_gwh.items():
        add(f"  of which {t.replace('_', ' ')}", np.nan, float(n), "GWh/yr", "model",
            f"{n_est.get(t, 0):,.0f} facilities")
    hospitals = fac23[["National Referral Hospitals", "Provincial Hospital", "District Hospitals",
                       "Private Hospital"]].sum()
    add("Hospitals (count)", float(hospitals), float(n_est.get("hospital", 0)), "facilities",
        "NISR Yearbook 2024, Table 3.10.2 (R-HMIS, 2023)", "the model's hospitals are healthsites.io 'hospital' tags")
    add("Health centres (count)", float(fac23["Health Centers"]), float(n_est.get("health_centre", 0)), "facilities",
        "NISR Yearbook 2024, Table 3.10.2", "tagged health centres only; the rest are untagged")
    add("Health posts, clinics and dispensaries (count)", float(fac23[["Health Posts", "Private Clinics",
                                                                        "Private Dispensaries",
                                                                        "Prison Dispensaries"]].sum()),
        float(n_est.get("health_post", 0)), "facilities", "NISR Yearbook 2024, Table 3.10.2", "tagged facilities only")
    add("Hotels electricity", float(yr.at["hotels", "2023"]), float(g.loc[["guesthouse"], "expected_gwh"].sum()),
        "GWh/yr", "NISR Yearbook 2024, Table 7.4 (REG)",
        "the model's guesthouses are small (8 rooms); REG's hotel tariff covers large hotels too")
    other = g.drop(index=[i for i in g.index if i.startswith("household_") or i in HEALTH or i == "guesthouse"])
    add("Non-residential + small industry electricity", float(yr.at["non-residential", "2023"]
                                                              + yr.at["industries small", "2023"]),
        float(other.expected_gwh.sum()), "GWh/yr", "NISR Yearbook 2024, Table 7.4 (REG)",
        "shops, services, workshops, mills, schools, offices, churches, stores; REG also counts businesses with no "
        "building-level census count (banks, offices of firms, petrol stations)")
    curve = g.attrs["peak_curve_mw"]
    add("Coincident peak of modelled demand", 233.74, float(curve.max()), "MW",
        "RURA Q2 2024 (system peak, Q2 2024)", f"model peak at {int(curve.argmax())}:00; the system peak also "
        "carries industry, water pumping, telecoms, street lights and losses")

    # customers per branch (Kigali = its three districts) vs connected households, 2023
    c23 = cust[(cust.year == 2023) & (cust.branch != "Rwanda")].set_index("branch").customers
    m23 = years[years.year == 2023].assign(branch=lambda d: np.where(d.district.isin(KIGALI), "Kigali", d.district))
    m23 = m23.groupby("branch").connected_households.sum()
    both = pd.concat([c23.rename("reg"), m23.rename("model")], axis=1).dropna()
    r = float(np.corrcoef(both.reg, both.model)[0, 1])
    rest = both.drop(index="Kigali")
    r27 = float(np.corrcoef(rest.reg, rest.model)[0, 1])
    ratio = rest.model / rest.reg
    add("Customers per branch 2023 (28 branches)", float(both.reg.sum()), float(both.model.sum()),
        "households vs meters", "NISR Yearbook 2024, Table 7.2 (REG)",
        f"correlation r = {r:.2f} with Kigali (one branch for 3 districts), {r27:.2f} across the other 27; "
        f"model/REG per branch {ratio.min():.2f}-{ratio.max():.2f} (lowest {ratio.idxmin()}, highest {ratio.idxmax()})")

    out = pd.DataFrame(rows)
    out.to_csv(OUT, index=False)
    lines = ["# The model against REG, RURA and Ministry of Health figures", "",
             "Generated by `pixi run demand-benchmarks`. Ratio = model / official. Scopes differ: read the notes.", "",
             "| item | official | model | ratio | unit | source | note |", "|---|---|---|---|---|---|---|"]
    def f(v: float) -> str:
        return "" if pd.isna(v) else f"{v:,.1f}" if abs(v) < 1e4 else f"{v:,.0f}"

    for x in out.itertuples():
        lines.append(f"| {x.item} | {f(x.official)} | {f(x.model)} | "
                     f"{'' if pd.isna(x.ratio) else f'{x.ratio:.2f}'} | {x.unit} | {x.source} | {x.note} |")
    REPORT.write_text("\n".join(lines) + "\n")
    print(out[["item", "official", "model", "ratio"]].round(2).to_string(index=False))


if __name__ == "__main__":
    main()
