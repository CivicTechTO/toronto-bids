"""Export scoped response facts with counts that distinguish firms and source cells."""
from collections import defaultdict
from toronto_bids.linking.city_crosswalk import supplier_identity


def build_scoped_export(conn):
    prices=defaultdict(list)
    for row in conn.execute('SELECT * FROM bid_response_price ORDER BY response_id,price_key'):
        prices[row['response_id']].append(dict(row))
    responses=defaultdict(list)
    for row in conn.execute('SELECT r.*,b.bidder_name_raw AS participating_firm FROM bid_response r LEFT JOIN bid b ON b.id=r.bid_id ORDER BY r.scope_id,r.response_key'):
        item=dict(row);item['prices']=prices[row['id']];responses[row['scope_id']].append(item)
    by_doc={}
    for row in conn.execute('SELECT * FROM bid_scope ORDER BY document_number,source,reference,scope_key'):
        item=dict(row);item['responses']=responses[row['id']]
        group=by_doc.setdefault(row['document_number'],dict(scopes=[]))
        group['scopes'].append(item)
    for group in by_doc.values():
        rows=[r for s in group['scopes'] for r in s['responses']]
        firms={supplier_identity(r['participating_firm']) for r in rows if r['participating_firm']}
        group['summary']=dict(participating_firms=len(firms),response_observations=len(rows),
            submitted_response_cells=sum(r['response_kind']=='submitted' for r in rows),
            resolved_scoped_submissions=sum(r['response_kind']=='submitted' and r['bid_id'] is not None for r in rows),
            explicit_no_bid_cells=sum(r['response_kind']=='no_bid' for r in rows),
            unscoped_outcomes=sum(r['response_kind']=='unscoped_outcome' for r in rows),
            unresolved_party_cells=sum(r['identity_status']=='unresolved' for r in rows),
            price_variants=sum(len(r['prices']) for r in rows))
    return by_doc
