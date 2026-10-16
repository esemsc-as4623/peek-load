"""Planted cases for the census setting, wealth split, grid calibration, school-registry parsing, Black Marble tiles
and the back-cast growth fit."""

import numpy as np
import pandas as pd

from rtl.demand.allocate import census_setting, wealth_upper
from rtl.demand.calibrate import shift_to_mean
from rtl.demand.years import slope
from rtl.ingest.blackmarble import tile_origin
from rtl.ingest.schools import annex_table


def test_census_setting_puts_densest_slots_urban_up_to_the_census_share():
    df = pd.DataFrame({"district": ["A"] * 4 + ["B"] * 2, "pop_density_per_km2": [9000, 100, 5000, 50, 10, 20]})
    slots = pd.Series([1.0, 1.0, 1.0, 1.0, 2.0, 2.0])
    s = census_setting(df, slots, pd.Series({"A": 0.5, "B": 0.0}))
    assert list(s) == ["urban", "rural", "urban", "rural", "rural", "rural"]


def test_wealth_upper_takes_richest_homes_until_target_share():
    df = pd.DataFrame({"district": ["A"] * 4, "rwi": [0.9, -0.5, 0.2, 0.1], "area_m2": [50.0] * 4})
    hh = pd.Series([1, 1, 2, 0])
    target = pd.DataFrame({"district": ["A"], "setting": ["rural"], "target_upper_share": [0.25]})
    up = wealth_upper(df, hh, pd.Series(["rural"] * 4), target)
    assert list(up) == [True, False, False, False]  # 1 of 4 households; the empty building is never upper


def test_shift_to_mean_hits_the_target_share():
    p = np.array([0.1, 0.5, 0.9, 0.3])
    w = np.array([1.0, 2.0, 1.0, 4.0])
    s = shift_to_mean(p, w, 0.62)
    z = np.log(p / (1 - p)) + s
    assert abs(np.average(1 / (1 + np.exp(-z)), weights=w) - 0.62) < 1e-6


def test_annex_table_reads_the_last_column_and_skips_totals():
    text = ("Annex 3: Number of Primary schools by district ..... 100\nother\n"
            "Annex 3: Number of Primary schools by district\nDistrict/Province 2019 2020/21\n"
            "Gisagara 64 95\nHuye 100 109\nSouth 754 900\nKigali city 247 323\nRwanda 2,961 3,691\nSource: x")
    assert annex_table(text, "Annex 3: Number of Primary schools by district") == {"Gisagara": 95, "Huye": 109}


def test_black_marble_tiles_cover_rwanda():
    assert tile_origin("h20v09") == (20, 0) and tile_origin("h21v09") == (30, 0)  # Rwanda: 28.9-30.9 E, 1-2.9 S


def test_slope_recovers_log_growth():
    years = np.arange(2016, 2024)
    assert abs(slope(years, 100 * np.exp(0.03 * (years - 2016))) - 0.03) < 1e-9


def test_statistics_rows_take_the_table_not_the_contents_and_read_dashes_as_missing():
    from rtl.ingest.energy_statistics import block, rows

    t = ("Table 7.4: Electricity consumption ... 88 Other text Table 7.4: Electricity consumption (Kwh), 2021-2023 "
         "Category 2021 2022 2023 Hotels 31,007,535.09 45,367,013.86 51,329,016.27 Health facilities "
         "17,313,952.23 - 22,654,693.01 Source: REG")
    r = rows(block(t, "Table 7.4: Electricity consumption", "Source: REG"), ["Hotels", "Health facilities"], 3)
    assert r["Hotels"] == [31007535.09, 45367013.86, 51329016.27]
    assert r["Health facilities"] == [17313952.23, None, 22654693.01]


def test_hospital_curation_merges_units_aliases_and_fills_to_the_count():
    import pandas as pd

    from rtl.ingest.hospitals import curate

    cfg = {"counts_2023": {"hospitals": 3}, "hospitals": {
        "unit_patterns": "laboratory", "clinic_patterns": r"\beye\b", "private_patterns": "omega|legacy",
        "merge_m": 400, "aliases": [["Ruhengeri Referral Hospital", "Musanze Hospital"]], "compound_m": 150}}
    f = pd.DataFrame({"facility_id": list("abcdefg"),
                      "name": ["Ruhengeri Referral Hospital", "Musanze Hospital", "Kibogora Hospital",
                               "Kibogora Hospital Laboratory", "Kabgayi Eye Hospital", "Omega Hospital",
                               "Legacy Hospital"],
                      "lat": [-1.50, -1.97, -2.30, -2.3001, -2.1, -1.95, -1.96],
                      "lon": [29.63, 30.16, 29.20, 29.2001, 29.75, 30.06, 30.10]})
    out = curate(f, cfg, pd.Series({"f": 9000.0, "g": 5000.0})).set_index("facility_id")
    assert out.at["b", "canonical_id"] == "a"            # misplaced alias joins the first name's hospital
    assert out.at["d", "role"] == "unit" and out.at["d", "canonical_id"] == "c"
    assert out.at["e", "role"] == "clinic"
    assert out.at["f", "role"] == "hospital" and out.at["g", "role"] == "clinic"   # one private place left: largest
    assert out[out.role == "hospital"].canonical_id.nunique() == 3
