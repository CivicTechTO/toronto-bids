"""#124: opaque identities join only proven source relationships."""
import json
import sqlite3
from pathlib import Path

import pytest

from toronto_bids.buyers import seed_buyers
from toronto_bids.export.document import build_export_document
from toronto_bids.export.json_export import export_json
from toronto_bids.linking.procurement import resolve_procurements
from toronto_bids.models import (AgencyAward, AgencySolicitation, AribaPosting, Award,
                                BackgroundPdf, Bid, CompositeAward, CouncilItem,
                                NonCompetitive, Solicitation)
from toronto_bids.sources.bids_tenders import store_listings
from toronto_bids.store import db

FIXTURES = Path(__file__).parent / 'fixtures/agencies'


def put(conn, row):
    db.upsert_row(conn, row, overwrite=True)


def test_every_saved_portal_page_survives_repeated_references_and_replay(conn):
    buyers = seed_buyers(conn)
    rows = json.loads((FIXTURES/'bids_tenders_trca_identity_2026-10-22.json').read_text())
    rows += json.loads((FIXTURES/'bids_tenders_zoo_2026-10-21.json').read_text())
    assert len(rows) == 371
    assert store_listings(conn, rows, buyers) == 371
    before = [tuple(r) for r in conn.execute('SELECT id,buyer_id,portal_url,native_ref,procurement_id,first_seen FROM portal_listing ORDER BY id')]
    assert len({r[4] for r in before}) == 371
    assert len({(r[1],r[3]) for r in before}) < 371
    assert store_listings(conn, list(reversed(rows)), buyers) == 371
    after = [tuple(r) for r in conn.execute('SELECT id,buyer_id,portal_url,native_ref,procurement_id,first_seen FROM portal_listing ORDER BY id')]
    assert before == after
    out = build_export_document(conn, 'fixed')
    assert sum(len(b['portal_listings']) for b in out['buyers']) == 371
    assert not conn.execute('PRAGMA foreign_key_check').fetchall()


def test_later_collision_and_identifierless_page_are_never_merged(conn):
    buyers = seed_buyers(conn)
    row = json.loads((FIXTURES/'bids_tenders_record_sample.json').read_text())
    store_listings(conn, [row], buyers)
    first = conn.execute('SELECT procurement_id,first_seen FROM portal_listing').fetchone()
    store_listings(conn, [dict(row, Id='new-source-page'),
                         dict(row, Id='unlabelled-page', ReferenceNumber=None, Title='No bid number published')], buyers)
    held = conn.execute('SELECT * FROM portal_listing ORDER BY id').fetchall()
    assert len({r['procurement_id'] for r in held}) == 3
    assert tuple(conn.execute('SELECT procurement_id,first_seen FROM portal_listing WHERE id=1').fetchone()) == tuple(first)
    assert held[2]['native_ref'] is None


def test_keyspaces_do_not_merge_identical_strings_and_orphan_facts_get_ids(conn):
    put(conn, Solicitation('1234567890'))
    put(conn, NonCompetitive('1234567890'))
    put(conn, CompositeAward('1234567890', source='composite'))
    put(conn, Award('9999999999', supplier_name_raw='orphan'))
    put(conn, AribaPosting('no-document-number'))
    put(conn, Bid('No identifier bidder', source='test'))
    resolve_procurements(conn)
    ids = [conn.execute(f'SELECT procurement_id FROM {t}').fetchone()[0]
           for t in ('solicitation','noncompetitive','composite_award','award','ariba_posting','bid')]
    assert len(set(ids)) == 6 and None not in ids
    before = [tuple(r) for r in conn.execute('SELECT * FROM procurement ORDER BY id')]
    resolve_procurements(conn)
    assert before == [tuple(r) for r in conn.execute('SELECT * FROM procurement ORDER BY id')]


def test_proven_late_ariba_join_keeps_old_id_and_publishes_redirect(conn):
    put(conn, AribaPosting('event'))
    put(conn, Solicitation('1234567890'))
    resolve_procurements(conn)
    initial = {r['namespace']: r['procurement_id'] for r in conn.execute('SELECT * FROM procurement_key')}
    assert len(set(initial.values())) == 2
    put(conn, AribaPosting('event', document_number='1234567890'))
    resolve_procurements(conn)
    winner, loser = sorted(initial.values())
    assert conn.execute('SELECT procurement_id FROM ariba_posting').fetchone()[0] == winner
    assert conn.execute('SELECT procurement_id FROM solicitation').fetchone()[0] == winner
    alias = conn.execute('SELECT * FROM procurement WHERE id=?', (loser,)).fetchone()
    assert alias['merged_into'] == winner
    assert json.loads(alias['merge_evidence'])['rule'] == 'published_ariba_document_number'


def test_multi_contract_council_pdf_links_to_each_call_without_merging(conn):
    ref = '2011.BD1.1'
    url = 'https://www.toronto.ca/report.pdf'
    put(conn, CouncilItem(ref))
    put(conn, BackgroundPdf(url, reference=ref, kind='bgrd'))
    for call in ('100-2011','101-2011'):
        put(conn, CompositeAward(call, reference=ref, source='composite'))
    resolve_procurements(conn)
    assert conn.execute('SELECT COUNT(DISTINCT procurement_id) FROM composite_award').fetchone()[0] == 2
    assert conn.execute('SELECT procurement_id FROM background_pdf').fetchone()[0] is None
    assert conn.execute('SELECT COUNT(*) FROM procurement_document').fetchone()[0] == 2
    assert conn.execute('SELECT COUNT(*) FROM procurement_council_item').fetchone()[0] == 2


def test_exact_existing_links_join_council_bids_and_staff_reports(conn):
    ref, doc, url = '2018.BD1.1', '1234567890', 'https://www.toronto.ca/staff.pdf'
    put(conn, Solicitation(doc))
    put(conn, CouncilItem(ref))
    put(conn, Bid('Winner', reference=ref))
    put(conn, BackgroundPdf(url, reference=ref, kind='bgrd'))
    conn.execute('INSERT INTO solicitation_link VALUES (?,?,?)', (ref, doc, 'unique_winner_amount'))
    resolve_procurements(conn)
    expected = conn.execute('SELECT procurement_id FROM solicitation').fetchone()[0]
    assert conn.execute('SELECT procurement_id FROM bid').fetchone()[0] == expected
    assert conn.execute('SELECT procurement_id FROM background_pdf').fetchone()[0] == expected


def test_conflicting_late_document_link_rolls_back_identity_changes(conn):
    put(conn, AribaPosting('event', document_number='1234567890'))
    resolve_procurements(conn)
    before = {t: [tuple(r) for r in conn.execute(f'SELECT * FROM {t} ORDER BY 1')]
              for t in ('procurement','procurement_key')}
    put(conn, AribaPosting('event', document_number='9999999999'))
    with pytest.raises(ValueError, match='Conflicting procurement anchors'):
        resolve_procurements(conn)
    for t, rows in before.items():
        assert rows == [tuple(r) for r in conn.execute(f'SELECT * FROM {t} ORDER BY 1')]


def test_legacy_portal_page_migrates_but_prior_reference_enrichment_is_not_proof(conn):
    buyers = seed_buyers(conn)
    row = json.loads((FIXTURES/'bids_tenders_record_sample.json').read_text())
    store_listings(conn, [row], buyers)
    portal = conn.execute('SELECT * FROM portal_listing').fetchone()
    # Old backfill could attach a portal URL to a board row merely by matching the
    # reference. That inherited URL does not establish independent source evidence.
    put(conn, AgencySolicitation(buyers['trca'], portal['native_ref'],
                                portal_url=portal['portal_url'], source='trca_board'))
    put(conn, AgencyAward(buyers['trca'], portal['native_ref'], report_url='https://example.com/board.pdf'))
    resolve_procurements(conn)
    assert conn.execute('SELECT procurement_id FROM agency_award').fetchone()[0] != portal['procurement_id']
    # An old portal-origin observation is the same page; migrate that observation,
    # while board facts with the printed reference still remain separate.
    conn.execute("UPDATE agency_solicitation SET source='bids_tenders'")
    resolve_procurements(conn)
    assert conn.execute('SELECT procurement_id FROM agency_solicitation').fetchone()[0] == portal['procurement_id']
    assert conn.execute('SELECT procurement_id FROM agency_award').fetchone()[0] != portal['procurement_id']


def test_old_database_migration_preserves_rows_and_adds_real_foreign_keys(tmp_path):
    conn = db.connect(tmp_path/'old.sqlite')
    conn.execute('CREATE TABLE solicitation (document_number TEXT PRIMARY KEY,title TEXT,first_seen TEXT,last_seen TEXT)')
    conn.execute("INSERT INTO solicitation VALUES ('1234567890','Old title','2000-01-01','2001-01-01')")
    conn.commit()
    db.init_db(conn)
    assert any(r['from']=='procurement_id' and r['table']=='procurement' for r in conn.execute('PRAGMA foreign_key_list(solicitation)'))
    resolve_procurements(conn)
    row = conn.execute('SELECT * FROM solicitation').fetchone()
    assert (row['document_number'],row['title'],row['first_seen'],row['last_seen']) == ('1234567890','Old title','2000-01-01','2001-01-01')
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute('UPDATE solicitation SET procurement_id=99999')
    conn.close()


def test_export_prepares_identity_after_enrichment_and_keeps_legacy_sections(conn, tmp_path):
    put(conn, Solicitation('1234567890'))
    out = export_json(conn, tmp_path/'bids.json', 'fixed')
    doc = json.loads(out.read_text())
    assert doc['solicitations'][0]['procurement_id'] in {p['id'] for p in doc['procurements']}
    assert doc['procurement_keys'][0]['namespace'] == 'city_document'
    assert 'noncompetitive' in doc and 'council_items' in doc and 'buyers' in doc


def test_reference_becoming_ambiguous_cannot_keep_a_previous_single_doc_join(conn):
    ref = '2022.BA1.1'
    put(conn, Bid('Bidder A', reference=ref, document_number='1234567890'))
    resolve_procurements(conn)
    before = [tuple(r) for r in conn.execute('SELECT * FROM procurement_key ORDER BY namespace,value')]
    put(conn, Bid('Bidder B', reference=ref, document_number='9999999999'))
    with pytest.raises(ValueError, match='ambiguous council reference'):
        resolve_procurements(conn)
    assert before == [tuple(r) for r in conn.execute('SELECT * FROM procurement_key ORDER BY namespace,value')]


def test_new_many_document_reference_keeps_document_identities_distinct(conn):
    ref = '2022.BA1.1'
    for i, doc in enumerate(('1234567890','9999999999')):
        put(conn, Solicitation(doc))
        put(conn, Bid('Bidder ' + str(i), reference=ref, document_number=doc))
    put(conn, CouncilItem(ref))
    put(conn, BackgroundPdf('https://example.com/multiple.pdf', reference=ref, kind='bgrd'))
    resolve_procurements(conn)
    assert conn.execute('SELECT COUNT(DISTINCT procurement_id) FROM bid').fetchone()[0] == 2
    assert conn.execute('SELECT procurement_id FROM background_pdf').fetchone()[0] is None
    assert conn.execute('SELECT COUNT(*) FROM procurement_council_item').fetchone()[0] == 2


def test_ambiguous_reference_is_never_arbitrarily_exported_under_one_document(conn):
    ref = '2022.BA1.1'
    for i, doc in enumerate(('1234567890','9999999999')):
        put(conn, Solicitation(doc))
        put(conn, Bid('Bidder ' + str(i), reference=ref, document_number=doc))
    put(conn, BackgroundPdf('https://example.com/multiple.pdf', reference=ref, kind='bgrd'))
    resolve_procurements(conn)
    doc = build_export_document(conn, 'fixed')
    assert all(not sol['documents'] for sol in doc['solicitations'])
    assert len(doc['procurement_documents']) == 2
