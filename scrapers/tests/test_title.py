import pytest

from toronto_bids.models import Solicitation
from toronto_bids.store import db
from toronto_bids.title import clean_title, clear_placeholder_titles


@pytest.mark.parametrize("raw", [
    # the bulk: the City publishes the document number as the title
    "Doc-3524228095",
    "Doc-Doc3524228095",
    "Doc-DOC2951402864",
    "Doc-Doc 2922336030",
    "Doc- Doc2899083119 ",
    "Doc-Summary6714187046",
    "Doc-Notice1914466835",
    # ...and variants carrying only a secondary reference number, still no subject
    "Doc-Doc2922336030 (1062021)",
    "Doc-Doc2219202727 / nRFP No. 1201205001",
    "Doc-Doc2300075322    Tender No. 712020",
    "Doc-Ariba Doc No. 2243638006 RFP NO. 9118205024",
    "Doc-Doc No. 2243638002 (RFP No. 9115205026)",
    "Doc-202020, Ariba Doc No. 2201203353",
    "Doc-2457091244 (9120205041)",
    "Doc-252020 (Doc 2201872432)",
    # nothing at all
    None, "", "   ",
])
def test_placeholders_become_none(raw):
    assert clean_title(raw) is None


@pytest.mark.parametrize("raw,expected", [
    ("Restoration of an Exposed Sanitary Siphon Crossing",
     "Restoration of an Exposed Sanitary Siphon Crossing"),
    ("RFQ for Chinaware", "RFQ for Chinaware"),
    ("Urban Forestry Supplies", "Urban Forestry Supplies"),
    # short real titles must survive — the rule keys on words, not length
    ("Sewer", "Sewer"),
    ("Bed Frames", "Bed Frames"),
    ("SNOW REMOVAL", "SNOW REMOVAL"),
    ("Natural Gas", "Natural Gas"),
    # a real title that happens to lead with the doc number
    ("Doc4171532487 Request for Quotations for Uptown Yonge BIA - Benches",
     "Doc4171532487 Request for Quotations for Uptown Yonge BIA - Benches"),
    # a project code is information, not scaffolding — keep it
    ("Doc-Doc2982722772(21TROM120SCTU)", "Doc-Doc2982722772(21TROM120SCTU)"),
    # whitespace is tidied but the title is otherwise verbatim
    ("  Bus Charter  ", "Bus Charter"),
])
def test_real_titles_are_kept_verbatim(raw, expected):
    assert clean_title(raw) == expected


def test_placeholder_no_longer_clobbers_a_real_title(conn):
    """#70: the feed carries two records for one document, in arbitrary order."""
    real = Solicitation("2922336030", title="Restoration of an Exposed Sanitary Siphon Crossing",
                        status="Open", source="odata")
    placeholder = Solicitation("2922336030", title=clean_title("Doc-Doc2922336030 (1062021)"),
                               status="Awarded", source="odata")
    db.upsert_row(conn, real, overwrite=True)
    db.upsert_row(conn, placeholder, overwrite=True)   # arrives last — must not win
    conn.commit()
    assert conn.execute("SELECT title FROM solicitation").fetchone()[0] == \
        "Restoration of an Exposed Sanitary Siphon Crossing"


def test_order_does_not_matter(conn):
    """The feed's order is arbitrary, so the outcome must not depend on it."""
    placeholder = Solicitation("2922336030", title=clean_title("Doc-Doc2922336030"),
                               status="Awarded", source="odata")
    real = Solicitation("2922336030", title="Restoration of an Exposed Sanitary Siphon Crossing",
                        status="Open", source="odata")
    db.upsert_row(conn, placeholder, overwrite=True)   # placeholder first this time
    db.upsert_row(conn, real, overwrite=True)
    conn.commit()
    assert conn.execute("SELECT title FROM solicitation").fetchone()[0] == \
        "Restoration of an Exposed Sanitary Siphon Crossing"


def test_null_title_is_fillable_by_a_backfill_source(conn):
    """Why this matters beyond #70: overwrite=False fills NULLs, so a legacy title lands."""
    db.upsert_row(conn, Solicitation("2922336030", title=clean_title("Doc-Doc2922336030"),
                                     source="odata"), overwrite=True)
    conn.commit()
    db.upsert_row(conn, Solicitation("2922336030", title="Watermain Replacement on King St",
                                     source="legacy_ariba_html"), overwrite=False)
    conn.commit()
    assert conn.execute("SELECT title FROM solicitation").fetchone()[0] == \
        "Watermain Replacement on King St"


def test_cleanup_clears_placeholders_already_in_the_store(conn):
    # COALESCE means a NULL from the normalizer never clears an existing placeholder,
    # so rows written before this change need the pass.
    for doc, title in [("2922336030", "Doc-Doc2922336030 (1062021)"),
                       ("3524228095", "Doc-3524228095"),
                       ("4127450139", "Urban Forestry Supplies")]:
        conn.execute("INSERT INTO solicitation (document_number, title, source) VALUES (?,?,?)",
                     (doc, title, "odata"))
    conn.commit()
    assert clear_placeholder_titles(conn) == 2
    titles = dict(conn.execute("SELECT document_number, title FROM solicitation"))
    assert titles["2922336030"] is None
    assert titles["3524228095"] is None
    assert titles["4127450139"] == "Urban Forestry Supplies"   # real title untouched


def test_cleanup_is_idempotent(conn):
    conn.execute("INSERT INTO solicitation (document_number, title, source) VALUES (?,?,?)",
                 ("3524228095", "Doc-3524228095", "odata"))
    conn.commit()
    assert clear_placeholder_titles(conn) == 1
    assert clear_placeholder_titles(conn) == 0   # nothing left to clear
