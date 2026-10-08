"""The contracts must reject the mistakes they exist to catch."""

import pandas as pd
import pandera.errors as pe
import pytest

from rtl.schemas import LABEL_CLASSES, building_height, labels


def test_height_rejects_out_of_range():
    df = pd.DataFrame({"bldg_id": ["RWA-0123456789abcdef"], "height_m": [250.0], "height_source": ["x"],
                       "first_seen_year": pd.array([2019], dtype="Int64"),
                       "est_floors": pd.array([1], dtype="Int64"), "gfa_m2": [10.0]})
    with pytest.raises(pe.SchemaError):
        building_height.validate(df)


def test_labels_reject_unknown_class_and_bad_id():
    df = pd.DataFrame({"bldg_id": ["not-an-id"], "label_source": ["gold"], "label": ["castle"],
                       "confidence": [1.0], "probs_json": [None], "abstain": [False], "model": [None],
                       "prompt_version": [None], "labeler": ["me"],
                       "labeled_at": pd.to_datetime(["2026-10-10"], utc=True)})
    with pytest.raises(pe.SchemaErrors):
        labels.validate(df, lazy=True)


def test_codebook_has_unknown_class():
    assert "unknown" in LABEL_CLASSES
