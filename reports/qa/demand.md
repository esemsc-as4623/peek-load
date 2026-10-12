# QA: demand
**PASS**

- checked: 2026-10-12 08:57 UTC
- archetypes: 17

| id set | ids | missing vs archetype JSON | extra |
|---|---|---|---|
| recipes (config/archetypes.yaml) | 17 | - | - |
| archetype JSON | 17 | - | - |
| mapping (config/profile_mapping.yaml) | 17 | - | - |
| dashboard (config/dashboard.yaml) | 17 | - | - |
| profile_library | 17 | - | - |
| building_profiles | 15 | bar_restaurant, salon | - |

| check | result | detail |
|---|---|---|
| weights non-negative and present | PASS | 0 bad of 397,509 |
| library daily kWh > 0 | PASS | 17 profiles |
| archetype status matches approvals | PASS | ['human_reviewed'] vs ['human_reviewed'] |
