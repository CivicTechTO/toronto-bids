"""Replay committed, source-reviewed City cache projections; no extraction or discovery.

Every projection binds the whole source and extraction context. Historical native-call
identities use the existing crosswalk gate; a modern Doc must identify the actual event,
not an earlier qualification. Literal table/row quotations retain the review evidence.
"""
import hashlib
import json
from pathlib import Path
import re

from toronto_bids import config
from toronto_bids.extract import EXTRACTOR_VERSION
from toronto_bids.linking.call_number import find_call_numbers
from toronto_bids.linking.city_crosswalk import canonical,digest
from toronto_bids.linking.document_number import normalize_document_number
from toronto_bids.models import Bid
from toronto_bids.store import db

PATH=Path(__file__).resolve().parents[1]/'data/city_cached_bid_reviews.json'
_DOC=re.compile(r'\bDoc(?:ument)?\.?(?:\s+(?:Number|No\.?))?\s*[:#]?\s*(\d{10})(?!\d)',re.I)


def _text(value):
    return ' '.join((value or '').split())


def _fail(proof,reason):
    raise ValueError(f"Reviewed City bid context changed for {proof['reference']}: {reason}; re-review required")


def reviewed_reports(conn,*,registry=None,corpus=None):
    """Return validated URL -> (proof, Bid rows), without changing the archive."""
    bundle=json.loads(Path(registry or PATH).read_text())
    if bundle.get('version')!=1 or bundle.get('extractor_version')!=EXTRACTOR_VERSION:
        raise ValueError('Unsupported reviewed City bid registry/version; re-review required')
    proofs=bundle['reviews'];hashes={digest(p) for p in proofs}
    for prior in conn.execute('SELECT * FROM city_bid_evidence'):
        old_proof=json.loads(prior['proof_json'])
        fields={'document_number':'document_number','reference':'reference','source':'source','report_url':'report_url',
                'report_sha256':'report_sha256','result_sha256':'result_sha256','source_role':'source_role'}
        if (digest(old_proof)!=prior['proof_sha256'] or any(prior[k]!=old_proof[v] for k,v in fields.items())
                or prior['bid_count']!=len(old_proof['bids'])):
            raise ValueError('Stored reviewed City bid evidence is not intact')
        if prior['proof_sha256'] not in hashes:
            raise ValueError('Applied City bid review removed/changed; explicit re-review/repair required')
    selected=[];keys=set()
    for proof in proofs:
        key=(proof['source'],proof['reference'])
        if key in keys:raise ValueError('Repeated reviewed City source/reference')
        keys.add(key)
        present=conn.execute('SELECT 1 FROM solicitation WHERE document_number=?',(proof['document_number'],)).fetchone()
        item=conn.execute('SELECT title FROM council_item WHERE reference=?',(proof['reference'],)).fetchone()
        prior=conn.execute('SELECT 1 FROM city_bid_evidence WHERE proof_sha256=?',(digest(proof),)).fetchone()
        if not (present and item) and not prior:continue
        if corpus is not None and proof['source']!=corpus:continue
        if not present or not item or item['title']!=proof['agenda_title']:_fail(proof,'council title or target missing/changed')
        selected.append(proof)
    if not selected:return {}
    historical={}
    if any(p['identity']['kind']=='city_crosswalk' for p in selected):
        from toronto_bids.linking.city_crosswalk import reviewed_links
        historical=reviewed_links(conn,persist=False)
    result={}
    for proof in selected:
        if proof['source'] not in ('ba_report','bd_report') or proof['extractor_version']!=EXTRACTOR_VERSION:
            _fail(proof,'source/version unsupported')
        row=conn.execute('SELECT b.*,e.result_json FROM background_pdf b LEFT JOIN extraction_cache e '
            'ON e.sha256=b.sha256 AND e.extractor_version=? WHERE b.url=?',(EXTRACTOR_VERSION,proof['report_url'])).fetchone()
        if (row is None or row['reference']!=proof['reference'] or row['kind']!='bgrd'
                or row['sha256']!=proof['report_sha256'] or not row['text'] or not row['result_json']
                or hashlib.sha256(row['text'].encode()).hexdigest()!=proof['text_sha256']
                or hashlib.sha256(row['result_json'].encode()).hexdigest()!=proof['result_sha256']):
            _fail(proof,'held report/text/cache missing or changed')
        path=Path(row['local_path']) if row['local_path'] else None
        if path is None or not path.exists():
            # Old captures can retain a workstation path after the archive moves.
            # A conventional local file is usable only if its bytes match the pinned hash.
            path=config.DATA_DIR/'documents'/'council'/Path(proof['report_url']).name
        prior=conn.execute('SELECT 1 FROM city_bid_evidence WHERE proof_sha256=?',(digest(proof),)).fetchone()
        if path is not None and path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest()!=proof['report_sha256']:_fail(proof,'held PDF bytes changed')
        elif not prior:_fail(proof,'new approval requires held original PDF')
        contracts=json.loads(row['result_json']).get('contracts',[])
        if proof['contract_count']!=1 or len(contracts)!=1 or proof['contract_index']!=0 or digest(contracts[0])!=proof['contract_sha256']:
            _fail(proof,'contract collection/position changed')
        contract=contracts[0];doc=proof['document_number'];ref=proof['reference'];identity=proof['identity']
        if identity['kind']=='city_crosswalk':
            link=historical.get(ref)
            if not link or link['document_number']!=doc or link['proof_sha256']!=identity['proof_sha256']:
                _fail(proof,'independent historical crosswalk missing or changed')
            if find_call_numbers(contract.get('reference'))!={link['call_number']} or link['call_number'] not in find_call_numbers(row['text']):
                _fail(proof,'native Call Number does not agree with reviewed identity')
            document_number=None
        elif identity['kind']=='primary_document':
            native=_DOC.findall(contract.get('reference') or '')
            direct=next(iter(native)) if len(set(native))==1 else (normalize_document_number(contract.get('reference')) if not find_call_numbers(contract.get('reference')) else None)
            alias=_text(identity.get('reference_quotation'))
            alias_bound=(alias.startswith('Solicitation Number:') and alias in _text(row['text'])
                         and set(_DOC.findall(alias))=={doc} and _text(contract.get('reference')) in alias)
            if set(_DOC.findall(proof['agenda_title']))!={doc} or (direct!=doc and not alias_bound) or doc not in _DOC.findall(row['text']):
                _fail(proof,'primary document role is not established by title/contract/source')
            document_number=doc
        else:_fail(proof,'unknown identity proof class')
        if conn.execute('SELECT 1 FROM solicitation_link WHERE reference=? AND document_number!=?',(ref,doc)).fetchone():
            _fail(proof,'conflicting stored council link')
        table=_text(proof['table_quotation']);body=_text(row['text'])
        if not table or table not in body:_fail(proof,'reviewed submission table changed')
        if set(_DOC.findall(table))-{doc}:_fail(proof,'bid table names a different document')
        raw=contract.get('bids',[]);indices=[b['raw_bid_index'] for b in proof['bids']]
        if any(type(i) is not int for i in indices) or sorted(indices)!=list(range(len(raw))):
            _fail(proof,'raw submission observations not accounted for exactly once')
        bids=[];names=set()
        for review in proof['bids']:
            quote=_text(review['row_quotation']);parts=review['name_parts']
            if not quote or quote not in table or not isinstance(parts,list) or not parts or not all(isinstance(p,str) and p.strip() for p in parts):
                _fail(proof,'row/name quotation missing from submission table')
            cursor=0
            for part in parts:
                offset=quote.find(_text(part),cursor)
                if offset<0:_fail(proof,'supplier name parts absent/out of order')
                cursor=offset+len(_text(part))
            name=' '.join(parts)
            if name in names:_fail(proof,'repeated supplier requires unsupported submission/lot scope')
            names.add(name)
            original=raw[review['raw_bid_index']]
            if review['role'] not in ('submitted_bid','informal_submission','prequalification_submission'):_fail(proof,'unknown participation role')
            if original.get('status')=='no_bid' and not (review['role']=='informal_submission' and re.search(r'\binformal\b',quote,re.I)):
                _fail(proof,'no-bid outcome cannot establish submitted participation')
            price,basis,header=review['bid_price'],review['hst_basis'],review['price_header']
            if price is not None:
                amounts=re.findall(r'\$[ \t]*\d[\d,]*(?:\.\d+)?[ \t]*[*+]*',quote)
                if not isinstance(price,str) or len(amounts)!=1 or _text(price)!=_text(amounts[0]):
                    _fail(proof,'price absent from one unambiguous reviewed row')
            if price is None and (basis is not None or header is not None):_fail(proof,'unpriced row cannot carry tax/price metadata')
            if basis not in (None,'including','excluding'):_fail(proof,'unsupported tax basis')
            if basis:
                if not header or _text(header) not in table:_fail(proof,'tax header absent')
                marker='including' if basis=='including' else 'excluding'
                if not re.search(r'\b'+marker+r'\s+H\.?S\.?T\.?\b|\b'+('Incl' if basis=='including' else 'Excl')+r'\.?\s+HST\b',header,re.I):
                    _fail(proof,'tax basis not literally stated by the header')
            bids.append(Bid(name,reference=ref,document_number=document_number,bid_price=price,hst_basis=basis,price_header=header,status=None,source=proof['source']))
        if not bids:_fail(proof,'empty reviewed projection')
        result[proof['report_url']]=(proof,bids)
    return result


def persist_evidence(conn,reviews):
    for proof,_ in reviews.values():
        conn.execute('INSERT OR IGNORE INTO city_bid_evidence '
            '(proof_sha256,document_number,reference,source,report_url,report_sha256,result_sha256,source_role,bid_count,proof_json) '
            'VALUES (?,?,?,?,?,?,?,?,?,?)', (digest(proof),proof['document_number'],proof['reference'],proof['source'],proof['report_url'],proof['report_sha256'],proof['result_sha256'],proof['source_role'],len(proof['bids']),canonical(proof)))


def replay_reviewed_bids(conn,*,registry=None):
    """Validate the entire applicable inventory, then replay only its reviewed source scopes.

    The caller owns commit. A savepoint keeps all projections/evidence atomic; canonical
    resolution wraps this in its own transaction, so a later identity conflict also rolls back.
    """
    reviews=reviewed_reports(conn,registry=registry)
    if not reviews:return 0
    conn.execute('SAVEPOINT reviewed_city_bids')
    try:
        for source in ('bd_report','ba_report'):
            selected=[(p,b) for p,b in reviews.values() if p['source']==source]
            if not selected:continue
            refs=[p['reference'] for p,_ in selected];marks=','.join('?' for _ in refs)
            db.rebuild_rows(conn,[bid for _,bids in selected for bid in bids],scope=f'source=? AND reference IN ({marks})',params=(source,*refs))
        persist_evidence(conn,reviews)
        conn.execute('RELEASE reviewed_city_bids')
    except BaseException:
        conn.execute('ROLLBACK TO reviewed_city_bids');conn.execute('RELEASE reviewed_city_bids');raise
    return sum(len(bids) for _,bids in reviews.values())
