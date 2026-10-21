"""Versioned Ariba bundle contents and provenance, carried atomically in the ZIP (#256)."""
import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

FORMAT = 'toronto-bids-ariba-versions'
COMMENT_LIMIT = 65535


def read_provenance(archive):
    """Return (manifest, metadata member), or (None, None) for a legacy/source ZIP."""
    try:
        header = json.loads(archive.comment)
    except (ValueError, UnicodeDecodeError):
        return None, None
    if not isinstance(header, dict) or header.get('format') != FORMAT:
        return None, None
    member = header.get('manifest_member')
    data = json.loads(archive.read(member)) if member else header
    if (not isinstance(data,dict) or data.get('format') != FORMAT or data.get('version') != 1
            or not isinstance(data.get('files'), list) or not isinstance(data.get('listing'), list)):
        raise ValueError('Invalid Ariba version provenance')
    paths = set()
    for item in data['files']:
        if (not isinstance(item, dict) or not isinstance(item.get('path'), str)
                or item['path'] in paths or not re.fullmatch('[0-9a-f]{64}', item.get('sha256',''))):
            raise ValueError('Invalid/duplicate Ariba version entry')
        paths.add(item['path'])
    names = set()
    for item in data['listing']:
        if (not isinstance(item,dict) or any(not isinstance(item.get(k),str) or not item[k]
                for k in ('key','name','archive_name')) or item['archive_name'] in names
                or (item.get('path') is not None and item['path'] not in paths)):
            raise ValueError('Invalid current Ariba listing provenance')
        names.add(item['archive_name'])
    members = {i.filename for i in archive.infolist() if not i.is_dir() and i.filename != member}
    if paths != members:
        raise ValueError('Ariba provenance does not match archived files')
    return data, member


def write_provenance(path, data):
    """Small manifests live in the ZIP comment; larger ones use a marked metadata member."""
    blob = json.dumps(data, sort_keys=True, separators=(',',':')).encode()
    with zipfile.ZipFile(path, 'a', compression=zipfile.ZIP_DEFLATED) as archive:
        if len(blob) <= COMMENT_LIMIT:
            archive.comment = blob
        else:
            member = '_toronto_bids_archive/manifest.json'
            while member in archive.namelist():
                member = '_' + member
            archive.writestr(member, blob)
            archive.comment = json.dumps({'format':FORMAT,'manifest_member':member}).encode()


def _hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def merge_capture(captured, target, listing, *, retired=()):
    """Stage old bytes plus current versions; filenames never establish content equivalence.

    Existing archive paths remain stable. Conflicting new content gets a hash-qualified path.
    Legacy files retain unknown source keys/times rather than inventing historical evidence.
    Temporary extraction uses numeric local filenames, never untrusted archive paths.
    """
    now = datetime.now(UTC).isoformat()
    files, records, old_hashes = {}, {}, {}
    with tempfile.TemporaryDirectory(prefix='.ariba-versions-', dir=target.parent) as work:
        if target.exists():
            with zipfile.ZipFile(target) as archive:
                previous, metadata_member = read_provenance(archive)
                if previous and previous['document_number'] != target.stem.removeprefix('Doc'):
                    raise ValueError('Version provenance belongs to a different Ariba event')
                previous_files = {item['path']:item for item in previous['files']} if previous else {}
                for index, info in enumerate(archive.infolist()):
                    if info.is_dir() or info.filename == metadata_member:
                        continue
                    if info.filename in files:
                        raise ValueError('Duplicate archived member names prevent safe version merge')
                    disk = Path(work) / str(index)
                    with archive.open(info) as source, disk.open('wb') as dest:
                        shutil.copyfileobj(source, dest, 1 << 20)
                    sha = _hash(disk)
                    record = dict(previous_files.get(info.filename) or {
                        'path':info.filename, 'name':info.filename, 'source_keys':[],
                        'first_observed':None, 'last_observed':None, 'sha256':sha,
                    })
                    if record['sha256'] != sha:
                        raise ValueError(f'Archived hash disagrees with provenance: {info.filename}')
                    files[info.filename] = disk
                    records[info.filename] = record
                    old_hashes[info.filename] = sha
        for disk, name, key in retired:
            sha = _hash(disk)
            path = f'_versions/{sha}/{name}'
            while path in records and records[path]['sha256'] != sha:
                path = '_' + path
            if path not in records:
                records[path] = {'path':path,'name':name,'sha256':sha,'source_keys':[],
                                 'first_observed':None,'last_observed':None,
                                 'origin':'retired_partial'}
                files[path] = Path(disk)
            if key is not None:
                records[path]['source_keys'] = list(dict.fromkeys([*records[path]['source_keys'],key]))
        planned = {item['archive_name']:item for item in listing}
        if len(planned) != len(listing):
            raise ValueError('Current listing archive names must be unique')
        current_paths = {}
        for disk, name in captured:
            if name not in planned:
                raise ValueError(f'Captured file absent from verified listing: {name}')
            sha = _hash(disk)
            path = name
            if path in records and records[path]['sha256'] != sha:
                path = f'_versions/{sha}/{name}'
                while path in records and records[path]['sha256'] != sha:
                    path = '_' + path
            if path not in records:
                records[path] = {'path':path,'name':name,'sha256':sha,'source_keys':[],
                                 'first_observed':now,'last_observed':now}
            key = planned[name]['key']
            record = records[path]
            record['source_keys'] = list(dict.fromkeys([*record['source_keys'],key]))
            record['last_observed'] = now
            files[path] = Path(disk)
            current_paths[name] = path
        data = {'format':FORMAT,'version':1,'document_number':target.stem.removeprefix('Doc'),
                'observed_at':now,'files':list(records.values()),
                'listing':[{**item,'path':current_paths.get(item['archive_name'])} for item in listing]}
        yield list((disk,path) for path,disk in files.items()), data, old_hashes


def verify_preserved(candidate, old_hashes):
    """Re-read the candidate: every old outer member must survive byte for byte."""
    with zipfile.ZipFile(candidate) as archive:
        manifest, _ = read_provenance(archive)
        if manifest is None:
            raise ValueError('Candidate has no version provenance')
        expected = {item['path']:item['sha256'] for item in manifest['files']}
        for path, sha in old_hashes.items():
            if expected.get(path) != sha:
                raise ValueError(f'Candidate would lose historical version {path}')
        for info in archive.infolist():
            if info.filename not in expected:
                continue
            digest = hashlib.sha256()
            with archive.open(info) as stream:
                for chunk in iter(lambda: stream.read(1 << 20), b''):
                    digest.update(chunk)
            if digest.hexdigest() != expected[info.filename]:
                raise ValueError(f'Candidate content hash mismatch: {info.filename}')
