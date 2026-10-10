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

from rtl.archetypes import Archetype
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
    from ramp import UseCase

    end = (pd.Timestamp(START) + pd.Timedelta(days=days - 1)).strftime("%Y-%m-%d")
    uc = UseCase(name=arch.archetype_id, date_start=START, date_end=end, random_seed=seed)
    uc.add_user(arch.to_ramp(n, usecase=uc))
    return np.asarray(uc.generate_daily_load_profiles(), dtype=float).reshape(days, 1440)


def summarise(arch: Archetype, load: np.ndarray, n: int = N_HOUSEHOLDS) -> dict:
    per_hh = load / n
    daily_kwh = per_hh.sum(axis=1) / 60 / 1000
    hourly_w = per_hh.reshape(load.shape[0], 24, 60).mean(axis=(0, 2))
    peaks = per_hh.max(axis=1)
    return {"profile_id": arch.archetype_id, "status": arch.status, "n_households": n, "days": load.shape[0],
            "daily_kwh_mean": daily_kwh.mean(), "daily_kwh_p10": np.quantile(daily_kwh, 0.1),
            "daily_kwh_p90": np.quantile(daily_kwh, 0.9), "annual_kwh_mean": daily_kwh.mean() * 365,
            "coincident_peak_w_p50": np.median(peaks), "coincident_peak_w_p90": np.quantile(peaks, 0.9),
            "expected_daily_kwh_deterministic": arch.expected_daily_kwh(n) / n,
            **{f"w_h{h:02d}": hourly_w[h] for h in range(24)}}


def build_library() -> pd.DataFrame:
    rows = []
    for path in sorted(ARCH_DIR.glob("*.json")):
        if path.name.endswith(".schema.json"):
            continue
        arch = Archetype.load(path)
        rows.append(summarise(arch, simulate(arch)))
        r = rows[-1]
        print(f"{arch.archetype_id} [{arch.status}]: {r['daily_kwh_mean']:.2f} kWh/day per household "
              f"(p10-p90 {r['daily_kwh_p10']:.2f}-{r['daily_kwh_p90']:.2f}), coincident peak "
              f"{r['coincident_peak_w_p50']:.0f} W (p90 {r['coincident_peak_w_p90']:.0f} W)")
    lib = pd.DataFrame(rows)
    lib.to_parquet(LIBRARY, index=False)
    return lib


def building_preview(lib: pd.DataFrame) -> None:
    """Expected daily/annual kWh per building = sum over profiles of weight x per-unit mean (draft profiles only)."""
    bp = pd.read_parquet(PROCESSED_DIR / "building_profiles.parquet")
    have = set(lib.profile_id)
    missing = bp[~bp.profile_id.isin(have)].groupby("profile_id").weight.sum().sort_values(ascending=False)
    covered = bp[bp.profile_id.isin(have)].merge(lib[["profile_id", "daily_kwh_mean", "status"]], on="profile_id")
    covered["daily_kwh"] = covered.weight * covered.daily_kwh_mean
    out = covered.groupby("bldg_id").agg(daily_kwh=("daily_kwh", "sum")).reset_index()
    share = bp.assign(ok=bp.profile_id.isin(have)).groupby("bldg_id").apply(
        lambda g: (g.weight * g.ok).sum() / g.weight.sum() if g.weight.sum() else 0.0, include_groups=False)
    out = out.merge(share.rename("profile_weight_covered").reset_index(), on="bldg_id", how="right").fillna(
        {"daily_kwh": 0.0})
    out["annual_kwh"] = out.daily_kwh * 365
    out["note"] = "PREVIEW: draft archetypes; demand from profiles without an archetype JSON is missing"
    out.to_parquet(PREVIEW, index=False)
    print(f"preview for {len(out):,} buildings; {out.profile_weight_covered.mean():.0%} of profile weight has an "
          f"archetype. Missing archetypes (expected units):\n{missing.round(0).astype(int).to_string()}")
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
