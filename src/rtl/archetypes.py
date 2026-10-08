"""RAMP appliance archetypes as validated JSON: every number carries its evidence or an explicit assumption.

The models mirror RAMP (rampdemand 0.5.2) 1:1: `User(user_name, num_users, user_preference)` and
`User.add_appliance(name, number, power, num_windows, func_time, time_fraction_random_variability, func_cycle,
fixed, fixed_cycle, continuous_duty_cycle, occasional_use, flat, thermal_p_var, pref_index, wd_we_type,
window_1..3, random_var_w)`. Field names are RAMP's own, so a reviewer can check them against the RAMP docs.

Each numeric parameter is a `Sourced` value: it must cite `evidence_ids` (rows of the `evidence` table or
`prior_id`s in validation_anchors/dhs_ownership_priors.csv) or give an `assumption` rationale, and a
`derivation` says how cited numbers became the value. A file can't validate with an unsourced number.

Ownership isn't a RAMP parameter: RAMP gives every user in a class every appliance. `to_ramp` therefore makes
one RAMP User class per appliance with num_users = round(ownership_share x n_households). Appliances in a
RAMP user are simulated independently (except cooking preferences, unused here), so the expected aggregate
load is the same as for mixed ownership within one class.

Duty cycles (p_11, t_11, cw11, ...; fridges, irons) are not modelled yet: fixed_cycle must stay 0.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Sourced(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: float | list[list[int]]
    evidence_ids: list[str] = Field(default_factory=list)
    assumption: str | None = None  # rationale, required when there is no evidence
    derivation: str | None = None  # how the cited numbers became `value`
    human_reviewed: bool = False   # archetype parameters are a human gate

    @model_validator(mode="after")
    def _has_source(self) -> Sourced:
        if not self.evidence_ids and not (self.assumption and self.assumption.strip()):
            raise ValueError("every value needs evidence_ids or an assumption rationale")
        return self


def ramp_default(value: float) -> Sourced:
    return Sourced(value=value, assumption="RAMP default (rampdemand 0.5.2)")


class Appliance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    ownership_share: Sourced                      # not RAMP: fraction of households owning it (see module doc)
    number: Sourced                               # appliances per owning household
    power: Sourced                                # W
    func_time: Sourced                            # minutes on per day
    windows: Sourced                              # [[start_min, end_min], ...] -> window_1..3, num_windows
    func_cycle: Sourced = Field(default_factory=lambda: ramp_default(1))
    time_fraction_random_variability: Sourced = Field(default_factory=lambda: ramp_default(0))
    random_var_w: Sourced = Field(default_factory=lambda: ramp_default(0))
    occasional_use: Sourced = Field(default_factory=lambda: ramp_default(1))
    thermal_p_var: Sourced = Field(default_factory=lambda: ramp_default(0))
    wd_we_type: Sourced = Field(default_factory=lambda: ramp_default(2))
    pref_index: Sourced = Field(default_factory=lambda: ramp_default(0))
    fixed_cycle: Sourced = Field(default_factory=lambda: ramp_default(0))
    continuous_duty_cycle: Sourced = Field(default_factory=lambda: ramp_default(1))
    fixed: str = "no"
    flat: str = "no"

    @model_validator(mode="after")
    def _check(self) -> Appliance:
        w = self.windows.value
        if not isinstance(w, list) or not 1 <= len(w) <= 3:
            raise ValueError(f"{self.name}: 1-3 windows required")
        if sum(b - a for a, b in w) < self.func_time.value:
            raise ValueError(f"{self.name}: windows shorter than func_time")  # RAMP raises InvalidWindow too
        if not 0 <= self.ownership_share.value <= 1:
            raise ValueError(f"{self.name}: ownership_share must be in [0, 1]")
        if self.fixed_cycle.value != 0:
            raise ValueError(f"{self.name}: duty cycles not supported yet")
        return self


class Archetype(BaseModel):
    model_config = ConfigDict(extra="forbid")
    archetype_id: str
    description: str
    population: str                               # who this represents, e.g. "rural grid-connected households"
    year: int
    user_preference: Sourced = Field(default_factory=lambda: ramp_default(0))
    appliances: list[Appliance]
    status: str = "draft"                         # draft -> human_reviewed (human gate)

    @classmethod
    def load(cls, path: Path) -> Archetype:
        return cls.model_validate(json.loads(Path(path).read_text()))

    def expected_daily_kwh(self, n_households: int) -> float:
        """Deterministic expectation (no randomness): sum of share x n x number x power x hours."""
        return sum(round(a.ownership_share.value * n_households) * a.number.value * a.power.value
                   * a.func_time.value / 60 * a.occasional_use.value for a in self.appliances) / 1000

    def to_ramp(self, n_households: int, usecase=None) -> list:
        """RAMP User objects, one class per appliance (ownership via num_users; see module doc)."""
        from ramp import User

        users = []
        for a in self.appliances:
            n = round(a.ownership_share.value * n_households)
            if n == 0:
                continue
            u = User(user_name=f"{self.archetype_id}:{a.name}", num_users=n,
                     user_preference=int(self.user_preference.value), usecase=usecase)
            windows = {f"window_{i}": w for i, w in enumerate(a.windows.value, start=1)}
            u.add_appliance(name=a.name, number=int(a.number.value), power=float(a.power.value),
                            num_windows=len(a.windows.value), func_time=int(a.func_time.value),
                            time_fraction_random_variability=a.time_fraction_random_variability.value,
                            func_cycle=int(a.func_cycle.value), fixed=a.fixed, fixed_cycle=int(a.fixed_cycle.value),
                            continuous_duty_cycle=int(a.continuous_duty_cycle.value),
                            occasional_use=a.occasional_use.value, flat=a.flat,
                            thermal_p_var=a.thermal_p_var.value, pref_index=int(a.pref_index.value),
                            wd_we_type=int(a.wd_we_type.value), random_var_w=a.random_var_w.value, **windows)
            users.append(u)
        return users


def smoke_run(archetype: Archetype, n_households: int = 100, day: str = "2026-01-05", seed: int = 1):
    """One simulated day (1440 one-minute values, W). A format check, not a calibrated result."""
    from ramp import UseCase

    uc = UseCase(name=archetype.archetype_id, date_start=day, date_end=day, random_seed=seed)
    uc.add_user(archetype.to_ramp(n_households, usecase=uc))
    return uc.generate_daily_load_profiles()


if __name__ == "__main__":  # pixi run archetype-smoke
    import sys

    from rtl.settings import PROCESSED_DIR

    path = Path(sys.argv[1]) if len(sys.argv) > 1 else PROCESSED_DIR / "archetypes" / "rural_grid_household_tier2.json"
    arch = Archetype.load(path)
    profile = smoke_run(arch)
    print(f"{arch.archetype_id}: {len(profile)} minutes, peak {profile.max() / 1000:.2f} kW, "
          f"{profile.sum() / 60 / 1000:.2f} kWh/day for 100 households "
          f"(expectation {arch.expected_daily_kwh(100):.2f} kWh/day)")
