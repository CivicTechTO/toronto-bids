"""Controlled stale-state replay on an isolated, post-#124 database copy."""
import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from itertools import zip_longest
from pathlib import Path

from toronto_bids.export.json_export import export_json
from toronto_bids.sources.bids_tenders import parse_listing, record_listings, store_listings
from toronto_bids.store import db

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--baseline', required=True, type=Path)
parser.add_argument('--trca-recordings', required=True, type=Path)
parser.add_argument('--out-dir', required=True, type=Path)
args = parser.parse_args()
root = args.out_dir
root.mkdir(parents=True, exist_ok=True)
assert not (root/'bids.sqlite').exists(), 'retain existing evidence'
baseline = sqlite3.connect(f'file:{args.baseline.resolve()}?mode=ro', uri=True)
baseline.row_factory = sqlite3.Row
conn = db.connect(root/'bids.sqlite')
baseline.backup(conn)
fixtures = Path(__file__).resolve().parents[2]/'scrapers/tests/fixtures/agencies'
trca = [json.loads(p.read_text()) for p in sorted(args.trca_recordings.glob('trca-*.json'))]
zoo = json.loads((fixtures/'bids_tenders_zoo_2026-10-21.json').read_text())
rows = trca + zoo
assert len(trca) == 249 and len(zoo) == 122
assert len({(r['buyer_slug'], r['Id']) for r in rows}) == 371
buyers = {r['slug']:r['id'] for r in conn.execute('SELECT * FROM buyer')}
identity_query = 'SELECT id,buyer_id,portal_url,procurement_id,first_seen FROM portal_listing ORDER BY id'
identities = [tuple(r) for r in conn.execute(identity_query)]
assert len(identities) == 371
# These are controlled stale database values, NOT purported historical source captures.
conn.execute("UPDATE portal_listing SET status='Open',title='controlled stale title',"
             "native_ref=NULL,posted_date='1999-01-01',closing_date='2000-01-01'")
conn.commit()
assert store_listings(conn, rows, buyers) == 371
for raw in rows:
    parsed = parse_listing(raw, buyers[raw['buyer_slug']])
    held = conn.execute('SELECT * FROM portal_listing WHERE buyer_id=? AND portal_url=?',
                        (parsed.buyer_id, parsed.portal_url)).fetchone()
    for field in ('native_ref','title','status','posted_date','closing_date'):
        assert held[field] == getattr(parsed, field), (raw['Id'],field)
assert identities == [tuple(r) for r in conn.execute(identity_query)]
assert store_listings(conn, list(reversed(rows)), buyers) == 371
assert identities == [tuple(r) for r in conn.execute(identity_query)]
unchanged = {}
for (table,) in baseline.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('portal_listing','sqlite_sequence') ORDER BY name"):
    digest = hashlib.sha256()
    count = 0
    for before, after in zip_longest(baseline.execute(f'SELECT * FROM {table} ORDER BY rowid'),
                                    conn.execute(f'SELECT * FROM {table} ORDER BY rowid')):
        assert before is not None and after is not None and tuple(before) == tuple(after), table
        digest.update(json.dumps(tuple(before),ensure_ascii=False).encode())
        count += 1
    unchanged[table] = dict(rows=count,sha256=digest.hexdigest())
assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
assert not conn.execute('PRAGMA foreign_key_check').fetchall()
record_listings(rows, root/'recordings')
export_json(conn, root/'bids.json', '2026-10-22T00:00:00+00:00')
exported = json.loads((root/'bids.json').read_text())
assert sum(len(b['portal_listings']) for b in exported['buyers']) == 371
report = dict(records=371, refreshed_fields=['native_ref','title','status','posted_date','closing_date'],
              controlled_stale_state_restored=True, identities_and_first_seen_preserved=True,
              source_tables_unchanged=unchanged, integrity_check='ok', foreign_key_violations=0,
              status_counts={slug:dict(Counter(r['Status'] for r in group))
                             for slug,group in [('trca',trca),('toronto-zoo',zoo)]},
              awarded_grid_records=sum(r['Status']=='Awarded' for r in rows),
              grid_fields={slug:sorted({k for r in group for k in r})
                           for slug,group in [('trca',trca),('toronto-zoo',zoo)]},
              raw_snapshots=len(list((root/'recordings').glob('*.json'))),
              replay_network_requests=0,paid_calls=0,production_modified=False)
(root/'validation.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k not in ('source_tables_unchanged','grid_fields')},indent=2))
