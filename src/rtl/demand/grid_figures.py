"""B4 figures and report for the grid-connection model (called by rtl.demand.grid_connection).

No figure shows a DHS cluster location: the label is shown as distributions, district aggregates and binned
relationships; maps show only open features and model predictions.
"""

from __future__ import annotations

import duckdb
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LogNorm, Normalize
from shapely import wkt

from rtl import viz
from rtl.demand.grid_connection import LABELS
from rtl.settings import INTERIM_DIR, REPORTS_DIR

FIG = REPORTS_DIR / "figures"
SETTING_COLOURS = {"urban": viz.CAT[0], "rural": viz.CAT[1]}


def hexes(values: pd.DataFrame) -> gpd.GeoDataFrame:
    """H3 r7 polygons for a frame with an h3_r7 column."""
    con = duckdb.connect()
    con.sql("LOAD h3")
    con.register("v", values)
    g = con.sql("SELECT v.*, h3_cell_to_boundary_wkt(h3_r7) AS w FROM v").df()
    return gpd.GeoDataFrame(g.drop(columns="w"), geometry=g.w.map(wkt.loads), crs="EPSG:4326")


def raw_feature_maps() -> None:
    I = INTERIM_DIR  # noqa: E741, N806
    con = duckdb.connect()
    con.sql("INSTALL h3 FROM community; LOAD h3")
    v = con.sql(f"""
        SELECT x.h3_r7, median(x.dist_powerline_m) / 1000 AS dist_mv_km, median(x.pop_density_per_km2) AS pop,
               avg(x.rwi) AS rwi, avg(c.cropland_share) AS cropland, count(*) / 5.16 AS bldg_km2,
               avg((b.area_m2 >= 100)::INT) AS large
        FROM '{I}/buildings_base.parquet' b JOIN '{I}/building_context.parquet' x USING (bldg_id)
        LEFT JOIN '{I}/cropland_h3r8.parquet' c ON c.h3_r8 = h3_cell_to_parent(b.h3_r9, 8)
        WHERE x.h3_r7 IS NOT NULL GROUP BY 1""").df()

    def lim(col: str, log: bool = False):
        lo, hi = v[col].quantile([0.02, 0.98])
        return LogNorm(max(lo, 1e-3), hi) if log else Normalize(lo, hi)

    g = hexes(v)
    panels = [("dist_mv_km", "Distance to predicted MV line (km, median)", lim("dist_mv_km", True), "viridis_r"),
              ("pop", "Population density (people/km², median)", lim("pop", True), "viridis"),
              ("rwi", "Relative wealth index (mean)", lim("rwi"), "viridis"),
              ("cropland", "Cropland share (WorldCover 2021)", lim("cropland"), "YlGn"),
              ("bldg_km2", "Buildings per km²", lim("bldg_km2", True), "viridis"),
              ("large", "Share of buildings ≥ 100 m²", lim("large"), "viridis")]
    fig, axs = plt.subplots(2, 3, figsize=(15, 8.2))
    fig.subplots_adjust(wspace=0.05, hspace=0.12, top=0.92)
    for ax, (col, title, norm, cmap) in zip(axs.flat, panels, strict=True):
        g.plot(column=col, ax=ax, cmap=cmap, norm=norm, linewidth=0, legend=True,
               legend_kwds={"shrink": 0.7, "extend": "both"}, missing_kwds={"color": viz.GRID})
        ax.set_title(title, fontsize=11)
        ax.set_axis_off()
    fig.suptitle("Raw features of the grid-connection model, per H3 r7 cell (all 6.4M buildings)",
                 x=0.01, ha="left", fontweight="bold")
    viz.caption(fig, "Sources: Gridfinder (Arderne et al. 2020), WorldPop 2023, Meta RWI, ESA WorldCover 2021, "
                     "Google Open Buildings 2.5D Temporal. Colour limits: 2nd-98th percentile of cells.")
    fig.savefig(FIG / "B4_grid_raw_features.png")
    plt.close(fig)


def label_figure(c: pd.DataFrame) -> None:
    fig, axs = plt.subplots(1, 3, figsize=(18, 5.6), gridspec_kw={"width_ratios": [1, 1.3, 1.1]})
    fig.subplots_adjust(wspace=0.32, top=0.84)
    ax = axs[0]
    bins = np.linspace(0, 1, 11)
    for s in ("urban", "rural"):
        m = c.setting == s
        ax.hist(c.share[m], bins=bins, alpha=0.75, color=SETTING_COLOURS[s],
                label=f"{s} ({m.sum()} clusters, {c.share[m].mul(c.w_all[m]).sum() / c.w_all[m].sum():.0%})")
    ax.set(title="Share connected per cluster", xlabel="Share of the cluster's households",
           ylabel="DHS clusters")
    ax.legend()
    ax = axs[1]
    d = c.groupby("district").apply(lambda g: pd.Series({"share": g.w_yes.sum() / g.w_all.sum(), "hh": g.n_hh.sum()}),
                                    include_groups=False)
    d.index = d.index.str.title()
    d = d[d.hh >= 10].sort_values("share")  # suppression rule: cells under 10 respondents are not shown
    from rtl.settings import PROCESSED_DIR

    anchor = pd.read_csv(PROCESSED_DIR / "validation_anchors" / "grid_lighting_by_district_2024.csv")
    a = anchor.set_index(anchor.district.str.title()).grid_main_lighting_share.reindex(d.index)
    y = np.arange(len(d))
    ax.barh(y, d.share, color=viz.CAT[0], alpha=0.8, label="DHS 2025: has electricity (weighted)")
    ax.scatter(a, y, color=viz.INK, s=14, zorder=3, label="EICV7 2024: grid main lighting")
    ax.set_yticks(y, d.index, fontsize=7)
    ax.set(title="By district (not a model input)", xlabel="Share of households", xlim=(0, 1))
    ax.legend(loc="lower right", fontsize=8)
    ax = axs[2]
    for s in ("urban", "rural"):
        m = c[c.setting == s]
        q = pd.qcut(m.log_dist_mv, 6 if s == "urban" else 10, duplicates="drop")
        b = m.groupby(q, observed=True).apply(lambda g: pd.Series({
            "x": np.expm1(g.log_dist_mv.mean()) / 1000, "share": g.w_yes.sum() / g.w_all.sum()}),
            include_groups=False)
        ax.plot(b.x, b.share, "o-", color=SETTING_COLOURS[s], label=s)
    ax.set_xscale("log")
    ax.set_xticks([0.1, 0.2, 0.5, 1, 2, 5, 10], ["0.1", "0.2", "0.5", "1", "2", "5", "10"])
    ax.minorticks_off()
    ax.set(title="Falls with distance to the MV line", xlabel="Distance to predicted MV line "
           "(km, ring mean; clusters binned)", ylabel="Share with electricity", ylim=(0, 1))
    ax.legend()
    fig.suptitle(f"Grid-connection label: DHS 2025, {int(c.n_hh.sum()):,} households in {len(c)} clusters",
                 x=0.01, ha="left", fontweight="bold")
    viz.caption(fig, "hv206 'has electricity' (solar-lantern households answer no). Cluster locations are restricted "
                     "and not shown; bins hold 15-60 clusters each.")
    fig.savefig(FIG / "B4_grid_label.png")
    plt.close(fig)


def model_figure(c: pd.DataFrame, oof: pd.DataFrame, scores: pd.DataFrame, imp: pd.Series, best: str) -> None:
    fig, axs = plt.subplots(1, 3, figsize=(19, 5.8), gridspec_kw={"width_ratios": [1, 1, 1.1]})
    fig.subplots_adjust(wspace=0.55, top=0.84)
    ax = axs[0]
    names = {"logistic": "Logistic regression", "gradient_boosting": "Gradient boosting",
             "urban_rural_rate": "Urban/rural rate only"}
    for i, (m, label) in enumerate(names.items()):
        q = pd.qcut(oof[m], 10, duplicates="drop")
        b = oof.groupby(q, observed=True).apply(lambda g, m=m: pd.Series({
            "p": np.average(g[m], weights=g.w_all), "o": np.average(g.share, weights=g.w_all)}), include_groups=False)
        s = scores[(scores.model == m) & (scores.scope == "all")].iloc[0]
        ax.plot(b.p, b.o, "o-", color=viz.CAT[i], label=f"{label}: AUC {s.auc:.2f}, Brier {s.brier:.3f}")
    ax.plot([0, 1], [0, 1], color=viz.INK_2, lw=1, ls="--")
    ax.set(title="Calibration (held-out districts)", xlabel="Predicted share (decile mean)",
           ylabel="Observed share", xlim=(0, 1), ylim=(0, 1))
    ax.legend(fontsize=8, loc="upper left")
    ax = axs[1]
    for s in ("urban", "rural"):
        m = oof.setting == s
        ax.scatter(oof[best][m], oof.share[m], s=10, alpha=0.5, color=SETTING_COLOURS[s], label=s)
    ax.plot([0, 1], [0, 1], color=viz.INK_2, lw=1, ls="--")
    r = scores[(scores.model == best) & (scores.scope == "all")].iloc[0]
    o = scores[(scores.model == "oracle_cluster_share") & (scores.scope == "all")].iloc[0]
    axs[0].text(0.98, 0.03, f"ceiling (each cluster's own share): AUC {o.auc:.2f}", transform=axs[0].transAxes,
                ha="right", fontsize=8, color=viz.INK_2)
    ax.set(title=f"Clusters: R² {r.cluster_r2:.2f}, MAE {r.cluster_mae:.2f} ({best.replace('_', ' ')})",
           xlabel="Predicted (held-out district)", ylabel="Observed share", xlim=(0, 1), ylim=(0, 1))
    ax.legend()
    ax = axs[2]
    top = imp.head(10)[::-1]
    ax.barh([LABELS[k] for k in top.index], top.values, color=viz.CAT[0])
    ax.set(title="Permutation importance", xlabel="Rise in log loss when shuffled")
    ax.tick_params(axis="y", labelsize=8)
    fig.suptitle("Grid-connection model: grouped cross-validation by district", x=0.01, ha="left",
                 fontweight="bold")
    viz.caption(fig, "Household-weighted scores. Baselines: national rate (AUC 0.5) and the urban/rural rate. "
                     "Distance to the MV line is constrained to lower the probability as it grows.")
    fig.savefig(FIG / "B4_grid_model.png")
    plt.close(fig)


def prediction_figure(pred: pd.DataFrame, dist: pd.DataFrame) -> None:
    v = pred.groupby("h3_r7").p_connected.mean().reset_index()
    g = hexes(v)
    fig, axs = plt.subplots(1, 2, figsize=(14, 6), gridspec_kw={"width_ratios": [1.3, 1]})
    g.plot(column="p_connected", ax=axs[0], cmap=viz.SEQ_BLUE, vmin=0, vmax=1, linewidth=0, legend=True,
           legend_kwds={"shrink": 0.7, "label": "P(connected), mean over buildings"})
    axs[0].set_axis_off()
    axs[0].set_title("Predicted probability of a grid connection, per H3 r7 cell")
    ax = axs[1]
    ax.scatter(dist.grid_main_lighting_share, dist.predicted, color=viz.CAT[0])
    notable = dist[(dist.predicted - dist.grid_main_lighting_share).abs().rank(ascending=False) <= 6
                   | (dist.grid_main_lighting_share >= 0.6)]
    for _, r in notable.iterrows():
        ax.annotate(r.district, (r.grid_main_lighting_share, r.predicted), fontsize=7, xytext=(3, 2),
                    textcoords="offset points", color=viz.INK_2)
    ax.plot([0, 1], [0, 1], color=viz.INK_2, lw=1, ls="--")
    r = np.corrcoef(dist.grid_main_lighting_share, dist.predicted)[0, 1]
    ax.set(xlim=(0, 1), ylim=(0, 1), xlabel="EICV7 2024: grid as main lighting (share of households)",
           ylabel="Model: mean P(connected), buildings ≥ 20 m²",
           title=f"Independent check by district (r = {r:.2f})")
    viz.caption(fig, "Labelled: the six largest differences and the most connected districts. Buildings include "
                     "non-homes (no national use labels), and the survey years differ "
                     "(DHS 2025 vs EICV7 2024): read the check for ranking, not exact levels.")
    fig.savefig(FIG / "B4_grid_predictions.png")
    plt.close(fig)


def figures(c, oof, scores, imp, pred, dist, best: str) -> None:
    viz.use()
    FIG.mkdir(parents=True, exist_ok=True)
    raw_feature_maps()
    label_figure(c)
    model_figure(c, oof, scores, imp, best)
    prediction_figure(pred, dist)


def report(summary: dict, scores: pd.DataFrame, dist: pd.DataFrame) -> None:
    def md(df: pd.DataFrame) -> str:
        cols = list(df.columns)
        lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        lines += ["| " + " | ".join(f"{v:.3f}" if isinstance(v, float) else str(v) for v in r) + " |"
                  for r in df.itertuples(index=False)]
        return "\n".join(lines)

    imp = pd.Series(summary["importance_log_loss"]).head(8)
    text = f"""# Grid-connection model (B4)

*Generated by `pixi run grid-model` (rtl.demand.grid_connection). Status: draft.*

**Question.** What is the probability that a household in a given place is connected to the grid?
**Label.** DHS 2025, `hv206` "has electricity", {summary['households']:,} households in {summary['clusters']} clusters
(weighted national share {summary['national_share_weighted']:.0%}). Cluster points are displaced (urban ≤ 2 km,
rural ≤ 5 km), so features are building-weighted means over H3 r8 rings of that size (k = {summary['ring_k']}), and
each building receives its neighbourhood's probability. The model cannot tell two neighbouring buildings apart.
**Features.** {", ".join(summary['features'])}. No DHS variable is an input.
**Model used for predictions:** {summary['model'].replace('_', ' ')} (lowest held-out log loss).

## Held-out performance (grouped 5-fold cross-validation by district)

{md(scores)}

AUC and Brier are household-weighted; cluster MAE and R² compare the predicted and observed share per cluster.
`oracle_cluster_share` uses each cluster's own observed share: the ceiling for any model that, like this one, can
only tell neighbourhoods apart (households in one cluster share their features).

## What drives it

Permutation importance (rise in log loss when a feature is shuffled):

{md(imp.rename("importance").reset_index().rename(columns={"index": "feature"}))}

## Independent check: EICV7 2024 grid lighting by district

Pearson r = {summary['district_check']['pearson_r']}, mean absolute difference {summary['district_check']['mae']}
(the model's mean over buildings ≥ 20 m², which include non-homes, so levels are not directly comparable).

{md(dist.sort_values("grid_main_lighting_share"))}

## Figures
- `reports/figures/B4_grid_raw_features.png`: the raw features, per H3 r7 cell
- `reports/figures/B4_grid_label.png`: the label, its district pattern and its link to distance to the MV line
- `reports/figures/B4_grid_model.png`: calibration, cluster fit and importance
- `reports/figures/B4_grid_predictions.png`: national predictions and the district check

## Limits
- Displacement blurs the label: building-level differences within ~2-5 km are not learnt.
- Distance to the MV line is Gridfinder's prediction, itself built from night lights and roads.
- "Has electricity" includes a few solar home systems and mini-grids; it is a connection proxy, not a meter.
- Predictions are for households; non-residential buildings get the same neighbourhood value.
"""
    (REPORTS_DIR / "demand" / "grid_model.md").write_text(text)
