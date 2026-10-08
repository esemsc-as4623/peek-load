"""Client + cache tests with a fake API (no network, no key). Fake responses are real SDK Message objects."""

from types import SimpleNamespace

import pytest
from anthropic.types import Message

from rtl.llm.cache import Cache, cache_key
from rtl.llm.client import LLMClient, RefusalError, build_request, cost_usd, document_block


def make_message(text="hello", stop_reason="end_turn", usage=None, citations=None):
    block = {"type": "text", "text": text}
    if citations:
        block["citations"] = citations
    return Message.model_validate({
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": [] if stop_reason == "refusal" else [block], "stop_reason": stop_reason,
        "stop_sequence": None, "usage": usage or {"input_tokens": 1000, "output_tokens": 500}})


class FakeMessages:
    def __init__(self, reply):
        self.reply, self.calls = reply, []
        self.batches = FakeBatches()

    def create(self, **params):
        self.calls.append(params)
        return self.reply


class FakeBatches:
    def __init__(self):
        self.submitted = []

    def create(self, requests):
        self.submitted.append(requests)
        return SimpleNamespace(id="batch_1")

    def retrieve(self, batch_id):
        return SimpleNamespace(processing_status="ended")

    def results(self, batch_id):
        for i, r in enumerate(self.submitted[-1]):
            if i == 0:
                yield SimpleNamespace(custom_id=r["custom_id"], result=SimpleNamespace(
                    type="succeeded", message=make_message("batched")))
            else:
                yield SimpleNamespace(custom_id=r["custom_id"], result=SimpleNamespace(type="errored"))


@pytest.fixture
def client(tmp_path):
    return LLMClient(api=SimpleNamespace(messages=FakeMessages(make_message())), cache=Cache(tmp_path / "c.duckdb"))


# ------------------------------------------------------------------ cache key
def test_cache_key_is_deterministic_and_order_independent():
    a = {"model": "m", "max_tokens": 10, "messages": [{"role": "user", "content": "x"}]}
    b = {"messages": [{"content": "x", "role": "user"}], "max_tokens": 10, "model": "m"}
    assert cache_key("m", "v1", a) == cache_key("m", "v1", b)


def test_cache_key_sensitive_to_content_model_and_prompt_version():
    req = build_request("count the TVs")
    base = cache_key("claude-opus-5-5", "evidence_v1", req)
    assert cache_key("claude-opus-5-5", "evidence_v2", req) != base  # prompt-version bump
    assert cache_key("claude-sonnet-5-5", "evidence_v1", req) != base
    assert cache_key("claude-opus-5-5", "evidence_v1", build_request("count the fridges")) != base
    assert cache_key("claude-opus-5-5", "evidence_v1", build_request("count the TVs", effort="high")) != base


def test_pdf_blob_hashed_into_key():
    k1 = cache_key("m", "v", build_request([document_block(b"%PDF-1 one")]))
    k2 = cache_key("m", "v", build_request([document_block(b"%PDF-1 two")]))
    assert k1 != k2


# ------------------------------------------------------------------ cost accounting
def test_cost_hand_computed():
    u = {"input_tokens": 1_000_000, "output_tokens": 100_000}
    assert cost_usd("claude-opus-5-5", u) == pytest.approx(4.0 + 2.0)
    assert cost_usd("claude-opus-5-5", u, batch=True) == pytest.approx(3.0)
    u = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 1_000_000,
         "cache_creation_input_tokens": 1_000_000}
    assert cost_usd("claude-opus-5-5", u) == pytest.approx(0.20 + 4.0 * 1.25)
    u["cache_creation"] = {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 1_000_000}
    assert cost_usd("claude-opus-5-5", u) == pytest.approx(0.20 + 8.0)
    assert cost_usd("claude-haiku-4-5", {"input_tokens": 2_000_000, "output_tokens": 0}) == pytest.approx(2.0)


def test_unknown_model_cost_raises():
    with pytest.raises(KeyError):
        cost_usd("claude-made-up", {"input_tokens": 1})


# ------------------------------------------------------------------ get_or_call
def test_second_call_is_cache_hit_and_ledger_counts_once(client):
    req = build_request("hi")
    r1 = client.get_or_call(req, "t_v1", tag="test")
    r2 = client.get_or_call(req, "t_v1", tag="test")
    assert not r1.from_cache and r2.from_cache
    assert r2.text == "hello" and len(client.api.messages.calls) == 1
    led = client.cache.ledger()
    assert len(led) == 1
    assert led.cost_usd.iloc[0] == pytest.approx((1000 * 4 + 500 * 20) / 1e6)
    client.get_or_call(req, "t_v2")  # new prompt version -> new call
    assert len(client.api.messages.calls) == 2


def test_request_follows_opus_rules():
    req = build_request("x", json_schema={"type": "object"})
    assert req["thinking"] == {"type": "adaptive"} and req["output_config"]["effort"] == "medium"
    assert "temperature" not in req and "tool_choice" not in req
    assert req["output_config"]["format"]["type"] == "json_schema"


def test_refusal_is_flagged_and_content_never_read(tmp_path):
    c = LLMClient(api=SimpleNamespace(messages=FakeMessages(make_message(stop_reason="refusal"))),
                  cache=Cache(tmp_path / "c.duckdb"))
    r = c.get_or_call(build_request("x"), "v1")
    assert r.refused
    with pytest.raises(RefusalError):
        _ = r.text
    with pytest.raises(RefusalError):
        r.json()
    assert c.cache.ledger().stop_reason.iloc[0] == "refusal"  # still paid for, still logged


def test_structured_json_and_citations(tmp_path):
    cit = [{"type": "page_location", "cited_text": "TV 14.8%", "document_index": 0, "document_title": "d",
            "start_page_number": 3, "end_page_number": 4}]
    c = LLMClient(api=SimpleNamespace(messages=FakeMessages(make_message('{"rows": []}', citations=cit))),
                  cache=Cache(tmp_path / "c.duckdb"))
    r = c.get_or_call(build_request("x"), "v1")
    assert r.json() == {"rows": []}
    assert r.citations()[0]["citations"][0]["start_page_number"] == 3


def test_missing_key_gives_clear_error(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    from rtl.settings import settings
    settings.cache_clear()
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        LLMClient(cache=Cache(tmp_path / "c.duckdb")).get_or_call(build_request("x"), "v1")
    settings.cache_clear()


# ------------------------------------------------------------------ batches
def test_batch_skips_cached_and_collects_with_discount(client):
    cached = build_request("already")
    client.get_or_call(cached, "v1")
    items = {"a": build_request("new a"), "b": build_request("new b"), "c": cached}
    bid = client.submit_batch(items, "v1", tag="t")
    assert bid == "batch_1"
    assert [r["custom_id"] for r in client.api.messages.batches.submitted[0]] == ["a", "b"]
    out = client.collect_batch(bid)
    assert out["b"] == "errored" and out["a"].text == "batched" and out["a"].batch
    assert out["a"].cost_usd == pytest.approx((1000 * 4 + 500 * 20) / 1e6 / 2)
    hit = client.get_or_call(items["a"], "v1")  # collected result now served from cache
    assert hit.from_cache and hit.text == "batched"
