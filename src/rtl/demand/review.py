"""B1: the evidence review sheet: every RAMP-relevant number in one place, for a human to approve or correct.

    pixi run evidence-review

Writes reports/review/evidence_review.csv (one row per evidence value, with empty `decision`,
`corrected_value` and `reviewer_note` columns to fill in) and reports/review/coverage.md (appliance x parameter
counts, so gaps are visible). Only the families RAMP needs are included: ownership, rated power, hours per day,
usage windows and monthly/annual kWh (the latter for validation). Everything else stays in the evidence table.
"""

from __future__ import annotations

import pandas as pd

from rtl.settings import PROCESSED_DIR, REPORTS_DIR

FAMILIES = ["ownership_share", "rated_power_w", "hours_per_day", "usage_window", "monthly_kwh", "annual_kwh"]
OUT = REPORTS_DIR / "review"


def load() -> pd.DataFrame:
    rep = pd.read_parquet(PROCESSED_DIR / "evidence.parquet").assign(origin="report")
    web = pd.read_parquet(PROCESSED_DIR / "evidence_productive_use.parquet").assign(origin="web research")
    cur = pd.read_parquet(PROCESSED_DIR / "evidence_curated.parquet").assign(origin="curated")
    e = pd.concat([rep, web, cur], ignore_index=True)
    parts = e.parameter.str.split(".", n=2, expand=True)
    e["family"], e["appliance"], e["population_key"] = parts[0], parts[1], parts[2]
    return e[e.family.isin(FAMILIES)]


def main() -> None:
    e = load()
    sheet = e.assign(decision="", corrected_value="", reviewer_note="",
                     quote=e.quote.str.slice(0, 300))[[
        "decision", "corrected_value", "reviewer_note", "family", "appliance", "population_key", "value",
        "value_low", "value_high", "unit", "population", "geography", "year", "origin", "doc_id", "page", "quote",
        "quote_verified", "evidence_id"]].sort_values(["family", "appliance", "geography", "year"])
    OUT.mkdir(parents=True, exist_ok=True)
    sheet.to_csv(OUT / "evidence_review.csv", index=False)

    cov = pd.crosstab(e.appliance, e.family).reindex(columns=FAMILIES, fill_value=0)
    cov = cov.loc[cov.sum(axis=1).sort_values(ascending=False).index]
    usable = cov[(cov.rated_power_w > 0) & (cov.hours_per_day + cov.usage_window > 0)]
    lines = ["# Evidence coverage for RAMP parameters", "",
             f"{len(sheet):,} rows to review in `evidence_review.csv` "
             f"({(sheet.origin == 'report').sum():,} from reports, {(sheet.origin == 'web research').sum():,} from web "
             f"research, {(sheet.origin == 'curated').sum():,} curated from productive-use reports). "
             "Fill `decision` with `approve`, `correct` (and `corrected_value`) or `reject`.", "",
             f"Appliances with both a rated power and a usage time: {len(usable)} of {len(cov)}. "
             "Everything else needs more sources or an explicit, reviewed assumption.", "",
             "| appliance | " + " | ".join(FAMILIES) + " |", "|---|" + "---|" * len(FAMILIES)]
    lines += [f"| {a} | " + " | ".join(str(int(v)) if v else "" for v in row) + " |"
              for a, row in cov.head(60).iterrows()]
    (OUT / "coverage.md").write_text("\n".join(lines) + "\n")
    print(f"{len(sheet):,} review rows, {len(cov)} appliances, {len(usable)} with power + usage time -> {OUT}")


if __name__ == "__main__":
    main()
