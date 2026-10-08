"""A4: public DHS API indicators for Rwanda (no login needed).

Household electricity access, appliance/asset ownership, lighting and cooking with electricity,
by total / urban-rural / province / wealth quintile. These are the first ownership priors for RAMP
archetypes; microdata (restricted, via data/restricted/dhs/) refines them later.

Raw API responses are saved as JSON in data/raw/dhs_api/<date>/ and recorded in the manifest;
a tidy table goes to data/interim/dhs_indicators.parquet.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pandas as pd

from rtl.manifest import record
from rtl.settings import INTERIM_DIR, PROCESSED_DIR, RAW_DIR

API = "https://api.dhsprogram.com/rest/dhs"
SURVEYS = ["RW2015DHS", "RW2019DHS", "RW2025DHS"]
# Individual phone/smartphone ownership (CO_MOBB_*) is pulled to cross-check the household phone indicator.
PREFIXES = ("HC_ELEC_", "HC_HEFF_", "HC_LTFL_H_", "HC_CKFL_H_ELC", "HC_CKTC_H_ELC",
            "CO_MOBB_W_MOB", "CO_MOBB_W_OSP", "CO_MOBB_M_MOB", "CO_MOBB_M_OSP")

# Ownership priors for archetypes: item -> DHS indicator (all "percentage of households possessing ...").
PRIOR_ITEMS = {"electricity": "HC_ELEC_H_ELC", "radio": "HC_HEFF_H_RDO", "tv": "HC_HEFF_H_TLV",
               "fridge": "HC_HEFF_H_FRG", "computer": "HC_HEFF_H_CMP", "mobile_phone": "HC_HEFF_H_MPH"}
DIMENSIONS = {"Total": "total", "Residence": "residence", "Region": "province", "Wealth quintile": "wealth_quintile"}
# Known problems become flags on the rows, not silent drops (see data/processed/validation_anchors/README.md).
FLAGS = {("RW2025DHS", "HC_HEFF_H_MPH"): "ordinary_phone_only_use_mobile_phone_any"}  # FR401 Table 2.5


def get(path: str, **params) -> dict:
    r = httpx.get(f"{API}/{path}", params={"f": "json", **params}, timeout=120)
    r.raise_for_status()
    return r.json()


def main() -> None:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    out_dir = RAW_DIR / "dhs_api" / today
    out_dir.mkdir(parents=True, exist_ok=True)

    indicators = get("indicators", returnFields="IndicatorId,Label,Definition", perpage=10000)["Data"]
    ids = sorted(i["IndicatorId"] for i in indicators if i["IndicatorId"].startswith(PREFIXES))
    meta_path = out_dir / "indicators_meta.json"
    meta_path.write_text(json.dumps([i for i in indicators if i["IndicatorId"] in ids], indent=1))
    record("dhs_api", meta_path, f"{API}/indicators")

    rows = []
    for chunk in [ids[i:i + 40] for i in range(0, len(ids), 40)]:
        params = dict(countryIds="RW", surveyIds=",".join(SURVEYS), indicatorIds=",".join(chunk),
                      breakdown="all", perpage=10000)
        rows += get("data", **params)["Data"]
    data_path = out_dir / "rw_household_energy_indicators.json"
    data_path.write_text(json.dumps(rows, indent=1))
    record("dhs_api", data_path, f"{API}/data?countryIds=RW&surveyIds={','.join(SURVEYS)}&breakdown=all")

    df = pd.DataFrame(rows)[["SurveyId", "IndicatorId", "Indicator", "CharacteristicCategory",
                             "CharacteristicLabel", "Value", "DenominatorWeighted", "DenominatorUnweighted",
                             "CILow", "CIHigh"]]
    for col in ["Value", "DenominatorWeighted", "DenominatorUnweighted", "CILow", "CIHigh"]:  # '' = missing
        df[col] = pd.to_numeric(df[col], errors="coerce")
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(INTERIM_DIR / "dhs_indicators.parquet", index=False)
    print(f"{len(ids)} indicators, {len(df)} rows -> data/interim/dhs_indicators.parquet")
    print(df.groupby(["SurveyId", "CharacteristicCategory"]).size().to_string())


def build_priors(df: pd.DataFrame) -> pd.DataFrame:
    """Household ownership shares by survey x one breakdown dimension (DHS API publishes marginals only:
    residence, province and wealth quintile separately; cross-tabs need the restricted microdata).

    Shares are fractions. ci_low/ci_high are the API's CIs, which it does not publish for these
    indicators (left empty = not observed; no CI is invented here).
    """
    d = df[df.IndicatorId.isin(PRIOR_ITEMS.values()) & df.CharacteristicCategory.isin(DIMENSIONS)].copy()
    item = {v: k for k, v in PRIOR_ITEMS.items()}
    out = pd.DataFrame({
        "survey_id": d.SurveyId, "survey_year": d.SurveyId.str[2:6].astype(int), "item": d.IndicatorId.map(item),
        "indicator_id": d.IndicatorId, "dimension": d.CharacteristicCategory.map(DIMENSIONS),
        "group": d.CharacteristicLabel.str.strip(), "share": (d.Value / 100).round(4), "ci_low": d.CILow / 100,
        "ci_high": d.CIHigh / 100, "n_households_weighted": d.DenominatorWeighted,
        "n_households_unweighted": d.get("DenominatorUnweighted"),
        "flag": [FLAGS.get((s, i), "") for s, i in zip(d.SurveyId, d.IndicatorId, strict=True)],
        "source": "DHS Program API " + API + "/data (breakdown=all)", "role": "evidence"})
    return out.sort_values(["item", "survey_id", "dimension", "group"]).reset_index(drop=True)


# RW2025 household phone ownership as published in the final report (FR401, Table 2.5, PDF page 58). The API's
# HC_HEFF_H_MPH for RW2025 equals the report's "Ordinary mobile phone" row (non-smartphones only), so these
# report rows are the correct "any phone" prior. Values transcribed by hand; see validation_anchors/README.md.
REPORT_ROWS = [  # (item, group, share)
    ("mobile_phone_any", "Total", 0.837), ("mobile_phone_any", "Urban", 0.933), ("mobile_phone_any", "Rural", 0.796),
    ("smartphone", "Total", 0.379), ("smartphone", "Urban", 0.644), ("smartphone", "Rural", 0.266),
]


def report_rows() -> pd.DataFrame:
    return pd.DataFrame([{
        "survey_id": "RW2025DHS", "survey_year": 2025, "item": item, "indicator_id": "",
        "dimension": "total" if g == "Total" else "residence", "group": g, "share": v, "ci_low": None,
        "ci_high": None, "n_households_weighted": None, "n_households_unweighted": None, "flag": "",
        "source": "dhs_rw_2025_fr p58 Table 2.5 (hand-transcribed)", "role": "evidence"} for item, g, v in REPORT_ROWS])


def with_prior_ids(df: pd.DataFrame) -> pd.DataFrame:
    """Stable ids that archetype JSON files cite as evidence_ids, e.g. dhs:RW2025DHS:tv:residence:Rural."""
    ids = "dhs:" + df.survey_id + ":" + df["item"] + ":" + df.dimension + ":" + df.group.str.replace(" ", "_")
    out = df.assign(prior_id=ids)
    assert out.prior_id.is_unique
    return out[["prior_id", *df.columns]]


def write_priors() -> None:
    out = PROCESSED_DIR / "validation_anchors" / "dhs_ownership_priors.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    pri = build_priors(pd.read_parquet(INTERIM_DIR / "dhs_indicators.parquet"))
    pri = with_prior_ids(pd.concat([pri, report_rows()], ignore_index=True))
    pri.to_csv(out, index=False)
    print(f"{len(pri)} rows -> {out.relative_to(out.parents[3])}")


if __name__ == "__main__":
    import sys
    write_priors() if "--priors" in sys.argv else main()
