# QA: buildings_base
**PASS**

- file: `buildings_base.parquet`
- checked: 2026-10-08 10:32 UTC
- rows: 6,434,247
- duplicate `bldg_id`: 0
- schema: PASS (random sample of 200,000)

| column | null share |
|---|---|
| bldg_id | 0.0% |
| source | 0.0% |
| source_confidence | 9.6% |
| area_m2 | 0.0% |
| perimeter_m | 0.0% |
| compactness | 0.0% |
| orientation_deg | 0.0% |
| n_vertices | 0.0% |
| lon | 0.0% |
| lat | 0.0% |
| h3_r9 | 0.0% |
| qa_flags | 0.0% |

| qa flag | buildings | share |
|---|---|---|
| low_confidence | 808,772 | 12.57% |
| tiny | 34,070 | 0.53% |
| nested_same_source | 2,186 | 0.03% |
