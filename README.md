# Peek Load: Rooftops → Load Curves for Rwanda

![CI Pipeline](https://github.com/esemsc-as4623/peek-load/actions/workflows/ci.yml/badge.svg)
![Tests](https://img.shields.io/badge/tests-passing-success)
![License](https://img.shields.io/badge/license-GPL--3.0-blue)
![Version](https://img.shields.io/badge/version-1.0.0-blue)

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

## Deliverables
1. The output dataset: https://huggingface.co/datasets/arushisinha98/peek-load
2. Interactive frontend: https://peek-load-rwa.tailce5583.ts.net/#/challenge (active until 2026-11-10)

## Documentation
- [`docs/datasheet.md`](docs/datasheet.md): what the dataset contains, how it was made, known limits, licences
- [`docs/data_sources.md`](docs/data_sources.md): every source with role, licence and status (generated)
- [`docs/codebook.md`](docs/codebook.md): building-use classes (v1)
- [`docs/ai_use.md`](docs/ai_use.md): AI-use disclosure
- [`docs/workstreams.md`](docs/workstreams.md): breakdown of the workstreams and data input and outputs
- [`status_report.md`](status_report.md): progress, decisions, findings, open questions, opportunities

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
Code: GNU GPLv3.
Derived building data inherits **ODbL-1.0** (OSM / Microsoft / Google Open Buildings).
The
Meta wealth index is CC-BY-NC-4.0. Restricted survey microdata (DHS, World Bank, NISR) is never redistributed; only aggregates are published. Per-source licences: [`docs/data_sources.md`](docs/data_sources.md).

## Demo
![Load Profile Page](https://github.com/esemsc-as4623/peek-load/blob/f9b997b2cd59c6f449b36b2104768ab5e4e33bcf/imgs/load-profile-page.png)
![Overview Page](https://github.com/esemsc-as4623/peek-load/blob/f9b997b2cd59c6f449b36b2104768ab5e4e33bcf/imgs/overview-page.png)
![Deep Dive Page](https://github.com/esemsc-as4623/peek-load/blob/f9b997b2cd59c6f449b36b2104768ab5e4e33bcf/imgs/deep-dive-page.png)
![Productive Use Page](https://github.com/esemsc-as4623/peek-load/blob/f9b997b2cd59c6f449b36b2104768ab5e4e33bcf/imgs/productive-use-page.png)
![Workflow Page](https://github.com/esemsc-as4623/peek-load/blob/f9b997b2cd59c6f449b36b2104768ab5e4e33bcf/imgs/workflow-page.png)
