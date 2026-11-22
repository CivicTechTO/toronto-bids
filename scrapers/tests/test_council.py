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


def _blank_valid_pdf() -> bytes:
    """Build a minimal structurally valid 1-page PDF with no embedded text (scanned-PDF equivalent)."""
    objs = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\n",
    ]
    buf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objs:
        offsets.append(len(buf))
        buf.extend(obj)
    xref_pos = len(buf)
    buf.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for off in offsets[1:]:
        buf.extend(f"{off:010d} 00000 n \n".encode())
    buf.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    )
    return bytes(buf)


@needs_pdftotext
def test_download_pdf_rejects_html_and_corrupt_pdf_without_replacing_held_file(tmp_path):
    pdf = (FIXTURES / "tiny.pdf").read_bytes()
    url = "https://www.toronto.ca/legdocs/mmis/2025/gg/bgrd/backgroundfile-260581.pdf"
    held = tmp_path / "backgroundfile-260581.pdf"
    held.write_bytes(pdf)

    with pytest.raises(ValueError, match="not a PDF"):
        download_pdf(_http_serving(b"<html><body>Maintenance</body></html>"), url, tmp_path)
    assert held.read_bytes() == pdf

    with pytest.raises(ValueError):
        download_pdf(_http_serving(b"%PDF-1.4\nnot actually a valid pdf stream"), url, tmp_path)
    assert held.read_bytes() == pdf
    assert [p.name for p in tmp_path.iterdir()] == ["backgroundfile-260581.pdf"]


@needs_pdftotext
def test_download_pdf_accepts_valid_scanned_pdf_with_no_text(tmp_path):
    blank = _blank_valid_pdf()
    url = "https://secure.toronto.ca/c3api_upload/retrieve/pmmd_solicitations/scanned123"
    result = download_pdf(_http_serving(blank), url, tmp_path, layout=True)
    saved = Path(result["local_path"])
    assert saved.read_bytes() == blank
    assert result["sha256"] == hashlib.sha256(blank).hexdigest()
    assert result["text"] is None
