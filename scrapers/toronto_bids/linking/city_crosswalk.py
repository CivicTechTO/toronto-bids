"""Reviewed City crosswalks: publisher assertions or independent compound facts.

Numeric Call Number encodings are deliberately absent from admission. Proposals
must survive a global publisher comparison and a pinned, single-item agenda
context. Only committed reviewed proofs become persistent identity links.
"""
import hashlib
import gzip
import json
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re

from lxml import html as html_parser
from toronto_bids.linking.call_number import find_call_numbers

PROOFS = Path(__file__).resolve().parents[1] / 'data/city_call_crosswalk.json'
_FEED = 'https://secure.toronto.ca/c3api_data/v2/DataAccess.svc/pmmd_solicitations/feis_solicitation_published'
_REF = re.compile(r'^\d{4}\.B[AD]\d+\.\d+$')
_SPLIT = re.compile(r'(B[AD]\d+\.\d+) - ')
_WINNER = re.compile(r'\bto\s+(.+?)\s+for\s+',re.I)
_NET = re.compile(r'\$\s*([\d,]+(?:\.\d+)?)\s+net of all applicable taxes(?: and charges)?',re.I)
_LEGAL = re.compile(r'\b(?:limited|ltd|incorporated|inc|corporation|corp|company|co|lp|llp|ulc)\b',re.I)


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def clean(text):
    return ' '.join((text or '').split())


def supplier_identity(name):
    """Full name with legal/punctuation typography only; no token/fuzzy matching."""
    text=re.sub(r'^the\s+','',(name or '').casefold()).replace('&',' and ')
    return re.sub(r'[^a-z0-9]','',_LEGAL.sub(' ',text))


def amount(value):
    try:
        text=re.sub(r'\s','',str(value) if value is not None else '')
        if not re.fullmatch(r'\$?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?',text):return None
        result=Decimal(text.replace('$','').replace(',',''))
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def fingerprint(issued,closed,kind,supplier,value):
    return (issued,closed,kind,supplier_identity(supplier),amount(value))


def publisher_index(records):
    index=defaultdict(set)
    for record in records:
        for native in re.finditer(r'\bCall number\s*:\s*([^\n]+)',record.get('Solicitation_Award_Details') or '',re.I):
            for call in find_call_numbers(native.group(1)):
                index[('native_call',call)].add((record.get('id'),record.get('Solicitation_Document_Number')))
        for award in record.get('Awarded_Suppliers') or []:
            key=fingerprint(record.get('Issue_Date'),record.get('Closing_Date'),record.get('Solicitation_Document_Type'),
                            award.get('Successful_Bidder'),award.get('Award_Amount'))
            index[key].add((record.get('id'),record.get('Solicitation_Document_Number')))
    return index


def sections(source,meeting):
    # Playwright agenda captures are UTF-8 even when an inherited page meta tag
    # advertises another charset. Keep adjacent HTML blocks/text nodes separate.
    root=html_parser.fromstring(source.decode('utf-8') if isinstance(source,bytes) else source)
    parts=_SPLIT.split(clean(' '.join(root.itertext())))
    by_ref=defaultdict(list)
    for i in range(1,len(parts)-1,2):
        by_ref[meeting[:4]+'.'+parts[i]].append(parts[i+1])
    return by_ref


def _date_witness(text,role,value):
    try:d=date.fromisoformat(value)
    except (ValueError,TypeError):return None
    m=re.search(rf'\b{role}\s*:\s*(?:{d:%B}|{d:%b})\.?\s+0?{d.day}\s*,?\s*{d.year}\b',text,re.I)
    return m.group() if m else None


def _source_type(title):
    m=re.search(r'\b(Request for Quotation|RFQ|Request for Proposal|RFP|Request for Tender|Tender(?: Call)?|RFT)\b',title,re.I)
    if not m:return None
    value=m.group().casefold()
    return 'RFQ' if value in ('request for quotation','rfq') else 'RFP' if value in ('request for proposal','rfp') else 'RFT'


def propose(*, reference,title,source,publisher,index,source_sha256,snapshot_sha256):
    """One auditable proposal or ValueError; this function never writes identities."""
    encoded=source.encode('utf-8') if isinstance(source,str) else source
    if hashlib.sha256(encoded).hexdigest()!=source_sha256:raise ValueError('source bytes disagree with supplied digest')
    if not _REF.fullmatch(reference):raise ValueError('invalid council reference')
    if not re.fullmatch(r'\d{10}',publisher.get('Solicitation_Document_Number') or ''):raise ValueError('publisher document key is not an exact stored identifier')
    chunks=sections(source,reference.rsplit('.',1)[0]).get(reference,[])
    if len(chunks)!=1:raise ValueError('agenda item missing or repeated')
    chunk=chunks[0];text=clean(chunk)
    if not text.startswith(clean(title)):raise ValueError('original agenda title disagrees with stored item')
    calls=find_call_numbers(title)
    if len(calls)!=1:raise ValueError('title does not identify one native call')
    call=next(iter(calls))
    fields=list(re.finditer(r'\bCall\s+(?:Number|No\.?)\s*:\s*(.+?)(?=\s+(?:Description|Recommended (?:Bidder|Proponent)|Contract Award Value)\s*:|$)',text,re.I))
    if not fields or any(find_call_numbers(f.group(1))!={call} for f in fields):raise ValueError('native call role is not unambiguous in agenda item')
    field=fields[0]
    kind=_source_type(title)
    if (kind is None or kind!=publisher.get('Solicitation_Document_Type')
            or any(_source_type(f.group(1)) not in (None,kind) for f in fields)):
        raise ValueError('source solicitation types disagree')
    issued,closed=publisher.get('Issue_Date'),publisher.get('Closing_Date')
    try:
        if date.fromisoformat(closed)<date.fromisoformat(issued):raise ValueError('publisher closing date precedes issue date')
    except (TypeError,ValueError) as exc:raise ValueError('publisher dates invalid') from exc
    iw,cw=_date_witness(chunk,'Issued',issued),_date_witness(chunk,'Closed',closed)
    if not iw or not cw:raise ValueError('exact issued/closed source witnesses absent')
    shared=dict(reference=reference,document_number=publisher.get('Solicitation_Document_Number'),call_number=call,
                source_title=title,source_url=f'https://secure.toronto.ca/council/report.do?meeting={reference.rsplit(".",1)[0]}&type=agenda',
                source_sha256=source_sha256,item_sha256=hashlib.sha256(chunk.encode()).hexdigest(),
                publisher_url=_FEED,publisher_record_sha256=digest(publisher),snapshot_sha256=snapshot_sha256,
                publisher_record_id=publisher.get('id'),issued_date=issued,closed_date=closed,
                solicitation_type=publisher.get('Solicitation_Document_Type'),
                witnesses=dict(call=field.group(),issued=iw,closed=cw))
    details=publisher.get('Solicitation_Award_Details') or ''
    directs=list(re.finditer(r'\bCall number\s*:\s*([^\n]+)',details,re.I))
    if directs:
        if len(directs)!=1:raise ValueError('multiple publisher native call assertions')
        direct=directs[0]
        if find_call_numbers(direct.group(1))!={call}:raise ValueError('publisher native call contradicts agenda')
        if index.get(('native_call',call))!={(publisher.get('id'),publisher.get('Solicitation_Document_Number'))}:raise ValueError('publisher native call is not unique')
        return dict(shared,proof_class='publisher_native_call',publisher_call_witness=direct.group(),
                    publisher_call_field='Solicitation_Award_Details')
    winner=_WINNER.search(chunk[:400]);value=_NET.search(chunk)
    if not winner or not value:raise ValueError('single named winner / explicit net award witness absent')
    # The first recommendation amount is the compared scope. Never choose a later
    # option, contingent sum or total just because it matches a feed amount.
    award_label=re.search(r'\bContract Award Value\s*:',text,re.I)
    if not award_label:raise ValueError('award value role is not explicit')
    scoped=text[award_label.end():]
    end=re.search(r'\b(?:Summary|Financial Impact|Background Information)\b',scoped,re.I)
    if end:scoped=scoped[:end.start()]
    first=_NET.search(scoped)
    if (not first or re.search(r'\bOption Year\b',scoped[:first.start()],re.I)
            or amount(first.group(1))!=amount(value.group(1))):raise ValueError('initial award scope disagrees')
    supplier=clean(winner.group(1));net=value.group(1)
    key=fingerprint(issued,closed,shared['solicitation_type'],supplier,net)
    if not key[3] or key[4] is None or index.get(key)!={(publisher.get('id'),publisher.get('Solicitation_Document_Number'))}:
        raise ValueError('compound facts do not identify one global publisher record')
    matches=[a for a in publisher.get('Awarded_Suppliers') or [] if
             supplier_identity(a.get('Successful_Bidder'))==key[3] and amount(a.get('Award_Amount'))==key[4]]
    if not matches:raise ValueError('publisher supplier / exact scoped amount mismatch')
    return dict(shared,proof_class='corroborated_compound',supplier_name=matches[0]['Successful_Bidder'],
                award_amount=matches[0]['Award_Amount'],report_supplier=supplier,
                witnesses=dict(shared['witnesses'],supplier=supplier,net_award=value.group()))


def reviewed_links(conn, *, registry=None, agendas=None):
    """Validate every applicable committed proof before writing any link or ledger row.

    A previously accepted immutable proof can travel with a released database
    when the original agenda files are unavailable. New approvals require held
    bytes and repeat proposal validation against the original publisher record.
    """
    from toronto_bids import config
    from toronto_bids.sources.odata import identity_record
    bundle=json.loads((registry or PROOFS).read_text())
    existing=[dict(r) for r in conn.execute('SELECT * FROM city_crosswalk_evidence')]
    for row in existing:
        evidence=json.loads(row['proof_json'])
        if digest(evidence)!=row['proof_sha256'] or any(row[k]!=evidence[k] for k in ('reference','document_number','call_number','proof_class')):
            raise ValueError('Stored City crosswalk evidence is not intact')
    expected={(p['reference'],p['document_number']) for p in bundle['proofs']}
    for row in conn.execute("SELECT reference,document_number FROM solicitation_link WHERE method='city_call_crosswalk'"):
        if (row['reference'],row['document_number']) not in expected:
            raise ValueError('Stored reviewed City crosswalk absent from registry; explicit repair required')
    applicable=[p for p in bundle['proofs'] if conn.execute('SELECT 1 FROM council_item WHERE reference=?',(p['reference'],)).fetchone()
                and conn.execute('SELECT 1 FROM solicitation WHERE document_number=?',(p['document_number'],)).fetchone()]
    if not applicable:return {}
    records=bundle['publisher_records']
    population=json.loads(gzip.decompress(((registry or PROOFS).parent/bundle['publisher_population_file']).read_bytes()))
    if digest(population)!=bundle['publisher_population_sha256'] or len(population)!=bundle['publisher_population_count']:
        raise ValueError('Reviewed City publisher population changed')
    index=publisher_index(population)
    approved=[]
    # Global uniqueness was measured against the complete original snapshot. Also
    # reject ambiguity introduced by any currently held (including archived) award.
    current=defaultdict(set)
    for r in conn.execute("SELECT s.document_number,s.issue_date,s.submission_deadline,s.rfx_type,a.supplier_name_raw,a.award_amount FROM solicitation s JOIN award a ON a.document_number=s.document_number WHERE a.source='odata'"):
        current[fingerprint(r['issue_date'],r['submission_deadline'],r['rfx_type'],r['supplier_name_raw'],r['award_amount'])].add(r['document_number'])
    by_record={r['id']:r for r in records}
    call_refs=defaultdict(set)
    for item in conn.execute('SELECT reference,title FROM council_item'):
        calls=find_call_numbers(item['title'])
        for call in calls:call_refs[call].add(item['reference'])
    directory=Path(agendas or config.COUNCIL_AGENDAS_DIR)
    for proof in applicable:
        ref,doc=proof['reference'],proof['document_number']
        item=conn.execute('SELECT title FROM council_item WHERE reference=?',(ref,)).fetchone()
        sol=conn.execute('SELECT * FROM solicitation WHERE document_number=?',(doc,)).fetchone()
        if not item or not sol:continue
        if (item['title']!=proof['source_title'] or sol['odata_id']!=proof['publisher_record_id']
                or sol['issue_date']!=proof['issued_date'] or sol['submission_deadline']!=proof['closed_date']
                or sol['rfx_type']!=proof['solicitation_type']):
            raise ValueError('Reviewed City crosswalk context changed: '+ref)
        latest=sol['publisher_identity_json']
        if not latest or json.loads(latest)!=identity_record(by_record[proof['publisher_record_id']]):
            raise ValueError('Reviewed City publisher identity snapshot changed or missing: '+ref)
        if call_refs[proof['call_number']]!={ref}:raise ValueError('Reviewed City call now has multiple council contexts: '+ref)
        if proof['proof_class']=='corroborated_compound':
            key=fingerprint(proof['issued_date'],proof['closed_date'],proof['solicitation_type'],proof['supplier_name'],proof['award_amount'])
            if current.get(key)!={doc}:raise ValueError('Reviewed City crosswalk compound facts changed or ambiguous: '+ref)
        elif proof['proof_class']!='publisher_native_call':raise ValueError('Unknown City crosswalk proof class')
        ph=digest(proof)
        prior=conn.execute('SELECT proof_json FROM city_crosswalk_evidence WHERE proof_sha256=?',(ph,)).fetchone()
        path=directory/(ref.rsplit('.',1)[0]+'.html')
        if path.exists():
            source=path.read_bytes()
            if hashlib.sha256(source).hexdigest()!=proof['source_sha256']:raise ValueError('Reviewed City crosswalk source changed: '+ref)
            original=by_record[proof['publisher_record_id']]
            actual=propose(reference=ref,title=item['title'],source=source,publisher=original,index=index,
                           source_sha256=proof['source_sha256'],snapshot_sha256=bundle['snapshot_sha256'])
            if actual!=proof:raise ValueError('Reviewed City crosswalk proof mismatch: '+ref)
        elif not prior:raise ValueError('New City crosswalk needs held original agenda: '+ref)
        if prior and prior['proof_json']!=canonical(proof):raise ValueError('Stored City crosswalk evidence changed: '+ref)
        linked=conn.execute('SELECT document_number FROM solicitation_link WHERE reference=?',(ref,)).fetchone()
        if linked and linked[0]!=doc:raise ValueError('Reviewed City crosswalk conflicts with stored link: '+ref)
        if conn.execute('SELECT 1 FROM bid WHERE reference=? AND document_number IS NOT NULL AND document_number!=?',(ref,doc)).fetchone():
            raise ValueError('Reviewed City crosswalk conflicts with explicit bid identity: '+ref)
        approved.append((proof,ph))
    for proof,ph in approved:
        conn.execute('INSERT OR IGNORE INTO city_crosswalk_evidence (proof_sha256,reference,document_number,call_number,proof_class,proof_json) VALUES (?,?,?,?,?,?)',
                     (ph,proof['reference'],proof['document_number'],proof['call_number'],proof['proof_class'],canonical(proof)))
        conn.execute("INSERT INTO solicitation_link (reference,document_number,method) VALUES (?,?,'city_call_crosswalk') ON CONFLICT(reference) DO UPDATE SET method=excluded.method",
                     (proof['reference'],proof['document_number']))
    return {p['reference']:dict(rule='reviewed_city_call_crosswalk',proof_sha256=h,proof_class=p['proof_class'],call_number=p['call_number'],reference=p['reference'],document_number=p['document_number']) for p,h in approved}
