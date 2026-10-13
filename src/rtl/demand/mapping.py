"""B2: turn each labelled building into weights on RAMP profiles (rules in config/profile_mapping.yaml).

    pixi run profile-mapping

Uses the Claude class probabilities (not just the top label), so uncertainty carries into the demand estimate:
a building that is 60% residential and 30% shop-house gets 0.67 + 0.33 household units and 0.33 shop units
(probabilities under `min_class_probability` are ignored and the rest renormalised). Productive use is not
mapped here: see rtl.demand.productive_use.
Output: data/processed/building_profiles.parquet (bldg_id, profile_id, weight).
"""

from __future__ import annotations

import json

import duckdb
import pandas as pd

from rtl.schemas import LABEL_CLASSES
from rtl.settings import INTERIM_DIR, PROCESSED_DIR, load_yaml

OUT = PROCESSED_DIR / "building_profiles.parquet"
LABELER = "claude-haiku-4-5|text|label_v2"  # the configuration chosen on the gold set (reports/labels/eval.md)


def context_flags(ctx: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    u = cfg["context"]["urban"]
    urban = (ctx.pop_density_per_km2.fillna(0) >= u["pop_density_per_km2_min"]) | ctx.ghsl_class.isin(u["ghsl_classes"])
    if cfg["context"]["wealth_split"] != "within_setting":
        raise ValueError("only wealth_split: within_setting is implemented")
    # national medians per setting, so the split doesn't depend on which buildings happen to be labelled
    med = duckdb.sql(f"""
        SELECT (pop_density_per_km2 >= {u["pop_density_per_km2_min"]} OR ghsl_class IN
                ({", ".join(repr(c) for c in u["ghsl_classes"])})) AS urban, median(rwi) AS m
        FROM read_parquet('{INTERIM_DIR / "building_context.parquet"}') GROUP BY 1""").df().set_index("urban").m
    rich = ctx.rwi > urban.map(lambda x: med[bool(x)])
    return ctx.assign(urban=urban, rich=rich.fillna(False))


def _matches(row: pd.Series, when: dict) -> bool:
    for k, v in when.items():
        if k in ("urban", "rich") and bool(row[k]) != v:
            return False
        if k == "overture_any" and row.overture_category not in v:
            return False
        if k == "facility_prefix" and not str(row.facility_type or "").startswith(v):
            return False
    return True


def class_profiles(row: pd.Series, cls: str, rules: dict) -> dict[str, float]:
    """Profiles (with units) for one class in this building's context: first matching rule wins."""
    for rule in rules[cls]:
        if _matches(row, rule["when"]):
            out: dict[str, float] = {}
            for prof, units in rule["profiles"].items():
                sub = class_profiles(row, prof[1:], rules) if prof.startswith("@") else {prof: 1.0}
                for p, w in sub.items():
                    out[p] = out.get(p, 0.0) + units * w
            return out
    return {}


def kept_probabilities(probs: dict[str, float], cfg: dict, top_class: bool = False) -> dict[str, float]:
    """Drop area-level classes, ignore probabilities under the threshold, renormalise what is left.
    top_class=True keeps only the most likely remaining class. A building left with nothing keeps its most likely
    remaining class, so every labelled building is still modelled."""
    p = {c: float(probs.get(c, 0.0)) for c in LABEL_CLASSES if c not in cfg.get("area_level_classes", [])}
    if not any(v > 0 for v in p.values()):
        return {}
    best = max(p, key=p.get)
    if top_class:
        return {best: 1.0}
    keep = {c: v for c, v in p.items() if v >= cfg.get("min_class_probability", 0.0)} or {best: p[best]}
    total = sum(keep.values())
    return {c: v / total for c, v in keep.items()}


def building_weights(row: pd.Series, probs: dict[str, float], rules: dict) -> dict[str, float]:
    total = sum(probs.get(c, 0.0) for c in LABEL_CLASSES) or 1.0
    out: dict[str, float] = {}
    for cls in LABEL_CLASSES:
        if cls not in rules:
            continue
        p = probs.get(cls, 0.0) / total
        if p <= 0:
            continue
        for prof, w in class_profiles(row, cls, rules).items():
            out[prof] = out.get(prof, 0.0) + p * w
    return out


def weights_table(top_class: bool = False) -> pd.DataFrame:
    """(bldg_id, profile_id, weight) for every labelled building. top_class=True puts all probability on the
    most likely per-building class (the dashboard's "top class only" alternative); the default keeps the class
    probabilities at or above the threshold."""
    cfg = load_yaml("profile_mapping.yaml")
    lab = pd.read_parquet(PROCESSED_DIR / "labels.parquet")
    lab = lab[lab.labeler == LABELER][["bldg_id", "label", "probs_json"]]
    ctx = duckdb.sql(f"""SELECT bldg_id, pop_density_per_km2, ghsl_class, rwi, overture_category, facility_type
                         FROM read_parquet('{INTERIM_DIR / "building_context.parquet"}')""").df()
    df = context_flags(lab.merge(ctx, on="bldg_id", how="left"), cfg)
    rows = []
    for r in df.itertuples(index=False):
        r = pd.Series(r._asdict())
        raw = {k: v for k, v in json.loads(r.probs_json).items() if not k.startswith("_")}
        if r.label and not any(raw.values()):
            raw = {r.label: 1.0}
        probs = kept_probabilities(raw, cfg, top_class)
        rows += [{"bldg_id": r.bldg_id, "profile_id": p, "weight": w}
                 for p, w in building_weights(r, probs, cfg["classes"]).items() if w > 1e-6]
    out = pd.DataFrame(rows)
    # located productive use and services (rtl.demand.productive_use, rtl.demand.services): a trade or service tag
    # replaces the class-based profiles
    for name in ("pu_located.parquet", "svc_located.parquet"):
        loc_path = PROCESSED_DIR / name
        if loc_path.exists():
            loc = pd.read_parquet(loc_path)[["bldg_id", "profile_id"]].assign(weight=1.0)
            out = pd.concat([out[~out.bldg_id.isin(loc.bldg_id)], loc], ignore_index=True)
    return out


def main() -> None:
    out = weights_table()
    out.to_parquet(OUT, index=False)
    summary = out.groupby("profile_id").weight.sum().sort_values(ascending=False)
    print(f"{out.bldg_id.nunique():,} buildings -> {len(out):,} building-profile weights -> {OUT}")
    print("expected units per profile:\n" + summary.round(0).astype(int).to_string())


if __name__ == "__main__":
    main()
