"""Original City award publications bridge native calls to migrated publisher keys.

An archive capture date is not an award date; a published award date is not a
council decision date. Only exact supplier/amount/published-award-date relations
in both complete publisher populations, with an unambiguous original call, can
support a reviewed link. This module never infers bids or number conversions.
"""
from collections import defaultdict
from datetime import date,datetime
import gzip
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
import zipfile

from lxml import html
from toronto_bids.linking import city_crosswalk as cw
from toronto_bids.sources.odata import award_identity_record,identity_record

_TAX=re.compile(r'All award dollar values listed below do not include Harmonized Sales Tax\s*\(HST\)\.',re.I)


def award_date(award):
    values={str(award.get(k)) for k in ('Date_Awarded','AwardedDate') if award.get(k)}
    if len(values)!=1:return None
    value=next(iter(values))
    try:return date.fromisoformat(value).isoformat()
    except ValueError:return None


def award_index(records):
    index=defaultdict(set)
    for record in records:
        for award in record.get('Awarded_Suppliers') or []:
            day=award_date(award);supplier=cw.supplier_identity(award.get('Successful_Bidder'));value=cw.amount(award.get('Award_Amount'))
            if day and supplier and value is not None:
                index[(day,supplier,value)].add((record.get('id'),record.get('Solicitation_Document_Number')))
    return index


class ArchiveEvidence:
    def __init__(self,population,sources,*,snapshot_sha256=None):
        self.population=[award_identity_record(r) for r in population]
        self.snapshot_sha256=snapshot_sha256 or cw.digest(self.population)
        self.publisher_index=award_index(self.population)
        documents=defaultdict(list)
        for record in self.population:documents[record['Solicitation_Document_Number']].append(record)
        # Keep every record in the comparison index. Repeated/junk document keys
        # elsewhere in the feed cannot approve a target or erase a competitor.
        self.by_document={doc:records[0] for doc,records in documents.items()
                          if len(records)==1 and re.fullmatch(r'\d{10}',doc or '')}
        self.rows={};self.call_documents=defaultdict(set);self.fact_calls=defaultdict(set)
        self.rejected_rows=[]
        for metadata,body in sources:
            if hashlib.sha256(body).hexdigest()!=metadata['sha256']:raise ValueError('archived publisher bytes changed')
            original=urlsplit(metadata['source_url'])
            stamp=metadata['archive_timestamp']
            if (original.hostname!='wx.toronto.ca' or not original.path.lower().startswith('/inter/pmmd/callawards.nsf/')
                    or not re.fullmatch(r'\d{14}',stamp) or metadata.get('status')!=200
                    or metadata['url']!='https://web.archive.org/web/'+stamp+'id_/'+metadata['source_url']
                    or metadata.get('final_url',metadata['url'])!=metadata['url']):
                raise ValueError('archived publisher provenance is not exact')
            capture=datetime.strptime(stamp,'%Y%m%d%H%M%S').date()
            root=html.fromstring(body.decode('utf-8'))
            tax=_TAX.search(cw.clean(root.text_content()))
            if not tax:raise ValueError('archived award tax role absent')
            for ordinal,tr in enumerate(root.xpath('//tr')):
                cells=[cw.clean(td.text_content()) for td in tr.xpath('./td')]
                if len(cells)!=12:continue
                calls=cw.find_call_numbers(cells[4]);value=cw.amount(cells[9])
                if len(calls)!=1 or value is None:continue
                if re.search(r'\b(?:cancelled|canceled|withdrawn)\b',' '.join(cells[i] for i in (4,8,9)),re.I):continue
                try:day=datetime.strptime(cells[10],'%m/%d/%Y').date()
                except ValueError:continue
                if day>capture:
                    self.rejected_rows.append(dict(source_sha256=metadata['sha256'],row_ordinal=ordinal,reason='award date follows archive capture'))
                    continue
                links=tr.xpath('./td[5]//a/@href')
                if len(links)!=1 or not re.search(r'/[a-fA-F0-9]{32}\?OpenDocument$',links[0],re.I):continue
                supplier=cw.supplier_identity(cells[8])
                if not supplier:continue
                call=next(iter(calls));key=(day.isoformat(),supplier,value)
                row=dict(call_number=call,call_quotation=cells[4],supplier=cells[8],amount=cells[9],
                         published_award_date=day.isoformat(),award_date_quotation=cells[10],
                         division=cells[5],description=cells[6],record_link=links[0],tax_quotation=tax.group(),
                         source=metadata,row_ordinal=ordinal,row_sha256=hashlib.sha256(html.tostring(tr)).hexdigest())
                self.rows[(metadata['sha256'],ordinal)]=row
                self.fact_calls[key].add(call)
                self.call_documents[call].update(doc for _,doc in self.publisher_index.get(key,set()))

    def assertion(self,source_sha256,row_ordinal,publisher):
        row=self.rows.get((source_sha256,row_ordinal))
        if row is None:raise ValueError('valid archived award row absent')
        projected=award_identity_record(publisher);doc=projected['Solicitation_Document_Number']
        key=(row['published_award_date'],cw.supplier_identity(row['supplier']),cw.amount(row['amount']))
        target={(projected['id'],doc)}
        if self.publisher_index.get(key)!=target:raise ValueError('archived award does not identify one global publisher record')
        if self.fact_calls[key]!={row['call_number']} or self.call_documents[row['call_number']]!={doc}:
            raise ValueError('archived call / award facts are ambiguous')
        if self.by_document.get(doc)!=projected:raise ValueError('archive publisher snapshot disagrees')
        for match in re.finditer(r'\bCall number\s*:\s*([^\n]+)',projected.get('Solicitation_Award_Details') or '',re.I):
            if cw._title_calls(match[1])!={row['call_number']}:raise ValueError('current native call assertion contradicts archive')
        return row,projected,key

    def propose(self,*,reference,title,agenda,publisher,source_sha256,row_ordinal):
        row,projected,key=self.assertion(source_sha256,row_ordinal,publisher)
        if not cw._REF.fullmatch(reference):raise ValueError('invalid council reference')
        chunks=cw.sections(agenda,reference.rsplit('.',1)[0]).get(reference,[])
        if len(chunks)!=1:raise ValueError('agenda context absent or repeated')
        text=chunks[0];call=row['call_number']
        if not text.startswith(cw.clean(title)) or cw._title_calls(title)!={call}:
            raise ValueError('original title does not name the same unique call')
        fields=list(re.finditer(r'\bCall\s+(?:Number|No\.?)\s*:\s*(.+?)(?=\s+(?:Description|Recommended (?:Bidder|Proponent)|Contract Award Value)\s*:|$)',text,re.I))
        if not fields or any(cw._title_calls(f[1])!={call} for f in fields):raise ValueError('native call field absent or ambiguous')
        award=next(a for a in projected['Awarded_Suppliers'] if award_date(a)==key[0]
                   and cw.supplier_identity(a.get('Successful_Bidder'))==key[1] and cw.amount(a.get('Award_Amount'))==key[2])
        label=re.search(r'\bContract Award Value\s*:',text,re.I)
        recommendation=text[label.end():] if label else ''
        end=re.search(r'\b(?:Summary|Financial Impact|Background Information)\b',recommendation,re.I)
        if end:recommendation=recommendation[:end.start()]
        return dict(reference=reference,document_number=projected['Solicitation_Document_Number'],call_number=call,
            proof_class='archived_publisher_award',source_title=title,
            source_url='https://secure.toronto.ca/council/report.do?meeting='+reference.rsplit('.',1)[0]+'&type=agenda',
            source_sha256=hashlib.sha256(agenda).hexdigest(),item_sha256=hashlib.sha256(text.encode()).hexdigest(),
            publisher_record_id=projected['id'],publisher_record_sha256=cw.digest(projected),
            snapshot_sha256=self.snapshot_sha256,issued_date=projected.get('Issue_Date'),closed_date=projected.get('Closing_Date'),
            solicitation_type=projected.get('Solicitation_Document_Type'),source_solicitation_type=cw._source_type(title),
            supplier_name=award['Successful_Bidder'],award_amount=award['Award_Amount'],published_award_date=key[0],
            archived_award=dict(source=row['source'],row_ordinal=row_ordinal,row_sha256=row['row_sha256'],record_link=row['record_link']),
            agenda_recommendation_quotation=cw.clean(recommendation),
            agenda_date_quotations=[m.group() for m in re.finditer(
                r'\b(?:Call\s+)?(?:Issued|Closed)\b(?:\s*:\s*|\s+)(?:on\s+)?[A-Z][a-z]+\.?\s+\d{1,2}(?:st|nd|rd|th)?\s*,?\s*\d{4}\b',text,re.I)],
            witnesses=dict(call=fields[0].group(),archived_call=row['call_quotation'],archived_supplier=row['supplier'],
                           archived_net_award=row['amount'],archived_award_date=row['award_date_quotation'],tax=row['tax_quotation']))


def load_evidence(bundle,registry):
    spec=bundle.get('archived_awards')
    if not spec:raise ValueError('archived award evidence configuration absent')
    directory=Path(registry).parent
    raw=(directory/spec['population_file']).read_bytes()
    population=json.loads(gzip.decompress(raw))
    if cw.digest(population)!=spec['population_sha256'] or len(population)!=spec['population_count']:
        raise ValueError('archived award publisher population changed')
    source_zip=(directory/spec['sources_file']).read_bytes()
    if hashlib.sha256(source_zip).hexdigest()!=spec['sources_sha256']:raise ValueError('archived award source bundle changed')
    import io
    with zipfile.ZipFile(io.BytesIO(source_zip)) as archive:
        sources=[(metadata,archive.read(metadata['file'])) for metadata in spec['sources']]
    return ArchiveEvidence(population,sources,snapshot_sha256=spec['snapshot_sha256'])


def current_index(conn):
    # Stale independent award rows can refuse an ambiguous join, never approve it.
    # Approval below always requires the target's latest full date projection.
    index=defaultdict(set)
    for row in conn.execute("SELECT s.odata_id,s.document_number,a.supplier_name_raw,a.award_amount,a.award_date FROM solicitation s JOIN award a ON a.document_number=s.document_number WHERE a.source='odata'"):
        try:day=date.fromisoformat(row['award_date']).isoformat()
        except (ValueError,TypeError):continue
        value=cw.amount(row['award_amount']);supplier=cw.supplier_identity(row['supplier_name_raw'])
        if value is not None and supplier:index[(day,supplier,value)].add((row['odata_id'],row['document_number']))
    for row in conn.execute('SELECT publisher_award_identity_json FROM solicitation WHERE publisher_award_identity_json IS NOT NULL'):
        for key,targets in award_index([json.loads(row[0])]).items():index[key].update(targets)
    return index


def validate(proof,conn,evidence,*,agendas,index,call_refs,prior):
    doc=proof['document_number'];ref=proof['reference'];publisher=evidence.by_document.get(doc)
    if publisher is None:raise ValueError('archived award publisher record absent')
    arc=proof['archived_award'];row,projected,key=evidence.assertion(arc['source']['sha256'],arc['row_ordinal'],publisher)
    if (proof['call_number']!=row['call_number'] or proof['publisher_record_sha256']!=cw.digest(projected)
            or proof['snapshot_sha256']!=evidence.snapshot_sha256 or arc['row_sha256']!=row['row_sha256']):
        raise ValueError('archived award proof changed')
    sol=conn.execute('SELECT * FROM solicitation WHERE document_number=?',(doc,)).fetchone()
    item=conn.execute('SELECT title FROM council_item WHERE reference=?',(ref,)).fetchone()
    if (not sol or not item or item['title']!=proof['source_title'] or sol['odata_id']!=projected['id']
            or sol['issue_date']!=projected.get('Issue_Date') or sol['submission_deadline']!=projected.get('Closing_Date')
            or sol['rfx_type']!=projected.get('Solicitation_Document_Type')):
        raise ValueError('archived award source context changed: '+ref)
    latest=sol['publisher_award_identity_json']
    if not latest or json.loads(latest)!=projected:raise ValueError('current publisher award dates / pairs changed or missing: '+ref)
    base=sol['publisher_identity_json']
    if not base or json.loads(base)!=identity_record(projected):
        raise ValueError('current publisher projections disagree: '+ref)
    if index.get(key)!={(projected['id'],doc)}:raise ValueError('current published award facts changed or ambiguous: '+ref)
    if call_refs[proof['call_number']]!={ref}:raise ValueError('archived award call has multiple council contexts: '+ref)
    path=Path(agendas)/(ref.rsplit('.',1)[0]+'.html')
    if path.exists():
        actual=evidence.propose(reference=ref,title=item['title'],agenda=path.read_bytes(),publisher=publisher,
                                source_sha256=arc['source']['sha256'],row_ordinal=arc['row_ordinal'])
        if actual!=proof:raise ValueError('archived award source / proof mismatch: '+ref)
    elif not prior:raise ValueError('new archived award proof needs held agenda: '+ref)
    if prior and prior['proof_json']!=cw.canonical(proof):raise ValueError('stored archived award proof changed: '+ref)
    link=conn.execute('SELECT document_number FROM solicitation_link WHERE reference=?',(ref,)).fetchone()
    if link and link[0]!=doc:raise ValueError('archived award conflicts with stored link: '+ref)
    if conn.execute('SELECT 1 FROM bid WHERE reference=? AND document_number IS NOT NULL AND document_number!=?',(ref,doc)).fetchone():
        raise ValueError('archived award conflicts with explicit bid identity: '+ref)
