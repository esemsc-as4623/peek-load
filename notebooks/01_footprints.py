# ---
# jupyter:
#   jupytext:
#     formats: py:percent
# ---

# %% [markdown]
# # 01 · What's in 6.4 million footprints?
#
# Before anything is joined or labelled, look at the raw material: where the footprints come from, how
# confident the detector was, how big the buildings are, and which ones the QA step flagged.
# Everything is aggregated in DuckDB; geometries never load into memory.

# %%
from pathlib import Path

import duckdb
import matplotlib.pyplot as plt
import numpy as np

from rtl import viz
from rtl.conform.buildings import OUT as BASE
from rtl.settings import REPORTS_DIR

viz.use()
FIG = REPORTS_DIR / "figures"
FIG.mkdir(parents=True, exist_ok=True)
con = duckdb.connect()
con.sql("LOAD spatial; INSTALL h3 FROM community; LOAD h3; SET threads=2;")
B = f"read_parquet('{BASE}')"

# %% [markdown]
# ## Source mix and size
# VIDA merges Google V3 (ML from imagery), Microsoft (ML) and OSM (human-mapped).

# %%
src = con.sql(f"""
    SELECT source, count(*) AS n, median(area_m2) AS med_area, avg(source_confidence) AS mean_conf
    FROM {B} GROUP BY 1 ORDER BY n DESC""").df()
print(src.to_string(index=False))

# %%
edges = np.logspace(0, 4, 61)  # 1 m² → 10,000 m²
hist = con.sql(f"""
    SELECT source, least(60, floor(log10(area_m2) * 15)::INT + 1) AS b, count(*) AS n  -- 15 bins per decade
    FROM {B} WHERE area_m2 >= 1 GROUP BY 1, 2""").df()
fig, ax = plt.subplots(figsize=(7, 3.6))
for s in ["google", "microsoft", "osm"]:
    h = hist[hist.source == s].set_index("b").n.reindex(range(1, 61), fill_value=0)
    share = h / h.sum()
    ax.step(edges[:-1], share.values, where="post", color=viz.SOURCE_COLOURS[s], label=s)
ax.set_xscale("log")
ax.set_xlabel("footprint area (m², log scale)")
ax.set_ylabel("share of footprints")
ax.axvline(6, color=viz.INK_2, lw=1, ls=":")
ax.text(6.3, ax.get_ylim()[1] * 0.92, "tiny flag < 6 m²", fontsize=8, color=viz.INK_2)
ax.legend(title="source")
ax.set_title("Footprint size by source")
viz.caption(fig, "Source: VIDA Google–Microsoft–OSM Open Buildings (RWA), conformed. Areas in UTM 35S.")
fig.savefig(FIG / "eda01_area_by_source.png")

# %% [markdown]
# Note the hard floor in Microsoft's footprints at ~15 m²: Microsoft drops small detections, so where
# Microsoft fills gaps, latrines, kitchens and small kiosks are missing. OSM (hand-mapped) is the opposite,
# rich in small structures. Source mix is therefore a bias in the `ancillary` class, not just noise.

# %% [markdown]
# ## Detector confidence (Google only)
# Google V3 publishes a per-footprint confidence ≥ 0.65. Below 0.70 we flag `low_confidence`:
# these footprints stay in the table, but downstream sampling can exclude them.

# %%
conf = con.sql(f"""
    SELECT floor(source_confidence * 100) / 100 AS c, count(*) AS n
    FROM {B} WHERE source = 'google' GROUP BY 1 ORDER BY 1""").df()
fig, ax = plt.subplots(figsize=(7, 3.2))
colors = [viz.CAT[1] if c < 0.70 else viz.CAT[0] for c in conf.c]
ax.bar(conf.c, conf.n / 1e3, width=0.0085, color=colors)
ax.set_xlabel("Google detection confidence")
ax.set_ylabel("footprints (thousands)")
ax.set_title("12.6% of footprints fall below the 0.70 confidence flag")
viz.caption(fig, "Orange: flagged low_confidence (< 0.70). Source: Google Open Buildings V3 via VIDA.")
fig.savefig(FIG / "eda01_google_confidence.png")

# %% [markdown]
# ## Where are the buildings?
# Footprint density per H3 resolution-7 cell (~5.2 km²). Rwanda is one of Africa's most densely populated
# countries, and rural settlement is dispersed rather than clustered, which shows up as density almost everywhere.

# %%
dens = con.sql(f"""
    SELECT h7, h3_cell_to_lat(h7) AS lat, h3_cell_to_lng(h7) AS lon, n
    FROM (SELECT h3_cell_to_parent(h3_r9, 7) AS h7, count(*) AS n FROM {B} GROUP BY 1)""").df()
area_km2 = 5.16  # average H3 r7 cell area
fig, ax = plt.subplots(figsize=(7, 5.0))
sc = ax.scatter(dens.lon, dens.lat, c=np.log10(dens.n / area_km2), s=4, marker="h",
                cmap=viz.SEQ_BLUE, linewidths=0)
cb = fig.colorbar(sc, ax=ax, shrink=0.7, label="footprints per km² (log10)")
ax.set_aspect("equal")
ax.set_xlabel("longitude")
ax.set_ylabel("latitude")
ax.set_title("Building density, H3 res-7 cells")
viz.caption(fig, f"{len(dens):,} cells with ≥1 footprint. Source: VIDA Open Buildings (RWA).")
fig.savefig(FIG / "eda01_density_h3r7.png")

# %% [markdown]
# ## QA flags

# %%
flags = con.sql(f"""
    SELECT flag, count(*) AS n FROM (SELECT unnest(string_split(qa_flags, ',')) AS flag FROM {B}
                                     WHERE qa_flags <> '') GROUP BY 1 ORDER BY 2 DESC""").df()
total = con.sql(f"SELECT count(*) FROM {B}").fetchone()[0]
flags["share"] = (flags.n / total).map("{:.2%}".format)
print(flags.to_string(index=False))
clean = con.sql(f"SELECT count(*) FROM {B} WHERE qa_flags = ''").fetchone()[0]
print(f"unflagged: {clean:,} of {total:,} ({clean / total:.1%})")

# %%
Path(FIG / "eda01_summary.txt").write_text(
    src.to_string(index=False) + "\n\n" + flags.to_string(index=False)
    + f"\n\nunflagged: {clean:,} of {total:,} ({clean / total:.1%})\n")
