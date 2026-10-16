"""Dashboard bundle: the client-side formulas in dashboard/DATA_CONTRACT.md reproduce the exported numbers, and the
committed sample bundle is internally consistent."""

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from rtl.dashboard.export import building_demand, characteristics, pu_scores

SAMPLE = Path(__file__).resolve().parents[1] / "dashboard" / "sample_data" / "v1"
needs_sample = pytest.mark.skipif(not (SAMPLE / "manifest.json").exists(), reason="sample bundle not built")


def test_characteristics_of_an_evening_peak():
    c = np.full(24, 10.0)
    c[18:23] = 100.0
    ch = characteristics(c, c, c)
    assert ch["peak_w"] == 100.0 and ch["peak_hour"] == 18
    assert ch["load_factor"] == pytest.approx(c.mean() / 100, abs=0.01)
    assert ch["evening_share"] == pytest.approx(500 / c.sum(), abs=0.01) and ch["shape"] == "evening peak"
    assert ch["weekday_weekend_ratio"] == 1.0


def test_building_demand_is_weights_times_profiles():
    lib = pd.DataFrame({"daily_kwh_mean": [1.0, 10.0], "daily_kwh_p10": [0.9, 9.0], "daily_kwh_p90": [1.1, 11.0],
                        **{f"w_{k}h{h:02d}": [100.0 if h == 20 else 0.0, 500.0 if h == 10 else 0.0]
                           for k in ("", "weekday_", "weekend_") for h in range(24)}}, index=["home", "mill"])
    w = pd.DataFrame({"bldg_id": ["a", "a", "b"], "profile_id": ["home", "mill", "home"], "weight": [1.0, 0.1, 0.5]})
    dem, _ = building_demand(w, lib)
    assert dem.loc["a", "annual_kwh"] == pytest.approx(365 * (1.0 + 1.0), abs=0.1)
    assert dem.loc["a", "peak_w"] == 100.0 and dem.loc["a", "peak_hour"] == 20  # 100 W at 20:00 beats 50 W at 10:00
    assert dem.loc["b", "annual_kwh"] == pytest.approx(182.5, abs=0.1)


def test_pu_score_uses_group_weights_and_only_eligible_buildings():
    df = pd.DataFrame({"poi_count_250m": [10, 10, 0], "dist_road_major_m": [0.0, 0.0, 5000.0],
                       "pop_density_per_km2": [15000.0, 15000.0, 0.0], "dist_powerline_m": [0.0, 0.0, 9000.0],
                       "rwi": [1.248, 1.248, -1.0], "cropland_share": [0.0, 1.0, 0.0], "area_m2": [500.0, 500.0, 20.0],
                       "pu_group": ["town", "grain_mill", "town"], "pu_eligible": [True, True, False]})
    pu_cfg = {"candidate_score": {"town": {"market_access": 1, "agri_context": 0},
                                  "grain_mill": {"agri_context": 1, "building_size": 1}}}
    cats = {"categories": {"low": [0, 33.3], "medium": [33.3, 66.7], "high": [66.7, 100]}}
    crit, score, cat = pu_scores(df, pu_cfg, cats)
    assert list(score[:2]) == [100.0, 100.0]  # each group scored on its own criteria
    assert pd.isna(score[2]) and cat[2] is None  # ineligible: no score
    assert crit.building_size[2] == 0.0 and crit.ability_to_pay[2] == 0.0


@needs_sample
def test_sample_bundle_files_match_their_manifest():
    man = json.loads((SAMPLE / "manifest.json").read_text())
    for f in man["files"]:
        data = (SAMPLE / f["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == f["sha256"], f["path"]
        if f["path"].endswith(".geojson"):
            assert len(json.loads(data)["features"]) == f["features"]


@needs_sample
def test_frontend_formulas_reproduce_exported_building_metrics():
    profiles = {p["profile_id"]: p for p in json.loads((SAMPLE / "profiles" / "profiles.json").read_text())["profiles"]}
    meta = json.loads((SAMPLE / "meta.json").read_text())
    wts = meta["productive_use"]["weights"]
    for dd in ("nyamirambo", "muhoza", "nasho"):
        w = json.loads((SAMPLE / "deep_dives" / dd / "weights.json").read_text())
        order = w["profile_order"]
        P = np.array([profiles[k]["hourly_w"]["all"] for k in order])  # noqa: N806
        daily = np.array([profiles[k]["daily_kwh"] for k in order])
        feats = json.loads((SAMPLE / "deep_dives" / dd / "buildings.geojson").read_text())["features"]
        for f in feats:
            p = f["properties"]
            if not p["modelled"]:
                assert p["annual_kwh"] is None and p["bldg_id"] not in w["probabilities"]
                continue
            v = np.array(w["probabilities"][p["bldg_id"]])
            curve = v @ P
            assert 365 * v @ daily == pytest.approx(p["annual_kwh"], abs=0.15)
            assert curve.max() == pytest.approx(p["peak_w"], abs=0.15) and int(curve.argmax()) == p["peak_hour"]
            if p["bldg_id"] not in w["pu_criteria"]:
                assert p["pu_score"] is None
                continue
            crit = dict(zip(w["pu_criteria_order"], w["pu_criteria"][p["bldg_id"]], strict=True))
            g = {k: x for k, x in wts[w["pu_group"][p["bldg_id"]]].items() if x}
            score = 100 * sum(x * crit[k] for k, x in g.items()) / sum(g.values())
            assert score == pytest.approx(p["pu_score"], abs=0.3)


@needs_sample
def test_workflow_edges_point_at_nodes():
    wf = json.loads((SAMPLE / "workflow" / "workflow.json").read_text())
    ids = {n["id"] for n in wf["nodes"]}
    assert all(e["from"] in ids and e["to"] in ids for e in wf["edges"])
    assert {n["lane"] for n in wf["nodes"]} <= {lane["id"] for lane in wf["lanes"]}


def test_site_frame_is_1000_wide_with_the_margin_and_keeps_shapes_square():
    import math

    import numpy as np

    from rtl.dashboard.pack import Frame

    f = Frame(np.array([[30.0, -2.0], [30.1, -1.9]]), 0.01)
    x0, y0 = f.xy(29.99, -1.89)  # top-left corner of the margin
    x1, y1 = f.xy(30.11, -2.01)
    assert (round(x0, 6), round(y0, 6), round(x1, 6)) == (0, 0, 1000)
    assert y1 == f.height or abs(y1 - f.height) < 0.05
    # a degree of latitude is longer on the page than a degree of longitude by 1 / cos(latitude)
    assert abs(f.ky / f.kx - 1 / math.cos(math.radians(-1.95))) < 1e-12
    assert f.path([[[30.0, -2.0], [30.1, -2.0], [30.0, -2.0]]]).startswith("M83.3 917.2L916.7 917.2")


def test_pick_rounds_scales_and_keeps_missing_values():
    from rtl.dashboard.pack import pick

    out = pick({"g": 0.031, "a": 12.3456, "s": "x"}, {"growth": ("g", 1, 100), "a": ("a", 1), "s": ("s", None),
                                                       "none": (None, None)})
    assert out == {"growth": 3.1, "a": 12.3, "s": "x", "none": None}
