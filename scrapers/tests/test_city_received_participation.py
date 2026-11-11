"""Received lists retain scope and never borrow an awardee as a bidder."""
import json

import pytest

from tests.test_city_native_participation import setup_source
from toronto_bids.extract import EXTRACTOR_VERSION

TEXT = '''Call No: RFQ 3905-14-0097
Number of Bids: Two (2)
Summary of Bids Received for RFQ 3905-14-0097
Appendix C
Alpha Construction Ltd. $150
Beta Ltd. $200
Appendix C1
Alpha Construction Ltd. No Bid
'''


def witness(text, quote):
    start = text.index(quote)
    return dict(start=start, end=start + len(quote), quote=quote)


def setup_receipts(conn, tmp_path, monkeypatch):
    args = setup_source(conn, tmp_path, monkeypatch)
    for k in ('contract_index', 'scope_start', 'scope_end'):
        args.pop(k)
    conn.execute('UPDATE council_item SET title=?', ('Award of RFQ 3905-14-0097',))
    conn.execute('UPDATE background_pdf SET text=?', (TEXT,))
    bids = [dict(supplier_name='Alpha Construction Ltd.', amount_raw='$150', status='compliant'),
            dict(supplier_name='Beta Ltd.', amount_raw='$200', status='not_stated'),
            dict(supplier_name='Alpha Construction Ltd.', amount_raw=None, status='no_bid')]
    contract = dict(reference='RFQ 3905-14-0097', stage='single_stage', declared_submissions=2, bids=bids)
    conn.execute('UPDATE extraction_cache SET result_json=?', (json.dumps(dict(contracts=[contract])),))
    section = witness(TEXT, TEXT[TEXT.index('Number of Bids:'):])
    parties = []
    for i, bid in enumerate(bids):
        name = bid['supplier_name']
        start = TEXT.index(name) if i != 2 else TEXT.rindex(name)
        party = dict(contract_index=0, bid_index=i,
                     name_parts=[dict(start=start, end=start+len(name), quote=name)],
                     role='no_bid' if i == 2 else 'received_submission',
                     source_scope='Appendix C1' if i == 2 else 'Appendix C',
                     scope_parts=[witness(TEXT, 'Appendix C1' if i == 2 else 'Appendix C')])
        if i == 2:
            party['no_bid_witness'] = witness(TEXT, 'Alpha Construction Ltd. No Bid')
        parties.append(party)
    args['review'] = dict(contract_indices=[0], receipt_sections=[section], parties=parties, scope_notes=[])
    return args


def test_received_list_retains_scoped_no_bid_and_unpriced_participants(conn, tmp_path, monkeypatch):
    from toronto_bids.linking.city_received_participation import propose
    proof = propose(**setup_receipts(conn, tmp_path, monkeypatch))
    assert proof['participant_names'] == ['Alpha Construction Ltd.', 'Beta Ltd.']
    assert len(proof['withheld_model_observations']) == 3
    assert proof['receipt_review']['parties'][2]['role'] == 'no_bid'
    assert proof['source_role'] == 'reviewed_received_submissions'


@pytest.mark.parametrize('change', ['missing_observation', 'wrong_name', 'no_bid_as_submission', 'call_conflict', 'outside_table', 'source_mutation', 'unquoted_scope'])
def test_received_list_refuses_source_scope_and_cache_mismatches(conn, tmp_path, monkeypatch, change):
    from toronto_bids.linking.city_received_participation import propose
    args = setup_receipts(conn, tmp_path, monkeypatch)
    review = args['review']
    if change == 'missing_observation':
        review['parties'].pop()
    elif change == 'wrong_name':
        review['parties'][0]['name_parts'] = [witness(TEXT, 'Beta Ltd.')]
    elif change == 'no_bid_as_submission':
        review['parties'][2]['role'] = 'received_submission'
    elif change == 'call_conflict':
        text = TEXT.replace('Summary of Bids Received for RFQ 3905-14-0097', 'Summary of Bids Received for RFQ 4000-14-0001')
        conn.execute('UPDATE background_pdf SET text=?', (text,))
        review['receipt_sections'] = [witness(text, text[text.index('Number of Bids:'):])]
    elif change == 'outside_table':
        review['receipt_sections'] = [witness(TEXT, 'Number of Bids: Two (2)')]
    elif change == 'unquoted_scope':
        review['parties'][0]['source_scope'] = 'Appendix D'
    else:
        (tmp_path/'composite.pdf').write_bytes(b'changed PDF')
    with pytest.raises(ValueError):
        propose(**args)


def test_received_replay_keeps_participants_and_refuses_changed_proof(conn, tmp_path, monkeypatch):
    from toronto_bids.linking import city_native_participation as native
    from toronto_bids.linking.city_received_participation import propose
    from toronto_bids.linking.procurement import resolve_procurements
    proof = propose(**setup_receipts(conn, tmp_path, monkeypatch))
    registry = tmp_path/'received.json'
    payload = dict(version=1, extractor_version=EXTRACTOR_VERSION, reviews=[proof])
    registry.write_text(json.dumps(payload))
    monkeypatch.setattr(native, 'PATH', registry)
    resolve_procurements(conn)
    rows = list(conn.execute("SELECT * FROM bid WHERE source='city_native_review'"))
    assert len(rows) == 2
    assert all(r['bid_price'] is None and r['status'] is None and r['reference'] is None for r in rows)
    before = [tuple(r) for r in rows]
    resolve_procurements(conn)
    assert before == [tuple(r) for r in conn.execute("SELECT * FROM bid WHERE source='city_native_review'")]
    payload['reviews'][0]['receipt_review']['parties'][0]['source_scope'] = 'Different scope'
    registry.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='changed or removed'):
        resolve_procurements(conn)


def test_split_count_column_keeps_literal_receipt_header_parts(conn, tmp_path, monkeypatch):
    from toronto_bids.linking.city_received_participation import propose
    args = setup_receipts(conn, tmp_path, monkeypatch)
    text = TEXT.replace('Number of Bids: Two (2)', 'Number       Vendor Name        Price\nof Bids      Two (2)')
    text = text.replace('Summary of Bids Received for RFQ 3905-14-0097\n', '')
    conn.execute('UPDATE background_pdf SET text=?', (text,))
    review = args['review']
    review['receipt_sections'] = [witness(text, text[text.index('Number'):])]
    review['receipt_headings'] = [[witness(text, 'Number'), witness(text, 'of Bids')]]
    for p in review['parties']:
        name = p['name_parts'][0]['quote']
        start = text.rindex(name) if p['role'] == 'no_bid' else text.index(name)
        p['name_parts'] = [dict(start=start, end=start+len(name), quote=name)]
        p['scope_parts'] = [witness(text, p['source_scope'])]
        if p['role'] == 'no_bid':
            p['no_bid_witness'] = witness(text, name+' No Bid')
    assert len(propose(**args)['participant_names']) == 2
    review['receipt_headings'][0].reverse()
    with pytest.raises(ValueError, match='ordered source parts'):
        propose(**args)


def test_a_party_with_only_a_no_bid_row_is_excluded_from_participants(conn, tmp_path, monkeypatch):
    from toronto_bids.linking.city_received_participation import propose
    args = setup_receipts(conn, tmp_path, monkeypatch)
    text = TEXT.replace('Alpha Construction Ltd. No Bid', 'Gamma Construction Ltd. No Bid')
    conn.execute('UPDATE background_pdf SET text=?', (text,))
    raw = json.loads(conn.execute('SELECT result_json FROM extraction_cache').fetchone()[0])
    raw['contracts'][0]['bids'][2]['supplier_name'] = 'Gamma Construction Ltd.'
    conn.execute('UPDATE extraction_cache SET result_json=?', (json.dumps(raw),))
    args['review']['receipt_sections'] = [witness(text, text[text.index('Number'):])]
    party = args['review']['parties'][2]
    party['name_parts'] = [witness(text, 'Gamma Construction Ltd.')]
    party['no_bid_witness'] = witness(text, 'Gamma Construction Ltd. No Bid')
    proof = propose(**args)
    assert proof['participant_names'] == ['Alpha Construction Ltd.', 'Beta Ltd.']
    assert proof['withheld_model_contracts'][0]['contract']['declared_submissions'] == 2
