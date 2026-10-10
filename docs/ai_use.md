# AI-use disclosure

Status: Last update on **2026-10-10**

## 1. AI as a coding assistant (Claude Code)
- **Who did what:**
  - The repo owner set the goals, scope and every human decision listed in section 3.
  - Claude Code drafted most of the code, configuration and documentation under the owner's direction.
- **How it ran:** several parallel Claude Code agents built the data workstreams
  (footprints/heights, vector context, raster context, climate, evidence); `docs/workstreams.md` lists who owned
  what.
- **How it was checked:**
  - Each transform has tests that would fail if it were wrong: independent recomputation, planted cases, and row
    reconciliation (82 tests).
  - Every table must pass the QA gate (`pixi run qa`) against its contract in `src/rtl/schemas.py`.
- **Notable corrections made during review:**
  - a quadratic spatial join;
  - a UTM-zone bug that dropped tiles east of 30°E;
  - a biased exactextract median;
  - a UTC/local-time shift in NASA POWER data;
  - storey-height over-counting, replaced by fitted height cut-points.

## 2. AI as a research method (Claude API inside the pipeline)
These are methods with measured error rates, not hidden helpers. Every call is cached with model, prompt version,
tokens and cost (`data/llm_cache/`), so every result is reproducible and re-running costs nothing.

| Use | Models | Scale | How it's checked |
|---|---|---|---|
| Building-use labels from vector-only context cards | Haiku 4.5, Sonnet 5.5, Opus 5.5; prompts v1–v3 | 400 gold buildings × 11 configurations; 44,156 deep-dive buildings (Haiku, v2) | agreement with human gold labels, class variety, cues cited (`reports/labels/eval.md`); labels are never presented as ground truth |
| Evidence extraction from 18 reports | Opus 5.5 with source citations | 3,076 parameters | quotes verified verbatim on the cited page (81%); human review before any value enters an appliance profile |
| Productive-use appliance web research | Opus 5.5 with web search/fetch | 163 parameters | marked *summary statement, verify at source*; human review required |

API spend: ~$157, under a hard cap enforced in code (`src/rtl/llm/budget.py`).

## 3. Not done by AI (human decisions and gates)
- **Building use:**
  - approved the codebook, including the separate `mixed_shop_house` and `religious` classes;
  - made all gold labels.
- **Data and modelling choices:**
  - choice of AOIs and data sources;
  - floor-mapping method;
  - first-seen threshold;
  - cooling-class approach;
  - climate source (Copernicus CDS).
- **Still to come:**
  - review and sign-off of every appliance-profile parameter;
  - licence review before publishing;
  - the final submission text.
