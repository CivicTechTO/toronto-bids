"""New agency identity proofs bind the whole report without requiring date edits."""
import copy,hashlib,json
import pytest
from toronto_bids.buyers import seed_buyers
from toronto_bids.extraction import backfill_from_extraction
from toronto_bids.extract import EXTRACTOR_VERSION
from toronto_bids.linking import agency_identity
from toronto_bids.store import db


@pytest.fixture
def context(conn,tmp_path,monkeypatch):
    url='https://www.toronto.ca/legdocs/mmis/2099/zb/bgrd/backgroundfile-1.pdf';sha='a'*64
    contract=dict(reference=None,title='Reviewed work',bids=[],awards=[dict(supplier_name='Fixture supplier',amount_raw=None)])
    other=dict(reference='RFP 2 (2099-01)',title='Other work',bids=[],awards=[dict(supplier_name='Another supplier',amount_raw=None)])
    raw=json.dumps(dict(contracts=[contract,other]))
    proof=dict(corpus='zoo',document_sha256=sha,extractor_version=EXTRACTOR_VERSION,
        contract_sha256=agency_identity.contract_fingerprint(contract),native_ref='RFP 1 (2099-01)',
        report_url=url,contract_count=2,contract_index=0,result_sha256=hashlib.sha256(raw.encode()).hexdigest(),
        identifiers=[dict(role='solicitation',value='RFP 1 (2099-01)',authority='toronto-zoo')],
        basis='Fixture primary source relationship, no date or price edits.',sources=[dict(url=url,sha256=sha,passages=['RFP 1 (2099-01)'])])
    path=tmp_path/'agency_identity_evidence.json'
    def save():
        path.write_text(json.dumps(dict(version=1,decisions=[proof])));agency_identity._evidence.cache_clear()
    save();monkeypatch.setattr(agency_identity.resources,'files',lambda _:tmp_path)
    seed_buyers(conn)
    conn.execute('INSERT INTO background_pdf(url,kind,sha256,text) VALUES (?,?,?,?)',(url,'agency_board',sha,'fixture source'))
    db.mark_extracted(conn,sha,EXTRACTOR_VERSION,raw)
    yield proof,raw,save
    agency_identity._evidence.cache_clear()


@pytest.mark.parametrize('change',['reviewed_payload','other_payload','count','url','document','version','missing_guard'])
def test_changed_reviewed_context_aborts_without_losing_awards(conn,context,monkeypatch,change):
    proof,raw,save=context
    backfill_from_extraction(conn,'zoo')
    before=[tuple(r) for r in conn.execute('SELECT * FROM agency_award ORDER BY id')]
    model=json.loads(raw)
    if change in ('reviewed_payload','other_payload'):
        model['contracts'][0 if change=='reviewed_payload' else 1]['title']+=' changed'
        db.mark_extracted(conn,proof['document_sha256'],EXTRACTOR_VERSION,json.dumps(model))
    if change=='count':
        model['contracts'].append(copy.deepcopy(model['contracts'][1]))
        db.mark_extracted(conn,proof['document_sha256'],EXTRACTOR_VERSION,json.dumps(model))
    if change=='url':conn.execute('UPDATE background_pdf SET url=?',(proof['report_url'].replace('file-1','file-2'),))
    if change=='document':
        conn.execute('UPDATE background_pdf SET sha256=?',('b'*64,));db.mark_extracted(conn,'b'*64,EXTRACTOR_VERSION,raw)
    if change=='version':monkeypatch.setattr(agency_identity,'EXTRACTOR_VERSION','unreviewed')
    if change=='missing_guard':del proof['result_sha256'];save()
    conn.commit()
    with pytest.raises(RuntimeError,match='existing rows kept'):backfill_from_extraction(conn,'zoo')
    assert [tuple(r) for r in conn.execute('SELECT * FROM agency_award ORDER BY id')]==before
