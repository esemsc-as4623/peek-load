"""Phase-2 groundwork: profile mapping rules and library summaries, on synthetic inputs."""

import numpy as np
import pandas as pd
import pytest

from rtl.demand.mapping import building_weights, class_profiles

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
