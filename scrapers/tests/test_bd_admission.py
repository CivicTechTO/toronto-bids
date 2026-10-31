import json
from functools import partial

import pytest

from toronto_bids.bd_admission import admit_report
from toronto_bids.extract import EXTRACTOR_VERSION
from toronto_bids.extraction import backfill_from_extraction, extract_corpus
from toronto_bids.linking.call_number import find_call_numbers
from toronto_bids.models import BackgroundPdf, Bid, CouncilItem
from toronto_bids.store.db import mark_extracted, upsert_row as _upsert_row

upsert_row = partial(_upsert_row, overwrite=True)


def report(**changes):
    value = dict(reference='2014.BD198.1', title='Award of RFQ 0202-14-0081 for products',
                 text='RFQ 0202-14-0081\nAlpha Inc. $100.00\nBeta Ltd. $120.00',
                 result={'contracts': [{'reference':'RFQ 0202-14-0081', 'declared_submissions':2,
                     'bids':[{'supplier_name':'Alpha Inc.', 'amount_raw':'$100.00', 'amount_basis':'including_HST'},
                             {'supplier_name':'Beta Ltd.', 'amount_raw':'$120.00', 'status':'non_compliant'}]}]})
    value.update(changes)
    return value


def test_native_call_is_not_encoded_into_an_ariba_document():
    bids, decision = admit_report(**report())
    assert decision['accepted']
    assert decision['call_number'] == '0202-14-0081'
    assert len(bids) == 2
    assert {b.document_number for b in bids} == {None}
    assert {b.reference for b in bids} == {'2014.BD198.1'}
    assert bids[0].hst_basis == 'including'
    assert bids[1].status is None
    assert decision['statuses_withheld'] == 1


@pytest.mark.parametrize('changes', [
    {'title':'Award of RFQ 0202-14-0082'},
    {'title':None},
    {'title':'RFQ 0202-14-0081 and Tender 12-2014'},
    {'text':'No original report text'},
    {'text':None},
])
def test_independent_title_and_source_call_must_agree(changes):
    bids, decision = admit_report(**report(**changes))
    assert not bids
    assert not decision['accepted']


def test_multiple_calls_are_not_hidden_by_normalizing_only_the_first():
    assert find_call_numbers('RFQ 0202–14–0081; Tender 3 - 2014') == {'0202-14-0081','3-2014'}
    value=report()
    value['result']['contracts'][0]['reference']='RFQ 0202-14-0081 and Tender 3-2014'
    assert not admit_report(**value)[1]['accepted']


def test_multipart_is_held_even_when_model_returns_one_flat_contract():
    value=report()
    value['text'] += '\nBidders who bid on Part A\nBidders who bid on Part B'
    bids, decision=admit_report(**value)
    assert not bids
    assert 'part/lot scope' in decision['reason']


@pytest.mark.parametrize('change', ['count', 'name', 'price', 'duplicate', 'contracts'])
def test_unsupported_observations_refuse_the_report(change):
    value=report()
    c=value['result']['contracts'][0]
    if change=='count': c['declared_submissions']=3
    if change=='name': c['bids'][0]['supplier_name']='Invented Co.'
    if change=='price': c['bids'][0]['amount_raw']='$99.00'
    if change=='duplicate': c['bids'].append(dict(c['bids'][0]))
    if change=='contracts': value['result']['contracts'].append(dict(c))
    assert not admit_report(**value)[1]['accepted']


def test_qualified_prices_do_not_erase_supported_participation_or_mutate_cache():
    value=report()
    value['text'] += '\nBid prices include 30% miscellaneous'
    before=json.dumps(value['result'])
    bids, decision=admit_report(**value)
    assert decision['accepted'] and decision['prices_withheld']==2
    assert all(b.bid_price is None and b.hst_basis is None for b in bids)
    assert len(bids)==2
    assert json.dumps(value['result'])==before


def cache_report(conn, value, sha):
    upsert_row(conn, CouncilItem(reference=value['reference'],title=value['title']))
    upsert_row(conn, BackgroundPdf(url=f'https://example.com/{sha}.pdf',reference=value['reference'],
                                  kind='bgrd',sha256=sha,text=value['text']))
    mark_extracted(conn,sha,EXTRACTOR_VERSION,result_json=json.dumps(value['result']))
    conn.commit()


def test_backfill_preserves_refused_report_history_and_unrelated_sources(conn):
    accepted=report()
    refused=report(reference='2014.BD199.1',title='Unrelated agenda title')
    cache_report(conn,accepted,'good')
    cache_report(conn,refused,'held')
    upsert_row(conn,Bid(reference=refused['reference'],bidder_name_raw='Earlier supported fact',source='bd_report'))
    upsert_row(conn,Bid(reference=accepted['reference'],bidder_name_raw='Stale derived fact',source='bd_report'))
    upsert_row(conn,Bid(reference=accepted['reference'],bidder_name_raw='Agenda fact',source='agenda'))
    first=backfill_from_extraction(conn,'bd_report')
    assert first['bids_written']==2
    assert first['contracts_refused']==1
    assert len(first['admission'])==2
    rows=conn.execute('select id,bidder_name_raw,first_seen from bid order by id').fetchall()
    assert {r['bidder_name_raw'] for r in rows}=={'Alpha Inc.','Beta Ltd.','Earlier supported fact','Agenda fact'}
    backfill_from_extraction(conn,'bd_report')
    assert [tuple(r) for r in rows]==[tuple(r) for r in conn.execute('select id,bidder_name_raw,first_seen from bid order by id')]
    assert conn.execute('select count(*) from composite_award').fetchone()[0]==0


@pytest.mark.parametrize('text',[None,'',' \n\t '])
def test_textless_historical_report_is_visible_and_never_sent(conn,text):
    upsert_row(conn, BackgroundPdf(url='https://example.com/scan.pdf',reference='2016.BD1.1',kind='bgrd',sha256='scan',text=text))
    class NoCalls:
        def extract(self,text):
            raise AssertionError('textless report sent for extraction')
    stats=extract_corpus(conn,'bd_report',client=NoCalls(),labels={})
    assert stats['total']==1 and stats['no_text']==1


def test_cached_refusal_is_reported_to_enrichment_caller(conn, monkeypatch):
    from toronto_bids.extraction import extract_and_backfill
    cache_report(conn,report(title='No independently printed call'), 'refused')
    def no_key():
        raise ValueError('No key configured')
    monkeypatch.setattr('toronto_bids.extract.ExtractionClient',no_key)
    failures=[]
    result=extract_and_backfill(conn,'bd_report',failures=failures)
    assert result['contracts_refused']==1
    assert failures[0][0]=='coverage:bd_report'
    assert 'raw evidence retained' in failures[0][1]


def test_conflicting_reports_for_same_item_roll_back_before_replacing_facts(conn):
    cache_report(conn,report(),'first')
    value=report()
    value['result']['contracts'][0]['reference']='RFQ 0202-14-0082'
    cache_report(conn,value,'second')
    upsert_row(conn,Bid(reference=value['reference'],bidder_name_raw='Preserved',source='bd_report'))
    conn.commit()
    with pytest.raises(RuntimeError,match='mixed accepted/refused'):
        backfill_from_extraction(conn,'bd_report')
    assert [r[0] for r in conn.execute('select bidder_name_raw from bid')]==['Preserved']


@pytest.mark.parametrize('name,status', [('Alpha Inc. - Informal','no_bid'),('Alpha Inc.','no_bid')])
def test_informal_or_no_bid_claim_is_not_admitted_as_submitted_bid(name,status):
    value=report()
    value['result']['contracts'][0]['bids'][0].update(supplier_name=name,status=status)
    value['text'] += '\n'+name
    assert not admit_report(**value)[1]['accepted']


def test_standalone_backfill_returns_nonzero_for_held_historical_report(conn,monkeypatch):
    from toronto_bids import cli
    cache_report(conn,report(title='Missing independent call'),'cli-held')
    monkeypatch.setattr(cli,'_open_db',lambda:conn)
    monkeypatch.setattr('toronto_bids.extract.ExtractionClient',lambda **kw:object())
    args=cli.build_parser().parse_args(['extract','--corpus','bd_report','--backfill'])
    assert cli._cmd_extract_corpus(args)==1
