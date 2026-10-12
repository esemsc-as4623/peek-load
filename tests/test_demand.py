"""Phase-2 groundwork: profile mapping rules and library summaries, on synthetic inputs."""

import numpy as np
import pandas as pd
import pytest

from rtl.demand.mapping import building_weights, class_profiles, kept_probabilities

RULES = {
    "residential": [{"when": {"urban": True}, "profiles": {"hh_urban": 1}},
                    {"when": {}, "profiles": {"hh_rural": 1}}],
    "mixed_shop_house": [{"when": {}, "profiles": {"@residential": 1, "kiosk": 1}}],
    "productive_use": [{"when": {}, "profiles": {"mill": 0.5, "weld": 0.5}}],
    "ancillary": [{"when": {}, "profiles": {}}],
}
ROW = pd.Series({"urban": True, "rich": False, "overture_category": None, "facility_type": None})


def test_first_matching_rule_wins_and_references_expand():
    assert class_profiles(ROW, "residential", RULES) == {"hh_urban": 1.0}
    assert class_profiles(ROW.copy().replace({True: False}), "residential", RULES) == {"hh_rural": 1.0}
    # a shop-house is one household (of its context) plus one kiosk
    assert class_profiles(ROW, "mixed_shop_house", RULES) == {"hh_urban": 1.0, "kiosk": 1.0}
    assert class_profiles(ROW, "ancillary", RULES) == {}


def test_weights_follow_class_probabilities_and_are_additive():
    probs = {"residential": 0.6, "mixed_shop_house": 0.3, "productive_use": 0.1}
    w = building_weights(ROW, probs, RULES | {c: [{"when": {}, "profiles": {}}] for c in
                                              ["commercial", "institutional", "religious", "industrial_warehouse",
                                               "unknown"]})
    assert w["hh_urban"] == pytest.approx(0.9)  # 0.6 residential + 0.3 from the shop-house
    assert w["kiosk"] == pytest.approx(0.3)
    assert w["mill"] == pytest.approx(0.05) and w["weld"] == pytest.approx(0.05)


def test_threshold_drops_small_classes_and_area_level_productive_use():
    cfg = {"min_class_probability": 0.10, "area_level_classes": ["productive_use"]}
    probs = {"residential": 0.62, "commercial": 0.18, "mixed_shop_house": 0.05, "productive_use": 0.15}
    kept = kept_probabilities(probs, cfg)
    # productive use leaves the per-building weights, the 0.05 shop-house is ignored, the rest renormalised
    assert kept == pytest.approx({"residential": 0.62 / 0.80, "commercial": 0.18 / 0.80})
    assert kept_probabilities(probs, cfg, top_class=True) == {"residential": 1.0}
    # a building labelled mostly productive use still gets its best remaining class rather than vanishing
    assert kept_probabilities({"productive_use": 0.9, "residential": 0.06, "ancillary": 0.04}, cfg) == {
        "residential": 1.0}


def test_library_summary_on_a_constant_load():
    from rtl.demand.runner import summarise

    class Fake:
        archetype_id, status = "flat", "draft"

        def expected_daily_kwh(self, n):
            return 2.4 * n

    load = np.full((7, 1440), 100.0 * 10)  # 10 households drawing 100 W each, all day, for a week
    s = summarise(Fake(), load, n=10)
    assert s["daily_kwh_mean"] == pytest.approx(2.4)
    assert s["coincident_peak_w_p90"] == pytest.approx(100.0)
    assert all(s[f"w_h{h:02d}"] == pytest.approx(100.0) for h in range(24))


def test_validation_flags_unit_slips_ranges_and_disagreement():
    from rtl.demand.validate import checks

    cfg = {"rated_power_w": [{"match": "tv", "min": 5, "max": 300}, {"match": ".*", "min": 0.5, "max": 5e4}],
           "hours_per_day": {"min": 0, "max": 24}, "ownership_share": {"min": 0, "max": 1},
           "usage_window_hour": {"min": 0, "max": 24}, "consensus": {"min_sources": 3, "max_ratio": 3.0}}
    rows = [("a", "rated_power_w", "tv", 60, "W"), ("b", "rated_power_w", "tv", 70, "W"),
            ("c", "rated_power_w", "tv", 0.08, "kW"),       # 80 W after unit conversion: fine
            ("d", "rated_power_w", "tv", 900, "W"),         # outside the TV range
            ("e", "hours_per_day", "radio", 30, "h/day"),   # impossible hours
            ("f", "ownership_share", "fridge", 0.4, "fraction")]
    e = pd.DataFrame([{"evidence_id": i, "family": f, "appliance": a, "value": v, "value_low": None,
                       "value_high": None, "unit": u, "doc_id": "x", "page": 1} for i, f, a, v, u in rows])
    flags = checks(e, cfg)
    assert set(flags.evidence_id) == {"d", "e"}
    assert set(flags[flags.evidence_id == "d"].check) == {"range", "consensus"}


def test_plausibility_rules_match_whole_words_not_substrings():
    from rtl.demand.validate import power_range
    from rtl.settings import load_yaml

    rules = load_yaml("plausibility.yaml")["rated_power_w"]
    assert power_range("rice_mill_maize_huller", rules) == (500, 40000)  # a mill, not "ice" (freezer)
    assert power_range("ice_freezer", rules) == (25, 800)
    assert power_range("flat_iron", rules) == (20, 300)                  # hair straightener, not a clothes iron
    assert power_range("electric_iron", rules) == (300, 2600)


def test_spending_inverts_the_block_tariff():
    from rtl.ingest.mtf_appliances import kwh_from_spend

    blocks = [(15, 89.0), (50, 212.0), (float("inf"), 249.0)]
    vat = 1.18
    spend = np.array([10 * 89, 15 * 89 + 5 * 212, 15 * 89 + 35 * 212 + 10 * 249]) * vat  # 10, 20 and 60 kWh
    assert kwh_from_spend(spend, blocks) == pytest.approx([10, 20, 60])
    assert kwh_from_spend(np.array([0.0]), blocks) == pytest.approx([0])


def test_ubudehe_split_and_unknowns():
    from rtl.ingest.mtf_appliances import ubudehe_half

    s = pd.Series(["Category 1", "Category 2", "Category 3", "Category 4", "Don't know/ Don't have"])
    assert ubudehe_half(s).tolist()[:4] == ["lower", "lower", "upper", "upper"]
    assert pd.isna(ubudehe_half(s).iloc[4])


def test_check_verdicts():
    from rtl.demand.checks import mtf_tier, verdict

    assert verdict(10, 8) == "ok" and verdict(10, 5) == "check" and verdict(10, 2) == "fail"
    assert verdict(50, low=37, high=509) == "ok" and verdict(361, low=1367, high=4101) == "fail"
    tiers = {1: {"min_wh_day": 12}, 2: {"min_wh_day": 200}, 3: {"min_wh_day": 1000}}
    assert [mtf_tier(w, tiers) for w in (5, 12, 330, 999, 1000)] == [0, 1, 2, 2, 3]


def test_recipe_values_need_a_source():
    from rtl.demand.build_archetypes import sourced

    v = sourced({"value": 15, "why": "typical CFL"}, src=None, family="rated_power_w")
    assert v.value == 15 and v.assumption == "typical CFL"
    with pytest.raises(KeyError):  # an assumed number without its rationale is rejected
        sourced({"value": 15}, src=None, family="rated_power_w")


def test_lighting_scaling_touches_only_lights_and_keeps_a_full_cycle():
    from rtl.archetypes import Appliance, Sourced
    from rtl.demand.build_archetypes import scale_lights

    def app(name, minutes):
        s = Sourced(value=1, assumption="test")
        return Appliance(name=name, ownership_share=s, number=s, power=Sourced(value=10, assumption="t"),
                         func_time=Sourced(value=minutes, assumption="t"),
                         windows=Sourced(value=[[0, 1440]], assumption="t"),
                         func_cycle=Sourced(value=30, assumption="t"))

    out = scale_lights([app("bulb", 240), app("tv", 180), app("lamp", 40)], [True, False, True], 0.5, "; x0.5", "id")
    assert [a.func_time.value for a in out] == [120, 180, 30]  # 40 x 0.5 = 20 < func_cycle 30 -> 30
    assert out[0].func_time.evidence_ids == ["id"] and out[1].func_time.evidence_ids == []


def test_ramp_runs_are_reproducible_and_peak_window_follows_the_users():
    from rtl.archetypes import Appliance, Archetype, Sourced, ramp_usecase
    from rtl.demand.runner import simulate

    def s(v):
        return Sourced(value=v, assumption="test")

    lights = Appliance(name="bulb", ownership_share=s(1), number=s(4), power=s(10), func_time=s(180),
                       windows=s([[1080, 1380]]), func_cycle=s(30))
    arch = Archetype(archetype_id="t", description="t", population="t", year=2022, appliances=[lights])
    a, b = simulate(arch, n=20, days=2), simulate(arch, n=20, days=2)
    assert np.array_equal(a, b)  # same seed -> same profile (RAMP alone isn't: it seeds after drawing)
    peak = ramp_usecase(arch, 20, "2025-01-06", "2025-01-06", seed=1).peak_time_range
    assert 1000 < np.mean(peak) < 1440  # around the evening lights, not anywhere in the day


def test_unlocated_allocation_conserves_units_and_follows_score():
    from rtl.demand.productive_use import allocate

    cand = pd.DataFrame({"score": [0.0, 50.0, 100.0]})
    units = allocate(cand, 10.0)
    assert units.sum() == pytest.approx(10.0)  # nothing created or lost when spreading the census amount
    assert units[0] < units[1] < units[2] and units[0] > 0  # a zero score still gets a small share


def test_census_parsing_on_a_planted_workbook(tmp_path, monkeypatch):
    from rtl.demand import productive_use as pu

    districts = [[f" D{i}", 0, 0, 0, 100 + i, 60, 40 + i] for i in range(30)]
    with pd.ExcelWriter(tmp_path / "ec.xlsx") as xw:
        pd.DataFrame([["title"] + [None] * 6, ["District", 2020, None, None, 2023, None, None],
                      *districts, ["Kigali City", 1, 1, 1, 1, 1, 1], ["Rwanda", 1, 1, 1, 1, 1, 1]]).to_excel(
            xw, sheet_name="T450", header=False, index=False)
        pd.DataFrame([["title"] + [None] * 10, ["Activity"] + [None] * 10,
                      ["Manufacturing", 0, 0, 0, 10, 0, 0, 0, 0, 20, 0],
                      ["Total", 0, 0, 0, 100, 0, 0, 0, 0, 400, 0]]).to_excel(
            xw, sheet_name="T243", header=False, index=False)
    monkeypatch.setattr(pu, "RESTRICTED_DIR", tmp_path)
    cfg = {"census": {"file": "ec.xlsx", "enterprises_by_district": "T450", "establishments_by_activity": "T243",
                      "activity": "Manufacturing"}}
    out = pu.census_counts(cfg)
    d0 = out[out.district == "D0"].set_index("setting")
    assert d0.at["urban", "pu_census"] == pytest.approx(60 * 0.10)  # 10 / 100 urban establishments
    assert d0.at["rural", "pu_census"] == pytest.approx(40 * 0.05)  # 20 / 400 rural establishments
    assert len(out) == 60  # province and national rows skipped


def test_worldcover_block_fraction():
    from rtl.ingest.worldcover import block_fraction

    cls = np.full((20, 25), 10, dtype=np.uint8)
    cls[:10, :10] = 40           # one whole block of cropland
    cls[10:15, 10:20] = 40       # half of another block
    frac = block_fraction(cls, 40, 10)
    assert frac.shape == (2, 2)  # the ragged 5 columns are trimmed
    assert frac[0, 0] == 1.0 and frac[1, 1] == 0.5 and frac[0, 1] == 0.0
