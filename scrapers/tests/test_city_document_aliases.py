import hashlib
import json
from datetime import date

import pytest

from toronto_bids.models import Bid
from toronto_bids.store import db


def setup_alias(conn, tmp_path, monkeypatch):
    from tests.test_city_archived_awards import setup_archive, archive_fixture
    from toronto_bids.linking import city_crosswalk as cw
    from toronto_bids.linking.city_archived_awards import load_evidence
    from toronto_bids.linking import city_document_aliases as aliases
    _, publisher, registry = setup_archive(conn, tmp_path, monkeypatch)
    bundle = json.loads(registry.read_text()); bundle['proofs'] = []; registry.write_text(json.dumps(bundle))
    conn.execute('DELETE FROM bid')
    modern = '1745310659'
    db.upsert_row(conn, Bid('Actual submitted firm', document_number=modern, source='original', reference='2014.BD1.1'), overwrite=True)
    issued = date.fromisoformat(publisher['Issue_Date']).strftime('%B %d, %Y')
    closed = date.fromisoformat(publisher['Closing_Date']).strftime('%B %d, %Y')
    body = f'''<table><tr><td>Call number:</td><td>3905-14-0097</td></tr>
    <tr><td>Description:</td><td>See ARIBA Doc {modern}</td></tr>
    <tr><td>Issue date:</td><td>{issued}</td><td>Closing date:</td><td>{closed} at 12:00 Noon</td></tr></table>'''
    source = 'https://wx.toronto.ca/inter/pmmd/calls.nsf/0/'+'a'*32+'?OpenDocument'
    page = dict(source_url=source, archive_timestamp='20140501000000',
                archive_url='https://web.archive.org/web/20140501000000id_/'+source,
                http_status=200, sha256=hashlib.sha256(body.encode()).hexdigest(), html=body)
    proof = aliases.propose(conn, evidence=load_evidence(bundle, registry), document_number=publisher['Solicitation_Document_Number'],
                            archive_source_sha256=archive_fixture()[1]['sha256'], archive_row_ordinal=0, page=page)
    path = tmp_path/'aliases.json'; path.write_text(json.dumps(dict(version=1, reviews=[proof])))
    monkeypatch.setattr(aliases, 'PATH', path)
    return proof, path


def test_published_alias_preserves_city_id_and_source_bid_while_recovering_export(conn, tmp_path, monkeypatch):
    from toronto_bids.linking.procurement import resolve_procurements
    from toronto_bids.export.document import build_export_document
    proof, _ = setup_alias(conn, tmp_path, monkeypatch)
    # Establish the real publisher's identity before admitting its alternate identifier.
    from toronto_bids.linking import city_document_aliases as aliases
    path = aliases.PATH; monkeypatch.setattr(aliases, 'PATH', tmp_path/'empty.json')
    aliases.PATH.write_text(json.dumps(dict(version=1, reviews=[])))
    resolve_procurements(conn)
    city_id = conn.execute('SELECT procurement_id FROM solicitation').fetchone()[0]
    before = tuple(conn.execute('SELECT id,document_number,reference,bidder_name_raw,first_seen FROM bid').fetchone())
    monkeypatch.setattr(aliases, 'PATH', path)
    resolve_procurements(conn)
    assert conn.execute('SELECT procurement_id FROM solicitation').fetchone()[0] == city_id
    assert conn.execute('SELECT procurement_id FROM bid').fetchone()[0] == city_id
    assert tuple(conn.execute('SELECT id,document_number,reference,bidder_name_raw,first_seen FROM bid').fetchone()) == before
    sol = build_export_document(conn)['solicitations'][0]
    assert sol['bids'][0]['document_number'] == proof['alias_document_number']
    state = [tuple(r) for r in conn.execute('SELECT * FROM procurement')]
    resolve_procurements(conn)
    assert state == [tuple(r) for r in conn.execute('SELECT * FROM procurement')]


@pytest.mark.parametrize('change', ['remove', 'bytes', 'identifier', 'dates'])
def test_applied_alias_refuses_changed_or_removed_source(conn, tmp_path, monkeypatch, change):
    from toronto_bids.linking.procurement import resolve_procurements
    proof, path = setup_alias(conn, tmp_path, monkeypatch)
    resolve_procurements(conn)
    bundle = json.loads(path.read_text())
    if change == 'remove': bundle['reviews'] = []
    elif change == 'bytes': bundle['reviews'][0]['page']['html'] += 'changed'
    elif change == 'identifier': bundle['reviews'][0]['alias_document_number'] = '9999999999'
    else: conn.execute("UPDATE solicitation SET issue_date='2020-01-01'")
    path.write_text(json.dumps(bundle))
    with pytest.raises(ValueError): resolve_procurements(conn)


def test_unproven_two_document_anchor_merge_still_refused(conn):
    from toronto_bids.linking.procurement import _Identities
    ids = _Identities(conn)
    with pytest.raises(ValueError, match='Conflicting procurement anchors'):
        ids.join(ids.get('city_document', '1111111111'), ids.get('city_document', '2222222222'), '{}')


@pytest.mark.parametrize('change', ['call', 'unlabelled_alias', 'competing_alias', 'dates', 'foreign_host', 'same_identifier'])
def test_rehashed_original_page_still_requires_exact_labelled_identity(conn, tmp_path, monkeypatch, change):
    from toronto_bids.linking import city_document_aliases as aliases, city_crosswalk as cw
    from toronto_bids.linking.city_archived_awards import load_evidence
    proof, _ = setup_alias(conn, tmp_path, monkeypatch)
    page = dict(proof['page'])
    if change == 'call': page['html'] = page['html'].replace('3905-14-0097', '4000-14-0001')
    elif change == 'unlabelled_alias': page['html'] = page['html'].replace('See ARIBA Doc', 'Document')
    elif change == 'competing_alias': page['html'] = page['html'].replace('See ARIBA Doc 1745310659', 'See ARIBA Doc 1745310659 and Doc9999999999')
    elif change == 'dates': page['html'] = page['html'].replace('2014', '2020')
    elif change == 'foreign_host': page['source_url'] = page['source_url'].replace('wx.toronto.ca', 'example.com')
    else: page['html'] = page['html'].replace('1745310659', proof['document_number'])
    page['sha256'] = hashlib.sha256(page['html'].encode()).hexdigest()
    arc = proof['archived_award']
    with pytest.raises(ValueError):
        aliases.propose(conn, evidence=load_evidence(json.loads(cw.PROOFS.read_text()), cw.PROOFS),
                        document_number=proof['document_number'], archive_source_sha256=arc['source_sha256'],
                        archive_row_ordinal=arc['row_ordinal'], page=page)


def test_late_canonical_conflict_rolls_back_alias_and_proof(conn, tmp_path, monkeypatch):
    from toronto_bids.linking import city_document_aliases as aliases
    from toronto_bids.linking.procurement import resolve_procurements
    proof, path = setup_alias(conn, tmp_path, monkeypatch)
    empty = tmp_path/'empty.json'; empty.write_text(json.dumps(dict(version=1, reviews=[])))
    monkeypatch.setattr(aliases, 'PATH', empty); resolve_procurements(conn)
    monkeypatch.setattr(aliases, 'PATH', path)
    conn.execute('INSERT INTO solicitation_link(reference,document_number,method) VALUES (?,?,?)',
                 ('2014.BD1.1', '9999999999', 'test_conflict'))
    before = {t: [tuple(r) for r in conn.execute('SELECT * FROM '+t)]
              for t in ('procurement', 'procurement_key', 'bid', 'city_document_alias_evidence')}
    with pytest.raises(ValueError): resolve_procurements(conn)
    assert before == {t: [tuple(r) for r in conn.execute('SELECT * FROM '+t)] for t in before}


def test_coverage_independently_attributes_the_source_alias_and_detects_bad_canonical_ids(conn, tmp_path, monkeypatch):
    from toronto_bids.bid_coverage import audit
    from toronto_bids.linking.procurement import resolve_procurements
    proof, _ = setup_alias(conn, tmp_path, monkeypatch)
    resolve_procurements(conn)
    result = audit(conn)
    assert result['summary']['canonical_bid_attribution_mismatches'] == []
    assert proof['document_number'] not in {r['document_number'] for r in result['missing']}
    conn.execute('UPDATE bid SET procurement_id=NULL')
    assert proof['document_number'] in audit(conn)['summary']['canonical_bid_attribution_mismatches']


def test_one_reference_naming_both_published_aliases_is_not_a_sibling_conflict(conn, tmp_path, monkeypatch):
    from toronto_bids.linking.procurement import resolve_procurements
    proof, _ = setup_alias(conn, tmp_path, monkeypatch)
    resolve_procurements(conn)
    conn.execute('INSERT INTO solicitation_link(reference,document_number,method) VALUES (?,?,?)',
                 ('2014.BD1.1', proof['document_number'], 'test_existing_link'))
    resolve_procurements(conn)
    assert conn.execute('SELECT procurement_id FROM bid').fetchone()[0] == conn.execute('SELECT procurement_id FROM solicitation').fetchone()[0]


@pytest.mark.parametrize('quotation',[
    'Ariba Doc.1745310659',
    'Ariba Doc No. 1745310659',
    'Ariba Reference No. Doc1745310659',
    'Ariba Document Number 1745310659',
    'Ariba Doc No. 1745310659 Ariba Doc No. 1745310659',
])
def test_published_ariba_identifier_spelling_and_equal_repetitions(conn, tmp_path, monkeypatch, quotation):
    from toronto_bids.linking import city_document_aliases as aliases,city_crosswalk as cw
    from toronto_bids.linking.city_archived_awards import load_evidence
    proof,_=setup_alias(conn,tmp_path,monkeypatch);page=dict(proof['page'])
    page['html']=page['html'].replace('See ARIBA Doc 1745310659',quotation)
    page['sha256']=hashlib.sha256(page['html'].encode()).hexdigest()
    source=page['source_url'].replace('/0/','/'+'b'*32+'/');page['source_url']=source
    page['archive_url']='https://web.archive.org/web/'+page['archive_timestamp']+'id_/'+source
    arc=proof['archived_award']
    result=aliases.propose(conn,evidence=load_evidence(json.loads(cw.PROOFS.read_text()),cw.PROOFS),
                           document_number=proof['document_number'],archive_source_sha256=arc['source_sha256'],
                           archive_row_ordinal=arc['row_ordinal'],page=page)
    assert result['alias_document_number']=='1745310659'


def test_cancelled_original_call_cannot_silently_become_a_published_alias(conn,tmp_path,monkeypatch):
    from toronto_bids.linking import city_document_aliases as aliases,city_crosswalk as cw
    from toronto_bids.linking.city_archived_awards import load_evidence
    proof,_=setup_alias(conn,tmp_path,monkeypatch);page=dict(proof['page'])
    page['html']=page['html'].replace('See ARIBA Doc','This call is cancelled. See ARIBA Doc')
    page['sha256']=hashlib.sha256(page['html'].encode()).hexdigest();arc=proof['archived_award']
    with pytest.raises(ValueError):
        aliases.propose(conn,evidence=load_evidence(json.loads(cw.PROOFS.read_text()),cw.PROOFS),
                        document_number=proof['document_number'],archive_source_sha256=arc['source_sha256'],
                        archive_row_ordinal=arc['row_ordinal'],page=page)
