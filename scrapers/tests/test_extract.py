import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def legacy_provider(monkeypatch):
    # Retained OpenRouter protocol/retry tests; Claude default is in test_claude.py.
    monkeypatch.setenv("TB_LLM_PROVIDER", "openrouter")
    monkeypatch.setattr("toronto_bids.extract.MODELS", ["test/first", "test/second"])


def _load_fixture(name="extract_response.json"):
    return json.loads((FIXTURES / name).read_text())


class _FakeResp:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


@pytest.mark.parametrize(
    "env, expected",
    [
        ({"OPENROUTER_API_KEY": "your-openrouter-api-key"}, None),
        ({"OPENROUTER_API_KEY": "sk-or-v1-abc123"}, "sk-or-v1-abc123"),
        ({}, None),
    ],
)
def test_real_openrouter_key(env, expected):
    from toronto_bids.extract import _real_openrouter_key

    with patch.dict("os.environ", env, clear=True):
        assert _real_openrouter_key() == expected


def test_validate_extraction_accepts_good_response():
    from toronto_bids.extract import validate_extraction

    fixture = _load_fixture()
    content = json.loads(fixture["choices"][0]["message"]["content"])
    result = validate_extraction(content)
    assert len(result["contracts"]) == 1
    c = result["contracts"][0]
    assert c["reference"] == "RFT 10041234"
    assert len(c["bids"]) == 3
    assert c["bids"][0]["supplier_name"] == "Acme Roofing Ltd."
    assert c["declared_submissions"] == 3
    assert len(c["awards"]) == 1


@pytest.mark.parametrize(
    "bad, match",
    [
        ("just a string", "not a JSON object"),
        ({"not_contracts": []}, "contracts"),
        ({"contracts": None}, "contracts"),
        ({"contracts": ["not a dict"]}, "not a dict"),
        ({"contracts": [{"reference": "R", "bids": None}]}, "bids"),
        ({"contracts": [{"reference": "R", "awards": None}]}, "awards"),
        ({"contracts": [{"reference": "R", "bids": ["Acme"]}]}, "Bid"),
        ({"contracts": [{"reference": "R", "bids": [{"amount_raw": "$100"}], "awards": []}]}, "supplier_name"),
        ({"contracts": [{"reference": "R", "awards": ["Acme"]}]}, "Award"),
        ({"contracts": [{"reference": "R", "awards": [{"amount_raw": "$100"}]}]}, "supplier_name"),
        ({"contracts": [{"reference": "R", "awards": [{"supplier_name": ""}]}]}, "supplier_name"),
    ],
)
def test_validate_extraction_rejects_invalid_shapes(bad, match):
    from toronto_bids.extract import validate_extraction

    with pytest.raises(ValueError, match=match):
        validate_extraction(bad)


def test_parse_llm_response_extracts_content():
    from toronto_bids.extract import parse_llm_response

    result = parse_llm_response(_load_fixture())
    assert len(result["contracts"]) == 1


def test_parse_llm_response_rejects_malformed_json():
    from toronto_bids.extract import parse_llm_response

    fixture = _load_fixture()
    fixture["choices"][0]["message"]["content"] = "not json at all"
    with pytest.raises(ValueError, match="malformed"):
        parse_llm_response(fixture)


def test_parse_llm_response_strips_markdown_fence():
    from toronto_bids.extract import parse_llm_response

    fixture = _load_fixture()
    inner = fixture["choices"][0]["message"]["content"]
    fixture["choices"][0]["message"]["content"] = f"```json\n{inner}\n```"
    result = parse_llm_response(fixture)
    assert len(result["contracts"]) == 1


@pytest.mark.parametrize(
    "env, kwargs, match",
    [
        ({}, {"provider": "openrouter"}, "OPENROUTER_API_KEY"),
        ({"OPENROUTER_API_KEY": "your-openrouter-api-key"}, {}, "placeholder"),
        ({"TB_LLM_PROVIDER": "unknown"}, {}, "Unknown TB_LLM_PROVIDER"),
    ],
)
def test_client_init_validation(env, kwargs, match):
    from toronto_bids.extract import ExtractionClient

    with (
        patch.dict("os.environ", env, clear=True),
        pytest.raises(ValueError, match=match),
    ):
        ExtractionClient(**kwargs)


def test_client_extract_calls_openrouter(monkeypatch):
    from toronto_bids.extract import ExtractionClient

    fixture = _load_fixture()
    calls = []

    def mock_post(url, *, json, headers, timeout):
        calls.append({"url": url, "model": json["model"], "headers": headers})
        return _FakeResp(fixture)

    monkeypatch.setattr(httpx, "post", mock_post)
    client = ExtractionClient(api_key="sk-or-v1-test123")
    result = client.extract("Some document text")
    assert len(result["contracts"]) == 1
    assert calls[0]["model"] == "test/first"
    assert "Bearer sk-or-v1-test123" in str(calls)


def test_client_extract_document_merges_chunks(monkeypatch):
    from toronto_bids.extract import ExtractionClient

    chunk_outputs = [
        {"contracts": [{"reference": "RFT 1", "bids": [{"supplier_name": "A"}], "awards": []}]},
        {
            "contracts": [
                {
                    "reference": "RFT 1",
                    "bids": [{"supplier_name": "A"}, {"supplier_name": "B"}],
                    "awards": [],
                },
                {"reference": "RFT 2", "bids": [{"supplier_name": "C"}], "awards": []},
            ]
        },
    ]

    def mock_post(url, **kwargs):
        payload = {"choices": [{"message": {"content": json.dumps(chunk_outputs.pop(0))}}]}
        return _FakeResp(payload)

    monkeypatch.setattr(httpx, "post", mock_post)
    client = ExtractionClient(api_key="sk-or-v1-test123")
    result = client.extract_document("sha1", ["chunk one", "chunk two"])
    assert len(result["contracts"]) == 2
    by_ref = {c["reference"]: c for c in result["contracts"]}
    assert [b["supplier_name"] for b in by_ref["RFT 1"]["bids"]] == ["A", "B"]
    assert [b["supplier_name"] for b in by_ref["RFT 2"]["bids"]] == ["C"]


def test_client_falls_back_to_second_model(monkeypatch):
    from toronto_bids.extract import ExtractionClient

    fixture = _load_fixture()
    calls = []

    def mock_post(url, *, json, headers, timeout):
        calls.append(json["model"])
        if json["model"] == "test/first":
            resp = httpx.Response(status_code=429)
            raise httpx.HTTPStatusError("rate limited", request=None, response=resp)
        return _FakeResp(fixture)

    monkeypatch.setattr(httpx, "post", mock_post)
    client = ExtractionClient(api_key="sk-or-v1-test123", retries=0, backoff=0)
    result = client.extract("Some document text")
    assert len(result["contracts"]) == 1
    assert calls == ["test/first", "test/second"]


def test_client_retries_on_500(monkeypatch):
    from toronto_bids.extract import ExtractionClient

    fixture = _load_fixture()
    attempts = 0

    def mock_post(url, *, json, headers, timeout):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            resp = httpx.Response(status_code=500)
            raise httpx.HTTPStatusError("server error", request=None, response=resp)
        return _FakeResp(fixture)

    monkeypatch.setattr(httpx, "post", mock_post)
    client = ExtractionClient(api_key="sk-or-v1-test123", backoff=0)
    result = client.extract("Some document text")
    assert len(result["contracts"]) == 1
    assert attempts == 2


def _status_then_fixture(monkeypatch, statuses):
    import time

    fixture = _load_fixture()
    calls = []
    sleeps = []

    def mock_post(url, *, json, headers, timeout):
        calls.append(json["model"])
        status = statuses.get(json["model"])
        if status is not None:
            resp = httpx.Response(status_code=status)
            raise httpx.HTTPStatusError(f"status {status}", request=None, response=resp)
        return _FakeResp(fixture)

    monkeypatch.setattr(httpx, "post", mock_post)
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    return calls, sleeps


@pytest.mark.parametrize("status", [404, 402])
def test_client_falls_back_on_non_auth_4xx_without_retrying(monkeypatch, status):
    from toronto_bids.extract import MODELS, ExtractionClient

    calls, sleeps = _status_then_fixture(monkeypatch, {MODELS[0]: status})
    client = ExtractionClient(api_key="sk-or-v1-test123", retries=3, backoff=1.0)
    assert len(client.extract("Some document text")["contracts"]) == 1
    assert calls == [MODELS[0], MODELS[1]]
    assert sleeps == []


@pytest.mark.parametrize("status", [401, 403])
def test_client_auth_error_raises_without_fallback(monkeypatch, status):
    from toronto_bids.extract import MODELS, ExtractionClient

    calls, sleeps = _status_then_fixture(monkeypatch, {MODELS[0]: status})
    client = ExtractionClient(api_key="sk-or-v1-test123", retries=3, backoff=1.0)
    with pytest.raises(httpx.HTTPStatusError) as info:
        client.extract("Some document text")
    assert info.value.response.status_code == status
    assert calls == [MODELS[0]]
    assert sleeps == []


def test_client_every_model_4xx_raises_last_error(monkeypatch):
    from toronto_bids.extract import MODELS, ExtractionClient

    calls, sleeps = _status_then_fixture(monkeypatch, {MODELS[0]: 404, MODELS[1]: 402})
    client = ExtractionClient(api_key="sk-or-v1-test123", retries=3, backoff=1.0)
    with pytest.raises(httpx.HTTPStatusError) as info:
        client.extract("Some document text")
    assert info.value.response.status_code == 402
    assert calls == [MODELS[0], MODELS[1]]
    assert sleeps == []


def test_client_sets_flex_tier_for_openai(monkeypatch):
    from toronto_bids.extract import ExtractionClient

    fixture = _load_fixture()
    captured = {}

    def mock_post(url, *, json, headers, timeout):
        captured.update(json)
        return _FakeResp(fixture)

    monkeypatch.setattr(httpx, "post", mock_post)
    client = ExtractionClient(
        api_key="sk-or-v1-test123", models=["openai/gpt-5.6-luna"]
    )
    client.extract("text")
    assert captured["service_tier"] == "flex"


def test_cli_extract_dry_run_prints_result(monkeypatch, conn):
    from toronto_bids.cli import main
    from toronto_bids.extract import ExtractionClient, parse_llm_response

    conn.execute(
        "INSERT INTO background_pdf (url, reference, kind, sha256, text) "
        "VALUES ('http://example.com/report.pdf', '2025.BA1.1', 'bgrd', 'deadbeef', "
        "'Some report text about a contract')"
    )
    conn.commit()

    fixture = _load_fixture()
    monkeypatch.setattr(ExtractionClient, "extract", lambda self, text: parse_llm_response(fixture))
    monkeypatch.setattr(ExtractionClient, "__init__", lambda self, **kw: None)
    monkeypatch.setattr("toronto_bids.cli._open_db", lambda: conn)

    assert main(["extract", "--dry-run", "deadbeef"]) == 0


def test_cli_extract_dry_run_missing_sha256(monkeypatch, conn):
    from toronto_bids.cli import main

    monkeypatch.setattr("toronto_bids.cli._open_db", lambda: conn)
    assert main(["extract", "--dry-run", "nonexistent"]) == 1
