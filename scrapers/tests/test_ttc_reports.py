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
