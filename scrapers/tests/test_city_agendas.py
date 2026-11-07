from datetime import date
import json
from pathlib import Path

import pytest

from toronto_bids.sources import city_agendas as ca

CSV='Term,Committee,MTGNum,Date\n2018-2022,Infrastructure and Environment Committee,1,2019-01-17\n'
HTML='''<html><head><title>2019-01-17 Agenda - Infrastructure and Environment Committee</title></head><body>
<p>Infrastructure and Environment Committee Meeting No.: 1</p>
<h3>IE1.1 - Election</h3><p>Ordinary business</p>
<h3>IE1.2 - Contract Award - Request for Proposal No. 9117-18-7185</h3><!-- ignored -->
<div>Four proposals received.<a href="https://www.toronto.ca/legdocs/mmis/2019/ie/bgrd/backgroundfile-123071.pdf">Main report</a>
<a href="https://www.toronto.ca/legdocs/mmis/2019/ie/bgrd/backgroundfile-123348.pdf#page=1">Map supplement</a></div>
<h3>IE1.3 - Non-Competitive Contract</h3><div>Sole source.<a href="https://www.toronto.ca/legdocs/mmis/2019/ie/bgrd/backgroundfile-999.pdf">Report</a></div>
</body></html>'''


def test_maintenance_classification_requires_the_platform_notice_not_procurement_prose():
    agenda=HTML.replace('Four proposals received.','Traffic signal and RESCU electronic system maintenance. Some equipment is temporarily unavailable.')
    assert not ca.maintenance_page(agenda)
    assert len(ca.parse_agenda(agenda,ca.schedule(CSV)[0])['items'])==3
    notice='<nav id="breadcrumbs"><ul><li>System Maintenance</li></ul></nav><section id="main">We are currently performing scheduled maintenance and will be back in service soon.</section>'
    assert ca.maintenance_page(notice)


def test_schedule_normalizes_both_schemas_and_scopes_numbers_by_body_and_term():
    old=ca.schedule(CSV,today=date(2026,11,6))
    new=ca.schedule(CSV.replace('MTGNum','MTG #'),today=date(2026,11,6))
    assert old==new and old[0]['reference']=='2019.IE1'
    multi=CSV+'2018-2022,Infrastructure and Environment Committee,1,2019-01-18\n'
    assert ca.schedule(multi)[0]['dates']==['2019-01-17','2019-01-18']
    assert ca.schedule(CSV,today=date(2018,1,1))[0]['state']=='future'
    with pytest.raises(ValueError,match='schema'):ca.schedule(CSV.replace('MTGNum','unknown'))


def test_full_agenda_keeps_native_call_titles_and_all_item_scoped_supplements():
    parsed=ca.parse_agenda(HTML,ca.schedule(CSV)[0])
    assert parsed['state']=='held' and len(parsed['items'])==3
    award=parsed['items'][1]
    assert award['reference']=='2019.IE1.2' and award['procurement_candidate']
    assert len(award['attachments'])==2
    assert award['attachments'][1]['url'].endswith('123348.pdf')
    assert '999.pdf' not in json.dumps(award)
    assert parsed['items'][2]['explicitly_noncompetitive'] and not parsed['items'][2]['procurement_candidate']


@pytest.mark.parametrize('change',['wrong_date','wrong_body','wrong_number','no_headings'])
def test_mismatched_or_unparsed_agenda_cannot_claim_completeness(change):
    source=HTML
    if change=='wrong_date':source=source.replace('2019-01-17 Agenda','2020-01-17 Agenda')
    if change=='wrong_body':source=source.replace('Agenda - Infrastructure and Environment Committee','Agenda - City Council')
    if change=='wrong_number':source=source.replace('Meeting No.: 1','Meeting No.: 2')
    if change=='no_headings':source=source.replace('h3','h4')
    with pytest.raises(ValueError):ca.parse_agenda(source,ca.schedule(CSV)[0])


def test_resumed_capture_deduplicates_and_rejects_changed_held_source(tmp_path):
    conn=ca.connect(tmp_path);ca.seed(conn,ca.schedule(CSV));conn.close()
    class Browser:
        def fetch(self,reference):return HTML
    result=ca.capture(tmp_path,browser=Browser())
    assert result['meetings']=={'held':1} and result['candidate_reports']==2
    class NoFetch:
        def fetch(self,reference):pytest.fail('held page was refetched')
    assert ca.capture(tmp_path,browser=NoFetch())==result
    path=tmp_path/'agendas/2019.IE1.html';path.write_text(HTML+'changed')
    result=ca.capture(tmp_path,browser=NoFetch())
    assert result['meetings']=={'failed':1}
    conn=ca.connect(tmp_path)
    assert 'changed' in conn.execute('SELECT error FROM meeting').fetchone()[0]


def test_source_deferral_stops_the_lane_and_persists_retry_after(tmp_path):
    conn=ca.connect(tmp_path);ca.seed(conn,ca.schedule(CSV));conn.close()
    class Browser:
        def fetch(self,reference):raise ca.CaptureDeferred('HTTP 429',600)
    assert ca.capture(tmp_path,browser=Browser())['meetings']=={'failed':1}
    assert json.loads((tmp_path/'deferred-until.json').read_text())['reason']=='HTTP 429'
    assert ca.capture(tmp_path,browser=Browser())['meetings']=={'failed':1}
    conn=ca.connect(tmp_path)
    assert conn.execute('SELECT error FROM meeting').fetchone()[0]=='HTTP 429'


def test_wrong_cancelled_page_cannot_retire_a_scheduled_meeting():
    source=HTML.replace('Meeting No.: 1','Meeting No.: 2. Meeting cancelled')
    with pytest.raises(ValueError):ca.parse_agenda(source,ca.schedule(CSV)[0])
    parsed=ca.parse_agenda(HTML.replace('Meeting No.: 1','Meeting No.: 1. Meeting cancelled'),ca.schedule(CSV)[0])
    assert parsed['state']=='cancelled'


def test_rejected_public_agenda_is_retained_without_claiming_item_coverage(tmp_path):
    conn=ca.connect(tmp_path);ca.seed(conn,ca.schedule(CSV));conn.close()
    class WrongPage:
        def fetch(self,reference):return HTML.replace('2019-01-17 Agenda','2020-01-17 Agenda')
    result=ca.capture(tmp_path,browser=WrongPage())
    assert result['meetings']=={'failed':1} and result['items']==0
    assert len(list((tmp_path/'rejected').glob('2019.IE1.*.html')))==1
    assert not (tmp_path/'agendas/2019.IE1.html').exists()


def test_utf8_browser_snapshot_overrides_stale_page_charset():
    source=HTML.replace('<head>','<head><meta charset="windows-1252">').replace('Election','Élection')
    assert ca.parse_agenda(source.encode(),ca.schedule(CSV)[0])['items'][0]['title']=='Élection'


def test_report_capture_deduplicates_identical_bytes_and_checks_resume(tmp_path):
    fixture=Path(__file__).parent/'fixtures'
    # Use a real minimal PDF so validation crosses the production seam.
    pdfs=list(fixture.rglob('*.pdf'))
    assert pdfs
    data=pdfs[0].read_bytes()
    conn=ca.connect(tmp_path)
    with conn:
        for url in ('https://www.toronto.ca/a.pdf','https://www.toronto.ca/b.pdf'):
            conn.execute('INSERT INTO report(url) VALUES (?)',(url,))
    conn.close()
    class Http:
        def get_bytes(self,url):return data
    result=ca.capture_reports(tmp_path,http=Http())
    assert result['reports']=={'held':2}
    assert len(list((tmp_path/'reports').glob('*.pdf')))==1
    class NoFetch:
        def get_bytes(self,url):pytest.fail('held PDF refetched')
    assert ca.capture_reports(tmp_path,http=NoFetch())==result
    next((tmp_path/'reports').glob('*.pdf')).write_bytes(b'changed')
    assert ca.capture_reports(tmp_path,http=NoFetch())['reports']=={'failed':2}


def test_report_lane_persists_the_actual_long_retry_after(tmp_path):
    import httpx
    from datetime import datetime, timezone
    conn=ca.connect(tmp_path)
    with conn:
        for url in ('https://www.toronto.ca/a.pdf','https://www.toronto.ca/b.pdf'):
            conn.execute('INSERT INTO report(url) VALUES (?)',(url,))
    conn.close()
    class Http:
        calls=0
        def get_bytes(self,url):
            self.calls+=1
            response=httpx.Response(429,headers={'Retry-After':'7200'},request=httpx.Request('GET',url))
            response.raise_for_status()
    http=Http();before=datetime.now(timezone.utc)
    assert ca.capture_reports(tmp_path,http=http)['reports']=={'failed':1,'pending':1}
    deadline=datetime.fromisoformat(json.loads((tmp_path/'reports-deferred-until.json').read_text())['until'])
    assert (deadline-before).total_seconds()>=7200 and http.calls==1
    with pytest.raises(ca.CaptureDeferred):ca.capture_reports(tmp_path,http=http)
    assert http.calls==1
