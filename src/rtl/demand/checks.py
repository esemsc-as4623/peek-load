"""B1 checks: simulated profiles (profile_library.parquet) against independent anchors and the MTF tiers.

    pixi run profile-checks          # after `pixi run profile-library` -> reports/demand/profile_checks.md + figures

Household archetypes are calibrated to MTF 2022 spending (one lighting factor each), so that comparison is
shown as `calibration`, not as a check. Independent checks:
- MTF energy tier of each archetype (config/plausibility.yaml `mtf_tiers`) vs the tier in its id;
- QSEL measured consumption (REG meter data and 2023-24 surveys) and EICV5 2017 spending, by setting. The
  setting's archetype mix weights its lower / upper archetypes by the allocated upper share (DHS-based, from
  building_use; 50/50 if the allocation hasn't run). These check the profiles before the REG scaling
  (rtl.demand.calibrate), which then imposes REG's means;
- establishments: MTF 2022 school and health-centre spending, NREL 2018 mill and workshop energy.
Verdict: `ok` within 1.5x of a point anchor (or inside its p25-p75 range), `check` within 3x, `fail` beyond.
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from rtl import viz
from rtl.demand.runner import LIBRARY
from rtl.settings import PROCESSED_DIR, REPORTS_DIR, load_yaml

ANCHORS = PROCESSED_DIR / "validation_anchors"
OUT = REPORTS_DIR / "demand" / "profile_checks.md"
FIG_DIR = REPORTS_DIR / "figures"
DAYS_PER_MONTH = 30.4
HOUSEHOLDS = {"rural": ["household_rural_lower", "household_rural_upper"],
              "urban": ["household_urban_lower", "household_urban_upper"]}
HOUSEHOLD_IDS = {a for ids in HOUSEHOLDS.values() for a in ids}


def upper_shares() -> dict[str, float]:
    """Share of allocated households in the upper wealth group per setting (0.5 each before the allocation)."""
    path = PROCESSED_DIR / "building_use.parquet"
    if not path.exists():
        return {"rural": 0.5, "urban": 0.5}
    u = pd.read_parquet(path, columns=["setting", "households", "upper"])
    u = u[u.households > 0]
    return {s: float((g.households * g.upper).sum() / g.households.sum()) for s, g in u.groupby("setting")}


def verdict(sim: float, value: float | None = None, low: float | None = None, high: float | None = None) -> str:
    if low is not None and high is not None:
        if low <= sim <= high:
            return "ok"
        ratio = max(low / sim, sim / high)
    else:
        ratio = max(sim / value, value / sim)
    return "ok" if ratio <= 1.5 else "check" if ratio <= 3 else "fail"


def mtf_tier(wh_day: float, tiers: dict) -> int:
    return max([0] + [int(t) for t, v in tiers.items() if wh_day >= v["min_wh_day"]])


def household_rows(lib: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    kwh = lib.set_index("profile_id").daily_kwh_mean * DAYS_PER_MONTH
    tiers = load_yaml("plausibility.yaml")["mtf_tiers"]
    mtf = pd.read_csv(ANCHORS / "mtf_household_consumption.csv").set_index("group")
    tier_rows = []
    for setting, ids in HOUSEHOLDS.items():
        for aid, half in zip(ids, ("lower", "upper"), strict=True):
            t = mtf_tier(kwh[aid] * 1000 / DAYS_PER_MONTH, tiers)
            g = f"grid_{setting}_{half}"
            tier_rows.append({"archetype": aid, "simulated_kwh_month": kwh[aid],
                              "calibrated_to_mtf_spend_mean": mtf.loc[g, "kwh_month_mean"],
                              "mtf_spend_median": mtf.loc[g, "kwh_month_median"],
                              "mtf_energy_tier": t})
    q = pd.read_csv(ANCHORS / "residential_kwh_qsel.csv")
    e = pd.read_csv(ANCHORS / "eicv5_household_electricity.csv").set_index("group")

    def qsel(param: str, pop: str) -> pd.Series:
        return q[(q.parameter == param) & q.population.str.contains(pop, regex=False)].iloc[0]

    up = upper_shares()
    mix = {s: float((1 - up[s]) * kwh[lo] + up[s] * kwh[hi]) for s, (lo, hi) in HOUSEHOLDS.items()}
    checks = []
    for setting, label, value, unit_div, src in [
        ("rural", "QSEL 2025: rural connected households, mean", qsel("monthly_kwh.mean", "rural connected"), 1,
         "qsel"),
        ("rural", "QSEL 2024: REG customers outside Kigali, mean", qsel("annual_kwh.mean", "outside Kigali"), 12,
         "qsel"),
        ("rural", "EICV5 2017: rural grid-lit households, mean", e.loc["grid_lighting_rural"], None, "eicv5"),
        ("urban", "QSEL 2024: REG customers in Kigali, mean", qsel("annual_kwh.mean", "in Kigali"), 12, "qsel"),
        ("urban", "EICV5 2017: urban grid-lit households, mean", e.loc["grid_lighting_urban"], None, "eicv5")]:
        v = float(value.value) / unit_div if src == "qsel" else float(value.kwh_month_mean)
        ref = (f"{value.doc_id} p{value.page}" if src == "qsel" else value.evidence_id)
        checks.append({"setting": setting, "archetype_mix_kwh_month": mix[setting], "anchor": label,
                       "anchor_kwh_month": v, "ratio": mix[setting] / v, "verdict": verdict(mix[setting], v),
                       "source": ref})
    return pd.DataFrame(tier_rows), pd.DataFrame(checks)


def establishment_rows(lib: pd.DataFrame) -> pd.DataFrame:
    kwh = lib.set_index("profile_id").daily_kwh_mean
    fac = pd.read_csv(ANCHORS / "mtf_facility_usage.csv").set_index(["facility", "item", "statistic"])
    cur = pd.read_csv(PROCESSED_DIR / "evidence_curated.csv").set_index("parameter")
    rows = []
    for aid, kind, label in [("school", "school", "MTF 2022 grid schools, spending-derived p25-p75"),
                             ("health_centre", "health_facility",
                              "MTF 2022 grid health centres, spending-derived p25-p75 (scaled to the median by "
                              "construction; the appliance-built profile is used for health posts)")]:
        lo, hi = (fac.loc[(kind, "kwh_month_from_spend", s), "value"] for s in ("p25", "p75"))
        sim = kwh[aid] * DAYS_PER_MONTH
        rows.append({"archetype": aid, "simulated": sim, "unit": "kWh/month", "anchor": label,
                     "anchor_value": f"{lo:,.0f}-{hi:,.0f}", "verdict": verdict(sim, low=lo, high=hi),
                     "source": fac.loc[(kind, "kwh_month_from_spend", "p25"), "evidence_id"].rsplit(":", 1)[0]})
    for aid, param, unit, scale in [("grain_mill", "other.daily_kwh.grain_mill", "kWh/day", 1),
                                    ("carpentry", "monthly_kwh.carpentry_tools.lathe_workshop", "kWh/month",
                                     DAYS_PER_MONTH)]:
        r = cur.loc[param]
        sim = kwh[aid] * scale
        rows.append({"archetype": aid, "simulated": sim, "unit": unit,
                     "anchor": f"NREL 2018 (Tanzania micro-grids): {param}",
                     "anchor_value": f"{r.value:g}", "verdict": verdict(sim, float(r.value)),
                     "source": f"{r.doc_id} p{r.page} ({r.evidence_id})"})
    return pd.DataFrame(rows)


def figures(lib: pd.DataFrame, tiers: pd.DataFrame, checks: pd.DataFrame) -> None:
    viz.use()
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    order = [a for ids in HOUSEHOLDS.values() for a in ids] + sorted(set(lib.profile_id) - HOUSEHOLD_IDS)
    lib = lib.set_index("profile_id").loc[order]
    hours = np.arange(24) + 0.5
    fig, axes = plt.subplots(5, 4, figsize=(12, 12), sharex=True)
    for ax, (aid, r) in zip(axes.flat, lib.iterrows(), strict=False):
        w = np.array([r[f"w_h{h:02d}"] for h in range(24)])
        unit, scale = ("kW", 1000) if w.max() > 2000 else ("W", 1)
        colour = viz.CAT[0] if aid in HOUSEHOLD_IDS else viz.CAT[1]
        ax.fill_between(hours, w / scale, step="mid", color=colour, alpha=0.85)
        ax.set_ylim(0, w.max() / scale * 1.15)
        ax.set_title(f"{aid}", fontsize=9)
        ax.text(0.02, 0.97, f"{r.daily_kwh_mean:.2f} kWh/day", transform=ax.transAxes, va="top", fontsize=8,
                color=viz.INK_2)
        ax.set_ylabel(unit, fontsize=8)
        ax.set_xticks([0, 6, 12, 18, 24])
        ax.set_xlim(0, 24)
    for ax in list(axes.flat)[len(lib):]:
        ax.axis("off")
    for ax in axes[-1]:
        ax.set_xlabel("hour of day")
    fig.suptitle("Mean daily load per unit, by RAMP profile (blue: households, orange: establishments)", x=0.0,
                 ha="left", fontweight="bold")
    fig.tight_layout()
    viz.caption(fig, "RAMP 0.5.2, 200 units x 28 days (weekdays and weekends) per profile; hourly means of the "
                     "per-unit load. Axes are per panel: compare shapes, and use the kWh/day labels for size. "
                     "Draft archetypes (config/archetypes.yaml; data/processed/archetypes/).")
    fig.savefig(FIG_DIR / "B1_profiles.png")
    plt.close(fig)

    entries: list[tuple[float, str, float, float | None, bool]] = []  # (y, label, value, median, simulated)
    y = 0.0
    tier = tiers.set_index("archetype")
    for setting in ("rural", "urban"):
        for _, c in checks[checks.setting == setting].iterrows():
            entries.append((y, c.anchor, c.anchor_kwh_month, None, False))
            y += 1
        for aid in HOUSEHOLDS[setting]:
            entries.append((y, f"{aid} (simulated)", tier.loc[aid, "simulated_kwh_month"],
                            tier.loc[aid, "mtf_spend_median"], True))
            y += 1
        y += 0.6
    fig, ax = plt.subplots(figsize=(8, 3.8))
    for yy, _, v, med, sim in entries:
        ax.plot(v, yy, "o" if sim else "D", color=viz.CAT[0] if sim else viz.INK_2, ms=8 if sim else 6)
        if med is not None:
            ax.plot(med, yy, "|", color=viz.CAT[1], ms=14, mew=2)
    ax.set_yticks([e[0] for e in entries], [e[1] for e in entries], fontsize=8)
    ax.set_xlim(0, 35)
    ax.set_xlabel("kWh per household per month")
    ax.invert_yaxis()
    ax.set_title("Household profiles vs independent consumption anchors")
    viz.caption(fig, "Blue: RAMP simulated mean (calibrated to MTF 2022 spending of the same group); orange tick: MTF "
                     "spending-derived median; grey diamonds: independent anchors (QSEL meter/survey means, EICV5 2017 "
                     "spending through the RURA tariff). Means are pulled up by a few large users; medians are lower.")
    fig.savefig(FIG_DIR / "B1_household_check.png")
    plt.close(fig)


def md_table(df: pd.DataFrame) -> str:
    """Minimal GitHub markdown table (tabulate isn't installed)."""
    def fmt(v):
        return f"{v:,.2f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)
    head = "| " + " | ".join(df.columns) + " |"
    rule = "|" + "|".join("---" for _ in df.columns) + "|"
    return "\n".join([head, rule] + ["| " + " | ".join(fmt(v) for v in r) + " |" for r in df.itertuples(index=False)])


def main() -> None:
    lib = pd.read_parquet(LIBRARY)
    tiers, checks = household_rows(lib)
    est = establishment_rows(lib)
    figures(lib, tiers, checks)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# B1 profile checks", "",
             "Generated by `pixi run profile-checks` from `data/processed/profile_library.parquet`. Archetype status: "
             f"**{', '.join(sorted(set(lib.status)))}** (parameters are a human gate; see data/gold/approvals.yaml). "
             "Verdicts: `ok` within 1.5x of an anchor or inside its p25-p75 "
             "range, `check` within 3x, `fail` beyond.", "",
             "## Households: calibration and MTF energy tier", "",
             "Each household archetype's lighting on-time is scaled so its simulated energy equals the MTF 2022 "
             "spending-derived mean of its group, so that column is a calibration, not a check. The MTF tier comes "
             "from daily energy only (Bhatia & Angelou 2015 thresholds in `config/plausibility.yaml`).", "",
             md_table(tiers), "",
             "## Households: independent anchors", "",
             "A setting's archetype mix weights lower and upper households by the allocated upper share "
             f"(rural {upper_shares()['rural']:.2f}, urban {upper_shares()['urban']:.2f}; DHS 2025 by district). "
             "These are the profiles before the REG scaling in `rtl.demand.calibrate`.", "",
             md_table(checks), "",
             "## Establishments", "",
             md_table(est), "",
             "Figures: `reports/figures/B1_profiles.png`, `reports/figures/B1_household_check.png`.", ""]
    OUT.write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
