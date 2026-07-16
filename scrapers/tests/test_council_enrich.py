from pathlib import Path

import httpx

from toronto_bids.http import HttpClient
from toronto_bids.models import SuspendedFirm
from toronto_bids.sources.council import enrich_council
from toronto_bids.store import db

FIXTURES = Path(__file__).parent / "fixtures"


def _stub_fetch(reference):
    # Return the fixture HTML regardless of reference (simulates the headed fetch).
    return (FIXTURES / "agenda_item.html").read_text()


def _http_pdf():
    pdf = (FIXTURES / "tiny.pdf").read_bytes()
    return HttpClient(client=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, content=pdf))), backoff=0.0)


def test_enrich_builds_council_item_and_pdfs(conn, tmp_path):
    db.upsert_row(conn, SuspendedFirm(supplier_name_raw="Capital Sewer", council_authority="2025.GG26.3",
                                      source="suspended_firms"), overwrite=True)
    conn.commit()
    n = enrich_council(conn, _http_pdf(), fetch=_stub_fetch, dest_dir=tmp_path)
    assert n == 1
    assert db.counts(conn)["council_item"] == 1
    # 3 distinct PDFs from the fixture, each downloaded + extracted
    assert db.counts(conn)["background_pdf"] == 3
    row = conn.execute("SELECT text FROM background_pdf LIMIT 1").fetchone()
    assert "HELLO PDF" in row["text"]


def test_enrich_skips_blank_authority_and_is_idempotent(conn, tmp_path):
    db.upsert_row(conn, SuspendedFirm(supplier_name_raw="No Auth", council_authority="",
                                      source="suspended_firms"), overwrite=True)
    db.upsert_row(conn, SuspendedFirm(supplier_name_raw="Capital Sewer", council_authority="2025.GG26.3",
                                      source="suspended_firms"), overwrite=True)
    conn.commit()
    enrich_council(conn, _http_pdf(), fetch=_stub_fetch, dest_dir=tmp_path)
    enrich_council(conn, _http_pdf(), fetch=_stub_fetch, dest_dir=tmp_path)  # re-run
    assert db.counts(conn)["council_item"] == 1      # only the non-blank authority
    assert db.counts(conn)["background_pdf"] == 3     # no duplicate PDFs on re-run
