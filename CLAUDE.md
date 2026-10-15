# Working rules for this repo (humans and agents)

Rooftops → Load Curves turns open building footprints for Rwanda into a curated, provenance-tracked
building dataset plus an evidence-backed RAMP appliance-archetype library (OSEAS26 hackathon).
**Phase 1 (data) is done; phase 2 (building-level demand with RAMP) runs nationally (see `docs/workstreams.md`).**

## Environment
- `pixi install`, then `pixi run <task>` (tasks in `pyproject.toml`). Python 3.12, DuckDB + spatial + h3.
- Heavy table work goes in DuckDB SQL (6.4M buildings; 15 GB RAM machine). GeoPandas only for small data.
- Store EPSG:4326; compute metrics in EPSG:32735 (`aoi.yaml: country.metric_crs`).

## Re-tunable choices
- `config/params.yaml` holds the floors method, first-seen threshold, cooling-class thresholds and the LLM budget cap.
  Change it and run `pixi run derive` (minutes, no re-sampling, no API).
- Long downloads (e.g. `pixi run fetch-era5-cds`) are resumable. Run them detached (`setsid nohup …`), because tool
  background jobs stop after 2 h.

## Demand profiles (phase 2)
- `pixi run demand` runs the whole chain below in order and ends with `qa` (the demand check is `reports/qa/demand.md`).
- All demand sits on buildings (owner, 2026-10-15). `pixi run use-model` trains the classical use classifier
  (A: tags; B: tags + LLM labels) and writes probabilities for every building; `pixi run pu-demand` / `svc-demand`
  prepare Establishment Census targets and tagged buildings; `pixi run allocate` places census counts on buildings,
  rarest first, with spacing caps (`config/allocation.yaml`, signed off 2026-10-15), and writes `building_use` and
  `building_profiles`. It also imposes MINEDUC school counts (`pixi run fetch-schools`), a national worship count,
  census urban/rural households per district and the DHS wealth split (`pixi run dhs-wealth`); school and health
  compounds share one facility (weights 1/n). After `profile-library` and `grid-model`, `pixi run calibrate` rakes
  P(connected) to EICV7 2024 per district and scales household energy to REG means (expected vs potential demand);
  `pixi run demand-years` back-casts 2016-2023 and projects to 2030; `pixi run demand-sensitivity` (~30 min) gives
  the cap sensitivity and uncertainty bands. QA (`reports/qa/demand.md`) checks every target and calibration.
  `pixi run grid-model` trains the grid-connection model on DHS 2025 (restricted: cluster locations stay in
  `data/restricted/`). Figures: `pixi run use-figures`.
- Decisions, pending items and opportunities live in `status_report.md` (one file; `decisions.md` is retired).
- Gold labels are an uncertain reference (imagery shows little beyond vegetation and roof shape); don't treat
  agreement with them as accuracy.
- Order: `pixi run mtf-appliances` / `eicv5-electricity` (restricted microdata → published aggregates) →
  `validate-evidence` → `build-archetypes` (recipes in `config/archetypes.yaml`; `derived` profiles are scaled copies)
  → `profile-library` → `grid-model` → `calibrate` → `demand-years` → `profile-checks` (`reports/demand/profile_checks.md`) → `dashboard-data` (`--sample` for the committed sample;
  contract in `dashboard/DATA_CONTRACT.md`).
- Archetype numbers must cite evidence ids or carry a `why`. An archetype is `draft` until
  `data/gold/approvals.yaml` records the owner's sign-off (signed off 2026-10-12); then the build marks it `human_reviewed`.

## Contracts and ownership
- `src/rtl/schemas.py` is the interface between workstreams. Every table must validate against it.
  Adding or changing a column = one reviewed change to `schemas.py`, never ad hoc.
- Each workstream owns its directories (see `docs/workstreams.md`). Don't edit another workstream's files;
  open a note in `reports/status/<agent>.md` instead.
- Develop against `tests/fixtures/buildings_base_fixture.parquet` first, then run nationally.

## Data rules
- Every source is declared in `config/sources.yaml` (licence, access, role, status) before ingest.
- Downloads go through `rtl.manifest.fetch` (or `pixi run register-local` for manual downloads) so
  `data/manifest.json` records url, sha256, size, licence and retrieval time.
- `data/raw/` is immutable. Derived data → `data/interim/` → `data/processed/`.
- `role: validation` sources are never used as features.
- Restricted data (DHS / World Bank / NISR microdata) lives in `data/restricted/` and is never committed,
  published or sent to an external API. Publish aggregates only.
- Flag, don't drop: quality problems become `qa_flags`, so exclusions are visible downstream.
- Nullable means "not observed", never zero.

## Quality gates
- `pixi run test` and `pixi run qa` must pass before merging. QA reports land in `reports/qa/`.
- Any new transform gets at least one test that would fail if it were wrong (independent recomputation,
  planted case, or reconciliation of row counts).

## Claude API use (labelling, evidence extraction)
- Key comes from `.env` via `rtl.settings`; never hard-code or print it.
- Every call goes through the cache in `rtl.llm` (key = sha256(model, prompt_version, input)) and logs
  tokens, cost and stop_reason. Use the Batch API for bulk work, and download batch results to disk
  (`python -m rtl.llm.download_batches`), then bulk-ingest them. Per-row cache writes are too slow at 40k+.
- The spend cap is enforced by `rtl.llm.budget` (cap in `config/params.yaml`). Card versions v1–v3 and prompts
  `label_v1..v3` live side by side. The current choice is Haiku 4.5 + v2 (see `reports/labels/eval.md`).
- Prompts are versioned files; changing a prompt bumps `prompt_version`.
- LLM outputs are data with measured accuracy against human gold labels, not ground truth.

## Hackathon AI policy
"AI is your assistant, not your author. If you cannot explain it, do not submit." Keep code small and
readable, record AI involvement in `docs/ai_use.md`, and leave human gates (codebook, gold labels,
archetype parameters, licences) to the human.

## Commits
Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`), one logical change per commit.
Never commit `.env`, `data/` (except `data/manifest.json` and `data/gold/`), or restricted files.
