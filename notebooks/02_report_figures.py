# ---
# jupyter:
#   jupytext:
#     formats: py:percent
# ---

# %% [markdown]
# # 02 · Report figures (heights and floors, growth, tag coverage, climate maps)
#
# Rebuilds the figures the status report and datasheet rely on from the current tables, with one shared style and
# **explicit axis and colour limits** chosen from the data's spread (outliers are clamped and flagged, never
# allowed to stretch the scale). Run after `pixi run derive` / `climate-h3` so the figures match the tables.

# %%
import json

import duckdb
import geopandas as gpd
import h3
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import shapely

from rtl import viz
from rtl.conform.height import first_seen_year
from rtl.settings import INTERIM_DIR, REPORTS_DIR

viz.use()
FIG = REPORTS_DIR / "figures"
derive = json.loads((REPORTS_DIR / "qa" / "derive.json").read_text())

# %% [markdown]
# ## Floors from height: OSM floor counts against 2.5D height, with the fitted cut-points

# %%
from rtl.conform.height_report import osm_validation  # noqa: E402

con = duckdb.connect()
con.sql("LOAD spatial;")
_, m = osm_validation(con)
m = m.dropna(subset=["osm_levels", "height_m"])
m["lvl"] = m.osm_levels.round().clip(1, 7).astype(int)
tc, sc = derive["threshold_calibration"], derive["storey_calibration"]

fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.2), gridspec_kw={"width_ratios": [1.6, 1]})
groups = [m.loc[m.lvl == k, "height_m"].to_numpy() for k in range(1, 8)]
bp = a1.boxplot(groups, positions=range(1, 8), widths=0.55, showfliers=False, patch_artist=True,
                medianprops={"color": viz.INK, "lw": 1.5}, whiskerprops={"color": viz.INK_2},
                capprops={"color": viz.INK_2})
for b in bp["boxes"]:
    b.set(facecolor="#9ec5f4", edgecolor=viz.CAT[0])
for y, txt in [(tc["t12_m"], f"1|2 floors cut-point {tc['t12_m']:.1f} m"),
               (tc["t23_m"], f"2|3 floors cut-point {tc['t23_m']:.1f} m")]:
    a1.axhline(y, color=viz.CAT[1], lw=1.5, ls="--", zorder=1)
    a1.text(0.55, y + 0.35, txt, ha="left", fontsize=8, color=viz.INK_2,
            bbox={"facecolor": viz.SURFACE, "edgecolor": "none", "pad": 1})
a1.set_xticks(range(1, 8), [f"{k}{'+' if k == 7 else ''}\nn={len(g):,}" for k, g in zip(range(1, 8), groups,
                                                                                    strict=True)])
a1.set_ylim(0, 30)
above = int((m.height_m > 30).sum())
a1.set_xlabel("OSM building:levels")
a1.set_ylabel("2.5D height 2023, footprint median (m)")
a1.set_title("Height per OSM floor count (boxes: quartiles; whiskers: 1.5 IQR)")

lv = ["1", "2", "3"]
x = np.arange(3)
a2.bar(x - 0.18, [sc["test_mae_by_levels"][k] for k in lv], 0.36, color=viz.CAT[2],
       label=f"one storey height ({sc['storey_m']} m)")
a2.bar(x + 0.18, [tc["test_mae_by_levels"][k] for k in lv], 0.36, color=viz.CAT[0],
       label="fitted cut-points (used)")
for xi, k in enumerate(lv):
    a2.text(xi + 0.18, tc["test_mae_by_levels"][k] + 0.05, f"{tc['test_mae_by_levels'][k]:.2f}", ha="center",
            fontsize=8)
a2.set_xticks(x, ["1 storey", "2 storeys", "3+ storeys"])
a2.set_ylim(0, 2.6)
a2.set_ylabel("mean absolute error (floors)")
a2.set_title(f"Held-out OSM check (n={tc['n_test']:,})")
a2.legend(fontsize=8, loc="upper left")
viz.caption(fig, "Google Open Buildings 2.5D Temporal (2023) vs OpenStreetMap building:levels. Cut-points fitted on "
                 "even OSM ids, errors measured on odd ids. 3+ storeys stay under-estimated: the 2.5D heights compress "
                 f"tall buildings. Height axis capped at 30 m ({above} buildings above).")
fig.tight_layout()
fig.savefig(FIG / "A1_height_vs_osm.png")

# %% [markdown]
# ## Growth: buildings by first year stably detected (experimental)

# %%
h = pd.read_parquet(INTERIM_DIR / "building_height.parquet", columns=[f"presence_{y}" for y in range(2016, 2024)])
years = list(range(2016, 2024))
P = h.to_numpy()
counts = {t: first_seen_year(P, years, t).value_counts().reindex(years, fill_value=0) / 1e3 for t in (0.3, 0.5, 0.7)}
used = derive["presence_threshold"]
fig, ax = plt.subplots(figsize=(8.5, 4))
xs = np.arange(len(years))
ax.bar(xs[1:], counts[used].to_numpy()[1:], 0.6, color=viz.CAT[0], label=f"threshold {used} (used)")
ax.bar(xs[0], counts[used].iloc[0], 0.6, color="#9ec5f4", hatch="//", edgecolor=viz.CAT[0],
       label="2016 = present by 2016 (not a build year)")
for t, c, mk in ((0.3, viz.CAT[1], "o"), (0.7, viz.CAT[2], "s")):
    ax.plot(xs[1:], counts[t].to_numpy()[1:], color=c, marker=mk, ms=6, label=f"threshold {t}")
ax.set_xticks(xs, [str(y) for y in years])
top = max(counts[0.3].iloc[1:].max(), counts[used].iloc[1:].max())
ax.set_ylim(0, top * 1.25)
if counts[used].iloc[0] > top * 1.25:
    ax.text(xs[0], top * 1.2, f"{counts[used].iloc[0]:,.0f}k ↑", ha="center", va="top", fontsize=8,
            color=viz.INK_2)
ax.set_ylabel("buildings (thousands)")
ax.set_title("Buildings by first year stably detected (experimental)")
ax.legend(fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=4)
viz.caption(fig, "Google Open Buildings 2.5D Temporal presence at each footprint centroid; first year ≥ threshold that "
                 "stays ≥ threshold. The jumps in 2019 and 2023 are source artefacts.")
fig.tight_layout()
fig.savefig(FIG / "A1_growth.png")

# %% [markdown]
# ## Where do use tags exist? Weak-label coverage by district (bias audit)

# %%
cov = duckdb.sql(f"""
    SELECT adm2_name AS district, count(*) AS n,
           avg((osm_amenity IS NOT NULL OR osm_shop IS NOT NULL OR coalesce(osm_building, 'yes') <> 'yes'
                OR overture_category IS NOT NULL OR facility_type IS NOT NULL)::int) AS tagged
    FROM read_parquet('{INTERIM_DIR / "building_context.parquet"}') GROUP BY 1 ORDER BY tagged""").df()
cov["district"] = cov.district.fillna("(no district)")  # footprints outside the admin polygons (border slivers)
national = float((cov.tagged * cov.n).sum() / cov.n.sum())
fig, ax = plt.subplots(figsize=(7.5, 7.5))
ax.barh(cov.district, cov.tagged * 100, color=viz.CAT[0], height=0.7)
ax.axvline(national * 100, color=viz.CAT[1], lw=1.5, ls="--", zorder=0)
ax.text(national * 100, -1.2, f" national {national:.2%}", color=viz.INK_2, fontsize=8, va="center")
for i, (v, n) in enumerate(zip(cov.tagged, cov.n, strict=True)):
    ax.text(v * 100 + 0.05, i, f"{v:.2%} of {n / 1e3:,.0f}k", va="center", fontsize=7, color=viz.INK_2,
            bbox={"facecolor": viz.SURFACE, "edgecolor": "none", "pad": 0.5})
ax.set_xlim(0, cov.tagged.max() * 100 * 1.35)
ax.set_ylim(-1.8, len(cov) - 0.4)
ax.set_xlabel("% of buildings with an informative tag (OSM use / Overture place / facility record)")
ax.set_title("Use tags are rare everywhere and concentrated in Kigali")
ax.tick_params(axis="y", labelsize=8)
viz.caption(fig, "OpenStreetMap, Overture places, healthsites/OSM facilities matched to footprints; "
                 "plain building=yes does not count.")
fig.tight_layout()
fig.savefig(FIG / "A2v_match_coverage.png")

# %% [markdown]
# ## Climate maps: mean temperature, CDD22, cooling class

# %%
clim = pd.read_parquet(INTERIM_DIR / "climate_h3.parquet")
gdf = gpd.GeoDataFrame(clim, geometry=[shapely.Polygon([(lo, la) for la, lo in h3.cell_to_boundary(c)])
                                       for c in clim.h3_r7], crs="EPSG:4326")
lo, hi = np.floor(clim.t2m_mean_c.quantile(0.01)), np.ceil(clim.t2m_mean_c.quantile(0.99))
fig, ax = plt.subplots(figsize=(7, 5.2))
viz.hex_map(ax, gdf, "t2m_mean_c", cmap=viz.SEQ_ORANGE, vmin=lo, vmax=hi, extend="both",
            label="mean 2 m temperature (°C)")
ax.set_title(f"Mean temperature 2019–2024 ({len(gdf):,} areas with buildings)")
viz.caption(fig, f"ERA5-Land hourly, downscaled with elevation (−6.05 K/km). Colours span the 1st–99th "
                 f"percentile ({lo:.0f}–{hi:.0f} °C); colder volcano and hotter valley cells are clamped.")
fig.savefig(FIG / "A3_t2m_mean_c.png")

fig, ax = plt.subplots(figsize=(7, 5.2))
viz.hex_map(ax, gdf, "cdd22_per_year", cmap=viz.SEQ_ORANGE, vmin=0, vmax=200, extend="max",
            label="cooling degree-days above 22 °C (K·day per year)")
ax.set_title("Sustained heat: cooling degree-days above 22 °C")
viz.caption(fig, f"Scale capped at 200; {(clim.cdd22_per_year > 200).sum()} cells exceed it: the eastern lowlands "
                 f"(Ngoma, Kirehe, Kayonza, Bugesera) and the Rusizi/Bugarama valley (up to "
                 f"{clim.cdd22_per_year.max():.0f}). Kigali ≈ 28. NASA POWER confirms Kigali and Bugarama but "
                 "shows less heat at Nasho (east): eastern values are provisional until the Copernicus rebuild.")
fig.savefig(FIG / "A3_cdd22_per_year.png")

order = ["low", "medium", "high"]
colours = {"low": "#f9c3a3", "medium": "#eb6834", "high": "#6b270c"}  # ordinal steps of the heat ramp
fig, ax = plt.subplots(figsize=(7, 5.2))
for cls in order:
    gdf[gdf.cooling_class == cls].plot(ax=ax, color=colours[cls], linewidth=0)
cnt = clim.cooling_class.value_counts()
ax.legend(handles=[plt.matplotlib.patches.Patch(color=colours[c], label=f"{c} ({cnt.get(c, 0):,} areas)")
                   for c in order], title="cooling class", loc="lower right", fontsize=8)
ax.set_aspect("equal")
ax.set_axis_off()
ax.set_title("Cooling class from the 95th-percentile daily maximum")
viz.caption(fig, "low < 26 °C, medium 26–29 °C, high ≥ 29 °C (config/params.yaml): a relative 'fans plausible' "
                 "proxy. Hot zones: the eastern lowlands and the Rusizi/Bugarama valley; Kigali is marginal.")
fig.savefig(FIG / "A3_cooling_class.png")
print("figures written")
