"""B1: appliance ownership and hours of use from the World Bank MTF Rwanda 2022 household survey (aggregates only).

    pixi run mtf-appliances

Reads the restricted microdata in data/restricted/worldbank_mtf/RWA_2022_MTF/ (never committed) and publishes
weighted aggregates per appliance, for the groups RAMP profiles need: grid-connected households by urban/rural,
all households, and households by the tier of their solar device (Section D's D_TIER is the solar device's tier,
not the household's overall MTF tier). National weights (HH_WT) only: the refugee-camp sample is excluded.
Statistics: ownership share (number > 0), mean number among owners, and hours of use on a typical day among owners
who used it in the last 6 months (weighted median, quartiles, mean). Cells with fewer than
MIN_N respondents are suppressed, so nothing identifies a household.

Output: data/processed/validation_anchors/mtf_appliance_usage.csv (committed; it feeds archetypes as evidence).
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from rtl.settings import PROCESSED_DIR, REPO_ROOT

SRC = REPO_ROOT / "data/restricted/worldbank_mtf/RWA_2022_MTF/household_survey_data"
OUT = PROCESSED_DIR / "validation_anchors" / "mtf_appliance_usage.csv"
MIN_N = 10


def wquantile(x: np.ndarray, w: np.ndarray, q: float) -> float:
    o = np.argsort(x)
    cw = np.cumsum(w[o])
    return float(x[o][np.searchsorted(cw, q * cw[-1])])


def appliance_columns(labels: dict[str, str]) -> dict[str, str]:
    """E##A 'Number of <appliance>' -> code -> appliance name."""
    out = {}
    for k, v in labels.items():
        m = re.fullmatch(r"(E\d\d)A", k)
        if m and v.lower().startswith("number of"):
            name = re.sub(r"^number of\s+", "", v, flags=re.I).strip().lower()
            out[m.group(1)] = re.sub(r"[^a-z0-9]+", "_", name).strip("_")[:40]
    return out


def load() -> tuple[pd.DataFrame, dict[str, str]]:
    with pd.read_stata(SRC / "SECTION_EF1.dta", iterator=True) as r:
        labels = r.variable_labels()
    ef = pd.read_stata(SRC / "SECTION_EF1.dta", convert_categoricals=False)
    c1 = pd.read_stata(SRC / "SECTION_C1.dta", columns=["hhid", "C002"])
    d = pd.read_stata(SRC / "SECTION_D.dta", columns=["hhid", "D_TIER"])
    urb = pd.read_stata(SRC / "SECTION_EF1.dta", columns=["hhid", "HI04"])  # with value labels
    df = ef.merge(c1, on="hhid", how="left").merge(d, on="hhid", how="left").drop(columns="HI04").merge(
        urb, on="hhid", how="left")
    # the 700 refugee-camp households have no national weight (they use ref_weight): a separate sample, excluded
    df = df[df.HH_WT.notna()].copy()
    df["grid"] = df.C002.astype(str).str.lower().str.startswith("yes")
    df["setting"] = df.HI04.astype(str).str.lower()
    df["tier"] = df.D_TIER.astype(object)  # tier of the household's SOLAR DEVICE (Section D), not its overall tier
    return df, appliance_columns(labels)


def stats(g: pd.DataFrame, code: str) -> dict:
    w = g.HH_WT.to_numpy(float)
    n_own = pd.to_numeric(g.get(f"{code}A"), errors="coerce").fillna(0).to_numpy()
    owners = n_own > 0
    out = {"n_households": len(g), "ownership_share": float(w[owners].sum() / w.sum()) if w.sum() else np.nan,
           "n_owners": int(owners.sum()),
           "mean_number_owners": float(np.average(n_own[owners], weights=w[owners])) if owners.any() else np.nan}
    hcol = f"{code}C"
    if hcol in g and owners.sum() >= MIN_N:
        h = pd.to_numeric(g[hcol], errors="coerce").to_numpy()
        ok = owners & np.isfinite(h) & (h >= 0) & (h <= 24)
        out["n_hours"] = int(ok.sum())
        if ok.sum() >= MIN_N:
            out.update({"hours_median": wquantile(h[ok], w[ok], 0.5), "hours_p25": wquantile(h[ok], w[ok], 0.25),
                        "hours_p75": wquantile(h[ok], w[ok], 0.75),
                        "hours_mean": float(np.average(h[ok], weights=w[ok]))})
    return out


def main() -> None:
    df, apps = load()
    groups = {"grid_" + s: df[df.grid & (df.setting == s)] for s in sorted(df.setting.dropna().unique())}
    groups["grid_all"] = df[df.grid]
    groups["all_households"] = df
    for t in sorted(df.tier.dropna().unique()):
        groups[f"solar_device_{t}".replace(" ", "_").lower()] = df[df.tier == t]
    rows = []
    for gname, g in groups.items():
        for code, name in apps.items():
            s = stats(g, code)
            if s["n_owners"] < MIN_N:  # suppress small cells: keep the share, drop per-owner statistics
                s = {k: v for k, v in s.items() if k in ("n_households", "ownership_share", "n_owners")}
            rows.append({"group": gname, "code": code, "appliance": name, **s})
    out = pd.DataFrame(rows)
    out["source"] = "World Bank MTF Rwanda 2022 household survey (restricted microdata; weighted aggregates, HH_WT)"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.round(4).to_csv(OUT, index=False)
    g = out[out.group == "grid_all"].sort_values("ownership_share", ascending=False)
    print(f"{len(out):,} rows ({len(apps)} appliances x {len(groups)} groups) -> {OUT}")
    print(g[["appliance", "ownership_share", "n_owners", "mean_number_owners", "hours_median", "hours_p25",
             "hours_p75"]].head(20).round(2).to_string(index=False))


ENTERPRISE_OUT = PROCESSED_DIR / "validation_anchors" / "mtf_enterprise_usage.csv"


def enterprises() -> pd.DataFrame:
    """Household enterprises (Section A2): operating hours, evening hours, appliances used, last-bill kWh, by
    main activity. Weighted with HH_WT; cells under MIN_N suppressed."""
    with pd.read_stata(SRC / "SECTION_A2.dta", iterator=True) as r:
        lab = r.variable_labels()
    a = pd.read_stata(SRC / "SECTION_A2.dta")
    a = a[a.HH_WT.notna() & a.A30.notna()].copy()
    uses = {k: re.sub(r"^Using\s+|\s+in your activity$", "", v).strip().lower()
            for k, v in lab.items() if re.fullmatch(r"A31_\d\d", k) and "No appliance" not in v}
    a["grid"] = a.A43_01.astype(str).str.lower().str.startswith("yes")
    rows = []
    for act, g in [("all", a), *a.groupby(a.A30.astype(str))]:
        if len(g) < MIN_N:
            continue
        w = g.HH_WT.to_numpy(float)
        row = {"activity": act, "n_enterprises": len(g), "grid_share": float(np.average(g.grid, weights=w))}
        for col, name in [("A37", "hours_per_day"), ("A38", "hours_6pm_6am")]:
            x = pd.to_numeric(g[col], errors="coerce").to_numpy()
            ok = np.isfinite(x)
            if ok.sum() >= MIN_N:
                row.update({f"{name}_median": wquantile(x[ok], w[ok], 0.5), f"{name}_p25": wquantile(x[ok], w[ok], .25),
                            f"{name}_p75": wquantile(x[ok], w[ok], 0.75)})
        kwh = pd.to_numeric(g.A53, errors="coerce").to_numpy()
        ok = np.isfinite(kwh) & (kwh > 0)
        if ok.sum() >= MIN_N:
            row.update({"last_bill_kwh_median": wquantile(kwh[ok], w[ok], 0.5), "n_bill": int(ok.sum())})
        for col, name in uses.items():
            row[f"uses_{re.sub(r'[^a-z0-9]+', '_', name).strip('_')}"] = float(
                np.average(g[col].astype(str).str.lower().str.startswith("yes"), weights=w))
        rows.append(row)
    out = pd.DataFrame(rows)
    out["source"] = "World Bank MTF Rwanda 2022, household enterprises (Section A2; weighted aggregates, HH_WT)"
    out.round(3).to_csv(ENTERPRISE_OUT, index=False)
    return out


if __name__ == "__main__":
    main()
    e = enterprises()
    print(e[[c for c in e.columns if c in ("activity", "n_enterprises", "grid_share", "hours_per_day_median",
                                            "hours_6pm_6am_median", "last_bill_kwh_median", "uses_light",
                                            "uses_refrigerator", "uses_tv")]].round(2).to_string(index=False))
