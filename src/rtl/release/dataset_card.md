---
license: odbl
pretty_name: {pretty_name}
language:
- en
tags:
- geospatial
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

Release {version}. Files: `data/buildings/<district>.parquet` (30 districts, plus `unassigned.parquet` for 833
footprints outside the district boundaries). Building-level electricity demand for all **{n_buildings:,}** building footprints in Rwanda: what
each building is used for, how much electricity it would use, when, and how likely it is to be connected. Built
for the OSEAS26 hackathon (EnAccess challenge "From building footprints to energy demand") with a reproducible,
open pipeline: <https://github.com/esemsc-as4623/peek-load>.

| National total (sum of the building table) | GWh/yr |
|---|---|
| Potential demand (every building grid-connected) | {potential:,.0f} |
| Expected demand (households weighted by their probability of a connection, 2024) | {expected:,.0f} |
| Expected demand by year (`demand_by_year`): 2016 back-cast / 2030 projection | {exp2016:,.0f} / {exp2030:,.0f} |

## How the numbers are made

1. **Footprints and context.** VIDA's merge of Google, Microsoft and OpenStreetMap footprints; heights and yearly
   presence (Google Open Buildings 2.5D Temporal); roads, places, settlement class, population density, climate
   (Copernicus ERA5-Land) and night lights (NASA Black Marble).
2. **Use.** A gradient-boosting classifier gives every building use probabilities. Census and registry counts are
   then placed on the most likely buildings, rarest use first: Establishment Census 2023 (manufacturing, services,
   shops), MINEDUC schools, Ministry of Health facilities, places of worship, and the 2022 census households per
   district (urban and rural), up to one household per floor. The rest are ancillary structures with no load.
3. **Load profiles.** 23 RAMP appliance profiles (households by setting and wealth, shops, bars, salons, mills,
   workshops, schools, health facilities, offices, churches, stores), built from World Bank MTF 2022 appliance use,
   a cited evidence base and SEforALL's 2023 health-facility measurements. Household energy is scaled to REG's
   mean residential consumption.
4. **Connection.** A model trained on DHS 2025 gives each building's neighbourhood a probability of grid
   connection, raked to EICV7 2024 per district. Expected = potential x that probability (households only).
5. **Years.** Back-cast 2016-2023 from each building's detected presence, pinned to census household growth, with
   connection rolled back per district to EICV4 and EICV5; projected to 2030 by district growth.

Checked against REG: households' expected demand is 208 GWh in 2023 against 211 GWh of residential sales; connected
households per REG branch correlate at r = 0.93; health facilities match REG's 2023 sales to within the QA bound.
Businesses are under-counted (modelled establishments reach about 30% of REG's non-residential sales). Full table:
`docs/benchmarks.md`.

**How to read a building's values.** Most inputs are aggregates (district counts, survey clusters, national means).
Building values are a *plausible allocation* that reproduces those totals, not observations: see `use_agreement`
for how stable a building's use is under perturbed scores.

## The five challenge outputs (columns of `buildings`)

| Output | Columns |
|---|---|
| 1. Estimated peak load | `peak_w` (expected coincident peak, W), `peak_hour` (local time, UTC+2) |
| 2. Annual energy consumption | `annual_kwh_potential`, `annual_kwh_expected`, `annual_kwh_p10`, `annual_kwh_p90` |
| 3. Cooling demand category | `cooling_class` (low / medium / high from tmax p95), `tmax_p95_c`, `cdd22_per_year` |
| 4. Load profile characteristics | `load_factor`, `evening_share`, `daytime_share`, `night_base_w`, `weekday_weekend_ratio`, `load_shape` |
| 5. Productive-use viability | `pu_viability_score` (0-100), `pu_viability` (low / medium / high) |

Other columns: identifiers and admin names (`bldg_id`, `province` ... `village`, `h3_r9`, `h3_r7`, `lon`, `lat`),
footprint (`footprint_source`, `source_confidence`, `area_m2`, `qa_flags`, `geometry` WKB, lon/lat WGS84), height
(`height_m`, `est_floors`, `gfa_m2`, `first_seen_year`), context (`facility_type`, `dist_road_any_m`,
`dist_mv_line_m`, `pop_density_per_km2`, `ghsl_class`, `elevation_m`), allocation (`setting`, `use`, `use_source`,
`establishment`, `establishment_share`, `households`, `upper_wealth`, `use_agreement`), connection
(`p_connected_model`, `p_connected_calibrated`) and the back-cast (`expected_kwh_2016` ... `expected_kwh_2023`).

To rebuild any building's hourly curve: join `building_profiles` (energy `weight` per profile) to `profiles`
(per-unit hourly W, `w_h00` ... `w_h23`) and sum weight x curve. Area peaks sum the curves first.

```python
from datasets import load_dataset
b = load_dataset("<repo>", "buildings", split="train")
# or with geometry: geopandas.read_parquet("data/buildings/kirehe.parquet")
```

## Licence and attribution

ODbL-1.0 (see `LICENSE.md`). {licence_notes} Meta's Relative Wealth Index values are not included.
Cite this dataset (`CITATION.cff`) and the underlying sources listed in `docs/data_sources.md`.

## Limitations

See `docs/datasheet.md` (known limitations) and `docs/status_report.md` (decisions and pending items). In short:
building use is uncertain where tags are absent; heights are compressed; rural manufacturing is 25% mills by
assumption; service splits are assumptions; profiles assume a grid connection; seasonal variation is not modelled.
