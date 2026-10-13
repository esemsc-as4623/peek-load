# %% [markdown]
# # 04 · Figures and tables for the status report
#
# `pixi run status-figures` writes:
# - `reports/figures/A1_height_by_district.png`: building height distribution per district
# - `reports/figures/A4_labels_by_district.png`: per district, how many buildings carry a usable tag or an LLM label,
#   and in the deep dives the class mix of tagged vs LLM-only buildings
# - `reports/status/district_table.md`: one row per district (footprint sources, heights, tags, labels)

# %%
import duckdb
import matplotlib.pyplot as plt
import numpy as np

from rtl import viz
from rtl.demand.mapping import LABELER
from rtl.join.vector import INFORMATIVE
from rtl.settings import INTERIM_DIR, PROCESSED_DIR, REPORTS_DIR

I, P, FIG = INTERIM_DIR, PROCESSED_DIR, REPORTS_DIR / "figures"  # noqa: E741
viz.use()
con = duckdb.connect()

# %% one row per building with what the report needs
con.sql(f"""CREATE TEMP TABLE t AS
    SELECT b.bldg_id, b.source, x.adm2_name AS district, x.adm3_name AS sector, h.height_m,
           {INFORMATIVE} AS tagged, l.label
    FROM '{I}/buildings_base.parquet' b JOIN '{I}/building_context.parquet' x USING (bldg_id)
    LEFT JOIN '{I}/building_height.parquet' h USING (bldg_id)
    LEFT JOIN (SELECT bldg_id, label FROM '{P}/labels.parquet' WHERE labeler = '{LABELER}') l USING (bldg_id)
    WHERE x.adm2_name IS NOT NULL""")

# %% district table
d = con.sql("""SELECT district, count(*) AS buildings,
        avg((source = 'google')::INT) AS google, avg((source = 'microsoft')::INT) AS microsoft,
        avg((source = 'osm')::INT) AS osm, avg((height_m IS NOT NULL)::INT) AS height_cov,
        median(height_m) AS height_median, quantile_cont(height_m, 0.9) AS height_p90,
        avg(tagged::INT) AS tagged, count(label) AS llm_labelled
    FROM t GROUP BY 1 ORDER BY buildings DESC""").df()
pct = lambda v: f"{100 * v:.1f}%"  # noqa: E731
lines = ["| District | Buildings | Google / Microsoft / OSM | With height | Median (p90) height | With usable tag | "
         "LLM-labelled |", "|---|---|---|---|---|---|---|"]
for r in d.itertuples():
    lines.append(f"| {r.district} | {r.buildings:,} | {pct(r.google)} / {pct(r.microsoft)} / {pct(r.osm)} | "
                 f"{pct(r.height_cov)} | {r.height_median:.1f} m ({r.height_p90:.1f} m) | {pct(r.tagged)} | "
                 f"{r.llm_labelled:,} |")
tot = con.sql("""SELECT count(*), avg((source='google')::INT), avg((source='microsoft')::INT), avg((source='osm')::INT),
        avg((height_m IS NOT NULL)::INT), median(height_m), quantile_cont(height_m, 0.9), avg(tagged::INT), count(label)
        FROM t""").fetchone()
lines.append(f"| **Rwanda** | **{tot[0]:,}** | {pct(tot[1])} / {pct(tot[2])} / {pct(tot[3])} | {pct(tot[4])} | "
             f"{tot[5]:.1f} m ({tot[6]:.1f} m) | {pct(tot[7])} | {tot[8]:,} |")
(REPORTS_DIR / "status").mkdir(exist_ok=True)
(REPORTS_DIR / "status" / "district_table.md").write_text("\n".join(lines) + "\n")
print("\n".join(lines[:4]), "...", lines[-1], sep="\n")

# %% heights by district (box = p25-p75, whiskers p5-p95; no outliers drawn)
h = con.sql("SELECT district, height_m FROM t WHERE height_m IS NOT NULL").df()
order = h.groupby("district").height_m.median().sort_values().index
fig, ax = plt.subplots(figsize=(13, 5.2))
data = [h.height_m[h.district == k].to_numpy() for k in order]
bp = ax.boxplot(data, whis=(5, 95), showfliers=False, patch_artist=True, widths=0.6,
                medianprops={"color": viz.INK, "linewidth": 1.6})
for b in bp["boxes"]:
    b.set(facecolor="#9ec5f4", edgecolor=viz.INK_2)
cov = d.set_index("district").height_cov.reindex(order)
ax.set_xticks(range(1, len(order) + 1), order, rotation=60, ha="right", fontsize=9)
ax.set_ylim(0, 10)
ax.set_ylabel("Building height (m)")
ax.axhline(6.0, color=viz.CAT[1], lw=1, ls="--")
ax.axhline(8.5, color=viz.CAT[1], lw=1, ls=":")
ax.text(0.8, 6.15, "2 floors from 6.0 m", color=viz.CAT[1], fontsize=8, va="bottom")
ax.text(0.8, 8.65, "3 floors from 8.5 m", color=viz.CAT[1], fontsize=8, va="bottom")
for i, c in enumerate(cov, start=1):
    ax.text(i, 9.6, f"{100 * c:.0f}%", ha="center", fontsize=7, color=viz.INK_2)
ax.text(0.2, 9.6, "with height:", ha="right", fontsize=7, color=viz.INK_2)
ax.set_title("Building heights by district (Google Open Buildings 2.5D Temporal, 2023)")
fig.text(0.0, -0.17, "Boxes: 25th-75th percentile, whiskers 5th-95th, line = median; districts sorted by median. "
         "Dashed lines: the fitted floor cut-points. Heights are compressed (single storeys read ~4-5 m).",
         ha="left", va="top", fontsize=8, color=viz.INK_2)
fig.savefig(FIG / "A1_height_by_district.png")
plt.close(fig)

# %% labels: coverage per district, and class mix in the deep dives (tagged vs LLM-only)
classes = ["residential", "mixed_shop_house", "commercial", "institutional", "religious", "productive_use",
           "industrial_warehouse", "ancillary", "unknown"]
colours = dict(zip(classes, ["#2a78d6", "#4a3aa7", "#eb6834", "#1baf7a", "#e87ba4", "#eda100", "#52514e",
                             "#c9c8c3", "#e4e3df"], strict=True))
cov = con.sql("""SELECT district, avg(tagged::INT) AS tagged, avg((label IS NOT NULL AND NOT tagged)::INT) AS llm_only,
        avg((label IS NOT NULL AND tagged)::INT) AS both FROM t GROUP BY 1""").df().set_index("district")
cov = cov.sort_values("tagged")
mix = con.sql("""SELECT district || ' · ' || sector AS area, tagged, label, count(*) AS n FROM t
        WHERE label IS NOT NULL AND sector IN ('Nyamirambo', 'Muhoza', 'Nasho') GROUP BY ALL""").df()
fig, axs = plt.subplots(1, 2, figsize=(16, 7.5), gridspec_kw={"width_ratios": [1, 1.25]})
fig.subplots_adjust(wspace=0.35, top=0.88)
ax = axs[0]
y = np.arange(len(cov))
ax.barh(y, 100 * cov.tagged, color=viz.CAT[2], label="usable OSM / Overture / facility tag")
ax.barh(y, 100 * cov.llm_only, left=100 * cov.tagged, color=viz.CAT[0], label="LLM label only (no usable tag)")
ax.set_yticks(y, cov.index, fontsize=8)
ax.set_xscale("symlog", linthresh=1)
ax.set_xlim(0, 100)
ax.set_xticks([0, 0.5, 1, 2, 5, 10, 20, 50, 100], ["0", "0.5", "1", "2", "5", "10", "20", "50", "100"])
ax.set_xlabel("% of the district's buildings (symlog scale)")
ax.set_title("Where use information comes from")
ax.legend(loc="lower right", fontsize=8)
ax = axs[1]
rows = []
for area, g in mix.groupby("area"):
    for tg, name in ((True, "tagged"), (False, "LLM only")):
        s = g[g.tagged == tg].set_index("label").n.reindex(classes, fill_value=0)
        rows.append((f"{area}\n{name} (n={int(s.sum()):,})", s / max(s.sum(), 1)))
for i, (_, s) in enumerate(rows):
    left = 0
    for c in classes:
        ax.barh(i, 100 * s[c], left=left, color=colours[c], label=c.replace("_", " ") if i == 0 else None)
        left += 100 * s[c]
ax.set_yticks(range(len(rows)), [r[0] for r in rows], fontsize=8)
ax.invert_yaxis()
ax.set_xlim(0, 100)
ax.set_xlabel("% of labelled buildings (top class)")
ax.set_title("Deep dives: class mix, tagged vs LLM-only buildings")
ax.legend(ncol=3, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.1))
fig.suptitle("Building use: tags are rare, so the LLM labels nearly every building it sees", x=0.01, ha="left",
             fontweight="bold")
viz.caption(fig, "LLM labels (Claude Haiku 4.5, prompt v2) cover the three deep-dive sectors and 400 gold buildings; "
                 "elsewhere use comes from tags only.")
fig.savefig(FIG / "A4_labels_by_district.png")
plt.close(fig)
print(mix.groupby(["area", "tagged"]).n.sum())
