# Working rules for this repo (humans and agents)

Rooftops → Load Curves turns open building footprints for Rwanda into a curated, provenance-tracked
building dataset plus an evidence-backed RAMP appliance-archetype library (OSEAS26 hackathon).
**Current phase: data gathering, curation, EDA, preprocessing. No model training, no full RAMP runs.**

## Environment
- `pixi install`, then `pixi run <task>` (tasks in `pyproject.toml`). Python 3.12, DuckDB + spatial + h3.
- Heavy table work goes in DuckDB SQL (6.4M buildings; 15 GB RAM machine). GeoPandas only for small data.
- Store EPSG:4326; compute metrics in EPSG:32735 (`aoi.yaml: country.metric_crs`).

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
  tokens, cost and stop_reason. Use the Batch API for bulk work.
- Prompts are versioned files; changing a prompt bumps `prompt_version`.
- LLM outputs are data with measured accuracy against human gold labels, not ground truth.

## Hackathon AI policy
"AI is your assistant, not your author. If you cannot explain it, do not submit." Keep code small and
readable, record AI involvement in `docs/ai_use.md`, and leave human gates (codebook, gold labels,
archetype parameters, licences) to the human.

## Commits
Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`), one logical change per commit.
Never commit `.env`, `data/` (except `data/manifest.json` and `data/gold/`), or restricted files.
