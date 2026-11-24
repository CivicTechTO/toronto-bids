import hashlib

import pytest

from toronto_bids.linking.procurement import resolve_procurements
from toronto_bids.models import AgencySolicitation, BackgroundPdf, Buyer
from toronto_bids.sources import trca_history_pilot as pilot
from toronto_bids.store import db


@pytest.fixture
def archive(tmp_path, monkeypatch):
    conn = db.connect(":memory:")
    db.init_db(conn)
    db.upsert_row(conn, Buyer(slug="trca", name="Toronto and Region Conservation Authority"), overwrite=False)
    db.upsert_row(conn, Buyer(slug="other", name="Other agency"), overwrite=False)
    buyer = conn.execute("SELECT id FROM buyer WHERE slug='trca'").fetchone()[0]
    other = conn.execute("SELECT id FROM buyer WHERE slug='other'").fetchone()[0]
    for docid in pilot.SOURCE_HASHES:
        path = tmp_path / f"{docid}.pdf"
        contents = f"%PDF- mock {docid}".encode()
        path.write_bytes(contents)
        sha = hashlib.sha256(contents).hexdigest()
        monkeypatch.setitem(pilot.SOURCE_HASHES, docid, sha)
        db.upsert_row(conn, BackgroundPdf(pilot.document_url(docid), kind=pilot.KIND,
                                         local_path=str(path), sha256=sha), overwrite=False)
    db.upsert_row(conn, AgencySolicitation(buyer, "10009000", source="trca_board"), overwrite=False)
    resolve_procurements(conn)
    db.upsert_row(conn, AgencySolicitation(other, "10000813", source="other"), overwrite=False)
    conn.commit()
    yield conn, buyer
    conn.close()


def test_import_is_scoped_and_repeatable(archive):
    conn, buyer = archive
    before = list(map(tuple, conn.execute("SELECT * FROM agency_solicitation ORDER BY id")))
    action, keys = pilot.import_pilot(conn)
    assert action == "imported"
    assert len(set(keys.values())) == 6
    assert pilot.import_pilot(conn) == ("verified", keys)
    assert list(map(tuple, conn.execute("SELECT * FROM agency_solicitation WHERE source<>? ORDER BY id",
                                       (pilot.KIND,)))) == before
    for table, count in zip(pilot.TABLES, (6, 37, 5), strict=True):
        assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE buyer_id=? AND source=?",
                            (buyer, pilot.KIND)).fetchone()[0] == count
    assert conn.execute("SELECT bid_price_numeric FROM agency_bid WHERE native_ref='10000813' "
                        "AND bidder_name_raw='Dufferin Apparel'").fetchone()[0] == 148391.15
    assert conn.execute("SELECT award_amount_numeric FROM agency_award "
                        "WHERE native_ref='10000813'").fetchone()[0] == 296000.0
    assert conn.execute("SELECT COUNT(*) FROM procurement_document").fetchone()[0] == 7
    assert conn.execute("SELECT COUNT(*) FROM agency_bid WHERE source=? AND status='compliant'",
                        (pilot.KIND,)).fetchone()[0] == 5
    assert conn.execute("SELECT status FROM agency_solicitation WHERE native_ref='RSD13-096'").fetchone()[0] is None
    assert conn.execute("SELECT COUNT(*) FROM agency_award WHERE native_ref='RSD13-096'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM agency_bid WHERE native_ref='RSD13-096'").fetchone()[0] == 6
    assert conn.execute("SELECT COUNT(*) FROM agency_bid WHERE native_ref='10000128' AND status='disqualified' "
                        "AND bid_price IS NULL").fetchone()[0] == 1
    resolve_procurements(conn)
    assert pilot.import_pilot(conn) == ("verified", keys)


def test_import_extends_original_pilot(archive, monkeypatch):
    conn, _ = archive
    complete = pilot._reviewed_rows

    def original(buyer):
        return tuple([row for row in rows if row.native_ref not in {"10000128", "RSD13-096"}]
                     for rows in complete(buyer))

    with monkeypatch.context() as patch:
        patch.setattr(pilot, "_reviewed_rows", original)
        _, original_ids = pilot.import_pilot(conn)
    action, ids = pilot.import_pilot(conn)
    assert action == "imported"
    assert original_ids.items() <= ids.items()
    assert pilot.import_pilot(conn) == ("verified", ids)


def test_import_refuses_current_trca_reference_collision(archive):
    conn, buyer = archive
    db.upsert_row(conn, AgencySolicitation(buyer, "Contract #10000813", source="trca_board"),
                  overwrite=False)
    conn.commit()
    with pytest.raises(ValueError, match="conflict with an existing TRCA source"):
        pilot.import_pilot(conn)
    assert conn.execute("SELECT COUNT(*) FROM agency_solicitation WHERE source=?",
                        (pilot.KIND,)).fetchone()[0] == 0


def test_import_refuses_archive_file_mismatch(archive):
    conn, _ = archive
    path = conn.execute("SELECT local_path FROM background_pdf WHERE url=?",
                        (pilot.document_url(pilot.FISHLEIGH),)).fetchone()[0]
    with open(path, "ab") as pdf:
        pdf.write(b" changed")
    with pytest.raises(ValueError, match="bytes differ"):
        pilot.import_pilot(conn)
    assert conn.execute("SELECT COUNT(*) FROM agency_award").fetchone()[0] == 0
