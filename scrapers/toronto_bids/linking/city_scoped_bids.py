"""Source-reviewed scopes, response observations and price variants for City reports.

One participation summary per proven firm coexists with detailed response cells.
PDF rectangles bind each cell to its scope and supplier; paid extraction is absent.
"""
import json
from pathlib import Path
import re

from toronto_bids import pdf_witness
from toronto_bids.extract import EXTRACTOR_VERSION
from toronto_bids.linking.city_cached_bids import reviewed_context
from toronto_bids.linking.city_crosswalk import canonical,digest,supplier_identity,reviewed_links
from toronto_bids.models import Bid,BidScope,BidResponse,BidResponsePrice
from toronto_bids.store import db

PATH=Path(__file__).resolve().parents[1]/'data/city_scoped_bid_reviews.json'


def _fail(proof,reason):
    raise ValueError(f"Scoped City bid review {proof['reference']}: {reason}; re-review required")


def _name(text):
    return pdf_witness.normalized(text).strip(' *+')


def projection(proof,quotes,document_number):
    """Validate source relationships and derive participation summaries, without writes."""
    witnesses=proof['witnesses']
    from toronto_bids.linking.city_cached_bids import _DOC
    if any(set(_DOC.findall(v))-{proof['document_number']} for v in quotes.values()):_fail(proof,'source witness names another procurement')
    def quote(key):
        if key not in quotes:_fail(proof,'missing source witness '+str(key))
        return quotes[key]
    def name_keys(item):
        return item.get('name_witnesses') or [item['name_witness']]
    def name_quote(item):
        return ' '.join(quote(k) for k in name_keys(item))
    participants={}
    for p in proof['participants']:
        if p['key'] in participants:_fail(proof,'repeated participant key')
        name=p['name']
        if supplier_identity(_name(name_quote(p)))!=supplier_identity(name):
            _fail(proof,'participant name is not the complete literal source name')
        if supplier_identity(name) in {supplier_identity(v['name']) for v in participants.values()}:
            _fail(proof,'one source firm cannot become several participants')
        participants[p['key']]=p
    scopes={}
    for s in proof['scopes']:
        if s['key'] in scopes or s['kind'] not in ('part','lot','category','region','unspecified'):
            _fail(proof,'scope key/kind unsupported')
        if not s['areas']:_fail(proof,'scope has no source area')
        for area in s['areas']:pdf_witness.rectangle(area)
        headers=s.get('header_witnesses',[])
        if s['kind']!='unspecified' and not headers:_fail(proof,'named scope lacks original heading')
        for key in headers:
            quote(key)
            if not pdf_witness.in_areas(witnesses[key],s['areas']):_fail(proof,'scope heading is outside its areas')
        if s.get('status') and s['status'].casefold() not in quote(s['status_witness']).casefold():
            _fail(proof,'scope outcome is not stated by its source')
        scopes[s['key']]=s
    # A review cannot widen a scope rectangle to consume another named leaf scope.
    for key,s in scopes.items():
        for other,t in scopes.items():
            if key==other:continue
            leaf=t.get('leaf_header_witness')
            if leaf and pdf_witness.in_areas(witnesses[leaf],s['areas']):_fail(proof,'scope area crosses another scope heading')
    responses=[];keys=set();receipts=set();identities=set()
    for r in proof['responses']:
        key=(r['scope'],r['key'])
        if key in keys or r['scope'] not in scopes:_fail(proof,'response key/scope repeated or absent')
        keys.add(key);scope=scopes[r['scope']]
        if r['kind'] not in ('submitted','no_bid','unscoped_outcome','unresolved'):_fail(proof,'unsupported response kind')
        row=quote(r['row_witness']);literal_name=name_quote(r);raw_name=r['name']
        if _name(literal_name)!=raw_name:_fail(proof,'source response name was changed')
        party_area=r['party_area'];pdf_witness.rectangle(party_area)
        if not all(pdf_witness.contains(party_area,witnesses[k]) for k in name_keys(r)) or not pdf_witness.contains(party_area,witnesses[r['row_witness']]):
            _fail(proof,'response/name outside its supplier area')
        if not pdf_witness.in_areas(witnesses[r['row_witness']],scope['areas']):
            _fail(proof,'response outside its source scope')
        participant=r.get('participant')
        if participant is not None:
            if participant not in participants or supplier_identity(raw_name)!=supplier_identity(participants[participant]['name']):
                _fail(proof,'response supplier is not its proven participant')
        elif r['kind'] not in ('no_bid','unresolved') and not r.get('identity_reason'):_fail(proof,'submitted supplier identity remains unresolved')
        for other in proof['responses']:
            if name_keys(other)==name_keys(r) or supplier_identity(other['name'])==supplier_identity(raw_name):continue
            if all(pdf_witness.contains(party_area,witnesses[k]) for k in name_keys(other)):
                _fail(proof,'supplier area includes a different supplier')
        alternative=r.get('alternative_label')
        if alternative:
            if alternative not in quote(r['alternative_witness']):_fail(proof,'alternative label not literal')
            if not pdf_witness.contains(r['alternative_area'],witnesses[r['row_witness']]) or not pdf_witness.contains(r['alternative_area'],witnesses[r['alternative_witness']]):
                _fail(proof,'response/alternative label outside its alternative area')
        signature=(r['scope'],participant or raw_name,alternative)
        if signature in identities:_fail(proof,'repeated response needs a source-labelled alternative')
        identities.add(signature)
        if r['kind']=='submitted' and not r['prices'] and re.search(r'\bno\s+bid\b',row,re.I):_fail(proof,'explicit No Bid cannot become a submission')
        if r['kind']=='no_bid' and (not re.search(r'\bno\s+bid\b',row,re.I) or r['prices']):_fail(proof,'No Bid is not a priced submission')
        if r['kind'] in ('unresolved','unscoped_outcome') and r['prices']:_fail(proof,'unresolved/unscoped outcome cannot carry scoped prices')
        if r['kind']=='unscoped_outcome' and scope['kind']!='unspecified':_fail(proof,'unstated outcome scope cannot become a named lot')
        if r.get('status'):
            binding=dict(party=participant or raw_name,witness=r['status_witness'],alternative=alternative)
            if binding not in scope.get('outcome_bindings',[]):_fail(proof,'outcome is not reviewed for this supplier and scope')
            if r['status'].casefold() not in quote(r['status_witness']).casefold():_fail(proof,'outcome not literally supported')
            marker=r.get('status_marker')
            if marker and marker not in literal_name+row+''.join(quote(p['value_witness']) for p in r['prices']):
                _fail(proof,'outcome marker absent from this response')
        if participant and r['kind'] in ('submitted','unscoped_outcome'):receipts.add(participant)
        price_keys=set()
        for p in r['prices']:
            if p['key'] in price_keys:_fail(proof,'repeated price variant')
            price_keys.add(p['key'])
            value=quote(p['value_witness']);header=quote(p['header_witness'])
            if p['raw']!=value:_fail(proof,'price is not the complete original cell')
            if p['kind'] not in ('money','discount_percentage','other'):_fail(proof,'unsupported quoted value kind')
            if p['kind']=='money' and '%' in value:_fail(proof,'percentage cannot become money')
            if p['kind']=='discount_percentage' and ('%' not in value or p.get('unit')!='percent_off_MSRP' or not re.search(r'(?:percentage\s+off|discount|off\s+MSRP)',header,re.I)):_fail(proof,'percentage cannot become a money total')
            cell=witnesses[p['value_witness']]
            if not pdf_witness.contains(witnesses[r['row_witness']],cell) or not pdf_witness.in_areas(cell,scope['areas']) or not pdf_witness.contains(party_area,cell):
                _fail(proof,'price cell crosses response, scope or supplier')
            if alternative and not pdf_witness.contains(r['alternative_area'],cell):_fail(proof,'price belongs to another alternative')
            for key in p.get('basis_witnesses',[]):quote(key)
            basis=p.get('hst_basis')
            if basis not in (None,'including','excluding','net_of_all_applicable_taxes','net_of_HST'):_fail(proof,'unsupported tax basis')
            if basis:
                tax=quote(p['tax_witness'])
                pattern={'including':r'(?:incl(?:uding)?\.?\s*H\.?S\.?T\.?)','excluding':r'(?:excl(?:uding)?\.?\s*H\.?S\.?T\.?)','net_of_all_applicable_taxes':r'net of all applicable taxes','net_of_HST':r'net\s+of\s+H\.?S\.?T\.?'}[basis]
                if not re.search(pattern,tax,re.I):_fail(proof,'tax basis absent from literal header')
        responses.append(r)
    if receipts!=set(participants):_fail(proof,'participation includes a firm with no supported receipt')
    expected={(i,j) for i,c in enumerate(proof['cache_contract_bid_counts']) for j in range(c)}
    accounted={(v['contract_index'],v['bid_index']) for v in proof['cache_accounting']}
    if expected!=accounted or len(accounted)!=len(proof['cache_accounting']) or any(not a.get('reason') for a in proof['cache_accounting']):
        _fail(proof,'original cached observations are not accounted for exactly once')
    return [Bid(p['name'],reference=proof['reference'],document_number=document_number,source=proof['source']) for p in participants.values()]


def reviewed_scoped_reports(conn,*,registry=None,corpus=None):
    """Preflight every applicable scoped review before any archive mutation."""
    bundle=json.loads(Path(registry or PATH).read_text())
    if bundle.get('version')!=1 or bundle.get('extractor_version')!=EXTRACTOR_VERSION:raise ValueError('Unsupported scoped City review registry')
    proofs=bundle['reviews'];hashes={digest(p) for p in proofs}
    for old in conn.execute('SELECT * FROM city_scoped_bid_evidence'):
        p=json.loads(old['proof_json'])
        fields=('document_number','reference','source','report_url','report_sha256','result_sha256')
        if digest(p)!=old['proof_sha256'] or any(old[k]!=p[k] for k in fields) or old['participant_count']!=len(p['participants']) or old['response_count']!=len(p['responses']) or old['price_count']!=sum(len(r['prices']) for r in p['responses']):raise ValueError('Stored scoped City evidence is not intact')
        if old['proof_sha256'] not in hashes:raise ValueError('Applied scoped City review removed/changed; explicit repair required')
    selected=[];seen=set()
    for p in proofs:
        key=(p['reference'],p['source'])
        if key in seen:raise ValueError('Repeated scoped City source/reference')
        seen.add(key)
        prior=conn.execute('SELECT 1 FROM city_scoped_bid_evidence WHERE proof_sha256=?',(digest(p),)).fetchone()
        present=conn.execute('SELECT 1 FROM solicitation WHERE document_number=?',(p['document_number'],)).fetchone()
        item=conn.execute('SELECT 1 FROM council_item WHERE reference=?',(p['reference'],)).fetchone()
        if not prior and not(present and item):continue
        if corpus is not None and p['source']!=corpus:continue
        if not present or not item:_fail(p,'target or council item disappeared')
        if p['source'] not in ('ba_report','bd_report') or p['extractor_version']!=EXTRACTOR_VERSION:_fail(p,'source/extractor version changed')
        if conn.execute('SELECT 1 FROM city_bid_evidence WHERE reference=? AND source=?',key).fetchone():_fail(p,'overlaps an applied scalar review; explicit migration required')
        selected.append((p,bool(prior)))
    if not selected:return {}
    historical=reviewed_links(conn,persist=False);result={}
    for p,prior in selected:
        row,contracts,document_number,path=reviewed_context(conn,p,historical,previously_approved=prior)
        if p['cache_contract_bid_counts']!=[len(c.get('bids',[])) for c in contracts]:_fail(p,'cache collection/count changed')
        if p.get('ocr_text_proof_sha256'):
            from toronto_bids import config
            from toronto_bids.linking.city_crosswalk import sections
            from toronto_bids.pdf_text import record_for
            stored=record_for(conn,p['report_sha256'])
            if not stored or stored['method']!='tesseract' or stored['proof_sha256']!=p['ocr_text_proof_sha256']:
                _fail(p,'OCR text provenance missing or changed')
            agenda=config.COUNCIL_AGENDAS_DIR/(p['reference'].rsplit('.',1)[0]+'.html')
            if agenda.exists():
                import hashlib
                source=agenda.read_bytes()
                chunks=sections(source,p['reference'].rsplit('.',1)[0]).get(p['reference'],[])
                if hashlib.sha256(source).hexdigest()!=p['corroborating_agenda_sha256'] or chunks!=[p['corroborating_transcription']]:
                    _fail(p,'independent City transcription changed')
            elif not prior:_fail(p,'new image review requires original City agenda')
            if path.exists():quotes=pdf_witness.verify_ocr(p['witnesses'],path,p['report_sha256'],json.loads(stored['provenance_json']))
            elif prior:quotes={k:pdf_witness.normalized(v['text']) for k,v in p['witnesses'].items()}
            else:_fail(p,'new image review requires original PDF')
            quotes=pdf_witness.corroborated_quotes(p,quotes,p['corroborating_transcription'])
        elif p.get('ocr_corrections'):_fail(p,'native PDF review cannot alter source quotations')
        elif path.exists():quotes=pdf_witness.verify(p['witnesses'],path,p['report_sha256'])
        elif prior:quotes={k:pdf_witness.normalized(v['text']) for k,v in p['witnesses'].items()}
        else:_fail(p,'new scoped review requires original PDF')
        bids=projection(p,quotes,document_number)
        result[p['report_url']]=(p,bids)
    return result


def persist_scoped_reviews(conn,reviews):
    """Persist validated proofs and child rows after participation rows are rebuilt."""
    for p,bids in reviews.values():
        ph=digest(p);ref=p['reference'];source=p['source'];doc=p['document_number']
        if p.get('ocr_text_proof_sha256'):
            from toronto_bids.pdf_text import record_for,persist_record
            persist_record(conn,record_for(conn,p['report_sha256']))
        conn.execute('INSERT OR IGNORE INTO city_scoped_bid_evidence (proof_sha256,document_number,reference,source,report_url,report_sha256,result_sha256,participant_count,response_count,price_count,proof_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            (ph,doc,ref,source,p['report_url'],p['report_sha256'],p['result_sha256'],len(bids),len(p['responses']),sum(len(r['prices']) for r in p['responses']),canonical(p)))
        scope_rows=[BidScope(ref,source,s['key'],s['kind'],ph,document_number=doc,scope_label=s.get('label'),scope_status=s.get('status')) for s in p['scopes']]
        db.rebuild_rows(conn,scope_rows,scope='reference=? AND source=?',params=(ref,source))
        scope_ids={r['scope_key']:r['id'] for r in conn.execute('SELECT scope_key,id FROM bid_scope WHERE reference=? AND source=?',(ref,source))}
        bid_ids={}
        for participant,bid in zip(p['participants'],bids):
            row=conn.execute('SELECT id FROM bid WHERE reference=? AND document_number IS ? AND bidder_name_raw=? AND source=? AND bid_price IS NULL',(ref,bid.document_number,bid.bidder_name_raw,source)).fetchone()
            if not row:raise ValueError('Scoped review participation row missing')
            bid_ids[participant['key']]=row['id']
        response_rows=[BidResponse(scope_ids[r['scope']],r['key'],r['name'],'resolved' if r.get('participant') else ('unlinked' if r['kind']=='no_bid' else 'unresolved'),r['kind'],pdf_witness.reviewed_quote(p,r['row_witness']),bid_id=bid_ids.get(r.get('participant')),alternative_label=r.get('alternative_label'),status=r.get('status')) for r in p['responses']]
        marks=','.join('?' for _ in scope_ids)
        db.rebuild_rows(conn,response_rows,scope=f'scope_id IN ({marks})',params=tuple(scope_ids.values()))
        response_ids={(r['scope_id'],r['response_key']):r['id'] for r in conn.execute(f'SELECT scope_id,response_key,id FROM bid_response WHERE scope_id IN ({marks})',tuple(scope_ids.values()))}
        prices=[]
        for r in p['responses']:
            rid=response_ids[(scope_ids[r['scope']],r['key'])]
            for v in r['prices']:
                prices.append(BidResponsePrice(rid,v['key'],v['raw'],v['kind'],pdf_witness.reviewed_quote(p,v['header_witness']),pdf_witness.reviewed_quote(p,v['value_witness']),unit=v.get('unit'),price_basis='; '.join(pdf_witness.reviewed_quote(p,k) for k in v.get('basis_witnesses',[])) or None,hst_basis=v.get('hst_basis')))
        ids=tuple(response_ids.values());marks=','.join('?' for _ in ids)
        if prices:db.rebuild_rows(conn,prices,scope=f'response_id IN ({marks})',params=ids)
        else:conn.execute(f'DELETE FROM bid_response_price WHERE response_id IN ({marks})',ids)


def replay_scoped_bids(conn,*,registry=None):
    reviews=reviewed_scoped_reports(conn,registry=registry)
    if not reviews:return 0
    conn.execute('SAVEPOINT scoped_city_bids')
    try:
        for source in ('bd_report','ba_report'):
            selected=[(p,b) for p,b in reviews.values() if p['source']==source]
            if not selected:continue
            refs=[p['reference'] for p,b in selected];marks=','.join('?' for _ in refs)
            db.rebuild_rows(conn,[bid for p,bids in selected for bid in bids],scope=f'source=? AND reference IN ({marks})',params=(source,*refs))
        persist_scoped_reviews(conn,reviews)
        conn.execute('RELEASE scoped_city_bids')
    except BaseException:
        conn.execute('ROLLBACK TO scoped_city_bids');conn.execute('RELEASE scoped_city_bids');raise
    return sum(len(bids) for p,bids in reviews.values())
