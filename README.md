# Rooftops → Load Curves

**Open building footprints → building-level energy-demand data for Rwanda.**
Challenge brief: [`docs/challenge.md`](docs/challenge.md). Built for the [OSEAS26 building-footprint energy-demand challenge](https://github.com/EnAccess/oseas26-building-footprint-energy-demand).

Planning electrification needs to know *who* needs *how much* power *where*. Rwanda has 6.4 million
mapped building footprints but no label saying what each one is or what it consumes. This project builds:

1. **A curated building table**: every footprint with shape, height and floors, growth since 2016, and its
   surroundings (admin area, roads, grid, places, population, wealth, climate). Every column has a source and
   licence, and every quality problem is a visible flag.
2. **Building-use labels**: Claude reads an open-data context card per building and returns class probabilities
   plus its reasons, measured against human gold labels.
3. **An evidence-backed appliance-profile library for [RAMP](https://github.com/RAMP-project/RAMP)**: every
   wattage, usage window and ownership rate traces to a quoted source or a reviewed assumption.

Together they give each building's demand as a **distribution, not a number**: median and 90th-percentile peak,
and annual kWh.

## Status: data phase complete for the mid-point review (Oct 15–16, 2026)
Full progress report for the organisers: [`status_report.md`](status_report.md).

| | |
|---|---|
| Building footprints (VIDA: Google + Microsoft + OSM) | ✅ 6,434,247 conformed, QA-flagged |
| Heights, floors, growth (Open Buildings 2.5D) | ✅ 71.6% with height; floors from height cut-points fitted on OSM |
| Context (admin to village, OSM, Overture, facilities, roads, grid, GHSL, WorldPop, wealth, DEM) | ✅ |
| Climate (ERA5-Land downscaled; cooling degree-days, cooling class; NASA POWER check) | ✅ · 🔄 switching to Copernicus CDS (downloading) |
| Building-use labels: codebook v1, 174 human gold labels, 44,156 deep-dive buildings labelled by Claude | ✅ pilot · 🔄 more gold labels |
| Evidence: 3,076 parameters from 18 reports + 163 productive-use parameters + DHS 2015/2019/2025 | ✅ · ⏳ human review |
| Datasheet, data-source list, AI-use disclosure | ✅ |
| Appliance profiles → RAMP runs → per-building demand | ⏳ phase 2 |

## Documentation
- [`docs/datasheet.md`](docs/datasheet.md): what the dataset contains, how it was made, known limits, licences
- [`docs/data_sources.md`](docs/data_sources.md): every source with role, licence and status (generated)
- [`docs/codebook.md`](docs/codebook.md): building-use classes (v1)
- [`docs/ai_use.md`](docs/ai_use.md): AI-use disclosure
- [`docs/workstreams.md`](docs/workstreams.md): who built what, and phase-2 workstreams
- [`status_report.md`](status_report.md): progress, decisions, findings, open questions

## Reproduce
```bash
pixi install
cp .env.example .env                       # keys: see .env.example (only the Claude API stages need one)
pixi run fetch-vida && pixi run conform-buildings && pixi run make-fixture   # footprints
pixi run conform-height && pixi run context-vector && pixi run context-raster && pixi run assemble-context
pixi run climate-h3 && pixi run derive     # climate + re-tunable columns (config/params.yaml)
pixi run test && pixi run qa               # 82 tests + contract checks on every table
```
Claude stages (`pixi run label …`, `pixi run evidence`) re-run from the local cache at no cost.

## Repo map
`config/` source catalogue, AOIs, parameters · `src/rtl/` pipeline (ingest, conform, join, climate, llm, qa) ·
`notebooks/` EDA (jupytext) · `tests/` (+ committed fixture) · `reports/` QA, figures, labelling evaluation ·
`docs/` · `data/manifest.json` (provenance of every raw file) · `data/gold/` (human labels) ·
`data/processed/{validation_anchors,archetypes}/` (small curated tables).

## Licences
Code: to be chosen. Derived building data inherits **ODbL-1.0** (OSM / Microsoft / Google Open Buildings). The
Meta wealth index is CC-BY-NC-4.0. Restricted survey microdata (DHS, World Bank, NISR) is never redistributed; only
aggregates are published. Per-source licences: [`docs/data_sources.md`](docs/data_sources.md).
