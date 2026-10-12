"""B4: a classical model of whether a household is connected to the grid, applied to every building.

    pixi run grid-model

LABEL. DHS 2025 households (`hv206`, "has electricity"; solar-lantern households answer "no") at their cluster's
GPS point. DHS displaces cluster points (urban up to 2 km, rural up to 5 km, within the district), so a label
can't be tied to a building: the model learns the connection rate of a NEIGHBOURHOOD from features averaged over
the displacement radius, and each building gets its neighbourhood's probability. Grid connection is observed, so
this is a model we can test, unlike training on our own simulated kWh.

FEATURES. Building-weighted means over the H3 r8 cells within the radius (urban k=2 rings ~2 km, rural k=5 ~5 km):
distance to the (predicted) MV line and to roads, population density, wealth (Meta RWI), settlement class, building
size and density, places nearby, elevation and cropland. No DHS variable is a feature.

EVALUATION. Grouped 5-fold cross-validation by district, so a district is never in its own training set; household-
weighted AUC, Brier score and cluster-level error, against two baselines. External check: predicted district shares
against EICV7 2024 grid lighting by district (a validation anchor, never a feature).

RESTRICTED DATA. Cluster coordinates and microdata stay in data/restricted/; the published outputs are the model,
its scores, predictions per building/cell and figures without cluster locations.
"""

from __future__ import annotations

import json
import pickle

import duckdb
import geopandas as gpd
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from rtl.demand.productive_use import setting_sql
from rtl.settings import DATA_DIR, INTERIM_DIR, PROCESSED_DIR, load_yaml

DHS = DATA_DIR / "restricted" / "dhs" / "RW_2025_DHS_10092026_81_260427"
DERIVED = DATA_DIR / "restricted" / "dhs" / "derived"  # cluster-level training table (has coordinates: restricted)
OUT_DIR = PROCESSED_DIR / "grid_model"
RING = {"urban": 2, "rural": 5}  # H3 r8 rings matching DHS displacement (2 km urban, 5 km rural)
R8_KM2 = 0.737  # mean H3 r8 cell area
SEED = 7

# building-level values averaged per ring; name -> SQL over buildings (b), context (x), height (h), cropland (c)
BUILDING_VALUES = {
    "log_dist_mv": "ln(1 + coalesce(x.dist_powerline_m, 20000))",
    "log_dist_road_major": "ln(1 + coalesce(x.dist_road_major_m, 20000))",
    "log_dist_road_any": "ln(1 + coalesce(x.dist_road_any_m, 5000))",
    "log_pop_density": "ln(1 + coalesce(x.pop_density_per_km2, 0))",
    "rwi": "coalesce(x.rwi, 0)",
    "urban_ghsl": "(x.ghsl_class IN ({urban}))::INT",
    "nres_share": "coalesce(x.ghsl_nres_share, 0)",
    "log_footprint": "ln(b.area_m2)",
    "large_share": "(b.area_m2 >= 100)::INT",
    "log_poi_250m": "ln(1 + coalesce(x.poi_count_250m, 0))",
    "elevation_km": "coalesce(x.elevation_m, 1500) / 1000",
    "cropland": "coalesce(c.cropland_share, 0)",
}
FEATURES = [*BUILDING_VALUES, "log_bldg_density"]
LABELS = {"log_dist_mv": "Distance to MV line (log m)", "log_dist_road_major": "Distance to major road (log m)",
          "log_dist_road_any": "Distance to any road (log m)", "log_pop_density": "Population density (log /km²)",
          "rwi": "Relative wealth (RWI)", "urban_ghsl": "Urban settlement share (GHSL)",
          "nres_share": "Non-residential built share (GHSL)", "log_footprint": "Footprint size (log m²)",
          "large_share": "Share of buildings ≥ 100 m²", "log_poi_250m": "Places within 250 m (log)",
          "elevation_km": "Elevation (km)",
          "cropland": "Cropland share (WorldCover)", "log_bldg_density": "Building density (log /km²)"}


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.sql("INSTALL h3 FROM community; LOAD h3; SET memory_limit='8GB'")
    return con


def r8_sums(con) -> None:
    """TEMP TABLE r8: per H3 r8 cell, building count and sums of each building value (rings add these up)."""
    I = INTERIM_DIR  # noqa: E741, N806
    urban = ", ".join(repr(c) for c in load_yaml("profile_mapping.yaml")["context"]["urban"]["ghsl_classes"])
    vals = ", ".join(f"sum({sql.format(urban=urban)}) AS s_{k}" for k, sql in BUILDING_VALUES.items())
    con.sql(f"""CREATE OR REPLACE TEMP TABLE r8 AS
        SELECT h3_cell_to_parent(b.h3_r9, 8) AS h, count(*) AS n, {vals}
        FROM '{I}/buildings_base.parquet' b JOIN '{I}/building_context.parquet' x USING (bldg_id)
        LEFT JOIN '{I}/building_height.parquet' h USING (bldg_id)
        LEFT JOIN '{I}/cropland_h3r8.parquet' c ON c.h3_r8 = h3_cell_to_parent(b.h3_r9, 8)
        WHERE NOT contains(coalesce(b.qa_flags, ''), 'nested_same_source')
        GROUP BY 1""")


def ring_features(con, cells: pd.DataFrame) -> pd.DataFrame:
    """Features for (key, h3_r8, setting) rows: building-weighted means over the setting's ring of r8 cells."""
    con.register("cells", cells[["key", "h3_r8", "setting"]])
    sums = ", ".join(f"sum(r8.s_{k}) / nullif(sum(r8.n), 0) AS {k}" for k in BUILDING_VALUES)
    df = con.sql(f"""
        WITH d AS (SELECT key, unnest(h3_grid_disk(h3_r8, CASE setting WHEN 'urban' THEN {RING['urban']}
                                                                       ELSE {RING['rural']} END)) AS h,
                          CASE setting WHEN 'urban' THEN {RING['urban']} ELSE {RING['rural']} END AS k FROM cells)
        SELECT d.key, {sums},
               ln(1 + coalesce(sum(r8.n), 0) / ((3 * any_value(d.k) * (any_value(d.k) + 1) + 1) * {R8_KM2}))
                 AS log_bldg_density
        FROM d LEFT JOIN r8 ON r8.h = d.h GROUP BY d.key""").df()
    return cells.merge(df, on="key", how="left")


# ------------------------------------------------------------------------------------------------ label
def clusters(con) -> pd.DataFrame:
    """One row per DHS cluster: weighted households connected / not, district, setting, ring features."""
    hr = pd.read_stata(DHS / "RWHR91DT" / "RWHR91FL.dta", columns=["hv001", "hv005", "hv025", "hv206", "shdistrict"],
                       convert_categoricals=True)
    hr["w"] = hr.hv005 / 1e6
    hr["yes"] = (hr.hv206.astype(str) == "yes").astype(int)
    g = hr.groupby("hv001").agg(n_hh=("w", "size"), w_yes=("w", lambda s: s[hr.loc[s.index, "yes"] == 1].sum()),
                                w_all=("w", "sum"), district=("shdistrict", "first"), dhs_setting=("hv025", "first"))
    ge = gpd.read_file(DHS / "RWGE91FL" / "RWGE91FL.shp")[["DHSCLUST", "LATNUM", "LONGNUM", "URBAN_RURA"]]
    ge = ge[(ge.LATNUM != 0) | (ge.LONGNUM != 0)]
    c = g.join(ge.set_index("DHSCLUST"), how="inner").reset_index(names="cluster")
    c["setting"] = np.where(c.URBAN_RURA == "U", "urban", "rural")
    c["h3_r8"] = con.sql("SELECT h3_latlng_to_cell_string(lat, lon, 8) AS h FROM (SELECT unnest(?) AS lat, "
                         "unnest(?) AS lon)", params=[list(c.LATNUM), list(c.LONGNUM)]).df().h.to_numpy()
    c["key"] = c.cluster.astype(str)
    c = ring_features(con, c)
    c["share"] = (c.w_yes / c.w_all).clip(0, 1)
    c["district"] = c.district.astype(str)
    return c


def binomial_rows(c: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Two rows per cluster (connected, not) weighted by the households behind them: the household-level weighted
    likelihood, without repeating identical feature rows."""
    X = np.vstack([c[FEATURES].to_numpy(float)] * 2)  # noqa: N806
    y = np.r_[np.ones(len(c)), np.zeros(len(c))]
    w = np.r_[c.w_yes.to_numpy(), (c.w_all - c.w_yes).to_numpy()]
    groups = np.r_[c.district.to_numpy(), c.district.to_numpy()]
    return X, y, w, groups


def models() -> dict:
    return {
        "logistic": make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=2000)),
        "gradient_boosting": HistGradientBoostingClassifier(
            max_depth=3, learning_rate=0.05, max_iter=300, min_samples_leaf=20, l2_regularization=1.0,
            monotonic_cst=[-1 if f == "log_dist_mv" else 0 for f in FEATURES], random_state=SEED),
    }


def evaluate(c: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Grouped (by district) 5-fold CV: out-of-fold cluster predictions and household-weighted scores."""
    X, y, w, groups = binomial_rows(c)  # noqa: N806
    n = len(c)
    oof = pd.DataFrame({"cluster": c.cluster, "setting": c.setting, "share": c.share, "w_all": c.w_all})
    folds = list(GroupKFold(n_splits=5).split(X[:n], groups=groups[:n]))
    for name, model in models().items():
        p = np.zeros(n)
        for tr, te in folds:
            tr2 = np.r_[tr, tr + n]
            key = "logisticregression__sample_weight" if name == "logistic" else "sample_weight"
            fit_kw = {key: w[tr2]}
            model.fit(X[tr2], y[tr2], **fit_kw)
            p[te] = model.predict_proba(X[te])[:, 1]
        oof[name] = p
    # baselines: the training folds' national rate, and its urban / rural rates
    oof["national_rate"], oof["urban_rural_rate"] = 0.0, 0.0
    for tr, te in folds:
        t = c.iloc[tr]
        oof.loc[oof.index[te], "national_rate"] = t.w_yes.sum() / t.w_all.sum()
        rates = t.groupby("setting").apply(lambda g: g.w_yes.sum() / g.w_all.sum(), include_groups=False)
        oof.loc[oof.index[te], "urban_rural_rate"] = c.iloc[te].setting.map(rates).to_numpy()
    # ceiling: a neighbourhood model can at best predict each cluster's own observed share
    oof["oracle_cluster_share"] = c.share.clip(0, 1).to_numpy()
    rows = []
    for name in ["national_rate", "urban_rural_rate", *models(), "oracle_cluster_share"]:
        p2 = np.r_[oof[name], oof[name]]
        for scope, m in [("all", np.ones(n, bool)), ("urban", (c.setting == "urban").to_numpy()),
                         ("rural", (c.setting == "rural").to_numpy())]:
            m2 = np.r_[m, m] & (w > 0)  # a cluster where every household is connected has an empty 'no' row
            err = oof[name][m] - c.share[m]
            rows.append({"model": name, "scope": scope, "clusters": int(m.sum()),
                         "auc": roc_auc_score(y[m2], p2[m2], sample_weight=w[m2]),
                         "brier": brier_score_loss(y[m2], p2[m2], sample_weight=w[m2]),
                         "log_loss": log_loss(y[m2], np.clip(p2[m2], 1e-6, 1 - 1e-6), sample_weight=w[m2]),
                         "cluster_mae": float(np.average(np.abs(err), weights=c.w_all[m])),
                         "cluster_r2": 1 - float(np.average(err ** 2, weights=c.w_all[m])
                                                 / np.average((c.share[m] - np.average(c.share[m], weights=c.w_all[m]))
                                                              ** 2, weights=c.w_all[m]))})
    return oof, pd.DataFrame(rows)


def permutation_importance(model, c: pd.DataFrame, repeats: int = 5) -> pd.Series:
    """Rise in household-weighted log loss when one feature is shuffled across clusters (fitted model, in sample)."""
    X, y, w, _ = binomial_rows(c)  # noqa: N806
    base = log_loss(y, model.predict_proba(X)[:, 1], sample_weight=w)
    rng = np.random.default_rng(SEED)
    out = {}
    n = len(c)
    for j, f in enumerate(FEATURES):
        d = []
        for _ in range(repeats):
            Xp = X.copy()  # noqa: N806
            perm = rng.permutation(n)
            Xp[:n, j] = X[:n, j][perm]
            Xp[n:, j] = X[n:, j][perm]
            d.append(log_loss(y, model.predict_proba(Xp)[:, 1], sample_weight=w) - base)
        out[f] = float(np.mean(d))
    return pd.Series(out).sort_values(ascending=False)


# ------------------------------------------------------------------------------------------------ predictions
def predict_buildings(con, model) -> pd.DataFrame:
    """P(connected) per H3 r8 cell and setting, joined to every building."""
    I = INTERIM_DIR  # noqa: E741, N806
    map_cfg = load_yaml("profile_mapping.yaml")
    cells = con.sql(f"""SELECT DISTINCT h3_cell_to_parent(b.h3_r9, 8) AS h3_r8, {setting_sql(map_cfg)} AS setting
                        FROM '{I}/buildings_base.parquet' b JOIN '{I}/building_context.parquet' x USING (bldg_id)
                        """).df()
    cells["key"] = cells.h3_r8 + ":" + cells.setting
    f = ring_features(con, cells)
    f["p_connected"] = model.predict_proba(f[FEATURES].to_numpy(float))[:, 1].astype("float32")
    con.register("pc", f[["h3_r8", "setting", "p_connected"]])
    return con.sql(f"""SELECT b.bldg_id, x.h3_r7, x.adm2_name AS district, pc.setting, pc.p_connected
        FROM '{I}/buildings_base.parquet' b JOIN '{I}/building_context.parquet' x USING (bldg_id)
        JOIN pc ON pc.h3_r8 = h3_cell_to_parent(b.h3_r9, 8) AND pc.setting = {setting_sql(map_cfg)}""").df()


def district_check(pred: pd.DataFrame) -> pd.DataFrame:
    """Predicted share by district (buildings that can be homes: >= 20 m2, not tiny) against EICV7 grid lighting."""
    anchor = pd.read_csv(PROCESSED_DIR / "validation_anchors" / "grid_lighting_by_district_2024.csv")
    d = pred.groupby("district").p_connected.mean().rename("predicted").reset_index()
    return anchor[["district", "grid_main_lighting_share"]].merge(d, on="district", how="inner")


def main() -> None:
    con = connect()
    r8_sums(con)
    c = clusters(con)
    DERIVED.mkdir(parents=True, exist_ok=True)
    c.to_parquet(DERIVED / "grid_clusters_2025.parquet", index=False)  # restricted: never committed or published
    oof, scores = evaluate(c)
    X, y, w, _ = binomial_rows(c)  # noqa: N806
    allsc = scores[(scores.scope == "all") & scores.model.isin(list(models()))]
    best = allsc.sort_values("log_loss").model.iloc[0]  # chosen on held-out districts, not in sample
    logit = models()["logistic"].fit(X, y, logisticregression__sample_weight=w)
    final = logit if best == "logistic" else models()[best].fit(X, y, sample_weight=w)
    imp = permutation_importance(final, c)
    coefs = pd.Series(logit[-1].coef_[0], index=FEATURES).sort_values()
    pred = predict_buildings(con, final)
    # buildings likely to be homes for the district comparison (no labels nationally): >= 20 m2
    dist = district_check(pred.merge(con.sql(f"SELECT bldg_id FROM '{INTERIM_DIR}/buildings_base.parquet' "
                                             "WHERE area_m2 >= 20").df(), on="bldg_id"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_DIR / "model.pkl", "wb") as fh:
        pickle.dump({"model": final, "name": best, "features": FEATURES, "ring": RING}, fh)
    pred.to_parquet(PROCESSED_DIR / "grid_connection.parquet", index=False)
    # published: scores, out-of-fold predictions WITHOUT coordinates or cluster ids, importance, district check
    oof.drop(columns="cluster").sample(frac=1, random_state=SEED).to_parquet(OUT_DIR / "cv_predictions.parquet",
                                                                              index=False)
    scores.round(4).to_csv(OUT_DIR / "cv_scores.csv", index=False)
    dist.round(3).to_csv(OUT_DIR / "district_check.csv", index=False)
    r = np.corrcoef(dist.grid_main_lighting_share, dist.predicted)[0, 1]
    summary = {"model": best, "clusters": len(c), "households": int(c.n_hh.sum()), "features": FEATURES, "ring_k": RING,
               "national_share_weighted": round(float(c.w_yes.sum() / c.w_all.sum()), 3),
               "importance_log_loss": imp.round(4).to_dict(), "logistic_coefficients_std": coefs.round(3).to_dict(),
               "district_check": {"pearson_r": round(float(r), 3),
                                  "mae": round(float((dist.predicted - dist.grid_main_lighting_share).abs().mean()),
                                               3)},
               "buildings_predicted": len(pred)}
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=1))
    from rtl.demand.grid_figures import figures, report

    figures(c, oof, scores, imp, pred, dist, best)
    report(summary, scores, dist)
    print(scores[scores.scope == "all"].round(3).to_string(index=False))
    print(f"district check vs EICV7 2024 grid lighting: r = {r:.2f}; predictions for {len(pred):,} buildings")


if __name__ == "__main__":
    main()
