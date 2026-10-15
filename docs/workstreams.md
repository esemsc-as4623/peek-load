# Workstreams

Contracts first: `src/rtl/schemas.py`, `config/*.yaml`, the manifest and the fixture are owned by the lead. Each
workstream owns the paths below, and is done when `pixi run test` and `pixi run qa <its tables>` pass on the fixture
and then nationally. Status notes: `reports/status/<id>.md`.

## Phase 1: data (done for the mid-point review)
| id | workstream | owns | produced | status |
|---|---|---|---|---|
| lead | integration | `schemas.py`, `settings.py`, `manifest.py`, `qa/`, `config/`, `derive.py`, merges | contracts, `buildings_base`, `building_context` assembly, re-tunable columns | ✅ |
| A1 | footprints & height | `ingest/{vida,open_buildings_25d}.py`, `conform/` | `buildings_base`, `building_height` | ✅ |
| A2v | context: vector | `ingest/{osm,overture,admin,facilities,gridfinder}.py`, `join/vector.py` | `context_vector`, deep-dive AOI polygons | ✅ |
| A2r | context: raster | `ingest/{ghsl,worldpop,rwi}.py`, `join/raster.py` | `context_raster` | ✅ |
| A3 | climate + DEM | `ingest/{dem,openmeteo,power,era5_land_cds}.py`, `climate/` | `dem_rwa_30m.tif`, `climate_h3`, cooling check | ✅ · 🔄 rebuild from Copernicus CDS |
| A4 | evidence & anchors | `ingest/{dhs_api,local,evidence_docs}.py`, `llm/{client,cache,evidence,productive_use}.py` | `evidence`, productive-use evidence, validation anchors, archetype template | ✅ · ⏳ human review |
| A5 | labels | `llm/{sample,cards,label,evaluate,download_batches}.py`, `label_app.py` | cards v1–v3, `labels`, gold page, `reports/labels/` | ✅ pilot · 🔄 gold labels |
| A6 | EDA & docs | `notebooks/`, `docs/` | EDA 01 (footprints), 03 (cooling), datasheet, data sources | 🔄 more EDA figures |

## Phase 2: demand modelling (in progress; status 2026-10-15)
| id | workstream | owns | produced | status |
|---|---|---|---|---|
| B1 | appliance profiles | `demand/{build_archetypes,checks}.py`, `config/archetypes.yaml` | 17 RAMP archetypes (signed off 2026-10-12) + derived health centre and hospital (draft); `reports/demand/profile_checks.md` | ✅ · ⏳ derived profiles |
| B2a | use classifier | `demand/use_model.py`, `config/use_model.yaml` | `use_probs` for every building (A: tags; B: tags + LLM labels) | ✅ · ⏳ sign-off |
| B2b | census-constrained allocation | `demand/{allocate,productive_use,services}.py`, `config/allocation.yaml` | `building_use`, `building_profiles`, `allocation_targets.csv`: census, MINEDUC and worship counts on buildings; facility compounds; urban/rural and DHS wealth splits | ✅ rules signed off 2026-10-15 |
| B2c | registries and aggregates | `ingest/{schools,dhs_wealth,blackmarble}.py` | MINEDUC schools per district; DHS wealth per district x setting; Black Marble night lights per H3 r8 and year | ✅ |
| B3 | RAMP runner | `demand/runner.py` | `profile_library`, per-building preview (potential and expected kWh) | ✅ |
| B4 | grid connection and calibration | `demand/{grid_connection,calibrate}.py` | P(connected) for every building, raked to EICV7 2024; household energy scaled to REG means | ✅ |
| B5 | demand by year | `demand/years.py`, `config/years.yaml` | back-cast 2016-2023 per building; projection to 2030 per district | ✅ |
| B6 | uncertainty and sensitivity | `demand/sensitivity.py` | sector p10/p50/p90 bands, per-building use agreement, spacing-cap sensitivity | ✅ |
| B7 | deliverable | `dashboard/`, `rtl/dashboard/export.py` | static bundle + prototype; final GeoParquet / PostGIS release | 🔄 |

Open items, decisions and opportunities: [`status_report.md`](../status_report.md) sections 5-8.

Human gates (repo owner only): codebook, gold labels, appliance-profile parameters, licence review before publishing.
