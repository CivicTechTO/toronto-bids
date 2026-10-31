"""Explicit source-reviewed corrections/holds for reviewed historical winner/value joins.

Keep the City document's stable identity and existing redirects. Give the incorrectly
joined council reference its own identity, retaining the split proof on that identity.
No dates, suppliers, prices, raw observations or source bytes are rewritten here.
"""
import json
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / 'data/city_identity_repairs.json'


def repair_city_identities(conn):
    repaired = 0
    for proof in json.loads(PATH.read_text())['repairs']:
        ref, doc = proof['reference'], proof['rejected_document_number']
        link = conn.execute('SELECT document_number FROM solicitation_link WHERE reference=?', (ref,)).fetchone()
        keys = {(r['namespace'], r['value']): r['procurement_id']
                for r in conn.execute("SELECT * FROM procurement_key WHERE namespace IN ('city_document','city_council_bid')")}
        ref_id, doc_id = keys.get(('city_council_bid', ref)), keys.get(('city_document', doc))
        joined = ref_id is not None and ref_id == doc_id
        sol = conn.execute('SELECT title,title_source,issue_date FROM solicitation WHERE document_number=?', (doc,)).fetchone()
        tainted = sol is not None and sol['title_source'] == 'council_pre_ariba' and sol['title'] == proof['source_title']
        if not (joined or (link and link[0] == doc) or tainted):
            continue
        item = conn.execute('SELECT title FROM council_item WHERE reference=?', (ref,)).fetchone()
        source = conn.execute('SELECT reference,sha256 FROM background_pdf WHERE url=?', (proof['source_url'],)).fetchone()
        if (not item or item[0] != proof['source_title'] or not source or source['reference'] != ref
                or source['sha256'] not in (None, proof['source_sha256']) or not sol
                or sol['issue_date'] != proof['target_issue_date']):
            raise ValueError('City identity correction context changed; re-review required: '+ref)
        conn.execute('DELETE FROM solicitation_link WHERE reference=? AND document_number=?', (ref,doc))
        if joined:
            evidence = dict(rule='reviewed_city_identity_split', previous_procurement_id=doc_id, **proof)
            new_id = conn.execute('INSERT INTO procurement(merge_evidence) VALUES (?) RETURNING id',
                                  (json.dumps(evidence,sort_keys=True),)).fetchone()[0]
            conn.execute("UPDATE procurement_key SET procurement_id=? WHERE namespace='city_council_bid' AND value=?", (new_id,ref))
        if tainted:
            conn.execute("UPDATE solicitation SET title=NULL,title_source=NULL WHERE document_number=?", (doc,))
        repaired += 1
    return repaired
