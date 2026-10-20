"""#217: identifiers and metadata survive cached extraction backfill."""
import json
import pytest

from toronto_bids.extract import EXTRACTOR_VERSION
from toronto_bids.extraction import backfill_from_extraction
from toronto_bids.store import db
from toronto_bids.buyers import seed_buyers


def cache(conn, corpus, contracts, *, tag='one', reference=None):
    seed_buyers(conn)
    base={'trca':'https://pub-trca.escribemeetings.com/',
          'ep':'https://www.toronto.ca/legdocs/mmis/2025/ep/bgrd/',
          'zoo':'https://www.toronto.ca/legdocs/mmis/2025/zb/bgrd/',
          'award_summary':'https://example.com/'}[corpus]
    url=base+tag+'.pdf'
    conn.execute('INSERT INTO background_pdf(url,kind,sha256,text,reference,document_number) VALUES (?,?,?,?,?,?)',
                 (url,'award_summary' if corpus=='award_summary' else 'agency_board',tag,'held text',reference,'5909867893' if corpus=='award_summary' else None))
    db.mark_extracted(conn,tag,EXTRACTOR_VERSION,json.dumps({'contracts':contracts}))
    conn.commit()
    return url


def contract(ref):
    return {'reference':ref,'bids':[{'supplier_name':'Bidder','amount_raw':'$10'}],
            'awards':[{'supplier_name':'Winner','amount_raw':'$10'}]}


@pytest.mark.parametrize('corpus,raw,expected',[
    ('trca','RFQ No. 10008808','10008808'),
    ('trca','# 10008808','10008808'),
    ('ep','Contract No. 21-076-17696 (RFT No. ep101-2021)','EP101-2021'),
    ('ep','Contract No. 19-085-98518','19-085-98518'),
    ('zoo','  rfp 18   (2018-03)  ','RFP 18 (2018-03)'),
])
def test_agency_keys_use_the_body_canonical_reference(conn,corpus,raw,expected):
    cache(conn,corpus,[contract(raw)])
    backfill_from_extraction(conn,corpus)
    for table in ['agency_solicitation','agency_bid','agency_award']:
        assert conn.execute(f'SELECT native_ref FROM {table}').fetchone()[0]==expected


@pytest.mark.parametrize('corpus',['ep','zoo'])
def test_reference_less_single_contract_uses_a_council_item(conn,corpus):
    ref='2025.EP1.2' if corpus=='ep' else '2025.ZB1.2'
    cache(conn,corpus,[contract(None)],reference=ref)
    backfill_from_extraction(conn,corpus)
    assert conn.execute('SELECT native_ref FROM agency_award').fetchone()[0]==ref


def test_meeting_level_reference_cannot_combine_unrelated_reports(conn):
    urls=[cache(conn,'ep',[contract(None)],tag=tag,reference='2025.EP1') for tag in ('one','two')]
    backfill_from_extraction(conn,'ep')
    assert {r[0] for r in conn.execute('SELECT native_ref FROM agency_award')}==set(urls)


def test_missing_references_in_a_multi_contract_report_are_reported_not_combined(conn):
    cache(conn,'ep',[contract(None),contract(None)],reference='2025.EP1.2')
    logs=[]
    result=backfill_from_extraction(conn,'ep',log=logs.append)
    assert result['contracts_refused']==2
    assert conn.execute('SELECT count(*) FROM agency_award').fetchone()[0]==0
    assert any('ambiguous' in line for line in logs)


def test_agency_dates_and_bid_metadata_are_retained(conn):
    data=contract('10008808')
    data.update(posted_date='2025-01-02',closed_date='2025-02-03',decision_date='2025-03-04')
    data['bids'][0].update(amount_basis='plus_HST',status='non_compliant',price_header='Price excluding HST')
    cache(conn,'trca',[data]);backfill_from_extraction(conn,'trca')
    row=conn.execute('SELECT hst_basis,status,price_header FROM agency_bid').fetchone()
    assert tuple(row)==('excluding','non_compliant','Price excluding HST')
    assert tuple(conn.execute('SELECT posted_date,closing_date FROM agency_solicitation').fetchone())==('2025-01-02','2025-02-03')
    assert conn.execute('SELECT award_date FROM agency_award').fetchone()[0]=='2025-03-04'


def test_city_bid_status_and_available_header_are_retained_without_a_prompt_change(conn):
    data=contract(None)
    data['bids'][0].update(status='non_compliant',price_header='Published header')
    cache(conn,'award_summary',[data]);backfill_from_extraction(conn,'award_summary')
    assert tuple(conn.execute('SELECT status,price_header FROM bid').fetchone())==('non_compliant','Published header')


@pytest.mark.parametrize('canonical_exists',[False,True])
def test_normalizing_held_aliases_preserves_history_and_removes_only_exact_duplicates(conn,canonical_exists):
    from toronto_bids.models import AgencySolicitation,AgencyBid,AgencyAward
    cache(conn,'trca',[contract('10008808')])
    buyer=conn.execute("SELECT id FROM buyer WHERE slug='trca'").fetchone()[0]
    refs=['10008808','RFQ No. 10008808'] if canonical_exists else ['RFQ No. 10008808']
    for ref in refs:
        db.upsert_row(conn,AgencySolicitation(buyer_id=buyer,native_ref=ref,source='trca_board'),overwrite=False)
        db.upsert_row(conn,AgencyBid(buyer_id=buyer,native_ref=ref,bidder_name_raw='Bidder',bid_price='$10',source='trca_board'),overwrite=True)
        db.upsert_row(conn,AgencyAward(buyer_id=buyer,native_ref=ref,supplier_name_raw='Winner',award_amount='$10',source='trca_board'),overwrite=True)
    for table in ('agency_solicitation','agency_bid','agency_award'):
        conn.execute(f"UPDATE {table} SET first_seen='2000-01-01' WHERE native_ref='RFQ No. 10008808'")
    conn.commit()
    backfill_from_extraction(conn,'trca')
    for table in ('agency_solicitation','agency_bid','agency_award'):
        assert tuple(conn.execute(f'SELECT native_ref,first_seen FROM {table}').fetchone())==('10008808','2000-01-01')
        assert conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0]==1


def test_report_url_fallback_retains_case():
    from toronto_bids.linking.agency_reference import normalize_agency_reference
    url='https://example.com/Report.pdf?DocumentId=123'
    assert normalize_agency_reference('trca',url)==url


def test_ambiguous_numbered_reference_is_refused_explicitly(conn):
    cache(conn,'trca',[contract('RFT 10008808 and 10009999')])
    logs=[]
    result=backfill_from_extraction(conn,'trca',log=logs.append)
    assert result['contracts_refused']==1
    assert any('ambiguous TRCA reference' in line for line in logs)


def test_nullable_metadata_columns_migrate_without_losing_existing_rows(conn):
    conn.execute("INSERT INTO bid(bidder_name_raw,source) VALUES ('Legacy','test')")
    conn.execute('ALTER TABLE bid DROP COLUMN status')
    conn.execute('ALTER TABLE agency_bid DROP COLUMN status')
    conn.commit()
    db.init_db(conn)
    assert tuple(conn.execute('SELECT bidder_name_raw,status FROM bid').fetchone())==('Legacy',None)
    assert 'status' in {r[1] for r in conn.execute('PRAGMA table_info(agency_bid)')}
