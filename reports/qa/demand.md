# QA: demand
**PASS**

- checked: 2026-10-15 11:54 UTC
- archetypes: 17

| id set | ids | missing vs archetype JSON | extra |
|---|---|---|---|
| recipes (config/archetypes.yaml) | 17 | - | - |
| archetype JSON | 17 | - | - |
| mapping (config/profile_mapping.yaml) | 13 | carpentry, grain_mill, tailoring, welding_workshop | - |
| dashboard (config/dashboard.yaml) | 17 | - | - |
| services (config/services.yaml) | 3 | carpentry, church, grain_mill, health_post, household_rural_lower, household_rural_upper, household_urban_lower, household_urban_upper, kiosk_shop, office, school, tailoring, warehouse_light, welding_workshop | - |
| productive use (config/productive_use.yaml) | 4 | bar_restaurant, church, guesthouse, health_post, household_rural_lower, household_rural_upper, household_urban_lower, household_urban_upper, kiosk_shop, office, salon, school, warehouse_light | - |
| profile_library | 17 | - | - |
| building_profiles | 17 | - | - |

| check | result | detail |
|---|---|---|
| weights non-negative and present | PASS | 0 bad of 3,509,127 |
| library daily kWh > 0 | PASS | 17 profiles |
| archetype status matches approvals | PASS | ['human_reviewed'] vs ['human_reviewed'] |
| allocation: every district's establishments = census target (tagged + assigned + shortfall) | PASS | 330 district x item rows; shortfall 0; tags above target in 2 rows |
| allocation: households per district = 2022 census (or shortfall reported) | PASS | 3,312,743 of 3,312,743; shortfall 0 |
| allocation: one use per building, every building with a district | PASS | 6,433,414 rows, 6,433,414 ids, 6,433,414 buildings |
| allocation: profile weights = households + establishment, building by building | PASS | 0 mismatches |
| allocation: tagged institutions, worship, stores and ancillary keep their tag | PASS | 0 changed of 36,048 tagged buildings |
| allocation: share of buildings carrying demand is plausible (40-75%) | PASS | 54.0% (census: 0.52 households per building) |
| allocation: establishments respect the spacing caps (relaxed caps used for <= 10%) | PASS | 15,179 of 233,732 census-placed establishments sit above their cell's base cap (6.5%) |
| use classifier B ranks held-out uses (macro AUC >= 0.70 on tags; ancillary AUC >= 0.70 by sector) | PASS | 0.778; 0.820 |
| grid model: one probability in [0, 1] per building | PASS | 6,434,247 of 6,434,247 |
