"""Empty historical result publications are disclosed absence, not zero bids."""
from pathlib import Path
import ast,re,warnings
import pytest
from lxml import html
from toronto_bids.sources import merx as m
F=Path(__file__).parent/'fixtures/merx'


def test_real_marked_empty_publication_has_no_fabricated_bidders():
    assert m.parse_bids((F/'bids-empty-publication.js').read_bytes(),1,'page','fragment')==[]


@pytest.mark.parametrize('change',['remove_table','hide_marker','add_file'])
def test_unmarked_or_nonempty_document_only_results_remain_refused(change):
    raw=(F/'bids-empty-publication.js').read_text()
    literal=re.search(r'\.html\((\'(?:\\.|[^\'\\])*\')\);',raw)[1]
    with warnings.catch_warnings():
        warnings.simplefilter('ignore',SyntaxWarning);text=ast.literal_eval(literal).replace('\\/','/')
    tree=html.fromstring(text);table=tree.xpath('//table[@id="tblBidResultDocument"]')[0]
    if change=='remove_table':table.drop_tree()
    elif change=='hide_marker':table.xpath('./tbody/tr')[0].set('class','mets-table-row-empty')
    else:
        table.xpath('./tbody')[0].append(html.fromstring('<tr class="mets-table-row"><td><a href="/actual-file">Results PDF</a></td></tr>'))
    modified='$("#innerTabContent").html('+repr(html.tostring(tree,encoding='unicode'))+');'
    with pytest.raises(ValueError):m.parse_bids(modified.encode(),1,'page','fragment')


def test_empty_publication_preserves_note_and_still_captures_advertised_award(conn):
    import json
    from toronto_bids.buyers import seed_buyers
    from toronto_bids import config
    from toronto_bids.store import db
    manifest = json.loads((F / 'empty-results-manifest.json').read_text())
    buyer = seed_buyers(conn)[config.MERX_PORTALS[0]['slug']]
    listing = m.PortalListing(buyer_id=buyer, portal_url=manifest['detail_url'],
                            title='Green Bus Technology', source='merx')
    db.upsert_row(conn, listing, overwrite=True)
    conn.commit()
    detail = (F / 'empty-result-detail.html').read_bytes().replace(
        b'</body>', b'<a data-ajax-url="/public/solicitations/526480708/abstract/award">Award</a></body>')
    award = (F / 'award-fragment.js').read_bytes().replace(b'0000331101', b'0000065665')
    calls, notes = [], []

    def fetch(url):
        calls.append(url)
        if url.endswith('/bid-results'):
            return (F / 'bids-empty-publication.js').read_bytes()
        if url.endswith('/award'):
            return award
        return detail

    assert m.capture_details(conn, None, listing, manifest['detail_url'], manifest['notice_id'],
                             fetch=fetch, observations=notes) == 1
    assert calls[-1].endswith('/award')
    assert not conn.execute('SELECT * FROM agency_bid').fetchall()
    assert conn.execute('SELECT count(*) FROM agency_award').fetchone()[0] == 1
    assert notes == [dict(notice_id=manifest['notice_id'], kind='no_bidder_disclosure',
                         source_url=manifest['fragment_url'],
                         reason='Public result tab contains no bidder entries and explicitly reports no result files.')]
