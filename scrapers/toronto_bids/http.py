import time

import httpx

from toronto_bids import config


class HttpClient:
    def __init__(self, client: httpx.Client | None = None, retries: int = config.HTTP_RETRIES,
                 backoff: float = 0.5):
        self._client = client or httpx.Client(
            timeout=config.HTTP_TIMEOUT,
            headers={"User-Agent": config.USER_AGENT},
            follow_redirects=True,
        )
        self._retries = retries
        self._backoff = backoff

    def _request(self, method, url, headers=None, **kwargs):
        last_exc = None
        for attempt in range(self._retries + 1):
            try:
                resp = self._client.request(method, url, headers=headers, **kwargs)
                resp.raise_for_status()
                return resp
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500:
                    raise  # client error: do not retry
                last_exc = exc
            except httpx.TransportError as exc:
                last_exc = exc
            if attempt < self._retries:
                time.sleep(self._backoff * (2 ** attempt))
        raise last_exc

    def get_json(self, url, params=None, headers=None):
        return self._request("GET", url, params=params, headers=headers).json()

    def post_json(self, url, json=None, params=None, headers=None):
        return self._request("POST", url, json=json, params=params, headers=headers).json()

    def close(self):
        self._client.close()
