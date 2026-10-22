"""Run from the repository with scrapers/.venv/bin/python; writes only to --out-dir."""
import hashlib,json,sqlite3,time
from itertools import zip_longest
from pathlib import Path
from toronto_bids.store import db
from toronto_bids.linking.procurement import resolve_procurements,FACT_TABLES
from toronto_bids.sources.bids_tenders import store_listings
from toronto_bids.export.json_export import export_json,export_schema
from toronto_bids.export.tabular_export import write_csv_zip,write_parquet_files

import argparse
parser = argparse.ArgumentParser(description='Offline #124 migration/preservation rehearsal; baseline is read-only.')
parser.add_argument('--baseline', type=Path, required=True)
parser.add_argument('--out-dir', type=Path, required=True)
args = parser.parse_args()
root = args.out_dir
root.mkdir(parents=True, exist_ok=True)
assert not (root/'bids.sqlite').exists(), 'retain completed evidence'
source=sqlite3.connect(f'file:{args.baseline.resolve()}?mode=ro',uri=True)
source.row_factory=sqlite3.Row
target=db.connect(root/'bids.sqlite');source.backup(target)
started=time.monotonic();db.init_db(target);resolve_procurements(target);target.commit()
buyers={r['slug']:r['id'] for r in target.execute('SELECT id,slug FROM buyer')}
fixtures=Path(__file__).resolve().parents[2]/'scrapers/tests/fixtures/agencies'
rows=json.loads((fixtures/'bids_tenders_trca_identity_2026-10-22.json').read_text())+json.loads((fixtures/'bids_tenders_zoo_2026-10-21.json').read_text())
assert store_listings(target,rows,buyers)==371
first=[tuple(r) for r in target.execute('SELECT * FROM procurement ORDER BY id')]
first_keys=[tuple(r) for r in target.execute('SELECT * FROM procurement_key ORDER BY namespace,value')]
assert store_listings(target,list(reversed(rows)),buyers)==371
assert first==[tuple(r) for r in target.execute('SELECT * FROM procurement ORDER BY id')]
assert first_keys==[tuple(r) for r in target.execute('SELECT * FROM procurement_key ORDER BY namespace,value')]
identity_seconds=round(time.monotonic()-started,3)
unchanged={}
for (table,) in source.execute("SELECT name FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence' ORDER BY name"):
    columns=[r[1] for r in source.execute(f'PRAGMA table_info({table})')]
    query='SELECT '+','.join('"'+c+'"' for c in columns)+f' FROM "{table}" ORDER BY rowid'
    digest=hashlib.sha256();count=0
    for before,after in zip_longest(source.execute(query),target.execute(query)):
        assert before is not None and after is not None and tuple(before)==tuple(after),table
        digest.update(json.dumps(tuple(before),ensure_ascii=False).encode());count+=1
    unchanged[table]={'rows':count,'sha256':digest.hexdigest()}
assert target.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
assert not target.execute('PRAGMA foreign_key_check').fetchall()
coverage={table:dict(target.execute(f'SELECT COUNT(*) AS rows,COUNT(procurement_id) AS identified FROM {table}').fetchone()) for table in FACT_TABLES}
for table in FACT_TABLES:
    assert any(r['from']=='procurement_id' and r['table']=='procurement' for r in target.execute(f'PRAGMA foreign_key_list({table})')),table
assert coverage['portal_listing']==dict(rows=371,identified=371)
assert target.execute('SELECT COUNT(DISTINCT procurement_id) FROM portal_listing').fetchone()[0]==371
assert not target.execute("SELECT namespace,procurement_id FROM procurement_key WHERE namespace IN ('city_document','city_workspace','city_call') GROUP BY namespace,procurement_id HAVING COUNT(*)>1").fetchall()
export_dir=root/'export'
export_json(target,export_dir/'bids.json','2026-10-22T00:00:00+00:00')
export_schema(target,export_dir/'schema.json','2026-10-22T00:00:00+00:00')
write_csv_zip(target,export_dir/'bids-csv.zip');write_parquet_files(target,export_dir)
exported=json.loads((export_dir/'bids.json').read_text())
assert sum(len(b['portal_listings']) for b in exported['buyers'])==371
report=dict(validated_at=__import__('datetime').datetime.now(__import__('datetime').UTC).isoformat(),
            integrity_check='ok',foreign_key_violations=0,identity_seconds=identity_seconds,
            active_identities=target.execute('SELECT COUNT(*) FROM procurement WHERE merged_into IS NULL').fetchone()[0],
            redirects=target.execute('SELECT COUNT(*) FROM procurement WHERE merged_into IS NOT NULL').fetchone()[0],
            source_rows_preserved=unchanged,identity_coverage=coverage,
            portal_records=371,portal_distinct_procurements=371,replay_ids_and_keys_unchanged=True,
            export_bytes=(export_dir/'bids.json').stat().st_size,
            exported_tables=len(db.EXPORT_TABLES),paid_calls=0,network_capture_requests=0,production_modified=False)
(root/'validation.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
