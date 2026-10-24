"""Independent public TTC reports: bytes/text only; shared extraction is opt-in.

A reviewed seed inventory, not an asserted exhaustive archive. No MERX paywall
content and no automatic bridge from TTC contract labels to MERX pages.
"""
import hashlib
import json
import subprocess
import time
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from urllib.parse import urlsplit

from toronto_bids import config
from toronto_bids.buyers import seed_buyers
from toronto_bids.models import BackgroundPdf
from toronto_bids.sources.merx import PublicSession
from toronto_bids.store import db

HOSTS = ('www.ttc.ca', 'cdn.ttc.ca', 'pw.ttc.ca')


def capture_reports(conn, *, inventory=None, client=None, data_dir=None, sleep=time.sleep,
                    clock=lambda: datetime.now(timezone.utc)):
    inventory = inventory if inventory is not None else json.loads(
        resources.files('toronto_bids.data').joinpath('ttc_reports.json').read_text())
    for item in inventory:
        p = urlsplit(item['url'])
        if (p.scheme != 'https' or p.netloc not in HOSTS or p.fragment or
                not p.path.lower().endswith('.pdf') or not item.get('basis')):
            raise ValueError('TTC inventory requires reviewed public TTC PDF URLs and basis')
    seed_buyers(conn)
    root = Path(data_dir or config.DATA_DIR) / 'agencies' / 'ttc_reports'
    session = PublicSession(root, client, sleep=sleep, clock=clock,
                            max_requests=max(1, len(inventory) * 4), allowed_hosts=HOSTS)
    report = dict(inventory=len(inventory), captured=0, held=0, failures=[], exhaustive=False)
    try:
        for item in inventory:
            url = item['url']
            row = conn.execute('SELECT local_path,sha256 FROM background_pdf WHERE url=?', (url,)).fetchone()
            if row and row['local_path'] and row['sha256']:
                path = Path(row['local_path'])
                if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == row['sha256']:
                    report['held'] += 1
                    continue
            # Index the retry queue before attempting bytes. A failed download is never
            # recorded as a successful/empty source.
            db.upsert_row(conn, BackgroundPdf(url=url, kind='ttc_board'), overwrite=False)
            conn.commit()
            try:
                response = session.get(url)
                data = response.content
                if not data.startswith(b'%PDF-') or b'%%EOF' not in data[-4096:]:
                    raise ValueError('TTC response is not a complete PDF')
                digest = hashlib.sha256(data).hexdigest()
                path = root / (digest + '.pdf')
                path.write_bytes(data)
                proc = subprocess.run(['pdftotext', '-layout', str(path), '-'],
                                      capture_output=True, text=True, timeout=60, check=True)
                if not proc.stdout.strip():
                    raise ValueError('TTC report has no extractable text; OCR not attempted')
                db.upsert_row(conn, BackgroundPdf(url=url, kind='ttc_board', local_path=str(path),
                    sha256=digest, text=proc.stdout.strip()), overwrite=True)
                conn.commit()
                report['captured'] += 1
            except Exception as exc:
                report['failures'].append(dict(url=url, error=str(exc)))
                # Respect denials/deadlines across the whole run, not just one PDF.
                break
    finally:
        (root / 'inventory.json').write_text(json.dumps(inventory, indent=2) + '\n')
        (root / 'capture.json').write_text(json.dumps(report, indent=2) + '\n')
        session.close()
    return report
