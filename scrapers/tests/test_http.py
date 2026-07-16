import httpx
import pytest

from toronto_bids.http import HttpClient


def _client(handler, **kwargs):
    transport = httpx.MockTransport(handler)
    inner = httpx.Client(transport=transport)
    return HttpClient(client=inner, backoff=0.0, **kwargs)


def test_get_json_returns_parsed_body():
    def handler(request):
        assert "User-Agent" in request.headers
        return httpx.Response(200, json={"ok": True})
    client = _client(handler)
    assert client.get_json("https://example.test/x") == {"ok": True}


def test_get_json_retries_on_500_then_succeeds():
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={"ok": True})
    client = _client(handler, retries=4)
    assert client.get_json("https://example.test/x") == {"ok": True}
    assert calls["n"] == 3


def test_get_json_does_not_retry_on_404():
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        return httpx.Response(404, text="nope")
    client = _client(handler, retries=4)
    with pytest.raises(httpx.HTTPStatusError):
        client.get_json("https://example.test/x")
    assert calls["n"] == 1


def test_get_json_raises_after_exhausting_retries():
    def handler(request):
        return httpx.Response(503, text="down")
    client = _client(handler, retries=2)
    with pytest.raises(httpx.HTTPStatusError):
        client.get_json("https://example.test/x")


def test_post_json_sends_body():
    def handler(request):
        assert request.method == "POST"
        return httpx.Response(200, json={"echo": True})
    client = _client(handler)
    assert client.post_json("https://example.test/x", json={"a": 1}) == {"echo": True}


def test_get_json_sends_custom_headers():
    seen = {}
    def handler(request):
        seen["accept"] = request.headers.get("Accept")
        return httpx.Response(200, json={"ok": True})
    client = _client(handler)
    client.get_json("https://example.test/x", headers={"Accept": "application/json"})
    assert seen["accept"] == "application/json"


def test_default_client_uses_config_user_agent():
    from toronto_bids import config
    client = HttpClient()
    try:
        assert client._client.headers["User-Agent"] == config.USER_AGENT
    finally:
        client.close()
