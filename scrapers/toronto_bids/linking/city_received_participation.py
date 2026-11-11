"""Validate literal, reviewed received-submission witnesses over immutable caches.

This is an admission boundary, not a PDF extractor. The review accounts for every
cached observation and keeps scope exclusions while projecting unpriced firms.
"""
import hashlib
import json
from pathlib import Path
import re

from toronto_bids import config
from toronto_bids.extract import EXTRACTOR_VERSION
from toronto_bids.linking import city_archived_awards as archived
from toronto_bids.linking import city_crosswalk as cw
from toronto_bids.pdf_text import source_text

ROLE = 'reviewed_received_submissions'
_RECEIVED = re.compile(
    r'Number\s+of\s+(?:Bids|Proposals|Submissions)\b|'
    r'Summary\s+of\s+(?:Bids|Proposals)\s+Received\b|'
    r'\b(?:bids|proposals)\s+(?:were\s+)?received\b', re.I)
_NO_BID = re.compile(r'\bNo\s+Bid\b', re.I)


def _quotation(text, witness):
    start, end, quote = (witness.get(k) for k in ('start', 'end', 'quote'))
    if (type(start) is not int or type(end) is not int or not isinstance(quote, str)
            or not quote.strip() or not 0 <= start < end <= len(text)
            or text[start:end] != quote):
        raise ValueError('literal received-submission quotation changed or absent')
    return quote


def propose(conn, *, evidence, document_number, archive_source_sha256,
            archive_row_ordinal, report_url, review, current=None,
            previously_approved=False):
    """Bind a reviewed receipt table to an independently proven City event."""
    index = archived.current_index(conn) if current is None else current
    award, publisher, _ = archived.validate_current_award(
        conn, evidence, document_number, archive_source_sha256, archive_row_ordinal, index)
    call = award['call_number']
    row = conn.execute('SELECT * FROM background_pdf WHERE url=?', (report_url,)).fetchone()
    if row is None or row['kind'] != 'bgrd' or not row['reference']:
        raise ValueError('original City report absent')
    item = conn.execute('SELECT title FROM council_item WHERE reference=?', (row['reference'],)).fetchone()
    if item is None or cw.find_call_numbers(item['title']) != {call}:
        raise ValueError('received-source agenda does not uniquely name the native call')
    text = source_text(conn, row)
    if not text:
        raise ValueError('original report text absent')
    path = Path(row['local_path']) if row['local_path'] else None
    if path is None or not path.exists():
        path = config.DATA_DIR/'documents/council'/Path(report_url).name
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('original PDF bytes changed')
    elif not previously_approved:
        raise ValueError('new received-submission review requires held original PDF')
    cache = conn.execute('SELECT result_json FROM extraction_cache WHERE sha256=? AND extractor_version=?',
                         (row['sha256'], EXTRACTOR_VERSION)).fetchone()
    if cache is None:
        raise ValueError('current shared extraction absent')
    raw = cache[0]
    contracts = json.loads(raw).get('contracts', [])
    matching = [i for i, c in enumerate(contracts) if cw.find_call_numbers(c.get('reference')) == {call}]
    selected = review.get('contract_indices')
    if not matching or selected != matching or any(type(i) is not int for i in selected):
        raise ValueError('all cached native-call observations must be accounted for')
    observations = {(ci, bi): bid for ci in selected
                    for bi, bid in enumerate(contracts[ci].get('bids', []))}
    parties = review.get('parties', [])
    positions = [(p.get('contract_index'), p.get('bid_index')) for p in parties]
    if not observations or len(set(positions)) != len(positions) or set(positions) != set(observations):
        raise ValueError('every cached party observation needs one source-role review')
    sections = review.get('receipt_sections', [])
    if not sections:
        raise ValueError('original received-submission section absent')
    headings = review.get('receipt_headings', [])
    for parts in headings:
        if (not parts or any(parts[i]['end'] > parts[i+1]['start'] for i in range(len(parts)-1))
                or not _RECEIVED.fullmatch(' '.join(' '.join(_quotation(text, p).split()) for p in parts))):
            raise ValueError('split receipt-count heading requires exact ordered source parts')
    receipt_sections = []
    for section in sections:
        quote = _quotation(text, section)
        # Supplementary name-column witnesses may overlap a larger received table.
        # A naked supplier heading or an award recommendation is insufficient.
        if (_RECEIVED.search(quote) or any(all(section['start'] <= p['start'] < p['end'] <= section['end']
                                              for p in parts) for parts in headings)):
            receipt_sections.append(section)
        if cw.find_call_numbers(quote) - {call}:
            raise ValueError('received table names a conflicting native call')
    if not receipt_sections:
        raise ValueError('explicit received-submission source role absent')
    names = {}
    withheld = []
    for party in parties:
        position = (party['contract_index'], party['bid_index'])
        bid = observations[position]
        parts = party.get('name_parts', [])
        if not parts or any(parts[i]['end'] > parts[i+1]['start'] for i in range(len(parts)-1)):
            raise ValueError('ordered full party-name quotations required')
        name = ' '.join(' '.join(_quotation(text, p).split()) for p in parts)
        if not cw.supplier_identity(name) or cw.supplier_identity(name) != cw.supplier_identity(bid.get('supplier_name')):
            raise ValueError('cached participant differs from full literal received party')
        if not any(all(s['start'] <= p['start'] < p['end'] <= s['end'] for p in parts)
                   for s in receipt_sections):
            raise ValueError('party name falls outside received-submission scope')
        scope_parts = party.get('scope_parts', [])
        if bool(party.get('source_scope')) != bool(scope_parts):
            raise ValueError('named response scope requires its literal source quotation')
        scope_names = [' '.join(_quotation(text, part).split()) for part in scope_parts]
        if scope_parts and ' '.join(party['source_scope'].split()) != ' and '.join(scope_names):
            raise ValueError('response scope label differs from its full source quotation')
        # Never read a named No Bid row as an actual submission. A firm's separate
        # submitted row in another lot remains eligible for event participation.
        tail_end = text.find('\n', parts[-1]['end'])
        if tail_end == -1:
            tail_end = len(text)
        tail = text[parts[-1]['end']:tail_end]
        role = party.get('role')
        if role == 'no_bid':
            witness = party.get('no_bid_witness', {})
            quote = _quotation(text, witness)
            if (bid.get('status') != 'no_bid' or not _NO_BID.search(quote)
                    or not all(witness['start'] <= p['start'] < p['end'] <= witness['end'] for p in parts)):
                raise ValueError('scoped No Bid observation lacks its literal row')
        elif role == 'received_submission':
            if bid.get('status') == 'no_bid' or _NO_BID.search(tail):
                raise ValueError('No Bid cannot become a submitted observation')
            names.setdefault(cw.supplier_identity(name), ' '.join(name.split()))
        else:
            raise ValueError('unsupported received-submission source role')
        withheld.append(dict(contract_index=position[0], raw_bid_index=position[1], **bid))
    if not names:
        raise ValueError('received review has no proven participant')
    return dict(version=1, source='city_native_review', source_role=ROLE,
                document_number=document_number, call_number=call, reference=row['reference'],
                agenda_title=item['title'], report_url=report_url, report_sha256=row['sha256'],
                text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                result_sha256=hashlib.sha256(raw.encode()).hexdigest(), extractor_version=EXTRACTOR_VERSION,
                receipt_review=review, participant_names=sorted(names.values()),
                withheld_model_observations=withheld,
                withheld_model_contracts=[dict(contract_index=i, contract=contracts[i]) for i in selected],
                publisher_record_sha256=cw.digest(publisher), snapshot_sha256=evidence.snapshot_sha256,
                archived_award=dict(source_sha256=archive_source_sha256, row_ordinal=archive_row_ordinal,
                                    row_sha256=award['row_sha256'], source_url=award['source']['url'],
                                    printed_award_date=award['published_award_date']))
