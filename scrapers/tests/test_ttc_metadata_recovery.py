"""Replay exact held TTC results with source-reviewed metadata omissions."""
import json
from pathlib import Path

from toronto_bids.buyers import seed_buyers
from toronto_bids.extract import EXTRACTOR_VERSION
from toronto_bids.extraction import backfill_from_extraction
from toronto_bids.store import db
from toronto_bids.linking import agency_identity


def test_source_reviewed_bid_metadata_recovery(conn,monkeypatch):
    documents=json.loads((Path(__file__).parent/'fixtures/agencies/ttc_metadata_recovery_2026-10-29.json').read_text())
    seed_buyers(conn)
    existing=next(d for d in documents if d['native_ref']=='G85-351')
    conn.execute('INSERT INTO background_pdf(url,kind,sha256,text) VALUES (?,?,?,?)',
        (existing['url'],'ttc_board',existing['sha256'],'held source text'))
    db.mark_extracted(conn,existing['sha256'],EXTRACTOR_VERSION,existing['result_json'])
    conn.commit()
    evidence=agency_identity._evidence()
    with monkeypatch.context() as patch:
        patch.setattr(agency_identity,'_evidence',lambda:{k:v for k,v in evidence.items() if v['document_sha256']!=existing['sha256']})
        backfill_from_extraction(conn,'ttc')
    previous={r['id']:dict(r) for r in conn.execute('SELECT * FROM agency_bid')}
    assert len(previous)==12
    expected=set()
    basis={'including_HST':'including','plus_HST':'excluding'}
    for doc in documents:
        conn.execute('INSERT OR IGNORE INTO background_pdf(url,kind,sha256,text) VALUES (?,?,?,?)',
            (doc['url'],'ttc_board',doc['sha256'],'held source text'))
        db.mark_extracted(conn,doc['sha256'],EXTRACTOR_VERSION,doc['result_json'])
        contract=json.loads(doc['result_json'])['contracts'][0]
        omissions={(f['bid_index'],f['field']) for f in doc['withheld_bid_fields']}
        for index,bid in enumerate(contract['bids']):
            expected.add((doc['native_ref'],bid['supplier_name'],bid.get('amount_raw'),
                None if (index,'amount_basis') in omissions else basis.get(bid.get('amount_basis')),
                None if (index,'status') in omissions else bid.get('status'),doc['url']))
    conn.commit()
    raw=[tuple(r) for r in conn.execute('SELECT * FROM extraction_cache ORDER BY sha256')]
    backfill_from_extraction(conn,'ttc')
    found={tuple(r) for r in conn.execute('SELECT native_ref,bidder_name_raw,bid_price,hst_basis,status,report_url FROM agency_bid')}
    assert len(found)==129 and found==expected
    current={r['id']:dict(r) for r in conn.execute('SELECT * FROM agency_bid')}
    assert len(current)-len(previous)==117
    for row_id,old in previous.items():
        assert old['status'] in ('compliant','non_compliant') and current[row_id]['status'] is None
        for field in ('native_ref','bidder_name_raw','bid_price','bid_price_numeric','report_url','first_seen'):
            assert current[row_id][field]==old[field]
    assert sum(len(d['withheld_bid_fields']) for d in documents)==74
    assert [tuple(r) for r in conn.execute('SELECT * FROM extraction_cache ORDER BY sha256')]==raw
    coverage=json.loads(conn.execute("SELECT summary_json FROM agency_coverage WHERE corpus='ttc'").fetchone()[0])
    assert coverage['withheld_bid_metadata_fields']==74
    # All source-supported monetary strings and explicit participation outcomes survive;
    # in particular, foreign-currency text must never enter the CAD numeric column.
    foreign=conn.execute("SELECT bid_price_numeric FROM agency_bid WHERE bid_price LIKE '%USD%' OR bid_price LIKE '%U.S.%'").fetchall()
    assert foreign and all(r[0] is None for r in foreign)
    assert not conn.execute('SELECT * FROM agency_award').fetchall()
    assert not conn.execute('PRAGMA foreign_key_check').fetchall()
    before=[tuple(r) for r in conn.execute('SELECT id,native_ref,bid_price,hst_basis,status,first_seen FROM agency_bid ORDER BY id')]
    backfill_from_extraction(conn,'ttc')
    assert [tuple(r) for r in conn.execute('SELECT id,native_ref,bid_price,hst_basis,status,first_seen FROM agency_bid ORDER BY id')]==before
