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
from rtl.settings import INTERIM_DIR, RAW_DIR

API = "https://api.dhsprogram.com/rest/dhs"
SURVEYS = ["RW2015DHS", "RW2019DHS", "RW2025DHS"]
PREFIXES = ("HC_ELEC_", "HC_HEFF_", "HC_LTFL_H_", "HC_CKFL_H_ELC", "HC_CKTC_H_ELC")


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
                             "CharacteristicLabel", "Value", "DenominatorWeighted", "CILow", "CIHigh"]]
    for col in ["Value", "DenominatorWeighted", "CILow", "CIHigh"]:  # API returns '' for missing
        df[col] = pd.to_numeric(df[col], errors="coerce")
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(INTERIM_DIR / "dhs_indicators.parquet", index=False)
    print(f"{len(ids)} indicators, {len(df)} rows -> data/interim/dhs_indicators.parquet")
    print(df.groupby(["SurveyId", "CharacteristicCategory"]).size().to_string())


if __name__ == "__main__":
    main()
