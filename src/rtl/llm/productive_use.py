"""Productive-use appliance specs from the web, cited, into the `evidence` contract.

The PDF corpus has almost nothing on the machines that drive productive-use load (grain mills, welders, sewing
machines, fridges in shops, pumps). This runs one web-research request per appliance (server-side web search +
fetch; every statement carries a citation with the exact source text), then reuses the evidence pipeline's
normalisation step to turn cited statements into rows. doc_id = source URL; quote = the cited span returned by the
API; human_verified = False until the repo owner reviews.

    pixi run productive-use            # live, ~$5 total
    pixi run productive-use --dry-run  # list topics only
"""

from __future__ import annotations

import argparse
import re

import pandas as pd

from rtl.llm.client import LLMClient, LLMResult, build_request
from rtl.llm.evidence import Claim, evidence_id, normalise_request, to_frame
from rtl.settings import PROCESSED_DIR

PROMPT_VERSION = "productive_use_v1"
OUT = PROCESSED_DIR / "evidence_productive_use.parquet"
TOOLS = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 6},
         {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 4}]

TOPICS = {
    "grain_mill": "electric grain / maize / cassava hammer mills used by small millers",
    "welding": "small arc-welding machines used by roadside metal workshops",
    "sewing_machine": "electric sewing machines used by tailors",
    "commercial_fridge": "refrigerators and chest freezers used in small shops, bars and butcheries",
    "water_pump": "electric water pumps for small-scale irrigation and water kiosks",
    "hair_salon": "hair clippers, hair dryers and other salon/barber-shop appliances",
    "phone_charging_kiosk": "mobile-phone charging stations / kiosks",
    "carpentry": "electric carpentry tools in small furniture workshops (planers, saws, drills)",
}

TASK = """Research typical electrical characteristics of {what} in Rwanda or elsewhere in East Africa (use other
low- and middle-income settings only if East African sources are lacking, and say so).

I need, as concrete numbers wherever sources give them:
- rated electrical power (W or kW) and typical range across common models;
- typical operating hours per day and days per week;
- when in the day the appliance is typically used;
- duty cycle / how much of the operating time the motor or heater actually draws power;
- monthly electricity use (kWh) for such a business, if reported.

Write each fact as its own short sentence that includes the number and the place it applies to. Only state
what a source supports; prefer manufacturer spec sheets, energy-access studies (e.g. Efficiency for Access, ESMAP,
GOGLA, CLASP, GIZ, academic field studies), and utility or mini-grid operator reports."""


def research_request(topic: str, model: str) -> dict:
    req = build_request(TASK.format(what=TOPICS[topic]), model=model, max_tokens=16000, effort="medium")
    req["tools"] = TOOLS
    return req


def claims_and_sources(result: LLMResult) -> tuple[list[Claim], dict[int, tuple[str, str]]]:
    """One Claim per web citation, plus claim_index -> (url, title)."""
    claims, src = [], {}
    for block in result.citations():
        for c in block["citations"]:
            if not c.get("url"):
                continue
            i = len(claims)
            claims.append(Claim(i, block["text"].strip(), c.get("cited_text", ""), None))
            src[i] = (c["url"], c.get("title") or "")
    return claims, src


def rows(topic: str, norm: LLMResult, claims: list[Claim], src: dict) -> list[dict]:
    by = {c.claim_index: c for c in claims}
    out = []
    for r in norm.json()["rows"]:
        c = by.get(r["claim_index"])
        if c is None:
            continue
        url, title = src[c.claim_index]
        out.append({"evidence_id": evidence_id(url, r, c.quote, None), "parameter": r["parameter"],
                    "value": r["value"], "value_low": r["value_low"], "value_high": r["value_high"],
                    "unit": r["unit"], "population": r["population"], "geography": r["geography"], "year": r["year"],
                    "doc_id": url, "page": None, "quote": c.quote,
                    # the cited span is returned by the API from the fetched page itself
                    "quote_verified": bool(c.quote.strip()),
                    "model": norm.model, "prompt_version": PROMPT_VERSION, "human_verified": False,
                    "notes": "; ".join(x for x in [f"topic={topic}", title, r.get("notes")] if x)})
    return out


def research(client: LLMClient, topic: str, model: str) -> LLMResult:
    """Run the research turn, continuing if the server pauses a long tool loop (stop_reason=pause_turn)."""
    req = research_request(topic, model)
    res = client.get_or_call(req, PROMPT_VERSION, tag=f"productive_use:{topic}")
    for _ in range(3):
        if res.stop_reason != "pause_turn":
            break
        req = {**req, "messages": req["messages"] + [{"role": "assistant", "content": res.response["content"]}]}
        res = client.get_or_call(req, PROMPT_VERSION, tag=f"productive_use:{topic}")
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--topics", help="comma-separated subset of: " + ",".join(TOPICS))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    topics = args.topics.split(",") if args.topics else list(TOPICS)
    if args.dry_run:
        print("\n".join(f"{t}: {TOPICS[t]}" for t in topics))
        return
    client = LLMClient()
    all_rows = []
    for t in topics:
        res = research(client, t, args.model)
        claims, src = claims_and_sources(res)
        if not claims:
            print(f"{t}: no cited statements (stop_reason={res.stop_reason})")
            continue
        doc = {"title": f"web research: {TOPICS[t]}", "publisher": "various web sources", "year": "",
               "doc_id": f"web:{t}"}
        norm = client.get_or_call(normalise_request(doc, claims, args.model), PROMPT_VERSION,
                                  tag=f"productive_use_norm:{t}")
        r = rows(t, norm, claims, src)
        all_rows += r
        print(f"{t}: {len(claims)} cited statements -> {len(r)} rows from "
              f"{len({re.sub(r'#.*', '', s[0]) for s in src.values()})} sources")
    df = to_frame(all_rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, index=False)
    print(f"{len(df)} rows -> {OUT}")
    print(pd.Series({"spent_usd": float(client.cache.ledger().cost_usd.sum())}).to_string())


if __name__ == "__main__":
    main()
