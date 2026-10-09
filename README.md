# Rooftops → Load Curves

**Open building footprints → building-level energy-demand data for Rwanda.**
Challenge brief: [`docs/challenge.md`](docs/challenge.md). Built for the [OSEAS26 building-footprint energy-demand challenge](https://github.com/EnAccess/oseas26-building-footprint-energy-demand).

Planning electrification needs to know *who* needs *how much* power *where*. Rwanda has 6.4 million
mapped building footprints but no label saying what each one is or what it consumes. This project builds the
data layer for that:

1. **A curated building table**: every footprint with shape, height, growth since 2016, and context (roads, grid,
   POIs, population, wealth, climate). Every column has a source and licence, and every quality problem is a visible flag.
2. **An evidence-backed appliance-archetype library for [RAMP](https://github.com/RAMP-project/RAMP)**: every wattage,
   usage window and ownership rate traces to a quoted source or a reviewed assumption.

Peak load should end up as a **distribution, not a number**.

## Status: data phase (v0.1 target: Oct 14, 2026, mid-point review)
| | |
|---|---|
| Building footprints (VIDA: Google + Microsoft + OSM) | ✅ 6,434,247 conformed, QA-flagged |
| DHS public indicators (2015 / 2019 / 2025) | ✅ ingested |
| Heights & growth (Open Buildings 2.5D) | ⏳ |
| Context layers (OSM, Overture, GHSL, WorldPop, RWI, grid, DEM) | ⏳ |
| Climate (ERA5 / NASA POWER) | ⏳ |
| Codebook → gold labels → Claude labelling pilot | ⏳ codebook draft awaiting approval |
| Evidence base → RAMP archetypes | ⏳ |

## Reproduce
```bash
pixi install
cp .env.example .env            # add your keys (see .env.example)
pixi run fetch-vida && pixi run conform-buildings && pixi run make-fixture
pixi run test && pixi run qa
```

## Repo map
`config/` source catalog + AOIs · `src/rtl/` pipeline · `tests/` (+ committed fixture) · `reports/qa/`
QA reports · `docs/` codebook, AI-use disclosure, workstreams · `data/manifest.json` provenance of every raw file.

## Licences
Code: TBD. Derived building data inherits **ODbL-1.0** (OSM / Microsoft / Google Open Buildings). Restricted
survey microdata (DHS, World Bank, NISR) is never redistributed; only aggregates are published. See
`config/sources.yaml` for per-source licences.

AI use is disclosed in [`docs/ai_use.md`](docs/ai_use.md).
