"""B1: appliance ownership and hours of use from the World Bank MTF Rwanda 2022 household survey (aggregates only).

    pixi run mtf-appliances

Reads the restricted microdata in data/restricted/worldbank_mtf/RWA_2022_MTF/ (never committed) and publishes
weighted aggregates per appliance, for the groups RAMP profiles need: grid-connected households by urban/rural,
all households, and households by the tier of their solar device (Section D's D_TIER is the solar device's tier,
not the household's overall MTF tier). National weights (HH_WT) only: the refugee-camp sample is excluded.
Statistics: ownership share (number > 0), mean number among owners, and hours of use on a typical day among owners
who used it in the last 6 months (weighted median, quartiles, mean). Cells with fewer than
MIN_N respondents are suppressed, so nothing identifies a household.

Grid households are also split by Rwanda's Ubudehe category (Section B, B13): `lower` = categories 1-2, `upper` =
categories 3-4 (about 43/57 of urban and 58/42 of rural grid households). It mirrors the approved mapping's
within-setting wealth split (Meta RWI above the setting median) with a measure that isn't built from appliances.

Outputs (committed; they feed archetypes as evidence and check them):
- data/processed/validation_anchors/mtf_appliance_usage.csv: appliances by group
- mtf_household_consumption.csv: typical monthly electricity spending (C025) -> kWh/month with the RURA 2020
  residential block tariff + 18% VAT, by group
- mtf_enterprise_usage.csv: household enterprises (Section A2), all and grid-connected only
- mtf_facility_usage.csv: schools and health facilities (community & public institutions surveys)
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from rtl.settings import PROCESSED_DIR, REPO_ROOT

SRC = REPO_ROOT / "data/restricted/worldbank_mtf/RWA_2022_MTF/household_survey_data"
OUT = PROCESSED_DIR / "validation_anchors" / "mtf_appliance_usage.csv"
MIN_N = 10


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")[:40]


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
    b = pd.read_stata(SRC / "SECTION_B.dta", columns=["hhid", "B13"])
    df = ef.merge(c1, on="hhid", how="left").merge(d, on="hhid", how="left").drop(columns="HI04").merge(
        urb, on="hhid", how="left").merge(b, on="hhid", how="left")
    # the 700 refugee-camp households have no national weight (they use ref_weight): a separate sample, excluded
    df = df[df.HH_WT.notna()].copy()
    df["grid"] = df.C002.astype(str).str.lower().str.startswith("yes")
    df["setting"] = df.HI04.astype(str).str.lower()
    df["tier"] = df.D_TIER.astype(object)  # tier of the household's SOLAR DEVICE (Section D), not its overall tier
    df["wealth"] = ubudehe_half(df.B13)
    return df, appliance_columns(labels)


def ubudehe_half(b13: pd.Series) -> pd.Series:
    """Ubudehe category 1-2 -> 'lower', 3-4 -> 'upper', don't know -> NaN."""
    cat = b13.astype(str).str.extract(r"Category (\d)", expand=False).astype(float)
    return cat.map({1: "lower", 2: "lower", 3: "upper", 4: "upper"})


def groups_of(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Named household groups: grid by setting, by setting x Ubudehe half, all grid, all, solar-device tier."""
    groups = {"grid_" + s: df[df.grid & (df.setting == s)] for s in sorted(df.setting.dropna().unique())}
    for s in sorted(df.setting.dropna().unique()):
        for h in ("lower", "upper"):
            groups[f"grid_{s}_{h}"] = df[df.grid & (df.setting == s) & (df.wealth == h)]
    groups["grid_all"] = df[df.grid]
    groups["all_households"] = df
    if "tier" in df:
        for t in sorted(df.tier.dropna().unique()):
            groups[f"solar_device_{t}".replace(" ", "_").lower()] = df[df.tier == t]
    return groups


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
    groups = groups_of(df)
    rows = []
    for gname, g in groups.items():
        for code, name in apps.items():
            s = stats(g, code)
            if s["n_owners"] < MIN_N:  # suppress small cells: keep the share, drop per-owner statistics
                s = {k: v for k, v in s.items() if k in ("n_households", "ownership_share", "n_owners")}
            rows.append({"group": gname, "code": code, "appliance": name, **s})
    out = pd.DataFrame(rows)
    out.insert(0, "evidence_id", "mtf:RWA2022:hh:" + out.group + ":" + out.appliance)  # cited by archetype JSON
    out["source"] = "World Bank MTF Rwanda 2022 household survey (restricted microdata; weighted aggregates, HH_WT)"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.round(4).to_csv(OUT, index=False)
    g = out[out.group == "grid_all"].sort_values("ownership_share", ascending=False)
    print(f"{len(out):,} rows ({len(apps)} appliances x {len(groups)} groups) -> {OUT}")
    print(g[["appliance", "ownership_share", "n_owners", "mean_number_owners", "hours_median", "hours_p25",
             "hours_p75"]].head(20).round(2).to_string(index=False))


ENTERPRISE_OUT = PROCESSED_DIR / "validation_anchors" / "mtf_enterprise_usage.csv"
CONSUMPTION_OUT = PROCESSED_DIR / "validation_anchors" / "mtf_household_consumption.csv"
FACILITY_OUT = PROCESSED_DIR / "validation_anchors" / "mtf_facility_usage.csv"
FAC = REPO_ROOT / "data/restricted/worldbank_mtf/RWA_2022_MTF/community_and_public_institutions_surveys_data"
VAT = 0.18  # Rwanda's standard VAT rate; RURA tariffs are quoted VAT and regulatory fee exclusive


def tariff_blocks(kind: str, doc_id: str = "rura_tariffs_2020") -> list[tuple[float, float]]:
    """[(upper kWh of block, RWF/kWh), ...] from the transcribed tariff anchors (validation_anchors)."""
    t = pd.read_csv(PROCESSED_DIR / "validation_anchors" / "tariffs_history.csv")
    t = t[(t.doc_id == doc_id) & t.parameter.str.startswith(f"tariff_rwf_per_kwh.{kind}.block_")]
    out = []
    for param, price in zip(t.parameter, t.value, strict=True):
        m = re.search(r"block_(?:(\d+)_(\d+)|over_(\d+))$", param)
        out.append((float(m.group(2)) if m.group(2) else np.inf, float(price)))
    return sorted(out)


def kwh_from_spend(rwf: np.ndarray, blocks: list[tuple[float, float]], vat: float = VAT) -> np.ndarray:
    """Invert a monthly increasing-block tariff: RWF spent (VAT included) -> kWh."""
    net = np.asarray(rwf, float) / (1 + vat)
    kwh, lo, paid = np.zeros_like(net), 0.0, 0.0
    for hi, price in blocks:
        cap = (hi - lo) * price  # cost of the whole block
        take = np.clip(net - paid, 0, cap)
        kwh += take / price
        paid, lo = paid + cap, hi
    return kwh


def consumption() -> pd.DataFrame:
    """Grid households' typical monthly electricity spending (C025) -> kWh/month, by group (weighted)."""
    df, _ = load()
    spend = pd.read_stata(SRC / "SECTION_C1.dta", columns=["hhid", "C025"], convert_categoricals=False)
    df = df.drop(columns=[c for c in ("C025",) if c in df]).merge(spend, on="hhid", how="left")
    blocks = tariff_blocks("residential")
    rows = []
    for gname, g in groups_of(df).items():
        if not gname.startswith("grid_"):
            continue
        x = pd.to_numeric(g.C025, errors="coerce").to_numpy()
        w = g.HH_WT.to_numpy(float)
        ok = np.isfinite(x) & (x > 0)
        row = {"group": gname, "n_households": len(g), "n_spend": int(ok.sum())}
        if ok.sum() >= MIN_N:
            k = kwh_from_spend(x[ok], blocks)
            row.update({"spend_rwf_median": wquantile(x[ok], w[ok], 0.5),
                        "kwh_month_median": wquantile(k, w[ok], 0.5), "kwh_month_p25": wquantile(k, w[ok], 0.25),
                        "kwh_month_p75": wquantile(k, w[ok], 0.75),
                        "kwh_month_mean": float(np.average(k, weights=w[ok]))})
        rows.append(row)
    out = pd.DataFrame(rows)
    out.insert(0, "evidence_id", "mtf:RWA2022:kwh:" + out.group)
    out["method"] = ("C025 'in a typical month, amount spent on electricity' (RWF) / 1.18 VAT, inverted through the "
                     "RURA 2020 residential blocks (89 / 212 / 249 RWF/kWh; tariffs_history.csv)")
    out["source"] = "World Bank MTF Rwanda 2022 household survey (restricted microdata; weighted aggregates, HH_WT)"
    out.round(3).to_csv(CONSUMPTION_OUT, index=False)
    return out


def enterprises() -> pd.DataFrame:
    """Household enterprises (Section A2): operating hours, evening hours, appliances used, last-bill kWh, by
    main activity, for all enterprises and grid-connected ones. Weighted with HH_WT; cells under MIN_N suppressed."""
    with pd.read_stata(SRC / "SECTION_A2.dta", iterator=True) as r:
        lab = r.variable_labels()
    a = pd.read_stata(SRC / "SECTION_A2.dta")
    a = a[a.HH_WT.notna() & a.A30.notna()].copy()
    uses = {k: re.sub(r"^Using\s+|\s+in your activity$", "", v).strip().lower()
            for k, v in lab.items() if re.fullmatch(r"A31_\d\d", k) and "No appliance" not in v}
    a["grid"] = a.A43_01.astype(str).str.lower().str.startswith("yes")
    rows = []
    for act, g0 in [("all", a), *a.groupby(a.A30.astype(str))]:
        for scope, g in (("all", g0), ("grid", g0[g0.grid])):
            if len(g) < MIN_N:
                continue
            w = g.HH_WT.to_numpy(float)
            row = {"activity": act, "scope": scope, "n_enterprises": len(g),
                   "grid_share": float(np.average(g.grid, weights=w))}
            for col, name in [("A37", "hours_per_day"), ("A38", "hours_6pm_6am")]:
                x = pd.to_numeric(g[col], errors="coerce").to_numpy()
                ok = np.isfinite(x)
                if ok.sum() >= MIN_N:
                    row.update({f"{name}_median": wquantile(x[ok], w[ok], 0.5),
                                f"{name}_p25": wquantile(x[ok], w[ok], .25),
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
    out.insert(0, "evidence_id", "mtf:RWA2022:ent:" + out.activity.map(slug) + ":" + out.scope)
    out["source"] = "World Bank MTF Rwanda 2022, household enterprises (Section A2; weighted aggregates, HH_WT)"
    out.round(3).to_csv(ENTERPRISE_OUT, index=False)
    return out


def _clock(x) -> float:
    """'7h20' -> 7.33; '888' (not applicable) or junk -> NaN."""
    m = re.fullmatch(r"\s*(\d{1,2})h(\d{2})?\s*", str(x))
    return int(m.group(1)) + int(m.group(2) or 0) / 60 if m else np.nan


def _facility_stats(d: pd.DataFrame, kind: str, own: dict[str, tuple[str, str]], w: np.ndarray) -> list[dict]:
    rows = []
    for name, (has, num) in own.items():
        owns = d[has].astype(str).str.lower().eq("yes").to_numpy()
        row = {"facility": kind, "item": name, "statistic": "ownership_share", "n": len(d),
               "value": float(np.average(owns, weights=w))}
        rows.append(row)
        n = pd.to_numeric(d[num], errors="coerce").to_numpy()
        ok = owns & np.isfinite(n) & (n > 0)
        if ok.sum() >= MIN_N:
            rows += [{"facility": kind, "item": name, "statistic": "number_used_median", "n": int(ok.sum()),
                      "value": wquantile(n[ok], w[ok], 0.5)},
                     {"facility": kind, "item": name, "statistic": "number_used_mean", "n": int(ok.sum()),
                      "value": float(np.average(n[ok], weights=w[ok]))}]
    return rows


def _quantiles(kind: str, item: str, x: np.ndarray, w: np.ndarray) -> list[dict]:
    ok = np.isfinite(x)
    if ok.sum() < MIN_N:
        return []
    return [{"facility": kind, "item": item, "statistic": st, "n": int(ok.sum()), "value": wquantile(x[ok], w[ok], q)}
            for st, q in (("p25", 0.25), ("median", 0.5), ("p75", 0.75))] + [
        {"facility": kind, "item": item, "statistic": "mean", "n": int(ok.sum()),
         "value": float(np.average(x[ok], weights=w[ok]))}]


def facilities() -> pd.DataFrame:
    """Schools and health facilities on the national grid: appliances (ownership, number used), opening hours,
    size, and monthly electricity spending -> kWh (RURA 2020 tariff for the category + 18% VAT)."""
    rows = []
    for kind, f, prefix, any_col, src_col, spend_col in [
            ("school", "Education facility_DATASET_BC.dta", "QE_B", "QE_B16", "QE_B18", "QE_B29"),
            ("health_facility", "Health facility_Dataset.dta", "HL_B", "HL_B18", "HL_B20", "HL_B30")]:
        with pd.read_stata(FAC / f, iterator=True) as r:
            lab = r.variable_labels()
        d = pd.read_stata(FAC / f)
        # interviewed facilities only (the health file has 128 empty records); facilities without electricity skip
        # the source question, so filter on "has electricity from any source", answered by all
        d = d[d[any_col].notna() & d.Basicweight.notna()]
        rows.append({"facility": kind, "item": "facilities", "statistic": "grid_share", "n": len(d),
                     "value": float(np.average(d[src_col].astype(str).str.startswith("National grid"),
                                               weights=d.Basicweight))})
        d = d[d[src_col].astype(str).str.startswith("National grid")]
        w = d.Basicweight.to_numpy(float)
        own = {}
        for k, v in lab.items():
            m = re.fullmatch(rf"({prefix}\d\d)A", k)
            hit = re.search(r"own any (.+?)(?: in work.*)?$", v, flags=re.I)
            if m and hit and f"{m.group(1)}B" in d:
                own[re.sub(r"[^a-z0-9]+", "_", hit.group(1).lower()).strip("_")] = (k, f"{m.group(1)}B")
        rows += _facility_stats(d, kind, own, w)
        spend = pd.to_numeric(d[spend_col], errors="coerce").to_numpy(float, copy=True)
        spend[spend <= 0] = np.nan
        if kind == "school":  # non-residential blocks; Q: "how much did the facility spend" (read as per month)
            kwh = kwh_from_spend(np.nan_to_num(spend), tariff_blocks("non_residential"))
            hours = np.array([_clock(b) if np.isfinite(_clock(b)) else _clock(a)
                              for a, b in zip(d.QE_B14B, d.QE_B15B, strict=True)]) - d.QE_B14A.map(_clock).to_numpy()
            rows += _quantiles(kind, "students_per_day", pd.to_numeric(d.QE_B13, errors="coerce").to_numpy(float), w)
            rows += _quantiles(kind, "open_hour", d.QE_B14A.map(_clock).to_numpy(), w)
            rows += _quantiles(kind, "opening_hours_per_day", hours, w)
        else:  # health facilities pay one flat rate
            t = pd.read_csv(PROCESSED_DIR / "validation_anchors" / "tariffs_history.csv")
            flat = float(t[(t.doc_id == "rura_tariffs_2020")
                           & t.parameter.str.endswith("health_facilities")].value.iloc[0])
            kwh = np.nan_to_num(spend) / (1 + VAT) / flat
            rows += [{"facility": kind, "item": "open_24h", "statistic": "share", "n": len(d),
                      "value": float(np.average(d.HL_B15.astype(str).eq("Yes"), weights=w))}]
            rows += _quantiles(kind, "patients_per_day", pd.to_numeric(d.HL_B14A, errors="coerce").to_numpy(float), w)
            rows += _quantiles(kind, "beds", pd.to_numeric(d.HL_B14B, errors="coerce").to_numpy(float), w)
        kwh[~np.isfinite(spend)] = np.nan
        rows += _quantiles(kind, "spend_rwf_month", spend, w)
        rows += _quantiles(kind, "kwh_month_from_spend", kwh, w)
    out = pd.DataFrame(rows)
    out.insert(0, "evidence_id", "mtf:RWA2022:" + out.facility + ":" + out.item + ":" + out.statistic)
    out["source"] = ("World Bank MTF Rwanda 2022, community & public institutions surveys (restricted; weighted with "
                     "Basicweight; grid-connected facilities except grid_share)")
    out.round(3).to_csv(FACILITY_OUT, index=False)
    return out


if __name__ == "__main__":
    main()
    e = enterprises()
    print(e[[c for c in e.columns if c in ("activity", "scope", "n_enterprises", "grid_share", "hours_per_day_median",
                                            "hours_6pm_6am_median", "uses_light", "uses_refrigerator", "uses_tv")]]
          .round(2).to_string(index=False))
    print(consumption().drop(columns=["method", "source"]).round(1).to_string(index=False))
    f = facilities()
    print(f[~f.statistic.isin(["number_used_mean"])].drop(columns="source").round(2).to_string(index=False))
