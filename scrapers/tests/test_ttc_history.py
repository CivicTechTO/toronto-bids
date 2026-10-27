"""Public history capture: known source census, linked documents and recovery."""
import json
from pathlib import Path

import httpx
import pytest

from toronto_bids.sources.ttc_history import _meeting, capture_history

F = Path(__file__).parent / 'fixtures' / 'ttc_history'


@pytest.mark.parametrize('notice', json.loads((F / 'meeting-notices.json').read_text())['notices'])
def test_retained_notice_source_fields_establish_year_and_disposition(notice):
    raw = f'''<main><h1>{notice['title']}</h1>
        <p><strong>Date:</strong>{notice['published_date']}</p>
        <p><strong>Start Time:</strong>{notice['published_start_time']}</p></main>'''
    _, _, result = _meeting(raw, int(notice['year']))
    assert result['disposition'] == notice['disposition']
    assert result['published_date'] == notice['published_date']


def test_cancelled_and_rescheduled_notices_have_explicit_source_dispositions(conn, tmp_path):
    def notices(request):
        if '/board/2018/' in request.url.path:
            status = 'Cancelled' if request.url.path.endswith('1') else 'Re-scheduled to July 11, 2018'
            return httpx.Response(200, text=f'''<main><h1>Board Meeting</h1>
                <p><strong>Date:</strong>Tuesday, June 12, 2018</p>
                <p><strong>Start Time:</strong>{status}</p></main>''')
        return small_archive(request)
    with httpx.Client(transport=httpx.MockTransport(notices)) as client:
        report = capture_history(conn, [2018], client=client, data_dir=tmp_path, sleep=lambda _: None)
    assert report['complete']
    notices = report['years']['2018']['discovery']['meeting_notices']
    assert len(notices) == 2
    assert {n['disposition'] for n in notices} == {'cancelled', 'rescheduled'}
    assert all(n['published_date'] == 'Tuesday, June 12, 2018' for n in notices)
    assert report['years']['2018']['documents']['held'] == 0


@pytest.mark.parametrize('date,status,title', [
    ('Tuesday, June 12, 2017', 'Cancelled', 'Board Meeting'),
    ('Tuesday, June 12, 2018', '10:00 AM', 'Board Meeting'),
    ('Tuesday, June 12, 2018', 'Cancelled', 'ACAT Meeting'),
    ('', 'Cancelled', 'Board Meeting'),
    ('Tuesday, June 12, 2018', 'Cancelled', 'Board Meeting 2017'),
])
def test_generic_notice_does_not_bypass_meeting_identity(conn, tmp_path, date, status, title):
    def invalid(request):
        if '/board/2018/' in request.url.path:
            return httpx.Response(200, text=f'''<main><h1>{title}</h1>
                <p><strong>Date:</strong>{date}</p>
                <p><strong>Start Time:</strong>{status}</p></main>''')
        return small_archive(request)
    with httpx.Client(transport=httpx.MockTransport(invalid)) as client:
        report = capture_history(conn, [2018], client=client, data_dir=tmp_path, sleep=lambda _: None)
    assert not report['complete']
    assert report['years']['2018']['failures']


def test_real_2017_census_and_all_link_roles_are_reconciled(conn, tmp_path):
    calls = []
    def handle(request):
        calls.append(request)
        if request.url.path == '/All-public-meetings':
            name = 'archive.html'
        elif '/sxa/search/results/' in request.url.path:
            name = 'board.json' if request.url.params.get('meetingtype') == 'Board' else 'year-' + request.url.params.get('e', '0') + '.json'
        else:
            name = request.url.path.rsplit('/', 1)[-1] + '.html'
        return httpx.Response(200, content=(F / name).read_bytes())
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        report = capture_history(conn, [2017], download=False, client=client,
                                 data_dir=tmp_path, sleep=lambda _: None)
    year = report['years']['2017']
    assert report['complete'] and year['discovery']['complete']
    assert year['discovery']['all_types_captured'] == 52
    assert year['discovery']['advertised'] == year['discovery']['captured'] == 13
    assert year['documents']['observations'] == 472
    assert year['documents']['discovered'] == 440
    assert not year['documents']['requested']
    inventory = json.loads((tmp_path / 'agencies/ttc_history/2017/inventory.json').read_text())
    assert any('recognition' in r['topics'] for r in inventory)
    assert any('procurement_change' in r['topics'] for r in inventory)
    assert not conn.execute('SELECT * FROM agency_award').fetchall()
    assert not any(r.url.path.lower().endswith('.pdf') for r in calls)


def small_archive(request, *, deny=False, count_changed=False):
    if request.url.path == '/All-public-meetings':
        return httpx.Response(200, content=(F / 'archive.html').read_bytes())
    if '/sxa/search/results/' in request.url.path:
        board = [dict(Id=f'board-{n}', Url=f'/All-public-meetings/board/2018/meeting-{n}') for n in (1, 2)]
        rows = board if request.url.params.get('meetingtype') else board + [dict(Id='other', Url='/All-public-meetings/acat/2018/meeting-1')]
        return httpx.Response(200, json=dict(Count=len(rows), Results=rows))
    if '/board/2018/' in request.url.path:
        links = '''<h4>Item 1. Procurement Authorization</h4>
          <a href="https://cdn.ttc.ca/report.pdf">Staff report for item 1</a>
          <a href="https://cdn.ttc.ca/decision.pdf">Decision for item 1</a>
          <h4>Item 2. Award Acknowledgement</h4>
          <a href="https://cdn.ttc.ca/recognition.pdf">Staff report for item 2</a>''' if request.url.path.endswith('1') else ''
        return httpx.Response(200, text='<main><h1>Board Meeting 2018</h1>' + links + '</main>')
    if deny and request.url.path == '/decision.pdf':
        return httpx.Response(403, text='Access denied')
    return httpx.Response(200, content=(F.parent / 'tiny.pdf').read_bytes())


def test_denied_document_keeps_partial_capture_incomplete_and_resumable(conn, tmp_path):
    with httpx.Client(transport=httpx.MockTransport(lambda r: small_archive(r, deny=True))) as client:
        first = capture_history(conn, [2018], client=client, data_dir=tmp_path, sleep=lambda _: None)
    year = first['years']['2018']
    assert not first['complete'] and not year['complete']
    assert year['documents']['held'] == 1 and year['documents']['pending'] == 2
    calls = []
    def recover(request):
        calls.append(request)
        return small_archive(request)
    with httpx.Client(transport=httpx.MockTransport(recover)) as client:
        resumed = capture_history(conn, [2018], resume=True, client=client,
                                  data_dir=tmp_path, sleep=lambda _: None)
    assert resumed['complete'] and resumed['years']['2018']['documents']['held'] == 3
    assert not any('/board/2018/' in r.url.path or r.url.path == '/report.pdf' for r in calls)
    kinds = {r[0] for r in conn.execute('SELECT DISTINCT kind FROM background_pdf')}
    assert kinds == {'ttc_archive_report', 'ttc_archive_decision'}
    assert not conn.execute('SELECT * FROM extraction_cache').fetchall()


def test_corrupt_held_pdf_is_fetched_again_instead_of_trusted(conn, tmp_path):
    with httpx.Client(transport=httpx.MockTransport(small_archive)) as client:
        report = capture_history(conn, [2018], client=client, data_dir=tmp_path, sleep=lambda _: None)
    assert report['complete']
    state_path = tmp_path / 'agencies/ttc_history/2018/checkpoint.json'
    state = json.loads(state_path.read_text())
    Path(state['documents']['https://cdn.ttc.ca/report.pdf']['path']).write_bytes(b'corrupt')
    calls = []
    def recover(request):
        calls.append(request)
        return small_archive(request)
    with httpx.Client(transport=httpx.MockTransport(recover)) as client:
        report = capture_history(conn, [2018], resume=True, client=client,
                                 data_dir=tmp_path, sleep=lambda _: None)
    assert report['complete']
    assert any(r.url.path == '/report.pdf' for r in calls)
    assert not any('/board/2018/' in r.url.path for r in calls)


def test_request_budget_leaves_following_year_unattempted(conn, tmp_path):
    calls = []
    def handle(request):
        calls.append(request)
        return small_archive(request)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        report = capture_history(conn, [2018, 2019], max_requests=2, client=client,
                                 data_dir=tmp_path, sleep=lambda _: None)
    assert not report['complete'] and report['unattempted_years'] == [2019]
    assert report['requests'] == len(calls) == 2
    assert report['years']['2018']['failures']


def test_changed_source_population_is_refused_before_document_capture(conn, tmp_path):
    searches = 0
    def change(request):
        nonlocal searches
        response = small_archive(request)
        if '/sxa/search/results/' in request.url.path:
            searches += 1
            if searches == 3:
                value = response.json()
                value['Count'] += 1
                return httpx.Response(200, json=value)
        return response
    with httpx.Client(transport=httpx.MockTransport(change)) as client:
        report = capture_history(conn, [2018], client=client, data_dir=tmp_path, sleep=lambda _: None)
    assert not report['complete']
    assert not conn.execute('SELECT * FROM background_pdf').fetchall()


def test_capture_does_not_replace_previously_reviewed_document_bytes(conn, tmp_path):
    conn.execute("INSERT INTO background_pdf(url,kind,sha256,text) VALUES('https://cdn.ttc.ca/report.pdf','ttc_board','reviewed-sha','reviewed text')")
    conn.commit()
    with httpx.Client(transport=httpx.MockTransport(small_archive)) as client:
        report = capture_history(conn, [2018], client=client, data_dir=tmp_path, sleep=lambda _: None)
    assert report['complete']
    assert report['years']['2018']['documents']['reviewed_content_changes'] == ['https://cdn.ttc.ca/report.pdf']
    row = conn.execute("SELECT * FROM background_pdf WHERE url='https://cdn.ttc.ca/report.pdf'").fetchone()
    assert (row['kind'], row['sha256'], row['text']) == ('ttc_board', 'reviewed-sha', 'reviewed text')


def test_board_pagination_is_not_assumed_to_fit_one_page(conn, tmp_path):
    offsets = []
    def handle(request):
        if request.url.path == '/All-public-meetings':
            return httpx.Response(200, content=(F / 'archive.html').read_bytes())
        if '/sxa/search/results/' in request.url.path:
            offset = int(request.url.params['e'])
            offsets.append((request.url.params.get('meetingtype'), offset))
            records = [dict(Id=f'id-{n}', Url=f'/All-public-meetings/board/2018/meeting-{n}') for n in range(25)]
            return httpx.Response(200, json=dict(Count=25, Results=records[offset:offset + 20]))
        return httpx.Response(200, text='<main><h1>Board Meeting 2018</h1></main>')
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        report = capture_history(conn, [2018], download=False, client=client,
                                 data_dir=tmp_path, sleep=lambda _: None)
    assert report['complete'] and report['years']['2018']['discovery']['captured'] == 25
    assert ('Board', 20) in offsets


def test_history_cli_requires_a_valid_range_before_opening_database(monkeypatch):
    import pytest
    from toronto_bids import cli
    monkeypatch.setattr(cli, '_open_db', lambda: (_ for _ in ()).throw(AssertionError('must validate first')))
    for args in [['--from-year', '2019', '--to-year', '2018'], ['--year', '2025'],
                 ['--year', '2018', '--to-year', '2019'], ['--year', '2018', '--max-requests', '0']]:
        with pytest.raises(SystemExit) as error:
            cli.main(['capture-ttc-history', *args])
        assert error.value.code == 2


def test_history_cli_passes_range_and_reports_partial_exit(conn, monkeypatch, capsys):
    from toronto_bids import cli
    from toronto_bids.sources import ttc_history
    observed = {}
    def capture(connection, years, **options):
        observed.update(years=years, **options)
        return dict(complete=False, failures=['Pending source bytes'])
    monkeypatch.setattr(cli, '_open_db', lambda: conn)
    monkeypatch.setattr(ttc_history, 'capture_history', capture)
    assert cli.main(['capture-ttc-history', '--from-year', '2017', '--to-year', '2018',
                     '--resume', '--discover-only', '--max-requests', '80']) == 1
    assert observed['years'] == [2017, 2018] and observed['resume'] and not observed['download']
    assert observed['max_requests'] == 80
    assert 'Pending source bytes' in capsys.readouterr().out


def test_misfiled_other_meeting_is_retained_as_source_year_discrepancy(conn, tmp_path):
    def misfiled(request):
        response = small_archive(request)
        if '/sxa/search/results/' in request.url.path and not request.url.params.get('meetingtype'):
            value = response.json()
            value['Results'][-1].update(Url='/All-public-meetings/acat/2019/misfiled',
                Html='<div class="field-contentdate">June 11, 2018</div>')
            return httpx.Response(200, json=value)
        assert '/2019/' not in request.url.path, 'Other-type record must never broaden Board scope'
        return response
    with httpx.Client(transport=httpx.MockTransport(misfiled)) as client:
        report = capture_history(conn, [2018], download=False, client=client,
                                 data_dir=tmp_path, sleep=lambda _: None)
    assert report['complete']
    discovery = report['years']['2018']['discovery']
    assert discovery['all_types_captured'] == 3 and discovery['captured'] == 2
    assert len(discovery['source_discrepancies']) == 1


def test_missing_pdf_stays_queued_while_other_links_are_archived(conn, tmp_path):
    def missing(request):
        if request.url.path == '/report.pdf':
            return httpx.Response(404, text='Not found')
        return small_archive(request)
    with httpx.Client(transport=httpx.MockTransport(missing)) as client:
        report = capture_history(conn, [2018], client=client, data_dir=tmp_path, sleep=lambda _: None)
    docs = report['years']['2018']['documents']
    assert not report['complete'] and (docs['held'], docs['pending']) == (2, 1)
    assert conn.execute("SELECT sha256 FROM background_pdf WHERE url='https://cdn.ttc.ca/report.pdf'").fetchone()[0] is None
    assert conn.execute('SELECT count(*) FROM background_pdf WHERE sha256 IS NOT NULL').fetchone()[0] == 2


def test_resume_refuses_checkpoint_from_another_year(conn, tmp_path):
    path = tmp_path / 'agencies/ttc_history/2018/checkpoint.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(dict(version=1, year=2017, meetings={}, documents={})))
    with httpx.Client(transport=httpx.MockTransport(small_archive)) as client:
        report = capture_history(conn, [2018], resume=True, client=client,
                                 data_dir=tmp_path, sleep=lambda _: None)
    assert not report['complete'] and 'checkpoint scope' in report['years']['2018']['failures'][0]['error']


def test_wrong_year_without_publisher_date_is_not_accepted(conn, tmp_path):
    def wrong(request):
        response = small_archive(request)
        if '/sxa/search/results/' in request.url.path:
            data = response.json()
            data['Results'][0]['Url'] = '/All-public-meetings/board/2026/meeting-1'
            return httpx.Response(200, json=data)
        return response
    with httpx.Client(transport=httpx.MockTransport(wrong)) as client:
        report = capture_history(conn, [2018], download=False, client=client,
                                 data_dir=tmp_path, sleep=lambda _: None)
    assert not report['complete'] and not conn.execute('SELECT * FROM background_pdf').fetchall()


def test_textless_new_version_cannot_inherit_old_pdf_text(conn, tmp_path):
    with httpx.Client(transport=httpx.MockTransport(small_archive)) as client:
        first = capture_history(conn, [2018], client=client, data_dir=tmp_path, sleep=lambda _: None)
    assert first['complete']
    old = conn.execute("SELECT sha256,text FROM background_pdf WHERE url='https://cdn.ttc.ca/report.pdf'").fetchone()
    assert old['text']
    def replaced(request):
        response = small_archive(request)
        if request.url.path == '/report.pdf':
            return httpx.Response(200, content=response.content.replace(b'HELLO PDF', b'         '))
        return response
    with httpx.Client(transport=httpx.MockTransport(replaced)) as client:
        current = capture_history(conn, [2018], client=client, data_dir=tmp_path, sleep=lambda _: None)
    assert current['complete']
    assert current['years']['2018']['documents']['textless'] == ['https://cdn.ttc.ca/report.pdf']
    row = conn.execute("SELECT sha256,text FROM background_pdf WHERE url='https://cdn.ttc.ca/report.pdf'").fetchone()
    assert row['sha256'] != old['sha256'] and row['text'] is None
