"""A4: extract RAMP-relevant parameters from the evidence corpus with Claude, with verifiable quotes.

Two steps per document, because citations and structured outputs can't share one request:
  (a) extract  — the selected PDF pages go in as a citations-enabled document; Claude answers in short
                 statements, each backed by API citations (cited_text + page number, produced by the API
                 from the document itself, not retyped by the model).
  (b) normalise — the statements (no PDF) go into a structured-output request that returns rows of the
                 `evidence` contract. Each row points back to one statement by claim_index, so the quote and
                 page always come from step (a)'s citation, never from the model's free text.
Then every quote is checked against pymupdf's text of the cited page (`verify_quote`) -> quote_verified.
human_verified is always False here: a person signs parameters off before they enter an archetype.

To keep cost proportional to relevance, only pages that mention electricity-demand keywords are sent
(`select_pages`), in chunks of at most MAX_PAGES_PER_REQUEST pages.

    pixi run evidence --dry-run                # token + cost estimate per document, no API call
    pixi run evidence --docs dhs_rw_2019_fr    # real run (needs ANTHROPIC_API_KEY)
    pixi run evidence --batch                  # same through the Batch API (50% cheaper, async)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import fitz as pymupdf  # PyMuPDF; the conda-forge build (1.24) only ships the `fitz` name
import pandas as pd

from rtl.llm.client import DEFAULT_MODEL, LLMClient, LLMResult, build_request, cost_usd, document_block
from rtl.manifest import read_manifest
from rtl.schemas import evidence as evidence_schema
from rtl.settings import INTERIM_DIR, PROCESSED_DIR, REPO_ROOT, load_yaml

PROMPT_DIR = Path(__file__).parent / "prompts"
PROMPT_VERSION = "evidence_v1"  # bump together with new prompt files (tests check prompts/versions.json)
MAX_PAGES_PER_REQUEST = 40
MAX_PAGES_PER_DOC = 120
MIN_KEYWORD_HITS = 2
NORMALISE_CHUNK = 60  # statements per normalisation request when a document overflows one response

# A page is "relevant" if it mentions at least MIN_KEYWORD_HITS distinct terms from this list.
KEYWORDS = [
    "electricity", "electrified", "electrification", "grid", "kwh", "kilowatt", "watt", "tariff", "appliance",
    "television", "radio", "refrigerator", "fridge", "mobile phone", "computer", "lighting", "solar",
    "consumption", "connection", "fan", "iron", "cooker", "pump", "mill", "welding", "sewing", "freezer",
    "energy", "household", "decoder", "power",
]

# --------------------------------------------------------------------------- dry-run heuristic (documented)
# Anthropic bills a PDF page as its extracted text plus an image of the page. We estimate:
#   text tokens  = characters / 4          (English prose; tables are denser, so this is a slight under-count)
#   image tokens = 1,600 per page          (a ~1000x1400 px page image at ~750 px per token)
#   step (a) output = 3,000 tokens per chunk (adaptive thinking at medium effort + cited statements)
#   step (b) one request per document: input = normalise prompt + all step (a) output + 500 overhead;
#            output = 2,500 tokens per chunk of step (a) (rows scale with the number of statements)
CHARS_PER_TOKEN = 4
IMAGE_TOKENS_PER_PAGE = 1600
EXTRACT_OUTPUT_TOKENS = 3000
NORMALISE_OUTPUT_TOKENS = 2500


def load_prompt(name: str) -> str:
    return (PROMPT_DIR / f"{PROMPT_VERSION}_{name}.md").read_text()


# --------------------------------------------------------------------------- corpus helpers
def catalog() -> dict[str, dict]:
    return {d["doc_id"]: d for d in load_yaml("evidence_docs.yaml")["documents"]}


def doc_path(doc_id: str) -> Path | None:
    """Local PDF for a doc, looked up in the manifest (version folders differ per file)."""
    hits = [e["path"] for e in read_manifest().values()
            if e.get("source_id") == "evidence_docs" and Path(e["path"]).name == f"{doc_id}.pdf"]
    return REPO_ROOT / sorted(hits)[-1] if hits else None


def page_texts(pdf: Path) -> list[str]:
    with pymupdf.open(pdf) as doc:
        return [page.get_text() for page in doc]


def select_pages(texts: list[str], max_pages: int = MAX_PAGES_PER_DOC) -> list[int]:
    """1-based page numbers with >= MIN_KEYWORD_HITS distinct keywords, best-scoring first if over the cap."""
    scored = []
    for i, t in enumerate(texts, start=1):
        low = t.lower()
        hits = sum(1 for k in KEYWORDS if k in low)
        if hits >= MIN_KEYWORD_HITS:
            scored.append((hits, i))
    keep = sorted(scored, reverse=True)[:max_pages]
    return sorted(i for _, i in keep)


def chunked(pages: list[int], size: int = MAX_PAGES_PER_REQUEST) -> list[list[int]]:
    return [pages[i:i + size] for i in range(0, len(pages), size)]


def sub_pdf(pdf: Path, pages: list[int]) -> bytes:
    """A new PDF containing only `pages` (1-based), so Claude sees the original layout and tables."""
    with pymupdf.open(pdf) as src, pymupdf.open() as out:
        for p in pages:
            out.insert_pdf(src, from_page=p - 1, to_page=p - 1)
        out.set_metadata({})  # no creation date and no random file /ID: same pages -> same bytes -> cache hit
        return out.tobytes(no_new_id=True, garbage=3, deflate=True)


# --------------------------------------------------------------------------- quote verification
_DASHES = dict.fromkeys(map(ord, "‐‑‒–—−"), "-")
_QUOTES = {ord("‘"): "'", ord("’"): "'", ord("“"): '"', ord("”"): '"'}


def normalise_text(s: str) -> str:
    """Make PDF text and quotes comparable: ligatures (NFKC), dashes, curly quotes, soft hyphens,
    words split by a line-end hyphen ('electri-\\ncity' -> 'electricity') and all whitespace runs -> one space."""
    s = unicodedata.normalize("NFKC", s).translate(_DASHES).translate(_QUOTES).replace("­", "")
    s = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", s)
    return re.sub(r"\s+", " ", s).strip()


def verify_quote(quote: str, texts: list[str], page: int | None) -> bool:
    """True if the normalised quote occurs on the cited page, or starts there and runs onto the next page.

    A quote that lies entirely on the next page is NOT accepted: the cited page would be wrong."""
    if not quote or page is None or not 1 <= page <= len(texts):
        return False
    q = normalise_text(quote)
    here = texts[page - 1]
    if q in normalise_text(here):
        return True
    if page == len(texts):
        return False
    nxt = texts[page]
    return q in normalise_text(here + "\n" + nxt) and q not in normalise_text(nxt)


# --------------------------------------------------------------------------- step (a) extract
@dataclass
class Claim:
    claim_index: int
    text: str
    quote: str
    page: int | None  # page in the ORIGINAL document


def extract_request(doc: dict, pdf_bytes: bytes, model: str = DEFAULT_MODEL) -> dict:
    prompt = load_prompt("extract").format(**{k: doc.get(k, "") for k in ("title", "publisher", "year")})
    return build_request([document_block(pdf_bytes, title=doc["title"], citations=True), {"type": "text",
                                                                                         "text": prompt}],
                         model=model, max_tokens=16000, effort="medium")


def claims_from(result: LLMResult, page_map: list[int], start_index: int = 0) -> list[Claim]:
    """One Claim per citation. page_map[i] is the original page of sub-PDF page i+1."""
    claims = []
    for block in result.citations():
        for c in block["citations"]:
            p = c.get("start_page_number")
            page = page_map[p - 1] if p and 1 <= p <= len(page_map) else None
            claims.append(Claim(start_index + len(claims), block["text"].strip(), c.get("cited_text", ""), page))
    return claims


# --------------------------------------------------------------------------- step (b) normalise
_NUM = {"type": ["number", "null"]}
_STR = {"type": ["string", "null"]}
ROW_SCHEMA = {
    "type": "object",
    "properties": {"rows": {"type": "array", "items": {
        "type": "object",
        "properties": {"parameter": {"type": "string"}, "value": _NUM, "value_low": _NUM, "value_high": _NUM,
                       "unit": {"type": "string"}, "population": _STR, "geography": _STR,
                       "year": {"type": ["integer", "null"]}, "claim_index": {"type": "integer"}, "notes": _STR},
        "required": ["parameter", "value", "value_low", "value_high", "unit", "population", "geography", "year",
                     "claim_index", "notes"],
        "additionalProperties": False}}},
    "required": ["rows"],
    "additionalProperties": False,
}


def normalise_request(doc: dict, claims: list[Claim], model: str = DEFAULT_MODEL) -> dict:
    system = load_prompt("normalise").format(**{k: doc.get(k, "") for k in ("title", "publisher", "year",
                                                                             "doc_id")})
    payload = [{"claim_index": c.claim_index, "statement": c.text, "quote": c.quote} for c in claims]
    return build_request(json.dumps(payload, ensure_ascii=False, indent=0), system=system, model=model,
                         max_tokens=32000, effort="medium", json_schema=ROW_SCHEMA)


def evidence_id(doc_id: str, row: dict, quote: str, page: int | None) -> str:
    basis = "|".join(str(x) for x in (doc_id, row["parameter"], row.get("population"), row.get("geography"),
                                      row.get("year"), page, quote))
    return "ev-" + hashlib.sha256(basis.encode()).hexdigest()[:12]


def rows_from(doc_id: str, result: LLMResult, claims: list[Claim], texts: list[str]) -> list[dict]:
    """Turn normalised rows into `evidence` contract rows; quote/page come from the referenced claim."""
    by_idx = {c.claim_index: c for c in claims}
    out = []
    for r in result.json()["rows"]:
        claim = by_idx.get(r["claim_index"])
        if claim is None:  # model referenced a claim that doesn't exist: keep it visible, unverified
            claim = Claim(r["claim_index"], "", "", None)
        out.append({
            "evidence_id": evidence_id(doc_id, r, claim.quote, claim.page), "parameter": r["parameter"],
            "value": r["value"], "value_low": r["value_low"], "value_high": r["value_high"], "unit": r["unit"],
            "population": r["population"], "geography": r["geography"], "year": r["year"], "doc_id": doc_id,
            "page": claim.page, "quote": claim.quote, "quote_verified": verify_quote(claim.quote, texts, claim.page),
            "model": result.model, "prompt_version": result.prompt_version, "human_verified": False,
            "notes": r["notes"]})
    return out


def to_frame(rows: list[dict]) -> pd.DataFrame:
    cols = list(evidence_schema.columns)
    df = pd.DataFrame(rows, columns=cols).drop_duplicates("evidence_id")
    for c in ("value", "value_low", "value_high"):
        df[c] = df[c].astype(float)
    for c in ("year", "page"):
        df[c] = df[c].astype("Int64")
    df["year"] = df["year"].where(df["year"].between(1990, 2026))  # out-of-range year = not observed
    for c in ("quote_verified", "human_verified"):
        df[c] = df[c].astype(bool)
    return evidence_schema.validate(df)


# --------------------------------------------------------------------------- dry run
def estimate(doc: dict, pdf: Path, model: str = DEFAULT_MODEL, batch: bool = False) -> dict:
    """Token and cost estimate for one document without calling the API (heuristic above)."""
    texts = page_texts(pdf)
    pages = select_pages(texts)
    n_chunks = len(chunked(pages))
    extract_prompt = len(load_prompt("extract")) // CHARS_PER_TOKEN
    norm_prompt = len(load_prompt("normalise")) // CHARS_PER_TOKEN
    doc_tokens = sum(len(texts[p - 1]) // CHARS_PER_TOKEN + IMAGE_TOKENS_PER_PAGE for p in pages)
    in_a = doc_tokens + n_chunks * extract_prompt
    out_a = n_chunks * EXTRACT_OUTPUT_TOKENS
    in_b = norm_prompt + 500 + out_a
    out_b = n_chunks * NORMALISE_OUTPUT_TOKENS
    usd = cost_usd(model, {"input_tokens": in_a + in_b, "output_tokens": out_a + out_b}, batch=batch)
    return {"doc_id": doc["doc_id"], "pages": len(texts), "pages_sent": len(pages), "requests": n_chunks + 1,
            "input_tokens": in_a + in_b, "output_tokens": out_a + out_b, "est_usd": round(usd, 3)}


# --------------------------------------------------------------------------- run
def run_requests(client: LLMClient, items: dict[str, dict], tag: str, batch: bool) -> dict[str, LLMResult]:
    """Run {custom_id: request}. With batch=True the uncached ones go through the Batch API first;
    collected results land in the cache, so the get_or_call loop below is then all cache hits."""
    if batch and (bid := client.submit_batch(items, PROMPT_VERSION, tag=tag)):
        print(f"submitted batch {bid} ({len(items)} requests); polling…")
        client.poll_batch(bid)
        client.collect_batch(bid)
    # live: run uncached requests concurrently (results are written to the cache on this thread)
    res = client.run_concurrent(items, PROMPT_VERSION, tag=tag, max_workers=6)
    errors = {cid: r for cid, r in res.items() if isinstance(r, Exception)}
    if errors:
        print(f"{len(errors)} of {len(items)} requests failed; first: {next(iter(errors.values()))!r}"[:400])
    return {cid: r for cid, r in res.items() if not isinstance(r, Exception)}


def extract_docs(doc_ids: list[str], client: LLMClient, model: str = DEFAULT_MODEL,
                 batch: bool = False) -> pd.DataFrame:
    docs = catalog()
    plans, step_a = {}, {}
    for doc_id in doc_ids:
        pdf = doc_path(doc_id)
        if pdf is None:
            print(f"[{doc_id}] not downloaded; skipping")
            continue
        texts = page_texts(pdf)
        plans[doc_id] = (texts, chunked(select_pages(texts)))
        for i, pages in enumerate(plans[doc_id][1]):
            step_a[f"{doc_id}--{i}"] = extract_request(docs[doc_id], sub_pdf(pdf, pages), model)
    res_a = run_requests(client, step_a, "evidence_extract", batch)

    claims: dict[str, list[Claim]] = {}
    step_b = {}
    for doc_id, (_texts, chunks) in plans.items():
        claims[doc_id] = []
        for i, pages in enumerate(chunks):
            r = res_a.get(f"{doc_id}--{i}")
            if r is None:  # request failed (reported above); rerun later, cached chunks are free
                continue
            if r.refused:
                print(f"[{doc_id} chunk {i}] refused; skipped")
                continue
            claims[doc_id] += claims_from(r, pages, start_index=len(claims[doc_id]))
        if claims[doc_id]:
            step_b[doc_id] = normalise_request(docs[doc_id], claims[doc_id], model)
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"doc_id": d, **c.__dict__} for d, cs in claims.items() for c in cs]).to_parquet(
        INTERIM_DIR / "evidence_claims.parquet", index=False)

    res_b = run_requests(client, step_b, "evidence_normalise", batch)
    # A document with very many statements can overflow one normalisation response. Only those documents are
    # re-normalised in chunks of statements, so documents that already succeeded stay cache hits.
    redo = {}
    for doc_id, r in res_b.items():
        if r.stop_reason == "max_tokens":
            cs = claims[doc_id]
            for j in range(0, len(cs), NORMALISE_CHUNK):
                redo[f"{doc_id}--n{j // NORMALISE_CHUNK}"] = (doc_id, cs[j:j + NORMALISE_CHUNK])
    res_c = run_requests(client, {k: normalise_request(docs[d], cs, model) for k, (d, cs) in redo.items()},
                         "evidence_normalise_chunk", batch) if redo else {}
    if redo:
        print(f"re-normalised {len({d for d, _ in redo.values()})} truncated documents in {len(redo)} chunks")

    rows = []
    results = [(d, r, claims[d]) for d, r in res_b.items() if r.stop_reason != "max_tokens"]
    results += [(redo[k][0], r, redo[k][1]) for k, r in res_c.items()]
    for doc_id, r, cs in results:
        if r.refused or r.stop_reason == "max_tokens":
            print(f"[{doc_id}] normalisation {'refused' if r.refused else 'truncated'}; skipped")
            continue
        rows += rows_from(doc_id, r, cs, plans[doc_id][0])
    return to_frame(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description="Extract RAMP evidence from the corpus with Claude")
    ap.add_argument("--docs", help="comma-separated doc_ids (default: all downloaded)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--batch", action="store_true", help="use the Batch API (50%% off, async)")
    ap.add_argument("--dry-run", action="store_true", help="estimate tokens and cost; no API call")
    args = ap.parse_args()
    docs = catalog()
    ids = args.docs.split(",") if args.docs else [d for d in docs if doc_path(d)]

    if args.dry_run:
        est = pd.DataFrame([estimate(docs[d], doc_path(d), args.model, args.batch) for d in ids if doc_path(d)])
        print(est.to_string(index=False))
        print(f"\nTOTAL {len(est)} docs, {est.pages.sum()} pages ({est.pages_sent.sum()} sent), "
              f"{est.requests.sum()} requests, {est.input_tokens.sum():,} in / {est.output_tokens.sum():,} out "
              f"tokens -> ~${est.est_usd.sum():.2f} ({args.model}{', batch' if args.batch else ''})")
        return

    df = extract_docs(ids, LLMClient(), args.model, args.batch)
    out = PROCESSED_DIR / "evidence.parquet"
    if out.exists():  # a run on some docs replaces only those docs' rows
        old = pd.read_parquet(out)
        df = to_frame(pd.concat([old[~old.doc_id.isin(ids)], df]).to_dict("records"))
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    print(f"{len(df)} evidence rows ({df.quote_verified.mean():.0%} quotes verified) "
          "-> data/processed/evidence.parquet")


if __name__ == "__main__":
    main()
