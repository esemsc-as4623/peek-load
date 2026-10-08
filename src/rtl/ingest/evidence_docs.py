"""A4: download the evidence corpus (config/evidence_docs.yaml) through rtl.manifest.fetch.

Each verified doc lands in data/raw/evidence_docs/<version>/<doc_id>.pdf and in data/manifest.json.
A download that isn't a real PDF (an HTML error page, a login wall) is a silent poison for extraction,
so every file is checked for the %PDF magic bytes and opened with PyMuPDF; failures are reported, not hidden.

    pixi run fetch-evidence-docs
"""

from __future__ import annotations

import fitz as pymupdf  # PyMuPDF; the conda-forge build (1.24) only ships the `fitz` name

from rtl.manifest import fetch
from rtl.settings import load_yaml


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


if __name__ == "__main__":
    main()
