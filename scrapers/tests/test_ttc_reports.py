import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from toronto_bids.buyers import seed_buyers
from toronto_bids.extract import EXTRACTOR_VERSION
from toronto_bids.extraction import backfill_from_extraction, CORPORA
from toronto_bids.sources.ttc_reports import capture_reports
from toronto_bids.store import db


def test_ttc_cached_extraction_keeps_bids_and_withholds_recommendations(conn):
    seed_buyers(conn)
    url='https://cdn.ttc.ca/Report.pdf'
    conn.execute("INSERT INTO background_pdf(url,kind,sha256,text) VALUES(?, 'ttc_board','hash','public report')",(url,))
    contract=dict(reference='C1-38',title='Wilson Carhouse North Expansion',decision_date='2013-03-27',
                  bids=[dict(supplier_name='Buttcon Limited',amount_raw='$37,700,190.00',amount_basis='including_HST')],
                  awards=[dict(supplier_name='Buttcon Limited',amount_raw='$37,700,190.00')])
    db.mark_extracted(conn,'hash',EXTRACTOR_VERSION,json.dumps(dict(contracts=[contract])))
    conn.commit()
    result=backfill_from_extraction(conn,'ttc')
    bid=conn.execute('SELECT * FROM agency_bid').fetchone()
    assert bid['native_ref']=='C1-38' and bid['hst_basis']=='including' and bid['result_stage']=='Board report'
    assert bid['report_url']==url and bid['source']=='ttc_board'
    assert not conn.execute('SELECT * FROM agency_award').fetchall()
    assert conn.execute('SELECT status FROM agency_solicitation').fetchone()[0] is None
    summary=json.loads(conn.execute("SELECT summary_json FROM agency_coverage WHERE corpus='ttc'").fetchone()[0])
    assert summary['accepted_bid_facts']==1 and summary['withheld_award_facts']==1
    assert 'ttc' in CORPORA


def test_report_html_error_is_queued_and_failure_is_visible(conn,tmp_path):
    calls=[]
    def handle(req):
        calls.append(req)
        return httpx.Response(200,content=b'<html>Login</html>')
    with httpx.Client(transport=httpx.MockTransport(handle)) as c:
        report=capture_reports(conn,inventory=[dict(url='https://cdn.ttc.ca/Report.pdf',basis='reviewed source')],
                               client=c,data_dir=tmp_path,sleep=lambda n:None)
    assert report['failures'] and report['captured']==0
    assert conn.execute('SELECT sha256 FROM background_pdf').fetchone()[0] is None
    assert len(calls)==1


@pytest.mark.parametrize('url',['http://cdn.ttc.ca/Report.pdf','https://other.org/Report.pdf','https://cdn.ttc.ca/login'])
def test_only_reviewed_ttc_pdf_origins_are_requested(conn,tmp_path,url):
    with pytest.raises(ValueError):
        capture_reports(conn,inventory=[dict(url=url,basis='reviewed')],data_dir=tmp_path)
    assert not list(tmp_path.iterdir())


def test_reviewed_inventory_hash_mismatch_never_replaces_source(conn, tmp_path):
    url = 'https://cdn.ttc.ca/report.pdf'
    conn.execute("INSERT INTO background_pdf(url,kind,sha256,text) VALUES(?,'ttc_board','previous','previous text')", (url,))
    conn.commit()
    payload = (Path(__file__).parent / 'fixtures/tiny.pdf').read_bytes()
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=payload))) as client:
        report = capture_reports(conn, inventory=[dict(url=url, basis='Reviewed report', sha256='0' * 64)],
                                 client=client, data_dir=tmp_path, sleep=lambda _: None)
    assert report['failures'] and report['captured'] == 0
    row = conn.execute('SELECT sha256,text FROM background_pdf WHERE url=?', (url,)).fetchone()
    assert tuple(row) == ('previous', 'previous text')


def test_pinned_review_admits_held_archive_report_without_fetch(conn, tmp_path):
    import hashlib
    url = 'https://cdn.ttc.ca/report.pdf'
    payload = (Path(__file__).parent / 'fixtures/tiny.pdf').read_bytes()
    path = tmp_path / 'held.pdf'
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    conn.execute("INSERT INTO background_pdf(url,kind,sha256,local_path,text) VALUES(?,'ttc_archive_report',?,?,?)",
                 (url, digest, str(path), 'Held public report text'))
    conn.commit()
    def unexpected(_):
        raise AssertionError('Reviewed held bytes should not be downloaded again')
    with httpx.Client(transport=httpx.MockTransport(unexpected)) as client:
        report = capture_reports(conn, inventory=[dict(url=url, basis='Source reviewed', sha256=digest)],
                                 client=client, data_dir=tmp_path, sleep=lambda _: None)
    assert report['held'] == 1 and not report['failures']
    assert conn.execute('SELECT kind FROM background_pdf WHERE url=?', (url,)).fetchone()[0] == 'ttc_board'


def test_no_bid_observations_are_separate_from_submitted_bid_facts(conn):
    seed_buyers(conn)
    url = 'https://cdn.ttc.ca/reviewed.pdf'
    conn.execute("INSERT INTO background_pdf(url,kind,sha256,text) VALUES(?,'ttc_board','reviewed','source')", (url,))
    contract = dict(reference='P6-14', title='Substation', prequalified=['Not a submitted bid'],
                    invited_to_tender=['Named decline retained outside bids'],
                    bids=[dict(supplier_name='Actual bidder', amount_raw='$12.00'),
                          dict(supplier_name='Explicit decline', amount_raw=None, status='no_bid'),
                          dict(supplier_name='Bridgecon', amount_raw='$21,293,529,50')], awards=[])
    db.mark_extracted(conn, 'reviewed', EXTRACTOR_VERSION, json.dumps(dict(contracts=[contract])))
    conn.commit()
    result = backfill_from_extraction(conn, 'ttc')
    assert result['coverage']['accepted_bid_facts'] == 3
    assert result['coverage']['accepted_no_bid_declarations'] == 1
    assert result['coverage']['accepted_submitted_bid_facts'] == 2
    assert {r[0] for r in conn.execute('SELECT bidder_name_raw FROM agency_bid')} == {'Actual bidder', 'Explicit decline', 'Bridgecon'}
    bad = conn.execute("SELECT bid_price,bid_price_numeric FROM agency_bid WHERE bidder_name_raw='Bridgecon'").fetchone()
    assert tuple(bad) == ('$21,293,529,50', None)
