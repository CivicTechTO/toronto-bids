"""Shared reports and missing evidence must not be mistaken for actual zero bids."""
import json
from toronto_bids.bid_coverage import audit, write_report
from toronto_bids.extract import EXTRACTOR_VERSION
from toronto_bids.linking.procurement import resolve_procurements
from toronto_bids.models import Award, BackgroundPdf, Bid, Solicitation
from toronto_bids.store import db


def put(conn,row):
    db.upsert_row(conn,row,overwrite=True)


def awarded(conn,doc,year='2020',source='odata'):
    put(conn,Solicitation(doc,issue_date=year+'-01-01'))
    put(conn,Award(doc,supplier_name_raw='Winner',award_amount='$100',source=source))


def finish(conn):
    resolve_procurements(conn)
    return audit(conn)


def test_population_deduplicates_award_lines_and_excludes_ckan_only_and_orphans(conn):
    awarded(conn,'2000000001')
    put(conn,Award('2000000001',supplier_name_raw='Second winner',source='odata'))
    put(conn,Award('2000000001',supplier_name_raw='Winner',source='ckan_awarded'))
    awarded(conn,'2000000002',source='ckan_awarded')
    put(conn,Award('2000000003',source='odata'))
    result=finish(conn)
    assert result['summary']['awarded_city_solicitations']==1
    assert result['summary']['without_bids']==1
    assert len(result['missing'][0]['award_lines'])==2


def test_unique_reference_counts_but_shared_item_does_not_distribute_bids(conn):
    for doc in ('2000000001','2000000002','2000000003','2000000004'):awarded(conn,doc)
    conn.execute("INSERT INTO solicitation_link VALUES ('one','2000000001','test')")
    put(conn,Bid('Linked',reference='one',source='test'))
    put(conn,Bid('First',reference='many',document_number='2000000002',source='test'))
    put(conn,Bid('Second',reference='many',document_number='2000000003',source='test'))
    put(conn,Bid('Unknown contract',reference='many',source='test'))
    put(conn,BackgroundPdf('https://example.org/report',kind='committee_award',document_number='2000000004',reference='many',text='Doc2000000004'))
    result=finish(conn)
    assert result['summary']['with_bids']==3
    assert result['summary']['covered_only_through_reference_link']==1
    assert result['summary']['ambiguous_reference_count']==1
    assert result['summary']['canonical_bid_attribution_mismatches']==[]
    assert [r['document_number'] for r in result['missing']]==['2000000004']


def test_inconsistent_canonical_ids_are_reported(conn):
    awarded(conn,'2000000001');awarded(conn,'2000000002')
    put(conn,Bid('Bidder',document_number='2000000001',source='test'))
    resolve_procurements(conn)
    conn.execute("UPDATE bid SET procurement_id=(SELECT procurement_id FROM solicitation WHERE document_number='2000000002')")
    assert audit(conn)['summary']['canonical_bid_attribution_mismatches']==['2000000001','2000000002']


def test_prequalification_mention_is_only_a_candidate_not_recovered_bids(conn):
    awarded(conn,'2000000001')
    put(conn,BackgroundPdf('https://example.org/later-tender',kind='award_summary',document_number='2000000002',sha256='held',text='Open to suppliers prequalified under RFSQ Doc2000000001. Telephone 2000000003.'))
    raw=json.dumps({'contracts':[{'reference':'2000000002','bids':[{'supplier_name':'Later tender bidder'}]}]})
    conn.execute('INSERT INTO extraction_cache (sha256,extractor_version,result_json) VALUES (?,?,?)',('held',EXTRACTOR_VERSION,raw))
    result=finish(conn);row=result['missing'][0]
    assert result['summary']['with_bids']==0
    assert row['reports'][0]['association']=='printed_document_mention_only'
    assert row['reports'][0]['cached_named_bid_observations']==1
    assert row['reports'][0]['cached_contracts'][0]['reference']=='2000000002'
    assert result['summary']['confirmed_zero_bid_solicitations'] is None


def test_empty_or_stale_cache_is_not_zero_and_states_reconcile(conn):
    for i in range(1,6):awarded(conn,f'200000000{i}')
    for i,sha,text in [(1,None,None),(2,'no-text',None),(3,'stale','text'),(4,'current','text')]:
        put(conn,BackgroundPdf(f'https://example.org/{i}',kind='award_summary',document_number=f'200000000{i}',sha256=sha,text=text))
    for sha,version in [('stale','old'),('current',EXTRACTOR_VERSION)]:
        conn.execute('INSERT INTO extraction_cache (sha256,extractor_version,result_json) VALUES (?,?,?)',(sha,version,'{"contracts": []}'))
    result=finish(conn)
    assert result['summary']['groups']=={'current_cache_without_named_bids_needs_source_review':1,
        'held_document_without_text':1,'held_text_without_current_cache':1,
        'no_report_association_or_explicit_held_mention':1,'queued_document_not_held':1}
    assert sum(x['without_bids'] for x in result['summary']['issue_year_cohorts'].values())==5
    assert result['summary']['confirmed_zero_bid_solicitations'] is None


def test_old_era_bid_counts_and_audit_is_read_only_deterministic(conn,tmp_path):
    awarded(conn,'2000000001',year='2010');awarded(conn,'2000000002',year='2010')
    put(conn,Bid('Disclosed bidder',document_number='2000000001',source='test'))
    resolve_procurements(conn);conn.commit();before=conn.total_changes
    conn.execute('PRAGMA query_only=ON');one=audit(conn);two=audit(conn)
    assert one==two and conn.total_changes==before
    assert one['summary']['issue_year_cohorts']['through_2012']=={'awarded':2,'without_bids':1}
    write_report(one,tmp_path)
    assert json.loads((tmp_path/'audit.json').read_text())==one
    assert len((tmp_path/'missing.csv').read_text().splitlines())==2
