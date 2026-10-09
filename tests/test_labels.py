"""Labelling pipeline checks that need no API key."""

import json

import numpy as np
import pandas as pd

from rtl.llm.evaluate import ece, macro_f1
from rtl.schemas import LABEL_CLASSES


def test_label_request_shapes():
    from rtl.llm.label import SCHEMA, request

    r = request("Building RWA-0123456789abcdef: test card", "RWA-0123456789abcdef", "haiku:text")
    assert r["model"] == "claude-haiku-4-5" and "thinking" not in r
    assert r["output_config"]["format"]["schema"] is SCHEMA
    assert "=== CODEBOOK ===" in r["system"] and "`religious`" in r["system"]
    o = request("Building RWA-0123456789abcdef: test card", "RWA-0123456789abcdef", "opus:text")
    assert o["output_config"]["effort"] == "low"
    assert set(SCHEMA["properties"]["probs"]["required"]) == set(LABEL_CLASSES)


def test_macro_f1_and_ece_on_known_cases():
    y = pd.Series(["a", "a", "b", "b"])
    assert macro_f1(y, y) == 1.0
    assert macro_f1(y, pd.Series(["b", "b", "a", "a"])) == 0.0
    # perfectly calibrated: confidence 0.5, half correct
    assert ece(pd.Series([0.5] * 4), pd.Series([True, False, True, False])) == 0.0
    # overconfident: confidence 1.0, all wrong
    assert np.isclose(ece(pd.Series([0.999] * 3), pd.Series([False] * 3)), 0.999)


def test_card_bearing_and_fmt():
    from rtl.llm.cards import _bearing, _fmt

    assert _bearing(0, 10) == "N" and _bearing(10, 0) == "E" and _bearing(-10, -10) == "SW"
    assert _fmt(pd.NA) == "unknown" and _fmt(float("nan"), none="-") == "-" and _fmt(3.0) == "3"


def test_sample_is_seeded_and_stratified(tmp_path, monkeypatch):
    from rtl.llm import sample

    rng_pop = np.random.default_rng(0)
    pop = pd.DataFrame({"bldg_id": [f"RWA-{i:016x}" for i in range(4000)],
                        "area_m2": rng_pop.lognormal(3.5, 0.6, 4000),
                        "group": np.repeat(["a", "b"], 2000),
                        "tagged": rng_pop.random(4000) < 0.1})
    pop["size_tercile"] = pop.groupby("group").area_m2.transform(lambda a: pd.qcut(a.rank(method="first"), 3,
                                                                                   labels=False))
    a = sample.stratified(pop[pop.group == "a"], 300, np.random.default_rng(1))
    b = sample.stratified(pop[pop.group == "a"], 300, np.random.default_rng(1))
    assert list(a.bldg_id) == list(b.bldg_id)  # seeded
    assert len(a) == 300 and a.tagged.mean() == 0.25  # tagged over-sampled to 25%
    assert a.groupby("size_tercile").size().between(99, 101).all()


def test_export_parses_cached_response(tmp_path, monkeypatch):
    from rtl.llm import label
    from rtl.llm.cache import Cache, CacheEntry
    from rtl.llm.client import LLMClient

    cache = Cache(tmp_path / "c.duckdb")
    req = label.request("Building RWA-0123456789abcdef: x", "RWA-0123456789abcdef", "sonnet:text")
    probs = {c: 0.0 for c in LABEL_CLASSES} | {"residential": 0.8, "mixed_shop_house": 0.2}
    body = {"label": "residential", "probs": probs, "confidence": "medium", "evidence": "small, no tags",
            "abstain": False}
    resp = {"content": [{"type": "text", "text": json.dumps(body)}], "stop_reason": "end_turn"}
    cache.put(CacheEntry("k1", "claude-sonnet-5-5", label.PROMPT_VERSION, "label:gold:sonnet:text", resp,
                         {"input_tokens": 10, "output_tokens": 5}, 0.001, "end_turn", False), req)
    monkeypatch.setattr(label, "OUT", tmp_path / "labels.parquet")
    df = label.export(LLMClient(api=object(), cache=cache))
    row = df.iloc[0]
    assert row.bldg_id == "RWA-0123456789abcdef" and row.label == "residential"
    assert row.labeler == "claude-sonnet-5-5|text|label_v1" and np.isclose(row.confidence, 0.8)
