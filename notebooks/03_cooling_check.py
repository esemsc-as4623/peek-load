# ---
# jupyter:
#   jupytext:
#     formats: py:percent
# ---

# %% [markdown]
# # 03 · Is there real cooling demand? An independent check with NASA POWER
#
# The climate table (ERA5-Land, downscaled with elevation) says Rwanda has almost no cooling demand, except in
# a few hot lowlands. Here we check that against an independent source, NASA POWER (MERRA-2 based, ~0.5°),
# at Kigali, two reference towns and the hottest building area in the country. Two signatures, each with
# a sharp threshold:
#
# - **tmax_p95**: 95th percentile of daily maximum temperature (how hot the hot afternoons get);
# - **CDD22**: cooling degree-days above a 22 °C daily mean (how much sustained heat a building must shed),
#
# plus *when* the heat happens: hours ≥ 26 °C by month and hour of day.
#
# What this can and can't show: it measures the **climate driver** of cooling (exposure). Attributing
# *measured electricity demand* to cooling needs metered load by season and time of day, which we don't have
# yet (a question for REG / the organisers).

# %%
import json

import duckdb
import h3
import httpx
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from rtl import viz
from rtl.manifest import record
from rtl.settings import INTERIM_DIR, RAW_DIR, REPORTS_DIR

viz.use()
LAPSE_K_PER_KM = -6.05  # fitted on the ERA5-Land grid by A3 (R² 0.94)
POWER_DIR = RAW_DIR / "nasa_power" / "2026-10-08"
clim = pd.read_parquet(INTERIM_DIR / "climate_h3.parquet")

# hottest building area: the cell with the highest tmax_p95
hot = clim.sort_values("tmax_p95_c", ascending=False).iloc[0]
hot_lat, hot_lon = h3.cell_to_latlng(hot.h3_r7)
SITES = {
    "Kigali": (-1.95, 30.06, "t2m_hourly_kigali.json"),
    "Musanze (cool ref.)": (-1.50, 29.63, "t2m_hourly_musanze.json"),
    "Kirehe/Nasho (east)": (-2.20, 30.65, "t2m_hourly_kirehe_nasho.json"),
    "Rusizi": (-2.48, 28.90, "t2m_hourly_rusizi.json"),
    "Hottest cell (Bugarama valley)": (round(hot_lat, 4), round(hot_lon, 4), "t2m_hourly_hottest_cell.json"),
}

# %%
# fetch POWER hourly T2M for the hottest cell (the four towns were downloaded by A3); free API, no key
f_hot = POWER_DIR / SITES["Hottest cell (Bugarama valley)"][2]
if not f_hot.exists():
    url = "https://power.larc.nasa.gov/api/temporal/hourly/point"
    q = dict(parameters="T2M", community="RE", latitude=hot_lat, longitude=hot_lon, start="20190101",
             end="20241231", format="JSON", **{"time-standard": "UTC"})
    r = httpx.get(url, params=q, timeout=300)
    r.raise_for_status()
    f_hot.write_text(r.text)
    record("nasa_power", f_hot, str(r.url))


# %%
def load_power(fname: str) -> tuple[pd.Series, float]:
    """Hourly T2M (°C) in Rwandan local time and the POWER grid-cell elevation (m).

    POWER hourly files come in UTC or local solar time (header `time_standard`). Rwanda is UTC+2 with no daylight
    saving, and local solar time at 29-31°E is within ~10 min of that, so UTC is shifted by 2 h and LST is used
    as is."""
    d = json.loads((POWER_DIR / fname).read_text())
    ts = d["header"]["time_standard"]
    s = pd.Series(d["properties"]["parameter"]["T2M"])
    s.index = pd.to_datetime(s.index, format="%Y%m%d%H") + pd.Timedelta(hours=2 if ts == "UTC" else 0)
    s = s[(s.index.year >= 2019) & (s.index.year <= 2024)]  # the shift pushes 2 h into 2025
    return s.where(s > -900).dropna(), float(d["geometry"]["coordinates"][2])


def signature(t: pd.Series) -> dict:
    daily = t.resample("D").agg(["mean", "max"])
    years = t.index.year.nunique()
    return {"t2m_mean_c": t.mean(), "tmax_p95_c": daily["max"].quantile(0.95),
            "cdd22_per_year": (daily["mean"] - 22).clip(lower=0).sum() / years,
            "hours_ge26_per_year": (t >= 26).sum() / years, "hours_ge28_per_year": (t >= 28).sum() / years}


rows, hourly = [], {}
for name, (lat, lon, fname) in SITES.items():
    t_raw, z_power = load_power(fname)
    cell = clim.set_index("h3_r7").loc[h3.latlng_to_cell(lat, lon, 7)] if h3.latlng_to_cell(lat, lon, 7) in set(
        clim.h3_r7) else None
    z_cell = float(cell.elevation_m) if cell is not None else np.nan
    # POWER's 0.5° cell has its own mean elevation; shift it to the building cell's elevation (same lapse rate)
    t = t_raw + (LAPSE_K_PER_KM / 1000) * (z_cell - z_power) if np.isfinite(z_cell) else t_raw
    hourly[name] = t
    sig = signature(t)
    rows.append({"site": name, "elev_cell_m": z_cell, "elev_power_m": z_power,
                 **{f"power_{k}": v for k, v in sig.items()},
                 "era5l_tmax_p95_c": cell.tmax_p95_c if cell is not None else np.nan,
                 "era5l_cdd22_per_year": cell.cdd22_per_year if cell is not None else np.nan,
                 "cooling_class": cell.cooling_class if cell is not None else None})
res = pd.DataFrame(rows)
pd.set_option("display.width", 200)
print(res.round(1).to_string(index=False))

# %%
# when does the heat happen? hours >= 26 °C by month and by hour of day
fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 3.8))
for k, (name, t) in enumerate(hourly.items()):
    yrs = t.index.year.nunique()
    by_month = (t >= 26).groupby(t.index.month).sum() / yrs
    by_hour = (t >= 26).groupby(t.index.hour).sum() / yrs
    col = viz.CAT[k]
    a1.plot(by_month.index, by_month.values, marker="o", ms=4, color=col, label=name)
    a2.plot(by_hour.index, by_hour.values, color=col, label=name)
a1.set_xticks(range(1, 13), list("JFMAMJJASOND"))
a1.set_ylabel("hours ≥ 26 °C per year")
a1.set_title("Hot hours by month")
a2.set_xlabel("hour of day (Rwanda, UTC+2)")
a2.set_title("Hot hours by time of day")
a2.legend(fontsize=8, loc="upper left")
viz.caption(fig, "NASA POWER hourly T2M 2019–2024, shifted to each building cell's elevation (−6.05 K/km).")
fig.savefig(REPORTS_DIR / "figures" / "A3_cooling_check.png")

# %%
out = {"sites": res.round(2).to_dict(orient="records"),
       "hottest_cell": {"h3_r7": hot.h3_r7, "lat": hot_lat, "lon": hot_lon},
       "buildings_by_cooling_class": duckdb.sql(f"""
            SELECT k.cooling_class, count(*) AS buildings
            FROM read_parquet('{INTERIM_DIR / "building_context.parquet"}') c
            JOIN read_parquet('{INTERIM_DIR / "climate_h3.parquet"}') k USING (h3_r7) GROUP BY 1""").df()
       .set_index("cooling_class").buildings.to_dict()}
(REPORTS_DIR / "qa" / "cooling_check.json").write_text(json.dumps(out, indent=2, default=float) + "\n")
print(json.dumps(out["buildings_by_cooling_class"]))
