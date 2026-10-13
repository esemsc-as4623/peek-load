# QA: demand
**PASS**

- checked: 2026-10-13 10:55 UTC
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
| weights non-negative and present | PASS | 0 bad of 56,746 |
| library daily kWh > 0 | PASS | 17 profiles |
| archetype status matches approvals | PASS | ['human_reviewed'] vs ['human_reviewed'] |
| productive use: area levels add up to the allocated census amount | PASS | {'district': 17706.0, 'h3_r7': 17706.0, 'sector': 17706.0} vs 17,706 |
| services: area levels add up to the allocated census amount | PASS | {'district': 73488.0, 'h3_r7': 73488.0, 'sector': 73488.0} vs 73,488 |
| grid model: one probability in [0, 1] per building | PASS | 6,434,247 of 6,434,247 |
