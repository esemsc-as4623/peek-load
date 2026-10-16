"""B3: RAMP profile library, plus a per-building demand preview built from it.

    pixi run profile-library                 # simulate every archetype JSON -> profile_library.parquet
    pixi run profile-library --buildings     # also combine with building_profiles -> building_demand_preview

RAMP runs once per profile type, not once per building: N_HOUSEHOLDS users of an archetype for DAYS days
(weekdays and weekends) give a per-household typical day, the spread of daily energy across days, and the
**coincident** peak per household, which is what matters when buildings are aggregated to a transformer or
mini-grid. Single-building (non-coincident) peaks need a separate small-group run; that's left for B4.

Results are only as good as the archetypes. While their status is `draft` (parameters not yet human-reviewed),
everything here is a pipeline check, not a demand estimate, and the outputs say so.
"""

from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from rtl.archetypes import Archetype, ramp_usecase
from rtl.settings import PROCESSED_DIR

ARCH_DIR = PROCESSED_DIR / "archetypes"
LIBRARY = PROCESSED_DIR / "profile_library.parquet"
PREVIEW = PROCESSED_DIR / "building_demand_preview.parquet"
N_HOUSEHOLDS = 200
DAYS = 28  # four weeks, so weekday/weekend appliances (wd_we_type) are both represented
START = "2025-01-06"
SEED = 20261010


def simulate(arch: Archetype, n: int = N_HOUSEHOLDS, days: int = DAYS, seed: int = SEED) -> np.ndarray:
    """Aggregate load of n users, W, shape (days, 1440)."""
    end = (pd.Timestamp(START) + pd.Timedelta(days=days - 1)).strftime("%Y-%m-%d")
    uc = ramp_usecase(arch, n, START, end, seed)  # users first, then seed, then the peak window
    return np.asarray(uc.generate_daily_load_profiles(), dtype=float).reshape(days, 1440)


def summarise(arch: Archetype, load: np.ndarray, n: int = N_HOUSEHOLDS) -> dict:
    per_hh = load / n
    daily_kwh = per_hh.sum(axis=1) / 60 / 1000
    by_hour = per_hh.reshape(load.shape[0], 24, 60).mean(axis=2)  # (days, 24)
    hourly_w = by_hour.mean(axis=0)
    weekend = (pd.Timestamp(START).dayofweek + np.arange(load.shape[0])) % 7 >= 5
    peaks = per_hh.max(axis=1)
    return {"profile_id": arch.archetype_id, "status": arch.status, "n_households": n, "days": load.shape[0],
            "daily_kwh_mean": daily_kwh.mean(), "daily_kwh_p10": np.quantile(daily_kwh, 0.1),
            "daily_kwh_p90": np.quantile(daily_kwh, 0.9), "annual_kwh_mean": daily_kwh.mean() * 365,
            "coincident_peak_w_p50": np.median(peaks), "coincident_peak_w_p90": np.quantile(peaks, 0.9),
            "kwh_day_all_units_on": arch.expected_daily_kwh(n) / n,
            **{f"w_h{h:02d}": hourly_w[h] for h in range(24)},
            **{f"w_weekday_h{h:02d}": by_hour[~weekend, h].mean() if (~weekend).any() else np.nan for h in range(24)},
            **{f"w_weekend_h{h:02d}": by_hour[weekend, h].mean() if weekend.any() else np.nan for h in range(24)}}


def build_library() -> pd.DataFrame:
    rows = []
    for path in sorted(ARCH_DIR.glob("*.json")):
        if path.name.endswith(".schema.json"):
            continue
        arch = Archetype.load(path)
        rows.append(summarise(arch, simulate(arch)))
        r = rows[-1]
        print(f"{arch.archetype_id} [{arch.status}]: {r['daily_kwh_mean']:.2f} kWh/day per unit "
              f"(p10-p90 {r['daily_kwh_p10']:.2f}-{r['daily_kwh_p90']:.2f}), coincident peak "
              f"{r['coincident_peak_w_p50']:.0f} W (p90 {r['coincident_peak_w_p90']:.0f} W)")
    lib = derived_profiles(pd.DataFrame(rows))
    lib.to_parquet(LIBRARY, index=False)
    return lib


def derived_profiles(lib: pd.DataFrame) -> pd.DataFrame:
    """Scaled copies of built profiles (config/archetypes.yaml `derived`): every energy and power column of the
    base is multiplied by `factor`, or by the ratio that gives `daily_kwh_from` (config/health_facilities.yaml).
    A derived id equal to its base replaces it. Status: human_reviewed if `signed_off`, else draft."""
    from rtl.settings import load_yaml

    spec = load_yaml("archetypes.yaml").get("derived", {})
    health = load_yaml("health_facilities.yaml")["profiles"]
    cols = [c for c in lib.columns if c.startswith(("daily_kwh", "annual_kwh", "coincident_peak", "kwh_day", "w_"))]
    out = lib.set_index("profile_id")
    built = out.copy()  # scale from the built profile even where a derived id replaces it
    for pid, d in spec.items():
        base = built.loc[d["base"]].copy()
        f = (float(health[d["daily_kwh_from"]]["daily_kwh"]) / base.daily_kwh_mean if "daily_kwh_from" in d
             else float(d["factor"]))
        base[cols] = base[cols].astype(float) * f
        base["status"] = "human_reviewed" if d.get("signed_off") else "draft"
        out.loc[pid] = base
        print(f"{pid}: derived from {d['base']} x {f:.2f} -> {base.daily_kwh_mean:.1f} kWh/day per unit")
    return out.reset_index()


def building_preview(lib: pd.DataFrame) -> None:
    """Daily/annual kWh per building: potential = sum over profiles of weight x per-unit mean; expected multiplies
    each row by p_grid (rtl.demand.calibrate; 1 when not calibrated)."""
    bp = pd.read_parquet(PROCESSED_DIR / "building_profiles.parquet")
    if "p_grid" not in bp:
        bp["p_grid"] = 1.0
    have = set(lib.profile_id)
    missing = bp[~bp.profile_id.isin(have)].groupby("profile_id").weight.sum().sort_values(ascending=False)
    covered = bp[bp.profile_id.isin(have)].merge(lib[["profile_id", "daily_kwh_mean", "status"]], on="profile_id")
    covered["daily_kwh"] = covered.weight * covered.daily_kwh_mean
    covered["expected_daily_kwh"] = covered.daily_kwh * covered.p_grid
    out = covered.groupby("bldg_id")[["daily_kwh", "expected_daily_kwh"]].sum().reset_index()
    g = bp.assign(ok_w=bp.weight * bp.profile_id.isin(have)).groupby("bldg_id")[["ok_w", "weight"]].sum()
    share = (g.ok_w / g.weight.where(g.weight > 0)).fillna(0.0)
    out = out.merge(share.rename("profile_weight_covered").reset_index(), on="bldg_id", how="right").fillna(
        {"daily_kwh": 0.0, "expected_daily_kwh": 0.0})
    out["annual_kwh"] = out.daily_kwh * 365
    out["expected_annual_kwh"] = out.expected_daily_kwh * 365
    out["note"] = "demand from profiles without an archetype JSON is missing"
    out.to_parquet(PREVIEW, index=False)
    print(f"preview for {len(out):,} buildings; potential {out.annual_kwh.sum() / 1e6:,.0f} GWh/yr, expected "
          f"{out.expected_annual_kwh.sum() / 1e6:,.0f} GWh/yr; {out.profile_weight_covered.mean():.0%} of profile "
          f"weight has an archetype. Missing archetypes (expected units):\n{missing.round(0).astype(int).to_string()}")
    (PROCESSED_DIR / "missing_archetypes.json").write_text(json.dumps(missing.round(1).to_dict(), indent=2) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--buildings", action="store_true", help="also write the per-building demand preview")
    args = ap.parse_args()
    lib = build_library()
    if args.buildings:
        building_preview(lib)


if __name__ == "__main__":
    main()
