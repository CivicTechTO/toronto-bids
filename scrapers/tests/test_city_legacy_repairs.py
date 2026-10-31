"""Primary-source counterexamples to the legacy winner/value identity matcher."""
import pytest
from toronto_bids.models import Award,Solicitation,Bid,CouncilItem,BackgroundPdf
from toronto_bids.store import db
from toronto_bids.sources.bid_award_panel import match_on_supplier_and_value
from toronto_bids.linking.procurement import resolve_procurements


def put(conn,row):
 db.upsert_row(conn,row,overwrite=True)


@pytest.mark.parametrize('left,right,value,reference,issued',[
 ('Integrated Building and Design Corporation','D CRUPI AND SONS','1720000','2014.BD174.2','2025-04-01'),
 ('Bond Paving and Construction Inc.','SANSCON CONSTRUCTION LIMITED','224400','2015.BD25.1','2021-05-07'),
 ('Same Distinctive Firm Ltd.','Same Distinctive Firm Limited','100','2014.BD1.1','2025-01-01'),
 ('Unrelated Construction Ltd.','Other Construction Ltd.','100','2014.BD1.1','2014-01-01'),
])
def test_equal_value_and_generic_words_or_impossible_date_cannot_fill_title(conn,left,right,value,reference,issued):
 put(conn,Solicitation('5000000001',issue_date=issued))
 put(conn,Award('5000000001',supplier_name_raw=right,award_amount=value,source='odata'))
 items=[dict(reference=reference,title='Wrong historical title',winner_raw=left,award_value=float(value))]
 assert match_on_supplier_and_value(conn,items,'council_pre_ariba')==0
 assert conn.execute('SELECT title FROM solicitation').fetchone()[0] is None


@pytest.mark.parametrize('left,right',[('Co-X-Co Construction Limited','Co-X-Co Construction Ltd.'),('Canada Construction Limited','Canada Construction Limited')])
def test_full_name_equivalence_keeps_short_or_generic_named_firms(conn,left,right):
 put(conn,Solicitation('5000000001',issue_date='2013-01-01'))
 put(conn,Award('5000000001',supplier_name_raw=right,award_amount='100',source='odata'))
 assert match_on_supplier_and_value(conn,[dict(reference='2013.BD1.1',title='Supported',winner_raw=left,award_value=100)],'council_pre_ariba')==1


def seeded_incorrect_joins(conn):
 import json
 from toronto_bids.linking.city_identity_repairs import PATH
 proofs=json.loads(PATH.read_text())['repairs'];before={}
 for proof in proofs:
  doc,ref=proof['rejected_document_number'],proof['reference']
  put(conn,Solicitation(doc,title=proof['source_title'],issue_date=proof['target_issue_date']))
  conn.execute("UPDATE solicitation SET title_source='council_pre_ariba' WHERE document_number=?",(doc,))
  put(conn,CouncilItem(ref,title=proof['source_title']))
  put(conn,BackgroundPdf(proof['source_url'],reference=ref,kind='bgrd'))
  put(conn,Bid('Held bidder',reference=ref,bid_price='$100',source='bid_award_panel'))
  pid=conn.execute('INSERT INTO procurement DEFAULT VALUES RETURNING id').fetchone()[0]
  for ns,value in [('city_document',doc),('city_council_bid',ref)]:
   conn.execute('INSERT INTO procurement_key VALUES (?,?,?)',(ns,value,pid))
  conn.execute('UPDATE solicitation SET procurement_id=? WHERE document_number=?',(pid,doc))
  conn.execute('UPDATE bid SET procurement_id=? WHERE reference=?',(pid,ref))
  before[doc]=pid
 conn.commit();return proofs,before


def test_reviewed_split_preserves_document_ids_and_raw_bids_and_replays(conn):
 import json
 proofs,prior=seeded_incorrect_joins(conn)
 bids=[tuple(r) for r in conn.execute('SELECT id,bidder_name_raw,bid_price,first_seen,status FROM bid ORDER BY id')]
 resolve_procurements(conn)
 for proof in proofs:
  doc,ref=proof['rejected_document_number'],proof['reference']
  assert conn.execute('SELECT procurement_id FROM solicitation WHERE document_number=?',(doc,)).fetchone()[0]==prior[doc]
  pid=conn.execute("SELECT procurement_id FROM procurement_key WHERE namespace='city_council_bid' AND value=?",(ref,)).fetchone()[0]
  assert pid!=prior[doc]
  assert conn.execute('SELECT procurement_id FROM bid WHERE reference=?',(ref,)).fetchone()[0]==pid
  assert conn.execute('SELECT title,title_source FROM solicitation WHERE document_number=?',(doc,)).fetchone()[:]==(None,None)
  assert json.loads(conn.execute('SELECT merge_evidence FROM procurement WHERE id=?',(pid,)).fetchone()[0])['rule']=='reviewed_city_identity_split'
 assert [tuple(r) for r in conn.execute('SELECT id,bidder_name_raw,bid_price,first_seen,status FROM bid ORDER BY id')]==bids
 before=[tuple(r) for r in conn.execute('SELECT * FROM procurement ORDER BY id')]
 resolve_procurements(conn)
 assert [tuple(r) for r in conn.execute('SELECT * FROM procurement ORDER BY id')]==before
 assert not conn.execute('PRAGMA foreign_key_check').fetchall()


def test_changed_review_context_rolls_back_all_splits(conn):
 proofs,prior=seeded_incorrect_joins(conn)
 conn.execute('UPDATE solicitation SET issue_date=? WHERE document_number=?',('2030-01-01',proofs[1]['rejected_document_number']))
 before=[tuple(r) for r in conn.execute('SELECT * FROM procurement_key ORDER BY namespace,value')]
 with pytest.raises(ValueError,match='context changed'):
  resolve_procurements(conn)
 assert [tuple(r) for r in conn.execute('SELECT * FROM procurement_key ORDER BY namespace,value')]==before


def test_publisher_title_is_not_removed_by_reviewed_split(conn):
 proofs,prior=seeded_incorrect_joins(conn)
 for p in proofs:conn.execute("UPDATE solicitation SET title='Publisher title',title_source=NULL WHERE document_number=?",(p['rejected_document_number'],))
 resolve_procurements(conn)
 assert {r[0] for r in conn.execute('SELECT title FROM solicitation')}=={'Publisher title'}


def test_corrected_table_replaces_stale_pairs_only_within_its_reference(conn):
 from pathlib import Path
 from toronto_bids.sources.bid_award_panel import store_bids
 html=(Path(__file__).parent/'fixtures/bid_award_panel/2015.BD25.1.html').read_text()
 put(conn,Bid('Serve Construction. Ltd.',reference='2015.BD25.1',bid_price='$ 286,536.36',source='bid_award_panel'))
 put(conn,Bid('Unrelated',reference='2015.BD25.2',bid_price='$99',source='bid_award_panel'))
 keep=tuple(conn.execute("SELECT * FROM bid WHERE reference='2015.BD25.2'").fetchone())
 assert store_bids(conn,{'2015.BD25':html})==8
 held=[dict(r) for r in conn.execute("SELECT * FROM bid WHERE reference='2015.BD25.1'")]
 assert len(held)==8 and not any(r['bidder_name_raw']=='Serve Construction. Ltd.' and r['bid_price']=='$ 286,536.36' for r in held)
 assert tuple(conn.execute("SELECT * FROM bid WHERE reference='2015.BD25.2'").fetchone())==keep
 before=[tuple(r) for r in conn.execute('SELECT id,first_seen FROM bid ORDER BY id')]
 store_bids(conn,{'2015.BD25':html})
 assert [tuple(r) for r in conn.execute('SELECT id,first_seen FROM bid ORDER BY id')]==before


def test_filtering_an_ambiguous_old_match_cannot_create_a_new_join(conn):
 for doc,issued in [('5000000001','2014-01-01'),('5000000002','2025-01-01')]:
  put(conn,Solicitation(doc,issue_date=issued))
  put(conn,Award(doc,supplier_name_raw='Same Firm Limited',award_amount='100',source='odata'))
 assert match_on_supplier_and_value(conn,[dict(reference='2014.BD1.1',title='Candidate',winner_raw='Same Firm Ltd.',award_value=100)],'council_pre_ariba')==0


def test_bridge_matcher_handles_empty_agenda_and_rejects_future_period(conn,monkeypatch):
 from toronto_bids.sources import bid_award_panel as p
 put(conn,Solicitation('5000000001',issue_date='2025-01-01'))
 put(conn,Award('5000000001',supplier_name_raw='Same Firm Limited',award_amount='100',source='odata'))
 monkeypatch.setattr(p,'parse_pre_ariba_awards',lambda html,meeting:[] if html=='empty' else [dict(reference='2014.BD1.1',winner_raw='Same Firm Ltd.',award_value=100)])
 assert p.match_pre_ariba_solicitations(conn,{'2013.BD1':'empty','2014.BD1':'item'})==0


def test_refused_reparse_retains_prior_rows_and_exposes_failure(conn):
 from toronto_bids.sources.bid_award_panel import store_bids
 put(conn,Bid('Prior observation',reference='2015.BD25.1',bid_price='$100',source='bid_award_panel'))
 before=[tuple(r) for r in conn.execute('SELECT * FROM bid')];failures=[]
 assert store_bids(conn,{'2015.BD25':'<h3>BD25.1 - Award of Tender 14-2015</h3>'},failures=failures)==0
 assert [tuple(r) for r in conn.execute('SELECT * FROM bid')]==before
 assert failures and failures[0][0]=='bid_table:2015.BD25.1'


def test_partial_reparse_cannot_sweep_prior_bid_observations(conn,monkeypatch):
 from toronto_bids.sources import bid_award_panel as p
 for name in ('First','Second'):put(conn,Bid(name,reference='2015.BD1.1',bid_price='$100',source='bid_award_panel'))
 monkeypatch.setattr(p,'parse_agenda',lambda *a:[{'reference':'2015.BD1.1'}])
 monkeypatch.setattr(p,'parse_bid_tables',lambda *a:[dict(reference='2015.BD1.1',document_number=None,bidder_name_raw='First',bid_price='$100')])
 before=[tuple(r) for r in conn.execute('SELECT * FROM bid ORDER BY id')];failures=[]
 assert p.store_bids(conn,{'2015.BD1':'held'},failures=failures)==0
 assert failures and [tuple(r) for r in conn.execute('SELECT * FROM bid ORDER BY id')]==before


def test_composite_title_matching_keeps_its_historical_period(conn):
 from toronto_bids.models import CompositeAward
 from toronto_bids.sources.bid_award_panel import match_composite_titles
 put(conn,Solicitation('5000000001',issue_date='2015-01-01'))
 put(conn,Award('5000000001',supplier_name_raw='Same Firm Limited',award_amount='100',source='odata'))
 put(conn,CompositeAward('1201-09-5016',reference='2009.BD1.1',title='Historical contract',supplier_name_raw='Same Firm Ltd.',award_value='100',source='bid_committee_composite'))
 assert match_composite_titles(conn)==0
 assert conn.execute('SELECT title FROM solicitation').fetchone()[0] is None
