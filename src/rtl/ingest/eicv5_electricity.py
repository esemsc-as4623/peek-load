"""B1 check: grid households' electricity use in 2017 from NISR EICV5 (aggregates only).

    pixi run eicv5-electricity

Reads the restricted EICV5 cross-sectional household file in data/restricted/reg/EICV5/ (never committed) and
publishes weighted aggregates of kWh/month for households whose main lighting source is code 1 of s5cq16.
The CSV export has no value labels; code 1 is grid electricity: it covers 26.8% of households and 90% of them
report paying for electricity (median 1,000 RWF per 4 weeks), while no other lighting code has more than a few
paying households except code 9 (7%, pay-as-you-go solar prices). ur: 1 urban, 2 rural.

kWh = s5cq17 "amount paid on electricity in last 4 weeks" x 30.4/28, / 1.18 VAT, inverted through the residential
block tariff. Interviews before 2017 are left out: the 89 RWF lifeline block started on 1 Jan 2017 (the flat rate
before it was about twice as high). The blocks come from the closest official schedule, the August 2018 gazette
(89 / 182 / 210 RWF/kWh; the >50 kWh block was 189 before August 2018, which matters for few households).

Output: data/processed/validation_anchors/eicv5_household_electricity.csv (validation only).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from rtl.ingest.mtf_appliances import MIN_N, kwh_from_spend, tariff_blocks, wquantile
from rtl.settings import PROCESSED_DIR, REPO_ROOT

SRC = (REPO_ROOT / "data/restricted/reg/EICV5/EICV5__2016-2017__Cross-Sectional_Sample/datasets/EICV5__Household"
       / "dataset/eicv5_s0_s5_household.csv")
OUT = PROCESSED_DIR / "validation_anchors" / "eicv5_household_electricity.csv"
GRID_LIGHTING = 1
SETTING = {1: "urban", 2: "rural"}


def main() -> pd.DataFrame:
    h = pd.read_csv(SRC, usecols=["hhid", "ur", "weight", "quintile", "s0q18y", "s5cq16", "s5cq17"])
    blocks = tariff_blocks("residential", doc_id="rura_tariffs_2018_gazette")
    rows = []
    for setting, g0 in [("all", h), *[(SETTING[k], g) for k, g in h.groupby("ur")]]:
        grid = g0.s5cq16.eq(GRID_LIGHTING)
        row = {"group": f"grid_lighting_{setting}", "n_households": len(g0),
               "grid_lighting_share": float(np.average(grid, weights=g0.weight))}
        g = g0[grid & g0.s0q18y.eq(2017) & g0.s5cq17.gt(0)]
        row["n_paying_2017"] = len(g)
        if len(g) >= MIN_N:
            w = g.weight.to_numpy(float)
            k = kwh_from_spend(g.s5cq17.to_numpy(float) * 30.4 / 28, blocks)
            row.update({"spend_rwf_4wk_median": wquantile(g.s5cq17.to_numpy(float), w, 0.5),
                        "kwh_month_median": wquantile(k, w, 0.5), "kwh_month_p25": wquantile(k, w, 0.25),
                        "kwh_month_p75": wquantile(k, w, 0.75), "kwh_month_mean": float(np.average(k, weights=w))})
        rows.append(row)
    out = pd.DataFrame(rows)
    out.insert(0, "evidence_id", "eicv5:RWA2017:kwh:" + out.group)
    out["method"] = ("s5cq17 (RWF, last 4 weeks) x 30.4/28 / 1.18 VAT through the Aug 2018 gazette residential blocks; "
                     "grid-lighting households (s5cq16 = 1) interviewed in 2017, paying > 0")
    out["source"] = "NISR EICV5 2016-17 cross-sectional household file (restricted; weighted aggregates)"
    out.round(3).to_csv(OUT, index=False)
    return out


if __name__ == "__main__":
    print(main().drop(columns=["method", "source"]).round(2).to_string(index=False))
