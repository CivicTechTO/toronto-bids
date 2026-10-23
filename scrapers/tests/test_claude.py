"""Direct-provider and money-boundary tests; never call a paid API."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import httpx
import pytest

from toronto_bids.claude import ClaudeExtractionClient, _charge
from toronto_bids.extract import EXTRACTOR_VERSION, ExtractionClient, build_prompt
from toronto_bids.llm_budget import BudgetExceeded, UsageBudget


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr("toronto_bids.config.DATA_DIR", tmp_path)
    monkeypatch.delenv("TB_CLAUDE_MODEL", raising=False)
    monkeypatch.delenv("TB_CLAUDE_EFFORT", raising=False)
    monkeypatch.delenv("TB_LLM_MONTHLY_USD", raising=False)
    return ClaudeExtractionClient(api_key="test-key", retries=0)


def response(stop="end_turn", text='{"contracts": []}'):
    return {
        "stop_reason": stop,
        "content": [{"type": "thinking", "thinking": "ignored"}, {"type": "text", "text": text}],
        "usage": {"input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 1000},
    }


def posted(monkeypatch, data, status=200):
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(status, json=data, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    return calls


def rows(client):
    with sqlite3.connect(client.budget.path) as conn:
        return conn.execute("SELECT charged_microdollars, status FROM request").fetchall()


def test_default_uses_direct_key_and_never_openrouter(monkeypatch, tmp_path):
    monkeypatch.setattr("toronto_bids.config.DATA_DIR", tmp_path)
    with patch.dict("os.environ", {"OPENROUTER_API_KEY": "legacy-key"}, clear=True):
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            ExtractionClient()
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "direct-key"}):
            assert isinstance(ExtractionClient()._claude, ClaudeExtractionClient)


def test_request_preserves_prompt_and_cache_version(client, monkeypatch):
    calls = posted(monkeypatch, response())
    assert client.extract("Public report") == {"contracts": []}
    url, kwargs = calls[0]
    assert url == "https://api.anthropic.com/v1/messages"
    body = kwargs["json"]
    assert body["model"] == "claude-sonnet-5-5"
    assert body["max_tokens"] == 32768
    assert "temperature" not in body
    # Prompting alone allowed prose and fenced JSON in a real July 2022 response.
    assert body["output_config"]["effort"] == "high"
    fmt = body["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    schema = fmt["schema"]
    assert schema["required"] == ["contracts"]
    assert schema["additionalProperties"] is False
    contract = schema["properties"]["contracts"]["items"]
    assert {"reference", "bids", "awards", "prequalified"} <= set(contract["required"])
    assert contract["properties"]["awards"]["items"]["additionalProperties"] is False
    blocks = body["messages"][0]["content"]
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in blocks[1]
    assert "".join(b["text"] for b in blocks) == build_prompt("Public report")
    assert EXTRACTOR_VERSION == "v1-b63aa8c835ae"
    assert kwargs["headers"]["x-api-key"] == "test-key"
    assert rows(client) == [(4100, "reported")]


@pytest.mark.parametrize("stop,text", [
    ("max_tokens", '{"contracts": []}'),
    ("refusal", '{"contracts": []}'),
    ("end_turn", "not JSON"),
    ("end_turn", '{"contracts": [{"bids": null}]}'),
])
def test_billed_bad_outputs_refused_and_accounted(client, monkeypatch, stop, text):
    posted(monkeypatch, response(stop, text))
    with pytest.raises(ValueError):
        client.extract("report")
    assert rows(client) == [(4100, "reported")]


def test_timeout_reservation_survives_process_restart(client, monkeypatch):
    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("response lost")
    monkeypatch.setattr(httpx, "post", timeout)
    with pytest.raises(httpx.ReadTimeout):
        client.extract("report")
    charge, state = rows(client)[0]
    assert state == "reserved" and charge > 327680
    budget = UsageBudget(client.budget.path, str(charge / 1_000_000))
    with pytest.raises(BudgetExceeded):
        budget.reserve("claude-sonnet-5-5", 1)


def test_definite_rejection_releases_budget_without_auth_retry(client, monkeypatch):
    calls = posted(monkeypatch, {"error": "bad key"}, 401)
    client.retries = 3
    with pytest.raises(httpx.HTTPStatusError):
        client.extract("report")
    assert len(calls) == 1
    assert rows(client) == [(0, "rejected")]


def test_budget_blocks_before_network_without_retry(client, monkeypatch):
    client.budget.limit = 0
    calls = posted(monkeypatch, response())
    client.retries = 3
    with pytest.raises(BudgetExceeded):
        client.extract("report")
    assert not calls
    assert rows(client) == []


def test_budget_reservations_are_atomic(tmp_path):
    budget = UsageBudget(tmp_path / "ledger.sqlite", "1")
    # Initialize schema before contending on the reservations.
    budget.reserve("test", 0)
    def reserve(_):
        try:
            budget.reserve("test", 600000)
            return True
        except BudgetExceeded:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve, range(2))) == [False, True]


def test_previous_month_does_not_consume_current_budget(tmp_path):
    budget = UsageBudget(tmp_path / "ledger.sqlite", "1")
    token = budget.reserve("test", 1000000)
    with sqlite3.connect(budget.path) as conn:
        conn.execute("UPDATE request SET month='2000-01' WHERE id=?", (token,))
    budget.reserve("test", 1000000)


def test_haiku_tiering_includes_cached_input():
    assert _charge("claude-haiku-5-5", {"input_tokens": 100, "cache_read_input_tokens": 100000, "output_tokens": 1000}) == 7550


@pytest.mark.parametrize("limit", ["NaN", "Infinity", "-1", "not a number"])
def test_invalid_budget_rejected(tmp_path, limit):
    with pytest.raises(ValueError):
        UsageBudget(tmp_path / "ledger.sqlite", limit)


def test_unpriced_model_refused_before_any_call(client):
    with pytest.raises(ValueError, match="reviewed price"):
        ClaudeExtractionClient(api_key="test", models=["unknown-expensive-model"])


@pytest.mark.parametrize("payload", [
    {"stop_reason": "end_turn", "content": None},
    {"stop_reason": "end_turn", "content": ["unexpected"]},
    {"stop_reason": "end_turn", "content": [{"type": "text", "text": None}]},
])
def test_bad_content_is_billed_and_refused(client, monkeypatch, payload):
    payload["usage"] = response()["usage"]
    posted(monkeypatch, payload)
    with pytest.raises(ValueError):
        client.extract("report")
    assert rows(client) == [(4100, "reported")]


def test_unusable_usage_retains_allowance(client, monkeypatch):
    data = response()
    data["usage"] = None
    posted(monkeypatch, data)
    with pytest.raises(ValueError, match="usage"):
        client.extract("report")
    assert rows(client)[0][1] == "reserved"


def test_retry_accounts_for_both_billed_attempts(client, monkeypatch):
    calls = []
    def post(url, **kwargs):
        calls.append(url)
        data = response(text="invalid" if len(calls) == 1 else '{"contracts": []}')
        return httpx.Response(200, json=data, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    client.retries = 1
    client.backoff = 0
    assert client.extract("report") == {"contracts": []}
    assert rows(client) == [(4100, "reported"), (4100, "reported")]


def test_schema_meets_claude_compilation_limits():
    from toronto_bids.claude import EXTRACTION_SCHEMA

    unions = 0
    def check(node):
        nonlocal unions
        if isinstance(node, dict):
            if isinstance(node.get("type"), list) or "anyOf" in node:
                unions += 1
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            # Nullable enums need a typed anyOf branch in the Claude validator.
            if "enum" in node:
                assert isinstance(node["type"], str)
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)
    check(EXTRACTION_SCHEMA)
    assert unions <= 16


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_effort_can_be_tuned_without_changing_prompt_or_model(client, monkeypatch, effort):
    monkeypatch.setenv("TB_CLAUDE_EFFORT", effort)
    calls = posted(monkeypatch, response())
    tuned = ClaudeExtractionClient(api_key="test", models=["claude-haiku-5-5"], retries=0)
    assert tuned.extract("report") == {"contracts": []}
    body = calls[0][1]["json"]
    assert body["model"] == "claude-haiku-5-5"
    assert body["output_config"]["effort"] == effort
    assert "".join(b["text"] for b in body["messages"][0]["content"]) == build_prompt("report")


def test_invalid_effort_is_rejected_before_sending(client, monkeypatch):
    monkeypatch.setenv("TB_CLAUDE_EFFORT", "unlimited")
    calls = posted(monkeypatch, response())
    with pytest.raises(ValueError, match="TB_CLAUDE_EFFORT"):
        ClaudeExtractionClient(api_key="test")
    assert not calls
