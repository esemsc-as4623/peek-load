"""Evidence extraction: quote verification, page mapping, prompt versioning, and a fake end-to-end run."""

import hashlib
import json
from types import SimpleNamespace

import fitz as pymupdf  # PyMuPDF; the conda-forge build (1.24) only ships the `fitz` name
import pytest
from anthropic.types import Message

from rtl.llm import evidence as ev
from rtl.llm.cache import Cache
from rtl.llm.client import LLMClient

PAGES = [
    "Chapter 1 Introduction\nThis survey covers fertility and health.",
    "Table 2.4 Household durable goods\nPercentage of households possessing a television: urban 32.7, rural 7.2.\n"
    "Households with electri-\ncity use it mainly for lighting.",
    "Electricity consumption of residential customers averaged 21 kWh per month in 2019 for the grid "
    "connection cohort of 2013.",
]


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "doc.pdf"
    with pymupdf.open() as d:
        for t in PAGES:
            d.new_page().insert_textbox(pymupdf.Rect(50, 50, 550, 800), t, fontsize=10)
        d.save(path)
    return path


# ------------------------------------------------------------------ quote verification (planted cases)
def test_true_quotes_verify(pdf):
    texts = ev.page_texts(pdf)
    assert ev.verify_quote("possessing a television: urban 32.7, rural 7.2.", texts, 2)
    assert ev.verify_quote("Households with electricity use it mainly", texts, 2)  # hyphenation across lines
    assert ev.verify_quote("averaged  21 kWh\nper month", texts, 3)  # whitespace differences
    assert ev.verify_quote("possessing a television: urban 32.7, rural 7.2.", texts, 2)


def test_false_quotes_fail(pdf):
    texts = ev.page_texts(pdf)
    assert not ev.verify_quote("possessing a television: urban 37.2, rural 7.2.", texts, 2)  # altered number
    assert not ev.verify_quote("averaged 21 kWh per month", texts, 2)  # right text, wrong page
    assert not ev.verify_quote("averaged 21 kWh per month", texts, None)
    assert not ev.verify_quote("", texts, 2)
    assert not ev.verify_quote("anything", texts, 99)


def test_normalise_text_handles_pdf_artifacts():
    assert ev.normalise_text("eﬃcient – “TV”  \n set") == 'efficient - "TV" set'


def test_select_pages_and_subpdf_mapping(pdf):
    texts = ev.page_texts(pdf)
    pages = ev.select_pages(texts)
    assert pages == [2, 3]  # page 1 has no electricity keywords
    sub = pymupdf.open(stream=ev.sub_pdf(pdf, pages), filetype="pdf")
    assert sub.page_count == 2 and "television" in sub[0].get_text()
    assert ev.chunked(list(range(1, 95)), 40)[-1] == list(range(81, 95))


# ------------------------------------------------------------------ prompts are versioned
def test_prompt_files_match_version_lock():
    """Editing a prompt without bumping its version (new file + versions.json entry) must fail this test."""
    lock = json.loads((ev.PROMPT_DIR / "versions.json").read_text())
    for name, sha in lock.items():
        assert hashlib.sha256((ev.PROMPT_DIR / name).read_bytes()).hexdigest() == sha, f"{name} changed"
    assert f"{ev.PROMPT_VERSION}_extract.md" in lock and f"{ev.PROMPT_VERSION}_normalise.md" in lock


# ------------------------------------------------------------------ fake end-to-end
def _msg(content, stop_reason="end_turn"):
    return Message.model_validate({"id": "m", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                                   "content": content, "stop_reason": stop_reason, "stop_sequence": None,
                                   "usage": {"input_tokens": 100, "output_tokens": 50}})


class FakeMessages:
    """Step (a): two cited statements (sub-PDF pages 1 and 2 = original pages 2 and 3).
    Step (b) (has output_config.format): three rows, the third citing a fabricated quote."""

    def create(self, **req):
        if "format" in req["output_config"]:
            rows = [
                {"parameter": "ownership_share.tv.urban_households", "value": 0.327, "value_low": None,
                 "value_high": None, "unit": "fraction", "population": "urban households", "geography": "Rwanda",
                 "year": 2025, "claim_index": 0, "notes": None},
                {"parameter": "monthly_kwh.mean.residential_customers_2013_cohort", "value": 21.0,
                 "value_low": None, "value_high": None, "unit": "kWh/month", "population": "residential",
                 "geography": "Rwanda", "year": 2019, "claim_index": 1, "notes": None},
                {"parameter": "ownership_share.fridge.urban_households", "value": 0.5, "value_low": None,
                 "value_high": None, "unit": "fraction", "population": None, "geography": None,
                 "year": 2025, "claim_index": 2, "notes": None}]
            return _msg([{"type": "text", "text": json.dumps({"rows": rows})}])

        def cit(text, page):
            return {"type": "page_location", "cited_text": text, "document_index": 0, "document_title": "t",
                    "start_page_number": page, "end_page_number": page + 1}
        return _msg([
            {"type": "text", "text": "32.7% of urban households own a TV.",
             "citations": [cit("Percentage of households possessing a television: urban 32.7, rural 7.2.", 1)]},
            {"type": "text", "text": "Residential customers used 21 kWh/month.",
             "citations": [cit("averaged 21 kWh per month in 2019", 2)]},
            {"type": "text", "text": "Half of urban households own a fridge.",
             "citations": [cit("50% of urban households own a refrigerator", 2)]}])


def test_end_to_end_with_fake_client(pdf, tmp_path, monkeypatch):
    doc = {"doc_id": "fake_doc", "title": "Fake", "publisher": "Test", "year": 2025}
    monkeypatch.setattr(ev, "catalog", lambda: {"fake_doc": doc})
    monkeypatch.setattr(ev, "doc_path", lambda d: pdf)
    monkeypatch.setattr(ev, "INTERIM_DIR", tmp_path)
    client = LLMClient(api=SimpleNamespace(messages=FakeMessages()), cache=Cache(tmp_path / "c.duckdb"))
    df = ev.extract_docs(["fake_doc"], client)

    assert len(df) == 3 and df.evidence_id.is_unique
    tv = df.set_index("parameter").loc["ownership_share.tv.urban_households"]
    assert tv.page == 2 and tv.quote_verified and not tv.human_verified  # page mapped back from sub-PDF
    assert df.set_index("parameter").loc["monthly_kwh.mean.residential_customers_2013_cohort"].page == 3
    fake = df.set_index("parameter").loc["ownership_share.fridge.urban_households"]
    assert not fake.quote_verified  # fabricated quote caught
    assert set(df.prompt_version) == {ev.PROMPT_VERSION}
    # a second run is served entirely from cache: same rows, no extra spend
    n_calls = len(client.cache.ledger())
    assert ev.extract_docs(["fake_doc"], client).equals(df)
    assert len(client.cache.ledger()) == n_calls


def test_dry_run_estimate_is_positive_and_cheaper_in_batch(pdf):
    doc = {"doc_id": "fake_doc", "title": "Fake", "publisher": "Test", "year": 2025}
    e, b = ev.estimate(doc, pdf), ev.estimate(doc, pdf, batch=True)
    assert e["pages"] == 3 and e["pages_sent"] == 2 and e["requests"] == 2
    assert e["input_tokens"] > 2 * ev.IMAGE_TOKENS_PER_PAGE
    assert b["est_usd"] == pytest.approx(e["est_usd"] / 2, abs=0.002)
