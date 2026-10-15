# %% [markdown]
# # 05 · Building use: classifier and census allocation (mid-review figures)
#
# `pixi run use-figures` writes to `reports/figures/`:
# - `C1_use_model_scores.png`: how well models A (tags) and B (tags + LLM) rank each use, on three held-out tests
# - `C2_use_mix.png`: class mix in the deep dives and nationally: LLM labels, model A, model B, final allocation
# - `C3_allocation_maps.png`: every building's allocated use in a Nyamirambo and a Nasho sample box
# - `C4_households_fit.png`: homes per district, one-per-residential-building vs the 2022 census
# - `C5_allocation_sources.png`: where each building's use comes from (tag, census, model, remainder)
# - `C6_national_demand.png`: potential and expected annual demand per H3 r7 cell, for every building
# - `C7_demand_by_year.png`: national demand back-cast 2016-2023 and projected to 2030 (rtl.demand.years)
# - `C8_uncertainty.png`: allocation and profile uncertainty bands for the three deep-dive sectors (B6)
# - `C9_benchmarks.png`: the model against REG sales (households by year; categories in 2023)

# %%
import duckdb
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LogNorm
from matplotlib.patches import Patch
from shapely import wkt

from rtl import viz
from rtl.demand.mapping import LABELER
from rtl.settings import INTERIM_DIR, PROCESSED_DIR, REPORTS_DIR, load_yaml

I, P, FIG = INTERIM_DIR, PROCESSED_DIR, REPORTS_DIR / "figures"  # noqa: E741
viz.use()
con = duckdb.connect()
con.sql("INSTALL h3 FROM community; LOAD h3; LOAD spatial")
# validated categorical order (dataviz palette check passes in this order); ancillary = the neutral "no load" bucket
ORDER = ["residential", "commercial", "institutional", "productive_use", "religious", "industrial_warehouse",
         "mixed_shop_house", "ancillary"]
COL = dict(zip(ORDER, ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#c9c8c3"],
               strict=True))
NAME = {c: c.replace("_", " ").replace("industrial warehouse", "store / warehouse").replace(
    "mixed shop house", "shop-house") for c in ORDER}
DD = {"Nyamirambo": "Nyarugenge", "Muhoza": "Musanze", "Nasho": "Kirehe"}

# %% C1: ranking quality (one-vs-rest AUC) per class, A vs B, on three held-out tests
sc = pd.read_csv(P / "use_model" / "scores.csv")
tests = ["tags (held-out districts)", "LLM labels (held-out sector)", "human gold (uncertain)"]
classes = ["residential", "commercial", "institutional", "religious", "productive_use", "industrial_warehouse",
           "ancillary", "mixed_shop_house"]
fig, axs = plt.subplots(1, 3, figsize=(17, 5.2), sharey=True)
fig.subplots_adjust(wspace=0.08, top=0.82, bottom=0.3)
for ax, test in zip(axs, tests, strict=True):
    x = np.arange(len(classes))
    for k, (model, colour) in enumerate((("A tags", "#2a78d6"), ("B tags+llm", "#eb6834"))):
        row = sc[(sc.model == model) & (sc.test == test)].iloc[0]
        v = [row.get(f"auc_{c}", np.nan) for c in classes]
        ok = ~np.isnan(np.array(v, dtype=float))
        ax.bar((x + (k - 0.5) * 0.38)[ok], np.array(v, dtype=float)[ok] - 0.5, 0.36, bottom=0.5, color=colour,
               label=f"{'A: tags only' if k == 0 else 'B: tags + LLM labels'} (mean {row.macro_auc:.2f})")
        for xi, vi in zip(x, v, strict=True):
            if np.isnan(vi):
                ax.text(xi + (k - 0.5) * 0.38, 0.52, "n/a", ha="center", fontsize=7, color=viz.INK_2, rotation=90)
    ax.axhline(0.5, color=viz.INK_2, lw=1)
    ax.set_xticks(x, [NAME[c] for c in classes], rotation=45, ha="right", fontsize=9)
    ax.set_ylim(0.4, 1.0)
    ax.set_title(f"{test}\nn = {int(sc[sc.test == test].n.iloc[0]):,}", fontsize=11)
    ax.legend(fontsize=8, loc="upper right")
axs[0].set_ylabel("One-vs-rest AUC (0.5 = chance)")
fig.suptitle("How well each model ranks buildings by use (what the census allocation relies on)", x=0.01,
             ha="left", fontweight="bold")
fig.text(0.0, 0.02, "n/a: fewer than 10 buildings of that class in the test set. Tags held out by district (5-fold); "
         "LLM labels held out one deep-dive sector at a time; gold labels are an uncertain reference.",
         fontsize=8, color=viz.INK_2)
fig.savefig(FIG / "C1_use_model_scores.png")
plt.close(fig)

# %% C2: class mix, LLM vs models vs allocation
u = con.sql(f"""SELECT u.bldg_id, u.district, u.sector, u.use, p.a_label, p.b_label, l.label AS llm
    FROM '{P}/building_use.parquet' u JOIN '{P}/use_probs.parquet' p USING (bldg_id)
    LEFT JOIN (SELECT bldg_id, label FROM '{P}/labels.parquet' WHERE labeler = '{LABELER}') l USING (bldg_id)""").df()
rows = []
for sector, district in DD.items():
    s = u[(u.sector == sector) & (u.district == district)]
    lab = s[s.llm.notna() & (s.llm != "unknown")]
    rows += [(f"{sector} · LLM labels (n={len(lab):,})", lab.llm), (f"{sector} · model A", s.a_label),
             (f"{sector} · model B", s.b_label), (f"{sector} · allocated", s.use)]
rows += [("Rwanda · model A", u.a_label), ("Rwanda · model B", u.b_label), ("Rwanda · allocated", u.use)]
fig, ax = plt.subplots(figsize=(13, 8.5))
fig.subplots_adjust(left=0.3, top=0.9, bottom=0.14)
for i, (_, ser) in enumerate(rows):
    share = ser.value_counts(normalize=True).reindex(ORDER, fill_value=0)
    left = 0.0
    for c in ORDER:
        ax.barh(i, 100 * share[c], left=left, color=COL[c], edgecolor=viz.SURFACE, linewidth=1.5, height=0.78)
        if share[c] >= 0.06:
            ax.text(left + 50 * share[c], i, f"{100 * share[c]:.0f}%", ha="center", va="center", fontsize=8,
                    color="#ffffff" if c not in ("ancillary", "productive_use") else viz.INK)
        left += 100 * share[c]
ax.set_yticks(range(len(rows)), [r[0] for r in rows], fontsize=9)
ax.invert_yaxis()
for y in (3.5, 7.5, 11.5):
    ax.axhline(y, color=viz.INK_2, lw=0.8)
ax.set_xlim(0, 100)
ax.set_xlabel("% of buildings (top class or allocated use)")
ax.legend(handles=[Patch(color=COL[c], label=NAME[c]) for c in ORDER], ncol=4, fontsize=9, loc="upper center",
          bbox_to_anchor=(0.4, -0.08))
ax.set_title("Use mix: the LLM, the two classifiers, and the census-constrained allocation", loc="left")
fig.savefig(FIG / "C2_use_mix.png")
plt.close(fig)

# %% C3: allocated use per building in two sample boxes
boxes = load_yaml("dashboard.yaml")["sample"]["bboxes"]
fig, axs = plt.subplots(1, 2, figsize=(16, 8.2))
fig.subplots_adjust(wspace=0.05, top=0.88, bottom=0.12)
for ax, (key, title) in zip(axs, (("nyamirambo", "Nyamirambo (Kigali, urban)"), ("nasho", "Nasho (Kirehe, rural)")),
                            strict=True):
    x0, y0, x1, y1 = boxes[key]
    g = con.sql(f"""SELECT ST_AsText(b.geometry) AS w, u.use, u.households, u.establishment
        FROM '{I}/buildings_base.parquet' b JOIN '{P}/building_use.parquet' u USING (bldg_id)
        WHERE b.lon BETWEEN {x0} AND {x1} AND b.lat BETWEEN {y0} AND {y1}""").df()
    gdf = gpd.GeoDataFrame(g, geometry=g.w.map(wkt.loads), crs="EPSG:4326")
    gdf.plot(ax=ax, color=gdf.use.map(COL), edgecolor=viz.INK_2, linewidth=0.15)
    n = len(gdf)
    mix = gdf.use.value_counts()
    ax.set_title(f"{title}\n{n:,} buildings · {int(gdf.households.sum()):,} households · "
                 f"{int(gdf.establishment.notna().sum()):,} establishments", fontsize=11)
    ax.set_axis_off()
    ax.text(0.01, 0.01, " · ".join(f"{NAME[c]} {mix.get(c, 0)}" for c in ORDER if mix.get(c, 0)),
            transform=ax.transAxes, fontsize=8, color=viz.INK_2)
fig.legend(handles=[Patch(color=COL[c], label=NAME[c]) for c in ORDER], ncol=8, fontsize=9, loc="lower center")
fig.suptitle("Every building gets a use: tags first, then census counts placed on the best candidates",
             x=0.01, ha="left", fontweight="bold")
fig.savefig(FIG / "C3_allocation_maps.png")
plt.close(fig)

# %% C4: households, one per residential-classified building vs the census
hh = pd.read_csv(P / "validation_anchors" / "households_by_district_2022.csv").set_index("district").households_total
naive = u[u.b_label.isin(["residential", "mixed_shop_house"])].groupby("district").size()
alloc = con.sql(f"SELECT district, sum(households) h FROM '{P}/building_use.parquet' GROUP BY 1").df().set_index(
    "district").h
d = pd.DataFrame({"census": hh, "naive": naive, "alloc": alloc}).dropna()
fig, ax = plt.subplots(figsize=(8.5, 7.5))
lim = (0, d[["census", "naive"]].max().max() * 1.08 / 1000)
ax.plot(lim, lim, color=viz.INK_2, lw=1, ls="--")
ax.scatter(d.census / 1000, d.naive / 1000, s=36, color="#eb6834", edgecolor=viz.SURFACE, linewidth=2,
           label="one household per building model B calls residential", zorder=3)
ax.scatter(d.census / 1000, d.alloc / 1000, s=36, color="#2a78d6", edgecolor=viz.SURFACE, linewidth=2,
           label="census-constrained allocation (equal by construction)", zorder=3)
for nm, r in d.sort_values("naive", ascending=False).head(4).iterrows():
    ax.annotate(nm, (r.census / 1000, r.naive / 1000), xytext=(4, 3), textcoords="offset points", fontsize=8,
                color=viz.INK_2)
ax.set(xlim=lim, ylim=lim, xlabel="2022 census households (thousands)", ylabel="Modelled households (thousands)",
       title=f"Homes per district: {d.naive.sum() / d.census.sum():.1f}x too many without the census")
ax.legend(fontsize=8, loc="upper left")
viz.caption(fig, "Rwanda has 0.52 census households per building: rural compounds have several structures "
                 "(kitchen, latrine, store) per household.")
fig.savefig(FIG / "C4_households_fit.png")
plt.close(fig)

# %% C5: where uses come from
src = con.sql(f"SELECT use, source, count(*) n FROM '{P}/building_use.parquet' GROUP BY ALL").df()
pv = src.pivot_table(index="use", columns="source", values="n", fill_value=0).reindex(ORDER).fillna(0)
srcs = [("tag", "tag on the building", "#2a78d6"), ("census", "census count, best candidate", "#eb6834"),
        ("registry", "registry count (schools, worship)", "#1baf7a"),
        ("facility", "next to a school / health facility", "#eda100"),
        ("model", "classifier x LLM rate", "#e87ba4"), ("remainder", "left over (no load)", "#c9c8c3")]
fig, ax = plt.subplots(figsize=(12, 5.6))
fig.subplots_adjust(left=0.2, bottom=0.28)
left = np.zeros(len(pv))
for key, label, colour in srcs:
    v = pv.get(key, pd.Series(0, index=pv.index)).to_numpy()
    ax.barh(range(len(pv)), v, left=left, color=colour, label=label, edgecolor=viz.SURFACE, linewidth=1.5)
    left += v
for i, tot in enumerate(left):
    ax.text(tot * 1.15, i, f"{int(tot):,}", va="center", fontsize=8, color=viz.INK)
ax.set_xscale("log")
ax.set_xlim(50, 2e7)
ax.set_yticks(range(len(pv)), [NAME[c] for c in pv.index])
ax.invert_yaxis()
ax.set_xlabel("Buildings (log scale)")
ax.legend(fontsize=9, ncol=3, loc="upper center", bbox_to_anchor=(0.45, -0.16))
ax.set_title("6.43 million buildings, one use each, and where it comes from", loc="left")
fig.text(0.0, 0.01, "Order: tags and facility compounds; productive use; services; shops; schools and worship "
                 "(registries); other institutions and stores; households; the remainder is ancillary. "
                 "Shop-houses are shops that also host a census household.", fontsize=8,
         color=viz.INK_2)
fig.savefig(FIG / "C5_allocation_sources.png")
plt.close(fig)

# %% C6: national potential and expected demand per H3 r7 cell
lib = pd.read_parquet(P / "profile_library.parquet")[["profile_id", "daily_kwh_mean"]]
con.register("lib", lib)
v = con.sql(f"""SELECT u.h3_r7, sum(w.weight * l.daily_kwh_mean) * 365 / 1000 AS potential,
                       sum(w.weight * w.p_grid * l.daily_kwh_mean) * 365 / 1000 AS expected
    FROM '{P}/building_profiles.parquet' w JOIN lib l USING (profile_id)
    JOIN '{P}/building_use.parquet' u USING (bldg_id) GROUP BY 1""").df()
m = con.sql("SELECT *, h3_cell_to_boundary_wkt(h3_r7) AS w FROM v WHERE h3_r7 IS NOT NULL").df()
gdf = gpd.GeoDataFrame(m.drop(columns="w"), geometry=m.w.map(wkt.loads), crs="EPSG:4326")
fig, axs = plt.subplots(1, 2, figsize=(16, 6.2))
fig.subplots_adjust(left=0.01, right=0.86, top=0.88, bottom=0.06, wspace=0.02)
norm = LogNorm(10, gdf.potential.quantile(0.99))  # one scale for both panels
for ax, col, title in zip(axs, ("potential", "expected"), ("Potential: every building grid-connected",
                                                           "Expected: x P(connected), raked to EICV7 2024"),
                          strict=True):
    gdf.plot(column=col, ax=ax, cmap=viz.SEQ_BLUE, norm=norm, linewidth=0)
    ax.set_axis_off()
    ax.set_title(f"{title}\n{gdf[col].sum() / 1000:,.0f} GWh/yr", loc="left", fontsize=12)
sm = plt.cm.ScalarMappable(norm=norm, cmap=viz.SEQ_BLUE)
fig.colorbar(sm, cax=fig.add_axes([0.88, 0.15, 0.015, 0.65]), extend="both",
             label="MWh per year per H3 r7 cell (log scale)")
viz.caption(fig, "Sum over buildings of allocated profiles x RAMP daily energy; household energy scaled to REG "
                 "residential consumption by region (Kigali / elsewhere). Establishments count as connected.")
fig.savefig(FIG / "C6_national_demand.png")
plt.close(fig)
print(f"national demand: potential {gdf.potential.sum() / 1000:,.0f}, expected {gdf.expected.sum() / 1000:,.0f} GWh/yr")

# %% C7: demand by year, back-cast and projection
y = pd.read_csv(P / "demand_by_year.csv").groupby(["year", "kind"], as_index=False)[
    ["potential_gwh", "expected_gwh"]].sum().sort_values("year")
fig, ax = plt.subplots(figsize=(11, 5.4))
for col, colour, label in (("potential_gwh", "#2a78d6", "potential (all connected)"),
                           ("expected_gwh", "#eb6834", "expected (connection by year)")):
    past, fut = y[y.year <= 2023], y[y.year >= 2023]
    ax.plot(past.year, past[col], color=colour, lw=2, marker="o", ms=8, mec=viz.SURFACE, mew=2, label=label)
    ax.plot(fut.year, fut[col], color=colour, lw=2, ls="--")
    ax.text(2030.2, fut[col].iloc[-1], f"{fut[col].iloc[-1]:,.0f}", va="center", fontsize=9, color=viz.INK)
ax.set_ylim(0, y.potential_gwh.max() * 1.12)
ax.axvline(2023, color=viz.INK_2, lw=0.8)
ax.text(2023.15, y.potential_gwh.max() * 1.06, "base year 2023", fontsize=8, color=viz.INK_2)
ax.set_xlim(2015.5, 2031)
ax.set_xlabel("Year")
ax.set_ylabel("GWh per year")
ax.legend(fontsize=9, loc="upper left")
ax.set_title("Building demand by year: back-cast from building presence, projected with district growth", loc="left")
viz.caption(fig, "Solid: 2016-2023 from each building's detected presence, with district stock pinned to census "
                 "household growth (3.1%/yr nationally); connection rolls back to EICV5 2017 shares. Dashed: 2023 "
                 "demand grown at each district's rate, connection held at 2024.")
fig.savefig(FIG / "C7_demand_by_year.png")
plt.close(fig)

# %% C8: uncertainty bands in the deep-dive sectors
bands = pd.read_csv(P / "uncertainty" / "sector_bands.csv")
bands = bands[[(r.sector, r.district) in DD.items() for r in bands.itertuples()]].set_index("sector").reindex(list(DD))
fig, ax = plt.subplots(figsize=(11, 4.6))
fig.subplots_adjust(left=0.2, bottom=0.3)
for i, (_, r) in enumerate(bands.iterrows()):
    for k, (lo, hi, colour, label) in enumerate((
            ("alloc_p10_mwh", "alloc_p90_mwh", "#2a78d6", "allocation (10 runs, scores perturbed)"),
            ("profile_p10_mwh", "profile_p90_mwh", "#eb6834", "profile (RAMP day-to-day p10-p90)"),
            ("combined_p10_mwh", "combined_p90_mwh", "#1baf7a", "combined"))):
        yy = i + (k - 1) * 0.24
        ax.plot([100 * (r[lo] / r.base_mwh - 1), 100 * (r[hi] / r.base_mwh - 1)], [yy, yy], color=colour, lw=7,
                solid_capstyle="round", label=label if i == 0 else None)
    ax.plot(0, i, "o", color=viz.INK, ms=8, mec=viz.SURFACE, mew=2, label="base allocation" if i == 0 else None)
    ax.text(5.6, i, f"{r.base_mwh / 1000:.2f} GWh/yr", va="center", fontsize=9, color=viz.INK)
ax.axvline(0, color=viz.INK_2, lw=0.8)
ax.set_yticks(range(len(bands)), [f"{s} ({DD[s]})" for s in bands.index])
ax.invert_yaxis()
ax.set_xlim(-6, 8)
ax.set_xlabel("Difference from the base allocation's potential energy, %")
ax.legend(fontsize=9, ncol=2, loc="upper center", bbox_to_anchor=(0.45, -0.22))
ax.set_title("Sector totals are robust: district constraints keep the bands within a few percent", loc="left")
viz.caption(fig, "Which building holds a use is far less certain than the total: see the per-building use agreement "
                 "in data/processed/uncertainty/building_agreement.parquet.")
fig.savefig(FIG / "C8_uncertainty.png")
plt.close(fig)

# %% C9: benchmarks against REG sales
bm_sales = pd.read_csv(P / "validation_anchors" / "reg_sales_by_category.csv")
reg_y = bm_sales[(bm_sales.period_type == "year")].pivot_table(index="period", columns="category", values="kwh") / 1e6
yy = pd.read_csv(P / "demand_by_year.csv").groupby("year")[["expected_household_gwh"]].sum()
bm = pd.read_csv(P / "benchmarks.csv").set_index("item")
fig, axs = plt.subplots(1, 2, figsize=(15, 5.6), gridspec_kw={"width_ratios": [1.1, 1]})
fig.subplots_adjust(wspace=0.35, bottom=0.2)
ax = axs[0]
ax.plot(yy.index, yy.expected_household_gwh, color="#2a78d6", lw=2, label="model: households' expected demand")
ax.plot(reg_y.index.astype(int), reg_y["residential"], color="#eb6834", lw=2, marker="o", ms=8, mec=viz.SURFACE,
        mew=2, label="REG: residential electricity sold")
ax.set_ylim(0, max(yy.expected_household_gwh.max(), reg_y["residential"].max()) * 1.15)
ax.set_xlim(2015.5, 2030.5)
ax.set_ylabel("GWh per year")
ax.set_title("Households: model vs REG residential sales", loc="left", fontsize=12)
ax.legend(fontsize=9, loc="upper left")
ax = axs[1]
cats = [("Residential electricity, Q3 2023 - Q2 2024", "Residential"), ("Non-residential + small industry electricity",
        "Non-residential\n+ small industry"), ("Hotels electricity", "Hotels"), ("Health facilities electricity",
        "Health facilities")]
x = np.arange(len(cats))
ax.bar(x - 0.2, [bm.at[c, "official"] for c, _ in cats], 0.38, color="#eb6834", label="REG / RURA (2023)")
ax.bar(x + 0.2, [bm.at[c, "model"] for c, _ in cats], 0.38, color="#2a78d6", label="model (expected)")
for i, (c, _) in enumerate(cats):
    ax.text(i + 0.2, bm.at[c, "model"] + 4, f"{bm.at[c, 'ratio']:.2f}x", ha="center", fontsize=9, color=viz.INK)
ax.set_xticks(x, [lab for _, lab in cats], fontsize=9)
ax.set_ylabel("GWh per year")
ax.set_title("Electricity sold by customer category vs the model", loc="left", fontsize=12)
ax.legend(fontsize=9, loc="upper right")
viz.caption(fig, "REG sales: NISR Statistical Yearbook 2024 (Table 7.4) and RURA Q2 2024 (last four quarters). Labels "
                 "give model / REG. Households match by construction in level (REG's mean per customer is the "
                 "constraint); businesses are under-counted and the health-facility profiles run high.")
fig.savefig(FIG / "C9_benchmarks.png")
plt.close(fig)
