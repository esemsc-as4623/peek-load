"""Hand-curated evidence from productive-use reports (read in the coding session, not via the Claude API).

    pixi run curate-evidence

Each entry names its document, page and an exact quote; the script checks the quote appears verbatim on that page
(the same check as the API-extracted evidence) and writes rows in the `evidence` contract to
data/processed/evidence_curated.parquet (+ .csv for review). human_verified stays False until the repo owner signs off.

GIZ catalogue values are for efficient **DC solar** products; grid-connected AC equivalents usually draw more.
The Power Africa Rwanda catalogue (2020) lists off-grid products sold in Rwanda: its mills are small solar AC mills
(1.3-1.8 kW), the low end of what grid-connected mills draw (7.5-17.5 kW in the other sources).
"""

from __future__ import annotations

from pathlib import Path

from rtl.llm.evidence import evidence_id, page_texts, to_frame, verify_quote
from rtl.settings import PROCESSED_DIR, RAW_DIR

OUT = PROCESSED_DIR / "evidence_curated.parquet"
DOCS = {"nrel_pue_microgrids_2018": "2018-08-15", "giz_pv_productive_use_catalogue_2016": "2016-06-20",
        "power_africa_pue_catalog_rwanda_2020": "2026-10-11"}
NREL = dict(doc_id="nrel_pue_microgrids_2018", geography="Tanzania (PowerGen micro-grids)", year=2018,
            population="micro-grid productive-use customer")
GIZ = dict(doc_id="giz_pv_productive_use_catalogue_2016", geography="global (product catalogue)", year=2016,
           population="off-grid micro-enterprise, DC solar product")
PUE_RW = dict(doc_id="power_africa_pue_catalog_rwanda_2020", geography="Rwanda (product catalogue)", year=2020,
              population="off-grid productive-use product sold in Rwanda (solar powered)")

# (parameter, value, low, high, unit, page, quote, extra fields)
ENTRIES = [
    ("rated_power_w.grain_mill.microgrid_mill", 10000, None, None, "W", 39,
     "Size of equivalent electric motor 10 kW", NREL),
    ("other.daily_kwh.grain_mill", 20, None, None, "kWh/day", 39, "Amount of power consumed per day 20 kWh", NREL),
    ("hours_per_day.grain_mill.microgrid_mill", 2.6, 2.6, 4, "h/day", 6,
     "at 33% loading (2.6 hours per day) or 50% loading (4 hours per day)", NREL),
    ("hours_per_day.grain_mill.observed_e4i", 2, None, None, "h/day", 46,
     "E4I data indicate that mills operate for approximately 2 hours per day", NREL),
    ("usage_window.grain_mill.weekdays", None, 9, 17, "hour of day", 6,
     "on weekdays between 9:00 AM and 5:00 PM", NREL),
    ("rated_power_w.egg_incubator.100_eggs", 100, None, None, "W", 41, "Power rating of incubator 100 W", NREL),
    ("hours_per_day.egg_incubator.100_eggs", 24, None, None, "h/day", 41, "Operational hours 24 hours/day", NREL),
    ("rated_power_w.ice_freezer.90_l", 180, None, None, "W", 35, "Power rating of freezer 180 W", NREL),
    ("hours_per_day.ice_freezer.compressor", 2, None, None, "h/day", 35, "compressor cycle period is two hours", NREL),
    ("hours_per_day.ice_freezer.operation", 8, None, None, "h/day", 35, "Freezer operates for eight hours", NREL),
    ("monthly_kwh.carpentry_tools.lathe_workshop", 18, None, None, "kWh/month", 40,
     "Energy consumption 18 kWh/month", NREL),
    ("rated_power_w.led_bulb.household", 7.5, 5, 10, "W", 32, "several 5–10 W LED light bulbs", NREL),
    ("rated_power_w.phone_charger.household", 8, None, None, "W", 32, "an 8 W cell phone charger", NREL),
    ("other.peak_w.small_household", 25, None, None, "W", 32, "a peak of around 25 W", NREL),
    ("rated_power_w.sewing_machine.dc", 40, None, None, "W", 144, "Sewing machine Load 40 W", GIZ),
    ("rated_power_w.sewing_machine.dc", 30, None, None, "W", 146, "Sewing machine Load 30 W", GIZ),
    ("rated_power_w.sewing_machine.dc", 60, None, None, "W", 147, "Sewing machine Load 60 W", GIZ),
    ("hours_per_day.sewing_machine.dc", 8, None, None, "h/day", 147, "Working time 8 h", GIZ),
    ("rated_power_w.egg_incubator.dc", 80, None, None, "W", 36, "Egg incubator Load 80 W", GIZ),
    ("rated_power_w.freezer.dc", 40, None, None, "W", 96, "Nominal power 40 W", GIZ),
    ("rated_power_w.refrigerator.dc", 45, None, None, "W", 109, "Nominal power 45 W", GIZ),
    ("rated_power_w.tv.dc", 5.5, None, None, "W", 187, "TV Load 5.5 W", GIZ),
    ("rated_power_w.tv.dc", 20, None, None, "W", 192, "TV Load 20 W", GIZ),
    ("hours_per_day.tv.dc", 6, None, None, "h/day", 193, "Working hours per day 6 h", GIZ),
    ("rated_power_w.hair_dryer.dc", 400, None, None, "W", 227, "Hair dryer Load 400 W", GIZ),
    ("rated_power_w.hair_dryer.dc", 180, None, None, "W", 228, "Hair dryer Load 180 W", GIZ),
    ("rated_power_w.hammer_mill.agsol_v1_3", 1300, None, None, "W", 29, "Power rating 1,300 W 1,800 W", PUE_RW),
    ("rated_power_w.hammer_mill.agsol_v1_8", 1800, None, None, "W", 29, "Power rating 1,300 W 1,800 W", PUE_RW),
    ("other.throughput_kg_per_h.hammer_mill_agsol", 60, 60, 80, "kg/h", 29, "Throughput 60 kg/h 80 kg/h", PUE_RW),
    ("rated_power_w.rice_mill_maize_huller.agsol_v1_3", 1300, None, None, "W", 33, "Power rating 1,300 W 1,800 W",
     PUE_RW),
    ("rated_power_w.rice_mill_maize_huller.agsol_v1_8", 1800, None, None, "W", 33, "Power rating 1,300 W 1,800 W",
     PUE_RW),
    ("rated_power_w.chaff_cutter.agsol_v1_3", 1300, None, None, "W", 27, "Power rating 1300 W 1800 W", PUE_RW),
    ("rated_power_w.chaff_cutter.agsol_v1_8", 1800, None, None, "W", 27, "Power rating 1300 W 1800 W", PUE_RW),
    ("rated_power_w.maize_thresher.agsol", 500, None, None, "W", 31, "Power rating 500 W 500 W", PUE_RW),
    ("rated_power_w.refrigerator.dc_166_l", 70, None, None, "W", 40, "Power (energy consumption) 70 W 100 W", PUE_RW),
    ("rated_power_w.refrigerator.dc_240_l", 100, None, None, "W", 40, "Power (energy consumption) 70 W 100 W", PUE_RW),
    ("rated_power_w.water_pump.futurepump_sf2", 100, 80, 120, "W", 59, "Power rating 80–120 W", PUE_RW),
]


def pdf_path(doc_id: str) -> Path:
    return RAW_DIR / "evidence_docs" / DOCS[doc_id] / f"{doc_id}.pdf"


def main() -> None:
    texts = {d: page_texts(pdf_path(d)) for d in DOCS}
    rows, failed = [], []
    for param, value, low, high, unit, page, quote, extra in ENTRIES:
        ok = verify_quote(quote, texts[extra["doc_id"]], page)
        if not ok:
            failed.append((param, page, quote))
        row = {"parameter": param, "value": value, "value_low": low, "value_high": high, "unit": unit,
               "population": extra["population"], "geography": extra["geography"], "year": extra["year"]}
        rows.append({**row, "evidence_id": evidence_id(extra["doc_id"], row, quote, page), "doc_id": extra["doc_id"],
                     "page": page, "quote": quote, "quote_verified": ok, "model": None,
                     "prompt_version": "curated_v1", "human_verified": False,
                     "notes": "curated in the coding session"
                              + ("; DC solar product, AC grid equivalents usually draw more"
                                 if extra is GIZ else "")
                              + ("; off-grid solar product" if extra is PUE_RW else "")})
    df = to_frame(rows)
    df.to_parquet(OUT, index=False)
    df.to_csv(OUT.with_suffix(".csv"), index=False)
    print(f"{len(df)} curated rows, {df.quote_verified.sum()} quotes verified verbatim -> {OUT}")
    for f in failed:
        print("NOT FOUND:", f)


if __name__ == "__main__":
    main()
