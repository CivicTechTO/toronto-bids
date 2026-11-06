"""Derived text for image-only PDFs; original captures and prompt/cache keys stay intact.

Native text remains the first choice. OCR is a bounded local preparation step, not
an extractor of bid facts. Its page words retain coordinates for source review.
"""
import csv
from functools import lru_cache
import gzip
import hashlib
import io
from importlib import resources
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

_MAX_PAGES = 40
_DPI = 300


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


@lru_cache(maxsize=1)
def _bundled():
    path=resources.files('toronto_bids.data').joinpath('city_image_pdf_text.json.gz')
    if not path.is_file():return {}
    return {r['sha256']:r for r in json.loads(gzip.decompress(path.read_bytes()))}


def record_for(conn, sha256):
    """Read accepted or shipped review evidence without altering the archive."""
    return conn.execute('SELECT * FROM pdf_text WHERE sha256=?',(sha256,)).fetchone() or _bundled().get(sha256)


def persist_record(conn, record):
    """Preserve first acceptance; a conflicting derivative requires explicit repair."""
    columns=('sha256','method','text','text_sha256','provenance_json','proof_sha256')
    conn.execute('INSERT OR IGNORE INTO pdf_text ('+','.join(columns)+') VALUES (?,?,?,?,?,?)',
                 tuple(record[k] for k in columns))
    stored=conn.execute('SELECT * FROM pdf_text WHERE sha256=?',(record['sha256'],)).fetchone()
    if any(stored[k]!=record[k] for k in columns):
        raise ValueError('Accepted derived text conflicts with reviewed evidence')


def _run(args, *, timeout=180):
    try:
        return subprocess.run(args, check=True, capture_output=True, text=True, timeout=timeout,
                              env={**os.environ, 'OMP_THREAD_LIMIT': '1'}).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f'PDF text preparation failed ({Path(args[0]).name}): {exc}') from exc


def read_pdf(path):
    """Return complete native or OCR text with provenance, or fail without partial output."""
    path = Path(path)
    native = _run(['pdftotext', '-layout', str(path), '-']).strip()
    if native:
        return dict(text=native, method='pdftotext', provenance=dict(version=1, layout=True))
    info = _run(['pdfinfo', str(path)])
    pages = re.search(r'^Pages:\s+(\d+)\s*$', info, re.M)
    if not pages or not 1 <= int(pages[1]) <= _MAX_PAGES:
        raise ValueError(f'Image-only PDF exceeds the {_MAX_PAGES}-page OCR bound or has no pages')
    version = _run(['tesseract', '--version']).splitlines()
    if not version:raise ValueError('Tesseract did not report its version')
    engine = version[0]
    languages = _run(['tesseract', '--list-langs'])
    data_dir = re.search(r'"([^"]+)"', languages)
    trained = Path(data_dir[1])/'eng.traineddata' if data_dir else None
    if trained is None or not trained.is_file():
        raise ValueError('English Tesseract language data could not be verified')
    provenance = dict(version=1, engine=engine, language='eng', dpi=_DPI, psm=6,
                      preserve_interword_spaces=True,
                      language_sha256=hashlib.sha256(trained.read_bytes()).hexdigest(), pages=[])
    texts = []
    with tempfile.TemporaryDirectory(prefix='tb-ocr-') as directory:
        base = Path(directory)/'page'
        for page in range(1, int(pages[1])+1):
            _run(['pdftoppm', '-f', str(page), '-l', str(page), '-singlefile', '-r', str(_DPI),
                  '-png', str(path), str(base)])
            _run(['tesseract', str(base.with_suffix('.png')), str(base), '-l', 'eng', '--psm', '6',
                  '-c', 'preserve_interword_spaces=1', 'txt', 'tsv'])
            try:
                text = base.with_suffix('.txt').read_text().strip()
                tsv = base.with_suffix('.tsv').read_text()
            except OSError as exc:
                raise ValueError(f'OCR page {page} did not produce complete text/word output') from exc
            words = []
            for word in csv.DictReader(io.StringIO(tsv), delimiter='\t'):
                if word['level'] != '5' or not word['text'].strip():
                    continue
                x, y, width, height = (int(word[k]) for k in ('left', 'top', 'width', 'height'))
                words.append(dict(text=word['text'], box=[x, y, x+width, y+height],
                                  confidence=float(word['conf'])))
            if text and not words:raise ValueError(f'OCR page {page} has text but no word geometry')
            provenance['pages'].append(dict(page=page, text=text, text_sha256=_digest(text), words=words))
            texts.append(text)
            # Each page is rendered/recognized serially; temporary images are bounded.
            for suffix in ('.png', '.txt', '.tsv'):
                base.with_suffix(suffix).unlink()
    text = '\n\f\n'.join(texts)
    if not text.strip():
        raise ValueError('OCR completed but the PDF still has no readable text')
    return dict(text=text, method='tesseract', provenance=provenance)


def source_text(conn, row, *, recover=False):
    """Resolve immutable derived text; optionally prepare a held textless original.

    A missing local file is an ordinary portable-archive gap. A present but changed
    original, failed OCR or changed accepted text is a visible failure. Callers own
    transactions; no procurement facts or raw capture fields are changed here.
    """
    sha = row['sha256']
    stored = record_for(conn,sha)
    if stored:
        proof = dict(sha256=sha, method=stored['method'], text_sha256=stored['text_sha256'],
                     provenance=json.loads(stored['provenance_json']))
        if (_digest(stored['text']) != stored['text_sha256'] or
                _digest(_json(proof)) != stored['proof_sha256']):
            raise ValueError('Accepted derived text/provenance changed; explicit review required')
        if row['text'] and row['text'].strip() and row['text'] != stored['text']:
            raise ValueError('Native text now conflicts with accepted derived text; review required')
        if recover and not conn.execute('SELECT 1 FROM pdf_text WHERE sha256=?',(sha,)).fetchone():
            path=Path(row['local_path']) if row['local_path'] else None
            if path is None or not path.is_file():return None
            if hashlib.sha256(path.read_bytes()).hexdigest()!=sha:
                raise ValueError('Held PDF bytes changed before accepting reviewed text')
            persist_record(conn,stored)
        return stored['text']
    if row['text'] and row['text'].strip():
        return row['text']
    if not recover or not sha or 'local_path' not in row.keys() or not row['local_path']:
        return None
    path = Path(row['local_path'])
    if not path.is_file():
        return None
    if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
        raise ValueError('Held PDF bytes changed before text recovery')
    result = read_pdf(path)
    if not result['text'].strip():
        raise ValueError('PDF text preparation returned empty text')
    # Detect a concurrent file replacement even if the renderer succeeded.
    if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
        raise ValueError('Held PDF bytes changed during text recovery')
    proof = dict(sha256=sha, method=result['method'], text_sha256=_digest(result['text']),
                 provenance=result['provenance'])
    conn.execute('INSERT INTO pdf_text (sha256,method,text,text_sha256,provenance_json,proof_sha256) '
                 'VALUES (?,?,?,?,?,?)', (sha, result['method'], result['text'], proof['text_sha256'],
                                         _json(result['provenance']), _digest(_json(proof))))
    return result['text']
