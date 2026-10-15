"""A4: official electricity and facility statistics (owner-supplied public reports) -> validation anchors.

    pixi run energy-statistics

Reads, from data/restricted/ (copies supplied by the repo owner; all are public reports):
- NISR Statistical Yearbook 2024: REG customers per branch 2017-2023 (Table 7.2), electricity sold by category
  2021-2023 (Table 7.4), health facilities by type 2017-2023 (Table 3.10.2) and public beds (Table 3.10.3);
- RURA Electricity Statistics Report, Q2 2024: electricity sold by category, Q2 2023 - Q2 2024 (Table 3);
- EICV3 / EICV4 / EICV5 Utilities and Amenities thematic reports: households lighting mainly with grid electricity,
  by district (Table A.6), for 2010/11, 2013/14 and 2016/17.

Writes data/processed/validation_anchors/:
  reg_sales_by_category.csv            period, period_type (year | quarter), category, kwh, doc_id, table
  reg_customers_by_branch.csv          branch (district, or Kigali for its three districts), year, customers
  health_facilities_by_type.csv        facility_type, year, facilities, beds (public facilities only)
  grid_lighting_by_district_history.csv district, year, grid_share (0-1), survey
"""

from __future__ import annotations

import re

import fitz as pymupdf  # PyMuPDF; the conda-forge build only ships the `fitz` name
import pandas as pd

from rtl.settings import DATA_DIR, PROCESSED_DIR

R = DATA_DIR / "restricted"
YEARBOOK = R / "nisr" / "Statistical_Year_Book_2024.pdf"
RURA = R / "rura" / "Electricity_Statistics_Report_as_of_the_Second_Quarter_2024.pdf"
EICV3 = R / "reg" / "EICV3_ThematicReport_Utilities_and_Amenities-compressed.pdf"
EICV5 = R / "reg" / "EICV5_Utilities_and_Amenities_Thematic_Report.xlsx"
OUT = PROCESSED_DIR / "validation_anchors"
NUM = r"(-|[\d][\d,]*(?:\.\d+)?)"
DISTRICTS = ["Nyarugenge", "Gasabo", "Kicukiro", "Nyanza", "Gisagara", "Nyaruguru", "Huye", "Nyamagabe", "Ruhango",
             "Muhanga", "Kamonyi", "Karongi", "Rutsiro", "Rubavu", "Nyabihu", "Ngororero", "Rusizi", "Nyamasheke",
             "Rulindo", "Gakenke", "Musanze", "Burera", "Gicumbi", "Rwamagana", "Nyagatare", "Gatsibo", "Kayonza",
             "Kirehe", "Ngoma", "Bugesera"]


def text(path) -> str:
    return re.sub(r"\s+", " ", "\n".join(p.get_text("text") for p in pymupdf.open(path)))


def num(s: str) -> float | None:
    return None if s == "-" else float(s.replace(",", ""))


def block(t: str, start: str, end: str) -> str:
    """The text of a table: from the LAST occurrence of `start` (the first is usually the table of contents)."""
    i = [m.start() for m in re.finditer(re.escape(start), t)][-1]
    return t[i:t.index(end, i)]


def rows(b: str, labels: list[str], n: int) -> dict[str, list[float | None]]:
    """{label: its next n numbers} for each row label found in a table block."""
    out = {}
    for lab in labels:
        m = re.search(re.escape(lab) + r"[\s:]+" + r"\s+".join([NUM] * n), b)
        if m:
            out[lab] = [num(g) for g in m.groups()]
    return out


def reg_sales(yb: str, rura: str) -> pd.DataFrame:
    cats = ["Residential", "Non-residential", "Water pumping stations", "Water treatment plants", "Broadcasters",
            "Health facilities", "Telecom towers", "Hotels", "Commercial data centers", "Industries", "Street lights",
            "Nasho Own Use", "Exports", "Total"]
    q = rows(block(rura, "Table 3: Trends of electricity sold", "Source: EUCL-REG"), cats, 5)
    quarters = ["2023Q2", "2023Q3", "2023Q4", "2024Q1", "2024Q2"]
    out = [{"period": p, "period_type": "quarter", "category": c.lower(), "kwh": v,
            "doc_id": "rura_electricity_q2_2024", "table": "Table 3"}
           for c, vals in q.items() for p, v in zip(quarters, vals, strict=True)]
    ycats = {"Domestic/Residential": "residential", "Non-Residential": "non-residential",
             "Water Pumping Station": "water pumping stations", "Water Treatment Plant": "water treatment plants",
             "Telecom towers": "telecom towers", "Hotels": "hotels", "a. Small industries (Energy)": "industries small",
             "b. Medium industries (Energy)": "industries medium", "c. Large Industries (Energy)": "industries large",
             "Street Lighting": "street lights", "Health facilities": "health facilities",
             "Data Centers": "commercial data centers"}
    y = rows(block(yb, "Table 7.4: Electricity consumption", "Source: REG"), list(ycats), 3)
    out += [{"period": str(yr), "period_type": "year", "category": ycats[c], "kwh": v,
             "doc_id": "nisr_statistical_yearbook_2024", "table": "Table 7.4"}
            for c, vals in y.items() for yr, v in zip((2021, 2022, 2023), vals, strict=True)]
    return pd.DataFrame(out)


def reg_customers(yb: str) -> pd.DataFrame:
    b = block(yb, "Table 7.2: Electricity Customers", "Source: REG")
    r = rows(b, ["Kigali"] + [d for d in DISTRICTS if d not in ("Nyarugenge", "Gasabo", "Kicukiro")], 7)
    tot = re.search(r"General Total\s+\d+\s+" + r"\s+".join([NUM] * 7), b)
    out = [{"branch": k, "year": y, "customers": v} for k, vals in r.items()
           for y, v in zip(range(2017, 2024), vals, strict=True)]
    out += [{"branch": "Rwanda", "year": y, "customers": num(v)} for y, v in zip(range(2017, 2024), tot.groups(),
                                                                                strict=True)]
    return pd.DataFrame(out)


def health_facilities(yb: str) -> pd.DataFrame:
    types = ["National Referral Hospitals", "Provincial Hospital", "District Hospitals", "Health Centers",
             "Prison Dispensaries", "Health Posts", "Private Dispensaries", "Private Clinics", "Private Hospital"]
    f = rows(block(yb, "Table 3.10.2: Number of Health Facilities", "Table 3.10.3"), types, 7)
    beds = rows(block(yb, "Table 3.10.3: Number of beds", "Source: R-HMIS"),
                ["Health Center", "District Hospital", "Provincial hospital", "Referral Hospitals"], 6)
    bed_of = {"Health Centers": "Health Center", "District Hospitals": "District Hospital",
              "Provincial Hospital": "Provincial hospital", "National Referral Hospitals": "Referral Hospitals"}
    out = []
    for t, vals in f.items():
        for y, v in zip(range(2017, 2024), vals, strict=True):
            b = beds.get(bed_of.get(t, ""), [None] * 6)
            out.append({"facility_type": t, "year": y, "facilities": v,
                        "beds": b[y - 2018] if y >= 2018 and t in bed_of else None})
    return pd.DataFrame(out)


def grid_history() -> pd.DataFrame:
    """Share of households lighting mainly with grid electricity per district: EICV3 (PDF), EICV4 and EICV5 (xlsx)."""
    e3 = rows(block(text(EICV3), "Table A.6 Primary fuel used for lighting, by district", "Source: EICV3"),
              DISTRICTS, 1)
    out = [{"district": d, "year": 2011, "grid_share": v[0] / 100, "survey": "EICV3 2010/11"} for d, v in e3.items()]
    x = pd.read_excel(EICV5, sheet_name="Table-A.6", header=None)
    lab = x[1].astype(str).str.strip()
    starts = {"EICV5": int(lab[lab == "EICV5"].index[0]), "EICV4": int(lab[lab == "EICV4"].index[0])}
    for survey, year in (("EICV5", 2017), ("EICV4", 2014)):
        part = x.iloc[starts[survey]: starts[survey] + 36]
        for _, r in part[part[1].astype(str).str.strip().isin(DISTRICTS)].iterrows():
            out.append({"district": str(r[1]).strip(), "year": year, "grid_share": round(float(r[2]) / 100, 4),
                        "survey": f"{survey} {year - 1}/{str(year)[2:]}"})
    return pd.DataFrame(out)


def main() -> None:
    yb, rura = text(YEARBOOK), text(RURA)
    out = {"reg_sales_by_category": reg_sales(yb, rura), "reg_customers_by_branch": reg_customers(yb),
           "health_facilities_by_type": health_facilities(yb), "grid_lighting_by_district_history": grid_history()}
    checks = {"reg_customers_by_branch": 28 * 7 + 7, "health_facilities_by_type": 9 * 7,
              "grid_lighting_by_district_history": 90}
    for name, df in out.items():
        if name in checks and len(df) != checks[name]:
            raise ValueError(f"{name}: expected {checks[name]} rows, parsed {len(df)}")
        df.to_csv(OUT / f"{name}.csv", index=False)
        print(f"{name}: {len(df)} rows")
    s = out["reg_sales_by_category"]
    print(s[s.period_type == "year"].pivot_table(index="category", columns="period", values="kwh").div(1e6).round(1))


if __name__ == "__main__":
    main()
