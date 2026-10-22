"""Replay real Zoo grid records from the permitted off-peak capture (#226)."""
import json
from pathlib import Path

import pytest

from toronto_bids.sources.bids_tenders import parse_listing

FIXTURE=Path(__file__).parent/'fixtures/agencies/bids_tenders_zoo_2026-10-21.json'
ROWS=json.loads(FIXTURE.read_text())


@pytest.mark.parametrize('row',[r for r in ROWS if r['Id'] in {
    'b6a63af4-0f9b-4ba3-bacf-5e075487b1ac','14d50919-e1f3-4d34-8dfb-2a633ee2dbe0',
    'b3c0c0f5-5593-480a-82cc-cbfb9a05f4b7','f80cedda-fb5d-43b5-9f8a-e10b5fc42955',
    'db6f7de3-19f3-43ba-8bc0-98209f8e7709','7dd876c9-66ca-4c53-89f3-01df544c0c6f',
    '8f01cee5-7a47-4529-9426-beef45b4004f','bc0f52fd-59e3-4c17-b935-a8311a1ad777',
    'd9f495ef-bd78-4d8c-ac29-2cf7e4714ec6'}],ids=lambda r:r['Id'])
def test_published_zoo_identifiers_are_preserved(row):
    expected={
        'b6a63af4-0f9b-4ba3-bacf-5e075487b1ac':'Q32202208',
        '14d50919-e1f3-4d34-8dfb-2a633ee2dbe0':'RFP 05.2023.03',
        'b3c0c0f5-5593-480a-82cc-cbfb9a05f4b7':'NRFP-08',
        'f80cedda-fb5d-43b5-9f8a-e10b5fc42955':'RFP 09 2023 03',
        'db6f7de3-19f3-43ba-8bc0-98209f8e7709':'RFP 19 2023 06',
        '7dd876c9-66ca-4c53-89f3-01df544c0c6f':'RFP 18 2023 05',
        '8f01cee5-7a47-4529-9426-beef45b4004f':'108',
        'bc0f52fd-59e3-4c17-b935-a8311a1ad777':'13',
        'd9f495ef-bd78-4d8c-ac29-2cf7e4714ec6':'127',
    }[row['Id']]
    result=parse_listing(row,1)
    assert result.native_ref==expected
    assert result.portal_url.endswith('/'+row['Id'])


def test_all_saved_live_zoo_records_parse_with_dates_and_stable_page_ids():
    assert len(ROWS)==len({row['Id'] for row in ROWS})==122
    parsed=[parse_listing(row,1) for row in ROWS]
    assert len({r.native_ref for r in parsed})==114
    for raw,result in zip(ROWS,parsed):
        assert result.native_ref==' '.join(raw['Title'].partition(' - ')[0].split()).upper()
        assert result.status==raw['Status']
        assert result.posted_date and result.closing_date
        assert result.portal_url.endswith('/'+raw['Id'])


@pytest.mark.parametrize('title',[
    'unstructured title','127 - ',' - missing identifier','UNKNOWN - missing number',
    'https://example.com/127 - invalid identifier',
    'd9f495ef-bd78-4d8c-ac29-2cf7e4714ec6 - opaque page id',
])
def test_zoo_unknown_layouts_remain_unlabelled_without_inventing_references(title):
    row=dict(ROWS[0],Title=title)
    assert parse_listing(row,1).native_ref is None


@pytest.mark.parametrize('page_id,reference,closed,status',[
    ('6fc2f901-6847-469e-b630-d09fc5e75cb9','RFQ-50','2022-11-04T16:00:00+00:00','Awarded'),
    ('ae4980d4-38f2-44cb-b5c4-0fe9ccc634b2','RFP-03','2026-02-06T17:00:00+00:00','Terminated'),
])
def test_specific_dated_zoo_positive_controls(page_id,reference,closed,status):
    raw=next(r for r in ROWS if r['Id']==page_id)
    result=parse_listing(raw,1)
    assert (result.native_ref,result.closing_date,result.status)==(reference,closed,status)


def test_full_zoo_capture_path_records_all_pages_and_stores_all_keys(conn,monkeypatch,tmp_path):
    import httpx
    from toronto_bids import config
    from toronto_bids.sources import bids_tenders as bt
    requests=[]
    def respond(request):
        if request.method=='GET':
            return httpx.Response(200,text='<input id="NodeId" value="zoo-node">'
                                  '<input name="__RequestVerificationToken" value="offline-token">')
        requests.append(request)
        start=int(request.url.params['start']);limit=int(request.url.params['limit'])
        assert request.url.params['status']==''
        return httpx.Response(200,json={'success':True,'total':122,'data':ROWS[start:start+limit]})
    client=httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(bt.httpx,'Client',lambda **kwargs:client)
    monkeypatch.setattr(bt.time,'sleep',lambda _:None)
    monkeypatch.setattr(config,'PORTAL_RECORDINGS_DIR',tmp_path/'recordings')
    result=bt.run_portal_capture(conn,only={'toronto-zoo'})
    assert result['toronto-zoo'] == 122
    assert [int(r.url.params['start']) for r in requests]==[0,50,100]
    assert len(list((tmp_path/'recordings').glob('*.json')))==122
    assert conn.execute('SELECT COUNT(*) FROM portal_listing').fetchone()[0]==122


def test_reused_portal_reference_cannot_silently_merge_different_listings(conn):
    from toronto_bids.buyers import seed_buyers
    from toronto_bids.sources.bids_tenders import store_listings
    rows=[r for r in ROWS if r['Title'].startswith('RFP-38 - ')]
    assert len(rows)==2
    assert store_listings(conn,rows,seed_buyers(conn)) == 2
    assert conn.execute('SELECT COUNT(DISTINCT procurement_id) FROM portal_listing').fetchone()[0] == 2


def test_unique_portal_identities_store_with_exact_positive_controls(conn):
    from collections import Counter
    from toronto_bids.buyers import seed_buyers
    from toronto_bids.sources.bids_tenders import store_listings
    ids=seed_buyers(conn)
    counts=Counter(parse_listing(row,ids['toronto-zoo']).native_ref for row in ROWS)
    rows=[row for row in ROWS if counts[parse_listing(row,ids['toronto-zoo']).native_ref]==1]
    assert len(rows)==106
    assert store_listings(conn,rows,ids)==106
    assert conn.execute('SELECT COUNT(*) FROM portal_listing').fetchone()[0]==106
    for ref,closed,status in [('RFQ-50','2022-11-04T16:00:00+00:00','Awarded'),
                              ('RFP-03','2026-02-06T17:00:00+00:00','Terminated')]:
        row=conn.execute('SELECT closing_date,status FROM portal_listing WHERE buyer_id=? AND native_ref=?',
                         (ids['toronto-zoo'],ref)).fetchone()
        assert tuple(row)==(closed,status)


def test_repeated_observation_of_same_page_does_not_trigger_identity_conflict(conn):
    from toronto_bids.buyers import seed_buyers
    from toronto_bids.sources.bids_tenders import store_listings
    row=next(r for r in ROWS if r['Id']=='6fc2f901-6847-469e-b630-d09fc5e75cb9')
    assert store_listings(conn,[row,dict(row,Status='Closed')],seed_buyers(conn))==2
    result=conn.execute('SELECT COUNT(*),MAX(status) FROM portal_listing').fetchone()
    assert tuple(result)==(1,'Awarded')


def test_reused_references_are_preserved_alongside_unique_references(conn):
    from toronto_bids.buyers import seed_buyers
    from toronto_bids.sources.bids_tenders import store_listings
    control=next(r for r in ROWS if r['Id']=='6fc2f901-6847-469e-b630-d09fc5e75cb9')
    conflicts=[r for r in ROWS if r['Title'].startswith('RFP-38 - ')]
    assert store_listings(conn,[control,*conflicts],seed_buyers(conn)) == 3
    assert conn.execute('SELECT COUNT(DISTINCT procurement_id) FROM portal_listing').fetchone()[0] == 3
