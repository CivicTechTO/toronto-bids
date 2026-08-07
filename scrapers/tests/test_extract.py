"""Tests for the LLM extraction client — offline, fixture-based, no network."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name="extract_response.json"):
    return json.loads((FIXTURES / name).read_text())


# ── config ──


def test_placeholder_api_key_reads_as_none():
    from toronto_bids.extract import _real_openrouter_key

    with patch.dict("os.environ", {"OPENROUTER_API_KEY": "your-openrouter-api-key"}):
        assert _real_openrouter_key() is None


def test_real_api_key_reads_through():
    from toronto_bids.extract import _real_openrouter_key

    with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-v1-abc123"}):
        assert _real_openrouter_key() == "sk-or-v1-abc123"


def test_missing_api_key_reads_as_none():
    from toronto_bids.extract import _real_openrouter_key

    with patch.dict("os.environ", {}, clear=True):
        assert _real_openrouter_key() is None


# ── schema validation ──


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


def test_validate_extraction_rejects_missing_contracts():
    from toronto_bids.extract import validate_extraction

    with pytest.raises(ValueError, match="contracts"):
        validate_extraction({"not_contracts": []})


def test_validate_extraction_rejects_bid_without_supplier():
    from toronto_bids.extract import validate_extraction

    bad = {
        "contracts": [
            {
                "reference": "RFT 123",
                "bids": [{"amount_raw": "$100"}],
                "awards": [],
            }
        ]
    }
    with pytest.raises(ValueError, match="supplier_name"):
        validate_extraction(bad)


def test_validate_extraction_rejects_non_dict():
    from toronto_bids.extract import validate_extraction

    with pytest.raises(TypeError, match="not a JSON object"):
        validate_extraction("just a string")


# ── response parsing ──


def test_parse_llm_response_extracts_content():
    from toronto_bids.extract import parse_llm_response

    fixture = _load_fixture()
    result = parse_llm_response(fixture)
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


# ── prompt ──


def test_prompt_contains_key_rules():
    from toronto_bids.extract import build_prompt

    prompt = build_prompt("Some document text here")
    assert "pre-qualification" in prompt.lower() or "pre-qualified" in prompt.lower()
    assert "disqualified" in prompt.lower()
    assert "supplier_name" in prompt
    assert "declared_submissions" in prompt


# ── extractor version ──


def test_extractor_version_is_stable():
    from toronto_bids.extract import EXTRACTOR_VERSION

    assert isinstance(EXTRACTOR_VERSION, str)
    assert len(EXTRACTOR_VERSION) > 0


# ── ExtractionClient ──


def test_client_raises_on_missing_key():
    from toronto_bids.extract import ExtractionClient

    with (
        patch.dict("os.environ", {}, clear=True),
        pytest.raises(ValueError, match="OPENROUTER_API_KEY"),
    ):
        ExtractionClient()


def test_client_raises_on_placeholder_key():
    from toronto_bids.extract import ExtractionClient

    with (
        patch.dict("os.environ", {"OPENROUTER_API_KEY": "your-openrouter-api-key"}),
        pytest.raises(ValueError, match="placeholder"),
    ):
        ExtractionClient()


def test_client_extract_calls_openrouter(monkeypatch):
    from toronto_bids.extract import ExtractionClient

    fixture = _load_fixture()
    calls = []

    def mock_post(url, *, json, headers, timeout):
        calls.append({"url": url, "model": json["model"], "headers": headers})

        class FakeResp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return fixture

        return FakeResp()

    import httpx

    monkeypatch.setattr(httpx, "post", mock_post)
    client = ExtractionClient(api_key="sk-or-v1-test123")
    result = client.extract("Some document text")
    assert len(result["contracts"]) == 1
    assert calls[0]["model"] == "nvidia/nemotron-3-ultra-253b-v1:free"
    assert "Bearer sk-or-v1-test123" in str(calls)


def test_client_falls_back_to_second_model(monkeypatch):
    from toronto_bids.extract import ExtractionClient

    fixture = _load_fixture()
    calls = []

    def mock_post(url, *, json, headers, timeout):
        calls.append(json["model"])
        if json["model"].startswith("nvidia/"):
            import httpx

            resp = httpx.Response(status_code=429)
            raise httpx.HTTPStatusError("rate limited", request=None, response=resp)

        class FakeResp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return fixture

        return FakeResp()

    import httpx

    monkeypatch.setattr(httpx, "post", mock_post)
    client = ExtractionClient(api_key="sk-or-v1-test123", retries=0, backoff=0)
    result = client.extract("Some document text")
    assert len(result["contracts"]) == 1
    assert "nvidia/nemotron-3-ultra-253b-v1:free" in calls
    assert "openai/gpt-5.6-luna" in calls


def test_client_retries_on_500(monkeypatch):
    from toronto_bids.extract import ExtractionClient

    fixture = _load_fixture()
    attempt_count = [0]

    def mock_post(url, *, json, headers, timeout):
        attempt_count[0] += 1
        if attempt_count[0] == 1:
            import httpx

            resp = httpx.Response(status_code=500)
            raise httpx.HTTPStatusError("server error", request=None, response=resp)

        class FakeResp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return fixture

        return FakeResp()

    import httpx

    monkeypatch.setattr(httpx, "post", mock_post)
    client = ExtractionClient(api_key="sk-or-v1-test123", backoff=0)
    result = client.extract("Some document text")
    assert len(result["contracts"]) == 1
    assert attempt_count[0] == 2


def test_client_does_not_retry_4xx(monkeypatch):
    from toronto_bids.extract import ExtractionClient

    def mock_post(url, *, json, headers, timeout):
        import httpx

        resp = httpx.Response(status_code=401)
        raise httpx.HTTPStatusError("unauthorized", request=None, response=resp)

    import httpx

    monkeypatch.setattr(httpx, "post", mock_post)
    client = ExtractionClient(api_key="sk-or-v1-test123", backoff=0)
    with pytest.raises(httpx.HTTPStatusError):
        client.extract("Some document text")


def test_client_sets_flex_tier_for_openai(monkeypatch):
    from toronto_bids.extract import ExtractionClient

    fixture = _load_fixture()
    captured = {}

    def mock_post(url, *, json, headers, timeout):
        captured.update(json)

        class FakeResp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return fixture

        return FakeResp()

    import httpx

    monkeypatch.setattr(httpx, "post", mock_post)
    client = ExtractionClient(
        api_key="sk-or-v1-test123", models=["openai/gpt-5.6-luna"]
    )
    client.extract("text")
    assert captured["service_tier"] == "flex"


# ── CLI dry-run ──


def test_cli_extract_dry_run_prints_result(monkeypatch, conn):
    from toronto_bids.cli import main

    conn.execute(
        "INSERT INTO background_pdf (url, reference, kind, sha256, text) "
        "VALUES ('http://example.com/report.pdf', '2025.BA1.1', 'bgrd', 'deadbeef', "
        "'Some report text about a contract')"
    )
    conn.commit()

    fixture = _load_fixture()

    def mock_extract(self, text):
        from toronto_bids.extract import parse_llm_response

        return parse_llm_response(fixture)

    from toronto_bids.extract import ExtractionClient

    monkeypatch.setattr(ExtractionClient, "extract", mock_extract)
    monkeypatch.setattr(ExtractionClient, "__init__", lambda self, **kw: None)
    monkeypatch.setattr("toronto_bids.cli._open_db", lambda: conn)

    rc = main(["extract", "--dry-run", "deadbeef"])
    assert rc == 0


def test_cli_extract_dry_run_missing_sha256(monkeypatch, conn):
    from toronto_bids.cli import main

    monkeypatch.setattr("toronto_bids.cli._open_db", lambda: conn)
    rc = main(["extract", "--dry-run", "nonexistent"])
    assert rc == 1
