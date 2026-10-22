"""Live-captured HTML and actual shared-model responses; all tests run offline."""
import hashlib
import json
from pathlib import Path

import pytest

from toronto_bids.export.document import build_export_document
from toronto_bids.linking.procurement import resolve_procurements
from toronto_bids.linking.supplier import build_supplier_dimension
from toronto_bids.sources import waterfront as wf
from toronto_bids.store import db

FIXTURES = Path(__file__).parent / 'fixtures' / 'waterfront'
HTML = (FIXTURES / 'awards.html').read_bytes()


def seed_cache(conn):
    for path in FIXTURES.glob('*.json'):
        if path.stem not in ('2017-09', '2021-10', '2025-07'):
            continue
        pilot = json.loads(path.read_text())
        db.mark_extracted(conn, hashlib.sha256(pilot['text'].encode()).hexdigest(),
                          pilot['extractor_version'], json.dumps(pilot['result']))


class Page:
    def __init__(self, content=HTML):
        self.content = content
        self.urls = []

    def get_bytes(self, url):
        self.urls.append(url)
        return self.content


def test_entire_published_history_is_lossless_and_month_precision():
    rows = wf.parse_publication(HTML)
    assert len(rows) == 510
    assert len({r['publication_month'] for r in rows}) == 111
    assert {r['publication_month'][:4] for r in rows} == {str(y) for y in range(2017, 2027)}
    assert len({r['source_key'] for r in rows}) == len(rows)
    assert any('$5,0000,000' in r['raw_text'] for r in rows)
    assert any('$2,750 per week as and when required' in r['raw_text'] for r in rows)
    assert any('including construction trade packages' in r['raw_text'] for r in rows)
    assert any('Non-Copetitive' in r['labels'] for r in rows)


@pytest.mark.parametrize('period,count', [('2017-09', 8), ('2021-10', 9), ('2025-07', 5)])
def test_real_extraction_resolves_nested_and_multiple_supplier_lists(period, count):
    pilot = json.loads((FIXTURES / f'{period}.json').read_text())
    rows = [r for r in wf.parse_publication(HTML) if r['publication_month'] == period]
    assert wf.extraction_text(period, rows) == pilot['text']
    claims, errors = wf.verified_claims(rows, pilot['result'])
    assert errors == []
    assert len(claims) == count
    if period == '2025-07':
        malformed = next(c for c in claims if c['award_amount'] == '$5,0000,000')
        assert malformed['award_amount_numeric'] is None
    if period == '2021-10':
        assert {c['supplier_name_raw'] for c in claims} >= {'APE Studio Inc.', 'Earthscape Play Inc.', 'Monstrum APS', 'Whole Trees LLC'}


def test_drift_refuses_instead_of_publishing_partial_history():
    with pytest.raises(ValueError, match='Unrecognized publication month'):
        wf.parse_publication(HTML.replace(b'July 2026', b'Unknown 2026'))
    with pytest.raises(ValueError):
        wf.parse_publication(b'<html>Unavailable</html>')


def test_reordering_does_not_rekey_source_blocks():
    rows = wf.parse_publication(HTML)
    changed = HTML  # insert another visible body block
    marker = b'<h3>July 2026</h3>'
    assert marker in changed
    changed = changed.replace(marker, marker + b'<p>New published observation</p>', 1)
    after = wf.parse_publication(changed)
    assert {r['source_key'] for r in rows} < {r['source_key'] for r in after}


def test_cache_replay_identity_supplier_exports_and_pending_are_honest(conn, tmp_path):
    seed_cache(conn)
    page = Page()
    result = wf.capture_awards(conn, page, data_dir=tmp_path, limit=0, log=lambda m: None)
    assert page.urls == [wf.URL]
    assert result['blocks'] == 510 and result['months'] == 111
    assert len(result['failures']) == 108
    assert result['requests'] == 0 and result['awards'] == 22
    assert conn.execute('SELECT COUNT(*) FROM award_publication').fetchone()[0] == 510
    assert list((tmp_path / 'waterfront').glob('*.html'))[0].read_bytes() == HTML
    resolve_procurements(conn)
    build_supplier_dimension(conn)
    before = [tuple(r) for r in conn.execute('SELECT source_key,procurement_id,first_seen FROM published_award ORDER BY source_key')]
    wf.capture_awards(conn, page, data_dir=tmp_path, limit=0, log=lambda m: None)
    resolve_procurements(conn)
    after = [tuple(r) for r in conn.execute('SELECT source_key,procurement_id,first_seen FROM published_award ORDER BY source_key')]
    assert before == after
    assert all(r['supplier_id'] for r in conn.execute('SELECT supplier_id FROM published_award'))
    out = build_export_document(conn, generated_at='fixed')
    buyer = next(b for b in out['buyers'] if b['slug'] == 'waterfront-toronto')
    assert buyer['partnered'] == 1 and buyer['funding_share'] is None
    assert len(buyer['published_awards']) == 22 and len(buyer['award_publications']) == 510
    assert len(buyer['publication_extractions']) == 111
    assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


def test_bad_layout_snapshot_is_retained_before_parse(conn, tmp_path):
    content = b'<html>Service unavailable</html>'
    with pytest.raises(ValueError):
        wf.capture_awards(conn, Page(content), data_dir=tmp_path, limit=0)
    assert next((tmp_path / 'waterfront').glob('*.html')).read_bytes() == content
    assert conn.execute('SELECT COUNT(*) FROM award_publication').fetchone()[0] == 0


def test_hallucinated_value_or_construction_manager_is_not_a_recipient():
    rows = [dict(source_key='a', raw_text='Works - Real Supplier ($1,000) (Contract awarded by Manager on behalf of Waterfront Toronto)')]
    result = {'contracts': [{'awards': [{'supplier_name': 'Manager', 'amount_raw': '$1,000'}]}]}
    assert wf.verified_claims(rows, result)[0] == []
    result['contracts'][0]['awards'] = [{'supplier_name': 'Real Supplier', 'amount_raw': '$9,000'}]
    assert wf.verified_claims(rows, result)[0] == []


def test_missing_second_award_is_not_false_complete():
    rows = [dict(source_key='a', raw_text='Works - Supplier A ($1,000) & Supplier B ($2,000)')]
    result = {'contracts': [{'awards': [{'supplier_name': 'Supplier A', 'amount_raw': '$1,000'}]}]}
    claims, errors = wf.verified_claims(rows, result)
    assert len(claims) == 1
    assert any('Unmapped monetary evidence' in e for e in errors)


def test_rates_and_package_totals_retain_qualifier_and_have_no_summable_number():
    for extent in ('$2,750 per week as and when required', '$858,000,000 including construction trade packages'):
        rows = [dict(source_key='a', raw_text=f'Works - Supplier ({extent})')]
        amount = extent.split(' ')[0]
        claims, errors = wf.verified_claims(rows, {'contracts': [{'awards': [{'supplier_name': 'Supplier', 'amount_raw': amount}]}]})
        assert errors == []
        assert claims[0]['award_amount'] == extent and claims[0]['award_amount_numeric'] is None


def test_incomplete_refresh_keeps_held_facts_and_other_supported_claims(conn, tmp_path):
    seed_cache(conn)
    wf.capture_awards(conn, Page(), data_dir=tmp_path, limit=0, log=lambda m: None)
    held = {r['source_key'] for r in conn.execute('SELECT source_key FROM published_award')}
    pilot = json.loads((FIXTURES / '2025-07.json').read_text())
    pilot['result']['contracts'][0]['awards'][0]['supplier_name'] = 'Not a published supplier'
    digest = hashlib.sha256(pilot['text'].encode()).hexdigest()
    db.mark_extracted(conn, digest, pilot['extractor_version'], json.dumps(pilot['result']))
    result = wf.capture_awards(conn, Page(), data_dir=tmp_path, limit=0, log=lambda m: None)
    assert len(result['failures']) == 109
    assert {r['source_key'] for r in conn.execute('SELECT source_key FROM published_award')} == held
    assert conn.execute("SELECT status FROM publication_extraction WHERE publication_month='2025-07'").fetchone()[0] == 'incomplete'


@pytest.mark.parametrize('period,count', [('2022-07', 4), ('2019-03', 10), ('2019-11', 4)])
def test_repeated_supplier_values_and_parenthesized_years_are_not_lost(period, count):
    pilot = json.loads((FIXTURES / f'{period}.json').read_text())
    rows = [r for r in wf.parse_publication(HTML) if r['publication_month'] == period]
    claims, errors = wf.verified_claims(rows, pilot['result'])
    assert errors == [] and len(claims) == count
    if period == '2022-07':
        repeated = [c for c in claims if c['supplier_name_raw'] == 'Priestly Demolition Incorporated']
        assert len(repeated) == 2 and repeated[0]['publication_key'] != repeated[1]['publication_key']
    if period == '2019-03':
        malformed = next(c for c in claims if c['award_amount'] == '$79,55')
        assert malformed['award_amount_numeric'] is None


def test_missing_published_recipient_cannot_be_filled_from_manager_annotation():
    pilot = json.loads((FIXTURES / '2023-03.json').read_text())
    rows = [r for r in wf.parse_publication(HTML) if r['publication_month'] == '2023-03']
    claims, errors = wf.verified_claims(rows, pilot['result'])
    assert len(claims) == 4 and errors
    assert all(c['supplier_name_raw'] != 'Ellis Don Civil Ltd' for c in claims)
