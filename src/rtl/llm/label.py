"""Claude building-use labels for context cards, cached and costed.

Configs are (model, variant) with variant in {text, image}. Every response is cached by rtl.llm.cache, so rerunning
a stage costs nothing, and labels can be rebuilt from the cache at any time.

    pixi run label calibrate                 # 20 live calls per config -> measured $/call (budget input)
    pixi run label gold                      # all 6 configs on the 400 gold candidates (live, concurrent)
    pixi run label pilot --config sonnet:image --batch
    pixi run label deep-dives --config sonnet:text --batch --max-usd 90
    pixi run label export                    # cache -> data/processed/labels.parquet (labels contract)
"""

from __future__ import annotations

import argparse
import base64
import json
from datetime import UTC, datetime

import pandas as pd

from rtl.llm import budget
from rtl.llm.cards import CARD_DIR, IMG_DIR
from rtl.llm.client import LLMClient, LLMResult, build_request
from rtl.schemas import LABEL_CLASSES, labels
from rtl.settings import GOLD_DIR, PROCESSED_DIR, REPO_ROOT

PROMPT_VERSION = "label_v1"
MODELS = {"haiku": "claude-haiku-4-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}
CONFIGS = [f"{m}:{v}" for m in MODELS for v in ("text", "image")]
OUT = PROCESSED_DIR / "labels.parquet"

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["label", "probs", "confidence", "evidence", "abstain"],
    "properties": {
        "label": {"type": "string", "enum": LABEL_CLASSES},
        "probs": {"type": "object", "additionalProperties": False, "required": LABEL_CLASSES,
                  "properties": {c: {"type": "number"} for c in LABEL_CLASSES}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "evidence": {"type": "string"},
        "abstain": {"type": "boolean"},
    },
}


def system_prompt() -> str:
    head = (REPO_ROOT / "src/rtl/llm/prompts/label_v1.md").read_text()
    return head + "\n" + (REPO_ROOT / "docs/codebook.md").read_text()


def request(card_text: str, bldg_id: str, config: str) -> dict:
    model_key, variant = config.split(":")
    content: list[dict] = []
    if variant == "image":
        png = (IMG_DIR / f"{bldg_id}.png").read_bytes()
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                    "data": base64.standard_b64encode(png).decode()}})
    content.append({"type": "text", "text": card_text})
    return build_request(content, system=system_prompt(), model=MODELS[model_key], max_tokens=4000,
                         effort="low", json_schema=SCHEMA)


def cards(which: str) -> pd.DataFrame:
    path = CARD_DIR / ("cards_deep_dives.parquet" if which == "deep-dives" else "cards.parquet")
    c = pd.read_parquet(path)
    if which in ("gold", "calibrate"):
        ids = pd.read_csv(GOLD_DIR / "sample_ids.csv").query("in_gold").bldg_id
        c = c[c.bldg_id.isin(set(ids))]
    return c.sort_values("bldg_id").reset_index(drop=True)


def requests_for(c: pd.DataFrame, config: str) -> dict[str, dict]:
    return {f"{config}|{r.bldg_id}": request(r.text, r.bldg_id, config) for r in c.itertuples()}


def mean_cost(results: dict) -> float:
    costs = [r.cost_usd for r in results.values() if isinstance(r, LLMResult)]
    return sum(costs) / max(1, len(costs))


def run(which: str, configs: list[str], batch: bool, limit: int | None, max_usd: float | None,
        client: LLMClient) -> None:
    c = cards(which)
    if limit:
        c = c.head(limit)
    for config in configs:
        reqs = requests_for(c, config)
        per = calibrated_cost(config, client)
        n_fit = budget.check(len(reqs), per, client.cache).affordable_n
        if max_usd is not None:
            n_fit = min(n_fit, int(max_usd // (per * budget.MARGIN)))
        if n_fit < len(reqs):
            print(f"[budget] {config}: trimming {len(reqs)} -> {n_fit} requests")
            reqs = dict(list(reqs.items())[:n_fit])
        budget.require(len(reqs), per, f"{which} {config}", client.cache)
        tag = f"label:{which}:{config}"
        if batch:
            for i in range(0, len(reqs), 1000):  # keep each batch well under the 256 MB payload limit
                chunk = dict(list(reqs.items())[i:i + 1000])
                bid = client.submit_batch({k.replace("|", "__").replace(":", "-"): v for k, v in chunk.items()},
                                          PROMPT_VERSION, tag=tag)
                if bid:
                    print(f"{config}: batch {bid} ({len(chunk)} requests) submitted")
                    client.poll_batch(bid, every_s=30)
                    client.collect_batch(bid)
        else:
            res = client.run_concurrent(reqs, PROMPT_VERSION, tag=tag, max_workers=8)
            errs = [r for r in res.values() if isinstance(r, Exception)]
            print(f"{config}: {len(res) - len(errs)} ok, {len(errs)} errors, mean ${mean_cost(res):.4f}/call"
                  + (f"; first error: {errs[0]!r}"[:300] if errs else ""))
        print(f"[budget] spent so far ${budget.spent(client.cache):.2f}")


def calibrated_cost(config: str, client: LLMClient) -> float:
    """Mean live $/call for this config from the ledger; falls back to a conservative prior before calibration."""
    led = client.cache.ledger()
    rows = led[led.tag.fillna("").str.endswith(config) & ~led.batch] if len(led) else led
    if len(rows) >= 5:
        return float(rows.cost_usd.mean())
    prior = {"haiku": 0.004, "sonnet": 0.012, "opus": 0.025}[config.split(":")[0]]
    return prior


def export(client: LLMClient) -> pd.DataFrame:
    """Rebuild data/processed/labels.parquet from every cached label_v1 response."""
    import duckdb

    with client.cache._connect() as con:
        rows = con.execute("SELECT key, model, tag, response_json, stop_reason, created_at FROM responses "
                           "WHERE prompt_version = ?", [PROMPT_VERSION]).fetchall()
    req_ids = {}
    with client.cache._connect() as con:  # custom ids of batch items carry the building id
        for key, cid in con.execute("SELECT key, custom_id FROM batches").fetchall():
            req_ids[key] = cid
    out = []
    for key, model, tag, resp_json, stop, created in rows:
        resp = json.loads(resp_json)
        bldg = _bldg_from_request(client, key, req_ids.get(key))
        variant = (tag or "::").split(":")[-1]
        rec = {"bldg_id": bldg, "label_source": "claude", "model": model, "prompt_version": PROMPT_VERSION,
               "labeler": f"{model}|{variant}|{PROMPT_VERSION}", "labeled_at": pd.Timestamp(created).tz_convert("UTC")}
        if stop == "refusal":
            out.append({**rec, "label": "unknown", "confidence": None, "probs_json": None, "abstain": True})
            continue
        text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
        try:
            j = json.loads(text)
        except json.JSONDecodeError:
            out.append({**rec, "label": "unknown", "confidence": None, "probs_json": None, "abstain": True})
            continue
        probs = j["probs"]
        tot = sum(probs.values()) or 1.0
        out.append({**rec, "label": j["label"], "confidence": float(min(1.0, probs.get(j["label"], 0) / tot)),
                    "probs_json": json.dumps({**probs, "_confidence": j["confidence"], "_evidence": j["evidence"]}),
                    "abstain": bool(j["abstain"])})
    df = pd.DataFrame(out).drop_duplicates(["bldg_id", "label_source", "labeler"], keep="last")
    df = df[list(labels.columns.keys())]
    df["labeled_at"] = pd.to_datetime(df["labeled_at"], utc=True).astype("datetime64[ns, UTC]")
    labels.validate(df, lazy=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, index=False)
    print(f"{len(df):,} labels -> {OUT}")
    print(duckdb.sql("SELECT labeler, count(*) n, avg(abstain::int) abstain FROM df GROUP BY 1 ORDER BY 1").df()
          .to_string(index=False))
    return df


def _bldg_from_request(client: LLMClient, key: str, custom_id: str | None) -> str:
    """The building id is in the card text of the stored request (first line: 'Building RWA-…:')."""
    with client.cache._connect() as con:
        req = json.loads(con.execute("SELECT request_json FROM responses WHERE key = ?", [key]).fetchone()[0])
    for block in req["messages"][0]["content"]:
        if block.get("type") == "text" and block["text"].startswith("Building RWA-"):
            return block["text"].split(":", 1)[0].removeprefix("Building ")
    raise ValueError(f"no building id in cached request {key}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("stage", choices=["calibrate", "gold", "pilot", "deep-dives", "export"])
    ap.add_argument("--config", action="append", help="model:variant, e.g. sonnet:image (repeatable)")
    ap.add_argument("--batch", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--max-usd", type=float, help="cap for this stage on top of the global budget")
    args = ap.parse_args()
    client = LLMClient()
    if args.stage == "export":
        export(client)
        return
    configs = args.config or CONFIGS
    if args.stage == "calibrate":
        run("calibrate", configs, batch=False, limit=args.limit or 20, max_usd=None, client=client)
    else:
        run(args.stage, configs, args.batch, args.limit, args.max_usd, client)
    print(f"done at {datetime.now(UTC):%H:%M} UTC")


if __name__ == "__main__":
    main()
