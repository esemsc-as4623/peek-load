# AI-use disclosure

The OSEAS contribution guide asks contributors to disclose meaningful AI involvement, and says:
*"AI is your assistant, not your author. If you cannot explain it, do not submit."* This file is that
disclosure. It is updated whenever AI involvement changes.

## 1. AI as a coding assistant (Claude Code)
| Area | What the assistant did | Human review |
|---|---|---|
| Repo scaffold, pixi env, config files | drafted | reviewed by repo owner |
| `rtl/manifest.py`, `rtl/settings.py`, `rtl/schemas.py` | drafted | reviewed |
| `rtl/conform/buildings.py` (DuckDB footprint conformance) | drafted; verified against an independent pyproj/shapely recomputation and a planted overlap case | reviewed |
| `rtl/ingest/*`, `rtl/qa/*`, tests | drafted | reviewed |

The repo owner reviews every module before it goes into a submission, and must be able to explain it.

## 2. AI as a research method (Claude API inside the pipeline)
These are methods with measured error rates, not hidden helpers:
- **Building-use labelling pilot.** Claude labels context cards built only from open vector data. Its accuracy
  is measured against human gold labels (macro-F1, confusion matrix, calibration) and reported in
  `reports/labels/`. LLM labels are never presented as ground truth.
- **Evidence extraction for RAMP archetypes.** Claude extracts appliance and usage parameters from
  survey reports, with verbatim quotes. Quotes are checked automatically against the source text, and every
  parameter is reviewed by a human before use.

Every API call is cached with its model id, prompt version, tokens and cost, so results are reproducible.

## 3. Not done by AI
Codebook approval, gold labels, archetype parameter sign-off, licence review, and the final
submission text.
