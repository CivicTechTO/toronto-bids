import httpx
import pytest

from toronto_bids import config
from toronto_bids.http import HttpClient, time as http_time


def _client(handler, *, backoff=0.0, **kwargs):
    transport = httpx.MockTransport(handler)
    inner = httpx.Client(transport=transport)
    return HttpClient(client=inner, backoff=backoff, **kwargs)


def test_get_json_returns_parsed_body():
    client = _client(lambda request: httpx.Response(200, json={"ok": True}))
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
    client = _client(lambda request: httpx.Response(503, text="down"), retries=2)
    with pytest.raises(httpx.HTTPStatusError):
        client.get_json("https://example.test/x")


def test_transport_error_retries_and_raises_when_exhausted():
    calls = {"n": 0}
    def flaky(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("reset")
        return httpx.Response(200, json={"ok": True})
    assert _client(flaky, retries=2).get_json("https://example.test/x") == {"ok": True}
    assert calls["n"] == 2

    def always_down(request):
        raise httpx.ReadTimeout("timed out")
    with pytest.raises(httpx.TransportError):
        _client(always_down, retries=2).get_json("https://example.test/x")


def test_retries_use_exponential_backoff(monkeypatch):
    sleeps = []
    monkeypatch.setattr(http_time, "sleep", sleeps.append)
    client = _client(lambda request: httpx.Response(503, text="down"), retries=3, backoff=0.5)
    with pytest.raises(httpx.HTTPStatusError):
        client.get_json("https://example.test/x")
    assert sleeps == [0.5, 1.0, 2.0]


def test_post_json_sends_body():
    def handler(request):
        assert request.method == "POST"
        return httpx.Response(200, json={"echo": True})
    client = _client(handler)
    assert client.post_json("https://example.test/x", json={"a": 1}) == {"echo": True}


def test_get_text_returns_body_text():
    client = _client(lambda request: httpx.Response(200, text="<html>hi</html>"))
    assert client.get_text("https://example.test/x") == "<html>hi</html>"


def test_get_bytes_returns_raw_content():
    client = _client(lambda request: httpx.Response(200, content=b"\x00\x01pdf"))
    assert client.get_bytes("https://example.test/x") == b"\x00\x01pdf"


def test_get_json_sends_custom_headers():
    seen = {}
    def handler(request):
        seen["accept"] = request.headers.get("Accept")
        return httpx.Response(200, json={"ok": True})
    client = _client(handler)
    client.get_json("https://example.test/x", headers={"Accept": "application/json"})
    assert seen["accept"] == "application/json"


def test_default_client_uses_config_user_agent():
    client = HttpClient()
    try:
        assert client._client.headers["User-Agent"] == config.USER_AGENT
    finally:
        client.close()
