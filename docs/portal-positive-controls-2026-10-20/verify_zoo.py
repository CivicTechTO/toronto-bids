"""One-off #226 public metadata verification. Run only in the Zoo grant's off-peak window.

Set TB_DATA_DIR to an isolated directory. No login, documents, extraction or publishing.
"""
import json
import os
import re
from datetime import datetime, timezone

from toronto_bids import config
from toronto_bids.sources.bids_tenders import run_portal_capture
from toronto_bids.store import db


def main():
    if not os.environ.get('TB_DATA_DIR'):
        raise RuntimeError('Set TB_DATA_DIR to an isolated verification directory')
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = db.connect(config.DB_PATH)
    db.init_db(conn)
    result = run_portal_capture(conn, only={'toronto-zoo'}, log=lambda m: print(m, flush=True))
    snapshots = [json.loads(p.read_text()) for p in config.PORTAL_RECORDINGS_DIR.glob('*.json')]
    controls = {}
    for label, pattern in {'RFQ-50': r'^RFQ[- ]?50(?:\b|[-_])',
                           'RFP-03': r'^RFP[- ]?03(?:\b|[-_])'}.items():
        controls[label] = [{key: row.get(key) for key in ('Id', 'Title', 'Status', 'DateClosing')}
                           for row in snapshots if re.search(pattern, row.get('Title', ''), re.I)]
    manifest = {
        'captured_at': datetime.now(timezone.utc).isoformat(),
        'result': result,
        'raw_snapshots': len(snapshots),
        'unique_listing_ids': len({r.get('Id') for r in snapshots}),
        'solicitations': conn.execute('SELECT count(*) FROM agency_solicitation').fetchone()[0],
        'control_candidates': controls,
    }
    conn.close()
    path = config.DATA_DIR / 'verification.json'
    path.write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest), flush=True)
    if any(isinstance(v, str) and v.startswith('FAILED') for v in result.values()):
        return 1
    return 0 if snapshots and all(controls.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
