"""Archive dates are publication evidence, never inferred council decision dates."""
import json

from toronto_bids.models import Solicitation
from toronto_bids.sources.odata import normalize_solicitation


def test_current_publisher_award_projection_retains_dates_and_removals():
    raw=dict(id='publisher-id',Solicitation_Document_Number='9000000042',Awarded_Suppliers=[
        dict(Successful_Bidder='Alpha Ltd.',Award_Amount='100.55',Date_Awarded='2014-04-10',AwardedDate=None)])
    sol=next(r for r in normalize_solicitation(raw) if isinstance(r,Solicitation))
    projection=json.loads(sol.publisher_award_identity_json)
    assert projection['Awarded_Suppliers'][0]['Date_Awarded']=='2014-04-10'
    raw['Awarded_Suppliers']=[]
    empty=next(r for r in normalize_solicitation(raw) if isinstance(r,Solicitation))
    assert json.loads(empty.publisher_award_identity_json)['Awarded_Suppliers']==[]
    # Existing identity proofs keep exactly their established field projection.
    assert 'Date_Awarded' not in json.loads(sol.publisher_identity_json)['Awarded_Suppliers'][0]


def test_original_portal_award_independently_links_a_native_call():
    import hashlib
    from toronto_bids.linking.city_archived_awards import ArchiveEvidence
    from tests.test_city_crosswalk import HTML,TITLE,REF,record
    publisher=record(Solicitation_Document_Number='9000000042',Awarded_Suppliers=[
        dict(Successful_Bidder='Alpha Construction Limited',Award_Amount='100000.55',Date_Awarded='2014-04-10',AwardedDate=None)])
    cells=['','','','','3905-14-0097','City division','Works','','Alpha Construction Ltd.','$100,000.55','04/10/2014','']
    source=('<html><p>All award dollar values listed below do not include Harmonized Sales Tax (HST).</p><table><tr>'+''.join(
        '<td><a href="/inter/pmmd/callawards.nsf/view/0123456789abcdef0123456789abcdef?OpenDocument">'+v+'</a></td>' if i==4 else '<td>'+v+'</td>' for i,v in enumerate(cells))+'</tr></table></html>').encode()
    metadata=dict(file='award.html',source_url='https://wx.toronto.ca/inter/pmmd/callawards.nsf/postedawards?OpenView',archive_timestamp='20140501000000',url='https://web.archive.org/web/20140501000000id_/https://wx.toronto.ca/inter/pmmd/callawards.nsf/postedawards?OpenView',sha256=hashlib.sha256(source).hexdigest(),status=200)
    evidence=ArchiveEvidence([publisher],[(metadata,source)])
    # A rounded recommendation does not substitute for the exact portal award.
    agenda=HTML.replace(b'$100,000.55',b'$100,001')
    proof=evidence.propose(reference=REF,title=TITLE,agenda=agenda,publisher=publisher,source_sha256=metadata['sha256'],row_ordinal=0)
    assert proof['proof_class']=='archived_publisher_award'
    assert proof['document_number']=='9000000042'
    assert proof['call_number']=='3905-14-0097'
    assert proof['published_award_date']=='2014-04-10'
    assert proof['witnesses']['archived_net_award']=='$100,000.55'
    assert '$100,001' in proof['agenda_recommendation_quotation']


def archive_fixture():
    import hashlib
    from tests.test_city_crosswalk import HTML,TITLE,REF,record
    publisher=record(Solicitation_Document_Number='9000000042',Awarded_Suppliers=[
        dict(Successful_Bidder='Alpha Construction Limited',Award_Amount='100000.55',Date_Awarded='2014-04-10',AwardedDate=None)])
    cells=['','','','','3905-14-0097','City division','Works','','Alpha Construction Ltd.','$100,000.55','04/10/2014','']
    source=('<html><p>All award dollar values listed below do not include Harmonized Sales Tax (HST).</p><table><tr>'+''.join(
        '<td><a href="/inter/pmmd/callawards.nsf/view/0123456789abcdef0123456789abcdef?OpenDocument">'+v+'</a></td>' if i==4 else '<td>'+v+'</td>' for i,v in enumerate(cells))+'</tr></table></html>').encode()
    metadata=dict(file='award.html',source_url='https://wx.toronto.ca/inter/pmmd/callawards.nsf/postedawards?OpenView',archive_timestamp='20140501000000',url='https://web.archive.org/web/20140501000000id_/https://wx.toronto.ca/inter/pmmd/callawards.nsf/postedawards?OpenView',sha256=hashlib.sha256(source).hexdigest(),status=200)
    return publisher,metadata,source,HTML,TITLE,REF


def setup_archive(conn,tmp_path,monkeypatch):
    import gzip,hashlib,zipfile
    from toronto_bids.linking import city_crosswalk as cw
    from toronto_bids.linking.city_archived_awards import ArchiveEvidence
    from toronto_bids.models import Bid,CouncilItem
    from toronto_bids.sources.odata import award_identity_record
    from toronto_bids.store import db
    publisher,metadata,body,agenda,title,ref=archive_fixture()
    evidence=ArchiveEvidence([publisher],[(metadata,body)])
    proof=evidence.propose(reference=ref,title=title,agenda=agenda,publisher=publisher,source_sha256=metadata['sha256'],row_ordinal=0)
    population=[award_identity_record(publisher)]
    (tmp_path/'awards.gz').write_bytes(gzip.compress(cw.canonical(population).encode()))
    (tmp_path/'legacy.gz').write_bytes(gzip.compress(b'[]'))
    with zipfile.ZipFile(tmp_path/'sources.zip','w') as archive:archive.writestr('award.html',body)
    bundle=dict(proofs=[proof],publisher_records=[],publisher_population_file='legacy.gz',publisher_population_sha256=cw.digest([]),publisher_population_count=0,
                archived_awards=dict(population_file='awards.gz',population_sha256=cw.digest(population),population_count=1,
                    sources_file='sources.zip',sources_sha256=hashlib.sha256((tmp_path/'sources.zip').read_bytes()).hexdigest(),sources=[metadata],snapshot_sha256=evidence.snapshot_sha256))
    registry=tmp_path/'proofs.json';registry.write_text(json.dumps(bundle))
    monkeypatch.setattr(cw,'PROOFS',registry);monkeypatch.setattr('toronto_bids.config.COUNCIL_AGENDAS_DIR',tmp_path)
    (tmp_path/'2014.BD1.html').write_bytes(agenda)
    for row in normalize_solicitation(publisher):db.upsert_row(conn,row,overwrite=True)
    db.upsert_row(conn,CouncilItem(ref,title=title),overwrite=True)
    db.upsert_row(conn,Bid('Beta bidder',reference=ref,source='original'),overwrite=True)
    conn.commit()
    return proof,publisher,registry


def test_original_portal_link_replays_preserving_facts_and_current_dates(conn,tmp_path,monkeypatch):
    from toronto_bids.linking.procurement import resolve_procurements
    proof,_,_=setup_archive(conn,tmp_path,monkeypatch)
    before=tuple(conn.execute('SELECT id,reference,document_number,bidder_name_raw,first_seen FROM bid').fetchone())
    resolve_procurements(conn)
    assert tuple(conn.execute('SELECT id,reference,document_number,bidder_name_raw,first_seen FROM bid').fetchone())==before
    assert conn.execute('SELECT procurement_id FROM bid').fetchone()[0]==conn.execute('SELECT procurement_id FROM solicitation').fetchone()[0]
    assert conn.execute('SELECT proof_class FROM city_crosswalk_evidence').fetchone()[0]=='archived_publisher_award'
    state=[tuple(r) for r in conn.execute('SELECT * FROM procurement')]
    resolve_procurements(conn)
    assert [tuple(r) for r in conn.execute('SELECT * FROM procurement')]==state
    (tmp_path/'2014.BD1.html').unlink()
    resolve_procurements(conn)


import pytest


@pytest.mark.parametrize('change',['date_removed','date_changed','suppliers_removed','base_suppliers_removed','competitor','source_bundle','registry_removed'])
def test_changed_archived_proof_context_refuses_before_merging(conn,tmp_path,monkeypatch,change):
    from toronto_bids.linking.procurement import resolve_procurements
    from toronto_bids.models import Award
    from toronto_bids.store import db
    proof,publisher,registry=setup_archive(conn,tmp_path,monkeypatch)
    if change=='registry_removed':
        resolve_procurements(conn);conn.commit()
        bundle=json.loads(registry.read_text());bundle['proofs']=[];registry.write_text(json.dumps(bundle))
    elif change=='source_bundle':(tmp_path/'sources.zip').write_bytes(b'changed')
    elif change=='base_suppliers_removed':
        current=json.loads(conn.execute('SELECT publisher_identity_json FROM solicitation').fetchone()[0]);current['Awarded_Suppliers']=[]
        conn.execute('UPDATE solicitation SET publisher_identity_json=?',(json.dumps(current),))
    elif change=='competitor':
        publisher=dict(publisher,id='other',Solicitation_Document_Number='9000000043')
        for row in normalize_solicitation(publisher):db.upsert_row(conn,row,overwrite=True)
    else:
        current=json.loads(conn.execute('SELECT publisher_award_identity_json FROM solicitation').fetchone()[0])
        if change=='suppliers_removed':current['Awarded_Suppliers']=[]
        else:current['Awarded_Suppliers'][0]['Date_Awarded']=None if change=='date_removed' else '2014-04-11'
        conn.execute('UPDATE solicitation SET publisher_award_identity_json=?',(json.dumps(current),))
    before=[tuple(r) for r in conn.execute('SELECT * FROM solicitation_link')]
    with pytest.raises(ValueError):resolve_procurements(conn)
    assert [tuple(r) for r in conn.execute('SELECT * FROM solicitation_link')]==before


@pytest.mark.parametrize('change',['cent','spelling','future','tax','bytes','redirect','reused_facts','publisher_competitor'])
def test_archive_source_identity_and_role_contradictions_refuse(change):
    import copy,hashlib
    from toronto_bids.linking.city_archived_awards import ArchiveEvidence
    publisher,meta,body,agenda,title,ref=archive_fixture();population=[publisher]
    if change=='cent':body=body.replace(b'$100,000.55',b'$100,000.56')
    if change=='spelling':body=body.replace(b'Alpha Construction Ltd.',b'Alphas Construction Ltd.')
    if change=='future':body=body.replace(b'04/10/2014',b'04/10/3014')
    if change=='tax':body=body.replace(b'do not include',b'include')
    if change=='reused_facts':
        import re
        row=re.search(rb'<tr>.*?</tr>',body)[0].replace(b'3905-14-0097',b'3905-14-0098')
        body=body.replace(b'</table>',row+b'</table>')
    if change=='publisher_competitor':population.append(dict(copy.deepcopy(publisher),id='other',Solicitation_Document_Number='9000000043'))
    if change!='bytes':meta['sha256']=hashlib.sha256(body).hexdigest()
    else:body+=b'changed'
    if change=='redirect':meta['final_url']=meta['url'].replace('20140501000000','20150501000000')
    with pytest.raises(ValueError):
        evidence=ArchiveEvidence(population,[(meta,body)])
        evidence.propose(reference=ref,title=title,agenda=agenda,publisher=publisher,source_sha256=meta['sha256'],row_ordinal=0)


def test_reviewed_publisher_proof_supersedes_a_prior_hold_without_repeated_splits(conn,tmp_path,monkeypatch):
    import hashlib
    from toronto_bids.linking import city_crosswalk as cw,city_identity_repairs as repairs
    from toronto_bids.linking.procurement import resolve_procurements
    from toronto_bids.models import BackgroundPdf
    from toronto_bids.store import db
    proof,_,_=setup_archive(conn,tmp_path,monkeypatch)
    source_url='https://www.toronto.ca/legdocs/mmis/2014/bd/bgrd/source.pdf'
    db.upsert_row(conn,BackgroundPdf(source_url,reference=proof['reference'],kind='bgrd'),overwrite=True)
    hold=dict(reference=proof['reference'],rejected_document_number=proof['document_number'],source_title=proof['source_title'],
              source_url=source_url,source_sha256='held-original',target_issue_date='2014-03-01',reason='Identity withheld pending independent proof.',
              superseded_by_crosswalk_sha256=cw.digest(proof))
    path=tmp_path/'repairs.json';path.write_text(json.dumps(dict(repairs=[hold])));monkeypatch.setattr(repairs,'PATH',path)
    resolve_procurements(conn)
    before=[tuple(r) for r in conn.execute('SELECT * FROM procurement ORDER BY id')]
    resolve_procurements(conn)
    assert [tuple(r) for r in conn.execute('SELECT * FROM procurement ORDER BY id')]==before
