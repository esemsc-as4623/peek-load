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
| Coding-session work (Claude Code): pipelines, checks, curation of 37 values from 3 productive-use PDFs, archetype recipes (`config/archetypes.yaml`) | Claude in the coding session, not the API | 17 draft archetypes; ~40 written assumptions | curated quotes verified in code; assumptions carry their rationale; profiles checked against independent anchors (`reports/demand/profile_checks.md`); archetypes were `draft` until the owner signed them off (2026-10-12) |

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
- **Approved 2026-10-11** (`data/gold/approvals.yaml`):
  - the evidence review sheet, as a **blanket approval** (not row by row), backed by automated plausibility
    checks (`rtl.demand.validate`, rules in `config/plausibility.yaml`) that flag rows for exclusion;
  - the building → profile mapping (`config/profile_mapping.yaml`).
- **Approved 2026-10-12:** the archetype assumptions (`config/archetypes.yaml`, reviewed via
  `reports/review/archetype_assumptions.csv`), and the renaming of the four household profiles by wealth half.
- **Decided 2026-10-12:** class probabilities under 0.10 are ignored; productive use comes from trade tags plus an
  unlocated census amount per area (the owner's rule: mills in rural roadside places, other trades in towns).
- **Decided 2026-10-15:** all demand sits on buildings. A classical classifier (scikit-learn gradient boosting, written in
  the coding session, no API calls) is trained on tags (model A) and on tags plus the LLM labels (model B); census
  counts are then placed on buildings by explicit rules. The LLM labels are training data here, never ground truth.
- **Decided 2026-10-15 (later):** the owner signed off the allocation rules and asked for registry counts (MINEDUC
  schools, a national worship count), census urban/rural households, EICV7 grid shares, REG consumption and DHS wealth
  as constraints, a health-facility split, a back-cast by year and uncertainty bands. The coding assistant found and
  parsed the sources (school yearbook tables, NASA Black Marble files), wrote the code and reported where sources
  conflict (REG vs QSEL); the owner chose which rules to apply. The worship count and the hospital factor are the
  assistant's proposals from news reports and an assumption, flagged as pending sign-off. No API calls were made.
- **Decided 2026-10-15 (evening):** the owner chose REG over QSEL, a lighter rural bar profile, a 25% rural mill
  share and kept the other recommendations. The coding assistant parsed the newly supplied reports (NISR yearbook,
  RURA, EICV thematic tables), found the SEforALL health-facility survey, wrote the benchmark comparison and proposed
  the options for the open health-facility and business items; the owner decides them.
- **Still to come:**
  - eligibility and candidate weights (`config/productive_use.yaml`);
  - the derived health profiles, the worship count and the REG-over-QSEL choice (`status_report.md` section 6);
  - licence review before publishing;
  - the final submission text.
