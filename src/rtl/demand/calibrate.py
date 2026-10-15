"""B4c: calibrate building demand to the grid and consumption totals we hold (after allocate, profile-library and
grid-model).

    pixi run calibrate

1. Grid connection (A9): the grid model's P(connected) is shifted per district on the logit scale until the
   household-weighted mean equals EICV7 2024's share of households lighting with grid power.
2. Household consumption (REG billing, via QSEL 2024): household energy is scaled per region so that a connected
   household's mean consumption equals REG's residential mean (Kigali 297 kWh/yr, rest of the country 71 kWh/yr).
   QSEL's rural survey mean (8.1 kWh/month) is reported as a check.

Establishments keep p_grid = 1: their energy stays a potential (no establishment-level connection data).
building_profiles gains: weight = units x scale (the energy weight every later step reads), scale, p_grid.
Expected demand = weight x p_grid x profile energy; potential demand = weight x profile energy.
Output: building_profiles.parquet (rewritten), calibration.csv, then the per-building preview (rtl.demand.runner).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from rtl.demand.runner import LIBRARY, building_preview
from rtl.settings import PROCESSED_DIR

KIGALI = {"Nyarugenge", "Gasabo", "Kicukiro"}
ANCHORS = PROCESSED_DIR / "validation_anchors"
OUT = PROCESSED_DIR / "calibration.csv"


def logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def shift_to_mean(p: np.ndarray, w: np.ndarray, target: float) -> float:
    """Logit shift s with sum(w * sigmoid(logit(p) + s)) / sum(w) = target (bisection; the mean rises with s)."""
    lo, hi, z = -15.0, 15.0, logit(p)
    for _ in range(60):
        mid = (lo + hi) / 2
        if np.average(1 / (1 + np.exp(-(z + mid))), weights=w) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def reg_targets() -> dict[str, float]:
    q = pd.read_csv(ANCHORS / "residential_kwh_qsel.csv")
    year = q[q.parameter == "annual_kwh.mean"].set_index("population").value
    return {"kigali": year["REG residential customers in Kigali"] / 12,
            "rest": year["REG residential customers outside Kigali"] / 12}


def main() -> None:
    use = pd.read_parquet(PROCESSED_DIR / "building_use.parquet", columns=["bldg_id", "district", "setting"])
    bp = pd.read_parquet(PROCESSED_DIR / "building_profiles.parquet")
    lib = pd.read_parquet(LIBRARY).set_index("profile_id")
    grid = pd.read_parquet(PROCESSED_DIR / "grid_connection.parquet", columns=["bldg_id", "p_connected"])
    eicv = pd.read_csv(ANCHORS / "grid_lighting_by_district_2024.csv").set_index("district").grid_main_lighting_share
    bp = bp.merge(use, on="bldg_id").merge(grid, on="bldg_id", how="left")
    hh = bp.profile_id.str.startswith("household_")
    bp["p_grid"] = 1.0
    rows = []
    for d, g in bp[hh].groupby("district"):
        p = g.p_connected.fillna(g.p_connected.mean()).to_numpy()
        s = shift_to_mean(p, g.units.to_numpy(), float(eicv[d]))
        bp.loc[g.index, "p_grid"] = 1 / (1 + np.exp(-(logit(p) + s)))
        rows.append({"level": "district", "key": d, "item": "grid_share", "target": float(eicv[d]),
                     "before": float(np.average(p, weights=g.units)), "factor": s})
    kwh_month = bp.profile_id.map(lib.daily_kwh_mean) * 30.4
    region = np.where(bp.district.isin(KIGALI), "kigali", "rest")
    bp["scale"] = 1.0
    for r, target in reg_targets().items():
        m = hh & (region == r)
        w = bp.units[m] * bp.p_grid[m]
        current = float((w * kwh_month[m]).sum() / w.sum())
        bp.loc[m, "scale"] = target / current
        rows.append({"level": "region", "key": r, "item": "household_kwh_month", "target": target,
                     "before": current, "factor": target / current})
    rural = hh & (bp.setting == "rural")
    w = bp.units[rural] * bp.p_grid[rural]
    rows.append({"level": "setting", "key": "rural", "item": "household_kwh_month_check_qsel", "target": 8.1,
                 "before": float((w * kwh_month[rural] * bp.scale[rural]).sum() / w.sum()), "factor": np.nan})
    bp["weight"] = bp.units * bp.scale
    bp[["bldg_id", "profile_id", "units", "weight", "scale", "p_grid"]].to_parquet(
        PROCESSED_DIR / "building_profiles.parquet", index=False)
    cal = pd.DataFrame(rows)
    cal.to_csv(OUT, index=False)
    print(cal[cal.level != "district"].round(3).to_string(index=False))
    d = cal[cal.level == "district"]
    print(f"grid shift per district: {d.factor.min():+.2f} to {d.factor.max():+.2f} logits; "
          f"connected households {float((bp.units * bp.p_grid)[hh].sum()):,.0f} of {float(bp.units[hh].sum()):,.0f}")
    building_preview(lib.reset_index())


if __name__ == "__main__":
    main()
