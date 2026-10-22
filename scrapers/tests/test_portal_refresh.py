"""#140: source-owned page metadata refreshes without inventing awards or identity links."""
import json
from pathlib import Path

import pytest

from toronto_bids import config
from toronto_bids.buyers import seed_buyers
from toronto_bids.export.document import build_export_document
from toronto_bids.models import AgencySolicitation
from toronto_bids.sources.bids_tenders import parse_listing, run_portal_capture, store_listings
from toronto_bids.store import db

FIXTURES = Path(__file__).parent/'fixtures/agencies'
ZOO = json.loads((FIXTURES/'bids_tenders_zoo_2026-10-21.json').read_text())
TRCA = json.loads((FIXTURES/'bids_tenders_trca_2026-10-20.json').read_text())


def awarded():
    return next(r for r in ZOO if r['Id']=='6fc2f901-6847-469e-b630-d09fc5e75cb9')


def test_real_awarded_observation_refreshes_seeded_open_state_and_keeps_identity(conn):
    raw = awarded()
    buyers = seed_buyers(conn)
    # Controlled stale-state setup; the updated observation below is unchanged live JSON.
    store_listings(conn, [dict(raw, Status='Open', DateClosing='/Date(1667491200000)/')], buyers)
    old = dict(conn.execute('SELECT * FROM portal_listing').fetchone())
    store_listings(conn, [raw], buyers)
    current = dict(conn.execute('SELECT * FROM portal_listing').fetchone())
    assert current['status'] == 'Awarded'
    assert current['closing_date'] == '2022-11-04T16:00:00+00:00'
    for key in ('id','buyer_id','portal_url','procurement_id','first_seen'):
        assert current[key] == old[key]
    assert conn.execute('SELECT COUNT(*) FROM portal_listing').fetchone()[0] == 1
    assert conn.execute('SELECT COUNT(*) FROM agency_award').fetchone()[0] == 0


def test_source_metadata_corrections_refresh_only_the_same_portal_page(conn):
    raw = awarded()
    buyers = seed_buyers(conn)
    store_listings(conn, [dict(raw, Title='RFQ-500 - earlier title', DateAvailable='/Date(1660000000000)/')], buyers)
    store_listings(conn, [raw], buyers)
    expected = parse_listing(raw, buyers['toronto-zoo'])
    held = conn.execute('SELECT * FROM portal_listing').fetchone()
    assert held['title'] == expected.title
    assert held['posted_date'] == expected.posted_date
    assert held['native_ref'] == expected.native_ref


def test_null_observation_never_erases_held_metadata_or_closes_absent_pages(conn):
    raw = awarded()
    buyers = seed_buyers(conn)
    store_listings(conn, [raw], buyers)
    before = dict(conn.execute('SELECT * FROM portal_listing').fetchone())
    store_listings(conn, [dict(raw, Title=None, Status=None, DateAvailable=None, DateClosing=None)], buyers)
    after = dict(conn.execute('SELECT * FROM portal_listing').fetchone())
    assert {k:v for k,v in after.items() if k!='last_seen'} == {k:v for k,v in before.items() if k!='last_seen'}
    store_listings(conn, [], buyers)
    assert conn.execute('SELECT status FROM portal_listing').fetchone()[0] == 'Awarded'


def test_refresh_keeps_board_record_and_other_page_with_reused_reference_unchanged(conn):
    buyers = seed_buyers(conn)
    rows = [r for r in ZOO if r['Title'].startswith('RFP-38 - ')]
    assert len(rows) == 2
    store_listings(conn, rows, buyers)
    db.upsert_row(conn, AgencySolicitation(buyers['toronto-zoo'], 'RFP-38',
                   title='Independent board title', status='awarded', source='zoo_board'), overwrite=False)
    from toronto_bids.linking.procurement import resolve_procurements
    resolve_procurements(conn)
    board = dict(conn.execute('SELECT * FROM agency_solicitation').fetchone())
    other = dict(conn.execute('SELECT * FROM portal_listing WHERE portal_url LIKE ?', ('%'+rows[1]['Id'],)).fetchone())
    store_listings(conn, [dict(rows[0], Status='Cancelled')], buyers)
    assert dict(conn.execute('SELECT * FROM agency_solicitation').fetchone()) == board
    assert dict(conn.execute('SELECT * FROM portal_listing WHERE id=?',(other['id'],)).fetchone()) == other
    assert conn.execute('SELECT status FROM portal_listing WHERE portal_url LIKE ?',('%'+rows[0]['Id'],)).fetchone()[0] == 'Cancelled'


def test_full_capture_retains_both_raw_observations_and_exports_current_status(conn, monkeypatch, tmp_path):
    from toronto_bids.sources import bids_tenders as bt
    raw = awarded()
    observations = [dict(raw, Status='Open'), raw]
    monkeypatch.setattr(config, 'PORTAL_RECORDINGS_DIR', tmp_path/'recordings')
    monkeypatch.setattr(bt, 'fetch_listings', lambda *a, **k: iter([observations.pop(0)]))
    for _ in range(2):
        assert run_portal_capture(conn, only={'toronto-zoo'}) == {'toronto-zoo':1}
    saved = [json.loads(p.read_text()) for p in (tmp_path/'recordings').glob('*.json')]
    assert {r['Status'] for r in saved} == {'Open','Awarded'}
    doc = build_export_document(conn, 'fixed')
    buyer = next(b for b in doc['buyers'] if b['slug']=='toronto-zoo')
    assert buyer['portal_listings'][0]['status'] == 'Awarded'
    assert buyer['awards'] == []


@pytest.mark.parametrize('status',[3,'3','3.0',{},['Awarded']])
def test_unlabelled_numeric_or_malformed_status_refuses_batch_instead_of_storing_a_code(conn, status):
    buyers = seed_buyers(conn)
    with pytest.raises(ValueError, match='status'):
        store_listings(conn, [dict(awarded(), Status=status)], buyers)
    assert conn.execute('SELECT COUNT(*) FROM portal_listing').fetchone()[0] == 0


def test_real_grid_statuses_are_preserved_verbatim():
    rows = ZOO + [dict(r, buyer_slug='trca') for r in TRCA]
    assert {'Awarded','Closed','Cancelled','Terminated','Archived'} <= {r['Status'] for r in rows}
    for raw in rows:
        assert parse_listing(raw, 1).status == raw['Status']


def test_provisional_record_sample_is_replaced_with_an_unchanged_live_trca_record():
    raw = json.loads((FIXTURES/'bids_tenders_record_sample.json').read_text())
    assert raw['Id'] == '5082161c-f80a-4cd8-aaac-8388a237e99e'
    row = parse_listing(raw, 7)
    assert row.native_ref == '10044383'
    assert row.status == 'Closed'
    assert row.posted_date == '2026-03-24T19:10:00+00:00'
    assert row.closing_date == '2026-05-19T20:00:00+00:00'
    assert 'ReferenceNumber' not in raw and 'StatusText' not in raw


def test_status_text_can_label_a_legacy_code_but_unknown_named_states_are_preserved():
    assert parse_listing(dict(awarded(), Status=3, StatusText='Awarded'),1).status == 'Awarded'
    assert parse_listing(dict(awarded(), Status='Under Review'),1).status == 'Under Review'
