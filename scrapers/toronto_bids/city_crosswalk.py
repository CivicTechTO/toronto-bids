"""Read-only proposals for historical City identity review; never applies links.

python -m toronto_bids.city_crosswalk --database DB --publisher-snapshot JSON
    --candidates JSON --agendas DIRECTORY --out DIRECTORY

The candidate file is a discovery inventory, not proof. Accepted proposals must
match original source witnesses independently of how the pair was discovered.
"""
import argparse
from collections import Counter,defaultdict
import gzip
import hashlib
import json
import re
from pathlib import Path
import sqlite3
from toronto_bids.linking.city_crosswalk import canonical,digest,publisher_index,propose
from toronto_bids.linking.call_number import find_call_numbers,normalize_call_number
from toronto_bids.bid_coverage import audit



def discover(conn):
    """Refresh numeric search candidates, never identity evidence or database writes."""
    calls=defaultdict(list)
    for item in conn.execute('SELECT reference,title FROM council_item ORDER BY reference'):
        call=normalize_call_number(item['title'])
        if not call:continue
        digits=re.sub(r'\D','',call)
        key=digits if len(digits)==10 else digits.ljust(10,'0') if re.fullmatch(r'\d{1,4}-(?:19|20)\d{2}',call) else None
        if key:calls[key].append(item['reference'])
    bids=dict(conn.execute('SELECT reference,COUNT(*) FROM bid WHERE reference IS NOT NULL GROUP BY reference'))
    candidates=[]
    for item in audit(conn)['missing']:
        year=item['issue_year'];doc=item['document_number']
        if year is None or year>2019:continue
        refs=calls.get(doc,[])
        count=sum(bids.get(ref,0) for ref in refs)
        if count:candidates.append(dict(document_number=doc,encoded_call_refs=refs,bids_on_candidate_refs=count))
    return candidates


def review(conn,records,candidates,agendas,snapshot_sha256):
    index=publisher_index(records);by_doc=defaultdict(list)
    for row in records:by_doc[row.get('Solicitation_Document_Number')].append(row)
    proposals=[];refused=[];files={}
    call_refs=defaultdict(set)
    for item in conn.execute('SELECT reference,title FROM council_item'):
        for call in find_call_numbers(item['title']):call_refs[call].add(item['reference'])
    for candidate in candidates:
        if not candidate.get('bids_on_candidate_refs'):continue
        doc=candidate['document_number'];refs=candidate['encoded_call_refs']
        if len(refs)!=1:
            refused.append(dict(document_number=doc,references=refs,reason='multiple council contexts for native call'));continue
        ref=refs[0];rows=by_doc.get(doc,[])
        if len(rows)!=1:
            refused.append(dict(document_number=doc,reference=ref,reason='publisher document record missing or repeated'));continue
        item=conn.execute('SELECT title FROM council_item WHERE reference=?',(ref,)).fetchone()
        sol=conn.execute('SELECT odata_id,issue_date,submission_deadline,rfx_type FROM solicitation WHERE document_number=?',(doc,)).fetchone()
        path=Path(agendas)/(ref.rsplit('.',1)[0]+'.html')
        try:
            if not item or not path.exists():raise ValueError('held original agenda context absent')
            pub=rows[0]
            if not sol or tuple(sol)!=(pub.get('id'),pub.get('Issue_Date'),pub.get('Closing_Date'),pub.get('Solicitation_Document_Type')):
                raise ValueError('stored spine and current publisher context disagree')
            if path not in files:files[path]=path.read_bytes()
            source=files[path]
            proof=propose(reference=ref,title=item['title'],source=source,publisher=rows[0],index=index,
                          source_sha256=hashlib.sha256(source).hexdigest(),snapshot_sha256=snapshot_sha256)
            if call_refs[proof['call_number']]!={ref}:raise ValueError('native call also appears in another council context')
            proposals.append(proof)
        except ValueError as exc:
            refused.append(dict(document_number=doc,reference=ref,reason=str(exc)))
    return dict(proposals=proposals,refusals=refused,
                summary=dict(candidates=len(proposals)+len(refused),proposed=len(proposals),refused=len(refused),
                             proof_classes=dict(Counter(p['proof_class'] for p in proposals)),
                             refusal_reasons=dict(Counter(p['reason'] for p in refused))))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('database','publisher-snapshot','agendas','out'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--candidates',type=Path,help='Optional frozen discovery inventory; otherwise refresh from the database')
    args=p.parse_args();raw=args.publisher_snapshot.read_bytes();records=json.loads(raw)['rows']
    conn=sqlite3.connect(args.database.resolve().as_uri()+'?mode=ro',uri=True);conn.row_factory=sqlite3.Row
    candidates=json.loads(args.candidates.read_text()) if args.candidates else discover(conn)
    report=review(conn,records,candidates,args.agendas,hashlib.sha256(raw).hexdigest())
    args.out.mkdir(parents=True,exist_ok=True)
    (args.out/'candidates.json').write_text(json.dumps(candidates,indent=2)+'\n')
    (args.out/'review.json').write_text(json.dumps(report,indent=2)+'\n')
    # The complete compact source population makes the uniqueness check portable,
    # while only proposed rows need their full original record for proof replay.
    keep=('id','Solicitation_Document_Number','Issue_Date','Closing_Date','Solicitation_Document_Type','Solicitation_Award_Details')
    population=[dict({k:r.get(k) for k in keep},Awarded_Suppliers=[{k:a.get(k) for k in ('Successful_Bidder','Award_Amount')} for a in r.get('Awarded_Suppliers') or []]) for r in records]
    name='city_call_publisher_population.json.gz'
    (args.out/name).write_bytes(gzip.compress(canonical(population).encode(),mtime=0))
    ids={p['publisher_record_id'] for p in report['proposals']}
    bundle=dict(version=1,snapshot_sha256=hashlib.sha256(raw).hexdigest(),publisher_population_file=name,
                publisher_population_sha256=digest(population),publisher_population_count=len(population),
                publisher_records=[r for r in records if r.get('id') in ids],proofs=report['proposals'])
    (args.out/'city_call_crosswalk.json').write_text(json.dumps(bundle,indent=2)+'\n')
    print(json.dumps(report['summary'],indent=2))


if __name__=='__main__':main()
