"""Read-only City bid coverage audit (#163); absence is never proof of zero bids.

Run with python -m toronto_bids.bid_coverage --database PATH --out DIRECTORY.
Document mentions identify review candidates, never procurement equivalences.
"""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from toronto_bids.extract import EXTRACTOR_VERSION

_DOC = re.compile(r'\bDoc(?:ument(?:\s+Number)?)?\s*[:#]?\s*(\d{10})(?!\d)', re.I)
_CITY_KINDS = {'bgrd', 'committee_award', 'award_summary'}


def audit(conn):
    """Return a deterministic audit of existing facts; execute no writes or model calls."""
    conn.row_factory = sqlite3.Row
    sols = {r['document_number']: dict(r) for r in conn.execute(
        "SELECT s.document_number,s.title,s.division,s.issue_date,s.procurement_id "
        "FROM solicitation s WHERE EXISTS (SELECT 1 FROM award a "
        "WHERE a.document_number=s.document_number AND a.source='odata') ORDER BY s.document_number")}
    awards = defaultdict(list)
    for r in conn.execute("SELECT * FROM award WHERE source='odata' ORDER BY id"):
        awards[r['document_number']].append(dict(r))
    bids = [dict(r) for r in conn.execute('SELECT * FROM bid ORDER BY id')]
    candidates = defaultdict(set)
    for b in bids:
        if b['reference'] and b['document_number']:
            candidates[b['reference']].add(b['document_number'])
    for r in conn.execute('SELECT * FROM solicitation_link'):
        candidates[r['reference']].add(r['document_number'])
    bridge = {ref: next(iter(docs)) for ref, docs in candidates.items() if len(docs) == 1}
    by_doc = defaultdict(set)
    canonical = defaultdict(set)
    pid_doc = {s['procurement_id']: doc for doc, s in sols.items() if s['procurement_id'] is not None}
    for b in bids:
        # An explicit document wins. A broad reference never distributes sibling bids.
        doc = b['document_number'] or bridge.get(b['reference'])
        if doc in sols:
            by_doc[doc].add(b['id'])
        if b['procurement_id'] in pid_doc:
            canonical[pid_doc[b['procurement_id']]].add(b['id'])
    mismatches = [doc for doc in sols if by_doc[doc] != canonical[doc]]
    missing = set(sols) - {doc for doc, facts in by_doc.items() if facts}
    pid_missing = {s['procurement_id']: doc for doc, s in sols.items()
                   if doc in missing and s['procurement_id'] is not None}
    associations = defaultdict(set)
    for r in conn.execute('SELECT procurement_id,url FROM procurement_document'):
        if r['procurement_id'] in pid_missing:
            associations[r['url']].add(pid_missing[r['procurement_id']])
    cache = {(r['sha256'], r['extractor_version']): r['result_json']
             for r in conn.execute('SELECT * FROM extraction_cache')}
    reports = defaultdict(list)
    for r in conn.execute('SELECT * FROM background_pdf ORDER BY url'):
        if r['kind'] not in _CITY_KINDS:
            continue
        targets = associations[r['url']] & missing
        if r['document_number'] in missing:
            targets.add(r['document_number'])
        mentions = set(_DOC.findall(r['text'] or '')) & missing
        for doc in sorted(targets | mentions):
            raw = cache.get((r['sha256'], EXTRACTOR_VERSION))
            contracts = json.loads(raw).get('contracts', []) if raw else []
            position = next((m.start() for m in _DOC.finditer(r['text'] or '') if m[1] == doc), None)
            snippet = None if position is None else ' '.join(r['text'][max(0,position-110):position+210].split())
            reports[doc].append(dict(url=r['url'],kind=r['kind'],reference=r['reference'],sha256=r['sha256'],
                association='stored' if doc in targets else 'printed_document_mention_only',
                has_text=bool((r['text'] or '').strip()),current_cache=raw is not None,
                result_sha256=hashlib.sha256(raw.encode()).hexdigest() if raw else None,
                cached_named_bid_observations=sum(bool(b.get('supplier_name')) for contract in contracts for b in contract.get('bids', [])),
                cached_contracts=[dict(reference=x.get('reference'),named_bids=sum(bool(b.get('supplier_name')) for b in x.get('bids',[])),declared_submissions=x.get('declared_submissions')) for x in contracts],
                mention_context=snippet))
    rows = []
    for doc in sorted(missing):
        s = sols[doc]; evidence = reports[doc]
        if not evidence:
            group = 'no_report_association_or_explicit_held_mention'
        elif any(e['current_cache'] and e['cached_named_bid_observations'] for e in evidence):
            group = 'cached_bid_observations_need_attribution_review'
        elif any(e['current_cache'] for e in evidence):
            group = 'current_cache_without_named_bids_needs_source_review'
        elif any(e['sha256'] and e['has_text'] for e in evidence):
            group = 'held_text_without_current_cache'
        elif any(e['sha256'] for e in evidence):
            group = 'held_document_without_text'
        else:
            group = 'queued_document_not_held'
        values = [a['award_amount_numeric'] for a in awards[doc]
                  if a['award_amount_numeric'] is not None and a['award_amount_numeric'] > 0
                  and a['award_amount_verdict'] not in ('not_an_award','corrupt','unknown','not_an_amount')]
        year = (s['issue_date'] or '')[:4]
        rows.append(dict(document_number=doc,title=s['title'],division=s['division'],issue_year=int(year) if year.isdigit() else None,
            max_parseable_award_line=max(values,default=None),group=group,
            award_lines=[{k:a[k] for k in ('supplier_name_raw','award_amount','award_amount_numeric','award_date','award_amount_verdict')} for a in awards[doc]],
            reports=evidence))
    groups = dict(sorted(Counter(r['group'] for r in rows).items()))
    cohorts = {}
    for name, test in [('through_2012',lambda y:y is not None and y<=2012),('2013_2018',lambda y:y is not None and 2013<=y<=2018),('2019',lambda y:y==2019),('2020_onward',lambda y:y is not None and y>=2020),('unknown',lambda y:y is None)]:
        cohort = [doc for doc,s in sols.items() if test(int(s['issue_date'][:4]) if s['issue_date'] and s['issue_date'][:4].isdigit() else None)]
        cohorts[name] = dict(awarded=len(cohort),without_bids=len(set(cohort)&missing))
    direct_docs = {b['document_number'] for b in bids if b['document_number']}
    linked_only = sum(bool(by_doc[doc]) and doc not in direct_docs for doc in sols)
    excluded = [doc for doc in sols if all(a['award_amount_verdict']=='not_an_award' for a in awards[doc])]
    summary = dict(fully_labelled_non_award_documents=excluded,awarded_city_solicitations=len(sols),with_bids=len(sols)-len(missing),without_bids=len(missing),
        without_bids_percent=round(100*len(missing)/len(sols),2) if sols else None,
        covered_only_through_reference_link=linked_only,groups=groups,issue_year_cohorts=cohorts,
        ambiguous_reference_count=sum(len(docs)>1 for docs in candidates.values()),canonical_bid_attribution_mismatches=mismatches,
        missing_with_stored_report=sum(any(e['association']=='stored' for e in r['reports']) for r in rows),
        missing_with_explicit_held_document_mention=sum(any(e['mention_context'] for e in r['reports']) for r in rows),
        confirmed_zero_bid_solicitations=None)
    assert sum(groups.values()) == len(missing)
    assert sum(x['without_bids'] for x in cohorts.values()) == len(missing)
    return dict(schema_version=1,extractor_version=EXTRACTOR_VERSION,summary=summary,missing=rows)


def write_report(result, out):
    out = Path(out);out.mkdir(parents=True,exist_ok=True)
    # Keep the generated evidence to one line per document so later audits diff cleanly.
    header = json.dumps({k: v for k, v in result.items() if k != 'missing'}, indent=2)
    rows = ',\n'.join('    '+json.dumps(row, ensure_ascii=False) for row in result['missing'])
    (out/'audit.json').write_text(header[:-2]+',\n  "missing": [\n'+rows+'\n  ]\n}\n')
    with (out/'missing.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,lineterminator='\n',fieldnames=['document_number','issue_year','division','title','max_parseable_award_line','group'])
        writer.writeheader();writer.writerows({k:r[k] for k in writer.fieldnames} for r in result['missing'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    with sqlite3.connect(args.database.resolve().as_uri()+'?mode=ro',uri=True) as conn:
        conn.execute('PRAGMA query_only=ON');result=audit(conn)
    write_report(result,args.out)
    print(json.dumps(result['summary'],indent=2))
    if result['summary']['canonical_bid_attribution_mismatches']:
        raise SystemExit('Stored canonical IDs disagree with source-proven links; review before using counts')


if __name__=='__main__':
    main()
