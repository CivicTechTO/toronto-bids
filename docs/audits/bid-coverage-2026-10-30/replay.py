"""Measure cached replay on a temporary copy. No production writes or API calls."""
import argparse
import json
from pathlib import Path
import sqlite3
import tempfile

from toronto_bids.bid_coverage import audit
from toronto_bids.extraction import backfill_from_extraction
from toronto_bids.linking.procurement import resolve_procurements
from toronto_bids.store import db

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--database', type=Path, required=True)
parser.add_argument('--out', type=Path, required=True)
args = parser.parse_args()
with tempfile.TemporaryDirectory(prefix='tb-163-replay-') as directory:
    path = Path(directory) / 'copy.sqlite'
    path.touch(mode=0o600)
    with sqlite3.connect(args.database.resolve().as_uri()+'?mode=ro', uri=True) as source, sqlite3.connect(path) as target:
        source.backup(target)
    conn = db.connect(path)
    before = audit(conn)
    bids_before = {r['id']: dict(r) for r in conn.execute('SELECT * FROM bid')}
    def payloads(table):
        return [{k: r[k] for k in r.keys() if k != 'procurement_id'}
                for r in conn.execute(f'SELECT * FROM {table} ORDER BY rowid')]
    immutable = {table: payloads(table) for table in ('award', 'solicitation', 'extraction_cache')}
    backfill = {corpus: backfill_from_extraction(conn, corpus) for corpus in ('committee', 'ba_report')}
    resolve_procurements(conn)
    after = audit(conn)
    bids_after = {r['id']: dict(r) for r in conn.execute('SELECT * FROM bid')}
    removed = sorted(set(bids_before)-set(bids_after))
    changed = [{"id": i, "fields": [k for k in bids_before[i] if k not in ('last_seen', 'procurement_id')
                                  and bids_before[i][k] != bids_after[i][k]]}
               for i in sorted(set(bids_before)&set(bids_after))]
    changed = [r for r in changed if r['fields']]
    # IDs may refresh, but cached source/financial payload must not change.
    preserved = all(payloads(table) == rows for table, rows in immutable.items())
    old = {r['document_number'] for r in before['missing']}
    new = {r['document_number'] for r in after['missing']}
    result = dict(scope='Temporary-copy counterfactual; cached model output is not source approval', model_requests=0,
                  backfill=backfill, before=before['summary'], after=after['summary'],
                  newly_covered=sorted(old-new), newly_uncovered=sorted(new-old),
                  added_bid_rows=len(set(bids_after)-set(bids_before)), removed_bid_ids=removed,
                  changed_prior_bids=changed, award_solicitation_and_cache_payloads_preserved=preserved)
    assert preserved and not removed and not after['summary']['canonical_bid_attribution_mismatches']
    args.out.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({k: result[k] for k in ('added_bid_rows','newly_covered','newly_uncovered','changed_prior_bids')}))
    conn.close()
