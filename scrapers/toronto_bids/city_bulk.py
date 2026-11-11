"""Bulk report screening and extraction against the durable City agenda inventory.

Discovery and model output never establish identities. This module queues the
shared extraction contract and emits source-bound participation proposals. Its
exception ledger is intended to be processed in cohorts, not report by report.
"""
from collections import Counter
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import subprocess

from toronto_bids.extract import EXTRACTOR_VERSION
from toronto_bids.extraction import split_document
from toronto_bids.batch_extraction import ExtractionPending
from toronto_bids.sources.city_agendas import connect, clean
from toronto_bids.linking.call_number import find_call_numbers

_PARTICIPATION=re.compile(r'\b(?:bids?|proposals?|submissions?|tenders?)\b.{0,100}\b(?:received|submitted)\b|\b(?:received|submitted)\b.{0,100}\b(?:bids?|proposals?|submissions?|tenders?)\b',re.I)
_NATIVE_PARTICIPATION=re.compile(r'\breceipt\b.{0,100}\b(?:bids?|proposals?|submissions?)\b|\bresponses?\b.{0,80}\breceived\b',re.I)
_DOC=re.compile(r'\b(?:Doc(?:ument)?\.?(?:\s+(?:Number|No\.?))?|RFP|RFQ|RFT)\s*[:#]?\s*(\d{10})(?!\d)',re.I)
_LIST=re.compile(r'\bfollowing\b(?=[^:]{0,160}\b(?:firms?|bidders?|proponents?|suppliers?|submissions?)\b)[^:]{0,160}:\s*\n',re.I)
_SOURCE_COUNT=r'(?:[A-Za-z]+(?:-[A-Za-z]+)?\s*\(\d+\)|\d+)'
_RECEIVED_LIST_COUNT=re.compile(r'\b(?P<count>'+_SOURCE_COUNT+r')\s+(?:bids?|proposals?|submissions?|responses?)\b.{0,100}\breceived\b|\breceipt\s+of\s+(?:a\s+total\s+of\s+)?(?P<receipt>'+_SOURCE_COUNT+r')\s+(?:bids?|proposals?|submissions?)\b',re.I)
_TABLE=re.compile(r'\bTable\s+\d+\s*:\s*Summary\s+(?:of\s+)?(?:(?P<count>'+_SOURCE_COUNT+r')\s+)?(?:Bids|Proposals|Submissions)\s+Received\b[^\n]*(?:\n|$)',re.I)
_RESPONDED_TABLE=re.compile(r'\bTable\s+(?P<table>\d+)\s*:\s*Suppliers\s+and\s+Fixed\s+Bid\s+Prices\b[^\n]*(?:\n|$)',re.I)
_RESPONDED_LEAD=re.compile(r'\b(?:A\s+total\s+of\s+)?(?P<count>'+_SOURCE_COUNT+r')\s+bidders?\s+responded\s+as\s+listed\s+in\s+Table\s+(?P<table>\d+)\s+below\s*:\s*$',re.I)


def _primary_heading(text):
    return re.split(r'\b(?:SUMMARY|RECOMMENDATIONS|PURPOSE)\b',text,flags=re.I)[0][:1600]


def _identity_role(doc,text):
    published=set(_DOC.findall(text))
    if published=={doc}:return 'unique_published_document'
    # A staff report can discuss an older event. That is different from a
    # conflicting current-event header/footer. Mixed reports need both a unique
    # primary subject and an explicitly labelled local submitted-firm context.
    if set(_DOC.findall(_primary_heading(text)))!={doc}:raise ValueError('conflicting_source_document_roles')
    for page in text.split('\f'):
        tail='\n'.join([line for line in page.splitlines() if line.strip()][-4:])
        if re.search(r'\bPage\s+\d+\s+of\s+\d+\b',tail,re.I) and set(_DOC.findall(tail))-{doc}:
            raise ValueError('conflicting_source_document_roles')
    return 'primary_subject_and_local_document'


def propose(archive, row, native_identities=None):
    """Conservative programmatic participation admission; no handwritten report rules.

    Supported projections bind one independently proven City event to a literal
    received-firm list or named received-bid table. Stage conflicts and complex tables
    remain explicit exceptions. No price, compliance or award is inferred.
    """
    proposals=[];exceptions=[]
    if native_identities is None:
        from toronto_bids.linking.city_bulk_identity import NativeCallIdentities
        native_identities=NativeCallIdentities(archive)
    if not row['result']:return proposals,[dict(reason=row.get('screen','extraction_pending'))]
    text=row['text'] or '';flat=clean(text)
    for index,contract in enumerate(row['result'].get('contracts',[])):
        try:
            reference=clean(contract.get('reference'))
            docs=set(_DOC.findall(reference))
            if not docs and re.fullmatch(r'\d{10}',reference):docs={reference}
            native_identity=None;call=None
            if not docs and len(find_call_numbers(reference))==1:
                call=next(iter(find_call_numbers(reference)))
                if _DOC.findall(text) or find_call_numbers(_primary_heading(text))!={call}:
                    raise ValueError('native_call_not_unique_primary_report_subject')
                items=[i for i in row['items'] if find_call_numbers(i['title'])=={call} and not _DOC.findall(i['title'])]
                if not items:raise ValueError('native_call_not_unique_agenda_subject')
                for page in text.split('\f'):
                    tail='\n'.join([line for line in page.splitlines() if line.strip()][-2:])
                    if re.search(r'\bPage\s+\d+\s+of\s+\d+\b',tail,re.I) and find_call_numbers(tail)-{call}:
                        raise ValueError('conflicting_native_source_footer')
                doc,native_identity=native_identities.resolve(call)
                identity_role='archived_award_and_primary_native_call'
                identity_anchor='agenda_title_and_report_subject'
            else:
                if len(docs)!=1:raise ValueError('historical_or_unresolved_contract_identity')
                doc=next(iter(docs))
                # Conflicting footer/header IDs cannot be repaired by frequency.
                identity_role=_identity_role(doc,text)
                items=[i for i in row['items'] if set(_DOC.findall(i['title']))=={doc}]
                identity_anchor='agenda_title'
                if not items:
                    items=[i for i in row['items'] if not _DOC.findall(i['title'])]
                    if not items or set(_DOC.findall(_primary_heading(text)))!={doc}:
                        raise ValueError('primary_document_absent_from_agenda_title_or_report_subject')
                    identity_anchor='report_subject'
                if not archive.execute('SELECT 1 FROM solicitation WHERE document_number=?',(doc,)).fetchone():
                    raise ValueError('document_absent_from_publisher_spine')
            tender=None
            if native_identity is not None:
                from toronto_bids.linking.city_bulk_receipts import tender_witness
                tender=tender_witness(text,call,contract)
            if not tender and (contract.get('stage')!='single_stage' or contract.get('solicitation_type')=='RFSQ'
                    or contract.get('prequalified') or contract.get('invited_to_tender')):
                raise ValueError('participation_stage_requires_review')
            raw=contract.get('bids',[])
            names=tender[0] if tender else list(dict.fromkeys(clean(b.get('supplier_name')) for b in raw))
            if not names:raise ValueError('no_named_submitted_bids')
            if any(not name for name in names):raise ValueError('unnamed_supplier_observation')
            declared=contract.get('declared_submissions')
            if type(declared) is int and len(names)>declared:
                raise ValueError('named_participation_exceeds_declared_submissions')
            if not tender and any(b.get('status')=='no_bid' for b in raw):raise ValueError('no_bid_outcome_requires_review')
            witness=tender[1] if tender else None
            markers=sorted([(m,'submitted_firm_list') for m in _LIST.finditer(text)]+
                           [(m,'submitted_bid_table') for m in _TABLE.finditer(text)]+
                           [(m,'submitted_bid_table') for m in _RESPONDED_TABLE.finditer(text)],key=lambda pair:pair[0].start())
            for marker,kind in ([] if tender else markers):
                context=clean(text[max(0,marker.start()-1200):marker.end()])
                lead=clean(text[max(0,marker.start()-250):marker.end()])
                count_quotation=marker.groupdict().get('count')
                if marker.re is _RESPONDED_TABLE:
                    response=_RESPONDED_LEAD.search(clean(text[max(0,marker.start()-350):marker.start()]))
                    if not response or response['table']!=marker['table'] or set(_DOC.findall(marker[0]))!={doc}:continue
                    count_quotation=response['count']
                elif not (_PARTICIPATION.search(lead) or (call and _NATIVE_PARTICIPATION.search(lead))):continue
                counts=[count_quotation] if count_quotation else []
                if call and marker.re is _LIST:
                    counts.extend(m['count'] or m['receipt'] for m in _RECEIVED_LIST_COUNT.finditer(lead))
                if any(len(names)>int(re.search(r'\d+',q)[0]) for q in counts):continue
                if re.search(r'pre[- ]?qualif|invited to',marker[0] if call else context,re.I):continue
                # The complete report and primary agenda title already agree
                # on one Doc. A list need not repeat it on the same page, but
                # a nearby historical native call is a competing event role.
                if find_call_numbers(context)-({call} if call else set()):continue
                if identity_role=='primary_subject_and_local_document' and set(_DOC.findall(context))!={doc}:continue
                section=text[marker.end():marker.end()+1800]
                # Keep the literal list separate from subsequent evaluation,
                # consultant descriptions and recommended award discussions.
                boundary=re.search(r'\n\s*(?:As required|A selection|The selection|The evaluation|All\s+\w+\s+\([^)]*\)\s+technical|Contract Award|Financial Impact|CONTACT|RECOMMENDATIONS)\b',section,re.I)
                if boundary:section=section[:boundary.start()]
                if re.search(r'^\s*no[- ]+bid\s*$',section,re.I|re.M):continue
                quote=clean(section)
                def literal_list_row(name):
                    pattern=re.escape(name).replace(r'\ ',r'\s+')
                    suffix=r'(?:\s+\([A-Z][A-Za-z0-9]{1,30}\))?' if call else ''
                    return re.search(r'(?:^|\n)[ \t]*(?:[•●▪][ \t]*|\d+\.[ \t]+)?'+pattern+suffix+r'[ \t]*[*†‡]*(?=\r?\n|$)',section)
                if all(name in quote and literal_list_row(name) for name in names):
                    witness=dict(kind=kind,context=context,list_quotation=quote);break
            if witness is None:raise ValueError('literal_submitted_firm_list_not_established')
            if any(name not in flat for name in names):raise ValueError('supplier_not_literal')
            proposal=dict(admission_version='city-list-v1',document_number=doc,contract_index=index,names=names,
                identity_role=identity_role,
                identity_anchor=identity_anchor,
                primary_subject_quotation=clean(_primary_heading(text)) if identity_anchor=='report_subject' or identity_role=='primary_subject_and_local_document' else None,
                raw_bid_observations=len(raw),
                declared_submissions=declared,
                report_url=row['url'],report_sha256=row['sha256'],extractor_version=EXTRACTOR_VERSION,
                result_sha256=hashlib.sha256(json.dumps(row['result'],sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                items=[dict(reference=i['reference'],title=i['title'],meeting=i['meeting'],
                            agenda_url='https://secure.toronto.ca/council/report.do?meeting='+i['meeting']+'&type=agenda',
                            agenda_sha256=i['source_sha256']) for i in items],
                witness=witness,prices_withheld=True,statuses_withheld=True)
            if native_identity is not None:
                proposal['admission_version']='city-native-list-v1'
                proposal['native_call_identity']=native_identity
                proposal['primary_subject_quotation']=clean(_primary_heading(text))
            proposals.append(proposal)
        except ValueError as exc:exceptions.append(dict(contract_index=index,reference=contract.get('reference'),reason=str(exc)))
    return proposals,exceptions


def inventory(directory, archive):
    """Screen native text and reuse the current whole-document extraction cache."""
    conn=connect(directory)
    try:
        rows=[]
        for row in conn.execute("SELECT * FROM report ORDER BY url"):
            row=dict(row);text=row['text'] or ''
            cache=archive.execute('SELECT result_json FROM extraction_cache WHERE sha256=? AND extractor_version=?',(row['sha256'],EXTRACTOR_VERSION)).fetchone()
            items=[dict(r) for r in conn.execute('SELECT DISTINCT i.reference,i.title,i.text,i.meeting,i.source_sha256 '
                'FROM item i JOIN attachment a ON a.meeting=i.meeting AND a.source_sha256=i.source_sha256 AND a.ordinal=i.ordinal '
                'JOIN meeting m ON m.reference=i.meeting AND m.source_sha256=i.source_sha256 '
                'WHERE a.url=? AND i.candidate=1 ORDER BY i.reference,i.meeting,i.source_sha256',(row['url'],))]
            state=('unavailable' if row['state']!='held' else 'no_native_text' if not text.strip() else
                   'cached' if cache else 'eligible' if _PARTICIPATION.search(clean(text)) else 'no_participation_language')
            rows.append(dict(row,screen=state,items=items,result=json.loads(cache[0]) if cache else None))
        return rows
    finally:conn.close()


def queue(directory, archive, *, client, limit=50, include_unlocated=False, log=lambda m:None):
    """Queue eligible unique PDF hashes through the installation's shared batch client.

    Does not submit, collect, write facts, or create a second spending authority.
    Callers collect into the authoritative operational archive before copying
    immutable cache entries into a validation archive.
    """
    missing={r[0] for r in archive.execute("SELECT s.document_number FROM solicitation s WHERE "
        "EXISTS(SELECT 1 FROM award a WHERE a.document_number=s.document_number AND a.source='odata') AND "
        "NOT EXISTS(SELECT 1 FROM bid b WHERE b.document_number=s.document_number OR b.procurement_id=s.procurement_id)")}
    rows=inventory(directory,archive)
    def priority(row):
        printed=set(_DOC.findall(row['text'] or ''))
        primary=set().union(*(set(_DOC.findall(i['title'])) for i in row['items']))
        row['covered_primary']=bool(primary) and not bool(primary & missing)
        row['discovery_targets']=(printed|primary)&missing
        return (not bool(printed & missing),row['covered_primary'],row['url'])
    rows.sort(key=priority)
    counts=Counter();seen=set();queued=0
    for row in rows:
        if row['screen']=='unavailable':
            counts['unavailable_urls']+=1;continue
        if row['sha256'] in seen:
            counts['duplicate_content_urls']+=1;continue
        seen.add(row['sha256']);counts[row['screen']]+=1
        if row['screen']!='eligible' or queued>=limit:continue
        if row['covered_primary']:
            counts['outside_missing_population']+=1;continue
        if not include_unlocated and not row['discovery_targets']:
            counts['identity_discovery_deferred']+=1;continue
        path=Path(row['local_path'])
        if hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:
            counts['changed_source']+=1;continue
        queued+=1
        try:
            # A ready queue result is still collected operationally, never cached
            # into the private archive as a side effect of this preparation step.
            client.extract_document(row['sha256'],split_document(row['text']),source='city_agenda_bulk')
            counts['ready_in_queue']+=1
        except ExtractionPending:counts['pending']+=1
        except (ValueError,TypeError) as exc:
            counts['failed']+=1;log(row['url']+': '+str(exc))
    return dict(counts)


def write_inventory(directory, archive, output):
    """One resumable-workload scoreboard, including unresolved and screened reports."""
    rows=inventory(directory,archive)
    proposals=[];exceptions=[]
    from toronto_bids.linking.city_bulk_identity import NativeCallIdentities
    native_identities=NativeCallIdentities(archive)
    for row in rows:
        accepted,held=propose(archive,row,native_identities)
        proposals.extend(accepted);exceptions.extend(dict(report_url=row['url'],**hold) for hold in held)
    result=dict(extractor_version=EXTRACTOR_VERSION,states=dict(Counter(r['screen'] for r in rows)),
        participation_proposals=proposals,exceptions=exceptions,reports=[
        dict(url=r['url'],sha256=r['sha256'],state=r['screen'],items=[dict(reference=i['reference'],title=i['title']) for i in r['items']],
             contracts=r['result'].get('contracts',[]) if r['result'] else None,error=r['error']) for r in rows])
    Path(output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return result['states']


def backfill(directory, archive):
    """Admit supported participation atomically; preserve other sources and row IDs.

    Run on a validation archive first. The caller initializes the schema, owns
    commit, and resolves canonical identities before measuring/exporting coverage.
    Existing evidence must remain reproducible; source/cache changes require an
    explicit correction rather than silently retaining or deleting accepted facts.
    """
    from toronto_bids.models import Bid, BackgroundPdf
    from toronto_bids.store import db
    from toronto_bids.linking.city_crosswalk import canonical,digest
    rows=inventory(directory,archive);accepted=[];exceptions=[]
    from toronto_bids.linking.city_bulk_identity import NativeCallIdentities
    native_identities=NativeCallIdentities(archive)
    for row in rows:
        proposals,held=propose(archive,row,native_identities)
        exceptions.extend(dict(report_url=row['url'],**h) for h in held)
        for proof in proposals:
            path=Path(row['local_path'])
            if hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:
                raise ValueError('Accepted report bytes missing or changed')
            native=subprocess.run(['pdftotext','-q',str(path),'-'],capture_output=True,text=True)
            if native.returncode or native.stdout.strip()!=(row['text'] or ''):
                raise ValueError('Accepted native text disagrees with PDF bytes')
            for item in row['items']:
                agenda=Path(directory)/'agendas'/(item['meeting']+'.html')
                if hashlib.sha256(agenda.read_bytes()).hexdigest()!=item['source_sha256']:
                    raise ValueError('Accepted agenda bytes missing or changed')
                from toronto_bids.sources.city_agendas import parse_agenda
                ledger=connect(directory)
                try:meeting=dict(ledger.execute('SELECT * FROM meeting WHERE reference=?',(item['meeting'],)).fetchone())
                finally:ledger.close()
                meeting['dates']=json.loads(meeting['dates_json'])
                actual=parse_agenda(agenda.read_bytes(),meeting)['items']
                if not any(i['reference']==item['reference'] and i['title']==item['title'] and i['text']==item['text']
                           and any(a['url']==row['url'] for a in i['attachments']) for i in actual):
                    raise ValueError('Accepted item/link context disagrees with agenda bytes')
            accepted.append((proof,row))
    expected={digest(proof) for proof,_ in accepted}
    managed_urls={row['url'] for row in rows}
    prior_rows=list(archive.execute('SELECT * FROM city_bulk_bid_evidence'))
    def retained_context(old,current):
        # Later Council consideration can add another capture of the same item.
        # Provenance grows; existing identical participation remains supported.
        old_core={k:v for k,v in old.items() if k!='items'}
        current_core={k:v for k,v in current.items() if k!='items'}
        return (old_core==current_core and
                {canonical(i) for i in old.get('items',[])}<={canonical(i) for i in current.get('items',[])})
    pinned=defaultdict(set)
    for old in prior_rows:
        # Revalidate this inventory's previously admitted reports. Immutable
        # evidence from other historical jobs survives a new term's workload.
        value=json.loads(old['proof_json'])
        if digest(value)!=old['proof_sha256'] or (old['report_url'] in managed_urls and old['proof_sha256'] not in expected and
                not any(retained_context(value,current) for current,_ in accepted)):
            raise ValueError('Previously admitted bulk evidence is absent or changed; explicit repair required')
        if any(old[k]!=value[k] for k in ('document_number','report_url','report_sha256')):
            raise ValueError('Stored bulk evidence columns disagree with proof')
        pinned[old['document_number']].add(old['report_url'])
    # Several supplements can name the same participants differently. Do not
    # invent supplier aliases or inflate participation by unioning those lists.
    # Choose one coherent list per event and preserve that choice on resume.
    groups=defaultdict(list)
    for proof,row in accepted:groups[proof['document_number']].append((proof,row))
    selected=[]
    for doc,options in sorted(groups.items()):
        if len(pinned[doc])>1:raise ValueError('Previously admitted event has multiple source lists; explicit reconciliation required')
        eligible=[p for p in options if not pinned[doc] or p[0]['report_url'] in pinned[doc]]
        if not eligible:
            exceptions.extend(dict(report_url=p['report_url'],document_number=doc,
                reason='existing_coherent_list_outside_this_inventory') for p,_ in options)
            continue
        chosen=min(eligible,key=lambda pair:(-len(pair[0]['names']),pair[0]['report_url'],pair[0]['contract_index']))
        selected.append(chosen)
        for alternative in options:
            if alternative is not chosen:exceptions.append(dict(report_url=alternative[0]['report_url'],
                document_number=doc,reason='alternative_named_list_retained_without_inventing_supplier_aliases'))
    accepted=selected
    if not archive.in_transaction:archive.execute('BEGIN')
    archive.execute('SAVEPOINT city_bulk_backfill')
    try:
        for proof,row in accepted:
            old=archive.execute('SELECT sha256 FROM background_pdf WHERE url=?',(row['url'],)).fetchone()
            if old and old[0] not in (None,row['sha256']):raise ValueError('Archived report bytes changed')
            db.upsert_row(archive,BackgroundPdf(url=row['url'],document_number=proof['document_number'],
                kind='city_bulk',local_path=row['local_path'],sha256=row['sha256'],text=row['text']),overwrite=False)
            for name in proof['names']:
                db.upsert_row(archive,Bid(name,document_number=proof['document_number'],source='city_bulk'),overwrite=False)
            archive.execute('INSERT OR IGNORE INTO city_bulk_bid_evidence '
                '(proof_sha256,document_number,report_url,report_sha256,proof_json) VALUES (?,?,?,?,?)',
                (digest(proof),proof['document_number'],proof['report_url'],proof['report_sha256'],canonical(proof)))
        archive.execute('RELEASE city_bulk_backfill')
    except BaseException:
        archive.execute('ROLLBACK TO city_bulk_backfill');archive.execute('RELEASE city_bulk_backfill');raise
    return dict(accepted_reports=len({p['report_url'] for p,_ in accepted}),
                documents=len({p['document_number'] for p,_ in accepted}),
                participation_rows=sum(len(p['names']) for p,_ in accepted),exceptions=exceptions)


def run(directory, archive, *, http, browser, client=None, meeting_limit=None,
        report_limit=None, extraction_limit=50, include_unlocated=False, submit=False, log=lambda m:None):
    """One resumable all-term discovery/acquisition/extraction workload.

    Source deferrals do not discard already held work. Paid submission is
    explicit, bounded and guarded by the caller's installation-wide budget.
    Collection and validated backfill are separate operational transitions.
    """
    from toronto_bids.sources.city_agendas import seed_schedules,capture,capture_reports,InventoryBusy,CaptureDeferred,status
    directory=Path(directory)
    seed_schedules(directory,http=http)
    result={}
    for phase,collect in (
            ('agendas',lambda:capture(directory,browser=browser,limit=meeting_limit,log=log)),
            ('reports',lambda:capture_reports(directory,http=http,limit=report_limit,log=log))):
        try:result[phase]=collect()
        except (InventoryBusy,CaptureDeferred) as exc:
            conn=connect(directory)
            try:result[phase]=dict(status(conn),lane_state='busy' if isinstance(exc,InventoryBusy) else 'deferred',reason=str(exc))
            finally:conn.close()
            log(phase+': '+str(exc))
    if client is not None:
        result['extraction']=queue(directory,archive,client=client,limit=extraction_limit,include_unlocated=include_unlocated,log=log)
        if submit:result['submission']=client.queue.submit(limit=extraction_limit)
    result['screening']=write_inventory(directory,archive,directory/'extraction-inventory.json')
    (directory/'run.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def main():
    import argparse
    import sqlite3
    from toronto_bids.batch_extraction import BatchExtractionClient
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('step',choices=['run','inventory','queue','backfill'])
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--database',type=Path,required=True,help='Archive: read-only for discovery/queue, writable for backfill')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--limit',type=int,default=50)
    parser.add_argument('--budget-database',type=Path,help='Existing installation-wide Claude queue/budget (required for queue)')
    parser.add_argument('--queue',action='store_true',help='Prepare shared Sonnet batch requests during run')
    parser.add_argument('--submit',action='store_true',help='Submit a bounded batch during run (implies --queue)')
    parser.add_argument('--meeting-limit',type=int,help='Bound one agenda capture pass, across all terms')
    parser.add_argument('--report-limit',type=int,help='Bound one report capture pass')
    parser.add_argument('--include-unlocated',action='store_true',help='Also prepare bounded research extraction without a missing-document discovery target')
    args=parser.parse_args()
    if args.limit<1:parser.error('--limit must be positive')
    if any(v is not None and v<1 for v in (args.meeting_limit,args.report_limit)):parser.error('Capture limits must be positive')
    paid=args.step=='queue' or args.queue or args.submit
    if paid and args.budget_database is None:parser.error('queue requires --budget-database; never create a private spending authority')
    if paid and not args.budget_database.is_file():parser.error('--budget-database must name the existing installation-wide budget')
    if (args.queue or args.submit) and args.step!='run':parser.error('--queue/--submit apply only to run')
    archive=sqlite3.connect(args.database.resolve().as_uri()+('?mode=rw' if args.step=='backfill' else '?mode=ro'),uri=True)
    archive.row_factory=sqlite3.Row
    try:
        client=None
        if paid:
            import os
            from toronto_bids.batch_extraction import BatchQueue
            from toronto_bids.llm_budget import UsageBudget
            client=BatchExtractionClient()
            budget=UsageBudget(args.budget_database,os.environ.get('TB_LLM_MONTHLY_USD','20'))
            client.queue=BatchQueue(budget=budget,api_key=client.client.key)
        if args.step=='run':
            from toronto_bids.sources.city_agendas import Browser
            from toronto_bids.http import HttpClient
            http=HttpClient(retries=0)
            try:
                with Browser() as browser:
                    result=run(args.directory,archive,http=http,browser=browser,client=client,
                        meeting_limit=args.meeting_limit,report_limit=args.report_limit,
                        extraction_limit=args.limit,include_unlocated=args.include_unlocated,submit=args.submit,log=print)
            finally:http.close()
        elif args.step=='queue':
            result=queue(args.directory,archive,client=client,limit=args.limit,include_unlocated=args.include_unlocated,log=print)
        elif args.step=='backfill':
            from toronto_bids.store import db
            from toronto_bids.linking.procurement import resolve_procurements
            from toronto_bids.linking.supplier import build_supplier_dimension
            db.init_db(archive)
            result=backfill(args.directory,archive)
            resolve_procurements(archive);build_supplier_dimension(archive);archive.commit()
            (args.output or args.directory/'backfill.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        else:result=write_inventory(args.directory,archive,args.output or args.directory/'extraction-inventory.json')
    finally:archive.close()
    print(json.dumps(result,indent=2))
    if args.step=='run' and (result['agendas']['meetings'].get('failed') or result['reports']['reports'].get('failed')
                            or any(result[p].get('lane_state') for p in ('agendas','reports'))
                            or any(result.get('extraction',{}).get(k) for k in ('failed','changed_source'))):
        raise SystemExit(1)
    if args.step=='queue' and any(result.get(k) for k in ('failed','changed_source')):raise SystemExit(1)


if __name__=='__main__':main()
