# Workstreams

Contracts first: `src/rtl/schemas.py`, `config/*.yaml`, the manifest and the fixture are owned by the lead.
Each workstream works on its own branch (`ws/<id>-<name>`), owns the paths below, and is done when
`pixi run test` and `pixi run qa <its tables>` pass on the fixture and then nationally.
Status notes go in `reports/status/<id>.md` (what's done, what's blocked, what needs a human).

| id | workstream | owns | produces | depends on |
|---|---|---|---|---|
| lead | integration | `schemas.py`, `settings.py`, `manifest.py`, `qa/`, `config/`, merges | contracts, `buildings_base` | — |
| A1 | footprints & height | `ingest/vida.py`, `ingest/open_buildings_25d.py`, `conform/` | `buildings_base`, `building_height` | contracts |
| A2 | context | `ingest/{osm,overture,admin,ghsl,worldpop,hrsl,rwi,viirs,dem,gridfinder,facilities}.py`, `join/` | per-layer files, `building_context` | ingest: none; joins: A1 ids |
| A3 | climate | `ingest/{era5,power}.py`, `climate/` | `climate_h3` | none |
| A4 | evidence & anchors | `ingest/dhs_api.py`, `ingest/local.py`, `llm/evidence.py`, `data/processed/archetypes/` | `evidence`, archetype JSON, validation anchors | none |
| A5 | labels | `llm/{client,cache,cards,label}.py`, `docs/codebook.md` (draft), labelling page | cards, `labels`, `reports/labels/` | A1 + A2 for national cards |
| A6 | EDA & docs | `notebooks/`, `docs/datasheet.md`, viewer | figures, datasheet, PMTiles | fixture first |

Merge order: A1 → A2/A3 → A5 → A6; A4 merges independently.
Human gates (repo owner only): codebook approval, gold labels, archetype parameter review, licence review.
