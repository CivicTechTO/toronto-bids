from pathlib import Path

import httpx

from toronto_bids.http import HttpClient
from toronto_bids.sources.council import download_pdf

FIXTURES = Path(__file__).parent / "fixtures"


def _http_serving(pdf_bytes):
    def handler(request):
        return httpx.Response(200, content=pdf_bytes)
    return HttpClient(client=httpx.Client(transport=httpx.MockTransport(handler)), backoff=0.0)


def test_get_bytes_returns_body_bytes():
    http = _http_serving(b"\x89PDFdata")
    assert http.get_bytes("https://example.test/x") == b"\x89PDFdata"


def test_download_pdf_saves_hashes_and_extracts(tmp_path):
    pdf = (FIXTURES / "tiny.pdf").read_bytes()
    http = _http_serving(pdf)
    result = download_pdf(http,
                          "https://www.toronto.ca/legdocs/mmis/2025/gg/bgrd/backgroundfile-260581.pdf",
                          tmp_path)
    saved = Path(result["local_path"])
    assert saved.exists() and saved.read_bytes() == pdf
    import hashlib
    assert result["sha256"] == hashlib.sha256(pdf).hexdigest()
    assert "HELLO PDF" in result["text"]  # pdftotext extracted the fixture's text
