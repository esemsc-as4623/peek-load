"""The release's per-building metrics recompute from weights x profile curves."""

import numpy as np
import pandas as pd

from rtl.release.build import HOURS, curve_metrics


def test_curve_metrics_match_a_hand_computed_building():
    flat = {h: 10.0 for h in HOURS}
    evening = {h: (100.0 if 18 <= int(h[-2:]) <= 22 else 0.0) for h in HOURS}
    lib = pd.DataFrame([flat, evening], index=["flat", "evening"])
    for kind in ("weekday", "weekend"):
        for h in range(24):
            lib[f"w_{kind}_h{h:02d}"] = lib[f"w_h{h:02d}"]
    lib["daily_kwh_p10"], lib["daily_kwh_p90"] = [0.2, 0.4], [0.3, 0.6]
    bp = pd.DataFrame({"bldg_id": ["a", "a", "b"], "profile_id": ["flat", "evening", "flat"],
                       "weight": [2.0, 1.0, 1.0]})
    m = curve_metrics(bp, lib).set_index("bldg_id")
    curve_a = 2 * 10 + np.array([100.0 if 18 <= h <= 22 else 0.0 for h in range(24)])
    assert m.at["a", "peak_w"] == curve_a.max() and m.at["a", "peak_hour"] == 18
    assert abs(m.at["a", "load_factor"] - curve_a.mean() / curve_a.max()) < 1e-6
    assert abs(m.at["a", "evening_share"] - curve_a[18:24].sum() / curve_a.sum()) < 1e-6
    assert m.at["a", "load_shape"] == "evening peak" and m.at["b", "load_shape"] == "flat"
    assert abs(m.at["a", "annual_kwh_p10"] - (2 * 0.2 + 0.4) * 365) < 1e-6
