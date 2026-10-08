"""A4: download the evidence corpus (config/evidence_docs.yaml) through rtl.manifest.fetch.

Each verified doc lands in data/raw/evidence_docs/<version>/<doc_id>.pdf and in data/manifest.json.
A download that isn't a real PDF (an HTML error page, a login wall) is a silent poison for extraction,
so every file is checked for the %PDF magic bytes and opened with PyMuPDF; failures are reported, not hidden.

    pixi run fetch-evidence-docs
    pixi run anchors                 # validation anchors transcribed from the downloaded reports (no LLM)
"""

from __future__ import annotations

import re

import fitz as pymupdf  # PyMuPDF; the conda-forge build (1.24) only ships the `fitz` name
import pandas as pd

from rtl.llm.evidence import doc_path, normalise_text, page_texts, verify_quote
from rtl.manifest import fetch
from rtl.settings import PROCESSED_DIR, load_yaml


def main() -> None:
    docs = load_yaml("evidence_docs.yaml")["documents"]
    total_pages, bad = 0, []
    for d in docs:
        if d["status"] != "verified":
            print(f"[{d['doc_id']}] skipped (status: {d['status']})")
            continue
        try:
            path = fetch("evidence_docs", url=d["url"], filename=f"{d['doc_id']}.pdf")
            if path.read_bytes()[:5] != b"%PDF-":
                raise ValueError("not a PDF")
            with pymupdf.open(path) as pdf:
                total_pages += pdf.page_count
                print(f"[{d['doc_id']}] {pdf.page_count} pages")
        except Exception as e:  # report and continue: one dead link shouldn't stop the corpus
            bad.append(d["doc_id"])
            print(f"[{d['doc_id']}] FAILED: {e}")
    n_ok = sum(d["status"] == "verified" for d in docs) - len(bad)
    print(f"corpus: {n_ok} docs ok, {total_pages} pages; failed: {bad or 'none'}")


if __name__ == "__main__" and "--anchors" not in __import__("sys").argv:
    main()


# =========================================================================== validation anchors (no LLM)
# Anchors are numbers transcribed from report tables, used only to VALIDATE model outputs (never features).
# Tables are parsed from PyMuPDF text and reconciled (district sums = national total), and hand-transcribed
# numbers carry their quote, which must be found verbatim on the cited page (rtl.llm.evidence.verify_quote).

DISTRICTS = [
    "Nyarugenge", "Gasabo", "Kicukiro", "Nyanza", "Gisagara", "Nyaruguru", "Huye", "Nyamagabe", "Ruhango",
    "Muhanga", "Kamonyi", "Karongi", "Rutsiro", "Rubavu", "Nyabihu", "Ngororero", "Rusizi", "Nyamasheke",
    "Rulindo", "Gakenke", "Musanze", "Burera", "Gicumbi", "Rwamagana", "Nyagatare", "Gatsibo", "Kayonza",
    "Kirehe", "Ngoma", "Bugesera",
]

# (doc_id, page, table, parameter, value, unit, population, quote) — transcribed by hand, verified in code.
TRANSCRIBED = {
    "residential_kwh": [
        ("qsel_consumption_trends_2024", 5, "text", "monthly_kwh.mean", 45, "kWh/month",
         "REG residential customers, Jan 2013", "In January 2013, the average residential customer consumed about 45 kWh"),  # noqa: E501 (verbatim quote)
        ("qsel_consumption_trends_2024", 5, "text", "monthly_kwh.mean", 22, "kWh/month",
         "REG residential customers, Dec 2019",
         "By December 2019, the average residential customer's consumption had decreased by more than half to about 22 kWh"),  # noqa: E501 (verbatim quote)
        ("qsel_consumption_trends_2024", 5, "text", "monthly_kwh.mean", 170, "kWh/month",
         "REG non-residential customers, Jan 2013", "the average non-residential customer consumed about 170 kWh"),
        ("qsel_consumption_trends_2024", 5, "text", "annual_kwh.mean", 71, "kWh/year",
         "REG residential customers outside Kigali", "a residential customer outside Kigali consumes 71 kWh annually"),
        ("qsel_consumption_trends_2024", 5, "text", "annual_kwh.mean", 297, "kWh/year",
         "REG residential customers in Kigali", "compared to the 297 kWh consumed by an average residential customer in Kigali"),  # noqa: E501 (verbatim quote)
        ("qsel_consumption_trends_2024", 5, "text", "monthly_kwh.median_plateau", 10, "kWh/month",
         "REG residential customers connected in 2013 (median, plateau)",
         "the median residential Rwandan customer electrified in the same year plateaued at around 10 kWh/month"),
        ("qsel_consumption_trends_2024", 9, "text", "monthly_kwh.median_upper_bound", 5, "kWh/month",
         "lowest-tier residential customers", "consistently below 5 kWh per month"),
        ("qsel_grid_reliability_2025", 15, "text", "monthly_kwh.median", 6.3, "kWh/month",
         "grid-connected survey households", "the median household consumes approximately 6.3 kWh per month"),
        ("qsel_grid_reliability_2025", 15, "text", "monthly_kwh.mean", 15.61, "kWh/month",
         "grid-connected survey households", "consumption 15.61 kWh for the average household"),
        ("qsel_grid_reliability_2025", 13, "text", "monthly_kwh.tv", 5.2, "kWh/month",
         "households using a television", "households consume an average of 5.2 kWh per month from television use"),
        ("qsel_grid_reliability_2025", 13, "text", "monthly_kwh.fridge", 19.58, "kWh/month",
         "households with a fridge", "19.58 kWh on fridges"),
        ("qsel_rural_adoption_2025", 2, "text", "monthly_kwh.mean", 8.1, "kWh/month",
         "rural connected households, up to 10 years after electrification",
         "the average connected household consumes 8.1 kWh per month, and the median is 4 kWh"),
        ("qsel_rural_adoption_2025", 2, "text", "monthly_kwh.median", 4, "kWh/month",
         "rural connected households, up to 10 years after electrification", "and the median is 4 kWh"),
    ],
    "tariffs": [
        ("reg_tariffs_2025", 1, "A. Tariffs for all customer categories", "tariff_rwf_per_kwh.residential.block_0_20",
         89, "RWF/kWh (excl. VAT and regulatory fee)", "residential, 0-20 kWh/month", "[ 0-20] 89"),
        ("reg_tariffs_2025", 1, "A. Tariffs for all customer categories", "tariff_rwf_per_kwh.residential.block_20_50",
         310, "RWF/kWh (excl. VAT and regulatory fee)", "residential, >20-50 kWh/month", "[>20 - 50] 310"),
        ("reg_tariffs_2025", 1, "A. Tariffs for all customer categories", "tariff_rwf_per_kwh.residential.block_over_50",  # noqa: E501 (verbatim quote)
         369, "RWF/kWh (excl. VAT and regulatory fee)", "residential, >50 kWh/month", ">50 369"),
        ("reg_tariffs_2025", 1, "A. Tariffs for all customer categories", "tariff_rwf_per_kwh.non_residential.block_0_100",  # noqa: E501 (verbatim quote)
         355, "RWF/kWh (excl. VAT and regulatory fee)", "non-residential, 0-100 kWh/month", "[0-100] 355"),
        ("reg_tariffs_2025", 1, "A. Tariffs for all customer categories", "tariff_rwf_per_kwh.non_residential.block_over_100",  # noqa: E501 (verbatim quote)
         376, "RWF/kWh (excl. VAT and regulatory fee)", "non-residential, >100 kWh/month", ">100 376"),
        ("reg_tariffs_2025", 1, "A. Tariffs for all customer categories", "tariff_rwf_per_kwh.small_industry",
         175, "RWF/kWh (excl. VAT and regulatory fee)", "manufacturing plants 5,000-100,000 kWh/year",
         "consumption between 5,000 and 100,000 kWh All consumed kWh 175"),
    ],
}


def _numbers(s: str) -> list[float]:
    return [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*\.?\d*", s)]


def households_by_district(texts: list[str]) -> pd.DataFrame:
    """RPHC5 Table 58 (private households by district and residence), reconciled to the national total."""
    page = next(i for i, t in enumerate(texts, 1)
                if "Table 58: Private households (Number)" in t and "Nyarugenge" in t)  # skip the list of tables
    flat = normalise_text(texts[page - 1] + " " + texts[page])
    rows = []
    for d in DISTRICTS:
        total, urban, rural = _numbers(re.search(rf"\b{d}\b\s+([\d,]+\s+[\d,]+\s+[\d,]+)", flat).group(1))
        assert urban + rural == total, d
        rows.append({"district": d, "households_total": int(total), "households_urban": int(urban),
                     "households_rural": int(rural)})
    df = pd.DataFrame(rows)
    national = _numbers(re.search(r"Rwanda\s+([\d,]+\s+[\d,]+\s+[\d,]+)", flat).group(1))
    assert df[["households_total", "households_urban", "households_rural"]].sum().tolist() == national
    return df.assign(year=2022, doc_id="rphc5_main_indicators", page=page,
                     table="Table 58: Private households (Number) by province, district and residence")


def grid_lighting_by_district(texts: list[str]) -> pd.DataFrame:
    """EICV7 Table A.8, first column (electricity distributors = grid as MAIN lighting source).

    Only the first column and the household count are parsed: empty cells are dropped in the PDF text, so
    later columns (solar, lanterns, ...) can't be aligned reliably and are left out on purpose."""
    page = next(i for i, t in enumerate(texts, 1) if "Table A.8" in t and "EICV7" in t and "Nyarugenge" in t)
    flat = normalise_text(texts[page - 1])
    rows = []
    for i, d in enumerate(DISTRICTS):
        nxt = DISTRICTS[i + 1] if i + 1 < len(DISTRICTS) else "EICV5|Source|Note"
        seg = re.search(rf"\b{d}\b(.*?)(?:\b(?:{nxt})\b|$)", flat).group(1)
        nums = _numbers(seg)
        k = nums.index(100)  # row total (100%) is followed by the household count in thousands
        rows.append({"district": d, "grid_main_lighting_share": nums[0] / 100, "households_thousands": nums[k + 1]})
    df = pd.DataFrame(rows)
    national = _numbers(re.search(r"All Rwanda\s+([\d.\s,]+)", flat).group(1))[0] / 100
    weighted = (df.grid_main_lighting_share * df.households_thousands).sum() / df.households_thousands.sum()
    assert abs(weighted - national) < 0.01, (weighted, national)  # parsed rows reproduce the national 50.0%
    return df.assign(year=2024, doc_id="eicv7_utilities_amenities", page=page,
                     table="Table A.8: households by primary fuel used for lighting, by district (EICV7)")


def transcribed(name: str) -> pd.DataFrame:
    rows = []
    for doc_id, page, table, param, value, unit, population, quote in TRANSCRIBED[name]:
        texts = page_texts(doc_path(doc_id))
        rows.append({"parameter": param, "value": value, "unit": unit, "population": population,
                     "geography": "Rwanda", "doc_id": doc_id, "page": page, "table": table, "quote": quote,
                     "quote_verified": verify_quote(quote, texts, page)})
    return pd.DataFrame(rows)


def build_anchors() -> None:
    out = PROCESSED_DIR / "validation_anchors"
    out.mkdir(parents=True, exist_ok=True)
    tables = {
        "households_by_district_2022.csv": households_by_district(page_texts(doc_path("rphc5_main_indicators"))),
        "grid_lighting_by_district_2024.csv": grid_lighting_by_district(
            page_texts(doc_path("eicv7_utilities_amenities"))),
        "residential_kwh_qsel.csv": transcribed("residential_kwh"),
        "tariffs_2025.csv": transcribed("tariffs"),
    }
    for name, df in tables.items():
        df.assign(role="validation").to_csv(out / name, index=False)
        bad = int((~df["quote_verified"]).sum()) if "quote_verified" in df else 0
        print(f"{name}: {len(df)} rows" + (f", {bad} quotes NOT verified" if bad else ""))


if __name__ == "__main__" and "--anchors" in __import__("sys").argv:
    build_anchors()
