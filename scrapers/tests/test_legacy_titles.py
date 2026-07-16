"""#65: titles recovered from the archived Ariba posting pages."""
from toronto_bids.models import Solicitation
from toronto_bids.sources.legacy_titles import fill_titles_from_legacy, titles_from_archive
from toronto_bids.store import db


def _archive(tmp_path, pages):
    """A stand-in for TB_DATA_DIR/legacy/azure/ariba_data: {folder: <title> text}."""
    root = tmp_path / "ariba_data"
    root.mkdir()
    for folder, title in pages.items():
        d = root / folder
        d.mkdir()
        (d / f"{folder}.html").write_text(
            f"<html><head><title>{title}</title></head><body>x</body></html>")
    return root


def test_reads_the_title_out_of_an_archived_posting_page(tmp_path):
    root = _archive(tmp_path, {"Doc3567676667": "Request for Quotations for Drywall Recycling"})
    assert titles_from_archive(root) == {
        "3567676667": "Request for Quotations for Drywall Recycling"}


def test_html_entities_are_decoded(tmp_path):
    """140 archived titles carry entities; storing them raw would publish the markup."""
    root = _archive(tmp_path, {
        "Doc3559742620": "RFP for Parks &amp; Recreation Security Guard Services",
        "Doc3524228095": "OTP - &nbsp;Legacy General Electric Controller",
    })
    got = titles_from_archive(root)
    assert got["3559742620"] == "RFP for Parks & Recreation Security Guard Services"
    assert "&nbsp;" not in got["3524228095"]
    assert "\xa0" not in got["3524228095"]


def test_an_archived_placeholder_is_rejected_like_any_other(tmp_path):
    """#70's rule applies here too — a placeholder must not sneak back in via the archive."""
    root = _archive(tmp_path, {"Doc3524228095": "Doc-Doc3524228095",
                               "Doc3567676667": "Drywall Recycling Services"})
    assert set(titles_from_archive(root)) == {"3567676667"}


def test_fills_a_title_less_solicitation(conn, tmp_path):
    root = _archive(tmp_path, {"Doc3567676667": "Request for Quotations for Drywall Recycling"})
    db.upsert_row(conn, Solicitation("3567676667", title=None, source="odata"), overwrite=True)
    conn.commit()
    assert fill_titles_from_legacy(conn, root) == 1
    row = conn.execute("SELECT title, source FROM solicitation").fetchone()
    assert row["title"] == "Request for Quotations for Drywall Recycling"
    assert row["source"] == "legacy_ariba_html"


def test_outranks_a_bid_award_panel_heading(conn, tmp_path):
    """The posting page names the solicitation; the council heading describes the award."""
    root = _archive(tmp_path, {"Doc3524228095": "RFQ for Non-OEM Preventative Vehicle Repairs"})
    conn.execute("INSERT INTO solicitation (document_number, title, source) VALUES (?,?,?)",
                 ("3524228095",
                  "Award of Ariba Document Number 3524228095 to Various Suppliers for the Non-OEM",
                  "bid_award_panel"))
    conn.commit()
    assert fill_titles_from_legacy(conn, root) == 1
    assert conn.execute("SELECT title FROM solicitation").fetchone()[0] == \
        "RFQ for Non-OEM Preventative Vehicle Repairs"


def test_never_overrides_a_title_the_city_published(conn, tmp_path):
    root = _archive(tmp_path, {"Doc3567676667": "Something From The Archive"})
    db.upsert_row(conn, Solicitation("3567676667", title="Urban Forestry Supplies",
                                     source="odata"), overwrite=True)
    conn.commit()
    assert fill_titles_from_legacy(conn, root) == 0
    assert conn.execute("SELECT title FROM solicitation").fetchone()[0] == "Urban Forestry Supplies"


def test_is_idempotent(conn, tmp_path):
    root = _archive(tmp_path, {"Doc3567676667": "Drywall Recycling Services"})
    db.upsert_row(conn, Solicitation("3567676667", title=None, source="odata"), overwrite=True)
    conn.commit()
    assert fill_titles_from_legacy(conn, root) == 1
    assert fill_titles_from_legacy(conn, root) == 0   # nothing left to fill or improve


def test_missing_archive_is_not_an_error(conn, tmp_path):
    assert fill_titles_from_legacy(conn, tmp_path / "nope") == 0
