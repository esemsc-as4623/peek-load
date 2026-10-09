"""Download raw Batch API results to JSONL files (durable; no cache writes, so it is fast).

    pixi run python -m rtl.llm.download_batches label:label_v2:deep-dives

Batch results stay retrievable for 29 days; keeping a local copy removes any dependence on the account later.
`ingest` loads the files into the response cache in bulk afterwards.
"""

from __future__ import annotations

import json
import sys
import time

from rtl.llm.cache import Cache
from rtl.llm.client import LLMClient
from rtl.settings import LLM_CACHE_DIR

OUT = LLM_CACHE_DIR / "batch_results"


def main(tag_prefix: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    api = LLMClient()._api()
    with Cache()._connect() as con:
        bids = [r[0] for r in con.execute("SELECT DISTINCT batch_id FROM batches WHERE tag LIKE ?",
                                          [tag_prefix + "%"]).fetchall()]
    for b in bids:
        f = OUT / f"{b}.jsonl"
        if f.exists() and f.stat().st_size > 0:
            continue
        for attempt in range(5):
            try:
                rows = [r.model_dump(mode="json") for r in api.messages.batches.results(b)]
                f.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
                print(b[-6:], len(rows), flush=True)
                break
            except Exception as e:  # noqa: BLE001 - network drops; retry
                print(b[-6:], "retry", attempt, repr(e)[:100], flush=True)
                time.sleep(2 * (attempt + 1))
    print("DONE", len(list(OUT.glob("*.jsonl"))), "of", len(bids), flush=True)




def ingest() -> int:
    """Load downloaded batch results into the response cache in ONE transaction (priced at the batch rate)."""
    from datetime import UTC, datetime

    from rtl.llm.cache import canonical_json
    from rtl.llm.client import cost_usd

    cache = Cache()
    with cache._connect() as con:
        meta = {r[0]: r[1:] for r in con.execute(
            "SELECT custom_id, key, model, prompt_version, tag, request_json FROM batches").fetchall()}
        have = {r[0] for r in con.execute("SELECT key FROM responses").fetchall()}
        now = datetime.now(UTC)
        resp_rows, ledger_rows = [], []
        for f in sorted(OUT.glob("*.jsonl")):
            for line in f.read_text().splitlines():
                r = json.loads(line)
                if r["result"]["type"] != "succeeded" or r["custom_id"] not in meta:
                    continue
                key, model, pv, tag, req_json = meta[r["custom_id"]]
                if key in have:
                    continue
                msg = r["result"]["message"]
                u = msg.get("usage") or {}
                cost = cost_usd(model, u, batch=True)
                resp_rows.append([key, model, pv, tag, req_json, canonical_json(msg), canonical_json(u), cost,
                                  msg.get("stop_reason"), True, now])
                ledger_rows.append([now, key, model, pv, tag, u.get("input_tokens") or 0, u.get("output_tokens") or 0,
                                    u.get("cache_read_input_tokens") or 0, u.get("cache_creation_input_tokens") or 0,
                                    cost, msg.get("stop_reason"), True])
                have.add(key)
        import pandas as pd

        rcols = [d[0] for d in con.execute("SELECT * FROM responses LIMIT 0").description]
        lcols = [d[0] for d in con.execute("SELECT * FROM ledger LIMIT 0").description]
        for i in range(0, len(resp_rows), 5000):  # DataFrame inserts in chunks; executemany runs out of memory
            con.register("r_df", pd.DataFrame(resp_rows[i:i + 5000], columns=rcols))
            con.register("l_df", pd.DataFrame(ledger_rows[i:i + 5000], columns=lcols))
            con.execute("BEGIN")
            con.execute("INSERT INTO responses SELECT * FROM r_df")
            con.execute("INSERT INTO ledger SELECT * FROM l_df")
            con.execute("COMMIT")
    print(f"ingested {len(resp_rows):,} batch results; batch cost ${sum(r[7] for r in resp_rows):.2f}")
    return len(resp_rows)


if __name__ == "__main__":
    if sys.argv[1:2] == ["ingest"]:
        ingest()
    else:
        main(sys.argv[1] if len(sys.argv) > 1 else "label:label_v2:deep-dives")
