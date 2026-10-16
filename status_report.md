# Rooftops → Load Curves: status report and decision log

*OSEAS26 challenge: building footprints → energy demand · repo `esemsc-as4623/peek-load` · updated 2026-10-16*

One file for the organisers and for us: what the pipeline does (section 2), results (3–4), every decision taken
(5), what is still open (6), opportunities (7) and bookkeeping (8). Section 4 also checks the model against REG,
RURA and Ministry of Health figures. It replaces `decisions.md`. Owner sign-offs are recorded in
`data/gold/approvals.yaml`.

## 1. Summary
We turn Rwanda's 6.4 million open building footprints into building-level electricity demand. Every building gets
its shape, height, surroundings, climate and a use. Use-specific appliance profiles (RAMP) then give a load curve,
peak and annual energy. The pipeline is a chain of separate steps (section 2), each with a clear input, output and
check. It is modular, so we can swap in ML models and decisions and see their effect.

Current state (2026-10-15):
- **All six national building tables** are built and pass QA.
- **Every unit of demand sits on a building, nationally.** A classical classifier gives every building use
  probabilities. Census and registry counts are then placed on the most likely buildings, rarest first.
  **3,489,045 buildings carry demand.**
- **Demand is constrained to the aggregates we hold**:
  - households: census counts per district, urban and rural;
  - establishments: Establishment Census counts (a quarter of rural manufacturing as mills, owner 2026-10-15);
  - schools: MINEDUC counts; health facilities: Ministry of Health counts (63 hospitals, owner 2026-10-16);
  - wealth split: DHS 2025 per district;
  - grid connection: EICV7 2024 shares per district, rolled back per district to EICV4 (2014) and EICV5 (2017);
  - household consumption: REG billing means (owner's choice over QSEL, 2026-10-15).
- **National totals:**
  - **potential demand 423 GWh/yr** (every building connected);
  - **expected demand 320 GWh/yr** (2024 connection);
  - **back-cast to 2016 (205 GWh expected) and projected to 2030 (389 GWh).**
- **Checked against REG sales** (section 4): households' expected demand is 178 / 193 / 208 GWh in 2021 / 2022 /
  2023 against REG residential sales of 154 / 179 / 211 GWh; connected households per REG branch correlate at
  r = 0.93 (27 branches outside Kigali); health facilities 23.0 GWh vs REG's 22.7 (SEforALL measurements, MoH
  counts). One gap remains: businesses (non-residential: model 78 vs REG 257 GWh).
- **A release package for Hugging Face** is built (`pixi run release-build`, section 8.4).
- Uncertainty bands per sector and a spacing-cap sensitivity are reported (section 4).
- A static dashboard bundle and a design prototype exist. The prototype's pages now run as a static site in `app/` (Vite + Preact), with the data hosted beside it on our server (`docs/hosting.md`).

**How to read the numbers.** Most of our "ground truth" is aggregated: district census counts, survey clusters,
national averages. Building-level values are a *plausible allocation* that reproduces those totals. They are not
observations. Each step says which totals it matches and how we check it. Gold labels are an uncertain reference
(the imagery shows little beyond vegetation and roof shape), so agreement with them is consistency, not accuracy.

## 2. The pipeline, step by step

| # | Step and goal | Input (raw data) | Output | Workflow, assumptions and decisions | Evaluation / extension |
|---|---|---|---|---|---|
| 1 | **Footprints.** One clean, de-duplicated layer | VIDA combined Open Buildings (Google V3 + Microsoft + OSM, ODbL), 6,434,264 polygons | `buildings_base`: **6,434,247 buildings** (Google 90.4%, Microsoft 7.6%, OSM 2.0%) | 17 exact duplicates dropped. Flagged, not dropped: low confidence < 0.70 (12.6%), tiny < 6 m² (0.5%), nested same-source (2,186) | QA (schema, validity, duplicates). Microsoft omits structures < ~15 m² |
| 2 | **Height, floors, presence by year** | Google Open Buildings 2.5D Temporal (height 2023, presence 2016–2023); OSM `building:levels` | `building_height`: height for 71.6% (median 3.3 m); floors; presence 2016–2023 | Floors by cut-points (1 below 6.0 m, 2 below 8.5 m, +1 per 3 m), fitted on even ids, **78.1% exact on odd ids**. Floors cap households per building | Heights are compressed (single storeys read 4–5 m). Floor regression: opportunity O4 |
| 3 | **Building context** | OSM, Overture, healthsites.io, Gridfinder, geoBoundaries, GHSL, WorldPop, Meta RWI, Copernicus DEM, ESA WorldCover; **new: VIIRS Black Marble night lights 2016–2024** | `building_context` (admin to village, tags, distances, density, RWI, elevation); `cropland_h3r8`; **`ntl_h3r8`** (radiance per H3 r8 cell and year) | Spatial joins in DuckDB. Only 37,132 buildings (0.6%) carry a usable tag | QA coverage. Night lights are downloaded; using them as a feature is O6 |
| 4 | **Climate** | ERA5-Land hourly 2019–2025 (Copernicus CDS); DEM; NASA POWER checks | `climate_h3`: 4,034 H3 r7 cells; cooling class | Lapse-rate corrected (−6.16 K/km). Cooling is marginal in most of Rwanda | 1991–2020 baseline: 8 of 60 requests in. Seasonal shape: O1 |
| 5 | **LLM labels (deep dives)** | Context cards from steps 1–4; 173 human gold labels | `labels`: 44,556 buildings (Nyamirambo, Nasho, part of Muhoza) | Claude Haiku 4.5, prompt v2, batch + cache | 39% agreement with uncertain gold. Used as training labels and class rates, not as truth |
| 6 | **Use classifier** | Steps 1–3 features (27); tag labels (A) and tag + LLM labels (B) | `use_probs`: 8 class probabilities for every building | HistGradientBoosting; gold labels never trained on | Held-out AUC: tags by district 0.78, LLM by sector 0.76, ancillary 0.82 (model B) |
| 7 | **Evidence and aggregates** | 26 public reports; restricted DHS 2025, MTF 2022, EICV5 | `evidence` (3,076), validation anchors, MTF/EICV5/DHS aggregates (cells < 10 suppressed) | Quotes verified on the page; plausibility flags exclude 17 of 1,077 | Restricted microdata never leaves `data/restricted/` |
| 8 | **Appliance profiles (RAMP)** | Step 7; `config/archetypes.yaml` | **23 profiles**: 18 built archetypes (17 signed off 2026-10-12; **rural bar**, P1) + derived: **health post, health centre, hospital, rural hospital** (P5, signed off 2026-10-16), **private clinic** (assumption), **rural workshop** (= carpentry, P2) | Households from MTF grid households by setting × Ubudehe half, lighting calibrated to MTF spending. **Health (P5, 2026-10-16):** daily energy from SEforALL's 2023 site surveys (hospital 825, health centre 10.9, health post 2.9 kWh/day, suppressed demand included); rural hospitals 3.346 kWh/bed/day (Nwanya et al. 2016) × 207 beds (R-HMIS mean district hospital) = 693 kWh/day; private clinics = a health centre (assumption); hourly shape of the appliance-built health facility. **Rural bar (P1):** lights, plus fridge, TV and radio at the shares of MTF 2022 grid household shops/traders (fridge 19%) | `reports/demand/profile_checks.md`: school, mill, carpentry ok. Health facilities total 23.0 GWh vs REG 22.7 (QA bound ±25%) |
| 9 | **Census and registry targets** | Establishment Census 2023 tables; 2022 census households (urban/rural); **MINEDUC school counts 2020/21**; **worship count (news reports of RGB figures)**; **DHS 2025 wealth** | `pu_census_districts`, `svc_census_districts`, `registries/schools_by_district_2021.csv`, `dhs_wealth_district.csv` | Manufacturing (rural: **25% mills, 75% light workshops**, P2), services and retail per district × setting. **5,904 schools** (primary 3,691, secondary 1,869, TVET 344). **7,000 places of worship** (range 6,000–9,000), spread by households. **Health (P5):** 127 healthsites "hospital" points curated into **63 hospitals** (MoH 2023; 50 public, 13 private: units merged, specialist and small private ones re-typed as clinics, `pixi run hospitals`); MoH counts of **518 health centres, 1,408 health posts + dispensaries, 232 clinics** spread by households | Section-level census only: trade and service splits stay assumptions (P1–P3) |
| 10 | **Allocation** (`config/allocation.yaml`, signed off 2026-10-15) | Steps 6 and 9; tags; facility points | `building_use` (one use per building, establishment, its share, households, wealth, setting), `building_profiles` (units) | Order: (0) tags; (0b) **facility compounds**: tagged school and health buildings, plus institution-like buildings within 150 m (schools) or 100 m (health), share one facility (weight 1/n); pharmacies are shops. (1) productive use; (2) services; (3) retail; (4a) **schools to the MINEDUC count**; (4b) **worship to the national count**; (4c) offices and stores at LLM rates; (5) households, **urban and rural separately**, up to one per floor; **upper/lower wealth per district × setting from DHS**; (6) the rest is ancillary. Spacing caps per H3 cell | QA: every target met, no shortfall; urban share per district = census (Kirehe 0.068 vs 0.07); upper share 0.359 rural / 0.547 urban = MTF |
| 11 | **Grid connection** | DHS 2025 households + displaced cluster GPS (restricted) | `grid_connection`: P(connected) for all buildings | Logistic regression on neighbourhood features | AUC 0.75; district r = 0.93 vs EICV7 |
| 12 | **Calibration (new)** | Steps 10–11; EICV7 2024 district grid shares; REG means via QSEL 2024 | `building_profiles` gains `p_grid`, `scale`, energy `weight`; `calibration.csv`; `building_demand_preview` (potential and expected kWh) | **A9:** P(connected) shifted per district on the logit scale (−1.15 to +0.58) so households match EICV7. **REG:** household energy scaled per region so a connected household uses REG's mean: Kigali ×1.01 (24.75 kWh/month), elsewhere **×0.42** (5.92) | QA: every district within 0.005 of EICV7. QSEL rural check: 4.4 vs 8.1 kWh/month (P4) |
| 13 | **Demand by year (new)** | Step 12; presence 2016–2023; censuses 2012/2022; **EICV4 2014 and EICV5 2017 grid lighting per district** | `demand_by_year.csv` (district × year), `building_demand_by_year.parquet` (expected kWh per building, 2016–2023) | Presence shows *where* and *in which order* buildings appeared; the census rate (3.1%/yr) sets *how much* (raw presence implies 7.5%/yr). Connection rolls back **per district** (logit shift, linear between EICV4 2014, EICV5 2017 and EICV7 2024; e.g. Kirehe 0.08 → 0.16 → 0.54). Projection: district growth (2.2–4.5%/yr), connection held at 2024 | Checked against REG residential sales 2021–2023 and customers 2017–2023 (section 4) |
| 14 | **Uncertainty and sensitivity (new)** | Step 10 re-run | `uncertainty/sector_bands.csv`, `uncertainty/building_agreement.parquet`, `reports/demand/cap_sensitivity.csv` | 10 allocations with class probabilities × exp(0.5·N(0,1)); profile p10/p90; caps ×0.5 / ×1 / ×2 / ×4 / none | Section 4 |
| 14b | **Benchmarks (new)** | REG/RURA electricity sold by category (2021–2023, Q2 2023–Q2 2024), REG customers per branch (2017–2023), MoH facility counts (2017–2023) | `benchmarks.csv`, `reports/demand/benchmarks.md` | `pixi run energy-statistics` parses the reports; `pixi run demand-benchmarks` compares | Section 4 |
| 15 | **Dashboard bundle and QA** | All steps | Static JSON bundle; `reports/qa/*.md` | `pixi run demand` runs steps 6–15 in order and ends with QA | Every building in the sample recomputed from the published formulas in a test |

## 3. Per district

Buildings, tags and labels: `reports/status/district_table.md` (`pixi run status-figures`). Demand by district:

| District | Households (urban share) | Upper-wealth share | Productive use | Schools | Hospitals | Grid share 2014 → 2017 → 2024 (EICV4/5/7) | Potential GWh/yr | Expected GWh/yr | Growth %/yr | Expected 2030 |
|---|---|---|---|---|---|---|---|---|---|---|
| Gasabo | 249,420 (0.83) | 0.59 | 1,628 | 242 | 11 | 0.68 → 0.75 → 0.87 | 80.4 | 73.6 | 3.1 | 90.6 |
| Kicukiro | 135,463 (0.99) | 0.58 | 1,098 | 157 | 4 | 0.81 → 0.84 → 0.94 | 47.4 | 45.2 | 3.3 | 56.3 |
| Nyarugenge | 103,985 (0.88) | 0.69 | 1,465 | 103 | 5 | 0.76 → 0.81 → 0.94 | 37.8 | 36.5 | 2.6 | 43.4 |
| Rubavu | 124,080 (0.56) | 0.49 | 847 | 185 | 2 | 0.28 → 0.41 → 0.68 | 15.8 | 12.8 | 3.9 | 16.3 |
| Bugesera | 137,777 (0.40) | 0.45 | 741 | 230 | 1 | 0.14 → 0.19 → 0.59 | 14.9 | 10.8 | 3.6 | 13.1 |
| Nyagatare | 160,435 (0.25) | 0.37 | 635 | 330 | 2 | 0.20 → 0.15 → 0.38 | 14.7 | 8.3 | 2.6 | 9.5 |
| Musanze | 119,387 (0.49) | 0.32 | 869 | 192 | 2 | 0.24 → 0.32 → 0.64 | 14.2 | 11.2 | 4.1 | 14.4 |
| Rwamagana | 121,051 (0.35) | 0.48 | 639 | 189 | 1 | 0.27 → 0.28 → 0.57 | 12.7 | 9.3 | 2.9 | 10.8 |
| Kamonyi | 116,378 (0.31) | 0.48 | 601 | 210 | 1 | 0.09 → 0.18 → 0.29 | 12.3 | 6.7 | 3.6 | 8.4 |
| Rusizi | 104,937 (0.33) | 0.37 | 580 | 239 | 2 | 0.29 → 0.32 → 0.65 | 11.0 | 8.7 | 4.9 | 11.8 |
| Gatsibo | 136,208 (0.11) | 0.41 | 504 | 253 | 2 | 0.13 → 0.14 → 0.31 | 11.0 | 5.8 | 2.6 | 6.7 |
| Kayonza | 114,186 (0.15) | 0.46 | 557 | 171 | 1 | 0.17 → 0.19 → 0.40 | 9.9 | 6.0 | 3.0 | 7.1 |
| Muhanga | 93,241 (0.23) | 0.46 | 546 | 217 | 1 | 0.17 → 0.20 → 0.38 | 9.2 | 6.0 | 3.0 | 7.1 |
| Gicumbi | 109,373 (0.06) | 0.48 | 587 | 229 | 1 | 0.04 → 0.12 → 0.40 | 9.1 | 5.7 | 2.9 | 6.5 |
| Kirehe | 113,886 (0.07) | 0.25 | 515 | 188 | 1 | 0.08 → 0.16 → 0.54 | 8.8 | 6.1 | 3.1 | 7.1 |
| Huye | 96,037 (0.18) | 0.38 | 415 | 166 | 2 | 0.17 → 0.14 → 0.47 | 8.6 | 5.8 | 2.5 | 6.5 |
| Ngoma | 102,589 (0.09) | 0.31 | 479 | 175 | 1 | 0.07 → 0.18 → 0.38 | 8.3 | 4.9 | 2.2 | 5.4 |
| Rulindo | 91,909 (0.11) | 0.53 | 404 | 192 | 3 | 0.09 → 0.15 → 0.31 | 8.2 | 4.6 | 3.3 | 5.6 |
| Gakenke | 93,609 (0.04) | 0.44 | 423 | 203 | 3 | 0.05 → 0.12 → 0.28 | 8.1 | 4.6 | 3.3 | 5.6 |
| Karongi | 91,444 (0.10) | 0.21 | 405 | 252 | 3 | 0.09 → 0.14 → 0.31 | 8.1 | 4.8 | 2.4 | 5.5 |
| Nyamagabe | 92,052 (0.10) | 0.25 | 452 | 202 | 2 | 0.06 → 0.09 → 0.21 | 8.1 | 4.2 | 2.6 | 4.9 |
| Ruhango | 94,508 (0.11) | 0.37 | 382 | 165 | 2 | 0.09 → 0.20 → 0.40 | 7.9 | 4.9 | 2.7 | 5.6 |
| Nyamasheke | 95,229 (0.08) | 0.46 | 416 | 241 | 2 | 0.09 → 0.22 → 0.47 | 7.9 | 5.3 | 3.6 | 6.5 |
| Nyabihu | 76,391 (0.24) | 0.38 | 329 | 150 | 2 | 0.10 → 0.17 → 0.40 | 7.5 | 4.7 | 3.0 | 5.5 |
| Burera | 91,786 (0.10) | 0.28 | 421 | 183 | 1 | 0.10 → 0.18 → 0.52 | 7.5 | 5.1 | 3.4 | 6.1 |
| Nyanza | 93,007 (0.10) | 0.40 | 351 | 170 | 1 | 0.11 → 0.14 → 0.35 | 7.3 | 4.1 | 3.4 | 4.9 |
| Ngororero | 92,623 (0.05) | 0.26 | 425 | 176 | 1 | 0.05 → 0.07 → 0.34 | 7.1 | 4.0 | 2.7 | 4.5 |
| Rutsiro | 86,802 (0.06) | 0.28 | 439 | 183 | 1 | 0.07 → 0.11 → 0.41 | 7.0 | 4.4 | 3.6 | 5.3 |
| Gisagara | 101,145 (0.03) | 0.29 | 298 | 154 | 1 | 0.01 → 0.10 → 0.37 | 7.0 | 3.8 | 3.0 | 4.3 |
| Nyaruguru | 73,805 (0.03) | 0.24 | 296 | 157 | 1 | 0.03 → 0.09 → 0.28 | 5.5 | 2.8 | 2.9 | 3.3 |
| **Rwanda** | **3,312,743** | | **17,747** | **5,904** | **63** | 0.20 → 0.27 → 0.61 | **423** | **320** | | **389** |

## 4. Results

### Deep dives

| | Nyamirambo (Kigali) | Muhoza (Musanze) | Nasho (Kirehe) |
|---|---|---|---|
| Buildings / carrying demand | 21,126 / 14,515 | 32,538 / 16,241 | 17,374 / 9,257 |
| Census households placed (urban share) | 14,210 (0.98) | 15,541 (0.89) | 9,053 (0.00) |
| Productive use (of which mills) | 216 (0) | 294 (3) | 50 (22) |
| Services / shops | 500 / 1,521 | 458 / 647 | 178 / 172 |
| Health facilities (whole; of which hospitals) | 7.2 (1) | 9.4 (1) | 3 (0) |
| **Potential / expected energy** | **6.00 / 5.93 GWh** | **3.00 / 2.68 GWh** | **0.73 / 0.50 GWh** |
| Uncertainty, combined p10–p90 (potential) | 5.92–6.06 GWh (−1.4% / +1.0%) | 2.95–3.04 GWh (−1.7% / +1.4%) | 0.71–0.74 GWh (−2.3% / +1.8%) |

### National energy by profile (GWh/yr, potential / expected)

Households 217 expected (potential 320). Grain mills 21.5 (2,271; 17–26 for a 20–30% share), bars/restaurants 17.2
(22,355 in towns) + rural bars 5.2 (37,849), shops 15.9, **hospitals 13.6 (45 urban) + 4.6 (18 rural)**, welding 5.7
(town trades 5.4–9.8 for a 20–50% welding share, P3), schools 3.9, salons 2.7, guesthouses 2.4, offices 2.3, health
centres 2.2 (550), health posts 1.5 (1,408), clinics 1.2 (297), tailoring 1.2, rural workshops 1.1 (6,809), churches
0.5, carpentry 0.5, stores 0.3. Establishments count as connected.

### Against REG, RURA and Ministry of Health figures (new data, 2026-10-15)

`pixi run demand-benchmarks` → `reports/demand/benchmarks.md`. Sources: NISR Statistical Yearbook 2024 (REG tables),
RURA Electricity Statistics Q2 2024, EICV3/4/5 thematic reports, Mugyenyi et al. (2025, *Energy Policy*), SEforALL
*Powering Healthcare in Rwanda* (2023).

| Check | Official | Model | Ratio | Reading |
|---|---|---|---|---|
| Residential sales 2021 / 2022 / 2023 (GWh) | 154 / 179 / 211 | 178 / 193 / 208 | 1.16 / 1.08 / 0.99 | levels follow from the REG constraint; the 2021–23 growth (+17% vs REG +37%) is slower: REG's per-customer use rose too |
| Residential customers 2017 / 2020 / 2023 (× 0.88 residential) | 629k / 1,038k / 1,324k | 718k / 1,060k / 1,525k connected households | 1.14 / 1.02 / 1.15 | households vs meters: shared meters make the model higher |
| Customers per branch 2023 | 28 branches | | r = 0.93 outside Kigali | per branch 0.60 (Nyaruguru) to 1.30 (Rwamagana) |
| Health facilities electricity 2023 (GWh) | 22.7 | 23.0 (hospitals 18.1, centres 2.2, posts 1.5, clinics 1.2) | 1.01 | P5 decided 2026-10-16; QA bound ±25% |
| Hospitals / health centres / posts + dispensaries / clinics (2023) | 63 / 518 / 1,408 / 232 | 63 / 550 / 1,408 / 297 | | constraints; tagged centres and clinics above a district's population share are kept |
| Hotels (GWh) | 51.3 | 2.4 (guesthouses) | 0.05 | large hotels aren't in the census services split |
| Non-residential + small industry (GWh) | 257 | 78 | 0.30 | see P10 |
| Coincident peak (MW) | 233.7 (system, Q2 2024) | 110 at 20:00 | 0.47 | system peak also carries industry, water, telecoms, losses |

Mugyenyi et al. (2025), on 361 million REG prepaid transactions (811,541 customers, 2012–2020, 15 districts), is
the source of REG's 297 / 71 kWh per customer (Kigali / elsewhere). Customers connected since 2017 use 8–14
kWh/month, and demand growth comes from new connections rather than higher use by existing ones; price elasticity
for low-consumption households is −0.31.

### Spacing-cap sensitivity (A6)

`reports/demand/cap_sensitivity.csv` (caps on every establishment kind ×0.5, ×1, ×2, ×4, none). Without caps the
best-scoring buildings cluster; with them, placements spread to more cells at little cost in fit:

| Caps | Mills: H3 r8 cells used | Most mills in one block (r9) | Nasho mills (MWh/yr) | Bars (towns): most in one block | Shops: most in one block | Above base cap (all placed) |
|---|---|---|---|---|---|---|
| ×0.5 | 2,269 | 1 | 22 (209) | 14 | 54 | as base for kinds capped at 1 |
| **base** | **2,269** | **1** | **22 (209)** | **16** | **54** | **6.2%** (QA limit 10%) |
| ×2 | 1,376 | 2 | 26 (247) | 17 | 54 | 23–42% by kind |
| ×4 | 930 | 4 | 29 (275) | 23 | 64 | 41–62% by kind |
| none | 614 | 39 | 26 (247) | 208 | 499 | 58–76% by kind |

With 2,271 mills (P2) the caps matter less than before: productive-use energy in Nasho ranges from 213 MWh/yr
(base) to 277; in Nyamirambo 135–278 and in Muhoza 253–529. District totals never change (they are census counts);
the caps move energy *within* districts.

### Uncertainty (B6)

Ten allocations with every class probability multiplied by exp(0.5·N(0,1)) per building, plus each profile's
RAMP p10/p90 day (`data/processed/uncertainty/`, figure `C8_uncertainty.png`):
- **National potential:** allocation p10–p90 421–426 GWh/yr, profile p10–p90 411–431, base 423.
- **Sectors:** the combined band is ±6% wide at the median sector (p90 9%). District constraints hold the totals,
  so the allocation band is narrower than the profile band.
- **Buildings:** 90% keep their base use in a single perturbed run, and 65% in all ten. Mean agreement by use:
  homes 0.92, productive use 0.92, ancillary 0.90, institutions 0.81, stores 0.70, shops 0.60, shop-houses 0.37 and
  churches 0.36. Totals are robust; *which* building holds a shop or church is not.

## 5. Decisions made (dated log)

| Date | Decision | By | Where |
|---|---|---|---|
| 2026-10-11 | Evidence review: blanket approval backed by automated checks | owner | approvals.yaml `evidence_review` |
| 2026-10-11 | Building → profile mapping (household ids renamed 2026-10-12) | owner | `profile_mapping` |
| 2026-10-12 | Archetype assumptions signed off (17 profiles) | owner | `archetypes` |
| 2026-10-12 | Class probabilities < 0.10 ignored (now superseded by the allocation) | owner | `class_probabilities` |
| 2026-10-12 | Mills only rural and roadside; tailors, welders and carpenters in towns | owner | `productive_use_method` |
| 2026-10-12 | Gold labels are an uncertain reference, never trained on | owner | `gold_labels` |
| 2026-10-15 | All demand on buildings; classical classifier (A tags, B tags + LLM); census counts on best candidates, rarest first | owner | `building_level_allocation` |
| 2026-10-15 | Households per building: census households, up to one per floor (A1); shops placed to census retail (A3); national coverage by classifier (was A9); area-level "unlocated" amounts removed | owner | `building_level_allocation` |
| 2026-10-15 | **Allocation rules signed off as defaults (A6)**, cap sensitivity reported | owner | `allocation_rules` |
| 2026-10-15 | **A7:** schools from MINEDUC counts; buildings near a school facility take the school profile (compound shares 1/n); worship from a national count spread by population | owner | `constraints_and_registries` |
| 2026-10-15 | **A8:** census urban/rural household shares imposed per district | owner | same |
| 2026-10-15 | **A9:** both: expected = potential × P(connected), with P raked to EICV7 2024 per district | owner | same |
| 2026-10-15 | **A10:** health facilities split by type (post / centre / hospital; pharmacies as shops) | owner | same |
| 2026-10-15 | **Constraints:** census urban/rural households, EICV7 grid shares, REG consumption by region; DHS wealth by district (was B7) | owner | same |
| 2026-10-15 | **Years:** back-cast by year from building presence; project with district growth (was B3) | owner | same |
| 2026-10-15 | **B6** uncertainty bands added; **B5** (targeted relabelling) dropped; floor cut-points kept (regression is O4); seasonal and weekly shape left as O1 | owner | same |
| 2026-10-15 | Validation is lower priority than using every aggregate as a constraint (A11 on hold) | owner | this file |
| 2026-10-15 | **P4:** household energy matched to REG billing means, not QSEL's rural survey | owner | `pending_decisions_2026_10_15` |
| 2026-10-15 | **P1:** a lighter rural bar profile (MTF 2022 grid household shops/traders) | owner | same |
| 2026-10-15 | **P2:** 25% of rural manufacturing are mills (range 20–30%), the rest light rural workshops | owner | same |
| 2026-10-15 | **P3** equal town-trade shares kept with a range; **P6** worship count kept; **P7** hold-out after the finals' core work; **P8** use-model choices signed off | owner | same |
| 2026-10-15 | Connection back-cast per district from EICV4/EICV5 (new data); REG/RURA/MoH figures added as benchmarks | owner (data supplied) | this file |
| 2026-10-16 | **P5/P9:** health facilities from SEforALL's measurements with MoH counts; only the 63 named hospitals are hospitals, other tagged "hospitals" re-typed as clinics; rural hospitals per bed (Nwanya et al. 2016); REG's 22.7 GWh as the check | owner | `health_facilities` |
| 2026-10-16 | Final dataset prepared for Hugging Face (private first; public after the licence review) | owner | this file |

## 6. Pending considerations

| # | Item | What we do now | Why it matters | Options | Recommendation |
|---|---|---|---|---|---|
| P10 | **Businesses under-count (new)** | Establishments from census counts × profiles: 78 GWh/yr (non-residential + small industry scope) | REG sold 257 GWh to non-residential + small-industry customers in 2023 (51 more to hotels). The census counts small establishments; offices, banks, public buildings, markets and large shops are under-represented, and the shop profile (9 kWh/month) is a kiosk | (a) report as a gap; (b) scale establishment energy per region to REG non-residential sales (as done for households); (c) add profiles for large commercial and public buildings by floor area | (a) now; (b) or (c) after the mid-review |
| P11 | **Faster growth in REG sales than in the model** | Back-cast holds per-household use fixed | REG residential sales grew 37% from 2021 to 2023, the model 17%: use per customer rose too (Mugyenyi et al. see flat use per cohort, so this is new customers plus more appliances) | (a) keep; (b) a per-year consumption trend fitted to REG 2021–2023 | (a) |

## 7. Future opportunities

| # | Opportunity | Data we have | Approach | Effort |
|---|---|---|---|---|
| O1 | **Seasonal and weekly shape** (was B2) | monthly ERA5-Land climate, school calendar, harvest seasons | seasonal RAMP variants: fans by cooling class, mills busy after harvests, schools closed in holidays | medium |
| O2 | **REG data** (customers and kWh by village, feeder loads; see 8.1) | none yet | village-level constraints on connection and consumption; observed hourly shapes for validation | small once obtained |
| O3 | **ISIC4 establishment counts** | public microdata is section-only | resolves P1–P3: mills vs other manufacturing, bars vs lodging, salons | request to NISR |
| O4 | **Floors by regression** (was B4) | 9,846 OSM-levelled buildings | regression in place of the cut-points (kept for now) | small |
| O5 | **Wealth beyond DHS** | Meta RWI, DHS clusters | done at district × setting; a within-district RWI calibration could follow | small |
| O6 | **Night lights as a feature** | Black Marble 2016–2024 per H3 r8 (downloaded) | add to the use classifier and grid model; activity check on the back-cast | small |
| O7 | **Establishment size** | EC2023 microdata `size2`, `Total_workers` (when downloadable) | scale establishment energy by size class | small |
| O8 | **Off-grid and solar profiles** | MTF solar tiers | expected demand for unconnected households | medium |
| O9 | **Customers per district as a constraint** | REG customers per branch 2017–2023 (yearbook) | rake connected households per branch, or check the back-cast year by year | small |
| O10 | **New-connection cohort effect** | Mugyenyi et al. 2025 (8–14 kWh/month for 2017+ connections) | lower use for households connected after 2024 in the projection | small |

## 8. Bookkeeping

### 8.1 Data to acquire

| Priority | What | Why | How |
|---|---|---|---|
| 1 | **REG data**: (a) billing aggregates: number of customers by tariff category and kWh sold per month, per village or cell; (b) **feeder loads**: hourly or 15-min MW per MV feeder (15/30 kV) from substation SCADA; (c) MV/LV network GIS (lines, transformers) | (a) village-level connection and consumption constraints, the sharpest check on step 12; (b) real load shapes and seasonality to validate the RAMP curves and cooling; (c) better grid distance than Gridfinder | request via the organisers / REG |
| 1 | **NISR Establishment Census ISIC4 counts** (restricted file or a custom table: district × urban/rural × 4-digit activity) | resolves P1–P3 | request to NISR via the organisers |
| 2 | EC2023 public microdata (catalog 112) | district × setting × section counts, size, market-place location | not downloadable for us on 2026-10-15; the 2020 report (section level) is held instead |
| 2 | MoH Master Facility List with coordinates (names and types of the 63 hospitals) | would replace the name rules in `config/health_facilities.yaml` (50 public found vs MoH's 52) | MoH / organisers |
| 2 | SEforALL site-survey load profiles per facility (annex "Detailed Load Assessment of HF") | P5 hourly shapes; daily totals are in hand | SEforALL report (downloaded) |
| 2 | EnAccess MicroPowerManager anonymised meter data | real mini-grid load curves for validation | organisers |
| 3 | Kigali Master Plan zoning GIS; NISR sector-level 2022 households; EICV7 microdata | sharper Kigali labels; sector-level checks | City of Kigali; NISR |

Received: MTF 2022 microdata (2026-10-11), productive-use reports (37 curated values), MINEDUC yearbook 2020/21,
NASA Earthdata token (Black Marble downloaded 2026-10-15), NISR Establishment Census 2020 report; on 2026-10-15 also
the NISR Statistical Yearbook 2024, RURA Electricity Statistics Q2 2024, EICV3/4/5 Utilities and Amenities thematic
reports and Mugyenyi et al. (2025). Fetched: SEforALL *Powering Healthcare in Rwanda* (2023).

### 8.2 Human gates

| Gate | Status |
|---|---|
| Codebook (9 use classes) | approved |
| Gold labels (173 of 400) | done; an uncertain reference |
| Evidence review | approved 2026-10-11 (blanket, backed by automated checks) |
| Archetype assumptions (17) | signed off 2026-10-12 |
| Allocation rules | **signed off 2026-10-15** |
| Registries, constraints, years, bands | **requested by the owner 2026-10-15** |
| REG over QSEL; rural bar; mill share; town-trade shares; worship count; use-model choices | **decided 2026-10-15** (P1–P4, P6, P8) |
| Health facilities | **decided 2026-10-16** (P5, P9); private-clinic energy = a health centre is an assumption |
| Business under-count | pending (P10) |
| Licence review before publishing (ODbL share-alike, Meta RWI non-commercial) | pending |

### 8.3 Housekeeping

| Item | Status |
|---|---|
| 1991–2020 climate baseline | 8 of 60 Copernicus requests done; fallback: compute from the hourly product |
| Evidence rows added after the blanket approval (11 Power Africa values) | covered by automated checks only |
| Low-confidence Google footprints (12.6%) | eligible like any other; consider down-weighting in the allocation scores |
| Two tags on one building | the building's own facility/worship/store tag wins; the establishment is placed by the census step |
| API spend | ~$157 of the $170 cap; responses cached, so re-runs cost nothing. New documents are curated in the coding session |
| Earthdata token | in `.env` as `EARTHDATA_TOKEN` (expires after 60 days; LAADS archive URLs need a browser login, so downloads use Earthdata Cloud) |

### 8.4 Release (Hugging Face)

`pixi run release-build` writes `data/release/v1.0.0/`: a GeoParquet building table (one row per footprint, 30
district files, the five challenge metrics, use, allocation, connection probabilities, yearly expected kWh), the
profile library and building weights, district and sector totals, demand by year, the benchmarks, the docs and a
dataset card (`README.md` with Hub metadata), `LICENSE.md` (ODbL-1.0), `CITATION.cff` and `SHA256SUMS`. Meta RWI
values are excluded (CC-BY-NC); restricted microdata and DHS cluster locations are never included.
`pixi run release-upload` checks the package (dry run); `--push` uploads to the Hub repository named in
`config/release.yaml` (`hf_repo`, private) with `HF_TOKEN` from `.env`. Before making it public: the licence review
(gate 8.2), the team name in `CITATION.cff` and the Hub namespace.

### 8.5 Questions for the organisers
- Can you help us obtain REG village-level billing aggregates and feeder loads (8.1), or NISR ISIC4 counts?
- Is LLM-assisted labelling acceptable if disclosed and measured against human labels as above?
- Any guidance on imagery? The pipeline uses only openly licensed vector data. Human gold labellers consulted
  commercial imagery for interpretation only; nothing was traced from it.

Documentation: [datasheet](docs/datasheet.md), [data sources](docs/data_sources.md), [AI use](docs/ai_use.md),
[workstreams](docs/workstreams.md), [data contract](dashboard/DATA_CONTRACT.md).
