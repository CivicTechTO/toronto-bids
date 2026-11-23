"""Bulk report screening, batch queueing, and participation backfill against the City agenda inventory."""
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
_RESPONDED_LIST=re.compile(r'\b(?:(?P<count>'+_SOURCE_COUNT+r')\s+)?(?:firms?|bidders?|proponents?|suppliers?)\s+responded\s+to\s+the\s+(?:RFP|RFT|RFQ|Call|Tender|Solicitation|Request\s+for\s+(?:Proposals?|Quotations?|Tenders?))\b[^:\n]{0,80}\bas\s+follows:\s*\n',re.I)
_RECEIVED_LIST_COUNT=re.compile(r'\b(?P<count>'+_SOURCE_COUNT+r')\s+(?:bids?|proposals?|submissions?|responses?)\b.{0,100}\breceived\b|\breceipt\s+of\s+(?:a\s+total\s+of\s+)?(?P<receipt>'+_SOURCE_COUNT+r')\s+(?:bids?|proposals?|submissions?)\b',re.I)
_TABLE=re.compile(r'\bTable\s+\d+\s*[:.\-]\s*(?:Summary\s+(?:of\s+)?(?:(?P<count>'+_SOURCE_COUNT+r')\s+)?(?:Bids?|Proposals?|Submissions?)\s+Received|List\s+of\s+(?:the\s+)?Suppliers\s+Who\s+Submitted\s+(?:a\s+)?(?:Proposals?|Bids?|Tenders?))\b[^\n]*(?:\n|$)',re.I)
_RESPONDED_TABLE=re.compile(r'\bTable\s+(?P<table>\d+)\s*[:.]\s*(?:Suppliers|Bidders)\s+and\s+(?:Fixed\s+)?Bid\s+Prices\b[^\n]*(?:\n|$)',re.I)
_RESPONDED_LEAD=re.compile(r'\b(?:A\s+total\s+(?:of\s+)?)?(?P<count>'+_SOURCE_COUNT+r')\s+(?:bidders?\s+responded|suppliers?\s+submitted\s+their\s+fixed\s+priced\s+bids?|bids?\s+were\s+submitted[^.\n]{0,80}?\bfrom\s+the\s+Suppliers)\b[^.\n:]{0,60}?\blisted\s+in\s+Table\s+(?P<table>\d+)\s+below\s*[:.]',re.I)
_POST_TABLE_RESPONDED=re.compile(r'\bsubmissions?\s+received\s+from\s+the\s+(?P<count>'+_SOURCE_COUNT+r')\s+(?:Suppliers?|bidders?|proponents?|firms?)\s+that\s+responded\s+to\b[^.]{0,180}',re.I)
_RECEIVED_AS_FOLLOWS=re.compile(r'\b(?:A\s+total\s+of\s+)?(?P<count>'+_SOURCE_COUNT+r')\s+(?:bids?|proposals?|submissions?)\s+were\s+received\b[^:]{0,180}\bas\s+follows:\s*\n',re.I)
_INLINE_SUBMITTED=re.compile(r'\b(?P<count>'+_SOURCE_COUNT+r')\s+(?:Suppliers?|Firms?|Proponents?|Bidders?)\s+submitted\s+(?:a\s+)?(?:proposals?|bids?|tenders?|submissions?)\s*:\s*(?P<body>.{10,400}?\.)(?=\s*(?:\n|$))',re.I|re.DOTALL)


_RECEIVED_REVIEWS = json.loads(
    Path(__file__).with_name("data").joinpath("city_bulk_received_reviews.json").read_text()
)


def _reviewed_identity_source(row, review, report_page):
    from lxml import html

    identity = review["independent_identity"]
    path = Path(row["local_path"]).with_name(identity["sha256"] + ".html")
    if not path.is_file():
        raise ValueError("reviewed_independent_identity_missing")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != identity["sha256"]:
        raise ValueError("reviewed_independent_identity_changed")
    contract = identity["contract"]
    wanted = identity["row"]
    matches = sum(
        [clean(cell.text_content()) for cell in tr.xpath("./td")] == wanted
        for tr in html.fromstring(data).xpath("//tr")
    )
    published_key = identity.get("published_key", "Doc" + review["document_number"])
    if (matches != 1 or contract not in report_page or not any(contract in cell for cell in wanted)
            or published_key not in wanted):
        raise ValueError("reviewed_independent_identity_not_literal")


def _reviewed_award_terms(archive, doc, identity, pages):
    # The original register supplies the award date. The report supplies the net term amounts.
    from datetime import date

    award_date = date.fromisoformat(identity["award_date"])
    terms = identity["net_terms"]
    award_text = " ".join(pages[page - 1] for page in identity["award_pages"])
    if (award_date.strftime("%m/%d/%Y") not in identity["row"]
            or identity["supplier"] not in award_text
            or not all(f"${amount:,} net of all applicable taxes and charges" in award_text for amount in terms)):
        raise ValueError("reviewed_award_terms_not_literal")
    matches = archive.execute(
        "SELECT DISTINCT document_number FROM award WHERE source='odata' AND award_date=? "
        "AND supplier_name_raw=? COLLATE NOCASE AND award_amount_numeric=?",
        (identity["award_date"], identity["supplier"], sum(terms)),
    )
    if {row[0] for row in matches} != {doc}:
        raise ValueError("reviewed_award_terms_match_absent_or_ambiguous")


def _reviewed_receipt(archive, row, review, native_identities):
    doc = review["document_number"]
    pages = [clean(page) for page in (row["text"] or "").split("\f")]
    for role in ("identity", "receipt"):
        page = review[f"{role}_page"]
        if not 1 <= page <= len(pages) or review[f"{role}_quotation"] not in pages[page - 1]:
            raise ValueError(f"reviewed_{role}_quotation_changed")
    if row["sha256"] != review["report_sha256"]:
        raise ValueError("reviewed_report_bytes_changed")
    publisher = archive.execute(
        "SELECT publisher_award_identity_json FROM solicitation WHERE document_number = ? AND source = 'odata' AND odata_id = ?",
        (doc, review["odata_id"]),
    ).fetchone()
    if not publisher:
        raise ValueError("reviewed_odata_identity_changed")
    if not review["names"] or not all(name in review["receipt_quotation"] for name in review["names"]):
        raise ValueError("invalid_reviewed_receipt")

    proof = dict(admission_version="city-reviewed-receipt-v1", report_url=row["url"],
                 contract_index=0, receipt_review=review, document_number=doc,
                 names=review["names"], report_sha256=review["report_sha256"],
                 prices_withheld=True, statuses_withheld=True)
    if call := review.get("native_call"):
        from toronto_bids.linking.city_bulk_identity import NativeCallIdentities
        if find_call_numbers(review["identity_quotation"]) != {call}:
            raise ValueError("reviewed_native_call_not_literal")
        if match := review.get("publisher_match"):
            from toronto_bids.amount import parse_amount

            for role in ("dates", "award"):
                page = match[f"{role}_page"]
                if not 1 <= page <= len(pages) or match[f"{role}_quotation"] not in pages[page - 1]:
                    raise ValueError(f"reviewed_{role}_quotation_changed")
            targets = archive.execute(
                "SELECT DISTINCT s.document_number FROM solicitation s JOIN award a USING (document_number) "
                "WHERE s.source='odata' AND a.source='odata' AND s.rfx_type=? AND s.issue_date=? "
                "AND s.submission_deadline=? AND a.supplier_name_raw=? COLLATE NOCASE AND a.award_amount_numeric=?",
                (match["rfx_type"], match["issue_date"], match["close_date"],
                 match["supplier"], parse_amount(match["amount"])),
            )
            if {row[0] for row in targets} != {doc} or review.get("alias_document_number"):
                raise ValueError("reviewed_publisher_match_absent_or_ambiguous")
        else:
            resolved, identity = (native_identities or NativeCallIdentities(archive)).resolve(call)
            if resolved != doc:
                raise ValueError("reviewed_native_call_identity_changed")
            proof["native_call_identity"] = identity
        if alias := review.get("alias_document_number"):
            quote = review["alias_quotation"]
            if quote not in pages[review["identity_page"] - 1] or set(re.findall(r"(?<!\d)\d{10}(?!\d)", quote)) != {alias}:
                raise ValueError("reviewed_alias_quotation_changed")
            if alias == doc or archive.execute(
                "SELECT 1 FROM solicitation WHERE document_number = ?", (alias,)
            ).fetchone():
                raise ValueError("reviewed_alias_conflicts_with_publisher_spine")
            proof.update(alias_document_number=alias, call_number=call)
    elif review.get("independent_identity"):
        from toronto_bids.linking.city_crosswalk import digest

        if not publisher[0] or digest(json.loads(publisher[0])) != review["publisher_record_sha256"]:
            raise ValueError("reviewed_independent_publisher_identity_changed")
        _reviewed_identity_source(row, review, pages[review["identity_page"] - 1])
        if alias := review.get("alias_document_number"):
            if (set(_DOC.findall(review["identity_quotation"])) != {alias} or alias == doc
                    or archive.execute("SELECT 1 FROM solicitation WHERE document_number = ?", (alias,)).fetchone()):
                raise ValueError("reviewed_independent_alias_identity_changed")
            proof["alias_document_number"] = alias
        else:
            _reviewed_award_terms(archive, doc, review["independent_identity"], pages)
    elif doc not in _DOC.findall(review["identity_quotation"]):
        raise ValueError("reviewed_document_not_literal")
    return proof


def _primary_heading(text):
    return re.split(r'\b(?:SUMMARY|RECOMMENDATIONS|PURPOSE)\b',text,flags=re.I)[0][:1600]


def _identity_role(doc,text,items=None):
    published=set(_DOC.findall(text))
    if published=={doc}:return 'unique_published_document'
    # Mixed reports must have a unique primary subject Doc and no conflicting page-footer Docs.
    heading_docs=set(_DOC.findall(_primary_heading(text)))
    allowed=None
    if heading_docs=={doc}:
        allowed={doc}
        role='primary_subject_and_local_document'
    elif (items and len(heading_docs)>1 and doc in heading_docs
          and any(set(_DOC.findall(i['title']))==heading_docs for i in items)):
        allowed=heading_docs
        role='joint_primary_subject_and_local_table'
    else:
        raise ValueError('conflicting_source_document_roles')
    for page in text.split('\f'):
        tail='\n'.join([line for line in page.splitlines() if line.strip()][-2:])
        if re.search(r'\bPage\s+\d+\s+of\s+\d+\b',tail,re.I) and set(_DOC.findall(tail))-allowed:
            raise ValueError('conflicting_source_document_roles')
    return role


def propose(archive, row, native_identities=None):
    """Emit source-bound participation proposals and structured exceptions for one screened report."""
    if review := _RECEIVED_REVIEWS.get(row["url"]):
        try:
            return [_reviewed_receipt(archive, row, review, native_identities)], []
        except ValueError as exc:
            return [], [dict(document_number=review["document_number"], reason=str(exc))]
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
            if not docs:
                bare=set(re.findall(r'(?<!\d)\d{10}(?!\d)',reference))
                if len(bare)==1 and re.match(r'^\d{10}(?:\s*[-/]|$)',reference):
                    docs=bare
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
                identity_role=_identity_role(doc,text,row['items'])
                if identity_role=='joint_primary_subject_and_local_table':
                    heading_docs=set(_DOC.findall(_primary_heading(text)))
                    items=[i for i in row['items'] if set(_DOC.findall(i['title']))==heading_docs]
                    identity_anchor='agenda_title'
                else:
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
            elif contract.get('solicitation_type')=='RFT' and contract.get('stage')=='prequalification_then_tender':
                from toronto_bids.linking.city_bulk_receipts import tender_witness
                tender=tender_witness(text,doc,contract,is_doc=True)
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
                           [(m,'submitted_firm_list') for m in _RESPONDED_LIST.finditer(text)]+
                           [(m,'submitted_bid_table') for m in _TABLE.finditer(text)]+
                           [(m,'submitted_bid_table') for m in _RESPONDED_TABLE.finditer(text)],key=lambda pair:pair[0].start())
            for marker,kind in ([] if tender or identity_role=='joint_primary_subject_and_local_table' else markers):
                context=clean(text[max(0,marker.start()-1200):marker.end()])
                lead=clean(text[max(0,marker.start()-250):marker.end()])
                count_quotation=marker.groupdict().get('count')
                if marker.re is _RESPONDED_TABLE:
                    response=_RESPONDED_LEAD.search(clean(text[max(0,marker.start()-550):marker.start()]))
                    heading_docs=set(_DOC.findall(marker[0])) or set(_DOC.findall(text[marker.start():marker.end()+80]))
                    if not response or response['table']!=marker['table'] or heading_docs!={doc}:continue
                    count_quotation=response['count']
                elif marker.re is not _RESPONDED_LIST and not (_PARTICIPATION.search(lead) or (call and _NATIVE_PARTICIPATION.search(lead))):continue
                counts=[count_quotation] if count_quotation else []
                if call and marker.re is _LIST:
                    counts.extend(m['count'] or m['receipt'] for m in _RECEIVED_LIST_COUNT.finditer(lead))
                if any(len(names)>int(re.search(r'\d+',q)[0]) for q in counts):continue
                if re.search(r'pre[- ]?qualif|invited to',marker[0] if call else context,re.I):continue
                # Reject lists whose surrounding context cites a competing historical call number.
                if find_call_numbers(context)-({call} if call else set()):continue
                if identity_role=='primary_subject_and_local_document' and set(_DOC.findall(context))!={doc}:continue
                section=text[marker.end():marker.end()+1800]
                # Trim section before evaluation or recommendation boilerplate.
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
            # Strict fallback witnesses (run only when primary loop found nothing, preserving byte-identical prior proofs).
            if witness is None and not tender:
                def _match_rows(sec):
                    q=clean(sec)
                    if re.search(r'^\s*no[- ]+bid\s*$',sec,re.I|re.M):return None
                    def _row(name):
                        pat=re.escape(name).replace(r'\ ',r'\s+')
                        return re.search(r'(?:^|\n)[ \t]*(?:[•●▪][ \t]*|\d+\.[ \t]+)?'+pat+r'[ \t]*[*†‡]*(?=\r?\n|$)',sec)
                    return q if all(name in q and _row(name) for name in names) else None

                # 1) Explicit doc-bound Suppliers/Bidders & Bid Prices table confirmed by an immediate post-table receipt sentence.
                for marker in _RESPONDED_TABLE.finditer(text):
                    heading_span=text[marker.start():marker.end()+120]
                    if set(_DOC.findall(heading_span))!={doc}:continue
                    section=text[marker.end():marker.end()+2000]
                    post=_POST_TABLE_RESPONDED.search(section)
                    if not post or set(_DOC.findall(post[0]))!={doc}:continue
                    if len(names)>int(re.search(r'\d+',post['count'])[0]):continue
                    table_body=section[:post.start()]
                    quote=_match_rows(table_body)
                    if quote:
                        witness=dict(kind='submitted_bid_table',context=clean(heading_span+' '+post[0]),list_quotation=quote);break

                # 2) Explicit doc-headed (or locally doc-bound) Summary of Bids Received table (immune to distant historical refs).
                if witness is None and identity_role!='joint_primary_subject_and_local_table':
                    for marker in _TABLE.finditer(text):
                        heading_span=text[marker.start():marker.end()+120]
                        heading_docs=set(_DOC.findall(heading_span))
                        lead=clean(text[max(0,marker.start()-(250 if heading_docs else 450)):marker.end()+120])
                        local_docs=heading_docs or set(_DOC.findall(lead))
                        if local_docs!={doc}:continue
                        if not _PARTICIPATION.search(lead) or re.search(r'pre[- ]?qualif|invited\s+to\s+(?:bid|tender|submit)',lead,re.I):continue
                        if find_call_numbers(lead):continue
                        cnt=marker.groupdict().get('count')
                        if not cnt:
                            m_cnt=_RECEIVED_LIST_COUNT.search(lead)
                            cnt=(m_cnt['count'] or m_cnt['receipt']) if m_cnt else None
                        if cnt and len(names)>int(re.search(r'\d+',cnt)[0]):continue
                        section=text[marker.end():marker.end()+1800]
                        boundary=re.search(r'\n\s*(?:As required|A selection|The selection|The evaluation|Contract Award|Financial Impact|CONTACT|RECOMMENDATIONS)\b',section,re.I)
                        if boundary:section=section[:boundary.start()]
                        quote=_match_rows(section)
                        if quote:
                            witness=dict(kind='submitted_bid_table',context=lead,list_quotation=quote);break

                # 3) Explicit "<count> bids/proposals were received ... as follows:" list/table or received-from-following-firms list
                #    where earlier negotiation-invitation wording does not indicate prequalification.
                if witness is None and identity_role!='joint_primary_subject_and_local_table':
                    for marker in sorted(list(_RECEIVED_AS_FOLLOWS.finditer(text))+list(_LIST.finditer(text)),key=lambda m:m.start()):
                        lead=clean(text[max(0,marker.start()-250):marker.end()])
                        context=clean(text[max(0,marker.start()-600):marker.end()])
                        if marker.re is _LIST and not (_PARTICIPATION.search(lead) or _NATIVE_PARTICIPATION.search(lead)):continue
                        if re.search(r'pre[- ]?qualif|invited\s+to\s+(?:bid|tender|submit)',context,re.I) or find_call_numbers(context):continue
                        if identity_role=='primary_subject_and_local_document' and set(_DOC.findall(context))!={doc}:continue
                        cnt=marker.groupdict().get('count')
                        if not cnt:
                            m_cnt=re.search(r'\b('+_SOURCE_COUNT+r')\b',marker[0]) or _RECEIVED_LIST_COUNT.search(lead)
                            cnt=m_cnt.group(1) if m_cnt and m_cnt.re.groups==1 else ((m_cnt['count'] or m_cnt['receipt']) if m_cnt else None)
                        if cnt and len(names)>int(re.search(r'\d+',cnt)[0]):continue
                        section=text[marker.end():marker.end()+1800]
                        boundary=re.search(r'\n\s*(?:As required|A selection|The selection|The evaluation|The bids were evaluated|Contract Award|Financial Impact|CONTACT|RECOMMENDATIONS)\b',section,re.I)
                        if boundary:section=section[:boundary.start()]
                        quote=_match_rows(section)
                        if quote:
                            witness=dict(kind='submitted_firm_list',context=context,list_quotation=quote);break

                # 4) Explicit inline prose "<count> Suppliers submitted a proposal: A, B and C."
                if witness is None and identity_role!='joint_primary_subject_and_local_table':
                    for marker in _INLINE_SUBMITTED.finditer(text):
                        context=clean(text[max(0,marker.start()-600):marker.end()])
                        if re.search(r'pre[- ]?qualif|invited\s+to\s+(?:bid|tender|submit)',context,re.I) or find_call_numbers(context):continue
                        if identity_role=='primary_subject_and_local_document' and set(_DOC.findall(context))!={doc}:continue
                        if len(names)>int(re.search(r'\d+',marker['count'])[0]):continue
                        body=clean(marker['body'])
                        if re.search(r'\bno[- ]+bid\b',body,re.I):continue
                        if all(name in body for name in names):
                            witness=dict(kind='submitted_firm_list',context=context,list_quotation=body);break
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
    """Screen held agenda reports and join them with cached extractions and candidate items."""
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
        existing_urls = {row["url"] for row in rows}
        for url, review in _RECEIVED_REVIEWS.items():
            path = Path(directory) / "reviewed-reports" / Path(url).name
            if url in existing_urls or not path.is_file():
                continue
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            if sha != review["report_sha256"]:
                raise ValueError(f"Held reviewed report bytes changed for {url}")
            native = subprocess.run(["pdftotext", "-q", str(path), "-"], capture_output=True, text=True, check=True)
            rows.append(dict(url=url, sha256=sha, local_path=str(path.resolve()), text=native.stdout.strip(),
                             screen="reviewed", items=[], result=None, error=None))
        return rows
    finally:conn.close()


def _native_call_target(row, native_identities):
    """Resolve a unique primary-subject/agenda-title native Call Number via archived-award proofs."""
    text=row['text'] or ''
    if _DOC.findall(text):return None
    calls=find_call_numbers(_primary_heading(text))
    if len(calls)!=1:return None
    call=next(iter(calls))
    items=[i for i in row['items'] if find_call_numbers(i['title'])=={call} and not _DOC.findall(i['title'])]
    if not items:return None
    for page in text.split('\f'):
        tail='\n'.join([line for line in page.splitlines() if line.strip()][-2:])
        if re.search(r'\bPage\s+\d+\s+of\s+\d+\b',tail,re.I) and find_call_numbers(tail)-{call}:
            return None
    try:
        doc,_=native_identities.resolve(call)
    except ValueError:
        return None
    return doc


def _missing_awarded_documents(archive) -> set[str]:
    """Return document_numbers of OData-awarded solicitations with no matching bid row."""
    bid_docs: set[str] = set()
    bid_procs: set[int] = set()
    for doc, pid in archive.execute("SELECT document_number, procurement_id FROM bid"):
        if doc is not None:
            bid_docs.add(doc)
        if pid is not None:
            bid_procs.add(pid)
    return {
        doc
        for doc, pid in archive.execute(
            "SELECT DISTINCT s.document_number, s.procurement_id "
            "FROM solicitation s "
            "JOIN award a ON a.document_number = s.document_number AND a.source = 'odata'"
        )
        if doc not in bid_docs and (pid is None or pid not in bid_procs)
    }


def queue(directory, archive, *, client, limit=50, include_unlocated=False, native_identities=None, log=lambda m:None):
    """Queue eligible unique PDF hashes via the shared batch client without submitting or writing facts."""
    missing=_missing_awarded_documents(archive)
    if native_identities is None:
        from toronto_bids.linking.city_bulk_identity import NativeCallIdentities
        native_identities=NativeCallIdentities(archive)
    rows=inventory(directory,archive)
    def priority(row):
        printed=set(_DOC.findall(row['text'] or ''))
        primary=set().union(*(set(_DOC.findall(i['title'])) for i in row['items']))
        native_doc=None
        if not printed and not primary and row['screen']=='eligible':
            native_doc=_native_call_target(row,native_identities)
        primary_targets=primary|({native_doc} if native_doc else set())
        row['covered_primary']=bool(primary_targets) and not bool(primary_targets & missing)
        row['discovery_targets']=(printed|primary_targets)&missing
        return (not bool((printed|({native_doc} if native_doc else set())) & missing),row['covered_primary'],row['url'])
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
            # Ready results are imported via collect, not cached here.
            client.extract_document(row['sha256'],split_document(row['text']),source='city_agenda_bulk')
            counts['ready_in_queue']+=1
        except ExtractionPending:counts['pending']+=1
        except (ValueError,TypeError) as exc:
            counts['failed']+=1;log(row['url']+': '+str(exc))
    return dict(counts)


def write_inventory(directory, archive, output):
    """Write screening states, proposals, and exceptions to output JSON."""
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


def _reuse_reviewed_bids(archive, proof):
    from toronto_bids.linking.supplier import supplier_key

    review = proof["receipt_review"]
    ref = review["council_reference"]
    doc = proof["document_number"]
    alias = proof.get("alias_document_number")
    held = archive.execute("SELECT document_number, bidder_name_raw FROM bid WHERE reference = ?", (ref,)).fetchall()
    if (any(bid[0] != alias for bid in held)
            or {supplier_key(bid[1]) for bid in held} != {supplier_key(name) for name in proof["names"]}):
        raise ValueError(f"Previously held bid list disagrees with reviewed receipt for {ref}")
    if alias is not None:
        return
    prior = archive.execute("SELECT document_number FROM solicitation_link WHERE reference = ?", (ref,)).fetchone()
    if prior and prior[0] != doc:
        raise ValueError(f"Conflicting stored solicitation link for {ref}")
    archive.execute(
        "INSERT OR IGNORE INTO solicitation_link (reference, document_number, method) "
        "VALUES (?, ?, 'city_bulk_reviewed_receipt')", (ref, doc),
    )


def backfill(directory, archive):
    """Validate and atomically admit bulk participation rows and evidence (caller owns commit)."""
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
        # Allow provenance items list to grow when Council reconsiders the same report.
        old_core={k:v for k,v in old.items() if k!='items'}
        current_core={k:v for k,v in current.items() if k!='items'}
        return (old_core==current_core and
                {canonical(i) for i in old.get('items',[])}<={canonical(i) for i in current.get('items',[])})
    pinned=defaultdict(set)
    for old in prior_rows:
        # Revalidate previously admitted reports in this inventory; other inventories stay untouched.
        value=json.loads(old['proof_json'])
        if digest(value)!=old['proof_sha256'] or (old['report_url'] in managed_urls and old['proof_sha256'] not in expected and
                not any(retained_context(value,current) for current,_ in accepted)):
            raise ValueError('Previously admitted bulk evidence is absent or changed; explicit repair required')
        if any(old[k]!=value[k] for k in ('document_number','report_url','report_sha256')):
            raise ValueError('Stored bulk evidence columns disagree with proof')
        pinned[old['document_number']].add(old['report_url'])
    # Pick one coherent list per document_number and preserve the pinned report_url on resume.
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
            if proof.get("alias_document_number") or proof.get("receipt_review", {}).get("reuse_reference_bids"):
                _reuse_reviewed_bids(archive, proof)
            else:
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
    """Run schedule seeding, agenda/report capture, optional batch queueing/submission, and screening."""
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
