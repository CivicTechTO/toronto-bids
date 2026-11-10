"""Reviewed native-call participation within multi-contract City reports.

The cached extractor supplies the observation; pinned original count and bidder
roles support its admission. Award values, compliance and sibling contracts are
not projected into the bid. Original portal award facts establish identity.
"""
import hashlib
import json
from pathlib import Path
import re

from toronto_bids import config
from toronto_bids.extract import EXTRACTOR_VERSION
from toronto_bids.linking import city_crosswalk as cw
from toronto_bids.linking import city_archived_awards as archived
from toronto_bids.models import Bid
from toronto_bids.pdf_text import source_text
from toronto_bids.store import db

PATH=Path(__file__).resolve().parents[1]/'data/city_native_bid_reviews.json'
SOURCE='city_native_review'
_CALL=re.compile(r'^[ \t]*Call\s+(?:No\.?|Number)\s*:',re.I|re.M)
_COUNT=re.compile(r'\bNumber\s+of\s+(?:Bids|Proposals|Submissions)(?:\s+Received)?\s*:',re.I)


def _flat(text):return ' '.join((text or '').split())


def propose(conn,*,evidence,document_number,archive_source_sha256,archive_row_ordinal,
            report_url,scope_start,scope_end,contract_index=None,contract_indices=None,current=None,previously_approved=False):
    """Return a source-bound review proposal; never write facts or run a model."""
    index=archived.current_index(conn) if current is None else current
    award,publisher,key=archived.validate_current_award(conn,evidence,document_number,archive_source_sha256,archive_row_ordinal,index)
    row=conn.execute('SELECT * FROM background_pdf WHERE url=?',(report_url,)).fetchone()
    if row is None or not row['reference'] or row['kind']!='bgrd':raise ValueError('original City report absent')
    item=conn.execute('SELECT title FROM council_item WHERE reference=?',(row['reference'],)).fetchone()
    if not item:raise ValueError('original council context absent')
    text=source_text(conn,row)
    if not text:raise ValueError('original report text absent')
    path=Path(row['local_path']) if row['local_path'] else None
    if path is None or not path.exists():path=config.DATA_DIR/'documents/council'/Path(report_url).name
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('original PDF bytes changed')
    elif not previously_approved:raise ValueError('new native-call review requires held original PDF')
    cache=conn.execute('SELECT result_json FROM extraction_cache WHERE sha256=? AND extractor_version=?',(row['sha256'],EXTRACTOR_VERSION)).fetchone()
    if cache is None:raise ValueError('current shared extraction absent')
    raw=cache[0];contracts=json.loads(raw).get('contracts',[])
    call=award['call_number']
    matching=[i for i,contract in enumerate(contracts) if cw.find_call_numbers(contract.get('reference'))=={call}]
    selected=[contract_index] if contract_indices is None else contract_indices
    if (not matching or not isinstance(selected,list) or any(type(i) is not int for i in selected)
            or selected!=matching):raise ValueError('all cached native-call observations must be accounted for')
    observations=[]
    for position in selected:
        contract=contracts[position]
        if (contract.get('stage')!='single_stage' or contract.get('prequalified') or contract.get('invited_to_tender')
                or type(contract.get('declared_submissions')) is not int or contract['declared_submissions']!=1):
            raise ValueError('cached submission stage/count requires review')
        bids=contract.get('bids',[])
        if not bids or any(b.get('status')=='no_bid' for b in bids):raise ValueError('actual submitted observations required')
        observations.extend(dict(contract_index=position,raw_bid_index=i,**bid) for i,bid in enumerate(bids))
    markers=list(_CALL.finditer(text));bounds={m.start():markers[i+1].start() if i+1<len(markers) else len(text) for i,m in enumerate(markers)}
    if type(scope_start) is not int or type(scope_end) is not int or bounds.get(scope_start)!=scope_end:
        raise ValueError('review must retain the complete labelled contract block')
    block=text[scope_start:scope_end];label=_CALL.match(block)
    description=re.search(r'\bDescription\s*:',block,re.I)
    if not label or not description or cw.find_call_numbers(block[label.end():description.start()])!={call}:
        raise ValueError('original contract block names another or ambiguous call')
    counts=list(_COUNT.finditer(block))
    if len(counts)!=1:raise ValueError('one complete source submission-count field required')
    count_tail=block[counts[0].end():].lstrip()
    next_field=re.search(r'\n[ \t]*(?:Financial Impact|Contacts?|Call Dates|Ward No\.?|Recommended (?:Bidder|Proponent)|Contract Award Value)[ \t]*(?::|(?=\r?\n|$))',count_tail,re.I)
    if next_field:count_tail=count_tail[:next_field.start()]
    count_field=re.split(r'\n\s*\n',count_tail,maxsplit=1)[0]
    if not re.fullmatch(r'(?:One\s*\(1\)|One|1)(?:\s+(?:Bid|Proposal|Submission))?',_flat(count_field),re.I):
        raise ValueError('source count is not exactly one complete submission')
    if re.search(r'\b(?:non[- ]compliant|informal|additional)\s+(?:bids?|proposals?|submissions?)\b',block,re.I):
        raise ValueError('mixed/additional submission language requires review')
    recommendations=list(re.finditer(r'\bRecommended[ \t]+(?:Bidders?|Proponents?)(?:[ \t]*:[ \t]*|[ \t]*\r?\n)\s*(.+?)(?=\s*\bContract\s+Award\s+Value\s*:)',block,re.I|re.S))
    if len(recommendations)!=1:raise ValueError('single explicit recommended bidder role absent')
    supplier=_flat(recommendations[0][1]);qualification=None
    scoped_name=re.fullmatch(r'(.+?)\s+((?:\(Price Schedule\s+[A-Z]\s*[:–-]\s*[^()]+\)\s*[,;]?\s*)+)',supplier,re.I)
    if scoped_name:supplier=scoped_name[1];qualification=scoped_name[2].strip()
    if not supplier or any(cw.supplier_identity(supplier)!=cw.supplier_identity(b.get('supplier_name')) for b in observations):
        raise ValueError('cached participants do not equal the full source bidder')
    return dict(version=1,source=SOURCE,source_role='single_scoped_submission',document_number=document_number,
        call_number=call,reference=row['reference'],agenda_title=item['title'],report_url=report_url,
        report_sha256=row['sha256'],text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        result_sha256=hashlib.sha256(raw.encode()).hexdigest(),extractor_version=EXTRACTOR_VERSION,
        contract_indices=selected,scope_start=scope_start,scope_end=scope_end,
        scope_sha256=hashlib.sha256(block.encode()).hexdigest(),supplier_name=supplier,
        submission_count_quotation=_flat(count_field),recommendation_quotation=_flat(recommendations[0].group()),
        recommendation_scope_qualification=qualification,
        withheld_model_price=observations[0].get('amount_raw') if len(observations)==1 else None,
        withheld_model_status=observations[0].get('status') if len(observations)==1 else None,
        withheld_model_observations=observations,
        publisher_record_sha256=cw.digest(publisher),snapshot_sha256=evidence.snapshot_sha256,
        archived_award=dict(source_sha256=archive_source_sha256,row_ordinal=archive_row_ordinal,
                            row_sha256=award['row_sha256'],source_url=award['source']['url'],
                            printed_award_date=award['published_award_date']))


def replay(conn,*,registry=None):
    """Validate the whole review batch, then preserve/insert unpriced submissions."""
    bundle=json.loads(Path(registry or PATH).read_text())
    if bundle.get('version')!=1 or bundle.get('extractor_version')!=EXTRACTOR_VERSION:raise ValueError('unsupported native participation registry')
    reviews=bundle['reviews'];expected={cw.digest(p) for p in reviews}
    prior={r['proof_sha256']:dict(r) for r in conn.execute('SELECT * FROM city_native_bid_evidence')}
    for sha,row in prior.items():
        proof=json.loads(row['proof_json'])
        if cw.digest(proof)!=sha or sha not in expected or any(row[k]!=proof[k] for k in ('document_number','call_number','reference','report_url','report_sha256')):
            raise ValueError('applied native-call participation evidence changed or removed')
    pairs={(p['document_number'],p['supplier_name']) for p in reviews}
    for row in conn.execute('SELECT * FROM bid WHERE source=?',(SOURCE,)):
        if ((row['document_number'],row['bidder_name_raw']) not in pairs or row['reference'] is not None
                or any(row[k] is not None for k in ('bid_price','bid_price_numeric','hst_basis','price_header','status'))):
            raise ValueError('native participation rows changed; explicit repair required')
    applicable=[]
    for proof in reviews:
        present=conn.execute('SELECT 1 FROM solicitation WHERE document_number=?',(proof['document_number'],)).fetchone()
        item=conn.execute('SELECT 1 FROM council_item WHERE reference=?',(proof['reference'],)).fetchone()
        report=conn.execute('SELECT 1 FROM background_pdf WHERE url=?',(proof['report_url'],)).fetchone()
        sha=cw.digest(proof)
        if not (present and item and report):
            if sha in prior:raise ValueError('applied native participation source context removed')
            continue
        applicable.append(proof)
    if not applicable:return []
    archive_bundle=json.loads(cw.PROOFS.read_text());evidence=archived.load_evidence(archive_bundle,cw.PROOFS);index=archived.current_index(conn)
    selected=[];documents=set()
    for proof in applicable:
        sha=cw.digest(proof)
        if proof['document_number'] in documents:raise ValueError('multiple native participation reviews for one City event')
        documents.add(proof['document_number']);arc=proof['archived_award']
        actual=propose(conn,evidence=evidence,document_number=proof['document_number'],archive_source_sha256=arc['source_sha256'],
            archive_row_ordinal=arc['row_ordinal'],report_url=proof['report_url'],contract_indices=proof['contract_indices'],
            scope_start=proof['scope_start'],scope_end=proof['scope_end'],current=index,previously_approved=sha in prior)
        if actual!=proof:raise ValueError('native participation source / cache / proof changed')
        selected.append((sha,proof))
    for sha,proof in selected:
        db.upsert_row(conn,Bid(proof['supplier_name'],document_number=proof['document_number'],source=SOURCE),overwrite=True)
        conn.execute('INSERT OR IGNORE INTO city_native_bid_evidence(proof_sha256,document_number,call_number,reference,report_url,report_sha256,proof_json) VALUES (?,?,?,?,?,?,?)',
            (sha,proof['document_number'],proof['call_number'],proof['reference'],proof['report_url'],proof['report_sha256'],cw.canonical(proof)))
    return [p for _,p in selected]
