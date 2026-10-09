"""Seeded, stratified sample of buildings for the labelling pilot, and its gold subset.

Pilot = 1,500 buildings from each deep-dive sector + 1,500 from the rest of Rwanda (6,000).
Within each group, strata are footprint-size tercile x tagged/untagged. "Tagged" means an *informative* tag:
an OSM amenity or shop, an OSM building value other than plain "yes", an Overture place, or a facility record.
Tagged buildings are rare, so they are over-sampled to 25% of each group, so the evaluation has enough of them.
The gold subset is 100 per group (400), drawn from the pilot with the same strata, so every gold building also has
pilot labels.

Output: data/gold/sample_ids.csv (committed): bldg_id, group, size_tercile, tagged, in_gold.
"""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from rtl.conform.buildings import OUT as BASE
from rtl.settings import GOLD_DIR, INTERIM_DIR, aoi

SEED = 20261009
PER_GROUP = 1500
GOLD_PER_GROUP = 100
TAGGED_SHARE = 0.25
OUT = GOLD_DIR / "sample_ids.csv"


def population() -> pd.DataFrame:
    dd = aoi()["deep_dives"]
    cases = " ".join(f"WHEN c.adm2_name = '{v['district']}' AND c.adm3_name = '{v['sector']}' THEN '{k}'"
                     for k, v in dd.items())
    return duckdb.sql(f"""
        SELECT b.bldg_id, b.area_m2,
               CASE {cases} ELSE 'national' END AS "group",
               (c.osm_amenity IS NOT NULL OR c.osm_shop IS NOT NULL
                OR coalesce(c.osm_building, 'yes') NOT IN ('yes')
                OR c.overture_category IS NOT NULL OR c.facility_type IS NOT NULL) AS tagged
        FROM read_parquet('{BASE}') b JOIN read_parquet('{INTERIM_DIR / "building_context.parquet"}') c
          USING (bldg_id)
    """).df()


def stratified(df: pd.DataFrame, n: int, rng: np.random.Generator) -> pd.DataFrame:
    """n rows: TAGGED_SHARE tagged (if available), split evenly over size terciles."""
    parts = []
    n_tag = min(int(round(n * TAGGED_SHARE)), int(df.tagged.sum()))
    for tagged, k in [(True, n_tag), (False, n - n_tag)]:
        sub = df[df.tagged == tagged]
        per = np.full(3, k // 3) + (np.arange(3) < k % 3)
        for t, kk in zip(range(3), per, strict=True):
            pool = sub[sub.size_tercile == t]
            parts.append(pool.sample(n=min(kk, len(pool)), random_state=rng.integers(1 << 31)))
    return pd.concat(parts)


def main() -> None:
    rng = np.random.default_rng(SEED)
    pop = population()
    pop["size_tercile"] = pop.groupby("group").area_m2.transform(
        lambda a: pd.qcut(a.rank(method="first"), 3, labels=False))
    pilot = pd.concat([stratified(g, PER_GROUP, rng) for _, g in pop.groupby("group")])
    gold_ids = pd.concat([stratified(g, GOLD_PER_GROUP, rng) for _, g in pilot.groupby("group")]).bldg_id
    pilot["in_gold"] = pilot.bldg_id.isin(set(gold_ids))
    out = pilot[["bldg_id", "group", "size_tercile", "tagged", "in_gold"]].sort_values(["group", "bldg_id"])
    GOLD_DIR.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, index=False)
    print(out.groupby(["group", "tagged"]).agg(n=("bldg_id", "size"), gold=("in_gold", "sum")).to_string())


if __name__ == "__main__":
    main()
