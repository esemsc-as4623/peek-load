"""B6: spacing-cap sensitivity (A6) and allocation uncertainty bands (B6), re-running rtl.demand.allocate on inputs
loaded once.

    pixi run demand-sensitivity              # both (about 15 allocation runs, ~30 min)
    pixi run demand-sensitivity --caps       # cap sensitivity only
    pixi run demand-sensitivity --bands      # uncertainty runs only

Caps: the allocation with spacing caps x0.5, x1 (base), x2, x4 and none. Per establishment kind: H3 r8 cells used,
the busiest r9 cell, the share of establishments placed by a relaxed (doubled) cap, and productive-use energy in
the three deep-dive sectors.
Bands: N runs with every class probability multiplied by exp(0.5 x N(0, 1)) per building (seeded). Per building:
the share of runs giving it its base use. Per sector: potential energy p10 / p50 / p90 across runs (allocation
uncertainty), and the p50 run with every profile at its p10 / p90 daily energy (profile uncertainty); the combined
band adds the two relative half-widths in quadrature.
Outputs: reports/demand/cap_sensitivity.csv, data/processed/uncertainty/{sector_bands.csv, building_agreement.parquet}
"""

from __future__ import annotations

import argparse

import duckdb
import numpy as np
import pandas as pd

from rtl.demand import allocate as al
from rtl.settings import PROCESSED_DIR, REPO_ROOT, load_yaml

DEEP_DIVES = {"Nyamirambo": "Nyarugenge", "Muhoza": "Musanze", "Nasho": "Kirehe"}
PU = ["grain_mill", "welding_workshop", "tailoring", "carpentry"]
SPACED = PU + ["guesthouse", "salon", "bar_restaurant", "kiosk_shop"]
CAPS = {"x0.5": 0.5, "base": 1.0, "x2": 2.0, "x4": 4.0, "none": None}
NOISE, RUNS = 0.5, 10
OUT_CAPS = REPO_ROOT / "reports" / "demand" / "cap_sensitivity.csv"
OUT_DIR = PROCESSED_DIR / "uncertainty"


def energy(out: pd.DataFrame, kwh: pd.Series, scale: dict[str, float]) -> pd.DataFrame:
    """Annual potential kWh per building-profile row (households rescaled to REG by region, as in calibrate)."""
    w = al.weights(out).merge(out[["bldg_id", "district", "sector"]], on="bldg_id")
    hh = w.profile_id.str.startswith("household_")
    reg = np.where(w.district.isin({"Nyarugenge", "Gasabo", "Kicukiro"}), scale["kigali"], scale["rest"])
    w["kwh"] = w.units * np.where(hh, reg, 1.0) * w.profile_id.map(kwh) * 365
    return w


def cap_metrics(name: str, out: pd.DataFrame, df: pd.DataFrame, cfg: dict, kwh: pd.Series,
                scale: dict) -> list[dict]:
    base_cap = {p: cfg["spacing"][p]["cap"] for p in SPACED}
    w = energy(out, kwh, scale)
    rows = []
    for p in SPACED:
        m = (out.establishment == p).to_numpy()
        r8 = df.h3_r8[m]
        per9 = df.h3_r9[m].value_counts()
        row = {"caps": name, "profile_id": p, "establishments": int(m.sum()), "r8_cells": int(r8.nunique()),
               "max_per_r9_cell": int(per9.max()) if len(per9) else 0,
               "share_above_base_cap": float(per9[per9 > base_cap[p]].sum() - base_cap[p] * (per9 > base_cap[p]).sum())
               / max(1, m.sum())}
        for sec, d in DEEP_DIVES.items():
            sel = (w.profile_id == p) & (w.sector == sec) & (w.district == d)
            row[f"{sec}_n"] = float(w.units[sel].sum())
            row[f"{sec}_mwh"] = float(w.kwh[sel].sum()) / 1e3
        rows.append(row)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--caps", action="store_true")
    ap.add_argument("--bands", action="store_true")
    ap.add_argument("--runs", type=int, default=RUNS)
    args = ap.parse_args()
    both = not (args.caps or args.bands)
    cfg = load_yaml("allocation.yaml")
    con = duckdb.connect()
    con.sql("INSTALL h3 FROM community; LOAD h3; INSTALL spatial; LOAD spatial; SET memory_limit='6GB'")
    df = al.load_inputs(con, cfg)
    factors, tgt = al.calibration(df), al.targets(cfg)
    lib = pd.read_parquet(PROCESSED_DIR / "profile_library.parquet").set_index("profile_id")
    cal = pd.read_csv(PROCESSED_DIR / "calibration.csv")
    scale = cal[cal.level == "region"].set_index("key").factor.to_dict()

    if args.caps or both:
        rows = []
        for name, mult in CAPS.items():
            out, _ = al.allocate(df, cfg, tgt, factors, cap_mult=mult)
            rows += cap_metrics(name, out, df, cfg, lib.daily_kwh_mean, scale)
            print(f"caps {name}: done")
        res = pd.DataFrame(rows)
        OUT_CAPS.parent.mkdir(parents=True, exist_ok=True)
        res.round(4).to_csv(OUT_CAPS, index=False)
        print(res[res.profile_id.isin(["grain_mill", "bar_restaurant", "kiosk_shop"])][
            ["caps", "profile_id", "r8_cells", "max_per_r9_cell", "share_above_base_cap", "Nasho_n", "Nasho_mwh"]
        ].to_string(index=False))

    if args.bands or both:
        base, _ = al.allocate(df, cfg, tgt, factors)
        codes = {u: i for i, u in enumerate(sorted(al.CLASSES))}
        base_use = base.use.map(codes).to_numpy()
        agree = np.zeros(len(base), dtype=np.int16)
        sector_runs = []
        for k in range(args.runs):
            out, _ = al.allocate(df, cfg, tgt, factors, noise=NOISE, seed=k + 1)
            agree += out.use.map(codes).to_numpy() == base_use
            e = energy(out, lib.daily_kwh_mean, scale)
            sector_runs.append(e.groupby(["district", "sector"]).kwh.sum().rename(k))
            print(f"run {k + 1}/{args.runs}: {np.mean(out.use.to_numpy() == base.use.to_numpy()):.3f} of buildings "
                  "keep their base use")
        runs = pd.concat(sector_runs, axis=1)
        q = runs.quantile([0.1, 0.5, 0.9], axis=1).T.set_axis(["alloc_p10", "alloc_p50", "alloc_p90"], axis=1)
        eb = energy(base, lib.daily_kwh_mean, scale)
        prof = {}
        for col in ("daily_kwh_p10", "daily_kwh_mean", "daily_kwh_p90"):
            f = eb.profile_id.map(lib[col]) / eb.profile_id.map(lib.daily_kwh_mean)
            prof[col] = (eb.kwh * f).groupby([eb.district, eb.sector]).sum()
        bands = q.join(pd.DataFrame(prof).set_axis(["profile_p10", "base", "profile_p90"], axis=1))
        rel_lo = np.hypot(1 - bands.alloc_p10 / bands.alloc_p50, 1 - bands.profile_p10 / bands.base)
        rel_hi = np.hypot(bands.alloc_p90 / bands.alloc_p50 - 1, bands.profile_p90 / bands.base - 1)
        bands["combined_p10"], bands["combined_p90"] = bands.base * (1 - rel_lo), bands.base * (1 + rel_hi)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (bands / 1e3).round(2).add_suffix("_mwh").reset_index().to_csv(OUT_DIR / "sector_bands.csv", index=False)
        pd.DataFrame({"bldg_id": base.bldg_id, "use_agreement": (agree / args.runs).astype("float32")}).to_parquet(
            OUT_DIR / "building_agreement.parquet", index=False)
        nat = bands.sum() / 1e6
        print(f"national potential GWh/yr: allocation p10-p90 {nat.alloc_p10:.0f}-{nat.alloc_p90:.0f}, profile "
              f"p10-p90 {nat.profile_p10:.0f}-{nat.profile_p90:.0f}, base {nat.base:.0f}")
        print(f"buildings keeping their base use in every run: {(agree == args.runs).mean():.3f}; "
              f"median agreement {np.median(agree / args.runs):.2f}")


if __name__ == "__main__":
    main()
