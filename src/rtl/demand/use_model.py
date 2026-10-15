"""B2a: a classical building-use classifier, trained two ways and applied to all 6.4M buildings.

    pixi run use-model

Model A learns from the labels that come with the data (OSM / Overture / facility tags, mapped to the 8 use classes
by `config/use_model.yaml`). Model B adds the LLM labels of the deep dives. Both use the same footprint and context
features, none of which is a tag or a source column (those would teach the model how labels were made, not use).

Evaluation (all held out):
1. tags, 5-fold cross-validation grouped by district (a district never trains its own test);
2. LLM labels, leave one deep-dive sector out (B never sees the sector it is tested on);
3. human gold labels (uncertain reference; never trained on).

Outputs: data/interim/use_features.parquet, data/processed/use_probs.parquet (model B probabilities per building
plus both models' top class), data/processed/use_model/{scores.csv, confusion_*.csv, importance.csv, summary.json}.
"""

from __future__ import annotations

import json
import re

import duckdb
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, roc_auc_score
from sklearn.model_selection import GroupKFold

from rtl.demand.mapping import LABELER
from rtl.settings import GOLD_DIR, INTERIM_DIR, PROCESSED_DIR, load_yaml

FEATURES_PATH = INTERIM_DIR / "use_features.parquet"
PROBS_PATH = PROCESSED_DIR / "use_probs.parquet"
OUT_DIR = PROCESSED_DIR / "use_model"
DEEP_DIVES = {"Nyamirambo": "Nyarugenge", "Muhoza": "Musanze", "Nasho": "Kirehe"}


# ------------------------------------------------------------------------------------------------ features
def build_features(con) -> pd.DataFrame:
    """One row per building: footprint, height, context and H3 r9 neighbourhood features (NaN = not observed)."""
    I = INTERIM_DIR  # noqa: E741, N806
    con.sql(f"""COPY (
        WITH j AS (
          SELECT b.bldg_id, b.h3_r9, h3_latlng_to_cell(b.lat, b.lon, 10) AS h3_r10, b.area_m2, b.perimeter_m,
                 b.compactness, b.n_vertices, h.height_m, h.est_floors,
                 h.first_seen_year, x.adm2_name AS district, x.adm3_name AS sector, x.dist_road_major_m,
                 x.dist_road_any_m, x.dist_powerline_m, x.poi_count_50m, x.poi_count_250m, x.ghsl_class,
                 x.ghsl_nres_share, x.pop_density_per_km2, x.rwi, x.elevation_m, c.cropland_share
          FROM '{I}/buildings_base.parquet' b JOIN '{I}/building_context.parquet' x USING (bldg_id)
          LEFT JOIN '{I}/building_height.parquet' h USING (bldg_id)
          LEFT JOIN '{I}/cropland_h3r8.parquet' c ON c.h3_r8 = h3_cell_to_parent(b.h3_r9, 8))
        SELECT bldg_id, district, sector,
               ln(area_m2)::FLOAT AS log_area, ln(perimeter_m)::FLOAT AS log_perimeter,
               compactness::FLOAT AS compactness,
               n_vertices::FLOAT AS n_vertices, height_m::FLOAT AS height_m,
               (height_m IS NOT NULL)::FLOAT AS has_height, est_floors::FLOAT AS est_floors,
               first_seen_year::FLOAT AS first_seen_year,
               ln(1 + dist_road_major_m)::FLOAT AS log_dist_road_major,
               ln(1 + dist_road_any_m)::FLOAT AS log_dist_road_any,
               ln(1 + dist_powerline_m)::FLOAT AS log_dist_powerline, poi_count_50m::FLOAT AS poi_count_50m,
               poi_count_250m::FLOAT AS poi_count_250m,
               (dense_rank() OVER (ORDER BY ghsl_class) - 1)::FLOAT AS ghsl_code,
               ghsl_nres_share::FLOAT AS ghsl_nres_share,
               ln(1 + pop_density_per_km2)::FLOAT AS log_pop_density, rwi::FLOAT AS rwi,
               elevation_m::FLOAT AS elevation_m,
               cropland_share::FLOAT AS cropland_share,
               (count(*) OVER w)::FLOAT AS r9_n_buildings, (median(area_m2) OVER w)::FLOAT AS r9_median_area,
               (area_m2 / median(area_m2) OVER w)::FLOAT AS r9_area_ratio,
               (percent_rank() OVER (PARTITION BY h3_r9 ORDER BY area_m2))::FLOAT AS r9_area_pct,
               (area_m2 = max(area_m2) OVER w)::FLOAT AS r9_largest,
               (count(*) OVER w10)::FLOAT AS r10_n_buildings,
               (area_m2 / max(area_m2) OVER w10)::FLOAT AS r10_area_ratio_max,
               (rank() OVER (PARTITION BY h3_r10 ORDER BY area_m2 DESC))::FLOAT AS r10_size_rank
        FROM j WINDOW w AS (PARTITION BY h3_r9), w10 AS (PARTITION BY h3_r10)) TO '{FEATURES_PATH}' (FORMAT parquet)""")
    return pd.read_parquet(FEATURES_PATH)


# ------------------------------------------------------------------------------------------------ labels
def tag_rule(row: dict, rules: dict) -> str | None:
    """The class a building's tags imply, first rule wins (facility, OSM amenity, OSM shop, OSM building, Overture)."""
    f = row.get("facility_type")
    if isinstance(f, str):
        for cls, prefixes in rules["facility"].items():
            if any(f.startswith(p) for p in prefixes):
                return cls
    a = row.get("osm_amenity")
    if isinstance(a, str):
        for cls, vals in rules["osm_amenity"].items():
            if a in vals:
                return cls
    if isinstance(row.get("osm_shop"), str):
        return "commercial"
    b = row.get("osm_building")
    if isinstance(b, str):
        for cls, vals in rules["osm_building"].items():
            if b in vals:
                return cls
    o = row.get("overture_category")
    if isinstance(o, str):
        for cls, pattern in rules["overture"].items():
            if re.search(pattern, o):
                return cls
    return None


def tag_labels(con, cfg: dict) -> pd.DataFrame:
    I = INTERIM_DIR  # noqa: E741, N806
    t = con.sql(f"""SELECT bldg_id, facility_type, osm_amenity, osm_shop, osm_building, overture_category
                    FROM '{I}/building_context.parquet'
                    WHERE facility_type IS NOT NULL OR osm_amenity IS NOT NULL OR osm_shop IS NOT NULL
                       OR coalesce(osm_building, 'yes') NOT IN ('yes', 'no') OR overture_category IS NOT NULL""").df()
    t["label"] = [tag_rule(r, cfg["tags"]) for r in t.to_dict("records")]
    return t.dropna(subset=["label"])[["bldg_id", "label"]].assign(source="tag")


def llm_labels(con) -> pd.DataFrame:
    return con.sql(f"""SELECT bldg_id, label, confidence FROM '{PROCESSED_DIR}/labels.parquet'
                       WHERE labeler = '{LABELER}' AND label <> 'unknown'""").df().assign(source="llm")


# ------------------------------------------------------------------------------------------------ model
def class_weights(y: np.ndarray, classes: list[str], cfg: dict) -> np.ndarray:
    counts = pd.Series(y).value_counts()
    w = (counts.max() / counts) ** cfg["class_weight_power"]
    return pd.Series(y).map(w.clip(upper=cfg["max_class_weight"])).to_numpy(float)


def fit(X: np.ndarray, y: np.ndarray, sw: np.ndarray, cfg: dict) -> HistGradientBoostingClassifier:  # noqa: N803
    m = cfg["model"]
    model = HistGradientBoostingClassifier(max_iter=m["max_iter"], learning_rate=m["learning_rate"],
                                           max_leaf_nodes=m["max_leaf_nodes"], l2_regularization=m["l2_regularization"],
                                           random_state=m["seed"])
    return model.fit(X, y, sample_weight=sw * class_weights(y, cfg["classes"], m))


def proba(model, X: np.ndarray, classes: list[str]) -> np.ndarray:  # noqa: N803
    """Probabilities in the config's class order (a class absent from training gets 0)."""
    p = model.predict_proba(X)
    out = np.zeros((len(X), len(classes)), dtype="float32")
    for j, c in enumerate(model.classes_):
        out[:, classes.index(c)] = p[:, j]
    return out


def scores(name: str, test: str, y: np.ndarray, pred: np.ndarray, classes: list[str],
           prob: np.ndarray | None = None) -> dict:
    """Accuracy and F1 of the top class, and (given probabilities) one-vs-rest AUC per class: how well each class's
    probability *ranks* buildings, which is what the census allocation uses."""
    present = [c for c in classes if c in set(y)]
    per = f1_score(y, pred, labels=present, average=None, zero_division=0)
    out = {"model": name, "test": test, "n": len(y), "accuracy": accuracy_score(y, pred),
           "macro_f1": f1_score(y, pred, labels=present, average="macro", zero_division=0),
           **{f"f1_{c}": v for c, v in zip(present, per, strict=True)}}
    if prob is not None:
        aucs = {c: roc_auc_score(y == c, prob[:, classes.index(c)]) for c in present if 10 <= (y == c).sum() < len(y)}
        out["macro_auc"] = float(np.mean(list(aucs.values()))) if aucs else np.nan
        out.update({f"auc_{c}": v for c, v in aucs.items()})
    return out


def training_set(feat: pd.DataFrame, tags: pd.DataFrame, llm: pd.DataFrame | None, exclude: set[str]) -> pd.DataFrame:
    """Labelled rows (a tag wins over the LLM), minus excluded buildings, joined to features."""
    lab = tags.assign(confidence=1.0)
    if llm is not None:
        lab = pd.concat([lab, llm[~llm.bldg_id.isin(tags.bldg_id)]], ignore_index=True)
    lab = lab[~lab.bldg_id.isin(exclude)]
    return lab.merge(feat, on="bldg_id")


def evaluate(feat, tags, llm, gold, cfg) -> tuple[pd.DataFrame, dict]:
    F, classes = cfg["features"], cfg["classes"]  # noqa: N806
    rows, conf = [], {}
    # 1. tags, grouped by district
    for name, use_llm in (("A tags", False), ("B tags+llm", True)):
        d = training_set(feat, tags, llm if use_llm else None, set())
        pred = pd.Series(index=d.index, dtype=object)
        prob = pd.DataFrame(0.0, index=d.index, columns=classes)
        for tr, te in GroupKFold(n_splits=5).split(d, groups=d.district.fillna("none")):
            m = fit(d.iloc[tr][F].to_numpy(), d.iloc[tr].label.to_numpy(), d.iloc[tr].confidence.to_numpy(), cfg)
            te_tag = d.iloc[te][d.iloc[te].source == "tag"]
            pred[te_tag.index] = m.predict(te_tag[F].to_numpy())
            prob.loc[te_tag.index] = proba(m, te_tag[F].to_numpy(), classes)
        t = d[d.source == "tag"]
        rows.append(scores(name, "tags (held-out districts)", t.label.to_numpy(), pred[t.index].to_numpy(), classes,
                           prob.loc[t.index].to_numpy()))
        conf[f"{name}|tags"] = confusion_matrix(t.label, pred[t.index], labels=classes)
    # 2. LLM labels, leave one deep-dive sector out
    for name, use_llm in (("A tags", False), ("B tags+llm", True)):
        ys, ps, pp = [], [], []
        for sector, district in DEEP_DIVES.items():
            test_ids = set(feat.bldg_id[(feat.sector == sector) & (feat.district == district)])
            held = llm[llm.bldg_id.isin(test_ids) & ~llm.bldg_id.isin(tags.bldg_id)]
            d = training_set(feat, tags[~tags.bldg_id.isin(test_ids)], llm if use_llm else None, test_ids)
            m = fit(d[F].to_numpy(), d.label.to_numpy(), d.confidence.to_numpy(), cfg)
            te = held.merge(feat, on="bldg_id")
            ys.append(te.label.to_numpy())
            ps.append(m.predict(te[F].to_numpy()))
            pp.append(proba(m, te[F].to_numpy(), classes))
        y, p = np.concatenate(ys), np.concatenate(ps)
        rows.append(scores(name, "LLM labels (held-out sector)", y, p, classes, np.vstack(pp)))
        conf[f"{name}|llm"] = confusion_matrix(y, p, labels=classes)
    # 3. gold (never trained on)
    g = gold[gold.label.isin(classes)]
    for name, use_llm in (("A tags", False), ("B tags+llm", True)):
        d = training_set(feat, tags, llm if use_llm else None, set(g.bldg_id))
        m = fit(d[F].to_numpy(), d.label.to_numpy(), d.confidence.to_numpy(), cfg)
        te = g.merge(feat, on="bldg_id")
        rows.append(scores(name, "human gold (uncertain)", te.label.to_numpy(), m.predict(te[F].to_numpy()), classes,
                           proba(m, te[F].to_numpy(), classes)))
        conf[f"{name}|gold"] = confusion_matrix(te.label, m.predict(te[F].to_numpy()), labels=classes)
    # reference: the LLM itself against gold
    lg = g.merge(llm[["bldg_id", "label"]].rename(columns={"label": "llm"}), on="bldg_id")
    rows.append(scores("LLM (Haiku v2)", "human gold (uncertain)", lg.label.to_numpy(), lg.llm.to_numpy(), classes))
    return pd.DataFrame(rows), conf


def main() -> None:
    cfg = load_yaml("use_model.yaml")
    F, classes = cfg["features"], cfg["classes"]  # noqa: N806
    con = duckdb.connect()
    con.sql("INSTALL h3 FROM community; LOAD h3; SET memory_limit='8GB'")
    feat = build_features(con)
    tags, llm = tag_labels(con, cfg), llm_labels(con)
    gold = pd.read_csv(GOLD_DIR / "gold_labels.csv")
    gold = gold[~gold.repeat.astype(bool)] if "repeat" in gold else gold
    print(f"{len(feat):,} buildings; tag labels {len(tags):,} ({tags.label.value_counts().to_dict()}); "
          f"LLM labels {len(llm):,}")
    sc, conf = evaluate(feat, tags, llm, gold, cfg)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sc.round(4).to_csv(OUT_DIR / "scores.csv", index=False)
    for k, m in conf.items():
        pd.DataFrame(m, index=classes, columns=classes).to_csv(
            OUT_DIR / f"confusion_{k.split('|')[0][0]}_{k.split('|')[1]}.csv")
    # final models on all labels, applied nationally
    X = feat[F].to_numpy()  # noqa: N806
    out = feat[["bldg_id"]].copy()
    for name, use_llm in (("a", False), ("b", True)):
        d = training_set(feat, tags, llm if use_llm else None, set())
        m = fit(d[F].to_numpy(), d.label.to_numpy(), d.confidence.to_numpy(), cfg)
        p = np.vstack([proba(m, X[i:i + 500_000], classes) for i in range(0, len(X), 500_000)])
        out[f"{name}_label"] = np.array(classes)[p.argmax(1)]
        if name == "b":
            for j, c in enumerate(classes):
                out[f"p_{c}"] = p[:, j]
            imp = pd.Series(dict(zip(F, _importance(m, d, F), strict=True))).sort_values(ascending=False)
            imp.round(4).to_csv(OUT_DIR / "importance.csv", header=["importance"])
    tag_of = tags.set_index("bldg_id").label
    out["tag_label"] = out.bldg_id.map(tag_of)
    out.to_parquet(PROBS_PATH, index=False)
    mix = {k: out[f"{k}_label"].value_counts(normalize=True).round(4).to_dict() for k in ("a", "b")}
    (OUT_DIR / "summary.json").write_text(json.dumps({
        "buildings": len(out), "tag_labels": tags.label.value_counts().to_dict(), "llm_labels": len(llm),
        "national_top_class_mix": mix, "features": F}, indent=1))
    print(sc.round(3).to_string(index=False))
    print("national top-class mix:", json.dumps(mix))


def _importance(model, d: pd.DataFrame, features: list[str], n: int = 20_000, seed: int = 7) -> list[float]:
    """Permutation importance (drop in accuracy) on a sample of the training rows."""
    s = d.sample(min(n, len(d)), random_state=seed)
    X, y = s[features].to_numpy(), s.label.to_numpy()  # noqa: N806
    base = accuracy_score(y, model.predict(X))
    rng = np.random.default_rng(seed)
    out = []
    for j in range(len(features)):
        Xp = X.copy()  # noqa: N806
        Xp[:, j] = Xp[rng.permutation(len(Xp)), j]
        out.append(base - accuracy_score(y, model.predict(Xp)))
    return out


if __name__ == "__main__":
    main()
