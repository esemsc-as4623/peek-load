# Rooftops → Load Curves: status report and decision log

*OSEAS26 challenge: building footprints → energy demand · repo `esemsc-as4623/peek-load` · updated 2026-10-15*

One file for the organisers and for us: what the pipeline does (section 2), results (3–4), every decision taken
(5), what is still open (6), opportunities (7) and bookkeeping (8). It replaces `decisions.md`. Owner sign-offs are
recorded in `data/gold/approvals.yaml`.

## 1. Summary
We turn Rwanda's 6.4 million open building footprints into building-level electricity demand. Every building gets
its shape, height, surroundings, climate and a use. Use-specific appliance profiles (RAMP) then give a load curve,
peak and annual energy. The pipeline is a chain of separate steps (section 2), each with a clear input, output and
check. It is modular, so we can swap in ML models and decisions and see their effect.

Current state (2026-10-15):
- **All six national building tables** are built and pass QA.
- **Every unit of demand sits on a building, nationally.** A classical classifier gives every building use
  probabilities. Census and registry counts are then placed on the most likely buildings, rarest first.
  **3,488,223 buildings carry demand.**
- **Demand is constrained to the aggregates we hold**:
  - households: census counts per district, urban and rural;
  - establishments: Establishment Census counts;
  - schools: MINEDUC counts;
  - wealth split: DHS 2025 per district;
  - grid connection: EICV7 2024 shares per district;
  - household consumption: REG billing means.
- **National totals:**
  - **potential demand 543 GWh/yr** (every building connected);
  - **expected demand 440 GWh/yr** (2024 connection);
  - **back-cast to 2016 (327 GWh expected) and projected to 2030 (542 GWh).**
- Uncertainty bands per sector and a spacing-cap sensitivity are reported (section 4).
- A static dashboard bundle and a design prototype exist.

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
| 8 | **Appliance profiles (RAMP)** | Step 7; `config/archetypes.yaml` | **19 profiles**: 17 built archetypes (signed off 2026-10-12) + **health centre and hospital (derived, draft)** | Households from MTF grid households by setting × Ubudehe half, lighting calibrated to MTF spending. **Health split (A10):** the appliance-built profile (366 kWh/month) is the health post; a health centre is scaled to MTF centres' median (2,734 kWh/month); a hospital is 10 health centres (assumption) | `reports/demand/profile_checks.md`: health centre ok by construction; school, mill, carpentry ok |
| 9 | **Census and registry targets** | Establishment Census 2023 tables; 2022 census households (urban/rural); **MINEDUC school counts 2020/21**; **worship count (news reports of RGB figures)**; **DHS 2025 wealth** | `pu_census_districts`, `svc_census_districts`, `registries/schools_by_district_2021.csv`, `dhs_wealth_district.csv` | Manufacturing, services and retail per district × setting. **5,904 schools** (primary 3,691, secondary 1,869, TVET 344). **7,000 places of worship** (range 6,000–9,000), spread by households | Section-level census only: trade and service splits stay assumptions (P1–P3) |
| 10 | **Allocation** (`config/allocation.yaml`, signed off 2026-10-15) | Steps 6 and 9; tags; facility points | `building_use` (one use per building, establishment, its share, households, wealth, setting), `building_profiles` (units) | Order: (0) tags; (0b) **facility compounds**: tagged school and health buildings, plus institution-like buildings within 150 m (schools) or 100 m (health), share one facility (weight 1/n); pharmacies are shops. (1) productive use; (2) services; (3) retail; (4a) **schools to the MINEDUC count**; (4b) **worship to the national count**; (4c) offices and stores at LLM rates; (5) households, **urban and rural separately**, up to one per floor; **upper/lower wealth per district × setting from DHS**; (6) the rest is ancillary. Spacing caps per H3 cell | QA: every target met, no shortfall; urban share per district = census (Kirehe 0.068 vs 0.07); upper share 0.359 rural / 0.547 urban = MTF |
| 11 | **Grid connection** | DHS 2025 households + displaced cluster GPS (restricted) | `grid_connection`: P(connected) for all buildings | Logistic regression on neighbourhood features | AUC 0.75; district r = 0.93 vs EICV7 |
| 12 | **Calibration (new)** | Steps 10–11; EICV7 2024 district grid shares; REG means via QSEL 2024 | `building_profiles` gains `p_grid`, `scale`, energy `weight`; `calibration.csv`; `building_demand_preview` (potential and expected kWh) | **A9:** P(connected) shifted per district on the logit scale (−1.15 to +0.58) so households match EICV7. **REG:** household energy scaled per region so a connected household uses REG's mean: Kigali ×1.01 (24.75 kWh/month), elsewhere **×0.42** (5.92) | QA: every district within 0.005 of EICV7. QSEL rural check: 4.4 vs 8.1 kWh/month (P4) |
| 13 | **Demand by year (new)** | Step 12; presence 2016–2023; censuses 2012/2022; EICV5 2017 | `demand_by_year.csv` (district × year), `building_demand_by_year.parquet` (expected kWh per building, 2016–2023) | Presence shows *where* and *in which order* buildings appeared; the census rate (3.1%/yr) sets *how much* (raw presence implies 7.5%/yr). Connection rolls back to EICV5 (urban 0.755, rural 0.152). Projection: district growth (2.3–4.6%/yr), connection held at 2024 | Seasonal and weekly shape: O1 |
| 14 | **Uncertainty and sensitivity (new)** | Step 10 re-run | `uncertainty/sector_bands.csv`, `uncertainty/building_agreement.parquet`, `reports/demand/cap_sensitivity.csv` | 10 allocations with class probabilities × exp(0.5·N(0,1)); profile p10/p90; caps ×0.5 / ×1 / ×2 / ×4 / none | Section 4 |
| 15 | **Dashboard bundle and QA** | All steps | Static JSON bundle; `reports/qa/*.md` | `pixi run demand` runs steps 6–15 in order and ends with QA | Every building in the sample recomputed from the published formulas in a test |

## 3. Per district

Buildings, tags and labels: `reports/status/district_table.md` (`pixi run status-figures`). Demand by district:

| District | Households (urban share) | Upper-wealth share | Productive use | Schools | EICV7 grid share | Potential GWh/yr | Expected GWh/yr | Growth %/yr | Expected 2030 |
|---|---|---|---|---|---|---|---|---|---|
| Gasabo | 249,420 (0.83) | 0.59 | 1,628 | 242 | 0.87 | 85.1 | 78.4 | 3.2 | 97.9 |
| Kicukiro | 135,463 (0.99) | 0.58 | 1,097 | 157 | 0.94 | 52.3 | 50.1 | 3.3 | 62.9 |
| Nyarugenge | 103,985 (0.88) | 0.69 | 1,465 | 103 | 0.94 | 40.2 | 38.9 | 2.8 | 47.3 |
| Nyagatare | 160,435 (0.25) | 0.37 | 635 | 330 | 0.38 | 19.2 | 12.8 | 2.7 | 15.2 |
| Bugesera | 137,777 (0.40) | 0.45 | 741 | 230 | 0.59 | 18.8 | 14.8 | 3.9 | 19.2 |
| Rubavu | 124,080 (0.56) | 0.49 | 846 | 185 | 0.68 | 18.4 | 15.4 | 4.0 | 20.1 |
| Musanze | 119,387 (0.49) | 0.32 | 868 | 192 | 0.64 | 17.3 | 14.2 | 4.2 | 18.9 |
| Rwamagana | 121,051 (0.35) | 0.48 | 639 | 189 | 0.57 | 16.4 | 13.0 | 2.9 | 15.7 |
| Kamonyi | 116,378 (0.31) | 0.48 | 602 | 210 | 0.29 | 15.9 | 10.3 | 3.8 | 13.2 |
| Gatsibo | 136,208 (0.11) | 0.41 | 504 | 253 | 0.31 | 15.3 | 10.2 | 2.5 | 11.8 |
| Kayonza | 114,186 (0.15) | 0.46 | 557 | 171 | 0.40 | 15.2 | 11.3 | 2.8 | 13.5 |
| Rusizi | 104,937 (0.33) | 0.37 | 580 | 239 | 0.65 | 15.2 | 12.9 | 4.6 | 17.6 |
| Gicumbi | 109,373 (0.06) | 0.48 | 587 | 229 | 0.40 | 14.7 | 11.2 | 2.9 | 13.5 |
| Kirehe | 113,886 (0.07) | 0.25 | 515 | 188 | 0.54 | 13.9 | 11.1 | 3.1 | 13.5 |
| Gakenke | 93,609 (0.04) | 0.44 | 423 | 203 | 0.28 | 13.1 | 9.6 | 3.0 | 11.6 |
| Muhanga | 93,241 (0.23) | 0.46 | 546 | 217 | 0.38 | 12.9 | 9.7 | 2.7 | 11.6 |
| Rulindo | 91,909 (0.11) | 0.53 | 404 | 192 | 0.31 | 12.7 | 9.2 | 3.1 | 11.2 |
| Ngoma | 102,589 (0.09) | 0.31 | 479 | 175 | 0.38 | 12.7 | 9.3 | 2.3 | 10.7 |
| Karongi | 91,444 (0.10) | 0.21 | 405 | 252 | 0.31 | 12.5 | 9.2 | 2.6 | 10.9 |
| Huye | 96,037 (0.18) | 0.38 | 415 | 166 | 0.47 | 12.3 | 9.5 | 2.4 | 11.1 |
| Rutsiro | 86,802 (0.06) | 0.28 | 438 | 183 | 0.41 | 12.2 | 9.6 | 3.2 | 11.7 |
| Nyamasheke | 95,229 (0.08) | 0.46 | 417 | 241 | 0.47 | 12.2 | 9.5 | 3.4 | 11.8 |
| Nyamagabe | 92,052 (0.10) | 0.25 | 452 | 202 | 0.21 | 11.9 | 8.0 | 2.6 | 9.4 |
| Ngororero | 92,623 (0.05) | 0.26 | 426 | 176 | 0.34 | 11.3 | 8.2 | 2.5 | 9.6 |
| Ruhango | 94,508 (0.11) | 0.37 | 382 | 165 | 0.40 | 11.1 | 8.1 | 2.6 | 9.5 |
| Burera | 91,786 (0.10) | 0.28 | 421 | 183 | 0.52 | 11.1 | 8.7 | 3.2 | 10.7 |
| Nyanza | 93,007 (0.10) | 0.40 | 351 | 170 | 0.35 | 10.5 | 7.3 | 3.4 | 9.0 |
| Gisagara | 101,145 (0.03) | 0.29 | 298 | 154 | 0.37 | 10.0 | 6.8 | 3.0 | 8.1 |
| Nyabihu | 76,391 (0.24) | 0.38 | 329 | 150 | 0.40 | 9.5 | 6.6 | 3.0 | 8.0 |
| Nyaruguru | 73,805 (0.03) | 0.24 | 296 | 157 | 0.28 | 8.9 | 6.3 | 2.8 | 7.5 |
| **Rwanda** | **3,312,743** | | **17,746** | **5,904** | | **543** | **440** | | **542** |

## 4. Results

### Deep dives

| | Nyamirambo (Kigali) | Muhoza (Musanze) | Nasho (Kirehe) |
|---|---|---|---|
| Buildings / carrying demand | 21,126 / 14,516 | 32,538 / 16,232 | 17,374 / 9,262 |
| Census households placed (urban share) | 14,213 (0.98) | 15,549 (0.89) | 9,051 (0.00) |
| Productive use (of which mills) | 214 (2) | 274 (18) | 49 (49) |
| Services / shops | 500 / 1,521 | 459 / 650 | 183 / 174 |
| Schools / health facilities (whole facilities) | 15.2 / 7.2 | 17.1 / 9.4 | 9 / 2 |
| **Potential / expected energy** | **6.39 / 6.32 GWh** | **3.52 / 3.19 GWh** | **1.13 / 0.91 GWh** |
| Uncertainty, combined p10–p90 (potential) | 6.31–6.45 GWh (−1.3% / +1.0%) | 3.46–3.56 GWh (−1.6% / +1.4%) | 1.11–1.15 GWh (−2.1% / +1.5%) |

### National energy by profile (GWh/yr, potential / expected)

Households: urban upper 135 / 121, rural lower 73 / 27, urban lower 65 / 48, rural upper 47 / 21. Grain mills
86, bars/restaurants 46, **hospitals 42 (127 facilities, assumption P5)**, shops 16, health centres 12, welding 6,
schools 4, salons 3, offices 2.5, guesthouses 2.4, health posts 1.8, tailoring 1.2, carpentry 0.5, churches 0.5,
stores 0.3. Establishments count as connected.

### Spacing-cap sensitivity (A6)

`reports/demand/cap_sensitivity.csv` (caps on every establishment kind ×0.5, ×1, ×2, ×4, none). Without caps the
best-scoring buildings cluster; with them, placements spread to more cells at little cost in fit:

| Caps | Mills: H3 r8 cells used | Most mills in one block (r9) | Nasho mills (MWh/yr) | Bars: most in one block | Shops: most in one block | Above base cap (all placed) |
|---|---|---|---|---|---|---|
| ×0.5 | 9,075 | 1 | 49 (465) | 14 | 54 | as base for kinds capped at 1 |
| **base** | **9,075** | **1** | **49 (465)** | **16** | **54** | **6.2%** (QA limit 10%) |
| ×2 | 4,927 | 2 | 64 (607) | 17 | 54 | 26–46% by kind |
| ×4 | 3,052 | 4 | 90 (854) | 23 | 64 | 45–70% by kind |
| none | 1,419 | 66 | 165 (1,566) | 208 | 500 | 60–89% by kind |

Productive-use energy in Nasho ranges from 465 MWh/yr (base) to 1,566 (no caps); in Nyamirambo 153–297 and in
Muhoza 389–578. District totals never change (they are census counts); the caps move energy *within* districts.

### Uncertainty (B6)

Ten allocations with every class probability multiplied by exp(0.5·N(0,1)) per building, plus each profile's
RAMP p10/p90 day (`data/processed/uncertainty/`, figure `C8_uncertainty.png`):
- **National potential:** allocation p10–p90 540–546 GWh/yr, profile p10–p90 530–552, base 543.
- **Sectors:** the combined band is ±5% wide at the median sector (p90 7.5%). District constraints hold the totals,
  so the allocation band is narrower than the profile band. Perturbed runs place slightly more demand in
  Nyamirambo (+0.5 to +0.9%) and less in Nasho (−1.9 to −0.5%) than the base.
- **Buildings:** 90% keep their base use in a single perturbed run, and 65% in all ten. Mean agreement by use:
  homes 0.92, productive use 0.93, ancillary 0.90, institutions 0.81, stores 0.71, shops 0.60, shop-houses and
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

## 6. Pending considerations

| # | Item | What we do now | Why it matters | Options | Recommendation |
|---|---|---|---|---|---|
| P1 | **Services splits and rural bars** (was A2) | Section I = 97% bars/restaurants, 3% accommodation; S = 60% salons; one bar profile (with a fridge) everywhere | 60,204 bars at 2.1 kWh/day = 46 GWh/yr. Public census microdata has **ISIC section only**, so it can't split I55/I56; the RDB register isn't public (~170 graded hotels in 2021) | (a) keep; (b) ask NISR for the restricted ISIC4 file; (c) a lighter rural cabaret profile | (c) now, (b) by request |
| P2 | **Rural manufacturing = mills** (was A4) | All 9,079 rural manufacturing establishments are 10 kW mills | 86 GWh/yr; Nasho gets 49 mills. Public microdata can't split manufacturing (section C only) | (a) keep; (b) a 20–30% mill share, the rest a light workshop; (c) ISIC4 from NISR | (b) with a range |
| P3 | **Town trade shares** (was A5) | Tailoring 34%, welding 33%, carpentry 33% | Welding dominates the energy; no source | keep with a range; ISIC4 from NISR | keep, show the range |
| P4 | **REG vs QSEL for rural households** | REG's outside-Kigali mean (5.92 kWh/month) is the constraint | It scales rural households to 4.4 kWh/month, below QSEL's rural survey mean (8.1). The two can't both hold: REG's "outside Kigali" includes towns | REG (billing, all customers) or QSEL (survey, rural) | REG, as now; QSEL shown as a check |
| P5 | **Hospital profile** | 10 × a health centre (899 kWh/day), applied to 127 healthsites "hospitals" | 42 GWh/yr, 8% of potential; some tagged hospitals may be clinics | a metered or audited source; a smaller factor; MoH hospital list | sign off or replace; check the 127 against MoH |
| P6 | **Worship count** | 7,000 national (6,000–9,000), spread by households | Weak evidence (news reports); 0.5 GWh/yr, so low stakes | keep, or an RGB list if obtainable | keep |
| P7 | **Validation after constraining** (was A11) | Constrained aggregates can't validate | What's left: LLM labels in the deep dives, QSEL check, cap sensitivity, bands | hold out 5 districts once | after the finals' core work |
| P8 | **Use-model choices** | `config/use_model.yaml` (features, class weights) | They rank buildings for every placement | sign off as defaults | sign off |
| P9 | **Derived health profiles** | health centre and hospital are draft | They enter national totals | sign off | sign off with P5 |

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

## 8. Bookkeeping

### 8.1 Data to acquire

| Priority | What | Why | How |
|---|---|---|---|
| 1 | **REG data**: (a) billing aggregates: number of customers by tariff category and kWh sold per month, per village or cell; (b) **feeder loads**: hourly or 15-min MW per MV feeder (15/30 kV) from substation SCADA; (c) MV/LV network GIS (lines, transformers) | (a) village-level connection and consumption constraints, the sharpest check on step 12; (b) real load shapes and seasonality to validate the RAMP curves and cooling; (c) better grid distance than Gridfinder | request via the organisers / REG |
| 1 | **NISR Establishment Census ISIC4 counts** (restricted file or a custom table: district × urban/rural × 4-digit activity) | resolves P1–P3 | request to NISR via the organisers |
| 2 | EC2023 public microdata (catalog 112) | district × setting × section counts, size, market-place location | not downloadable for us on 2026-10-15; the 2020 report (section level) is held instead |
| 2 | MoH facility list with type and beds | P5 (hospitals vs clinics) | MoH / organisers |
| 2 | Health-post and hospital energy (MoH, WHO/SEforALL Powering Healthcare, metered sites) | P5, P9 | public reports |
| 2 | EnAccess MicroPowerManager anonymised meter data | real mini-grid load curves for validation | organisers |
| 3 | Kigali Master Plan zoning GIS; NISR sector-level 2022 households; EICV7 microdata | sharper Kigali labels; sector-level checks | City of Kigali; NISR |

Received: MTF 2022 microdata (2026-10-11), productive-use reports (37 curated values), MINEDUC yearbook 2020/21,
NASA Earthdata token (Black Marble downloaded 2026-10-15), NISR Establishment Census 2020 report.

### 8.2 Human gates

| Gate | Status |
|---|---|
| Codebook (9 use classes) | approved |
| Gold labels (173 of 400) | done; an uncertain reference |
| Evidence review | approved 2026-10-11 (blanket, backed by automated checks) |
| Archetype assumptions (17) | signed off 2026-10-12 |
| Allocation rules | **signed off 2026-10-15** |
| Registries, constraints, years, bands | **requested by the owner 2026-10-15** |
| Derived health profiles; worship count; REG over QSEL | pending (P4–P6, P9) |
| Trade shares, service splits, mill share | pending (P1–P3) |
| Use-model choices | pending (P8) |
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

### 8.4 Questions for the organisers
- Can you help us obtain REG village-level billing aggregates and feeder loads (8.1), or NISR ISIC4 counts?
- Is LLM-assisted labelling acceptable if disclosed and measured against human labels as above?
- Any guidance on imagery? The pipeline uses only openly licensed vector data. Human gold labellers consulted
  commercial imagery for interpretation only; nothing was traced from it.

Documentation: [datasheet](docs/datasheet.md), [data sources](docs/data_sources.md), [AI use](docs/ai_use.md),
[workstreams](docs/workstreams.md), [data contract](dashboard/DATA_CONTRACT.md).
