"""Thin, cached wrapper around the Anthropic Messages + Batches APIs (shared by A4 evidence and A5 labels).

Design choices (why):
- Every call goes through `rtl.llm.cache` (get_or_call), so results are reproducible and never paid twice.
- Responses are stored as plain JSON dicts, so a cache hit and a fresh call look identical to callers.
- The Anthropic SDK object is created lazily, only when a real call is needed: tests and `--dry-run`
  never need a key. Tests inject a fake `api` object with the same `.messages.create` / `.messages.batches`.
- Claude Opus 5.5 rules baked into `build_request`: adaptive thinking (can't be disabled), explicit
  `effort`, no temperature, no assistant prefill, no forced tool_choice.
- Citations and structured outputs can't be combined in one request (400), so the evidence pipeline is
  two-step: `document_block(..., citations=True)` then `json_schema=...` (see rtl.llm.evidence).
- A refusal (`stop_reason == "refusal"`) is checked before content is read: `.text` raises RefusalError.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rtl.llm.cache import Cache, CacheEntry, cache_key

DEFAULT_MODEL = "claude-opus-5-5"

# USD per million tokens (claude-api skill, shared/models.md + prompt-caching.md, checked 2026-10-08).
# cache_read is model-specific (Opus 5.5: 0.05x input); cache writes are 1.25x (5 min TTL) / 2x (1 h TTL).
PRICES: dict[str, dict[str, float]] = {
    "claude-opus-5-5": {"input": 4.0, "output": 20.0, "cache_read": 0.20},
    "claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "cache_read": 0.20},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0, "cache_read": 0.10},
}
CACHE_WRITE_MULT = {"5m": 1.25, "1h": 2.0}
BATCH_DISCOUNT = 0.5  # the Batch API bills all token usage at 50%


class RefusalError(RuntimeError):
    """The model declined (stop_reason == 'refusal'); content must not be used."""


def cost_usd(model: str, usage: dict, batch: bool = False) -> float:
    """Dollar cost of one response from its `usage` block. Unknown model -> KeyError (never silently $0).

    output_tokens already include thinking tokens. Cache writes are split by TTL when the API reports
    `usage.cache_creation`; otherwise they are priced at the 5-minute rate.
    """
    p = PRICES[model]
    cc = usage.get("cache_creation") or {}
    w1h = cc.get("ephemeral_1h_input_tokens") or 0
    w5m = cc.get("ephemeral_5m_input_tokens")
    if w5m is None:
        w5m = (usage.get("cache_creation_input_tokens") or 0) - w1h
    dollars = ((usage.get("input_tokens") or 0) * p["input"]
               + (usage.get("output_tokens") or 0) * p["output"]
               + (usage.get("cache_read_input_tokens") or 0) * p["cache_read"]
               + w5m * p["input"] * CACHE_WRITE_MULT["5m"]
               + w1h * p["input"] * CACHE_WRITE_MULT["1h"]) / 1e6
    return dollars * (BATCH_DISCOUNT if batch else 1.0)


@dataclass
class LLMResult:
    key: str
    model: str
    prompt_version: str
    response: dict
    usage: dict
    cost_usd: float
    stop_reason: str | None
    from_cache: bool
    batch: bool = False
    tag: str | None = None

    @property
    def refused(self) -> bool:
        return self.stop_reason == "refusal"

    @property
    def text(self) -> str:
        """Concatenated text blocks. Raises on refusal, so refused output never flows into data."""
        if self.refused:
            details = self.response.get("stop_details") or {}
            raise RefusalError(f"model refused ({details.get('category')}): {details.get('explanation')}")
        return "".join(b.get("text", "") for b in self.response.get("content", []) if b.get("type") == "text")

    def json(self) -> Any:
        """Parse a structured-output response (output_config.format guarantees one JSON text block)."""
        if self.stop_reason == "max_tokens":
            raise ValueError("response truncated (stop_reason=max_tokens); raise max_tokens")
        return json.loads(self.text)

    def citations(self) -> list[dict]:
        """Text blocks with their citations: [{'text': ..., 'citations': [...]}, ...] (raises on refusal)."""
        _ = self.text
        return [{"text": b.get("text", ""), "citations": b.get("citations") or []}
                for b in self.response.get("content", []) if b.get("type") == "text"]


def document_block(pdf: bytes | Path, title: str | None = None, citations: bool = True,
                   cache: bool = False) -> dict:
    """A base64 PDF document content block (optionally with citations and a prompt-cache breakpoint)."""
    data = pdf.read_bytes() if isinstance(pdf, Path) else pdf
    block: dict[str, Any] = {"type": "document",
                             "source": {"type": "base64", "media_type": "application/pdf",
                                        "data": base64.standard_b64encode(data).decode()}}
    if title:
        block["title"] = title
    if citations:
        block["citations"] = {"enabled": True}
    if cache:
        block["cache_control"] = {"type": "ephemeral"}
    return block


def build_request(user_content: str | list, *, system: str | None = None, model: str = DEFAULT_MODEL,
                  max_tokens: int = 16000, effort: str = "medium", json_schema: dict | None = None) -> dict:
    """Messages API params with the project defaults. `json_schema` turns on structured outputs."""
    req: dict[str, Any] = {"model": model, "max_tokens": max_tokens,
                           "thinking": {"type": "adaptive"},
                           "output_config": {"effort": effort},
                           "messages": [{"role": "user", "content": user_content}]}
    if system:
        req["system"] = system
    if json_schema:
        req["output_config"]["format"] = {"type": "json_schema", "schema": json_schema}
    return req


def _to_dict(msg: Any) -> dict:
    return msg if isinstance(msg, dict) else msg.model_dump(mode="json")


@dataclass
class LLMClient:
    """Cached client. `api` defaults to anthropic.Anthropic() built from rtl.settings when first needed."""

    api: Any = None
    cache: Cache = field(default_factory=Cache)

    def _api(self) -> Any:
        if self.api is None:
            import anthropic

            from rtl.settings import settings
            key = settings().anthropic_api_key
            if key is None or not key.get_secret_value():
                raise RuntimeError("ANTHROPIC_API_KEY is not set (.env). Use --dry-run to estimate cost.")
            self.api = anthropic.Anthropic(api_key=key.get_secret_value())
        return self.api

    def _result(self, entry: CacheEntry, from_cache: bool) -> LLMResult:
        return LLMResult(entry.key, entry.model, entry.prompt_version, entry.response, entry.usage,
                         entry.cost_usd, entry.stop_reason, from_cache, entry.batch, entry.tag)

    def get_or_call(self, request: dict, prompt_version: str, tag: str | None = None,
                    refresh: bool = False) -> LLMResult:
        """Return the cached response for this exact request, or call the API once and cache it."""
        model = request["model"]
        key = cache_key(model, prompt_version, request)
        if not refresh and (hit := self.cache.get(key)) is not None:
            return self._result(hit, from_cache=True)
        resp = _to_dict(self._api().messages.create(**request))
        usage = resp.get("usage") or {}
        entry = CacheEntry(key, model, prompt_version, tag, resp, usage, cost_usd(model, usage),
                           resp.get("stop_reason"), batch=False)
        self.cache.put(entry, request)
        return self._result(entry, from_cache=False)

    # ------------------------------------------------------------------ Batch API (50% off, async)
    def submit_batch(self, items: dict[str, dict], prompt_version: str, tag: str | None = None) -> str | None:
        """Submit {custom_id: request}; requests already cached are skipped. Returns batch id (None if all cached).

        The custom_id -> cache key mapping is stored in the cache DB, so `collect_batch` works from a new process.
        """
        todo = {}
        for cid, req in items.items():
            key = cache_key(req["model"], prompt_version, req)
            if self.cache.get(key) is None:
                todo[cid] = (key, req)
        if not todo:
            return None
        batch = self._api().messages.batches.create(
            requests=[{"custom_id": cid, "params": req} for cid, (_, req) in todo.items()])
        self.cache.record_batch(batch.id, [(cid, key, req["model"], prompt_version, tag, req)
                                           for cid, (key, req) in todo.items()])
        return batch.id

    def poll_batch(self, batch_id: str, every_s: float = 60, timeout_s: float = 24 * 3600) -> str:
        t0 = time.time()
        while True:
            status = self._api().messages.batches.retrieve(batch_id).processing_status
            if status == "ended" or time.time() - t0 > timeout_s:
                return status
            time.sleep(every_s)

    def collect_batch(self, batch_id: str) -> dict[str, LLMResult | str]:
        """Store succeeded results in the cache (priced with the batch discount).

        Returns {custom_id: LLMResult} for successes and {custom_id: '<error type>'} for the rest.
        """
        meta = self.cache.batch_items(batch_id)
        out: dict[str, LLMResult | str] = {}
        for r in self._api().messages.batches.results(batch_id):
            m = meta[r.custom_id]
            if r.result.type != "succeeded":
                out[r.custom_id] = r.result.type
                continue
            resp = _to_dict(r.result.message)
            usage = resp.get("usage") or {}
            entry = CacheEntry(m["key"], m["model"], m["prompt_version"], m["tag"], resp, usage,
                               cost_usd(m["model"], usage, batch=True), resp.get("stop_reason"), batch=True)
            self.cache.put(entry, m["request"])
            out[r.custom_id] = self._result(entry, from_cache=False)
        return out
