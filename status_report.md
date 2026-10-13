# Rooftops → Load Curves: status report

*OSEAS26 challenge: building footprints → energy demand · repo `esemsc-as4623/peek-load` · updated 2026-10-13*

## 1. Summary
We turn Rwanda's 6.4 million open building footprints into building-level electricity demand. Every building gets
its shape, height, surroundings, climate and, where we can tell, a use. Use-specific appliance profiles (RAMP)
then give a load curve, peak and annual energy. The pipeline is a chain of separate steps (section 2), each with a
clear input, output and check, so any step can be swapped: a simple ML model for the LLM labeller or the height
rules, for example.

Where we are (2026-10-13):
- **All six national building tables** are built and pass QA.
- **Demand is modelled** for three deep-dive sectors: Nyamirambo (Kigali), Muhoza (Musanze) and Nasho (Kirehe).
  It is *potential* demand if every building were grid-connected.
- **A grid-connection model** now exists (not yet applied to demand).
- **A static dashboard bundle and design prototype** show it all.

Open decisions and data opportunities are in [`decisions.md`](decisions.md).

**How to read the numbers.** Most of our "ground truth" is aggregated: district census counts, survey clusters,
national averages. Building-level values are a *plausible allocation* that reproduces those totals, not
observations. Each step says which totals it matches and how we check it.

## 2. The pipeline, step by step

| # | Step and goal | Input (raw data) | Output | Workflow, assumptions and decisions | Figures | Evaluation / extension |
|---|---|---|---|---|---|---|
| 1 | **Footprints.** One clean, de-duplicated footprint layer for the country. | VIDA combined Open Buildings (Google V3 + Microsoft + OSM, Source Cooperative, ODbL), 6,434,264 polygons | `buildings_base`: **6,434,247 buildings**. **Google 5,814,224 (90.4%)**, **Microsoft 491,967 (7.6%)**, **OSM 128,056 (2.0%)**. Each has area, perimeter, compactness, orientation, H3 r9 cell and a stable id | VIDA already merges the three sources (no cross-source overlaps remain: 0 pairs). We drop only 17 exact duplicates. **Flag, don't drop:** low detector confidence below 0.70 (808,772; 12.6%); tiny below 6 m² (34,070; 0.5%); same-source footprint more than half inside another (2,186). 87.2% carry no flag. Metrics are computed in UTM 35S | `eda01_area_by_source.png`, `eda01_google_confidence.png`, `eda01_density_h3r7.png` | **Evaluation:** QA (schema, geometry validity, duplicates). Not yet checked against 2022 census households per district. **Extension:** a completeness model (buildings vs households); Microsoft omits structures under ~15 m² |
| 2 | **Height, floors, age.** Floors and floor area for loads that scale with size; a first-seen year for growth. | Google Open Buildings 2.5D Temporal (heights 2023, yearly presence 2016–2023); OSM `building:levels` (12,088 tagged polygons) | `building_height`: height for **71.6%** of buildings (median 3.3 m, p90 5.1 m), estimated floors, floor area, first-seen year, presence 2016–2023 | **Rule-based floors:** 1 floor below 6.0 m, 2 floors below 8.5 m, then +1 per 3 m. Cut-points fitted on OSM-tagged buildings with even ids (n = 4,919) and **tested on odd ids (n = 4,927)**. Exact on the test set: 82.9% for 1-storey, 32.9% for 2, 9.7% for 3+ (78.1% overall). A single 3.4 m storey height does worse on 1-storey buildings (MAE 0.41 vs 0.20 floors). First-seen year is the first year presence stays ≥ 0.5: 33% of buildings get one | **`A1_height_by_district.png`** (new: heights per district), `A1_height_vs_osm.png`, `A1_growth.png` | **Evaluation:** held-out OSM levels (above). The heights are compressed: single storeys read ~4–5 m, so 3+ floors are unreliable. First-seen year has jumps in 2019 and 2023 (experimental). **Extension:** a regression or classification model (footprint size and shape, neighbours' heights, GHSL built height) in place of the cut-points; quantile output for floor uncertainty |
| 3 | **Building context.** Everything around a building that hints at its use, wealth and grid access. | OSM (Geofabrik: roads, land use, tags, power lines); Overture places; healthsites.io; Gridfinder MV lines; geoBoundaries ADM1–5; GHSL BUILT-C 2018; WorldPop 2023; Meta RWI; Copernicus DEM; **ESA WorldCover 2021** (new) | `building_context`: admin names to village; OSM/Overture/facility tags; distance to major road, any road and MV line; places within 50/100/250 m; GHSL settlement class and non-residential share; population density; RWI; elevation. Separately, cropland share per H3 r8 cell (`cropland_h3r8`) | Spatial joins in DuckDB (point-in-polygon, then nearest within a radius); raster values sampled at the centroid. A tag counts as **usable** if it says something about use: a non-generic OSM `building=*`, amenity/shop, an OSM point inside the footprint, an Overture category or a facility record. **Only 37,132 buildings (0.6%) have one** (Overture 2,100, facilities 8,088, the rest OSM). Distance to grid uses Gridfinder (a prediction) because OSM maps only ~120 lines | `A2v_match_coverage.png`, `B4_grid_raw_features.png` (raw feature maps) | **Evaluation:** QA on coverage and ranges (RWI and density are present for >99.9%). **Extension:** official registries (MINEDUC schools, MoH facilities), Kigali zoning, REG network GIS in place of Gridfinder |
| 4 | **Climate.** Cooling need and hourly and seasonal climate per area. | ERA5-Land hourly 2019–2025 from the Copernicus Climate Data Store (temperature, dewpoint, solar, precipitation, wind); Copernicus DEM; NASA POWER for checks | `climate_h3`: 4,034 H3 r7 cells with mean and 95th-percentile daily max temperature, cooling degree-days (18/22/24 °C), 24-hour typical day, monthly temperature and rainfall, humidity, solar (GHI), cooling class | Bilinear interpolation of the 0.1° grid plus a fitted lapse rate (−6.16 K/km, r² 0.94) to each cell's elevation; local time UTC+2; 2020–2025. Cooling class from tmax p95: low < 26 °C, medium 26–29, high ≥ 29. **Cells: 1,468 / 1,532 / 1,034; buildings: 2.27M / 3.16M / 1.01M.** ERA5-Land rainfall runs wet over mountains (indicative) | `A3_cooling_class.png`, `A3_cdd22_per_year.png`, `A3_t2m_mean_c.png`, `A3_cooling_check.png` | **Evaluation:** NASA POWER at 4 towns. Kigali agrees (bias −0.01 °C after the elevation shift). The eastern lowlands run warmer in ERA5-Land than POWER (provisional). **Extension:** the 1991–2020 baseline (2 of 60 requests in; Copernicus is very slow); station data; seasonal load profiles |
| 5 | **Building use (LLM labelling).** A use class for buildings without a usable tag. | A text "context card" per building, built only from steps 1–4; 400 gold candidates with **173 human labels** | `labels`: **44,556 buildings** labelled with probabilities over 9 classes plus cited cues (all of Nyamirambo and Nasho, 8.7k of Muhoza, and the 400 gold candidates). Top-class mix: residential 69%, ancillary 28%, commercial 0.8%, institutional 0.7%, shop-house 0.6%, productive use 5 buildings | Stratified sample (deep dives + national; size tercile × tagged). 11 model/prompt configurations compared; **Claude Haiku 4.5, prompt v2** chosen as the cheapest within 0.03 macro-F1 of the best ($3.6 per 1,000 buildings). Batch API, every call cached. **The labeller sees tags for only 1.1% (Nyamirambo), 2.1% (Muhoza) and 0.01% (Nasho) of buildings**; everything else is the LLM's inference from shape, size and surroundings | **`A4_labels_by_district.png`** (new: tagged vs LLM-only by district, and class mix), `labels_confusion.png` | **Evaluation:** agreement with the human labels is 39% (macro-F1 0.19). **The gold labels are themselves uncertain:** the imagery showed little beyond vegetation and roof shape, so this measures consistency between two uncertain readers, not accuracy. Productive use is invisible to both. **Extension:** a classical classifier (gradient boosting on context features) trained on tags as silver labels, needed for national coverage, where the LLM would cost ~$23k; calibrating class shares to census establishment counts |
| 6 | **Evidence base.** Cited numbers for appliance power, hours, ownership, tariffs and consumption. | 26 public reports (DHS, MTF, NISR, RURA, REG/QSEL, NREL, GIZ, Power Africa); restricted microdata: DHS 2025, World Bank MTF 2022, NISR EICV5 | `evidence` (3,076 extracted parameters, 81% with a quote verified verbatim on the page); 163 productive-use web parameters; 37 hand-curated values; `validation_anchors` and MTF/EICV5 aggregates (cells under 10 respondents suppressed) | LLM extraction with page and quote, verified in code; automated plausibility checks (units, ranges, cross-source agreement) **flag 17 of 1,077** RAMP-relevant values, which are excluded. The owner gave a blanket approval backed by those checks (not row by row). Microdata never leaves `data/restricted/`; only aggregates are published | – | **Evaluation:** quote verification, plausibility flags. **Extension:** health-post energy data; RAMP input sets from published studies |
| 7 | **Appliance profiles (RAMP archetypes).** One load profile per building type. | Step 6 evidence and aggregates; recipes in `config/archetypes.yaml` | **17 archetypes**, signed off 2026-10-12: 4 households (rural/urban × lower/upper wealth half), shop, salon, bar/restaurant, guesthouse, school, health facility, office, church, store, grain mill, welding, tailoring, carpentry. `profile_library`: per-unit hourly curves, daily kWh spread and coincident peak (RAMP 0.5.2, 200 units × 28 days) | Households: ownership and hours from MTF 2022 grid households by setting and Ubudehe group. **One calibration:** lighting on-time scaled to MTF spending, converted with the RURA tariff (factors 0.52–1.09). Establishments: MTF enterprise, school and health surveys plus the evidence table; bars, guesthouses, offices, churches and stores rest on written assumptions. We fixed a RAMP seeding bug: runs are now reproducible and peaks fall in the evening | `B1_profiles.png`, `B1_household_check.png` | **Evaluation** (`reports/demand/profile_checks.md`): urban 24.4 kWh/month vs REG Kigali 24.8 and EICV5 23.1 (ok); rural 9.96 vs QSEL 8.1 (ok) and REG outside Kigali 5.9 (1.7×, check); school, mill and carpentry ok; **health fails** (built from health centres, not posts). All four household archetypes are MTF Tier 2. **Extension:** seasonal variants (monthly climate is ready), off-grid/solar variants, a health-post source |
| 8 | **Building → profile mapping.** How many units of which profile each building holds. | `labels`, `building_context`, rules in `config/profile_mapping.yaml`, tagged trades and services (steps 9–10) | `building_profiles`: **45,534 buildings → 56,746 weights** (44,062 labelled + tagged trade/service buildings elsewhere) | Class probabilities **under 0.10 are ignored and the rest renormalised** (owner, 2026-10-12): this keeps real commercial and institutional signal without adding phantom shops. Productive use is never taken from the labeller. Urban if density ≥ 2,500/km² or a GHSL urban class; upper wealth half if RWI is above its setting's median. A shop-house is 1 household + 1 shop. **Tagged trade and service buildings replace their class weights with their tag's profile** | – | **Evaluation:** QA reconciles profile ids across recipes, mapping, weights, library and dashboard. A "top class only" alternative is exported for comparison (about 10% less energy in each sector). **Extension:** calibrate the labeller's probabilities on gold and census counts |
| 9 | **Productive use.** Mills and workshops are what the challenge asks about, but they can't be seen in open data. | NISR Establishment Census 2023 tables (`data/restricted/reg/`); trade tags (OSM, Overture); steps 1–3; ESA WorldCover | `pu_located` (**57 buildings**), `pu_unlocated_areas` (per H3 cell, sector, district), `pu_candidates` (**17,103 buildings**) | **Reported:** manufacturing is 6.6% of urban and 7.0% of rural establishments, giving **~17,750 nationally** (district enterprises × setting share; there is no district × activity table). **Located:** buildings with a trade tag get that trade's profile. **Unlocated:** the rest is spread over eligible buildings (≥ 30 m², not ancillary/religious/institutional or a facility) by a candidate score, then summed per area and never put on a building. **Owner's rule:** mills only in rural roadside places (≤ 100 m from a road); tailors, welders and carpenters in towns (equal shares, an assumption). **Candidates:** the top scorers per cell are flagged for field checks, with no demand assigned | dashboard *Productive use* page | **Evaluation:** QA checks the census amount is conserved across cell, sector and district. All rural manufacturing counted as mills gives ~86 GWh/yr nationally, an upper bound. **Extension:** a mill share for rural manufacturing; trade shares from the census microdata or the RDB register; field checks of candidates as labels |
| 10 | **Services (new, 2026-10-13).** Bars/restaurants, guesthouses and salons, which almost vanished from the labels after the 0.10 rule. | Establishment Census 2023 sections I (accommodation and food, 61,923) and S (other services, 22,949); OSM/Overture bar, restaurant, hotel and salon tags; WorldPop density | `svc_located` (**1,500 buildings**: 768 bars/restaurants, 679 hotels/guesthouses, 53 salons), `svc_unlocated_areas` | Same pattern as productive use. **Assumed splits:** section I is 97% bars/restaurants and 3% accommodation; section S is 60% hairdressing and beauty. That gives ~60,200 bars/restaurants, 1,860 guesthouses and 12,800 salons nationally, of which 73,500 are unlocated. Within a district and setting, unlocated establishments are **allocated by population density** (they follow customers) | dashboard *Sector* and overview pages | **Evaluation:** QA conservation check, as in step 9. The implied rates (~1 bar or eatery per 100–150 residents in rural Nasho) look plausible for small cabarets, but the bar/restaurant profile (2.1 kWh/day, with a fridge) is an upper bound for them. **Extension:** a rural cabaret profile; RDB accommodation register; checking the overlap with labelled shops (`decisions.md`) |
| 11 | **Grid connection (new, 2026-10-12).** Who is likely connected, to move from potential to expected demand. | DHS 2025 households (`hv206` "has electricity") and displaced cluster GPS (restricted); 13 open neighbourhood features from steps 1–3 | `grid_connection`: P(connected) for **all 6,434,247 buildings**; model, scores and figures in `data/processed/grid_model/` and `reports/demand/grid_model.md` | DHS shifts cluster locations by up to 2 km (urban) or 5 km (rural), so features are building-weighted means over H3 rings of that size. The model learns a **neighbourhood rate**, and neighbouring buildings share a value. Two rows per cluster (connected, not) weighted by household weights. Logistic regression chosen over gradient boosting on held-out log loss. No DHS variable is an input; cluster locations never leave `data/restricted/` | `B4_grid_raw_features.png`, `B4_grid_label.png`, `B4_grid_model.png`, `B4_grid_predictions.png` | **Evaluation:** 5-fold CV grouped by district: **AUC 0.75**, cluster R² 0.45 (urban/rural rate alone 0.68; a perfect neighbourhood model 0.89). Independent check: district predictions vs EICV7 2024 grid lighting, **r = 0.93**. Population density dominates. **Extension:** apply it (expected demand = potential × P); add night lights; REG customer counts per village would allow a much sharper model |
| 12 | **Aggregation and dashboard bundle.** The five challenge metrics per building and per area, in a form a browser can read. | Steps 1–11 | Static JSON/GeoJSON bundle (`pixi run dashboard-data`; full 189 MB in `data/dashboard/v1`, committed sample 16 MB); contract `dashboard/DATA_CONTRACT.md`; design prototype (claude.ai canvas) | Building metrics = weights × profile curves; area peaks are coincident (the peak of the summed curve). Area figures come in **three kinds, each with its own colour**: *reported* for the area (census), *sum over buildings*, *unlocated* (area only). Unlocated productive use and services are reported next to building totals, never added into them silently | the dashboard prototype | **Evaluation:** a test recomputes every sample building from the published formulas. **Extension:** national roll-out (needs step 5 at national scale), PMTiles, PostGIS release |
| 13 | **Quality gates.** Nothing moves on without passing. | all tables | `reports/qa/*.md` (9 reports, all PASS); 107 tests | `pixi run qa` checks schemas (pandera), coverage, ranges and duplicates, plus a demand check (one id set across the demand files; census conservation; one probability per building; archetype status vs approvals). `pixi run demand` runs steps 6–13 in order | – | Restricted-data rule: microdata, cluster GPS and the census workbook are never committed or published |

## 3. Per district

From `pixi run status-figures` (`reports/status/district_table.md`). "Usable tag" is defined in step 3. LLM labels
exist only where we labelled: the three deep dives plus the 400 gold candidates.

| District | Buildings | Google / Microsoft / OSM | With height | Median (p90) height | With usable tag | LLM-labelled |
|---|---|---|---|---|---|---|
| Gasabo | 415,079 | 94.3% / 4.1% / 1.6% | 78.4% | 3.8 m (5.6 m) | 1.6% | 12 |
| Bugesera | 327,199 | 94.4% / 5.5% / 0.0% | 64.3% | 2.9 m (4.8 m) | 0.3% | 3 |
| Nyagatare | 315,504 | 86.7% / 4.3% / 9.1% | 66.3% | 3.1 m (4.7 m) | 0.0% | 3 |
| Gicumbi | 265,395 | 94.2% / 3.2% / 2.5% | 66.3% | 3.2 m (4.9 m) | 0.0% | 2 |
| Kamonyi | 233,842 | 93.1% / 6.8% / 0.1% | 79.3% | 3.3 m (5.1 m) | 0.0% | 3 |
| Kirehe | 232,044 | 93.3% / 6.6% / 0.1% | 79.4% | 3.4 m (4.9 m) | 0.0% | 15,979 |
| Rwamagana | 231,602 | 94.0% / 6.0% / 0.0% | 76.7% | 3.3 m (5.0 m) | 0.2% | 1 |
| Musanze | 231,142 | 86.9% / 10.7% / 2.4% | 73.5% | 3.5 m (5.4 m) | 1.3% | 8,666 |
| Rusizi | 229,783 | 82.3% / 11.9% / 5.8% | 53.9% | 3.0 m (5.0 m) | 0.5% | 2 |
| Kicukiro | 225,351 | 94.7% / 3.0% / 2.3% | 79.6% | 3.8 m (5.8 m) | 3.4% | 7 |
| Gatsibo | 223,049 | 89.4% / 10.3% / 0.3% | 74.7% | 3.1 m (4.6 m) | 0.1% | 5 |
| Rubavu | 212,058 | 93.6% / 5.6% / 0.7% | 79.9% | 3.8 m (5.6 m) | 0.4% | 4 |
| Rulindo | 211,985 | 95.5% / 4.4% / 0.1% | 77.6% | 3.4 m (5.1 m) | 0.1% | 4 |
| Nyamasheke | 210,077 | 88.4% / 11.1% / 0.5% | 66.0% | 3.6 m (5.6 m) | 0.1% | 4 |
| Nyanza | 209,942 | 92.0% / 7.9% / 0.1% | 75.9% | 3.1 m (4.7 m) | 0.1% | 2 |
| Gakenke | 209,407 | 83.4% / 16.5% / 0.1% | 69.4% | 3.6 m (5.3 m) | 0.4% | 3 |
| Kayonza | 203,391 | 93.7% / 6.2% / 0.1% | 74.4% | 3.1 m (4.7 m) | 1.4% | 4 |
| Ngoma | 200,820 | 91.3% / 8.7% / 0.0% | 52.8% | 2.8 m (4.6 m) | 0.1% | 4 |
| Huye | 199,208 | 92.4% / 5.0% / 2.7% | 80.4% | 3.4 m (5.0 m) | 1.0% | 5 |
| Karongi | 194,746 | 87.9% / 11.3% / 0.8% | 59.0% | 3.3 m (5.3 m) | 0.2% | 2 |
| Ruhango | 193,229 | 89.0% / 10.0% / 1.0% | 75.0% | 3.0 m (4.8 m) | 0.3% | 3 |
| Muhanga | 191,769 | 84.7% / 13.1% / 2.2% | 63.8% | 3.1 m (5.0 m) | 0.3% | 3 |
| Burera | 181,985 | 91.6% / 6.3% / 2.1% | 80.1% | 3.7 m (5.2 m) | 0.1% | 2 |
| Nyamagabe | 172,375 | 90.7% / 6.2% / 3.2% | 76.1% | 3.3 m (5.0 m) | 0.3% | 3 |
| Ngororero | 167,685 | 77.2% / 18.6% / 4.2% | 52.7% | 3.0 m (4.9 m) | 0.4% | 1 |
| Gisagara | 165,555 | 94.9% / 3.5% / 1.7% | 83.1% | 3.3 m (4.8 m) | 0.5% | 3 |
| Rutsiro | 163,091 | 83.5% / 12.2% / 4.4% | 63.9% | 3.5 m (5.4 m) | 0.2% | 0 |
| Nyarugenge | 142,873 | 93.9% / 5.3% / 0.8% | 86.5% | 3.7 m (5.4 m) | 2.6% | 19,825 |
| Nyaruguru | 137,532 | 86.1% / 5.5% / 8.4% | 70.6% | 3.3 m (5.0 m) | 1.1% | 1 |
| Nyabihu | 135,696 | 87.5% / 8.7% / 3.8% | 67.1% | 3.2 m (5.1 m) | 0.2% | 0 |
| **Rwanda** | **6,433,414** | 90.4% / 7.6% / 2.0% | 71.6% | 3.3 m (5.1 m) | 0.6% | 44,556 |

## 4. Deep-dive results (potential demand if grid-connected; draft)

| | Nyamirambo (Kigali) | Muhoza (Musanze) | Nasho (Kirehe) |
|---|---|---|---|
| Buildings / modelled | 21,126 / 19,460 | 32,538 / 8,620 (labelled part) | 17,374 / 15,876 |
| **Buildings' annual energy** (sum over buildings) | 5.45 GWh (top class only 4.92) | 2.35 GWh (2.14) | 1.88 GWh (1.68) |
| Area peak (coincident) | 1,917 kW at 20:00 | 831 kW at 20:00 | 855 kW at 20:00 |
| Productive use: census, district (reported) | 1,465 (Nyarugenge) | 868 (Musanze) | 516 (Kirehe) |
| Productive use: unlocated (area only) | 241 establishments, 0.21 GWh | 164, 0.19 GWh | 62 mills, 0.59 GWh |
| Services: tagged buildings (sum over buildings) | 6 | 69 | 0 |
| Services: unlocated (area only) | 562 establishments, 0.35 GWh | 526, 0.33 GWh | 251, 0.18 GWh |
| Mean P(grid connection), model | 0.98 | 0.87 | 0.40 |

The unlocated rows are *not* included in the buildings' totals: add them to get the area's potential demand
(Nasho: 1.88 + 0.59 + 0.18 = 2.65 GWh/yr).

## 5. Human gates

| Gate | Status |
|---|---|
| Codebook (9 use classes) | approved |
| Gold labels (173 of 400) | done; an uncertain reference (owner's note, 2026-10-12) |
| Evidence review | approved 2026-10-11 (blanket, backed by automated checks) |
| Building → profile mapping | approved 2026-10-11; household ids renamed 2026-10-12 |
| Archetype assumptions | **signed off 2026-10-12** |
| Class-probability rule (0.10) and the productive-use method | **approved 2026-10-12** |
| Productive-use trade shares, eligibility and candidate weights | pending (`decisions.md`) |
| Services section splits (97/3, 60%) | pending (`decisions.md`) |
| Applying the grid model to demand | pending |
| Licence review before publishing | pending |

## 6. Mid-point readiness (Oct 15–16)
Ready. Every step above runs from one command per step, passes QA and is documented: the
[datasheet](docs/datasheet.md), [data sources](docs/data_sources.md), [AI use](docs/ai_use.md) and the
[data contract](dashboard/DATA_CONTRACT.md). API spend is ~$157 of the $170 cap; all responses are cached, so
re-runs cost nothing. The open items for the finals are in [`decisions.md`](decisions.md).

## 7. Data still to acquire
The API credit has expired, so new documents are read and curated directly in the coding session (with a quote
and page for every value, and human review), not through the Claude API.

| Priority | What | Why | How |
|---|---|---|---|
| 1 | ~~**World Bank MTF Rwanda microdata**~~ (received 2026-10-11) | appliance **usage hours** by tier: the biggest gap in the RAMP profiles (today mostly assumptions) | repo owner (registered; licensed download) |
| 1 | **REG data**: customers and consumption by tariff category and village; hourly feeder/transformer loads (Kigali, Rusizi); MV/LV network GIS | demand validation; cooling attribution; better grid distances than Gridfinder | request via the organisers / REG |
| 1 | **Productive-use sources**: ~~NREL/USAID PUE in African Micro-Grids (2018)~~, ~~GIZ PV for Productive Use catalogue (2016)~~, ~~ESMAP Mini Grids (2022, text)~~, ~~Power Africa PUE 2020 Rwanda catalogue~~ (Internet Archive copy; 37 values curated with verified quotes); AMDA benchmarking, more Efficiency for Access briefs | rated power, hours and duty cycles for mills, welding, tailoring, refrigeration, pumping | in the coding session |
| 1 | **Health-post energy use** (MoH / WHO / SEforALL Powering Healthcare; any metered posts) | the health profile is built from MTF health *centres* and is far above what a health post uses | public reports; ask the organisers |
| 2 | **EICV5 questionnaire / value labels** (NISR microdata portal) | the CSV export has no labels for the durable-goods item codes, so appliance ownership in 2017 can't be read yet | repo owner (NISR portal documentation) |
| 2 | **Published RAMP input sets** (RAMP repo examples; open-access RAMP studies in sub-Saharan Africa) | ready-made, peer-reviewed user-type parameters to compare with ours | public: fetched in the coding session |
| 2 | **EnAccess MicroPowerManager** anonymised meter data | real mini-grid customer load curves for validation | ask the organisers (hackathon partner) |
| 2 | **Official facility registries**: MINEDUC schools, MoH health facilities (with coordinates) | better institutional labels than OSM/healthsites | ask the organisers / ministries; Giga school map |
| 3 | **Kigali Master Plan zoning GIS** | commercial/industrial zones improve Kigali building-use labels | City of Kigali / RLMUA geoportal |
| 3 | **NISR**: sector-level 2022 census households; EICV7 microdata | sector-level completeness checks; energy-use detail | NISR (open tables; microdata needs registration) |
| 3 | NASA Earthdata login (VIIRS Black Marble night lights) | optional check of electrification / activity | repo owner (free account) |

## 8. Questions for the organisers
- Is there a preferred AOI, or validation data (metered consumption, MicroPowerManager exports) we could use?
  Seasonal or hourly feeder loads for Kigali and the Rusizi valley would let us test whether hot-afternoon load is
  really cooling.
- Is LLM-assisted labelling acceptable if it's disclosed and measured against human labels as above?
- Any guidance on imagery? Our pipeline deliberately uses only openly licensed vector data. Human gold labellers
  consulted commercial imagery for interpretation only; nothing was traced from it.
