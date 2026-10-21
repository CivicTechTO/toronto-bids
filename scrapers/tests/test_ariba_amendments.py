"""Amendment publication must retain historical bytes and resolve current coverage (#256)."""
import zipfile

from toronto_bids.sources import ariba_files


class Source:
    def __init__(self, files, missing=()):
        self.files = files
        self.missing = missing

    def list_files(self):
        return [{'key':key,'name':name} for key,name,_ in self.files]

    def download(self, entry, dest):
        if entry['key'] in self.missing:
            raise RuntimeError('unavailable')
        dest.write_bytes(next(blob for key,_,blob in self.files if key==entry['key']))


def test_amended_retry_preserves_old_names_and_resolves_withdrawn_gap(tmp_path):
    doc='5861259648'
    target=ariba_files.capture_files(Source([
        ('old','drawings.pdf',b'old drawings'),('agreement','old agreement.pdf',b'agreement'),
    ],missing=['agreement']),doc,tmp_path)
    assert target
    result=ariba_files.capture_files(Source([
        ('new','revised drawings.pdf',b'new drawings'),('agreement2','new agreement.pdf',b'agreement'),
    ]),doc,tmp_path)
    assert result == target, 'a completed amendment must publish without losing older files'
    with zipfile.ZipFile(target) as z:
        assert z.read('drawings.pdf') == b'old drawings'
        assert z.read('revised drawings.pdf') == b'new drawings'
        assert z.read('new agreement.pdf') == b'agreement'
    assert not target.with_suffix('.omitted.json').exists()


def test_same_name_revision_preserves_previous_bytes(tmp_path):
    doc='5861259648'
    target=ariba_files.capture_files(Source([('a','agreement.pdf',b'old')]),doc,tmp_path)
    assert ariba_files.capture_files(Source([('a','agreement.pdf',b'new')]),doc,tmp_path)
    with zipfile.ZipFile(target) as z:
        contents=[z.read(info) for info in z.infolist() if not info.is_dir()]
    assert b'old' in contents and b'new' in contents


def provenance(path):
    from toronto_bids.sources.ariba_versions import read_provenance
    with zipfile.ZipFile(path) as archive:
        return read_provenance(archive)[0]


def test_revision_provenance_and_indexing_are_idempotent(tmp_path, conn):
    from toronto_bids.sources.ariba_attachments import store_bundle
    doc='5861259648'
    target=ariba_files.capture_files(Source([('a','agreement.pdf',b'old')]),doc,tmp_path)
    revised=Source([('a','agreement.pdf',b'new'),('b','agreement.pdf',b'other')])
    assert ariba_files.capture_files(revised,doc,tmp_path)
    first=provenance(target)
    assert len(first['files'])==3
    current={r['key']:r['path'] for r in first['listing']}
    assert current['a'].startswith('_versions/') and current['b']=='agreement_2.pdf'
    assert 'agreement.pdf' not in current.values()
    assert first['document_number']==doc
    assert ariba_files.capture_files(revised,doc,tmp_path)
    second=provenance(target)
    assert {r['path']:r['sha256'] for r in first['files']} == {
        r['path']:r['sha256'] for r in second['files']}
    assert store_bundle(conn,target,doc,tmp_path)==3
    assert conn.execute('SELECT count(*) FROM ariba_attachment').fetchone()[0]==3


def test_large_embedded_manifest_is_not_indexed_as_an_attachment(tmp_path,monkeypatch):
    from toronto_bids.sources import ariba_versions
    from toronto_bids.sources.zip_index import index_zip
    monkeypatch.setattr(ariba_versions,'COMMENT_LIMIT',1)
    target=ariba_files.capture_files(Source([('a','a.pdf',b'actual attachment')]),'5861259648',tmp_path)
    with zipfile.ZipFile(target) as z:
        manifest,member=ariba_versions.read_provenance(z)
        assert member and manifest['listing'][0]['key']=='a'
    assert [r['path'] for r in index_zip(target,strict=True)]==['a.pdf']
    assert ariba_files.capture_files(Source([('a','a.pdf',b'revised')]),'5861259648',tmp_path)
    assert len(index_zip(target,strict=True))==2


def test_legacy_archive_retains_unknown_provenance_without_guessing(tmp_path):
    target=tmp_path/'Doc5861259648.zip'
    with zipfile.ZipFile(target,'w') as z:
        z.writestr('old.pdf',b'old')
    ariba_files.capture_files(Source([('new','new.pdf',b'new')]),'5861259648',tmp_path)
    data=provenance(target)
    old=next(r for r in data['files'] if r['path']=='old.pdf')
    assert old['first_observed'] is None and old['source_keys']==[]
    assert data['listing'][0]['path']=='new.pdf'


def test_failed_atomic_swap_retains_zip_provenance_gap_and_partial_bytes(tmp_path,monkeypatch):
    import pytest
    doc='5861259648'
    target=ariba_files.capture_files(Source([('a','a.pdf',b'old'),('b','b.pdf',b'b')],missing=['b']),doc,tmp_path)
    before=target.read_bytes();gap=target.with_suffix('.omitted.json');old_gap=gap.read_bytes()
    replace=ariba_files.os.replace
    def fail(src,dst):
        if dst==target:raise OSError('disk replacement failed')
        return replace(src,dst)
    monkeypatch.setattr(ariba_files.os,'replace',fail)
    with pytest.raises(OSError,match='replacement failed'):
        ariba_files.capture_files(Source([('a','a.pdf',b'new'),('b','b.pdf',b'b')]),doc,tmp_path)
    assert target.read_bytes()==before and gap.read_bytes()==old_gap
    partial=ariba_files.partial_dir(tmp_path,doc)/'files'
    assert (partial/'a.pdf').read_bytes()==b'new' and (partial/'b.pdf').read_bytes()==b'b'


def test_tampered_existing_provenance_cannot_authorize_replacement(tmp_path):
    import pytest
    doc='5861259648'
    target=ariba_files.capture_files(Source([('a','a.pdf',b'old')]),doc,tmp_path)
    from toronto_bids.sources.ariba_versions import write_provenance
    data=provenance(target);data['files'][0]['sha256']='0'*64
    write_provenance(target,data)
    before=target.read_bytes()
    with pytest.raises(ValueError,match='hash disagrees'):
        ariba_files.capture_files(Source([('a','a.pdf',b'new')]),doc,tmp_path)
    assert target.read_bytes()==before


def test_current_gap_cannot_be_filled_by_a_historical_version(tmp_path):
    from toronto_bids.sources.ariba_attachments import bundle_complete
    doc='5861259648'
    target=ariba_files.capture_files(Source([('a','a.pdf',b'old')]),doc,tmp_path)
    ariba_files.capture_files(Source([('a','a.pdf',b'new'),('b','b.pdf',b'b')],missing=['a']),doc,tmp_path)
    data=provenance(target)
    assert data['listing'][0]['path'] is None
    assert not bundle_complete(tmp_path,doc)
    target.with_suffix('.omitted.json').unlink()
    assert not bundle_complete(tmp_path,doc), 'embedded missing-file evidence must also keep it pending'


def test_recorded_collision_stays_unresolved_after_an_amendment(tmp_path):
    from toronto_bids.sources.ariba_attachments import bundle_complete
    doc='5861259648'
    target=ariba_files.capture_files(Source([('a','a.pdf',b'a')]),doc,tmp_path)
    ariba_files.write_omitted(target,[],collided=1)
    ariba_files.capture_files(Source([('b','b.pdf',b'b')]),doc,tmp_path)
    assert not bundle_complete(tmp_path,doc)
    assert ariba_files.read_omitted(target)['collided']==1


def test_interrupted_old_listing_bytes_survive_an_amendment(tmp_path):
    import pytest
    doc='5861259648'
    class Interrupted(Source):
        def download(self,entry,dest):
            if entry['key']=='b':raise KeyboardInterrupt()
            return super().download(entry,dest)
    with pytest.raises(KeyboardInterrupt):
        ariba_files.capture_files(Interrupted([('a','a.pdf',b'old partial'),('b','b.pdf',b'b')]),doc,tmp_path)
    target=ariba_files.capture_files(Source([('new','new.pdf',b'new')]),doc,tmp_path)
    data=provenance(target)
    assert len(data['listing'])==1 and data['listing'][0]['name']=='new.pdf'
    with zipfile.ZipFile(target) as z:
        assert any(z.read(i)==b'old partial' for i in z.namelist())
    historical=next(r for r in data['files'] if r.get('origin')=='retired_partial')
    assert historical['source_keys']==['a']


def test_reverting_to_an_existing_version_creates_no_duplicate_bytes(tmp_path):
    doc='5861259648'
    target=ariba_files.capture_files(Source([('a','a.pdf',b'old')]),doc,tmp_path)
    ariba_files.capture_files(Source([('a','a.pdf',b'new')]),doc,tmp_path)
    ariba_files.capture_files(Source([('a','a.pdf',b'old')]),doc,tmp_path)
    data=provenance(target)
    assert len(data['files'])==2
    assert data['listing'][0]['path']=='a.pdf'


def test_wrong_shape_manifest_preserves_unknown_bytes_without_adopting_them(tmp_path):
    import json
    doc='5861259648';pdir=ariba_files.partial_dir(tmp_path,doc)
    (pdir/'files').mkdir(parents=True)
    (pdir/'files'/'a.pdf').write_bytes(b'unverified prior bytes')
    (pdir/'manifest.json').write_text(json.dumps({'fingerprint':{'files':[17]}}))
    target=ariba_files.capture_files(Source([('a','a.pdf',b'fresh')]),doc,tmp_path)
    with zipfile.ZipFile(target) as z:
        assert z.read('a.pdf')==b'fresh'
        assert any(z.read(i)==b'unverified prior bytes' for i in z.namelist())
    old=next(r for r in provenance(target)['files'] if r.get('origin')=='retired_partial')
    assert old['source_keys']==[]
