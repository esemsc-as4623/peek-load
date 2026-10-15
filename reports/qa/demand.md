# QA: demand
**PASS**

- checked: 2026-10-15 23:32 UTC
- archetypes: 21

| id set | ids | missing vs archetype JSON | extra |
|---|---|---|---|
| recipes (config/archetypes.yaml) | 21 | - | - |
| mapping (config/profile_mapping.yaml) | 13 | bar_rural, carpentry, grain_mill, health_centre, hospital, tailoring, welding_workshop, workshop_rural | - |
| dashboard (config/dashboard.yaml) | 21 | - | - |
| services (config/services.yaml) | 3 | bar_rural, carpentry, church, grain_mill, health_centre, health_post, hospital, household_rural_lower, household_rural_upper, household_urban_lower, household_urban_upper, kiosk_shop, office, school, tailoring, warehouse_light, welding_workshop, workshop_rural | - |
| productive use (config/productive_use.yaml) | 5 | bar_restaurant, bar_rural, church, guesthouse, health_centre, health_post, hospital, household_rural_lower, household_rural_upper, household_urban_lower, household_urban_upper, kiosk_shop, office, salon, school, warehouse_light | - |
| profile_library | 21 | - | - |
| building_profiles | 21 | - | - |
| archetype JSON (+ derived copies) | 21 | - | - |

| check | result | detail |
|---|---|---|
| weights non-negative and present | PASS | 0 bad of 3,523,077 |
| library daily kWh > 0 | PASS | 21 profiles |
| archetype status matches approvals | PASS | ['human_reviewed'] vs ['human_reviewed'] |
| allocation: every district's establishments = census target (tagged + assigned + shortfall) | PASS | 390 district x item rows; shortfall 0; tags above target in 2 rows |
| allocation: households per district = 2022 census (or shortfall reported) | PASS | 3,312,743 of 3,312,743; shortfall 0 |
| allocation: households urban per district = 2022 census (or shortfall reported) | PASS | 964,287 of 964,287; shortfall 0 |
| allocation: households rural per district = 2022 census (or shortfall reported) | PASS | 2,348,456 of 2,348,456; shortfall 0 |
| allocation: one use per building, every building with a district | PASS | 6,433,414 rows, 6,433,414 ids, 6,433,414 buildings |
| allocation: profile units = households + the building's share of its establishment | PASS | 0 mismatches |
| allocation: facility compounds add up to whole facilities (shares sum to integers per kind) | PASS | 0 kinds with a fractional total |
| allocation: upper-wealth household share per setting matches the DHS-based targets (+-0.02) | PASS | rural 0.359 vs 0.359; urban 0.547 vs 0.547 |
| calibration: households' mean P(connected) = EICV7 2024 grid share in every district (+-0.005) | PASS | largest gap 0.0000 |
| allocation: tagged institutions, worship, stores and ancillary keep their tag (pharmacies are shops) | PASS | 0 changed of 36,048 tagged buildings |
| allocation: share of buildings carrying demand is plausible (40-75%) | PASS | 54.2% (census: 0.52 households per building) |
| allocation: establishments respect the spacing caps (relaxed caps used for <= 10%) | PASS | 14,903 of 207,346 census-placed establishments sit above their cell's base cap (7.2%) |
| use classifier B ranks held-out uses (macro AUC >= 0.70 on tags; ancillary AUC >= 0.70 by sector) | PASS | 0.778; 0.820 |
| grid model: one probability in [0, 1] per building | PASS | 6,434,247 of 6,434,247 |
