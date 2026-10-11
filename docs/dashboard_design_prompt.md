# Prompt for Claude Design: the "Rooftops → Load Curves" dashboard

*How to use: paste everything below the line into Claude Design. Numbers and statuses are as of 2026-10-11
(`status_report.md`). If you regenerate it later, update section 7 (status) and the appendices first.*

---

## 0. Your role and the deliverable

You are designing (and, as far as you can, building as a clickable, interactive prototype) a web dashboard for
**Rooftops → Load Curves**, an entry to the OSEAS26 hackathon, EnAccess challenge *"From building footprints to
energy demand"*, set in **Rwanda**. The project has two halves:
1. an end-to-end, reproducible GIS workflow;
2. the resulting building-level dataset (GeoParquet tables keyed by `bldg_id`).

The dashboard is how judges, planners and other teams will see both. It must make three things obvious:
- **what we estimated**, per building and per area;
- **how we estimated it**: the workflow, step by step, with a full data-architecture diagram;
- **what it rests on**: every data source, cited, with its licence, plus how sure we are (status, uncertainty,
  validation).

Clarity beats decoration. Every number on screen must be explainable in one click: what it is, its unit, how it
was computed, its source and its status.

**You receive three things with this prompt:**
1. this brief;
2. `dashboard/DATA_CONTRACT.md`, which specifies how the data is stored, served and loaded, every file and field,
   and the client-side formulas (copied as Appendix C, in case the file isn't attached);
3. the **sample data bundle** `dashboard/sample_data/v1/`: real files in exactly the production schema, covering
   the whole country at hexagon/admin level and one small box of buildings per deep-dive sector.

Build the prototype against the sample bundle, loading everything relative to one `DATA_BASE` constant.
Pointing that constant at the full bundle must be the only change needed to go live. Both are in the project
repository (github.com/esemsc-as4623/peek-load); if you can't open it, ask for the files to be attached.

## 1. The challenge (show this verbatim on the About page)

> This end-to-end workflow would leverage existing building footprint datasets to generate high-resolution,
> spatially explicit energy demand intelligence. The process could integrate building footprint data with
> complementary geospatial and contextual information to classify structures by use type, such as residential,
> commercial, institutional, or productive-use buildings. Appliance-level load profiles are then modeled and
> forecast for each building category using tools such as RAMP (Remote Appliance Load Profiles), producing
> temporally resolved demand profile estimates based on climate conditions, appliance ownership data, and
> behavioral assumptions that capture daily and seasonal variations in energy use. The resulting analysis creates a
> more detailed understanding of current and potential energy needs at the individual building level, enabling
> more accurate electrification planning, demand aggregation, and infrastructure investment decisions.

The challenge names five outputs per building. They are the spine of the dashboard: show them as the five headline
metrics everywhere (national cards, area summaries, building drawer), always in this order and with these names.
1. **Estimated Peak Load**
2. **Annual Energy Consumption**
3. **Cooling Demand Category** (Low, Medium or High)
4. **Load Profile Characteristics**
5. **Productive-Use Viability**

Exact definitions are in section 5. Use them in tooltips and on the Methods page.

## 2. Who uses it and what they ask

| User | Typical question | Where they get the answer |
|---|---|---|
| Hackathon judge | What did they build, is it credible, and can I trace it? | Overview → Workflow in depth → Validation → Sources |
| Electrification planner (REG, EDCL, district) | Where is demand, how big is the evening peak, where should the grid or a mini-grid go? | National map → sector deep dive → aggregate a selection |
| Mini-grid developer | If I serve these 300 buildings, what load curve, peak and kWh/year do I size for? Any anchor (productive-use) customers? | Deep dive → draw an area → aggregated profile + PU candidates → export |
| Researcher / other team | Which data and assumptions, which licences, can I reuse it? | Sources & citations, Workflow, Downloads |

## 3. Information architecture (pages)

Use a persistent left navigation (collapsible on mobile) with these pages, a global **area selector** (National →
Province → District → Sector, plus the three deep-dive shortcuts), and a global **status legend** (section 7).

### 3.1 Overview: national (macro) view
- **Map** of Rwanda, using **H3 resolution-7 hexagons** (4,034 cells, ~5 km² each) and admin boundaries
  (provinces, districts, sectors). A layer switcher chooses what the hexagons show:
  - building count and footprint density;
  - Cooling Demand Category;
  - mean temperature and the 95th-percentile daily maximum;
  - cooling degree-days (base 22 °C);
  - solar irradiance (GHI);
  - population density;
  - relative wealth (Meta RWI);
  - distance to the medium-voltage grid (Gridfinder);
  - GHSL settlement class;
  - **estimated demand** (Annual Energy Consumption and Peak Load). Nationally this layer is **Planned**: show its
    empty state ("national roll-out pending: currently estimated for the three deep-dive sectors") with the three
    deep dives highlighted where values exist. Never fill it with invented numbers.
- **KPI cards** (top):
  - buildings mapped: 6,434,247;
  - buildings with a height: 4,603,981;
  - climate cells: 4,034, by cooling class (Low 1,484 · Medium 1,672 · High 878);
  - deep-dive buildings labelled and modelled: 44,556;
  - RAMP profiles: 17;
  - data sources: 27 datasets + 26 evidence documents.

  Each card shows its status chip.
- **The three deep dives** as cards with a thumbnail map. Each has a one-line description, building count, cooling
  category, and its preview Annual Energy (section 3.2). Clicking flies the map in.
- **District table** (sortable, exportable): buildings, footprint area, population, share of buildings in each
  cooling class, median distance to the MV grid, mean RWI, and demand (once available).
- A short **"How to read this"** strip: three sentences on what the dashboard estimates, and the
  "potential demand if connected" caveat (section 5).

### 3.2 Sector deep dive (the zoomed-in view)
Three sectors were chosen to contrast settings. Use these descriptions in the UI:

| Sector (district) | Setting | Buildings | Labelled + modelled | Cooling category of its buildings |
|---|---|---|---|---|
| **Nyamirambo** (Nyarugenge, City of Kigali) | dense mixed-use urban sector with shop-houses and informal areas | 21,126 | 19,822 | Medium (100%) |
| **Muhoza** (Musanze) | secondary city at ~1,850 m; cool, mixed commercial/residential core | 32,538 | 8,658 (partial) | Low (97%), Medium (3%) |
| **Nasho** (Kirehe) | warm eastern lowland (~1,350 m), rural, solar irrigation scheme (productive use) | 17,374 | 15,976 | High (70%), Medium (30%) |

- **Map** with every building footprint as a polygon. A "colour buildings by" control offers:
  - building use (class);
  - Annual Energy Consumption;
  - Estimated Peak Load;
  - Cooling Demand Category;
  - Productive-Use Viability;
  - label confidence;
  - height / floors;
  - first-seen year;
  - footprint source.

  Explicit legends with units and fixed colour limits (section 8).
- **Layer panel** (toggle + opacity), grouped:
  - **Context:** roads (OSM, major/minor), land use (OSM), points of interest (OSM + Overture places), health
    facilities (healthsites.io), admin boundaries down to village.
  - **Energy:** predicted MV lines (Gridfinder), distance-to-grid raster/hexes.
  - **Population & wealth:** WorldPop 2023, Meta RWI tiles.
  - **Built environment:** GHSL settlement class, building heights.
  - **Climate:** H3 cells (mean temperature, cooling class, CDD22, GHI).
  - **Terrain:** elevation hillshade (Copernicus DEM).
- **Area summary panel** (right):
  - the five headline metrics for the sector;
  - building mix by class (stacked bar);
  - the sector's **aggregated 24-hour load curve**, stacked by profile (households, shops, institutions, productive
    use), with its coincident peak marked;
  - Annual Energy with a p10–p90 band.
- **Aggregate a selection** (a key feature: "demand aggregation" in the abstract). The user can:
  - draw a polygon, use a radius, lasso, or click hexes or villages;
  - get for that selection: number of buildings by class, the aggregated load curve, coincident peak (kW), annual
    energy (MWh/yr), load factor, evening share, PU candidates in the selection;
  - **export** CSV (per building), GeoJSON (footprints + metrics) and PNG (chart).

  Show a **coincidence note**: area peaks are coincident (sum of hourly profiles), so they are less than the sum of
  single-building peaks.
- **Compare**: pin two areas, or two buildings, side by side (metrics + overlaid load curves).

### 3.3 Building detail drawer (click any footprint)
Header: `bldg_id`, sector › cell › village, coordinates (copy button), footprint source chip
(Google / Microsoft / OSM).
1. **The five headline metrics** as a card row. Each has a value, unit, uncertainty band and status chip.
2. **Building:**
   - footprint area (m²), perimeter, compactness, orientation;
   - height (m), estimated floors, gross floor area;
   - first-seen year (marked *experimental*);
   - a small sparkline of yearly presence, 2016–2023.
3. **Use classification:**
   - a bar chart of class probabilities over the 9 classes (section 6.2), with the top class highlighted and its
     confidence;
   - the cited context cues (short text from the labeller);
   - nearby tags (OSM/Overture);
   - a note that labels are model estimates with measured accuracy (link to Validation).
4. **Profile mix:** how the class probabilities and context became RAMP profile weights. Example: 0.62 × urban
   household (upper), 0.21 × kiosk/shop. Show it as a small Sankey or weighted list. Include the rule used (urban
   or rural; RWI above or below the setting median).
5. **Load profile:**
   - a 24-hour curve (W) of the building's expected load (Σ weight × profile), stacked by profile;
   - weekday/weekend toggle;
   - key characteristics (section 5.4) beside it;
   - a seasonal (monthly) view, as a **Planned** placeholder.
6. **Climate:**
   - the building's H3 cell: mean temperature, 95th-percentile daily maximum, CDD22, GHI;
   - the cooling class and the rule that produced it;
   - the diurnal temperature curve (24 hourly means).
7. **Productive-use viability:** score, category, and a component breakdown (section 5.5), each with its source.
8. **Context:**
   - distance to major road, any road and the MV grid;
   - POIs within 50 / 100 / 250 m;
   - population density;
   - RWI;
   - GHSL class;
   - elevation.
9. **Provenance & quality:**
   - a list of each field → source dataset (with licence) → processing step;
   - QA flags (e.g. overlap, tiny, sliver);
   - the status of each output.

### 3.4 Load profiles (the RAMP archetype library)
- A grid of 17 profile cards (small-multiple 24-h curves; households blue, establishments orange), with daily kWh
  and coincident peak per unit. Filter: households / commerce / institutions / productive use.
- **Profile detail:**
  - description and the population it represents;
  - an appliance table: appliance, ownership share, number, rated power (W), hours/day, usage windows;
  - a provenance chip per number: *survey data* (MTF 2022), *evidence* (document + page), *measured* (QSEL) or
    *assumption* (rationale shown);
  - "share of numbers from data" (households 71–80%; establishments 0–60%);
  - the calibration note for households (section 6.4);
  - the validation result (section 3.7).
  - RAMP parameters on demand (windows, cycle, variability).
- A **"How RAMP works" explainer**: stochastic switch-on events inside usage windows, coincidence and day-to-day
  variability, 200 units × 28 days per profile. Cite Lombardi et al. 2019.

### 3.5 Climate & cooling
- Maps (H3 r7):
  - mean 2 m temperature (5.7–23.9 °C);
  - 95th-percentile daily maximum;
  - CDD22 (cooling degree-days, base 22 °C);
  - Cooling Demand Category;
  - GHI (4.73–5.02 kWh/m²/day).
- **Diurnal curves**: the hourly mean temperature for a clicked cell against the national median, with 22 °C and
  the class thresholds marked.
- The **cooling-class rule** shown explicitly: Low when tmax_p95 < 26 °C; Medium when 26 ≤ tmax_p95 < 29 °C; High
  when tmax_p95 ≥ 29 °C. Also explain why CDD22 is shown as a cross-check.
- **Independent check panel**: NASA POWER hourly temperatures at 4 towns against our ERA5-Land values (figure
  `A3_cooling_check.png`). The finding: cooling demand is marginal in most of Rwanda, including Kigali. Sustained
  heat is in the eastern lowlands (11.6% of buildings in areas above 100 CDD22 a year; provisional, since NASA POWER
  shows less there) and the Rusizi/Bugarama valley (confirmed).
- **In progress banner**: the rebuild from the Copernicus CDS ERA5-Land archive. It adds humidity, rainfall, hourly
  solar radiation, monthly and seasonal climatology, and a 1991–2020 baseline. Design the monthly views now, with
  an empty state.

### 3.6 Productive-use viability
- A map of PU viability (deep dives), with candidate buildings highlighted and a ranked table of candidates (score,
  class, distances, nearby POIs).
- A component explorer: sliders for the component weights (section 5.5). The map re-ranks live, and a "reset to
  defaults" button restores them.
- A link to the productive-use appliance evidence: mills, welding, tailoring, carpentry, refrigeration, pumps, each
  with its source document and page.
- Status: **Planned**. The method is defined (section 5.5), but the score isn't computed yet. Prototype it with the
  components we already have per building, and label it "prototype score".

### 3.7 Validation & quality
- **Demand checks**: simulated household consumption against independent anchors (section 6.5). Use a dot plot
  like `B1_household_check.png`, plus the table of verdicts (ok / check / fail with ratios).
- **Label quality**:
  - the confusion matrix against 173 human gold labels;
  - macro-F1 (0.19) and agreement (~39–43%), stated plainly;
  - class distribution per deep dive;
  - why accuracy is modest: the cards use open vector data only, while the human labellers used imagery.
- **Evidence quality**:
  - 3,076 parameters extracted with citations, of which 81% have quotes verified verbatim;
  - automated plausibility checks flag 17 of 1,077 RAMP-relevant values;
  - the human approvals record.
- **Data QA**: per-table checks (all six tables pass) with links to the reports.
- **Known limitations**: the numbered list in section 7.3.

### 3.8 Workflow in depth (must have the full data-architecture diagram)
This is a first-class page, not an appendix. It has three parts:
1. **Interactive data-architecture diagram.** Use the node list and edges in section 9.
   - Left to right: Sources → Ingest & provenance → Conform → Enrich (context, climate) → Classify (labels) →
     Evidence & appliance data → Demand modelling (archetypes → mapping → RAMP → aggregation) → Validation →
     Outputs (dataset, dashboard).
   - Colour nodes by stage and badge them by status.
   - Mark **human gates** as distinct shapes: codebook, gold labels, evidence approval, mapping approval,
     archetype sign-off, licence review.
   - Mark **restricted data** (DHS, MTF, EICV5 microdata) with a lock icon and the note "aggregates only; cells
     with fewer than 10 respondents suppressed".
   - Clicking a node opens a side panel: what it does, inputs → outputs (table names and key columns), tools,
     record counts, run command (`pixi run …`), schema contract, status, and the citations it uses.
2. **Step-by-step narrative**: one scrollable section per stage (the texts in section 10), each with a mini
   version of the diagram that highlights where you are.
3. **Reproducibility & engineering**:
   - pixi environment, DuckDB/GeoParquet, pandera schema contracts;
   - content-addressed download manifest (URL, sha256, licence, retrieval time), cached LLM calls with a spend
     ledger;
   - tests (93 passing), QA reports, conventional commits;
   - the AI-use disclosure: "AI is your assistant, not your author".

### 3.9 Data sources & citations
- A filterable table of every dataset: name, owner/publisher, what we use it for, role (feature / weak label /
  evidence / validation), access (open / login / restricted), licence, version or date, link, how to cite it.
- The evidence document corpus (26 documents), with the same columns plus pages used.
- A "cite this dataset" box for our own output, and a licence summary. Note in particular that Meta RWI is
  CC-BY-NC-4.0, so the combined product is for non-commercial use unless RWI is dropped.
- Attribution footer on every map: "© OpenStreetMap contributors · Google Open Buildings · Microsoft Building
  Footprints · Overture Maps Foundation · GHSL (EC JRC) · WorldPop · Meta Data for Good · Copernicus · NASA POWER ·
  Gridfinder".
- Full citations: section 11.

### 3.10 About, methods & limitations
The challenge text, team/AI disclosure, glossary (RAMP, MTF tiers, CDD, coincident peak, load factor, H3, RWI,
Ubudehe), the metric definitions (section 5), limitations and planned work, and contact/repo link.

## 4. Global behaviours
- **Status chips** on every metric, layer and page section: **Built**, **Draft** (needs human sign-off),
  **In progress**, **Planned**. Section 7 gives the current status of each item.
- **"Potential demand (if grid-connected)"** is the current meaning of every demand number. Add a global toggle
  "Potential ↔ Expected (× probability of connection)", with Expected disabled and marked **Planned**.
- **Uncertainty**:
  - show p10–p90 bands for energy and profiles;
  - show label confidence;
  - a "use class probabilities" vs "use top class only" toggle for demand. This choice matters a lot, so show it
    prominently next to every demand number. Summing small productive-use probabilities over thousands of houses
    adds hundreds of "expected" workshops and mills. That raises annual energy by 34% in Nyamirambo and 95% in
    Nasho, with peaks only ~3% higher. Keep "probabilities" as the default, and show the top-class value
    alongside ("6.6 GWh/yr; 4.9 with top class only").
- **Units**: W and kW for power, kWh/day, kWh/month and MWh/yr for energy, °C, °C·day/yr for CDD, kWh/m²/day for
  GHI, metres, km². Use SI formatting with thin-space thousands separators.
- **Time** is local Rwanda time (UTC+2, no daylight saving). Profiles run 00:00–24:00.
- **Every chart and map** has a title, a units label, a legend with **fixed, stated limits** (no auto-rescaling
  that hides magnitudes), a source line under it, and a "how computed" popover.
- **Downloads**: CSV/GeoJSON for any table, map selection or chart; PNG/SVG for charts; a link to the full
  GeoParquet release (Planned).
- **Shareable state**: URL encodes page, area, layers, selection and toggles.

## 5. Definitions of the five headline metrics (use this wording)

Let a building *b* have profile weights *w_bk* (units of RAMP profile *k*; a shop-house can be 1 household + 1
shop). Let each profile have a per-unit hourly mean load *P_k(h)*, daily energy *E_k* and day-to-day spread.

### 5.1 Estimated Peak Load
- **Building (expected coincident peak), W:** max over h of Σ_k w_bk · P_k(h). This is the building's expected
  draw at its busiest hour, averaged across days and similar buildings. It's what adds up when buildings are
  aggregated.
- **Area peak, kW:** max over h of Σ_b Σ_k w_bk · P_k(h). This is coincident, so it's smaller than the sum of
  single-building peaks.
- **Single-building instantaneous peak:** for one building on one day (non-coincident, minute level). **Planned**
  (small-group RAMP runs).
- Show the peak hour beside the value, e.g. "≈ 0.13 kW at 20:00".

### 5.2 Annual Energy Consumption
- Σ_k w_bk · E_k × 365, in kWh/yr, with a p10–p90 band from day-to-day variation. Areas show MWh/yr or GWh/yr.
- Caveat on every view: *potential demand if grid-connected, at today's appliance ownership (MTF 2022).*

### 5.3 Cooling Demand Category (Low / Medium / High)
- From the building's H3 r7 climate cell: **Low** when tmax_p95 < 26 °C, **Medium** when 26–29 °C, **High** when
  ≥ 29 °C. tmax_p95 is the 95th percentile of daily maximum 2 m temperature, 2019–2024 (ERA5-Land, downscaled to
  cell elevation).
- Also show CDD22 per year as a magnitude cross-check.
- Wording: "a relative indicator of where fans and cooling become likely; true air-conditioning demand is marginal
  in most of Rwanda."
- Climate isn't yet coupled into the RAMP profiles (fan use by cooling class is **Planned**).

### 5.4 Load Profile Characteristics
For each building or area, computed from its 24-hour curve:
- **profile type**: dominant profile(s) and an evening-peaking / daytime / flat shape label;
- **peak hour**;
- **load factor** = mean ÷ peak;
- **evening share** = share of daily energy 18:00–23:00;
- **daytime (productive-hours) share** 08:00–17:00;
- **night base load** = mean 00:00–05:00;
- **weekday/weekend ratio**;
- **seasonal variation**: **Planned**, once CDS climate is in.

Show these as a compact "fingerprint" next to every load curve.

### 5.5 Productive-Use Viability (score 0–100 → Low / Medium / High) [Planned; prototype shown]
**score = 100 × √(productive-use likelihood) × (weighted mean of the enabling conditions)**, with bands Low below
33.3, Medium below 66.7, and High otherwise. The likelihood *gates* the score: a house in a well-served street isn't
a productive-use site. The square root softens the gate.
- **Productive-use likelihood:** the label probability of productive_use + commercial + mixed_shop_house.
- **Enabling conditions**, each 0–1, with UI-adjustable weights (defaults in brackets):
  1. **Market access** [0.30]: POIs within 250 m, and distance to a major road.
  2. **Customers nearby** [0.20]: population density.
  3. **Grid proximity** [0.30]: distance to predicted MV lines (Gridfinder).
  4. **Ability to pay** [0.15]: Meta RWI.
  5. **Agricultural context** [0.05]: cropland or irrigation nearby. Not computed yet (null); a missing component's
     weight is dropped, not counted as zero.

Exact formulas: `meta.pu_prototype` and DATA_CONTRACT.md section 6. Show the components as a bar chart in the
building drawer. Label the score "prototype" until the method is finalised and checked against known enterprises.

## 6. Data storage and access (how the frontend gets its data) and how it was produced

### 6.1 Storage and access in brief (full specification: Appendix C / `dashboard/DATA_CONTRACT.md`)
- **There is no API server and no database.** The project's source of truth is a set of GeoParquet tables (6.4M
  buildings, DuckDB). A Python exporter (`pixi run dashboard-data`) writes a **static, versioned bundle** of
  JSON/GeoJSON files. The frontend fetches them over plain HTTP from any static host (GitHub Pages, S3/R2,
  Netlify, local `python -m http.server`). Paths carry the version (`v1`), so cache them as immutable.
- **Two bundles, one schema:**
  - **sample:** `dashboard/sample_data/v1/`, ~14 MB, in the repo; build the prototype against it;
  - **full:** `data/dashboard/v1/`, ~170 MB and 903 files, published to static hosting.

  Everything is loaded relative to a `DATA_BASE` constant.
- **Files:**

  | File | What it holds | Size (full) |
  |---|---|---|
  | `meta.json` | classes, colours, metric definitions, status registry, deep-dive list, attribution, prototype productive-use formula | small |
  | `manifest.json` | every file with bytes, sha256, feature count | small |
  | `national/h3_r7.geojson` | 4,034 climate/stock hexagons | 2.7 MB |
  | `national/climate_hourly.json` | 24-hour temperature curve per hexagon | 0.6 MB |
  | `admin/districts.geojson`, `admin/sectors.geojson` | 30 and 416 polygons with aggregates | 0.2 / 1 MB |
  | `deep_dives/<id>/buildings.geojson` | footprints + map fields: five headline metrics in both modes, class, height, source, `h3_r9` | 9–18 MB raw, 2–4 MB gzipped |
  | `deep_dives/<id>/weights.json` | profile-weight vectors per building (both modes) + productive-use components, for client-side sums and sliders | 2–4 MB |
  | `deep_dives/<id>/details/<h3_r9>.json` | full building records, sharded by H3 res-9 cell, fetched on click | ≤ 1.4 MB per shard |
  | `deep_dives/<id>/summary.json` | area metrics and hourly curves by profile group | small |
  | `profiles/profiles.json` | 17 RAMP profiles: curves (all/weekday/weekend), appliances, per-number provenance | 0.2 MB |
  | `profiles/evidence_index.json` | what each cited id points at (document + page + quote, survey aggregate, measurement) | small |
  | `validation/checks.json`, `sources/sources.json`, `workflow/workflow.json` + `workflow.mmd` | validation tables, datasets and citations, the architecture graph | small |
  | `figures/*.png` | static report figures | 0.1–0.4 MB each |

- **Load on demand:**
  - at start, only `meta`, `manifest`, hexagons and districts;
  - a sector's three files when it's opened;
  - one details shard per click (the building's `h3_r9` is in its map properties).
- **Joins:**
  - `bldg_id` across the building files;
  - `h3_r7` from a building to climate;
  - `profile_id` from weights to profiles;
  - (`adm2_name`, `adm3_name`) for sectors.
- **Conventions:** EPSG:4326 `[lon, lat]`; local time UTC+2 with hour bins 0–23; units in field names; **null
  means not observed or not modelled, never zero**.
- **Computed in the browser**, with the formulas in contract section 6, which tests check against the exporter:
  - the building and selection 24-hour curves (weights × profile curves);
  - selection peak and annual energy;
  - load characteristics;
  - the probability ↔ top-class toggle;
  - the productive-use score under slider weights.
- **Restricted microdata** (DHS, MTF, EICV5) never enters the bundle, only published aggregates. Meta RWI fields
  are CC-BY-NC.
- **Production path (Planned):** national footprints as PMTiles vector tiles, and GeoParquet queried with
  DuckDB-WASM, still with no server.

Design against these structures. Where a value doesn't exist yet, show the **Planned** empty state, never a
fabricated number.

### 6.2 Building-use classes (fixed order, colours in section 8)
residential · mixed_shop_house · commercial · institutional · religious · productive_use · industrial_warehouse ·
ancillary (kitchens, latrines, stores) · unknown.

Deep-dive class mix (top class):
- Nyamirambo: residential 68%, ancillary 29%, commercial/shop-house 1.7%, institutional/religious 0.8%.
- Muhoza (labelled part): 69%, 27%, 2.8%, 1.5%.
- Nasho: 71%, 29%, 0.2%, 0.2%.

### 6.3 From building to profile (approved mapping)
Class probabilities × context choose profiles:
- **Urban**: population density ≥ 2,500/km² or a GHSL urban class.
- **Wealth**: Meta RWI above the median *of its own setting*.

Then, by class:
- **Residential** → one of four household profiles (rural/urban × lower/upper).
- **mixed_shop_house** → the household profile + a kiosk/shop.
- **Commercial** → bar/restaurant, guesthouse or salon if tagged, else kiosk/shop.
- **Institutional** → school, health facility or office (from facility tags).
- **Religious** → place of worship.
- **Productive use** → mill / welding / tailoring / carpentry (equal split when the trade is unknown).
- **Industrial** → store/warehouse lighting.
- **Ancillary** → no load.
- **Unknown** → treated as residential (low confidence).

### 6.4 Profiles (17 RAMP archetypes; status Draft)
- **Households (4)**: ownership, bulbs per home and hours of use come from the **World Bank MTF Rwanda 2022**
  survey of grid-connected households, split urban/rural × Ubudehe category (1–2 vs 3–4).
  - TV power = measured TV energy (QSEL) ÷ MTF viewing hours.
  - Fridge energy from QSEL measurements.
  - **One calibration**: lighting on-time is scaled per profile (×0.52–1.09) so the simulated energy matches what
    the same MTF households spend on electricity, converted to kWh with the RURA tariff in force (2020 decision)
    + 18% VAT.
- **Establishments (13)**:
  - kiosk/shop and salon: MTF household-enterprise data (grid shops: 47% use lights, 42% radio, 19% fridge; open
    12 h a day);
  - school: MTF school survey (n=162; median 105 laptops, 10-hour day);
  - health facility: MTF survey of health **centres** (n=150), so it overstates health posts;
  - grain mill, welding, tailoring, carpentry: the cited evidence (NREL 2018, GIZ 2016, Power Africa Rwanda 2020);
  - bar, guesthouse, office, place of worship, store: mostly assumptions, labelled as such.
- **RAMP 0.5.2** simulates 200 units × 28 days per profile. We fixed a RAMP initialisation-order issue so that runs
  are reproducible and the peak window follows the load.

### 6.5 Validation numbers to display (household profile mix vs independent anchors)

| Setting | Our profiles | Anchor | Ratio | Verdict |
|---|---|---|---|---|
| Urban | 24.4 kWh/month | REG customers in Kigali, mean 24.8 (QSEL 2024) | 0.99 | ok |
| Urban | 24.4 | EICV5 2017 grid-lit households (spending → kWh), mean 23.1 | 1.06 | ok |
| Rural | 10.0 | QSEL 2025 rural connected households, mean 8.1 | 1.23 | ok |
| Rural | 10.0 | EICV5 2017 rural grid-lit households, mean 11.4 | 0.87 | ok |
| Rural | 10.0 | REG customers outside Kigali, mean 5.9 (QSEL 2024) | 1.68 | check |

| Profile | Simulated | Anchor | Verdict |
|---|---|---|---|
| School | 55 kWh/month | MTF 2022 grid schools p25–p75: 37–509 | ok |
| Grain mill | 26 kWh/day | NREL 2018: 20 | ok |
| Carpentry | 13.5 kWh/month | NREL 2018: 18 | ok |
| Health facility | 366 kWh/month | MTF 2022 health centres p25–p75: 1,367–4,101 | fail (expected: the profile targets posts) |

MTF energy tier: all four household profiles fall in Tier 2 by daily energy (Bhatia & Angelou 2015 thresholds).

## 7. Status (show honestly; this is a judging criterion)

### 7.1 Built
- National building tables: footprints, heights, context and climate. All six pass QA.
- Labels for the three deep dives.
- Evidence and validation anchors.
- The approved building → profile mapping.
- The RAMP profile library.
- The demand preview for 44,556 deep-dive buildings.

### 7.2 Draft, In progress, Planned
- **Draft** (needs the owner's sign-off): the 17 profiles (archetype parameters are a human gate), and therefore
  every demand number.
- **In progress**: the climate rebuild from Copernicus CDS (humidity, rainfall, solar, monthly climatology,
  1991–2020 baseline).
- **Planned**:
  - national demand roll-out;
  - Expected demand (× probability of grid connection);
  - single-building instantaneous peaks;
  - seasonal and climate-coupled profiles (fans by cooling class);
  - the Productive-Use Viability score;
  - a health-post profile;
  - off-grid / solar variants;
  - the GeoParquet/PostGIS release and vector tiles.

### 7.3 Known limitations (verbatim list for the Validation and About pages)
1. Google 2.5D heights are compressed: single-storey buildings read ~4–5 m, so floors above 2 are unreliable.
2. First-seen year has artefacts (jumps in 2019 and 2023); it's experimental.
3. Use labels come from open vector data only and agree with human labels ~40% of the time. Use the probabilities,
   not only the top class.
4. Microsoft footprints omit structures under ~15 m².
5. Cooling demand is marginal in most of Rwanda; the category is a relative proxy.
6. Labels cover the three deep dives (Muhoza partly) and a national sample.
7. Profiles are drafts:
   - several establishment profiles rest on assumptions;
   - the health profile reflects health centres;
   - all demand assumes a grid connection.
8. Rural households in the two wealth halves spend almost the same (~10 kWh/month).

## 8. Visual design system
- **Tone**: calm, technical, legible. Neutral surfaces, one accent per meaning, no gradients for decoration, no 3D.
- **Colour tokens** (from our figure style; colour-blind checked). Provide dark-mode equivalents.
  - Surface `#fcfcfb`, ink `#0b0b0b`, secondary ink `#52514e`, grid/borders `#e4e3df`.
  - Categorical, in fixed order: `#2a78d6`, `#eb6834`, `#1baf7a`, `#eda100`, `#e87ba4`, `#008300`, `#4a3aa7`,
    `#e34948`.
  - Footprint source: Google `#2a78d6`, Microsoft `#eb6834`, OSM `#1baf7a`.
  - Building class:

    | Class | Colour |
    |---|---|
    | residential | `#2a78d6` |
    | mixed_shop_house | `#4a3aa7` |
    | commercial | `#eb6834` |
    | institutional | `#1baf7a` |
    | religious | `#e87ba4` |
    | productive_use | `#eda100` |
    | industrial_warehouse | `#52514e` |
    | ancillary | `#c9c8c3` |
    | unknown | `#e4e3df` with a hatch |

  - Magnitude (energy, peak, density): one sequential blue ramp `#cde2fb → #9ec5f4 → #6da7ec → #3987e5 → #256abf
    → #184f95 → #0d366b`.
  - Heat (temperature, CDD, cooling): one sequential orange ramp `#fde8dc → #f9c3a3 → #f39a6b → #eb6834 → #c94f1f →
    #9a3a14 → #6b270c`.
  - Cooling category: Low `#f9c3a3`, Medium `#eb6834`, High `#9a3a14`, always with the text label too.
  - Status chips: Built (green outline), Draft (amber), In progress (blue), Planned (grey dashed).
- **Legends**:
  - always show units and **fixed limits** stated in the legend (e.g. annual kWh 0–1,000+, clamped with an arrow
    for values above);
  - log scales only where labelled, with readable ticks (1, 10, 100, 1,000).
- **Charts**:
  - load curves as step or area charts over 0–24 h, with ticks every 3 h and a shaded 18:00–23:00 evening band;
  - peak marked with a dot and label;
  - uncertainty as a light band.
- **Typography**: a clean sans (e.g. Inter or Source Sans 3) with tabular numerals. Sentence-case titles; titles
  say the finding when possible ("Evening lighting drives the peak").
- **Accessibility**:
  - WCAG AA contrast;
  - never colour alone (pair with labels or patterns);
  - keyboard navigation for map and drawer;
  - alt text for figures;
  - a table alternative for every map layer.
- **Responsive**: desktop first (1440 px), usable at tablet width; on phones, map and drawer stack.

## 9. Data-architecture diagram (for the Workflow page)

The graph's data is `workflow/workflow.json` in the bundle. It has lanes; nodes with `kind` (source / process /
table / gate / output), `status`, `command`, `description`, `records` (live row counts) and `citations`; and edges.
Render from that file, so the diagram stays in sync with the pipeline. Below is the same graph as generated
Mermaid (`workflow/workflow.mmd`, checked to parse). Restyle it freely, but keep every node and edge.
- **Gates** (`kind: gate`, the hexagons) are human decisions.
- **`restricted: true`** marks survey microdata, which is aggregates only.
- **Table nodes** show their row counts.

```mermaid
flowchart LR
  subgraph sources["Sources"]
    src_vida["VIDA footprints (Google + Microsoft + OSM)"]
    src_google25d["Google Open Buildings 2.5D Temporal"]
    src_osm["OpenStreetMap (roads, land use, tags)"]
    src_overture["Overture places"]
    src_health["healthsites.io"]
    src_admin["geoBoundaries ADM0-5"]
    src_ghsl["GHSL BUILT-C 2018"]
    src_worldpop["WorldPop 2023"]
    src_rwi["Meta Relative Wealth Index"]
    src_gridfinder["Gridfinder MV lines"]
    src_dem["Copernicus DEM GLO-30"]
    src_era5["ERA5-Land (Open-Meteo / Copernicus CDS)"]
    src_power["NASA POWER"]
    src_microdata["Survey microdata: DHS 2025, MTF 2022, EICV5 (restricted)"]
    src_corpus["Evidence corpus (26 public reports)"]
    src_anchors["QSEL / REG / RURA consumption and tariffs"]
  end
  subgraph ingest["Ingest and provenance"]
    manifest["Download manifest (url, sha256, licence, time)"]
  end
  subgraph footprints["A1 Footprints and heights"]
    conform["Conform footprints"]
    t_base[("buildings_base")]
    heights["Heights, floors, first seen"]
    t_height[("building_height")]
  end
  subgraph context["A2 Context"]
    ctx_vector["Vector context"]
    ctx_raster["Raster context"]
    t_context[("building_context")]
  end
  subgraph climate["A3 Climate"]
    climate["Climate to H3 r7"]
    power_check["NASA POWER cross-check"]
    t_climate[("climate_h3")]
  end
  subgraph labels["A4 Building use"]
    codebook{{"Codebook approval (human)"}}
    cards["Context cards (open vector data only)"]
    labeller["Claude Haiku 4.5 labeller"]
    gold{{"Human gold labels (173)"}}
    label_eval["Label evaluation"]
    t_labels[("labels")]
  end
  subgraph evidence["Evidence and appliance data"]
    extraction["Evidence extraction with citations"]
    curation["Hand-curated productive-use values"]
    plausibility["Automated plausibility checks"]
    evidence_approval{{"Evidence approval (human)"}}
    microdata_agg["Microdata to weighted aggregates (restricted)"]
    t_anchors[("validation_anchors + MTF aggregates")]
  end
  subgraph demand["Demand modelling"]
    builder["B1 archetype builder"]
    signoff{{"Archetype sign-off (human, pending)"}}
    t_archetypes[("17 archetypes (draft)")]
    mapping_approval{{"Mapping approval (human)"}}
    mapping["B2 building to profile mapping"]
    t_weights[("building_profiles")]
    ramp["B3 RAMP 0.5.2 simulation"]
    t_library[("profile_library")]
    aggregation["B4 building and area metrics"]
  end
  subgraph validation["Validation"]
    checks["Profile checks"]
    qa["Table QA (schema contracts)"]
  end
  subgraph outputs["Outputs"]
    licence_review{{"Licence review (human)"}}
    dataset(["Building energy dataset (GeoParquet / PostGIS)"])
    bundle(["Dashboard data bundle (this)"])
    docs(["Datasheet, QA and status reports"])
  end
  src_vida --> manifest
  src_google25d --> manifest
  src_osm --> manifest
  src_overture --> manifest
  src_health --> manifest
  src_admin --> manifest
  src_ghsl --> manifest
  src_worldpop --> manifest
  src_rwi --> manifest
  src_gridfinder --> manifest
  src_dem --> manifest
  src_era5 --> manifest
  src_power --> manifest
  src_microdata --> manifest
  src_corpus --> manifest
  src_anchors --> manifest
  manifest --> conform
  conform --> t_base
  t_base --> heights
  manifest --> heights
  heights --> t_height
  t_base --> ctx_vector
  t_base --> ctx_raster
  manifest --> ctx_vector
  manifest --> ctx_raster
  ctx_vector --> t_context
  ctx_raster --> t_context
  manifest --> climate
  climate --> t_climate
  power_check --> t_climate
  t_height --> cards
  t_context --> cards
  t_climate --> cards
  codebook --> labeller
  cards --> labeller
  labeller --> t_labels
  t_labels --> label_eval
  gold --> label_eval
  manifest --> extraction
  manifest --> curation
  extraction --> plausibility
  curation --> plausibility
  plausibility --> evidence_approval
  manifest --> microdata_agg
  microdata_agg --> t_anchors
  evidence_approval --> builder
  t_anchors --> builder
  builder --> t_archetypes
  signoff --> t_archetypes
  t_labels --> mapping
  t_context --> mapping
  mapping_approval --> mapping
  mapping --> t_weights
  t_archetypes --> ramp
  ramp --> t_library
  t_weights --> aggregation
  t_library --> aggregation
  t_climate --> aggregation
  t_library --> checks
  t_anchors --> checks
  t_base --> qa
  aggregation --> dataset
  aggregation --> bundle
  licence_review --> dataset
  checks --> docs
  qa --> docs
```

Clicking a node opens a side panel with its description, run command, output table and key columns (Appendix C),
record count, status and citations.

## 10. Processing narrative (texts for the Workflow page and "how computed" popovers)
1. **Footprints:**
   - The VIDA combined layer merges Google Open Buildings V3, Microsoft and OSM footprints for Rwanda: 6,434,247
     buildings (Google 5.81M, Microsoft 0.49M, OSM 0.13M).
   - We repair geometries and flag overlaps and slivers rather than dropping them.
   - Shape metrics (area, perimeter, compactness, orientation) are computed in UTM 35S.
   - Each building is indexed to H3 r9 and given a stable `bldg_id`.
2. **Heights and growth:**
   - Google 2.5D Temporal rasters give a height for 4.6M buildings and yearly presence 2016–2023.
   - Floors come from height cut-points fitted on OSM `building:levels` with a held-out test: 1 floor below 6.0 m,
     2 below 8.5 m, then +1 per 3 m.
   - First-seen year is the first year presence stays ≥ 0.5.
3. **Context:**
   - Distances to the nearest major road, any road and the predicted MV line.
   - POI counts at 50, 100 and 250 m (OSM + Overture), with land use, OSM/Overture tags and health facilities.
   - Admin names from province to village.
   - Raster values: GHSL settlement class and non-residential share, WorldPop density, Meta RWI, Copernicus DEM
     elevation.
4. **Climate:**
   - ERA5-Land hourly 2 m temperature (0.1°, 2019–2024) is interpolated to H3 r7 cell centres and corrected to
     each cell's mean elevation with a fitted lapse rate (−6.05 K/km). Time is local (UTC+2).
   - From it: hourly diurnal means, annual mean, tmax_p95, and CDD18/22/24. GHI comes from NASA POWER.
   - The cooling class follows the rule in 5.3, and an independent NASA POWER check was run at 4 towns.
5. **Building use:**
   - Each building gets a text "context card" from open vector data only: footprint, height, size against
     neighbours, land use, road frontage, nearby tags and places, density, settlement class, wealth and distances.
   - Claude Haiku 4.5 returns probabilities over 9 classes plus the cues it used. Every call is cached and costed.
   - Accuracy is measured against human gold labels made with imagery. The labeller configuration was chosen from
     11 tested on cost and accuracy.
6. **Evidence:**
   - 26 public reports (DHS, MTF, EICV7, census, REG/RURA, QSEL, Efficiency for Access, NREL, GIZ, Power Africa,
     ESMAP).
   - Values were extracted with citations, and quotes are verified verbatim on the cited page.
   - Automated checks test units, physical ranges and agreement between sources. The owner approved the set
     (blanket approval, backed by the checks).
   - Restricted microdata (DHS 2025, MTF 2022, EICV5) is turned into weighted aggregates only, and spending is
     converted to kWh with the official RURA tariffs.
7. **Profiles:**
   - 17 RAMP archetypes are built from recipes. Every number cites survey data, evidence or measurements, or
     carries a written assumption.
   - Household lighting is calibrated to MTF spending.
   - Simulated in RAMP 0.5.2: 200 units × 28 days.
8. **Mapping and aggregation:**
   - Class probabilities × context give profile weights per building (approved rules, 6.3).
   - Building and area metrics follow section 5.
9. **Validation:** the comparisons in 6.5, plus label evaluation and table QA.
10. **Human gates**:
    - codebook;
    - gold labels;
    - evidence approval;
    - mapping approval;
    - archetype sign-off (pending);
    - licence review.

## 11. Citations (use these strings; don't add references that aren't listed)
**Buildings & context**
- VIDA. Google–Microsoft–OSM Open Buildings, combined (by country: RWA). Source Cooperative.
  https://source.coop/vida/google-microsoft-osm-open-buildings. ODbL-1.0.
- Sirko, W. et al. (2021). Continental-scale building detection from high resolution satellite imagery.
  arXiv:2107.12283. Google Open Buildings, CC-BY-4.0 / ODbL. Plus Google Open Buildings 2.5D Temporal dataset
  (2016–2023), CC-BY-4.0.
- Microsoft Building Footprints (ODbL-1.0).
- OpenStreetMap contributors (2026). Rwanda extract via Geofabrik. ODbL-1.0.
- Overture Maps Foundation. Places theme. CDLA-Permissive-2.0.
- healthsites.io. Rwanda health facilities, via HDX. ODbL-1.0.
- geoBoundaries (Runfola et al.), gbOpen RWA ADM0–5. CC-BY-4.0.
- European Commission JRC. GHS-BUILT-C R2023A (settlement characteristics, 2018). CC-BY-4.0.
- WorldPop (University of Southampton). Global2 R2025A constrained population, Rwanda 2023. CC-BY-4.0.
- Chi, G., Fang, H., Chatterjee, S., Blumenstock, J. E. (2022). Microestimates of wealth for all low- and
  middle-income countries. *PNAS* 119(3). Meta Relative Wealth Index, CC-BY-NC-4.0.
- Arderne, C., Zorn, C., Nicolas, C., Koks, E. E. (2020). Predictive mapping of the global power system using open
  data. *Scientific Data* 7, 19. Gridfinder, CC-BY-4.0.
- Copernicus DEM GLO-30 (ESA / Copernicus). Free licence with attribution.

**Climate**
- Muñoz-Sabater, J. et al. (2021). ERA5-Land: a state-of-the-art global reanalysis dataset for land applications.
  *Earth System Science Data* 13, 4349–4383. Copernicus Climate Change Service (C3S), accessed via the Copernicus
  Climate Data Store and Open-Meteo (CC-BY-4.0).
- NASA Langley Research Center POWER Project (MERRA-2 hourly temperature; CERES SYN1deg irradiance).

**Demand modelling & energy access**
- Lombardi, F., Balderrama, S., Quoilin, S., Colombo, E. (2019). Generating high-resolution multi-energy load
  profiles for remote areas with an open-source stochastic model. *Energy* 177, 433–444.
  doi:10.1016/j.energy.2019.04.097. RAMP (rampdemand 0.5.2), EUPL-1.2.
- Bhatia, M., Angelou, N. (2015). Beyond Connections: Energy Access Redefined. ESMAP Technical Report 008/15, World
  Bank (Multi-Tier Framework).
- World Bank (2018). Rwanda – Beyond Connections: Energy Access Diagnostic Report Based on the Multi-Tier Framework.
- World Bank. Multi-Tier Framework survey, Rwanda 2022 (microdata, restricted; aggregates only).
- NISR. EICV5 2016/17 (microdata, restricted; aggregates only); EICV7 2023/24 Main Indicators and Utilities &
  Amenities reports; RPHC5 2022 census reports.
- NISR, MoH, ICF. Rwanda DHS 2019-20 and 2025 Final Reports; DHS Program API.
- Columbia University QSEL. Rwanda's Path to Universal Electricity Access (2024); The role of grid reliability in
  appliance ownership and usage in Rwanda (2025); Adoption of Electricity in Rural Rwanda 10 Years after Connection
  (2025); Understanding Rwanda Household Electricity Demand (2024).
- RURA. Decision N°01/BD/ER-EWS/RURA/2020 reviewing end-user electricity tariffs (effective 21 Jan 2020); Official
  Gazette no.34 of 20/08/2018; REG tariffs effective 1 October 2025.
- REG. Annual Report 2022–2023; Least Cost Power Development Plan 2024 Review.
- NREL / Energy 4 Impact (2018). Productive Use of Energy in African Micro-Grids. GIZ (2016). Photovoltaics for
  Productive Use Applications. USAID Power Africa (2020). Productive Use of Energy Catalog – Rwanda. ESMAP (2022).
  Mini Grids for Half a Billion People. Efficiency for Access / CLASP (2021). Appliance Data Trends; Solar Appliance
  Technology Briefs (refrigerators, fans, milling).

Restricted-data notice (show on Sources and wherever MTF/DHS/EICV5 numbers appear): "Derived from restricted survey
microdata under the providers' terms; only weighted aggregates are shown, and cells with fewer than 10 respondents
are suppressed."

## 12. Deliverables and acceptance criteria
- An interactive prototype covering all pages in section 3, using the sample data, plus a short design-system
  sheet (tokens, components, chart rules).
- The **Workflow in depth** page has the full diagram from section 9, with every node clickable.
- The five headline metrics appear, with the exact names and definitions of section 5:
  - on the Overview (sector cards);
  - on the sector panel;
  - in the building drawer;
  - in any user selection.
- **Every** number has a unit, a status chip and a "how computed + source" popover. No invented data: missing
  values show a Planned or empty state.
- Maps show explicit legends with fixed limits and full attribution. Charts show the evening band and the peak
  marker.
- The national → sector → building zoom path works in three clicks. Selection aggregation and export work.
- Accessible (AA), keyboard-navigable, and readable at 1440 px and on a tablet.
- Data comes only from the bundle, through `DATA_BASE`, loaded on demand as in Appendix C section 3. Client-side
  numbers use the contract's formulas: recomputing a sample building from `weights.json` + `profiles.json` must
  reproduce its exported `annual_kwh` and `peak_w`. Swapping the sample for the full bundle needs no code change.

## Appendix A: profile library sample (real values, per unit; hourly means in W, hours 00–23)
```json
{
  "household_rural_t1": {"daily_kwh":0.31,"coincident_peak_w":67,"hourly_w":[0.4,0.9,0.9,1.0,1.8,21.7,30.0,5.0,0.5,0.0,0.0,0.1,1.7,1.9,0.0,0.0,0.0,4.7,26.0,50.3,55.6,51.7,39.8,14.7]},
  "rural_grid_household_tier2": {"daily_kwh":0.35,"coincident_peak_w":75,"hourly_w":[0.7,1.7,2.0,2.2,2.9,21.8,32.1,7.6,0.9,0.3,0.3,0.6,4.1,4.0,0.4,0.3,0.2,4.5,30.0,55.5,61.1,55.5,42.7,15.0]},
  "household_urban_t2": {"daily_kwh":0.64,"coincident_peak_w":126,"hourly_w":[1.3,3.1,4.1,4.5,6.1,41.1,62.2,23.9,3.2,3.5,3.6,5.2,13.0,10.3,3.8,3.5,2.8,9.7,61.2,96.1,101.0,81.2,67.1,24.6]},
  "household_urban_t3": {"daily_kwh":0.97,"coincident_peak_w":166,"hourly_w":[3.5,8.3,10.4,11.8,13.4,51.0,120.8,89.1,9.6,11.2,11.5,17.6,34.5,23.7,11.6,11.6,10.2,12.8,83.7,122.1,127.1,82.9,66.7,24.3]},
  "kiosk_shop": {"daily_kwh":0.3,"coincident_peak_w":24,"hourly_w":[1.8,4.5,5.9,7.0,7.8,8.3,13.9,11.3,13.7,15.1,15.8,16.0,16.5,16.5,16.6,16.6,16.4,16.8,21.1,22.2,18.8,10.5,5.8,3.5]},
  "salon": {"daily_kwh":0.58,"coincident_peak_w":65,"hourly_w":[0.0,0.0,0.0,0.0,0.0,0.1,5.9,20.4,36.1,39.1,42.1,45.1,40.5,42.2,43.9,42.2,42.0,43.4,47.5,49.0,38.1,4.4,0.1,0.0]},
  "bar_restaurant": {"daily_kwh":2.11,"coincident_peak_w":188,"hourly_w":[13.4,31.7,42.7,50.8,55.7,59.2,62.8,63.9,65.0,68.1,74.2,81.2,86.8,90.3,91.7,90.0,89.3,98.9,149.9,181.7,177.3,170.9,150.2,66.2]},
  "guesthouse": {"daily_kwh":3.46,"coincident_peak_w":588,"hourly_w":[8.2,19.9,26.4,30.4,34.2,242.2,310.6,32.9,30.8,31.0,31.6,32.2,62.5,67.8,31.9,31.4,31.2,39.4,275.6,481.3,511.2,497.7,452.5,144.4]},
  "school": {"daily_kwh":1.81,"coincident_peak_w":359,"hourly_w":[0.0,0.0,0.0,0.0,0.0,1.5,9.9,101.3,186.7,193.6,199.3,212.7,207.2,201.3,186.9,194.7,103.1,11.5,2.3,0.0,0.0,0.0,0.0,0.0]},
  "health_post": {"daily_kwh":12.05,"coincident_peak_w":994,"hourly_w":[134.9,335.1,431.6,474.6,470.9,432.7,303.5,190.9,327.0,542.2,587.6,610.1,632.9,634.0,621.9,601.9,428.1,167.1,483.3,845.0,942.6,924.7,718.0,213.3]},
  "office": {"daily_kwh":0.99,"coincident_peak_w":178,"hourly_w":[0.0,0.0,0.0,0.0,0.0,0.0,0.2,2.3,73.0,116.0,120.6,122.1,123.0,120.6,116.6,109.5,77.5,3.2,0.5,0.0,0.0,0.0,0.0,0.0]},
  "church": {"daily_kwh":0.18,"coincident_peak_w":18,"hourly_w":[0.0,0.0,0.0,0.0,0.0,0.0,0.0,2.1,20.4,35.6,36.0,34.8,22.7,2.7,0.0,0.0,0.0,0.7,7.8,11.0,7.9,0.8,0.0,0.0]},
  "warehouse_light": {"daily_kwh":0.28,"coincident_peak_w":27,"hourly_w":[4.8,8.5,9.3,9.3,9.3,8.7,6.0,5.2,5.8,10.3,13.4,13.3,13.5,14.8,15.1,12.2,7.5,6.9,13.6,21.9,23.8,22.9,17.5,7.3]},
  "grain_mill": {"daily_kwh":26.0,"coincident_peak_w":4350,"hourly_w":[0.0,0.0,0.0,0.0,0.0,0.1,5.3,0.1,0.0,1825.1,3434.9,3716.0,3683.9,3669.9,3682.6,3469.6,2476.9,0.7,6.8,12.1,11.0,3.8,0.0,0.0]},
  "welding_workshop": {"daily_kwh":5.45,"coincident_peak_w":772,"hourly_w":[0.0,0.0,0.0,0.0,0.0,0.1,5.4,0.1,363.8,563.5,559.7,563.8,561.6,575.2,585.1,587.0,595.3,458.6,6.9,12.0,11.1,3.7,0.0,0.0]},
  "tailoring": {"daily_kwh":1.08,"coincident_peak_w":156,"hourly_w":[0.0,0.0,0.0,0.0,0.0,0.2,9.5,0.4,66.9,104.4,110.6,110.7,109.6,111.0,109.8,106.9,105.6,83.7,11.0,16.2,15.1,5.6,0.1,0.0]},
  "carpentry": {"daily_kwh":0.45,"coincident_peak_w":94,"hourly_w":[0.0,0.0,0.0,0.0,0.0,0.1,5.2,0.1,37.4,40.8,41.2,42.1,41.7,40.6,42.2,41.9,39.9,38.0,7.0,12.0,11.1,3.8,0.0,0.0]}
}
```
Profile ids `household_rural_t1` … `household_urban_t3` are the four household profiles: rural lower, rural upper,
urban lower and urban upper wealth half. Display them as "Rural household (lower)" and so on; the `t1`/`t3`
suffixes are not MTF tiers (all four are Tier 2 by energy).

## Appendix B: key numbers for cards and examples
- **Area results in both modes** (full bundle; potential demand if connected; Draft):

  | Sector | Modelled buildings | Annual energy, probabilities | Annual energy, top class | Area peak, probabilities / top class |
  |---|---|---|---|---|
  | Nyamirambo | 19,822 | 6.61 GWh/yr | 4.92 GWh/yr | 1,785 / 1,732 kW at 20:00 |
  | Muhoza (labelled part) | 8,658 | 2.84 GWh/yr | 2.06 GWh/yr | 754 / 732 kW |
  | Nasho | 15,976 | 3.28 GWh/yr | 1.68 GWh/yr | 807 / 774 kW |

- **Preview Annual Energy per building** (probabilities mode):

  | Sector | Annual energy | Median per building | p90 per building |
  |---|---|---|---|
  | Nyamirambo | 6.61 GWh/yr | 392 kWh/yr | 406 kWh/yr |
  | Muhoza (labelled part, 8,658 buildings) | 2.84 GWh/yr | 379 kWh/yr | 418 kWh/yr |
  | Nasho | 3.28 GWh/yr | 197 kWh/yr | 289 kWh/yr |

- **Climate**: mean temperature 5.7–23.9 °C; GHI 4.73–5.02 kWh/m²/day. Cooling cells:

  | Category | Cells | tmax_p95 | Mean CDD22 |
  |---|---|---|---|
  | Low | 1,484 | < 26 °C | ≈ 1 |
  | Medium | 1,672 | 26–29 °C | ≈ 35 |
  | High | 878 | ≥ 29 °C | ≈ 167 |

- **Households, kWh/month**: rural ≈ 9.4 (lower) and 10.5 (upper); urban ≈ 19.3 (lower) and 29.5 (upper). Evening
  peak 19:00–21:00; morning bump 05:00–07:00.
- **Labels**:
  - 44,556 buildings modelled in the deep dives;
  - 173 human gold labels;
  - labeller agreement ≈ 39–43%, macro-F1 0.19;
  - labelling cost ≈ $3.6 per 1,000 buildings.

## Appendix C: data contract (copy of `dashboard/DATA_CONTRACT.md`; if they differ, the file wins)

How the dashboard's data is stored, where it lives, how to load it, and what every field means. The frontend
reads **only** this bundle. Generated by `pixi run dashboard-data` (`src/rtl/dashboard/export.py`, settings in
`config/dashboard.yaml`).

#### 1. Architecture in one paragraph
The project's source of truth is a set of GeoParquet tables (6.4M buildings) queried with DuckDB. They're far too
big, and partly too sensitive, to ship to a browser. The exporter writes a **static, versioned bundle** of
JSON/GeoJSON files: national and admin aggregates, the deep-dive buildings with their computed metrics, the RAMP
profile library, validation results, sources and the workflow graph. The frontend fetches these files over plain
HTTP from any static host (GitHub Pages, S3/R2, Netlify, or a local `python -m http.server`). There is **no API
server and no database**. Interactive maths (selection totals, the probability ↔ top-class toggle, the
productive-use weight sliders) runs in the browser, with the formulas in section 6.

```
GeoParquet tables (DuckDB)  --pixi run dashboard-data-->  bundle v1 (static files)  --HTTP GET-->  frontend
data/interim, data/processed                              data/dashboard/v1/  (full, ~170 MB, 903 files)
                                                          dashboard/sample_data/v1/  (sample, ~13 MB, committed)
```

#### 2. The two bundles
| Bundle | Path | Contents | Use |
|---|---|---|---|
| **Sample** | `dashboard/sample_data/v1/` (in the repo) | full national, admin, profile, validation, source and workflow files; deep-dive buildings in one small box per sector (Nyamirambo 1,266, Muhoza 986, Nasho 548) | building and testing the frontend |
| **Full** | `data/dashboard/v1/` (regenerate; publish to static hosting) | as the sample, but every building in the three sectors (71,038; 44,556 modelled) | the live dashboard |

The schemas are identical, so code written against the sample works on the full bundle. Point the app at a base
URL constant (`DATA_BASE = "./data/v1/"` or a CDN URL), and never hard-code the sample path.

#### 3. Hosting and loading strategy
- **Serving:** any static host. Enable gzip/brotli: GeoJSON and JSON compress 4–6×, so the 17.6 MB Muhoza
  footprints travel as ~3–4 MB. Allow CORS if the data and the app are on different origins. Paths contain the
  version (`v1`), so cache them as immutable.
- **Load on demand, not all at start:**

| When | Fetch | Size (full) |
|---|---|---|
| App start | `meta.json`, `manifest.json`, `national/h3_r7.geojson`, `admin/districts.geojson` | ~3 MB |
| National view, sector layer | `admin/sectors.geojson` | 1 MB |
| Click a hex / climate page | `national/climate_hourly.json` | 0.6 MB |
| Open a deep dive | `deep_dives/<id>/summary.json`, `buildings.geojson`, `weights.json` | 9–19 MB raw (2–4 MB gzipped) |
| Click a building | `deep_dives/<id>/details/<h3_r9>.json` (the building's `h3_r9` is in its map properties) | ≤ 1.4 MB, usually < 300 kB |
| Profiles page | `profiles/profiles.json`, `profiles/evidence_index.json` | 0.2 MB |
| Validation / Sources / Workflow pages | `validation/checks.json`, `sources/sources.json`, `workflow/workflow.json` (+ `workflow.mmd`) | < 0.1 MB each |
| Figures | `figures/*.png` (static report figures) | 0.1–0.4 MB each |

- `manifest.json` lists every file with `bytes`, `sha256` and (GeoJSON) `features`. Use it for progress bars and
  integrity checks.
- `deep_dives/<id>/details/index.json` maps each `h3_r9` shard to its building count. Use it for a missing shard,
  which means no buildings in that cell.

#### 4. Conventions
- **CRS:** EPSG:4326, coordinates `[lon, lat]` (RFC 7946 GeoJSON). Buildings are rounded to 1e-6° (~0.1 m), hexes
  and admin areas to 1e-5° and simplified (~50–100 m tolerance) for display.
- **IDs and joins:**
  - `bldg_id` (`RWA-` + 16 hex) is stable across all files.
  - `h3_r7` joins buildings → climate (`national/h3_r7.geojson`, `climate_hourly.json`).
  - `h3_r9` locates a building's details shard.
  - `profile_id` joins weights → `profiles.json`.
  - Admin areas are keyed by names; a sector is unique only with its district (`adm2_name` + `adm3_name`).
- **Fixed orders:** use them for arrays and legends.
  - Classes: `meta.classes` (`probs` arrays follow it).
  - Profiles: `weights.json.profile_order` (alphabetical).
  - Hours 0–23: local time UTC+2, hour-starting bins.
  - Presence years 2016–2023.
- **Units:** in field names where it matters (`_m`, `_m2`, `_kwh`, `_mwh`, `_kw`, `_w`, `_c`, `_per_year`,
  `_per_km2`). Power is W for buildings and kW for areas; energy is kWh/yr for buildings and MWh/yr for areas.
- **Null:** not observed or not modelled, **never zero**. A building with `modelled: false` has null demand and
  productive-use fields. In top-class mode, a modelled building whose top class is ancillary has demand 0 (no load).
- **Status enums:** `built`, `draft`, `in_progress`, `planned` (see `meta.status`). All demand is `draft`: the
  potential demand if grid-connected, from draft profiles.

#### 5. File reference

##### `meta.json`
`title`, `version`, `generated_at`, `git_commit`, `crs`, `time`, `nulls`, `units`, and the following:
- **`national`:** headline counts.
- **`classes`:** `[{id, label, colour}]`, 9 in fixed order.
- **`cooling`:** the rule text and categories `[{id, label, colour}]`.
- **`profile_groups`:** `[{id, label, colour}]`.
- **`profiles`:** `{profile_id: {name, group}}`.
- **`metrics`:** `{key: {name, unit, definition}}` for the five headline metrics, with the exact UI wording.
- **`status`:** `{item: {status, note}}`.
- **`pu_prototype`:** weights, formulas and category bands.
- **`deep_dives`:** `[{id, district, sector, description, aoi_key, n_buildings, n_modelled, bbox}]`.
- **`attribution`:** the map attribution string.
- **`licence_notes`**.

##### `national/h3_r7.geojson` (4,034 hexagon polygons)
| Field | Meaning |
|---|---|
| `h3_r7`, `district` | cell id; the district most of its buildings fall in |
| `n_buildings`, `footprint_m2`, `mean_height_m` | building stock |
| `pop_density_mean`, `rwi_mean`, `dist_powerline_m_median`, `ghsl_class_mode` | context (WorldPop, Meta RWI, Gridfinder, GHSL) |
| `elevation_m`, `t2m_mean_c`, `tmax_p95_c`, `cdd18_per_year`, `cdd22_per_year`, `cdd24_per_year`, `ghi_kwh_m2_day`, `cooling_class` | climate (ERA5-Land 2019–2024; GHI from NASA POWER) |
| `n_modelled`, `annual_mwh_modelled`, `modelled_share` | demand where buildings are modelled (deep dives only; `modelled_share` tells you coverage, so don't map MWh where it's ~0) |

##### `national/climate_hourly.json`
`{h3_r7: [24 hourly mean 2 m temperatures, °C]}`, the diurnal curve per cell.

##### `admin/districts.geojson` (30), `admin/sectors.geojson` (416)
Dissolved from village polygons. Fields: `adm1_name`, `adm2_name`, (`adm3_name`), plus the same stock and context
aggregates as the hexes, `n_modelled`, `annual_mwh_modelled`, and `share_low`, `share_medium`, `share_high` (the
share of buildings in each cooling category).

##### `deep_dives/<id>/buildings.geojson` (footprint polygons; the map layer)
| Field | Meaning |
|---|---|
| `bldg_id`, `h3_r9` | id; details shard |
| `label`, `confidence` | top use class and its probability (null if not labelled) |
| `modelled` | true if the building has a use label and profile weights |
| `annual_kwh`, `peak_w`, `peak_hour` | **Annual Energy Consumption** and **Estimated Peak Load** (W, local hour), using class probabilities |
| `annual_kwh_top`, `peak_w_top` | the same using the **top class only** (the dashboard toggle) |
| `cooling_class` | **Cooling Demand Category** (`low` / `medium` / `high`) |
| `pu_score`, `pu_category` | **Productive-Use Viability**, PROTOTYPE score 0–100 and band |
| `height_m`, `est_floors`, `first_seen_year`, `source`, `area_m2` | building attributes (`first_seen_year` is experimental) |

##### `deep_dives/<id>/weights.json`
`{profile_order: [17 ids], probabilities: {bldg_id: [17 weights]}, top_class: {bldg_id: [17 weights]},
pu_component_order: [6 names], pu_components: {bldg_id: [6 values 0-1 or null]}}` (modelled buildings only).
- A weight is the expected number of units of that profile in the building. A shop-house can be 1 household + 1
  shop, so weights can sum above 1.
- Modelled buildings with no load (top class ancillary) are absent from `top_class`.

##### `deep_dives/<id>/details/<h3_r9>.json`
`{bldg_id: details}`, where `details` has these parts:

| Part | Fields |
|---|---|
| position | `lon`, `lat`, `h3_r7`, `h3_r9` |
| `admin` | `sector`, `cell`, `village` |
| `footprint` | `source`, `source_confidence`, `area_m2`, `perimeter_m`, `compactness` (0–1), `orientation_deg`, `qa_flags` [list] |
| `height` | `height_m`, `est_floors`, `gfa_m2`, `first_seen_year`, `presence` [8 values, 2016–2023, 0–1] |
| `use` | `label`, `confidence`, `probs` [9, class order], `cues` (the labeller's cited reasons, text), `tags` (OSM/Overture/facility tags present) |
| `profiles` | `{profile_id: weight}`, readable |
| `profiles_top_class` | `{profile_id: weight}`, readable |
| `demand` | `annual_kwh`, `annual_kwh_p10`, `annual_kwh_p90`, `peak_w`, `peak_hour`, `load_factor`, `evening_share`, `daytime_share`, `night_base_w`, `weekday_weekend_ratio`, `shape` |
| `demand_top_class` | `annual_kwh`, `peak_w`, `peak_hour` |
| `climate` | `cooling_class`, `tmax_p95_c`, `cdd22_per_year`, `t2m_mean_c` |
| `pu` | `score`, `category`, `components` {pu_likelihood, market_access, demand_density, grid_proximity, ability_to_pay, agri_context}, each 0–1 or null |
| `context` | `dist_road_major_m`, `dist_road_any_m`, `dist_powerline_m`, `poi_count_50m/100m/250m`, `pop_density_per_km2`, `rwi`, `ghsl_class`, `ghsl_nres_share`, `elevation_m` |

##### `deep_dives/<id>/summary.json` (area panel)
- `id`, `district`, `sector`, `description`, `n_buildings`, `n_modelled`, `bbox`.
- `class_mix_top_label` (counts) and `class_mix_expected` (summed probabilities).
- `cooling_mix`, `pu_mix`.
- `units_by_profile` and `units_by_profile_top_class` (expected units per profile).
- `metrics`: `annual_mwh` with `_p10`/`_p90`, `area_peak_kw`, `area_peak_hour`, `sum_of_building_peaks_kw`,
  `median_building_annual_kwh`, `p90_building_annual_kwh`, `load_profile` (characteristics).
- `metrics_top_class`.
- `hourly_kw`: `all`, `weekday`, `weekend`, and `by_group` {household, commerce, institution, productive}, each 24
  values. `hourly_kw_top_class.all` has the same for top-class mode.
- `note`.

##### `profiles/profiles.json`
`{profiles: [...], groups, simulation}`. Each profile has:
- **Identity:** `profile_id`, `name`, `group`, `status` (`draft`), `description`, `population`, `year`.
- **Energy and peaks:** `daily_kwh` (+`_p10`/`_p90`), `annual_kwh`, `coincident_peak_w_p50/p90` (minute-level peak
  per unit in a group of 200).
- **Simulation:** `simulated_units`, `simulated_days`.
- **Curves:** `hourly_w` {all, weekday, weekend} (24 values, W per unit) and `characteristics`.
- **`share_from_data`:** the share of parameters resting on data rather than assumptions.
- **`appliances`:** each has the following.
  - Parameters: `name`, `ownership_share`, `number`, `power_w`, `minutes_per_day`, `hours_per_day`, `windows_min`
    [[start, end] minutes after midnight].
  - `wh_per_day_all_on`: the upper bound if every unit were on for the whole time; RAMP switches on fewer.
  - `ramp`: RAMP behaviour parameters.
  - `provenance` {field: {type: `survey` | `evidence` | `measured` | `assumption`, ids, derivation, rationale}}.

##### `profiles/evidence_index.json`
`{id: {...}}` for every id a profile cites. There are three kinds of entry:
- `evidence`: `doc_id`, `page`, verbatim `quote`, `value`, `unit`.
- `survey`: an MTF 2022 weighted aggregate row.
- `measured`: a QSEL document, page and parameter.

Join `doc_id` to `sources.evidence_documents` for titles and links.

##### `validation/checks.json`
`household_tiers` (simulated vs calibration target, MTF energy tier), `household_anchors` (setting, our mix,
anchor, ratio, verdict, source), `establishments`, `labels` (macro-F1, n_gold, cost, figure), `evidence` (values
checked/flagged), and `verdict_rule`.

##### `sources/sources.json`
- `datasets`: `id`, `name`, `owner`, `role`, `access`, `licence`, `status`, `url` (open sources only), `files`,
  `megabytes`, `citations`.
- `evidence_documents`: `doc_id`, `title`, `publisher`, `year`, `url`, `licence`, `status`, `topics`.
- `citations`: `{key: citation string}`; use verbatim.
- `restricted_notice`.

##### `workflow/workflow.json` and `workflow.mmd`
`{lanes: [{id, label}], nodes: [...], edges: [{from, to}]}`.
- Every node has `id`, `lane`, `kind` (`source` | `process` | `table` | `gate` | `output`) and `label`.
- Nodes may also have `status`, `command`, `description`, `restricted`, `records` (table row counts), `file`,
  `source` and `citations`.
- `workflow.mmd` is the same graph in Mermaid, checked to parse.

#### 6. Client-side formulas (must match the exporter; JS-like pseudocode)
```js
// lib = profiles.json profiles, in weights.profile_order; P[k][h] = lib[k].hourly_w.all[h] (W per unit)
buildingCurve(w)  = h => sum_k w[k] * P[k][h]                       // W, 24 values (weekday/weekend alike)
annualKwh(w)      = 365 * sum_k w[k] * lib[k].daily_kwh
peak(curve)       = { w: max(curve), hour: argmax(curve) }          // Estimated Peak Load (expected, coincident)
selectionCurve(B) = h => sum_{b in B} buildingCurve(weights[b])(h)  // area peak = max of THIS, not a sum of peaks
characteristics(c, cWeekday, cWeekend):
  load_factor = mean(c) / max(c);  evening_share = sum(c[18..22]) / sum(c)
  daytime_share = sum(c[8..16]) / sum(c);  night_base_w = mean(c[0..4])
  weekday_weekend_ratio = sum(cWeekday) / sum(cWeekend)
  shape = evening_share >= 0.4 && peak.hour in 17..22 ? "evening peak"
        : daytime_share >= 0.5 ? "daytime" : load_factor >= 0.6 ? "flat" : "mixed"
puScore(comp, wts) = 100 * sqrt(comp.pu_likelihood)
                   * sum_{k present} wts[k] * comp[k] / sum_{k present} wts[k]   // k over the enabling components
puCategory(s)      = s < 33.3 ? "low" : s < 66.7 ? "medium" : "high"
```
- The probability ↔ top-class toggle swaps `weights.probabilities` for `weights.top_class`.
- The productive-use sliders change `wts`. Recompute every building from `weights.pu_components`, which has the
  same order as `pu_component_order`. With the default weights the result equals the stored `pu_score`.

#### 7. Restricted data, licences, attribution
- **Restricted microdata:** DHS 2025, World Bank MTF 2022 and NISR EICV5 microdata **never** enter the bundle. Only
  their published aggregates appear, inside the profiles (`provenance` ids `mtf:…`) and checks. Show
  `sources.restricted_notice` wherever they're cited.
- **Footprints:** ODbL-1.0 (share-alike for derived databases).
- **Meta RWI:** CC-BY-NC-4.0, so the bundle is for non-commercial use unless the RWI fields (`rwi`, `rwi_mean`,
  `ability_to_pay`) are removed.
- **Map attribution:** show `meta.attribution` on every map.

#### 8. What isn't in v1 (Planned) and the production path
- **Not in v1:** national building-level demand; Expected demand (× P(grid connection)); single-building
  instantaneous peaks; seasonal/monthly profiles (after the Copernicus CDS climate rebuild); the agricultural
  context component of the productive-use score. Show these as Planned empty states.
- **v2 production path:**
  - national footprints (6.4M) as **PMTiles vector tiles** (tippecanoe), styled with the same fields;
  - the GeoParquet tables published for **DuckDB-WASM** queries over HTTP range requests;
  - the same static hosting, still with no server.

#### 9. Regenerating and checking
```
pixi run profile-library --buildings     # after any archetype change
pixi run dashboard-data --sample         # dashboard/sample_data/v1 (commit)
pixi run dashboard-data                  # data/dashboard/v1 (publish)
pixi run test                            # includes bundle formula and integrity tests
```
