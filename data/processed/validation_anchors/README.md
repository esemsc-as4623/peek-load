# Validation anchors (role: validation — never used as model features)

Numbers transcribed from public reports, used only to check model outputs. Built by `pixi run anchors`
(tables parsed from the PDF text with reconciliation checks; hand-transcribed numbers carry a quote that
code verifies verbatim on the cited page) and `pixi run dhs-priors`. No LLM involved. Pages are PDF page
numbers (1-based), not the printed page numbers.

| file | rows | source (doc_id → config/evidence_docs.yaml) | page / table | checks |
|---|---|---|---|---|
| `households_by_district_2022.csv` | 30 | `rphc5_main_indicators` (NISR, RPHC5 2022) | p96, Table 58: private households by province, district and residence | urban + rural = total per district; districts sum to national 3,312,743 / 964,287 / 2,348,456 |
| `grid_lighting_by_district_2024.csv` | 30 | `eicv7_utilities_amenities` (NISR, EICV7 2023/24) | p111, Table A.8, column "Electricity distributors" (grid as **main lighting source**) + household count (000s) | household-weighted mean reproduces the national 50.0%. Other columns (solar 22.1% nationally) are not parsed: empty cells are dropped in the PDF text, so they can't be aligned |
| `residential_kwh_qsel.csv` | 13 | `qsel_consumption_trends_2024` p5, p9; `qsel_grid_reliability_2025` p13, p15; `qsel_rural_adoption_2025` p2 | text statements (mean/median kWh per month or year by customer group/vintage) | each quote found verbatim on the page (`quote_verified`) |
| `tariffs_2025.csv` | 6 | `reg_tariffs_2025` (RURA tariffs effective 1 Oct 2025, published by REG) | p1, "A. Tariffs for all customer categories" | quotes verified. RWF/kWh excluding VAT and regulatory fee |
| `dhs_ownership_priors.csv` | 227 | DHS Program API (`data/raw/dhs_api/…`) + `dhs_rw_2025_fr` p58 Table 2.5 | API indicators HC_ELEC_H_ELC, HC_HEFF_H_{RDO,TLV,FRG,CMP,MPH}; RW2015/2019/2025 | marginals only (total, residence, province, wealth quintile); `share` is a fraction |

Notes
- **Sector-level households**: not in the RPHC5 main report; the district profiles / sector tables are a
  follow-up. Electrification by district uses EICV7 *main lighting source*, which undercounts households
  that have electricity but light mainly with something else; REG customer counts by district weren't in
  the downloaded REG annual report.
- **QSEL kWh** come from REG prepaid transactions 2013–2019 (consumption trends) and 2023–24 surveys (grid
  reliability, rural adoption). Distributions by vintage exist only as figures (Fig. 7); only the numbers
  stated in the text are transcribed.
- **DHS priors**: the API publishes each breakdown separately; residence × province × wealth cross-tabs
  need the restricted microdata (local aggregates only). The API gives **no confidence intervals** for
  these indicators, so `ci_low`/`ci_high` are empty (not observed, not invented). `prior_id` is the id
  archetype JSON cites in `evidence_ids`.

## RW2025 mobile-phone anomaly: resolved
The API's `HC_HEFF_H_MPH` for RW2025DHS gives urban 72.2% < rural 73.5% (RW2019: urban 90.4%). Individual
ownership in the same survey goes the other way: women 80.6% urban vs 55.8% rural (`CO_MOBB_W_MOB`), men
85.4% vs 67.5%. No other DHS-8 survey in the API (24 checked) has urban < rural on this indicator.
The RW2025 final report settles it: **Table 2.5 (FR401, PDF p58) splits phones into "Any type of mobile
phone" (urban 93.3, rural 79.6, total 83.7), "Ordinary mobile phone" (72.2 / 73.5 / 73.1) and "Smartphone"
(64.4 / 26.6 / 37.9).** The API's value is the *ordinary* (non-smart) phone row, so it is not comparable with
2015/2019 "mobile telephone" (which included all phones). Urban households have swapped to smartphones.
Recommendation: use `mobile_phone_any` (transcribed from Table 2.5, in this CSV) for phone-charging
ownership; keep the API row for traceability, flagged `ordinary_phone_only_use_mobile_phone_any`; and report
the mapping to the DHS Program (api@dhsprogram.com) so the API label can be corrected.
