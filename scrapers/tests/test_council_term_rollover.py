"""Evidence-gated automatic council-term rollover (#225)."""
from contextlib import contextmanager
import pytest
from toronto_bids.sources import bid_award_panel as bap

TERMS=[('EP',2023,'2022-2026',1)]
MISSING='<html>This meeting is not available</html>'


def site(pages,calls=None):
    def fetch(ref):
        if calls is not None:calls.append(ref)
        date=pages.get(ref)
        return f'<html>Meeting Date: Wednesday, {date}</html>' if date else MISSING
    return fetch


def discover(fetch,**kwargs):
    return bap.discover_meetings(fetch,term_starts=TERMS,auto_terms=True,
                                 max_per_term=10,stop_after_misses=2,**kwargs)


def test_first_successor_agenda_unlocks_the_next_term():
    closures=[]
    found=discover(site({'2023.EP1':'January 4, 2023','2023.EP2':'February 1, 2023',
                         '2027.EP1':'January 6, 2027','2027.EP2':'February 3, 2027'}),
                    today='2027-03-01',on_term_closed=lambda k,n:closures.append((k,n)))
    assert {'2027.EP1','2027.EP2'} <= found.keys()
    assert closures[0][0]=='EP2022-2026' and closures[0][1]['last_n']==2
    assert all(k!='EP2026-2030' for k,n in closures)
    assert TERMS==[('EP',2023,'2022-2026',1)]


def test_missing_successor_keeps_old_tail_live_despite_a_stale_closed_cache():
    closures=[]
    found=discover(site({'2023.EP1':'January 4, 2023','2023.EP2':'February 1, 2023'}),
                    today='2026-10-20',closed_terms={'EP2022-2026':1},
                    on_term_closed=lambda k,n:closures.append((k,n)))
    assert '2023.EP2' in found and not closures


def test_prepublished_future_agenda_does_not_freeze_the_current_term():
    closures=[]
    found=discover(site({'2023.EP1':'January 4, 2023','2023.EP2':'February 1, 2023',
                         '2027.EP1':'January 6, 2027'}),today='2026-10-20',
                    on_term_closed=lambda k,n:closures.append((k,n)))
    assert '2027.EP1' in found and '2023.EP2' in found
    assert not closures


def test_successor_date_must_belong_to_the_successor_term():
    closures=[]
    found=discover(site({'2023.EP1':'January 4, 2023','2027.EP1':'January 9, 2019'}),
                    today='2027-03-01',on_term_closed=lambda k,n:closures.append((k,n)))
    assert '2027.EP1' not in found and not closures


def test_an_unrecognized_response_cannot_confirm_a_new_term():
    valid=site({'2023.EP1':'January 4, 2023'})
    def fetch(ref):return '<html>Access denied</html>' if ref=='2027.EP1' else valid(ref)
    closures=[]
    with pytest.raises(RuntimeError,match='agenda'):
        discover(fetch,today='2026-10-20',on_term_closed=lambda k,n:closures.append((k,n)))
    assert not closures


def test_scrape_revalidates_legacy_caps_then_persists_successor_evidence(tmp_path,monkeypatch):
    (tmp_path/'.closed_terms.json').write_text('{"EP2022-2026":1}')
    pages={'2023.EP1':'January 4, 2023','2023.EP2':'February 1, 2023','2027.EP1':'January 6, 2027'}
    calls=[]
    @contextmanager
    def fetcher(**kwargs):yield site(pages,calls)
    monkeypatch.setattr(bap,'agenda_fetcher',fetcher)
    found=bap.scrape_agendas(tmp_path,term_starts=TERMS,auto_terms=True,today='2027-03-01')
    assert '2023.EP2' in found
    proof=bap._load_closed_terms(tmp_path)['EP2022-2026']
    assert proof['last_n']==2 and proof['successor']=='2027.EP1'
    calls.clear()
    bap.scrape_agendas(tmp_path,term_starts=TERMS,auto_terms=True,today='2027-03-01')
    assert not any(r.startswith(('2023.','2024.')) for r in calls)


@pytest.mark.parametrize('module,fn',[('ep_board','scrape_ep_agendas'),('zoo_board','scrape_zb_agendas')])
def test_active_body_wrappers_enable_automatic_terms(monkeypatch,module,fn):
    import importlib
    body=importlib.import_module('toronto_bids.sources.'+module)
    calls=[]
    monkeypatch.setattr(body,'scrape_agendas',lambda *a,**k:calls.append(k) or {})
    getattr(body,fn)()
    assert calls[0]['auto_terms'] is True


def test_adding_an_unconfirmed_configured_term_does_not_close_the_previous_one():
    closures=[]
    found=bap.discover_meetings(site({'2023.EP1':'January 4, 2023','2023.EP2':'February 1, 2023'}),
        term_starts=[*TERMS,('EP',2027,'2026-2030',1)],auto_terms=True,today='2026-10-20',
        max_per_term=5,stop_after_misses=2,closed_terms={'EP2022-2026':1},
        on_term_closed=lambda k,n:closures.append((k,n)))
    assert '2023.EP2' in found and not closures


def test_rollover_continues_past_the_first_generated_term():
    found=discover(site({'2023.EP1':'January 4, 2023','2027.EP1':'January 6, 2027',
                         '2031.EP1':'January 8, 2031'}),today='2031-03-01')
    assert '2027.EP1' in found and '2031.EP1' in found


def test_cached_non_agenda_is_refetched_not_used_as_successor_evidence(tmp_path,monkeypatch):
    (tmp_path/'2027.EP1.html').write_text('<html>Access denied</html>')
    calls=[]
    @contextmanager
    def fetcher(**kwargs):
        yield site({'2023.EP1':'January 4, 2023','2027.EP1':'January 6, 2027'},calls)
    monkeypatch.setattr(bap,'agenda_fetcher',fetcher)
    found=bap.scrape_agendas(tmp_path,term_starts=TERMS,auto_terms=True,today='2027-03-01')
    assert '2027.EP1' in found and '2027.EP1' in calls


@pytest.mark.parametrize('text',['[]','{"EP2022-2026":"not a boundary"}'])
def test_malformed_boundary_cache_is_ignored(tmp_path,text):
    (tmp_path/'.closed_terms.json').write_text(text)
    assert bap._load_closed_terms(tmp_path)=={}


def test_cached_wrong_term_page_is_refetched(tmp_path, monkeypatch):
    (tmp_path / '2027.EP1.html').write_text(site({'2027.EP1': 'January 9, 2019'})('2027.EP1'))
    calls = []
    @contextmanager
    def fetcher(**kwargs):
        yield site({'2023.EP1': 'January 4, 2023', '2027.EP1': 'January 6, 2027'}, calls)
    monkeypatch.setattr(bap, 'agenda_fetcher', fetcher)
    found = bap.scrape_agendas(tmp_path, term_starts=TERMS, auto_terms=True, today='2027-03-01')
    assert '2027.EP1' in found and '2027.EP1' in calls



def test_invalid_calendar_date_cannot_confirm_a_term():
    with pytest.raises(RuntimeError, match="agenda"):
        discover(site({'2023.EP1': 'January 4, 2023', '2027.EP1': 'February 31, 2027'}),
                 today='2027-03-01')
