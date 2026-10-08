"""Content-addressed cache and cost ledger for every Claude API call.

Why: LLM outputs are research data. The same request must give the same stored answer (reproducible),
must never be paid for twice, and every dollar spent must be traceable to a model, prompt version and tag.

- key = sha256(canonical JSON of {model, prompt_version, request}). Canonical = sorted keys, no spaces,
  so dict ordering can't change the key. Large base64 blobs (PDFs) are replaced by their sha256 first:
  same content -> same key, and the cache doesn't store megabytes of base64.
- Storage: one DuckDB file, data/llm_cache/cache.duckdb, with two tables:
    responses  one row per key (the cached answer)
    ledger     one row per real API call (append-only; cache hits cost nothing and aren't logged)
- Several workstreams (A4 evidence, A5 labels) share it, so each write opens a short connection under a
  file lock. DuckDB allows only one writing process at a time; the lock makes the others wait instead of fail.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from rtl.settings import LLM_CACHE_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS responses (
    key VARCHAR PRIMARY KEY, model VARCHAR, prompt_version VARCHAR, tag VARCHAR,
    request_json VARCHAR, response_json VARCHAR, usage_json VARCHAR,
    cost_usd DOUBLE, stop_reason VARCHAR, batch BOOLEAN, created_at TIMESTAMPTZ);
CREATE TABLE IF NOT EXISTS ledger (
    called_at TIMESTAMPTZ, key VARCHAR, model VARCHAR, prompt_version VARCHAR, tag VARCHAR,
    input_tokens BIGINT, output_tokens BIGINT, cache_read_tokens BIGINT, cache_write_tokens BIGINT,
    cost_usd DOUBLE, stop_reason VARCHAR, batch BOOLEAN);
CREATE TABLE IF NOT EXISTS batches (
    batch_id VARCHAR, custom_id VARCHAR, key VARCHAR, model VARCHAR, prompt_version VARCHAR, tag VARCHAR,
    request_json VARCHAR, submitted_at TIMESTAMPTZ);
"""


def canonical_json(obj: Any) -> str:
    """Deterministic JSON: the same logical object always serialises to the same bytes."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def strip_blobs(obj: Any) -> Any:
    """Replace base64 payloads ({"type": "base64", "data": ...}) by 'sha256:<hex>' (recursively)."""
    if isinstance(obj, dict):
        out = {k: strip_blobs(v) for k, v in obj.items()}
        if obj.get("type") == "base64" and isinstance(obj.get("data"), str):
            out["data"] = "sha256:" + hashlib.sha256(obj["data"].encode()).hexdigest()
        return out
    if isinstance(obj, list):
        return [strip_blobs(v) for v in obj]
    return obj


def cache_key(model: str, prompt_version: str, request: dict) -> str:
    payload = {"model": model, "prompt_version": prompt_version, "request": strip_blobs(request)}
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


@dataclass
class CacheEntry:
    key: str
    model: str
    prompt_version: str
    tag: str | None
    response: dict
    usage: dict
    cost_usd: float
    stop_reason: str | None
    batch: bool
    created_at: datetime | None = None


class Cache:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or LLM_CACHE_DIR / "cache.duckdb")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.execute(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        lock = self.path.with_suffix(".lock")
        with open(lock, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            con = duckdb.connect(str(self.path))
            try:
                yield con
            finally:
                con.close()
                fcntl.flock(f, fcntl.LOCK_UN)

    def get(self, key: str) -> CacheEntry | None:
        with self._connect() as con:
            row = con.execute("SELECT key, model, prompt_version, tag, response_json, usage_json, cost_usd, "
                              "stop_reason, batch, created_at FROM responses WHERE key = ?", [key]).fetchone()
        if row is None:
            return None
        return CacheEntry(row[0], row[1], row[2], row[3], json.loads(row[4]), json.loads(row[5]), row[6], row[7],
                          row[8], row[9])

    def put(self, entry: CacheEntry, request: dict) -> None:
        """Store a fresh API response and append its cost to the ledger (one transaction)."""
        now = datetime.now(UTC)
        u = entry.usage
        with self._connect() as con:
            con.execute("INSERT OR REPLACE INTO responses VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
                entry.key, entry.model, entry.prompt_version, entry.tag, canonical_json(strip_blobs(request)),
                canonical_json(entry.response), canonical_json(u), entry.cost_usd, entry.stop_reason, entry.batch,
                now])
            con.execute("INSERT INTO ledger VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
                now, entry.key, entry.model, entry.prompt_version, entry.tag, u.get("input_tokens") or 0,
                u.get("output_tokens") or 0, u.get("cache_read_input_tokens") or 0,
                u.get("cache_creation_input_tokens") or 0, entry.cost_usd, entry.stop_reason, entry.batch])

    # --- batches: remember which custom_id maps to which cache key, so results can be collected later
    def record_batch(self, batch_id: str, items: list[tuple[str, str, str, str, str | None, dict]]) -> None:
        now = datetime.now(UTC)
        with self._connect() as con:
            con.executemany("INSERT INTO batches VALUES (?,?,?,?,?,?,?,?)", [
                [batch_id, cid, key, model, pv, tag, canonical_json(strip_blobs(req)), now]
                for cid, key, model, pv, tag, req in items])

    def batch_items(self, batch_id: str) -> dict[str, dict]:
        with self._connect() as con:
            rows = con.execute("SELECT custom_id, key, model, prompt_version, tag, request_json FROM batches "
                               "WHERE batch_id = ?", [batch_id]).fetchall()
        return {r[0]: {"key": r[1], "model": r[2], "prompt_version": r[3], "tag": r[4],
                       "request": json.loads(r[5])} for r in rows}

    def ledger(self) -> pd.DataFrame:
        with self._connect() as con:
            return con.execute("SELECT * FROM ledger ORDER BY called_at").df()

    def spend_summary(self) -> pd.DataFrame:
        """Total calls, tokens and USD by model / prompt_version / tag."""
        with self._connect() as con:
            return con.execute("""
                SELECT model, prompt_version, tag, count(*) AS calls, sum(input_tokens) AS input_tokens,
                       sum(output_tokens) AS output_tokens, sum(cache_read_tokens) AS cache_read_tokens,
                       round(sum(cost_usd), 4) AS cost_usd
                FROM ledger GROUP BY ALL ORDER BY cost_usd DESC""").df()


if __name__ == "__main__":  # pixi run llm-ledger
    s = Cache().spend_summary()
    print(s.to_string(index=False) if len(s) else "no API calls recorded yet")
    print(f"total: ${s.cost_usd.sum():.4f}" if len(s) else "")
