"""Dashboard bundle: the client-side formulas in dashboard/DATA_CONTRACT.md reproduce the exported numbers, and the
committed sample bundle is internally consistent."""

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from rtl.dashboard.export import building_demand, characteristics, pu_score

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


def test_pu_score_is_gated_by_likelihood_and_skips_missing_components():
    cfg = {"components": {"a": {"weight": 0.5}, "b": {"weight": 0.5}},
           "categories": {"low": [0, 33.3], "medium": [33.3, 66.7], "high": [66.7, 100]}}
    c = pd.DataFrame({"pu_likelihood": [0.0, 1.0, 0.25], "a": [1.0, 1.0, 1.0], "b": [1.0, np.nan, 1.0]})
    score, cat = pu_score(c, cfg)
    assert list(score) == [0.0, 100.0, 50.0]  # sqrt(0.25) = 0.5; the missing `b` is dropped, not counted as 0
    assert list(cat) == ["low", "high", "medium"]


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
    wts = {k: v["weight"] for k, v in meta["pu_prototype"]["components"].items()}
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
            comp = dict(zip(w["pu_component_order"], w["pu_components"][p["bldg_id"]], strict=True))
            present = {k: x for k, x in comp.items() if k in wts and x is not None}
            score = 100 * np.sqrt(comp["pu_likelihood"]) * sum(wts[k] * x for k, x in present.items()) / sum(
                wts[k] for k in present)
            assert score == pytest.approx(p["pu_score"], abs=0.3)


@needs_sample
def test_workflow_edges_point_at_nodes():
    wf = json.loads((SAMPLE / "workflow" / "workflow.json").read_text())
    ids = {n["id"] for n in wf["nodes"]}
    assert all(e["from"] in ids and e["to"] in ids for e in wf["edges"])
    assert {n["lane"] for n in wf["nodes"]} <= {lane["id"] for lane in wf["lanes"]}
