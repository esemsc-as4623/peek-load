# RAMP archetype template

An archetype is a JSON file that validates against `rtl.archetypes.Archetype` (JSON Schema:
`archetype.schema.json`, generated from the pydantic model). Field names are RAMP's own
(rampdemand 0.5.2, `ramp.core.core.User` / `User.add_appliance`), so each one maps 1:1:

| JSON (per appliance) | RAMP `add_appliance` argument | unit |
|---|---|---|
| `name` | `name` | |
| `number` | `number` | appliances per owning household |
| `power` | `power` | W |
| `func_time` | `func_time` | min/day |
| `windows` `[[a,b],…]` (1-3) | `window_1..3`, `num_windows` | minute of day |
| `func_cycle` | `func_cycle` | min |
| `time_fraction_random_variability` | same | fraction |
| `random_var_w` | same | fraction |
| `occasional_use` | same | fraction of days |
| `thermal_p_var`, `pref_index`, `wd_we_type`, `fixed_cycle`, `continuous_duty_cycle`, `fixed`, `flat` | same | |
| `ownership_share` | *(not RAMP)* → `User.num_users` = round(share × households) | fraction |
| archetype `user_preference` | `User.user_preference` | |

**Every number is a `Sourced` object**: `{"value", "evidence_ids": [...]}` (ids of `evidence` rows or
`prior_id`s in `../validation_anchors/dhs_ownership_priors.csv`) or `{"value", "assumption": "<rationale>"}`,
optionally with `derivation` and `human_reviewed` (false until the repo owner signs it off). A file with a
bare number doesn't validate. RAMP defaults are filled in as `assumption: "RAMP default"`, so they are visible too.

Ownership: RAMP gives every user in a class every appliance, so `to_ramp(n_households)` builds one RAMP User
class per appliance with `num_users = round(ownership_share × n_households)`. Expected aggregate load is the
same as mixed ownership in one class, because RAMP draws each appliance independently. Not supported yet:
duty cycles (`p_11`, `t_11`, `cw11`, …: fridges, irons) and cooking preferences.

## Files
- `rural_grid_household_tier2.json`: **draft**. Lights, phone charger, radio and TV. Ownership is from DHS
  2025 (rural); TV is P(TV)/P(electricity) = 0.172. Phone uses `mobile_phone_any` (FR401 Table 2.5), because
  the API phone indicator counts only ordinary phones (see validation_anchors/README.md). Every power, hour and
  window is a labelled assumption until evidence extraction runs and a human reviews it.
- Smoke test: `pixi run archetype-smoke` simulates one day for 100 households:
  1440 one-minute values, ~1.8 kW peak, ~8.4-8.9 kWh/day (≈ 2.6-2.7 kWh/month per household; QSEL's rural
  median is 4 kWh/month). That only checks that the format loads; it is not a calibrated result.
