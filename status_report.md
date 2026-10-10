# Rooftops → Load Curves: status report

*OSEAS26 challenge: building footprints → energy demand · repo `esemsc-as4623/peek-load` · updated 2026-10-10*

## 1. Plan in one paragraph
We turn Rwanda's 6.4 million open building footprints into a **curated, provenance-tracked building
dataset**. Each building gets its shape, height, growth, surroundings, climate and a **building-use label**.
Alongside it sits an **evidence-backed library of RAMP appliance profiles**, where every wattage, usage hour
and ownership share traces to a quoted source or a reviewed assumption. Together they let us model each
building's load profile as a *distribution* (median and 90th-percentile peak, annual kWh) rather than a single
number. Phase 1 (now, through the mid-point review on Oct 15–16) is **data only**: gathering, curation, EDA and
preprocessing. Modelling follows in phase 2.

## 2. Done

### Building data (all of Rwanda, 6,434,247 buildings; each table passes automated QA)
| Table | What it holds | Notes |
|---|---|---|
| `buildings_base` | footprints (Google V3 + Microsoft + OSM via VIDA), area, shape, orientation, H3 index | quality problems are **flagged, not dropped**: 12.6% low detector confidence, 0.5% tiny slivers, 2,186 nested duplicates; 87.2% unflagged |
| `building_height` | height, estimated floors, floor area, first year seen, yearly presence 2016–2023 | heights for 71.6% of buildings (median 3.3 m). Floors come from **height cut-points fitted on OSM floor counts and tested on a held-out half**: 1 floor below 6.0 m, 2 floors below 8.5 m, then +1 per 3 m. That gives 83% exact for 1-storey buildings (MAE 0.20 floors, against 0.41 with a single 3.4 m storey height); the source under-estimates 3+ storeys. First-seen year uses presence ≥ 0.5 (33% of buildings get a year; 0.3 would give 52%) and is marked experimental |
| `building_context` | admin area down to village, OSM/Overture/registry tags, distance to roads and power lines, nearby-place counts, settlement type (GHSL), population density, relative wealth, elevation | direct use tags are rare: only ~14k of 6.4M buildings have an informative tag |
| `climate_h3` | ERA5-Land hourly temperature downscaled with elevation, cooling degree-days (18/22/24 °C), typical day, solar irradiance, cooling class | 4,034 areas; fitted lapse rate −6.05 K/km; Kigali 20.5 °C. Cooling class from the 95th-percentile daily maximum: low < 26 °C, medium 26–29, high ≥ 29 (2.30M / 3.38M / 0.76M buildings). **Checked against NASA POWER** (below) |

### Evidence for appliance profiles
- 18 public reports (2,406 pages: DHS 2019 and 2025, World Bank MTF, NISR census and EICV7, tariffs, Columbia/REG
  consumption studies, appliance studies) → **3,076 extracted parameters**, 81% with a quote verified word-for-word
  in the source page.
- Productive-use appliances (mills, welders, sewing, pumps, salons, phone charging, carpentry): 163 parameters
  from web research, marked *to verify at source*.
- DHS 2015/2019/2025 public indicators, plus the Rwanda 2025 DHS microdata (restricted; held locally, never
  redistributed).
- Validation anchors, never used as model inputs: households by district (2022 census), grid lighting by
  district, residential kWh distributions, 2025 tariffs, appliance-ownership priors.
- RAMP appliance-profile template plus one worked example that runs in RAMP.

### Building-use labelling (pilot)
- Approved 9-class codebook: residential, shop-house, commercial, institutional, religious, productive use,
  industrial/warehouse, ancillary, unknown.
- Stratified sample of 5,878 buildings: the 3 deep-dive sectors (Nyamirambo in Kigali, Muhoza in Musanze,
  Nasho in Kirehe) plus a national sample. 400 of them form the gold set.
- **Gold labels by a human (173 so far)**, most made with satellite imagery or Street View.
- Claude labels each building from a *context card* built only from open vector data. Three card versions:
  - **v1**: basic attributes;
  - **v2**: adds land use, road frontage, shape, relative size and distance to markets;
  - **v3**: adds the regularity of the surrounding buildings, how unusual this building is for its neighbourhood,
    and the tags and labels of neighbouring buildings.

  The six v1 configurations (Haiku, Sonnet or Opus × text or map) were compared on the 400 gold buildings.
- **Findings** (173 gold labels):
  - **v2 is the best version.** Agreement with the human labels: Haiku 29% → 39%, Sonnet 38% → 43%, Opus 39% → 42%.
    Claude **cites more context** (3.8 → 5.4 distinct cues per answer).
  - **v3 did not improve on v2** (Haiku 38%, Sonnet 42%). The neighbourhood features mostly reinforce "like its
    neighbours", which is residential: a useful negative result.
  - **Haiku v2 is the recommended configuration** (cheapest within 0.03 macro-F1 of the best), and it is what
    labelled the deep-dive coverage.
  - Overall agreement stays modest because open vector data rarely carries the use signal a human sees in imagery.
    That is itself a useful result for the energy-access community.
  - The card map image adds almost nothing over the text.
- Coverage run done: **44,156 deep-dive buildings labelled** (all of Nyamirambo and Nasho, 8.7k in Muhoza) with
  Claude Haiku on the v2 cards through the Batch API. The mix is about 69% residential, 28% ancillary
  (kitchens, latrines, stores), 1–2% commercial, institutional and shop-house, and rural Nasho has almost no
  commercial buildings. Every label carries class probabilities and a one-line justification naming its cues.

### Engineering
- Reproducible pixi environment.
- Every raw file in a provenance manifest (URL, checksum, licence).
- Table contracts with automated QA gates, and 80+ tests.
- Every LLM call cached, so it is reproducible and re-runs cost nothing.
- Hard spending cap; AI use disclosed in `docs/ai_use.md`.
- Total API spend: **~$157** of a $170 cap (labelling ~$110, evidence ~$33, web research ~$10). All responses are
  cached locally, so re-running costs nothing.

### Cooling demand: independent check (NASA POWER, hourly 2019–2024)
| Site | CDD22 per year, POWER / ERA5-Land | 95th-pct. daily max, POWER / ERA5-Land | Hours ≥ 26 °C per year | Hours ≥ 28 °C per year |
|---|---|---|---|---|
| Kigali | 26 / 28 | 29.4 / 27.9 °C | 832 | 202 |
| Kirehe/Nasho (east) | 5 / 37 | 28.4 / 28.2 °C | 512 | 95 |
| Musanze (cool reference) | 0 / 0 | 25.2 / 24.8 °C | 18 | 0 |
| **Bugarama valley (hottest, ~960 m)** | **714 / 713** | **31.3 / 31.4 °C** | **2,506** | **1,232** |

- **Kigali has marginal cooling demand.** Sustained heat is small (about 26 CDD22 a year), but there are about
  800 hot hours a year, concentrated at 11:00–15:00 in the dry seasons (Feb–Mar, Aug–Oct). That points to
  afternoon fan use, not air conditioning.
- **The Rusizi/Bugarama valley** (south-west) has real, year-round cooling need.
- **The eastern lowlands also carry sustained heat in the ERA5-Land table.** Ngoma, Kirehe and Bugesera have
  district medians of 100–116 CDD22 a year. Nationally, 11.6% of buildings are in areas above 100 CDD22 a year
  and 2.2% above 200. NASA POWER at Nasho (east) shows much less (5 against 37 CDD22), so the eastern values stay
  **provisional** until the Copernicus rebuild and, ideally, station data confirm them.
- Caveat: POWER's ~0.5° grid is coarse, so it is shifted to each cell's elevation. Bugarama's agreement is partly
  built in by that shift (the POWER cell's mean elevation is 1,872 m); Kigali's (only a 127 m shift) is a genuinely
  independent check.
- This measures the **climate driver**. Attributing *measured* electricity demand to cooling needs metered load
  by season and hour, which we don't have yet (see the questions below).
- Figures: `reports/figures/A3_cooling_check.png` (time of day and month), `A3_cdd22_per_year.png`,
  `A3_cooling_class.png`, `A3_t2m_mean_c.png`; notebooks `03_cooling_check.py`, `02_report_figures.py`.

## 3. In progress
- **Climate from Copernicus CDS**: ERA5-Land hourly 2019–2025 (temperature, dewpoint, solar radiation,
  precipitation, wind) plus a 1991–2020 daily baseline, downloading on the server (resumable). Then the climate
  table is rebuilt: Copernicus licence, humidity and heat index, hourly 0.1° sunlight, rainfall seasonality, and
  cooling classes on a 30-year normal.
- More gold labels: 174 of 400, plus 50 repeats to measure the labeller's own consistency.

## 4. Mid-point readiness (Oct 15–16)
**Substantively ready.** All six building tables pass QA, the evidence base, the labelling pilot and coverage, and the
cooling check are done, and every step is reproducible and documented: [`docs/datasheet.md`](docs/datasheet.md),
[`docs/data_sources.md`](docs/data_sources.md), [`docs/ai_use.md`](docs/ai_use.md). Remaining polish (no API cost):
2–3 more EDA figures and the labelling confusion-matrix figure.

## 5. To do

### Before the mid-point review (Oct 15–16)
1. ~~Final labelling evaluation and configuration decision~~ (done: Haiku v2). Still to do: the confusion-matrix
   figure for the review.
2. Exploration notebooks (01 footprints and 03 cooling done):
   - footprint completeness against census households;
   - growth 2016–2023;
   - height and floor distributions;
   - where tags exist (a bias audit);
   - climate and cooling;
   - grid distance against wealth.
3. ~~Datasheet~~ (done: `docs/datasheet.md`).
4. Human review of the appliance-profile parameters and the productive-use specs; licence check on the documents.

### After the mid-point (modelling phase)
5. Appliance profiles per building class and context (urban/rural, grid/off-grid, wealth), with ownership from
   the DHS 2025 microdata and parameters from the evidence table.
6. RAMP Monte Carlo runs per building class → per-building median and 90th-percentile peak, annual kWh,
   load-profile shape.
7. Productive-use viability score (road access, market proximity, density, grid distance).
8. Validation against REG/Columbia consumption distributions and district electrification rates.
9. Deliverable: an energy-classified building GeoParquet / PostGIS table plus a map viewer.

## 6. Data still to acquire (for phase 2)
The API credit has expired, so new documents are read and curated directly in the coding session (with a quote
and page for every value, and human review), not through the Claude API.

| Priority | What | Why | How |
|---|---|---|---|
| 1 | **World Bank MTF Rwanda microdata** | appliance **usage hours** by tier: the biggest gap in the RAMP profiles (today mostly assumptions) | repo owner (registered; licensed download) |
| 1 | **REG data**: customers and consumption by tariff category and village; hourly feeder/transformer loads (Kigali, Rusizi); MV/LV network GIS | demand validation; cooling attribution; better grid distances than Gridfinder | request via the organisers / REG |
| 1 | **Productive-use sources** replacing the unavailable Power Africa catalogue: NREL/USAID *Productive Use of Energy in African Micro-Grids* (2018), ESMAP *Mini Grids for Half a Billion People* (2022), AMDA benchmarking reports, GIZ *Photovoltaics for Productive Use Applications*, more Efficiency for Access briefs (pumps, cold rooms, sewing) | rated power, hours and duty cycles for mills, welding, tailoring, refrigeration, pumping | public PDFs: fetched and curated in the coding session |
| 2 | **Published RAMP input sets** (RAMP repo examples; open-access RAMP studies in sub-Saharan Africa) | ready-made, peer-reviewed user-type parameters to compare with ours | public: fetched in the coding session |
| 2 | **EnAccess MicroPowerManager** anonymised meter data | real mini-grid customer load curves for validation | ask the organisers (hackathon partner) |
| 2 | **Official facility registries**: MINEDUC schools, MoH health facilities (with coordinates) | better institutional labels than OSM/healthsites | ask the organisers / ministries; Giga school map |
| 3 | **Kigali Master Plan zoning GIS** | commercial/industrial zones improve Kigali building-use labels | City of Kigali / RLMUA geoportal |
| 3 | **NISR**: sector-level 2022 census households; EICV7 microdata | sector-level completeness checks; energy-use detail | NISR (open tables; microdata needs registration) |
| 3 | NASA Earthdata login (VIIRS Black Marble night lights) | optional check of electrification / activity | repo owner (free account) |

## 7. Questions for the organisers
- Is there a preferred AOI, or validation data (metered consumption, MicroPowerManager exports) we could use?
  Seasonal or hourly feeder loads for Kigali and the Rusizi valley would let us test whether hot-afternoon load is
  really cooling.
- Is LLM-assisted labelling acceptable if it's disclosed and measured against human labels as above?
- Any guidance on imagery? Our pipeline deliberately uses only openly licensed vector data. Human gold labellers
  consulted commercial imagery for interpretation only; nothing was traced from it.
