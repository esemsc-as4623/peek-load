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

## Phase 2: demand modelling (after the mid-point)
| id | workstream | produces | depends on |
|---|---|---|---|
| B1 | appliance profiles | ~12–15 RAMP user types (households by tier and urban/rural, shop-house, kiosk, salon, bar, school, health post, church, mill, welding, tailoring, pumping), each parameter linked to evidence or a reviewed assumption | evidence review; DHS 2025 microdata (ownership given electricity access); MTF microdata if obtained |
| B2 | building → profile mapping | lookup from (label probabilities, urban/rural, wealth, grid distance, floor area, cooling class) to a weighted mix of profiles | labels, context, climate |
| B3 | RAMP runner | profile library of simulated days per user type; per-building sampling → median/P90 peak, annual kWh, daily shape | B1, B2 |
| B4 | aggregation & validation | village/sector load curves (with diversity), current vs potential demand; checks against REG/QSEL consumption, district electrification, census households | B3, validation anchors |
| B5 | deliverable | energy-classified building GeoParquet / PostGIS + map viewer + final datasheet | B3, B4 |

Human gates (repo owner only): codebook, gold labels, appliance-profile parameters, licence review before publishing.
