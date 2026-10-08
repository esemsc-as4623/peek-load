# Workstreams

Contracts first: `src/rtl/schemas.py`, `config/*.yaml`, the manifest and the fixture are owned by the lead.
Each workstream works on its own branch (`ws/<id>-<name>`), owns the paths below, and is done when
`pixi run test` and `pixi run qa <its tables>` pass on the fixture and then nationally.
Status notes go in `reports/status/<id>.md` (what's done, what's blocked, what needs a human).

| id | workstream | owns | produces | depends on |
|---|---|---|---|---|
| lead | integration | `schemas.py`, `settings.py`, `manifest.py`, `qa/`, `config/`, merges | contracts, `buildings_base` | — |
| A1 | footprints & height | `ingest/vida.py`, `ingest/open_buildings_25d.py`, `conform/` | `buildings_base`, `building_height` | contracts |
| A2v | context: vector | `ingest/{osm,overture,admin,facilities,gridfinder}.py`, `join/vector.py` | layer files, `context_vector`, deep-dive AOI polygons | ingest: none; joins: A1 ids |
| A2r | context: raster | `ingest/{ghsl,worldpop,rwi,hrsl}.py`, `join/raster.py` | clipped rasters, `context_raster` | elevation needs A3's DEM |
| A3 | climate + DEM | `ingest/{dem,openmeteo,power}.py`, `climate/` | `dem_rwa_30m.tif`, `climate_h3` | none |
| A4 | evidence & anchors | `ingest/{dhs_api,local,evidence_docs}.py`, `llm/{client,cache,evidence}.py` (client+cache shared with A5), `config/evidence_docs.yaml`, `data/processed/{archetypes,validation_anchors}/` | `evidence`, archetype template, validation anchors | none |
| A5 | labels | `llm/{cards,label}.py`, `docs/codebook.md` (draft), labelling page | cards, `labels`, `reports/labels/` | A1 + A2 for national cards; uses A4's client/cache |
| A6 | EDA & docs | `notebooks/`, `docs/datasheet.md`, viewer | figures, datasheet, PMTiles | fixture first |

The lead joins `context_vector` + `context_raster` into `building_context`.
Merge order: A1 → A2v/A2r/A3 → A5 → A6; A4 merges independently.
Human gates (repo owner only): codebook approval, gold labels, archetype parameter review, licence review.
