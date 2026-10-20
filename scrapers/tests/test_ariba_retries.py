"""Incomplete-bundle retries through real capture selection, publication and indexing."""
import json
from types import SimpleNamespace

import pytest

from toronto_bids.sources import ariba_attachments as aa, ariba_files

DOC = '5909867893'
EVENT = {'rfx_id': '1110027542', 'document_number': DOC}


class Source:
    def __init__(self, names=('a.pdf','b.pdf','c.pdf'), fail=()):
        self.names, self.fail = names, fail

    def list_files(self):
        return [{'name':n,'key':n} for n in self.names]

    def download(self, file, dest):
        if file['name'] in self.fail:
            raise RuntimeError('download unavailable')
        dest.write_bytes(file['name'].encode())


@pytest.fixture
def retry(conn, tmp_path, monkeypatch):
    import playwright.sync_api
    browser = SimpleNamespace(new_context=lambda **k:browser, new_page=lambda:object(), close=lambda:None)
    class PW:
        chromium = SimpleNamespace(launch=lambda **k:browser)
        def __enter__(self):return self
        def __exit__(self,*a):pass
    monkeypatch.setattr(playwright.sync_api,'sync_playwright',PW)
    monkeypatch.setattr(aa.config,'ARIBA_USERNAME','offline-user')
    monkeypatch.setattr(aa.config,'ARIBA_PASSWORD','offline-password')
    monkeypatch.setattr(aa,'login',lambda *a,**k:None)
    monkeypatch.setattr(aa,'open_solicitation_events',lambda c:[EVENT])
    seen=[]
    def run(action):
        def capture(page,event,dest_dir,log):
            seen.append(event['document_number'])
            return action(log)
        monkeypatch.setattr(aa,'capture_event',capture)
        failures=[]
        count=aa.capture_attachments(conn,dest_dir=tmp_path,failures=failures)
        return count,failures
    def seed(fail=('b.pdf',)):
        path=ariba_files.capture_files(Source(fail=fail),DOC,tmp_path)
        aa.store_bundle(conn,path,DOC,tmp_path)
        return path
    return run,seed,seen


def test_complete_archives_stay_skipped(retry):
    run,seed,seen=retry
    seed(fail=())
    assert run(lambda log:pytest.fail('complete bundle retried'))==(0,[])
    assert seen==[]


def test_partial_retries_resolve_gaps_and_rebuild_the_index(retry,conn,tmp_path):
    run,seed,seen=retry
    path=seed()
    assert conn.execute('select count(*) from ariba_attachment').fetchone()[0]==2
    count,failures=run(lambda log:ariba_files.capture_files(Source(),DOC,tmp_path,log=log))
    assert count==1 and not failures
    assert seen==[DOC]
    assert not path.with_suffix('.omitted.json').exists()
    assert conn.execute('select count(*) from ariba_attachment').fetchone()[0]==3
    assert run(lambda log:pytest.fail('resolved bundle retried'))==(0,[])


@pytest.mark.parametrize('outcome',['error','closed','denied','poorer','swap-gap'])
def test_failed_or_poorer_retries_keep_archive_gap_and_index(retry,conn,tmp_path,outcome):
    run,seed,seen=retry
    path=seed();gap=path.with_suffix('.omitted.json')
    before,old_gap=path.read_bytes(),gap.read_bytes()
    def action(log):
        if outcome=='error':raise RuntimeError('Sourcing did not load')
        if outcome in ('closed','denied'):
            log('Respond disabled (closed)' if outcome=='closed' else 'no permission to view the event')
            return None
        fail=('b.pdf','c.pdf') if outcome=='poorer' else ('c.pdf',)
        return ariba_files.capture_files(Source(fail=fail),DOC,tmp_path,log=log)
    count,failures=run(action)
    assert seen==[DOC]
    assert count==0
    assert failures and failures[0][0]=='ariba:Doc'+DOC
    assert path.read_bytes()==before and gap.read_bytes()==old_gap
    assert conn.execute('select count(*) from ariba_attachment').fetchone()[0]==2


def test_a_changed_listing_does_not_erase_a_previous_omission(retry,tmp_path):
    run,seed,seen=retry
    path=seed()
    count,failures=run(lambda log:ariba_files.capture_files(
        Source(names=('a.pdf','c.pdf','new.pdf')),DOC,tmp_path,log=log))
    assert count==1 and failures
    assert json.loads(path.with_suffix('.omitted.json').read_text())['omitted']==['b.pdf']
    assert run(lambda log:None)[1]  # remains eligible and visibly unresolved


def test_corrupt_omission_evidence_is_not_silently_cleared(retry,tmp_path):
    run,seed,seen=retry
    path=seed();gap=path.with_suffix('.omitted.json');gap.write_text('invalid json')
    before=path.read_bytes()
    count,failures=run(lambda log:ariba_files.capture_files(Source(),DOC,tmp_path,log=log))
    assert count==0 and failures and seen==[DOC]
    assert path.read_bytes()==before and gap.read_text()=='invalid json'


def test_partial_archive_stays_in_the_near_close_warning(retry,monkeypatch,tmp_path,conn):
    run,seed,seen=retry
    seed()
    monkeypatch.setattr(aa,'open_solicitation_events',lambda c,**k:[{**EVENT,'closes':'2026-10-30'}])
    assert aa.near_close_uncaptured(conn,dest_dir=tmp_path,today='2026-10-29')


def test_failed_atomic_replacement_restores_the_held_gap_record(retry,tmp_path,monkeypatch):
    run,seed,seen=retry
    path=seed();gap=path.with_suffix('.omitted.json')
    gap.write_text(json.dumps({'omitted':['b.pdf','d.pdf']}))
    before,old_gap=path.read_bytes(),gap.read_bytes()
    replace=ariba_files.os.replace
    def fail_publish(src,dst):
        if dst==path:raise OSError('replacement failed')
        return replace(src,dst)
    monkeypatch.setattr(ariba_files.os,'replace',fail_publish)
    count,failures=run(lambda log:ariba_files.capture_files(
        Source(names=('a.pdf','b.pdf','c.pdf','d.pdf'),fail=('d.pdf',)),DOC,tmp_path,log=log))
    assert count==0 and failures
    assert path.read_bytes()==before and gap.read_bytes()==old_gap


def test_a_first_partial_capture_is_reported_as_incomplete(retry,tmp_path,conn):
    run,seed,seen=retry
    count,failures=run(lambda log:ariba_files.capture_files(Source(fail=('b.pdf',)),DOC,tmp_path,log=log))
    assert count==1 and failures and 'b.pdf' in failures[0][1]
    assert conn.execute('select count(*) from ariba_attachment').fetchone()[0]==2


def test_improving_partial_capture_retires_only_resolved_gaps(retry,tmp_path):
    run,seed,seen=retry
    path=seed()
    path.with_suffix('.omitted.json').write_text(json.dumps({'omitted':['b.pdf','d.pdf']}))
    count,failures=run(lambda log:ariba_files.capture_files(
        Source(names=('a.pdf','b.pdf','c.pdf','d.pdf'),fail=('d.pdf',)),DOC,tmp_path,log=log))
    assert count==1 and failures
    assert json.loads(path.with_suffix('.omitted.json').read_text())['omitted']==['d.pdf']
    count,failures=run(lambda log:ariba_files.capture_files(
        Source(names=('a.pdf','b.pdf','c.pdf','d.pdf')),DOC,tmp_path,log=log))
    assert count==1 and not failures
    assert not path.with_suffix('.omitted.json').exists()


def test_collision_evidence_stays_visible_until_identity_is_audited(retry,tmp_path):
    run,seed,seen=retry
    path=seed()
    path.with_suffix('.omitted.json').write_text(json.dumps({'omitted':[],'collided':1}))
    count,failures=run(lambda log:ariba_files.capture_files(Source(),DOC,tmp_path,log=log))
    assert count==1 and failures and '1 collapsed link' in failures[0][1]
    assert not aa.bundle_complete(tmp_path,DOC)
