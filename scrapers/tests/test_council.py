import hashlib
import shutil
from pathlib import Path

import httpx
import pytest

from toronto_bids.http import HttpClient
from toronto_bids.models import CouncilItem, SuspendedFirm
from toronto_bids.sources import council as council_mod
from toronto_bids.sources.council import download_pdf, enrich_council, parse_agenda_item
from toronto_bids.store import db

FIXTURES = Path(__file__).parent / "fixtures"

needs_pdftotext = pytest.mark.skipif(
    shutil.which("pdftotext") is None,
    reason="needs pdftotext (poppler): apt-get install -y poppler-utils / brew install poppler",
)


def _html():
    return (FIXTURES / "agenda_item.html").read_text()


def _stub_fetch(_reference):
    return _html()


def _http_serving(pdf_bytes):
    return HttpClient(
        client=httpx.Client(
            transport=httpx.MockTransport(lambda _r: httpx.Response(200, content=pdf_bytes))
        ),
        backoff=0.0,
    )


def _http_pdf():
    return _http_serving((FIXTURES / "tiny.pdf").read_bytes())


def test_parses_item_title_and_decision():
    item, _pdfs = parse_agenda_item(_html(), "2025.GG26.3")
    assert isinstance(item, CouncilItem)
    assert item.reference == "2025.GG26.3"
    assert item.title == "Agenda Item History - 2025.GG26.3"
    assert "suspend Capital Sewer" in item.decision_text


def test_dedups_pdf_links_and_classifies_kind():
    _item, pdfs = parse_agenda_item(_html(), "2025.GG26.3")
    urls = {p["url"] for p in pdfs}
    assert len(pdfs) == 3
    assert "https://www.toronto.ca/legdocs/mmis/2025/gg/bgrd/backgroundfile-260581.pdf" in urls
    kinds = {p["url"].rsplit("/", 2)[-2]: p["kind"] for p in pdfs}
    assert kinds["bgrd"] == "bgrd"
    assert kinds["comm"] == "comm"


def test_missing_decision_section_is_none_safe():
    item, pdfs = parse_agenda_item(
        "<html><head><title>Agenda Item History - X</title></head><body></body></html>", "X"
    )
    assert item.reference == "X"
    assert item.decision_text is None
    assert pdfs == []


@needs_pdftotext
def test_download_pdf_saves_hashes_and_extracts(tmp_path):
    pdf = (FIXTURES / "tiny.pdf").read_bytes()
    result = download_pdf(
        _http_serving(pdf),
        "https://www.toronto.ca/legdocs/mmis/2025/gg/bgrd/backgroundfile-260581.pdf",
        tmp_path,
    )
    saved = Path(result["local_path"])
    assert saved.exists() and saved.read_bytes() == pdf
    assert result["sha256"] == hashlib.sha256(pdf).hexdigest()
    assert "HELLO PDF" in result["text"]


@needs_pdftotext
def test_enrich_builds_council_item_and_pdfs(conn, tmp_path):
    db.upsert_row(
        conn,
        SuspendedFirm(
            supplier_name_raw="Capital Sewer",
            council_authority="2025.GG26.3",
            source="suspended_firms",
        ),
        overwrite=True,
    )
    conn.commit()
    n = enrich_council(conn, _http_pdf(), fetch=_stub_fetch, dest_dir=tmp_path)
    assert n == 1
    assert db.counts(conn)["council_item"] == 1
    assert db.counts(conn)["background_pdf"] == 3
    row = conn.execute("SELECT text FROM background_pdf LIMIT 1").fetchone()
    assert "HELLO PDF" in row["text"]


@needs_pdftotext
def test_enrich_skips_blank_authority_and_is_idempotent(conn, tmp_path):
    db.upsert_row(
        conn,
        SuspendedFirm(supplier_name_raw="No Auth", council_authority="", source="suspended_firms"),
        overwrite=True,
    )
    db.upsert_row(
        conn,
        SuspendedFirm(
            supplier_name_raw="Capital Sewer",
            council_authority="2025.GG26.3",
            source="suspended_firms",
        ),
        overwrite=True,
    )
    conn.commit()
    enrich_council(conn, _http_pdf(), fetch=_stub_fetch, dest_dir=tmp_path)
    enrich_council(conn, _http_pdf(), fetch=_stub_fetch, dest_dir=tmp_path)
    assert db.counts(conn)["council_item"] == 1
    assert db.counts(conn)["background_pdf"] == 3


@needs_pdftotext
def test_enrich_isolates_a_failing_ref(conn, tmp_path):
    db.upsert_row(
        conn,
        SuspendedFirm(supplier_name_raw="A", council_authority="2025.GG26.3", source="suspended_firms"),
        overwrite=True,
    )
    db.upsert_row(
        conn,
        SuspendedFirm(supplier_name_raw="B", council_authority="2025.BAD.1", source="suspended_firms"),
        overwrite=True,
    )
    conn.commit()

    def fetch(ref):
        if ref == "2025.BAD.1":
            raise RuntimeError("fetch blew up")
        return _stub_fetch(ref)

    n = enrich_council(conn, _http_pdf(), fetch=fetch, dest_dir=tmp_path)
    assert n == 1
    assert db.counts(conn)["council_item"] == 1


@needs_pdftotext
def test_enrich_isolates_a_failing_pdf(conn, tmp_path):
    pdf = (FIXTURES / "tiny.pdf").read_bytes()

    def handler(request):
        if "/comm/" in str(request.url):
            return httpx.Response(404, text="nope")
        return httpx.Response(200, content=pdf)

    http = HttpClient(client=httpx.Client(transport=httpx.MockTransport(handler)), backoff=0.0)
    db.upsert_row(
        conn,
        SuspendedFirm(supplier_name_raw="A", council_authority="2025.GG26.3", source="suspended_firms"),
        overwrite=True,
    )
    conn.commit()
    n = enrich_council(conn, http, fetch=_stub_fetch, dest_dir=tmp_path)
    assert n == 1
    assert db.counts(conn)["council_item"] == 1
    assert db.counts(conn)["background_pdf"] == 2


def test_enrich_requires_pdftotext(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(council_mod.shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError, match="pdftotext"):
        enrich_council(conn, _http_pdf(), fetch=_stub_fetch, dest_dir=tmp_path)
