"""A4: household wealth by district and residence from DHS 2025 (restricted microdata -> published aggregate).

    pixi run dhs-wealth

Share of households in the two richest national wealth quintiles (hv270 "richer" or "richest"), weighted with
hv005, per district x urban/rural (hv025). Cells with fewer than 10 households are suppressed (left blank).

The household profiles split households by Ubudehe category (MTF 2022: `upper` = categories 3-4), not by DHS
quintile. So the allocation keeps MTF's national upper share per setting and lets DHS spread it across districts:
target_upper(district, setting) = DHS share(district, setting) x MTF upper share(setting) / DHS share(setting),
capped to [0.02, 0.98]. Both national shares are written alongside.
Output: data/processed/dhs_wealth_district.csv (district, setting, households, dhs_upper_share, target_upper_share).
"""

from __future__ import annotations

import pandas as pd

from rtl.ingest import mtf_appliances
from rtl.settings import DATA_DIR, PROCESSED_DIR

DHS = DATA_DIR / "restricted" / "dhs" / "RW_2025_DHS_10092026_81_260427" / "RWHR91DT" / "RWHR91FL.dta"
OUT = PROCESSED_DIR / "dhs_wealth_district.csv"
MIN_N = 10


def mtf_upper_share() -> pd.Series:
    """Weighted share of households in Ubudehe categories 3-4 per setting (MTF 2022; don't-knows left out)."""
    df, _ = mtf_appliances.load()
    df = df[df.wealth.notna()]
    return df.groupby("setting").apply(lambda d: (d.HH_WT * (d.wealth == "upper")).sum() / d.HH_WT.sum(),
                                       include_groups=False)


def main() -> None:
    hr = pd.read_stata(DHS, columns=["hv005", "hv025", "hv270", "shdistrict"])
    hr["w"] = hr.hv005 / 1e6
    hr["upper"] = hr.hv270.astype(str).isin(["richer", "richest"]).astype(float)
    hr["setting"] = hr.hv025.astype(str)
    g = hr.groupby([hr.shdistrict.astype(str).str.title(), "setting"], observed=True)
    out = g.apply(lambda d: pd.Series({"households": len(d), "dhs_upper_share": (d.w * d.upper).sum() / d.w.sum()}),
                  include_groups=False).reset_index()
    out.columns = ["district", "setting", "households", "dhs_upper_share"]
    out.loc[out.households < MIN_N, "dhs_upper_share"] = None
    dhs_nat = hr.groupby("setting").apply(lambda d: (d.w * d.upper).sum() / d.w.sum(), include_groups=False)
    mtf = mtf_upper_share()
    out["target_upper_share"] = (out.dhs_upper_share * out.setting.map(mtf) / out.setting.map(dhs_nat)).clip(0.02, 0.98)
    out["households"] = out.households.astype(int)
    out = out.round(3)
    out.to_csv(OUT, index=False)
    print(f"{len(out)} district x setting cells ({int(out.dhs_upper_share.isna().sum())} suppressed)")
    print("DHS top-two-quintile share by setting:", dhs_nat.round(3).to_dict())
    print("MTF Ubudehe 3-4 share by setting:", mtf.round(3).to_dict(), "->", OUT)


if __name__ == "__main__":
    main()
