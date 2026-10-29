"""Reviewed source gaps the nightly reports as known, not failed (#283).

Known means an exact match against reviewed evidence; everything else must stay a failure.
"""
import json

import pytest

from tests.test_agency_extraction_fields import cache, contract
from toronto_bids import cli, known_gaps, notify
from toronto_bids.extraction import backfill_from_extraction
from tests.test_nightly import nightly  # noqa: F401 — shared fixture

TRCA = 'https://pub-trca.escribemeetings.com/FileStream.ashx?DocumentId='
WATERFRONT_2023_03 = (
    "Unresolved literal supplier/value evidence: 'Ellis Don Civil Ltd', '$313,750' (0 blocks); "
    "Unmapped monetary evidence: 9d1e6afd47834cd33c2730452ecd3bcb4ed36769af63506defd7b1ec8987a203")


@pytest.fixture
def baseline(tmp_path, monkeypatch):
    path = tmp_path / 'known_agency_refusals.json'
    monkeypatch.setattr(known_gaps, 'REFUSALS_PATH', path)
    # Generic classifier fixtures stay independent of resolved production gaps.
    gaps=tmp_path/'known_gaps.json'
    gaps.write_text(json.dumps({'waterfront':{'2023-03':dict(
        errors=WATERFRONT_2023_03.split('; '),reason='names no recipient (test fixture)')}}))
    monkeypatch.setattr(known_gaps,'GAPS_PATH',gaps)
    return path


def _refuse(conn, tag):
    # Two contracts in one report, one reference-less: an ambiguous fallback is refused.
    cache(conn, 'trca', [contract('10008808'), contract(None)], tag=tag)
    backfill_from_extraction(conn, 'trca')


def _identity(conn):
    return ('identity:trca', 'incomplete coverage: 1 contract(s) have refused identity; ...')


def test_trca_downloads_are_known_only_for_reviewed_originals(conn, baseline):
    reviewed = [
        ('download:trca', f'skip {TRCA}5558: 403 Forbidden; left queued (cause unverified)'),
        ('download:trca', f"not a PDF (b'<htm', 9 bytes), left queued: {TRCA}20288"),
    ]
    other = [
        ('download:trca', f'skip {TRCA}99999991: 403 Forbidden; left queued (cause unverified)'),
        ('download:trca', '2026: calendar fetch failed: boom'),
        ('download:trca', f'skip meeting {TRCA}5558: boom'),
    ]
    unknown, known = known_gaps.classify(conn, reviewed + other)
    assert [(k['name'], k['error']) for k in known] == reviewed
    assert unknown == other


def test_waterfront_is_known_only_for_the_recorded_errors(conn, baseline):
    _, known = known_gaps.classify(conn, [('waterfront:2023-03', WATERFRONT_2023_03)])
    assert len(known) == 1 and 'names no recipient' in known[0]['reason']
    for name, error in [
        ('waterfront:2023-03', WATERFRONT_2023_03 + '; No supported award claims extracted'),
        ('waterfront:2023-04', WATERFRONT_2023_03),
        ('waterfront:2023-03', 'Invalid supplier or amount type'),
    ]:
        unknown, known = known_gaps.classify(conn, [(name, error)])
        assert unknown == [(name, error)] and not known


def test_reviewed_withholding_is_known(conn, baseline):
    _, known = known_gaps.classify(conn, [(
        'coverage:trca', 'incomplete coverage: 8 individual award claim(s) withheld after '
        'source review; supported bids retained; inspect tb agency-coverage')])
    assert len(known) == 1


def test_identity_refusals_are_known_only_within_the_recorded_baseline(conn, baseline):
    _refuse(conn, 'one')
    unknown, _ = known_gaps.classify(conn, [_identity(conn)])
    assert unknown, 'no baseline recorded: nothing is known'

    assert known_gaps.record_refusals(conn, ['trca'], baseline) == {'trca': 1}
    unknown, known = known_gaps.classify(conn, [_identity(conn)])
    assert not unknown and known[0]['reason'].endswith('(#282)')

    _refuse(conn, 'two')  # a new refusal the baseline never saw
    unknown, known = known_gaps.classify(conn, [_identity(conn)])
    assert unknown and not known


def test_recording_one_corpus_keeps_the_others(conn, baseline):
    baseline.write_text(json.dumps({'corpora': {'zoo': [
        dict(sha256='z', extractor_version='v', result_sha256='r', contract_index=0,
             report_url='u', reason='x')]}}))
    _refuse(conn, 'one')
    known_gaps.record_refusals(conn, ['trca'], baseline)
    held = json.loads(baseline.read_text())['corpora']
    assert len(held['zoo']) == 1 and len(held['trca']) == 1


def test_known_gaps_section_groups_and_never_changes_the_header():
    text = notify.summarize({'failures': [], 'known_gaps': [
        {'name': 'download:trca', 'error': 'a', 'reason': 'reviewed (#259)'},
        {'name': 'download:trca', 'error': 'b', 'reason': 'reviewed (#259)'},
        {'name': 'waterfront:2023-03', 'error': 'c', 'reason': 'no recipient'},
    ]})
    assert text.startswith('*✅ toronto-bids nightly*')
    assert '*Known gaps (3)*\ndownload:trca ×2: reviewed (#259)\nwaterfront:2023-03: no recipient' in text
    assert 'Failures' not in text


def _nightly_with_failures(nightly, monkeypatch, conn, extra):
    from tests.test_nightly import _KeepOpen
    monkeypatch.setattr(cli, '_open_db', lambda: _KeepOpen(conn))
    monkeypatch.setattr(cli, '_capture_agency_bodies', lambda *a, **k: list(extra))
    from toronto_bids.sources import waterfront
    monkeypatch.setattr(waterfront, 'capture_awards', lambda *a, **k: {
        'blocks': 510, 'failures': [('waterfront:2023-03', WATERFRONT_2023_03)]})
    posted = {}
    monkeypatch.setattr(notify, 'post', lambda text, **k: posted.setdefault('text', text))
    return nightly(), posted['text']


def test_a_night_of_only_known_gaps_is_green(nightly, monkeypatch, conn, baseline):
    _refuse(conn, 'one')
    known_gaps.record_refusals(conn, ['trca'], baseline)
    code, text = _nightly_with_failures(nightly, monkeypatch, conn, [
        ('download:trca', f'skip {TRCA}6818: 403; left queued (cause unverified)'),
        _identity(conn)])
    assert code == 0
    assert '✅ agencies' in text and '2 known gaps' in text
    assert '✅ waterfront awards' in text and '1 known gaps' in text
    assert '*Known gaps (3)*' in text and 'Failures' not in text
    rows = dict(conn.execute("SELECT source, status FROM sync_run "
                             "WHERE source IN ('agencies','waterfront awards')").fetchall())
    assert rows == {'agencies': 'ok', 'waterfront awards': 'ok'}


def test_a_new_failure_beside_known_gaps_still_fails_the_step(nightly, monkeypatch, conn, baseline):
    code, text = _nightly_with_failures(nightly, monkeypatch, conn, [
        ('download:trca', f'skip {TRCA}6818: 403; left queued (cause unverified)'),
        ('extract:zoo', '1 of 4 documents failed extraction')])
    assert code == 1
    assert '❌ agencies' in text and '*Failures (1)*\nextract:zoo' in text
    row = conn.execute("SELECT status, error FROM sync_run WHERE source='agencies'").fetchone()
    assert row['status'] == 'failed' and row['error'] == 'extract:zoo: 1 of 4 documents failed extraction'


def test_an_unreadable_registry_recognizes_nothing(nightly, monkeypatch, conn, baseline):
    def broken():
        raise ValueError('registry corrupt')
    monkeypatch.setattr(known_gaps, 'load_registry', broken)
    code, text = _nightly_with_failures(nightly, monkeypatch, conn, [])
    assert code == 1
    assert '❌ waterfront awards' in text and 'known_gaps: registry corrupt' in text
    assert 'waterfront:2023-03' in text.split('*Failures')[1]


def test_resolved_waterfront_gap_is_no_longer_an_active_exception(conn):
    unknown,known=known_gaps.classify(conn,[('waterfront:2023-03',WATERFRONT_2023_03)])
    assert unknown==[('waterfront:2023-03',WATERFRONT_2023_03)] and known==[]
