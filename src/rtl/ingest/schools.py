"""A4r: schools per district from the MINEDUC Education Statistical Yearbook 2020/21 (school census).

    pixi run fetch-schools

Parses Annex 3 (primary), Annex 4 (general secondary) and Annex 5 (TVET L1-5): number of schools per district,
2020/21 column. Nursery schools (Annex 2) are left out: most share a campus with a primary school.
Output: data/processed/registries/schools_by_district_2021.csv (district, primary, secondary, tvet, schools).
"""

from __future__ import annotations

import re

import fitz as pymupdf  # PyMuPDF; the conda-forge build only ships the `fitz` name
import pandas as pd

from rtl.manifest import fetch
from rtl.settings import PROCESSED_DIR

OUT = PROCESSED_DIR / "registries" / "schools_by_district_2021.csv"
ANNEXES = {"primary": "Annex 3: Number of Primary schools by district",
           "secondary": "Annex 4: Number of general secondary schools by district",
           "tvet": "Annex 5: Number of TVET L1 to 5 schools by district"}
NOT_DISTRICTS = {"South", "West", "East", "North", "Kigali City", "Kigali city", "Rwanda", "Province", "District"}


def annex_table(text: str, title: str) -> dict[str, int]:
    """{district: 2020/21 count} from the annex page whose text starts with `title` (the table of contents also
    lists the title, followed by a page number, so the page that holds 'Source:' after it is the table)."""
    start = [m.start() for m in re.finditer(re.escape(title), text)][-1]
    block = text[start:text.index("Source:", start)]
    rows = re.findall(r"([A-Z][a-z]+(?: [A-Za-z]+)?)\s+([\d,]+)\s+([\d,]+)", block)
    return {name: int(b.replace(",", "")) for name, _a, b in rows if name not in NOT_DISTRICTS}


def main() -> None:
    pdf = fetch("mineduc_yearbook_2021")
    text = "\n".join(page.get_text("text") for page in pymupdf.open(pdf))
    df = pd.DataFrame({k: annex_table(text, t) for k, t in ANNEXES.items()}).fillna(0).astype(int)
    if len(df) != 30:
        raise ValueError(f"expected 30 districts, parsed {len(df)}: {sorted(df.index)}")
    df["schools"] = df.sum(axis=1)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.rename_axis("district").reset_index().to_csv(OUT, index=False)
    print(f"{df.schools.sum():,} schools (primary {df.primary.sum():,}, secondary {df.secondary.sum():,}, "
          f"TVET {df.tvet.sum():,}) -> {OUT}")


if __name__ == "__main__":
    main()
