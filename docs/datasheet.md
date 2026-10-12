# Datasheet: Rwanda building energy-context dataset (v0.1, data phase)

Status: Last update on **2026-10-11**

Structured after *Datasheets for Datasets* (Gebru et al.). Covers the tables produced in the data phase of
Rooftops → Load Curves (OSEAS26). Per-source details: [`data_sources.md`](data_sources.md). Building-use classes:
[`codebook.md`](codebook.md). AI involvement: [`ai_use.md`](ai_use.md).

## Motivation
- **Purpose:** give electrification planners a building-level view of Rwanda. For every building: what it is, how big, where, how connected, and in what climate. Combined with evidence-backed RAMP appliance profiles, this supports estimating each building's electricity demand as a distribution (median and 90th-percentile peak, annual kWh).

## Composition
One row per building footprint, keyed by `bldg_id` (`RWA-` + 16 hex digits, a hash of source + exact geometry).

| Table | Rows | What a row holds |
|---|---|---|
| `buildings_base` | 6,434,247 | footprint (EPSG:4326), source (google / microsoft / osm), detector confidence, area, perimeter, compactness, orientation, vertices, centroid, H3 r9, QA flags |
| `building_height` | 6,434,247 | height (2023), estimated floors, floor area, first-seen year (experimental), yearly presence 2016–2023 |
| `building_context` | 6,434,247 | admin area down to village; OSM/Overture/registry tags; distance to roads (any and major) and power lines; nearby tagged-place counts (50/100/250 m); GHSL settlement class and non-residential share; population density; relative wealth index; elevation; H3 r7 |
| `climate_h3` | 4,034 H3 r7 cells | mean temperature, 95th-percentile daily max, cooling degree-days (18/22/24 °C), cooling class, 24-hour typical day, monthly temperature and rainfall, mean relative humidity, annual rainfall, solar irradiance (2020–2025) |
| `labels` | 48,556 | building-use labels: class, probabilities, confidence, a one-line justification, model and prompt version. Covers 44,156 deep-dive buildings plus the 5,878-building pilot sample, with several model and prompt configurations on the 400 gold buildings |
| `gold_labels.csv` | 174 (growing to 400 + 50 repeats) | human building-use labels with confidence, note and an imagery flag. An uncertain reference, not ground truth: the imagery showed little beyond vegetation and roof shape |
| `pu_located` / `pu_unlocated_areas` / `pu_candidates` | 57 / per area / 17,103 | productive use: buildings with a trade tag; census establishments not tied to a building, per H3 r7 cell, sector and district; top-scoring candidate buildings (no demand assigned) |
| `grid_connection` | 6,434,247 | predicted probability that the building's neighbourhood is grid-connected (logistic model on DHS 2025; draft) |
| `evidence` | 3,076 | appliance and usage parameters from 18 reports, each with document, page, verbatim quote and a verification flag |
| `evidence_productive_use` | 163 | productive-use appliance parameters from web research (summary statements; to verify at the named source) |
| `validation_anchors/` | 5 small tables | households by district, grid lighting by district, residential kWh distributions, tariffs, DHS ownership priors. **Never model inputs.** |

- **Missing values** mean "not observed", never zero. Examples: no height pixel covering the footprint, no tag, or outside the wealth-index tiles.
- **Quality problems are flagged, not dropped** (`qa_flags`): 12.6% `low_confidence` (Google < 0.70), 0.5% `tiny` (< 6 m²), 0.03% `nested_same_source`. Only 17 exact duplicates were removed.
- **Sensitive data:** none at building level. No people, names or addresses. DHS/World Bank/NISR microdata is held locally (restricted) and only aggregates appear.

## Collection
- Footprints: VIDA's merge of Google Open Buildings V3, Microsoft and OSM (Source Cooperative, 2024).
- Heights and growth: Google Open Buildings 2.5D Temporal, read at 2 m (height) and 4 m (presence) from the files' built-in overviews.
- Context: OSM (Geofabrik), Overture places, geoBoundaries, healthsites/OSM facilities, Gridfinder, GHSL BUILT-C, WorldPop R2025A, Meta Relative Wealth Index, Copernicus DEM GLO-30.
- Climate: ERA5-Land hourly 2019–2025 from the Copernicus Climate Data Store (2m temperature, dewpoint, solar radiation, precipitation, wind; the table uses 2020–2025, with December 2019 as the UTC lead-in), plus NASA POWER for checks. A 1991–2020 daily baseline is still queued at CDS. The earlier Open-Meteo build (2019–2024) can be rebuilt with `--source openmeteo`.
- Every raw file is listed in `data/manifest.json` with its URL, sha256, size, licence and retrieval time.

## Preprocessing and labelling
- **Footprints:** made valid, metrics computed in UTM 35S. One R-tree spatial join flags cross-source overlaps (none found) and nested same-source detections.
- **Floors:** height cut-points fitted on OSM `building:levels` (even ids) and tested on odd ids: 1 floor below 6.0 m, 2 floors below 8.5 m, then +1 per 3 m. Held-out exact match is 83% for 1 storey, 33% for 2 and 10% for 3+.
- **First-seen year:** first year with presence ≥ 0.5 that stays ≥ 0.5. Re-tunable from the stored presence columns (`config/params.yaml`, `pixi run derive`).
- **Climate:** bilinear interpolation of the 0.1° grid plus a lapse-rate correction (−6.16 K/km, fitted) to each cell's mean DEM elevation. Local time is UTC+2. Accumulated radiation and precipitation are de-accumulated (reset at 01 UTC); gaps of up to 3 hours are interpolated, longer ones stay missing. Relative humidity uses the Magnus formula on temperature and dewpoint.
- **Building use:** Claude (Haiku 4.5, prompt v2) labels a *context card* built only from open vector data. No imagery is involved: footprint, height and floors, size against neighbours, land use, road frontage, nearby tags and places, density, settlement class, wealth and distances.
  - Measured against human gold labels, agreement is ~39–43%, and each answer cites ~5.4 context cues.
  - The human labels were mostly made with imagery, so they hold information the vector cards lack. The labels are probabilistic; use the class probabilities, not just the top class.
- **Evidence:** extracted by Claude with source citations, then normalised to a schema. 81% of quotes were automatically verified verbatim on the cited page. The repo owner gave the RAMP-relevant rows a
  blanket approval (2026-10-11), not a row-by-row review. Automated checks screen every value for units, physical or
  market ranges and agreement between sources, and flagged rows (17 of 1,077) are excluded unless overridden.
- **Appliance use (MTF 2022):** weighted aggregates from the World Bank MTF Rwanda 2022 microdata (restricted;
  aggregates only, cells under 10 respondents suppressed). They give ownership, number owned and hours of use per
  appliance for grid-connected urban and rural households, plus household-enterprise operating hours and appliance
  use (`data/processed/validation_anchors/mtf_*.csv`). Grid households are also split by Ubudehe category (1–2 vs
  3–4), and the MTF school and health-facility surveys give institutional appliance use. Electricity spending is
  converted to kWh with the RURA tariff in force (2020 decision for MTF 2022; 2018 gazette for EICV5 2017).
- **Appliance profiles (RAMP archetypes, draft):** 17 profiles built by `pixi run build-archetypes` from
  `config/archetypes.yaml`. Every number cites its source (MTF aggregate, approved evidence, QSEL measured energy)
  or a written assumption. Household lighting on-time is calibrated to MTF spending (one factor per archetype);
  independent checks are in `reports/demand/profile_checks.md`.

## Known limitations
1. Google's 2.5D heights are compressed: single-storey buildings read ~4–5 m and towers are under-estimated, so 3+ storey floors are unreliable.
2. `first_seen_year` has artefacts (jumps in 2019 and 2023). Treat it as experimental.
3. Direct building-use tags are rare (~14k informative tags among 6.4M buildings), and open vector data rarely shows use. Building-use labels are therefore uncertain, and residential and ancillary dominate.
4. Microsoft footprints omit structures under ~15 m², so small ancillary buildings are under-counted where Microsoft is the source.
5. Cooling demand is marginal in most of the country, including Kigali. Sustained heat is in the eastern lowlands (12.9% of buildings in areas above 100 CDD22 a year; provisional, as NASA POWER shows less there) and the Rusizi/Bugarama valley (confirmed). The cooling class is a relative proxy.
6. Human gold labels are themselves uncertain (imagery shows little beyond vegetation and roof shape), so label agreement is consistency, not accuracy.
7. Unlocated productive use treats every rural manufacturing establishment as an electric mill: an upper bound.
8. The grid-connection model predicts a neighbourhood rate (DHS clusters are displaced 2-5 km); neighbouring buildings share a value.
9. Labels cover the three deep-dive sectors (Nyamirambo fully, Nasho fully, Muhoza partly) and a national sample, not the whole country.
10. Appliance profiles are signed off by the owner (2026-10-12), but bars, guesthouses, offices, churches and stores rest on assumptions; the health
   profile is built from health centres and overstates health posts; all profiles assume a grid connection.

## Uses
- **Intended:** electrification and mini-grid planning studies, demand modelling with RAMP, energy-access research, teaching.
- **Not intended:** decisions about individual households or property; anything requiring certainty of a single building's use without field checks.

## Distribution and licences
- **Data:** derived building data inherits **ODbL-1.0** (OSM / Microsoft / Google Open Buildings).
- **Meta Relative Wealth Index (`rwi`):** CC-BY-NC-4.0, so release it separately or flag it for non-commercial use.
- **Climate:** Copernicus ERA5-Land, free to use including commercially, with attribution to the Copernicus Climate Change Service. ERA5-Land rainfall runs wet over the mountains; treat it as indicative.
- **Restricted microdata** (DHS, World Bank MTF, NISR) is never redistributed.

## Maintenance
Pipeline steps are pixi tasks (`pyproject.toml`), and each output is validated against `src/rtl/schemas.py` by `pixi run qa`. Every LLM response is cached, so the labels can be rebuilt from the cache at no cost. Owner: repo maintainer (GitHub `esemsc-as4623/peek-load`).
