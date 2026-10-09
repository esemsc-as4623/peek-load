# Rooftops → Load Curves: status report

*OSEAS26 challenge: building footprints → energy demand · repo `esemsc-as4623/peek-load` · updated 2026-10-09*

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
| `building_height` | height, estimated floors, floor area, first year seen, yearly presence 2016–2023 | heights for 71.6% of buildings (median 3.3 m); storey height calibrated on OSM floor counts (3.4 m); floors within ±0.4 for 1-storey and ±0.6 for 2-storey buildings, but the source under-estimates 3+ storeys |
| `building_context` | admin area down to village, OSM/Overture/registry tags, distance to roads and power lines, nearby-place counts, settlement type (GHSL), population density, relative wealth, elevation | direct use tags are rare: only ~14k of 6.4M buildings have an informative tag |
| `climate_h3` | ERA5-Land hourly temperature downscaled with elevation, cooling degree-days, typical day, solar irradiance, cooling class | 4,034 areas; fitted lapse rate −6.05 K/km; Kigali 20.5 °C. **Rwanda has almost no cooling demand**: only 4.7% of buildings see ≥10 cooling degree-days a year above 24 °C, so the cooling class is a relative "fans plausible" proxy |

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
- **Gold labels by a human (160 so far)**, 87% made with satellite imagery or Street View.
- Claude labels each building from a *context card* built only from open vector data. Three card versions:
  - **v1**: basic attributes;
  - **v2**: adds land use, road frontage, shape, relative size and distance to markets;
  - **v3**: adds the regularity of the surrounding buildings, how unusual this building is for its neighbourhood,
    and the tags and labels of neighbouring buildings.

  The six v1 configurations (Haiku, Sonnet or Opus × text or map) were compared on the 400 gold buildings.
- **Findings so far:**
  - **v2 raises agreement with the human labels** (Haiku 28% → 37%, Sonnet 35% → 42%) and **makes Claude cite more
    context** (3.8 → 5.4 distinct cues per answer).
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

## 3. In progress
- v3 cards on the gold set (Haiku and Sonnet), to measure whether neighbourhood regularity, out-of-distribution
  scores and neighbour labels improve labels.
- More gold labels: 160 of 400, plus 50 repeats to measure the labeller's own consistency.

## 4. To do

### Before the mid-point review (Oct 15–16)
1. Final labelling evaluation (v1, v2 and v3 against gold; variety and use of context), the confusion matrix, and
   a decision on the labelling configuration.
2. Exploration notebooks:
   - footprint completeness against census households;
   - growth 2016–2023;
   - height and floor distributions;
   - where tags exist (a bias audit);
   - climate and cooling;
   - grid distance against wealth.
3. Datasheet for the dataset, covering sources, licences, known limits (height compression for tall buildings,
   the experimental first-seen year, the vector-only labelling limit), and the AI-use disclosure.
4. Human review of the appliance-profile parameters and the productive-use specs; licence check on the documents.

### After the mid-point (modelling phase)
5. Appliance profiles per building class and context (urban/rural, grid/off-grid, wealth), with ownership from
   the DHS 2025 microdata and parameters from the evidence table.
6. RAMP Monte Carlo runs per building class → per-building median and 90th-percentile peak, annual kWh,
   load-profile shape.
7. Productive-use viability score (road access, market proximity, density, grid distance).
8. Validation against REG/Columbia consumption distributions and district electrification rates.
9. Deliverable: an energy-classified building GeoParquet / PostGIS table plus a map viewer.

## 5. Questions for the organisers
- Is there a preferred AOI, or validation data (metered consumption, MicroPowerManager exports) we could use?
- Is LLM-assisted labelling acceptable if it's disclosed and measured against human labels as above?
- Any guidance on imagery? Our pipeline deliberately uses only openly licensed vector data. Human gold labellers
  consulted commercial imagery for interpretation only; nothing was traced from it.
