"""Source-bound final tender rows, independently of model stage classifications."""
import re

from toronto_bids.linking.call_number import find_call_numbers
from toronto_bids.sources.city_agendas import clean

_OPENED=re.compile(r'\bopened\s+the\s+(?:following\b[^:]{0,160}\b(?:(?:Tender\s+)?submissions?|Tenders?|bids?)|tender\s+submissions?)\b[^:]{0,100}:\s*\n',re.I)
_TABLE=re.compile(r'\bTable\s+\d+\s*[:.\-]\s*Summary\s+of\s+Bids\s+received\b[^\n]*\n',re.I)
_NO_BID=re.compile(r'\b(?:No\s+bid(?:\s+submitted)?|Did\s+not\s+bid)\b',re.I)
_DOC=re.compile(r'\b(?:Doc(?:ument)?\.?(?:\s+(?:Number|No\.?))?|RFP|RFQ|RFT)\s*[:#]?\s*(\d{10})(?!\d)',re.I)


def tender_witness(text,call,contract,*,is_doc=False):
    """Require an actual opened/received table and literal priced or No Bid rows.

    Prices only distinguish actual responses from invitations; they are never
    projected. Every model observation is checked, including excluded No Bids.
    """
    if contract.get('solicitation_type')!='RFT' or contract.get('stage') not in ('single_stage','prequalification_then_tender'):
        return None
    raw=contract.get('bids',[])
    if not raw:return None
    for marker in sorted(list(_OPENED.finditer(text))+list(_TABLE.finditer(text)),key=lambda m:m.start()):
        # Ignore an older qualification call before the final tender subject;
        # anything after the most recent explicit current call still competes.
        before=text[max(0,marker.start()-1400):marker.end()]
        if is_doc:
            hits=[m for m in _DOC.finditer(before) if m.group(1)==call]
            if not hits:continue
            context=before[hits[-1].start():]
            if set(_DOC.findall(context))!={call}:continue
        else:
            pattern=r'\s*[-–—]\s*'.join(re.escape(part) for part in call.split('-'))
            calls=list(re.finditer(pattern,before))
            if not calls:continue
            context=before[calls[-1].start():]
            if find_call_numbers(context)!={call}:continue
        section=text[marker.end():marker.end()+2400]
        boundary=re.search(r'\n\s*(?:COMMENTS|The Tender documents|The low bid|\d+\)\s+An additional|\*Tender Price)\b',section,re.I)
        if boundary:section=section[:boundary.start()]
        positions=[]
        for bid in raw:
            name=clean(bid.get('supplier_name'))
            if not name:break
            literal=re.escape(name).replace(r'\ ',r'\s+')
            matches=list(re.finditer(r'(?:^|\n)[ \t]*'+literal+r'[ \t]*[*†‡]*(?=\r?\n|$)',section))
            if len(matches)!=1:break
            positions.append((matches[0],name,bid))
        if len(positions)!=len(raw):continue
        positions.sort(key=lambda p:p[0].start())
        header=section[:positions[0][0].start()]
        if (set(_DOC.findall(header))-{call} if is_doc else find_call_numbers(header)-{call}):continue
        names=[];excluded=[];rows=[]
        for index,(match,name,bid) in enumerate(positions):
            end=positions[index+1][0].start() if index+1<len(positions) else len(section)
            response=re.search(r'\S[^\r\n]*',section[match.end():end])
            if response is None:break
            # An omitted model party is still a source row boundary. Only the
            # immediately following nonempty line can be this party's response.
            outcome=response[0]
            end=match.end()+response.end()
            no_bid=_NO_BID.fullmatch(outcome.strip())
            if no_bid:
                if bid.get('status')!='no_bid' or re.search(r'\$\s*\d',outcome):break
                excluded.append(dict(name=name,quotation=clean(section[match.start():end])))
            else:
                if bid.get('status')=='no_bid' or not re.match(r'\$\s*\d',outcome):break
                names.append(name)
            rows.append(dict(name=name,quotation=clean(section[match.start():end])))
        else:
            names=list(dict.fromkeys(names));declared=contract.get('declared_submissions')
            count=re.search(r'\bfollowing\s+(?:\w+\s*\()?([0-9]+)\)?\s+Tender\s+submissions?\b',marker[0],re.I)
            if not names or (type(declared) is int and len(names)>declared) or (count and len(names)>int(count[1])):continue
            return names,dict(kind='modern_tender_received_table' if is_doc else 'native_tender_received_table',
                context=clean(context),list_quotation=clean(section),rows=rows,excluded_no_bid=excluded)
    return None
