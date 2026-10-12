"""B1: build RAMP archetype JSONs from the recipes in config/archetypes.yaml.

    pixi run build-archetypes        # -> data/processed/archetypes/<id>.json + archetype_sources.csv

Every number in an archetype comes from one of these, each cited by id in the JSON:
1. World Bank MTF Rwanda 2022 aggregates (validation_anchors/mtf_*.csv, ids `mtf:RWA2022:...`): household
   ownership, appliances per owner and hours of use; enterprise and school/health-facility appliance use;
2. approved evidence (reports/review/evidence_review.csv) that passed the automated checks
   (rtl.demand.validate; flagged rows are left out unless overridden): the median of the matching values;
3. measured appliance energy from REG/QSEL surveys (validation_anchors/residential_kwh_qsel.csv: TV, fridge);
4. an assumption with its rationale (`why`), optionally citing evidence that bounds it (`cite`).

Archetypes are written as `draft`: their parameters are a human gate. archetype_sources.csv counts, per
archetype, how many parameters rest on data and how many on assumptions.
"""

from __future__ import annotations

import json

import pandas as pd
import yaml

from rtl.archetypes import Appliance, Archetype, Sourced
from rtl.demand.review import load as load_evidence
from rtl.demand.validate import excluded_ids
from rtl.settings import GOLD_DIR, PROCESSED_DIR, REPORTS_DIR, load_yaml

ANCHORS = PROCESSED_DIR / "validation_anchors"
OUT_DIR = PROCESSED_DIR / "archetypes"
YEAR = 2022  # MTF fieldwork year, the main source
# the owner's sign-off is recorded in data/gold/approvals.yaml; until then archetypes are drafts
_APPROVALS = yaml.safe_load((GOLD_DIR / "approvals.yaml").read_text())
STATUS = "human_reviewed" if _APPROVALS.get("archetypes", {}).get("decision") == "approved" else "draft"
POWER_UNITS = {"W": 1.0, "Wp": 1.0, "kW": 1000.0}


class Sources:
    """Lookups into the evidence and MTF tables, returning Sourced values."""

    def __init__(self) -> None:
        e = load_evidence()
        self.ev = e[~e.evidence_id.isin(excluded_ids())].copy()
        self.ev["v"] = self.ev.value.fillna((self.ev.value_low + self.ev.value_high) / 2)
        self.mtf = pd.read_csv(ANCHORS / "mtf_appliance_usage.csv").set_index(["group", "appliance"])
        ent = pd.read_csv(ANCHORS / "mtf_enterprise_usage.csv")
        self.ent = ent.set_index(ent.activity + "|" + ent.scope)
        self.fac = pd.read_csv(ANCHORS / "mtf_facility_usage.csv").set_index(["facility", "item", "statistic"])
        self.qsel = pd.read_csv(ANCHORS / "residential_kwh_qsel.csv").set_index("parameter")
        self.kwh = pd.read_csv(ANCHORS / "mtf_household_consumption.csv").set_index("group")

    def ids(self, family: str | None, pattern: str) -> list[str]:
        rows = self.ev[self.ev.appliance.fillna("").str.contains(pattern, regex=True)]
        return list(rows[rows.family == family].evidence_id if family else rows.evidence_id)

    def evidence(self, family: str, pattern: str) -> tuple[float, list[str], int]:
        rows = self.ev[(self.ev.family == family) & self.ev.appliance.fillna("").str.contains(pattern, regex=True)]
        v = rows.v * (rows.unit.map(POWER_UNITS) if family == "rated_power_w" else 1.0)
        rows, v = rows[v.notna()], v.dropna()
        if v.empty:
            raise ValueError(f"no approved, unflagged {family} evidence matches {pattern!r}")
        return float(v.median()), list(rows.evidence_id), len(v)

    def qsel_kwh_month(self, parameter: str) -> tuple[float, str]:
        r = self.qsel.loc[parameter]
        return float(r.value), f"anchor:{r.doc_id}:p{r.page}:{parameter}"


def sourced(spec: dict, src: Sources, family: str, ctx: dict | None = None) -> Sourced:
    """Resolve a recipe value spec (see config/archetypes.yaml) to a Sourced value."""
    ctx = ctx or {}
    cite = src.ids(None, spec["cite"]) if "cite" in spec else []
    if "value" in spec:
        return Sourced(value=spec["value"], assumption=spec["why"], evidence_ids=cite)
    if "evidence" in spec:
        v, ids, n = src.evidence(family, spec["evidence"])
        f = spec.get("factor", 1)
        return Sourced(value=round(v * f, 3), evidence_ids=ids + cite, assumption=spec.get("why"),
                       derivation=f"median of {n} approved {family} values matching {spec['evidence']!r}"
                                  + (f" x {f}" if f != 1 else ""))
    if "enterprise" in spec:
        row = src.ent.loc[ctx["enterprise_row"]]
        return Sourced(value=round(float(row[spec["enterprise"]]), 4), evidence_ids=[row.evidence_id],
                       derivation=f"MTF 2022 {spec['enterprise']} for {ctx['enterprise_row']} (n={row.n_enterprises})")
    if "facility" in spec:
        key = (ctx["facility_kind"], spec["facility"], spec["stat"])
        if key not in src.fac.index:
            raise ValueError(f"no MTF facility statistic {key}")
        row = src.fac.loc[key]
        return Sourced(value=round(float(row.value), 4), evidence_ids=[row.evidence_id],
                       derivation=f"MTF 2022 {key[0]} {key[1]} {key[2]} (n={row.n})")
    raise ValueError(f"unknown value spec {spec}")


def power(kind: str, cfg: dict, src: Sources) -> Sourced:
    spec = cfg["power_w"][kind]
    if "qsel_kwh_month" in spec:  # TV: measured energy / (30 days x MTF hours of grid households)
        kwh, aid = src.qsel_kwh_month(spec["qsel_kwh_month"])
        rows = [src.mtf.loc[("grid_all", a)] for a in spec["mtf_hours"]]
        hours = sum(r.hours_median * r.n_owners for r in rows) / sum(r.n_owners for r in rows)
        return Sourced(value=round(kwh * 1000 / 30 / hours, 1), evidence_ids=[aid] + [r.evidence_id for r in rows],
                       assumption=spec["why"], derivation=f"{kwh} kWh/month / (30 d x {hours:.2f} h/day)")
    return sourced(spec, src, "rated_power_w")


def on_minutes_from_energy(key: str, p: Sourced, cfg: dict, src: Sources, factor: Sourced | None) -> Sourced:
    spec = cfg["energy_wh_day"][key]
    kwh, aid = src.qsel_kwh_month(spec["qsel_kwh_month"])
    f = factor.value if factor else 1.0
    wh_day = kwh * 1000 / 30 * f
    return Sourced(value=round(wh_day / p.value * 60), evidence_ids=[aid] + p.evidence_ids + (
        factor.evidence_ids if factor else []),
        assumption=" ".join(filter(None, [spec["why"], factor and factor.assumption])),
        derivation=f"{kwh} kWh/month / 30 d x {f} = {wh_day:.0f} Wh/day, / {p.value} W")


def preset(name: str, cfg: dict) -> dict[str, Sourced]:
    p = dict(cfg["ramp_presets"][name])
    why = p.pop("why")
    return {k: Sourced(value=v, assumption=why) for k, v in p.items()}


def appliance(name: str, a: dict, own: Sourced, number: Sourced, hours_min: Sourced, cfg: dict,
              src: Sources, p: Sourced, wd_we: Sourced | None = None) -> Appliance:
    w = cfg["windows"][a["windows"]]
    windows = Sourced(value=w["value"], assumption=w["why"],
                      evidence_ids=src.ids(None, w["cite"]) if "cite" in w else [])
    span = sum(b - a_ for a_, b in w["value"])
    if hours_min.value > span:  # RAMP can't fit more on-time than the windows allow: cap, and say so
        hours_min = hours_min.model_copy(update={"value": span, "derivation": (hours_min.derivation or "")
                                                 + f"; capped at the {span} min of its windows"})
    extra = preset(a["preset"], cfg)
    if wd_we is not None:
        extra["wd_we_type"] = wd_we
    return Appliance(name=name, ownership_share=own, number=number, power=p, func_time=hours_min, windows=windows,
                     **extra)


def to_minutes(h: Sourced) -> Sourced:
    return h.model_copy(update={"value": round(h.value * 60), "derivation": (h.derivation or "") + " (h/day x 60)"})


def household(aid: str, rec: dict, cfg: dict, src: Sources) -> Archetype:
    group = rec["group"]
    fallbacks = [group, group.rsplit("_", 1)[0], "grid_all"]  # e.g. grid_rural_lower -> grid_rural -> grid_all
    hh = cfg["household"]
    apps = []
    for name, a in hh["appliances"].items():
        if (group, name) not in src.mtf.index:
            continue
        row = src.mtf.loc[(group, name)]
        if row.ownership_share < hh["min_ownership"]:
            continue
        own = Sourced(value=round(float(row.ownership_share), 4), evidence_ids=[row.evidence_id],
                      derivation=f"MTF 2022 weighted share of {group} households owning one or more "
                                 f"(n_owners={row.n_owners} of {row.n_households})")

        def first(col: str, name=name):
            for g in fallbacks:
                if (g, name) in src.mtf.index and pd.notna(src.mtf.loc[(g, name), col]):
                    return g, src.mtf.loc[(g, name)]
            return None, None

        g, r = first("mean_number_owners")
        number = (Sourced(value=max(1, round(float(r.mean_number_owners))), evidence_ids=[r.evidence_id],
                          derivation=f"MTF 2022 mean number among {g} owners ({r.mean_number_owners:.2f}), rounded")
                  if r is not None
                  else Sourced(value=1, assumption="one per owning household (too few owners to estimate)"))
        p = power(a["power"], cfg, src)
        if "energy" in a:
            minutes = on_minutes_from_energy(a["energy"], p, cfg, src, None)
        else:
            g, r = first("hours_median")
            minutes = (Sourced(value=round(float(r.hours_median) * 60), evidence_ids=[r.evidence_id],
                               derivation=f"MTF 2022 median hours/day among {g} owners ({r.hours_median:g} h) x 60")
                       if r is not None else to_minutes(sourced(a["hours"], src, "hours_per_day")))
        apps.append(appliance(name, a, own, number, minutes, cfg, src, p))
    apps = calibrate_lighting(aid, apps, group, hh, src)
    return Archetype(archetype_id=aid, description=rec["description"], population=rec["population"], year=YEAR,
                     appliances=apps, status=STATUS)


def sim_wh_day(aid: str, apps: list[Appliance]) -> float:
    """Mean simulated Wh/day per unit (RAMP, the runner's settings)."""
    from rtl.demand.runner import N_HOUSEHOLDS, simulate

    if not apps:
        return 0.0
    load = simulate(Archetype(archetype_id=aid, description="calibration", population="-", year=YEAR, appliances=apps))
    return float(load.sum(axis=1).mean() / 60 / N_HOUSEHOLDS)


def scale_lights(apps: list[Appliance], light: list[bool], f: float, note: str = "", eid: str | None = None):
    out = []
    for a, is_l in zip(apps, light, strict=True):
        if is_l:
            ft = a.func_time
            upd = {"value": max(a.func_cycle.value, round(ft.value * f))}
            if eid:
                upd |= {"evidence_ids": ft.evidence_ids + [eid], "derivation": (ft.derivation or "") + note}
            a = a.model_copy(update={"func_time": ft.model_copy(update=upd)})
        out.append(a)
    return out


def calibrate_lighting(aid: str, apps: list[Appliance], group: str, hh: dict, src: Sources) -> list[Appliance]:
    """Scale lighting on-time so RAMP's simulated energy = the group's MTF spending-derived kWh (archetypes.yaml).

    Fitted on simulated energy, not share x number x power x hours: RAMP switches on only part of a household's
    `number` bulbs at a time (Lombardi et al. 2019, eqs. 3-4). Lighting energy is ~linear in on-time, so
    f = (target - other) / lighting, refined once on the scaled archetype.
    """
    c = hh["calibration"]
    row = src.kwh.loc[group]
    target = float(row[c["target"]]) * 1000 / 30
    light = [hh["appliances"][a.name]["preset"] == c["scale_preset"] for a in apps]
    other = sim_wh_day(aid, [a for a, is_l in zip(apps, light, strict=True) if not is_l])
    total0 = sim_wh_day(aid, apps)
    f = (target - other) / (total0 - other)
    total1 = sim_wh_day(aid, scale_lights(apps, light, f))
    f *= (target - other) / (total1 - other)
    lo, hi = c["bounds"]
    if not lo <= f <= hi:
        raise ValueError(f"{group}: lighting factor {f:.2f} outside {c['bounds']}; "
                         "archetype inconsistent with spending")
    note = (f"; x {f:.3f} lighting calibration factor (simulated {total0:.0f} -> {target:.0f} Wh/day = MTF 2022 "
            f"spending-derived {c['target']} of {group}, {row[c['target']]:.1f} kWh/month)")
    print(f"{aid}: lighting x {f:.3f} (simulated {total0:.0f} Wh/day, lights {total0 - other:.0f}; "
          f"target {target:.0f})")
    return scale_lights(apps, light, f, note, row.evidence_id)


def establishment(aid: str, rec: dict, cfg: dict, src: Sources) -> Archetype:
    ctx = {k: rec[k] for k in ("enterprise_row", "facility_kind") if k in rec}
    wd_default = sourced(rec["wd_we_type"], src, "") if "wd_we_type" in rec else None
    apps = []
    for a in rec["appliances"]:
        own = sourced(a["ownership"], src, "ownership_share", ctx)
        number = sourced(a["number"], src, "", ctx)
        number = number.model_copy(update={"value": max(1, round(number.value))})
        p = power(a["power"], cfg, src)
        if "energy" in a:
            factor = sourced(a["energy_factor"], src, "") if "energy_factor" in a else None
            minutes = on_minutes_from_energy(a["energy"], p, cfg, src, factor)
        else:
            minutes = to_minutes(sourced(a["hours"], src, "hours_per_day", ctx))
        wd = sourced(a["wd_we_type"], src, "") if "wd_we_type" in a else wd_default
        apps.append(appliance(a["name"], a, own, number, minutes, cfg, src, p, wd))
    return Archetype(archetype_id=aid, description=rec["description"], population=rec["population"], year=YEAR,
                     appliances=apps, status=STATUS)


def provenance(arch: Archetype) -> dict:
    """Share of an archetype's numbers resting on data vs assumptions (RAMP defaults excluded)."""
    vals = [v for a in arch.appliances for k in ("ownership_share", "number", "power", "func_time")
            for v in [getattr(a, k)]]
    data = sum(bool(v.evidence_ids) and not (v.assumption and not v.derivation) for v in vals)
    return {"archetype_id": arch.archetype_id, "appliances": len(arch.appliances), "parameters": len(vals),
            "from_data": data, "assumed": len(vals) - data, "share_from_data": round(data / len(vals), 2)}


REVIEW = REPORTS_DIR / "review" / "archetype_assumptions.csv"


def assumptions(built: list[Archetype]) -> pd.DataFrame:
    """The numbers that rest on an assumption (not RAMP defaults), for the owner's sign-off."""
    rows = []
    for arch in built:
        for a in arch.appliances:
            for k in ("ownership_share", "number", "power", "func_time", "windows"):
                v = getattr(a, k)
                if v.assumption and not v.derivation:
                    rows.append({"decision": "", "archetype": arch.archetype_id, "appliance": a.name, "parameter": k,
                                 "value": v.value, "why": v.assumption})
    return pd.DataFrame(rows)


def main() -> None:
    cfg = load_yaml("archetypes.yaml")
    src = Sources()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    built = [household(aid, rec, cfg, src) for aid, rec in cfg["household"]["archetypes"].items()]
    built += [establishment(aid, rec, cfg, src) for aid, rec in cfg["establishments"].items()]
    rows = []
    for arch in built:
        (OUT_DIR / f"{arch.archetype_id}.json").write_text(
            json.dumps(arch.model_dump(exclude_defaults=False), indent=1, ensure_ascii=False) + "\n")
        rows.append(provenance(arch) | {"kwh_day_all_units_on": round(arch.expected_daily_kwh(1000) / 1000, 3)})
    table = pd.DataFrame(rows)
    table.to_csv(OUT_DIR / "archetype_sources.csv", index=False)
    review = assumptions(built)
    review.to_csv(REVIEW, index=False)
    print(f"{len(review)} assumed values (windows included) -> {REVIEW}")
    print(table.to_string(index=False))



if __name__ == "__main__":
    main()
