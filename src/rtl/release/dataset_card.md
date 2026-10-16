---
license: odbl
pretty_name: {pretty_name}
language:
- en
tags:
- geospatial
- geoparquet
- energy
- electricity-demand
- energy-access
- buildings
- load-profiles
- ramp
- rwanda
- africa
size_categories:
- 1M<n<10M
configs:
- config_name: buildings
  data_files: "data/buildings/*.parquet"
  default: true
- config_name: building_profiles
  data_files: "data/building_profiles.parquet"
- config_name: profiles
  data_files: "data/profiles.parquet"
- config_name: districts
  data_files: "data/districts.parquet"
- config_name: sectors
  data_files: "data/sectors.parquet"
- config_name: demand_by_year
  data_files: "data/demand_by_year.parquet"
- config_name: benchmarks
  data_files: "data/benchmarks.parquet"
---

# {title}

**{n_buildings:,} building footprints in Rwanda, each with an estimate of what it is used for, how much electricity
it would use, when in the day, and how likely it is to be grid-connected.** Release {version} by {team}, built for
the OSEAS26 hackathon (EnAccess challenge *From building footprints to energy demand*) with an open, reproducible
pipeline: <https://github.com/esemsc-as4623/peek-load>.

| National total (sum of the building table) | GWh/yr |
|---|---|
| Potential demand (every building grid-connected) | {potential:,.0f} |
| Expected demand (households weighted by their probability of a connection, 2024) | {expected:,.0f} |
| Expected demand by year (`demand_by_year`): 2016 back-cast / 2030 projection | {exp2016:,.0f} / {exp2030:,.0f} |

## Quick start

```python
import pandas as pd
# one district (columns below); data/buildings/ holds the 30 districts + unassigned.parquet
b = pd.read_parquet("hf://datasets/{repo}/data/buildings/kirehe.parquet")
b.groupby("use").annual_kwh_expected.sum() / 1e6          # GWh/yr by building use

import geopandas as gpd                                     # footprints as polygons (GeoParquet, lon/lat WGS84)
g = gpd.read_parquet("hf://datasets/{repo}/data/buildings/kirehe.parquet")

import duckdb                                               # the whole country in SQL
duckdb.sql("""SELECT district, round(sum(annual_kwh_expected) / 1e6, 1) AS gwh
              FROM 'hf://datasets/{repo}/data/buildings/*.parquet' GROUP BY 1 ORDER BY 2 DESC""")

from datasets import load_dataset                           # the Hub loader (configs: buildings, profiles, ...)
profiles = load_dataset("{repo}", "profiles", split="train")
```

**A building's 24-hour load curve** is the sum over its profiles of `weight` × the profile's per-unit hourly curve:

```python
bp = pd.read_parquet("hf://datasets/{repo}/data/building_profiles.parquet")
lib = pd.read_parquet("hf://datasets/{repo}/data/profiles.parquet").set_index("profile_id")
hours = [f"w_h{{h:02d}}" for h in range(24)]
one = bp[bp.bldg_id == b.bldg_id.iloc[0]]
curve_w = (lib.loc[one.profile_id, hours].to_numpy() * one[["weight"]].to_numpy()).sum(axis=0)  # W, potential
# expected: multiply each row's weight by its p_grid. Area curves: sum building curves first, then take the peak.
```

## Files

{files_table}

## The five challenge outputs

| Output | Columns in `buildings` |
|---|---|
| 1. Estimated peak load | `peak_w` (expected coincident peak, W), `peak_hour` (local time, UTC+2) |
| 2. Annual energy consumption | `annual_kwh_potential`, `annual_kwh_expected`, `annual_kwh_p10`, `annual_kwh_p90` |
| 3. Cooling demand category | `cooling_class`, `tmax_p95_c`, `cdd22_per_year` |
| 4. Load profile characteristics | `load_factor`, `evening_share`, `daytime_share`, `night_base_w`, `weekday_weekend_ratio`, `load_shape` |
| 5. Productive-use viability | `pu_viability_score` (0-100), `pu_viability` (low / medium / high) |

## Columns of `buildings`

| Column | Unit | Meaning |
|---|---|---|
| `bldg_id` | | stable id (`RWA-` + hash of source and geometry); joins every table |
| `province`, `district`, `sector`, `cell`, `village` | | administrative areas (geoBoundaries ADM1-5); null outside the boundaries |
| `lon`, `lat`, `geometry` | degrees | centroid and footprint polygon (WKB, lon/lat on WGS84) |
| `h3_r9`, `h3_r7` | | H3 cells (~0.1 km², ~5 km²) |
| `footprint_source`, `source_confidence`, `qa_flags` | | google / microsoft / osm; detector confidence; quality flags (kept, not dropped) |
| `area_m2`, `height_m`, `est_floors`, `gfa_m2`, `first_seen_year` | m², m | footprint area; height (Google 2.5D, 2023); estimated floors; floor area; first year stably detected |
| `facility_type`, `dist_road_any_m`, `dist_mv_line_m`, `pop_density_per_km2`, `ghsl_class`, `elevation_m` | m, people/km² | context: tagged facility, distance to a road and to a (predicted) MV line, density, settlement class, elevation |
| `setting` | | urban / rural, matched to the 2022 census share of urban households per district |
| `use` | | residential, mixed_shop_house, commercial, institutional, religious, productive_use, industrial_warehouse, ancillary (no load) |
| `use_source` | | tag, facility, census, registry, model or remainder: where the building's use comes from |
| `establishment`, `establishment_share` | | profile of the business or institution in it; share when a facility spans several buildings |
| `households`, `upper_wealth` | count | census households placed in the building (up to one per floor); upper wealth group (DHS-calibrated) |
| `use_agreement` | 0-1 | share of 10 perturbed allocations that keep this use: how certain the placement is |
| `annual_kwh_potential`, `annual_kwh_expected` | kWh/yr | if connected; expected = households × P(connected) (establishments count as connected) |
| `annual_kwh_p10`, `annual_kwh_p90` | kWh/yr | day-to-day spread of the profiles (potential) |
| `peak_w`, `peak_hour`, `load_factor`, `evening_share`, `daytime_share`, `night_base_w`, `weekday_weekend_ratio`, `load_shape` | W, h | from the building's 24-hour curve (potential) |
| `cooling_class`, `tmax_p95_c`, `cdd22_per_year` | °C | climate of the H3 r7 cell (ERA5-Land 2020-2025): low < 26 °C, medium 26-29, high ≥ 29 (tmax p95) |
| `pu_viability_score`, `pu_viability` | 0-100 | productive-use candidate score (market access, customers, grid, ability to pay, cropland, size) |
| `p_connected_model`, `p_connected_calibrated` | 0-1 | neighbourhood probability of a grid connection (DHS 2025 model); raked to EICV7 2024 per district |
| `expected_kwh_2016` ... `expected_kwh_2023` | kWh/yr | back-cast expected demand (presence-weighted, so it can be fractional) |

## How the numbers are made

1. **Footprints and context.** VIDA's merge of Google, Microsoft and OpenStreetMap footprints; heights and yearly
   presence (Google Open Buildings 2.5D Temporal); roads, places, settlement class, population density, climate
   (Copernicus ERA5-Land) and night lights (NASA Black Marble).
2. **Use.** A gradient-boosting classifier gives every building use probabilities. Census and registry counts are
   then placed on the most likely buildings, rarest use first: Establishment Census 2023 (manufacturing, services,
   shops), MINEDUC schools, Ministry of Health facilities (63 hospitals, 518 health centres, ...), places of
   worship, and the 2022 census households per district (urban and rural). The rest are ancillary structures.
3. **Load profiles.** 23 RAMP appliance profiles (households by setting and wealth, shops, bars, salons, mills,
   workshops, schools, health facilities, offices, churches, stores) built from World Bank MTF 2022 appliance use,
   a cited evidence base and SEforALL's 2023 health-facility measurements. Household energy is scaled to REG's
   mean residential consumption (Kigali / elsewhere).
4. **Connection.** A model trained on DHS 2025 gives each neighbourhood a probability of grid connection, raked to
   EICV7 2024 per district.
5. **Years.** Back-cast 2016-2023 from each building's detected presence, pinned to census household growth, with
   connection rolled back per district to EICV4 and EICV5; projected to 2030 by district growth.

**Checked against REG** (`data/benchmarks.parquet`, `docs/benchmarks.md`): households' expected demand is 208 GWh
in 2023 against 211 GWh of residential sales; connected households per REG branch correlate at r = 0.93; health
facilities use 23.0 GWh against REG's 22.7. Businesses are under-counted (about 30% of REG's non-residential
sales), and large hotels are missing.

**How to read a building's values.** Most inputs are aggregates (district counts, survey clusters, national means).
Building values are a *plausible allocation* that reproduces those totals, not observations: use `use_agreement`
to see how stable a building's use is, and prefer area sums for planning.

## Limitations

Building use is uncertain where tags are absent; heights are compressed (floors above 2 are unreliable); rural
manufacturing is 25% electric mills by assumption; service splits are assumptions; private clinics use a health
centre's profile; profiles assume a grid connection; seasonal variation is not modelled; businesses are
under-counted. More in `docs/datasheet.md` and `docs/status_report.md`.

## Licence and attribution

Open Database License (ODbL-1.0), see `LICENSE.md`. {licence_notes} Meta's Relative Wealth Index values are not
included. Please cite this dataset and the underlying sources (`docs/data_sources.md`).

```bibtex
@misc{{peek_load_{year}_rooftops,
  author       = {{{team}}},
  title        = {{{title}}},
  year         = {{{year}}},
  version      = {{{version}}},
  publisher    = {{Hugging Face}},
  howpublished = {{\url{{https://huggingface.co/datasets/{repo}}}}},
  note         = {{OSEAS26 hackathon, EnAccess challenge. Code: https://github.com/esemsc-as4623/peek-load}}
}}
```
