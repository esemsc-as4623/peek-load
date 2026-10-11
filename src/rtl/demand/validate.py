"""Automated plausibility checks on appliance evidence (rules in config/plausibility.yaml).

    pixi run validate-evidence

Stands in for row-by-row human review (see data/gold/approvals.yaml). Every RAMP-relevant evidence value is checked
for (1) a recognised unit, (2) physical/market range for its kind, and (3) agreement with the other sources for the
same appliance and parameter. Flagged rows go to reports/review/validation_flags.csv with the reason; profiles
built in B1 exclude them unless the owner sets `override` to `keep` in that file.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from rtl.demand.review import load as load_evidence
from rtl.settings import REPORTS_DIR, load_yaml

OUT = REPORTS_DIR / "review" / "validation_flags.csv"
POWER_UNITS = {"W": 1.0, "kW": 1000.0, "Wp": 1.0}
HOUR_UNITS = {"h/day", "hours/day", "h", "hours", "hours per day"}


def power_range(appliance: str, rules: list[dict]) -> tuple[float, float]:
    for r in rules:
        if re.search(r["match"], appliance or "", re.I):
            return r["min"], r["max"]
    return 0.0, float("inf")


def checks(e: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    flags = []

    def flag(row, check: str, detail: str):
        flags.append({"evidence_id": row.evidence_id, "family": row.family, "appliance": row.appliance,
                      "value": row.value, "unit": row.unit, "check": check, "detail": detail,
                      "doc_id": row.doc_id, "page": row.page, "override": ""})

    e = e.copy()
    e["value_std"] = np.nan
    for row in e.itertuples():
        v = row.value if pd.notna(row.value) else (row.value_high if pd.notna(row.value_high) else row.value_low)
        if pd.isna(v):
            continue
        if row.family == "rated_power_w":
            if row.unit not in POWER_UNITS:
                flag(row, "unit", f"unexpected unit {row.unit!r} for rated power")
                continue
            w = v * POWER_UNITS[row.unit]
            lo, hi = power_range(row.appliance, cfg["rated_power_w"])
            if not lo <= w <= hi:
                flag(row, "range", f"{w:,.1f} W outside {lo:,.0f}-{hi:,.0f} W for '{row.appliance}'")
            e.loc[row.Index, "value_std"] = w
        elif row.family == "hours_per_day":
            b = cfg["hours_per_day"]
            if row.unit not in HOUR_UNITS:
                flag(row, "unit", f"unexpected unit {row.unit!r} for hours per day")
            elif not b["min"] <= v <= b["max"]:
                flag(row, "range", f"{v} h/day outside {b['min']}-{b['max']}")
            else:
                e.loc[row.Index, "value_std"] = v
        elif row.family == "ownership_share":
            b = cfg["ownership_share"]
            share = v / 100 if row.unit in ("%", "percent") else v
            if not b["min"] <= share <= b["max"]:
                flag(row, "range", f"share {share} outside 0-1 (unit {row.unit!r})")
            else:
                e.loc[row.Index, "value_std"] = share
        elif row.family == "usage_window":
            b = cfg["usage_window_hour"]
            for x in (row.value_low, row.value_high):
                if pd.notna(x) and not b["min"] <= x <= b["max"]:
                    flag(row, "range", f"window bound {x} outside 0-24 h")

    # consistency across sources for power and hours (log-ratio to the group median)
    c = cfg["consensus"]
    for _, g in e[e.family.isin(["rated_power_w", "hours_per_day"])].dropna(subset=["value_std"]).groupby(
            ["family", "appliance"]):
        if len(g) < c["min_sources"]:
            continue
        med = g.value_std.median()
        for row in g.itertuples():
            if med > 0 and not med / c["max_ratio"] <= row.value_std <= med * c["max_ratio"]:
                flag(row, "consensus", f"{row.value_std:,.1f} vs median {med:,.1f} of {len(g)} sources "
                                       f"(more than {c['max_ratio']}x apart)")
    return pd.DataFrame(flags)


def main() -> None:
    cfg = load_yaml("plausibility.yaml")
    e = load_evidence()
    flags = checks(e, cfg)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    flags.to_csv(OUT, index=False)
    n_rows = flags.evidence_id.nunique() if len(flags) else 0
    print(f"{len(e):,} evidence values checked; {n_rows} flagged ({len(flags)} findings) -> {OUT}")
    if len(flags):
        print(flags.groupby(["family", "check"]).size().to_string())


if __name__ == "__main__":
    main()
